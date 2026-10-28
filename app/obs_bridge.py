"""OBS v5 bridge with durable sidecar markers and Windows global hotkeys.

Timestamps come from OBS's encoded-frame clock, never from wall time.  A bridge
must observe the beginning of a recording.  Unexpected file splitting fails
closed because OBS's public split event contains a filename but no exact media
offset.  Native chapter metadata remains available for later import.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

KINDS = {'item': '收集物品', 'enemy': '击败敌人', 'achievement': '完成成就', 'note': '路线起点'}


class OBSBridge:
    def __init__(self, root):
        self.root = Path(root)
        self.data = self.root / 'data'
        self.data.mkdir(parents=True, exist_ok=True)
        self.log_path = self.data / 'markers.jsonl'
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._socket = None
        self._thread = None
        self._hotkey_thread = None
        self._hotkey_tid = 0
        self._session_safe = False
        self._recording_path = None
        self._split = False
        self._config = {}
        self._finalize_pending = set()
        self._recovery_checked = False
        self._state = {'connected': False, 'recording': False, 'paused': False,
                       'time': 0.0, 'video_path': None, 'can_mark': False,
                       'error': None, 'last_marker': None, 'hotkeys': {},
                       'profile': None}

    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='OBS-connection', daemon=True)
        self._thread.start()
        if os.name == 'nt':
            self._hotkey_thread = threading.Thread(target=self._hotkeys, name='OBS-hotkeys', daemon=True)
            self._hotkey_thread.start()
        return self

    def stop(self):
        self._stop.set()
        if os.name == 'nt' and self._hotkey_tid:
            ctypes.windll.user32.PostThreadMessageW(self._hotkey_tid, 0x0012, 0, 0)
        with self._lock:
            self._disconnect()
        for thread in (self._thread, self._hotkey_thread):
            if thread and thread is not threading.current_thread():
                thread.join(timeout=4)

    def status(self):
        with self._lock:
            message = self._state.get('error') or ('录制已暂停' if self._state['paused'] else
                       '正在录制，可以快捷标记' if self._state['can_mark'] else
                       'OBS 已连接，等待开始录制' if self._state['connected'] else '正在连接 OBS')
            return dict(self._state, hotkeys=dict(self._state['hotkeys']), message=message)

    def _connect(self):
        import websocket
        config_path = self.data / 'obs-connection.json'
        if not config_path.exists():
            raise RuntimeError('OBS 尚未配置连接。')
        self._config = json.loads(config_path.read_text(encoding='utf-8-sig'))
        host = self._config.get('host', '127.0.0.1')
        if host not in ('127.0.0.1', 'localhost', '::1'):
            raise RuntimeError('仅允许连接本机 OBS。')
        self._socket = websocket.create_connection(
            f"ws://127.0.0.1:{int(self._config.get('port', 4455))}", timeout=3)
        hello = json.loads(self._socket.recv())
        if hello.get('op') != 0:
            raise RuntimeError('OBS 连接握手失败。')
        identify = {'rpcVersion': 1, 'eventSubscriptions': 64}
        auth = hello['d'].get('authentication')
        if auth:
            secret = base64.b64encode(hashlib.sha256(
                (self._config.get('password', '') + auth['salt']).encode()).digest()).decode()
            identify['authentication'] = base64.b64encode(hashlib.sha256(
                (secret + auth['challenge']).encode()).digest()).decode()
        self._socket.send(json.dumps({'op': 1, 'd': identify}))
        if json.loads(self._socket.recv()).get('op') != 2:
            raise RuntimeError('OBS 身份验证失败，请检查本地连接配置。')
        self._session_safe = False
        self._recording_path = None
        self._split = False
        self._recovery_checked = False
        self._state['connected'] = True
        self._state['error'] = None
        profile = self._request('GetProfileList')
        self._state['profile'] = profile.get('currentProfileName')

    def _disconnect(self):
        sock, self._socket = self._socket, None
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        self._session_safe = False
        self._recording_path = None
        self._state.update(connected=False, can_mark=False)

    def _event(self, data):
        event = data.get('eventType')
        values = data.get('eventData') or {}
        if event == 'RecordStateChanged':
            state = values.get('outputState', '')
            if state == 'OBS_WEBSOCKET_OUTPUT_STARTED':
                self._session_safe = True
                self._split = False
                self._recording_path = None
            elif state == 'OBS_WEBSOCKET_OUTPUT_STOPPED':
                if values.get('outputPath'):
                    self._finalize_pending.add(values['outputPath'])
                self._session_safe = False
                self._recording_path = None
                self._split = False
        elif event == 'RecordFileChanged':
            self._split = True
            self._session_safe = False
            self._recording_path = values.get('newOutputPath')

    def _request(self, method, data=None):
        if not self._socket:
            raise RuntimeError('OBS 未连接。')
        rid = uuid.uuid4().hex
        self._socket.send(json.dumps({'op': 6, 'd': {
            'requestType': method, 'requestId': rid, 'requestData': data or {}}}))
        while True:
            packet = json.loads(self._socket.recv())
            if packet.get('op') == 5:
                self._event(packet['d'])
                continue
            result = packet.get('d', {})
            if packet.get('op') != 7 or result.get('requestId') != rid:
                continue
            status = result.get('requestStatus', {})
            if not status.get('result'):
                # Return only the OBS error; never serialize connection credentials.
                raise RuntimeError(f"OBS {method}: {status.get('comment', status.get('code', '失败'))}")
            return result.get('responseData') or {}

    def _current_path(self):
        outputs = self._request('GetOutputList').get('outputs', [])
        candidates = [o for o in outputs if o.get('outputActive') and
                      o.get('outputName') in ('adv_file_output', 'simple_file_output')]
        if len(candidates) != 1:
            raise RuntimeError('无法唯一确定 OBS 主录制输出。')
        settings = self._request('GetOutputSettings', {'outputName': candidates[0]['outputName']})
        path = settings.get('outputSettings', {}).get('path')
        if not path or not Path(path).is_absolute():
            raise RuntimeError('OBS 未提供当前录像的完整路径。')
        if self._recording_path and Path(path) != Path(self._recording_path):
            self._split = True
            self._session_safe = False
            raise RuntimeError('检测到录像分段，请停止后重新开始录制以继续快捷标记。')
        self._recording_path = str(Path(path))
        return self._recording_path

    def _poll(self):
        record = self._request('GetRecordStatus')
        active = bool(record.get('outputActive'))
        paused = bool(record.get('outputPaused'))
        seconds = max(0.0, float(record.get('outputDuration', 0)) / 1000)
        error = None
        if active and self._session_safe:
            self._current_path()
        if active and not self._session_safe:
            error = ('录像已分段；请停止后重新开始录制，或录完后导入原生章节。' if self._split else
                     '连接时录像已开始。请在下一段录制开始前保持助手开启。')
        if not active:
            if not self._recovery_checked:
                self._recovery_checked = True
                if self.log_path.exists():
                    for line in self.log_path.read_text(encoding='utf-8').splitlines():
                        try:
                            marker = json.loads(line)
                            if marker.get('timestamp_source') != 'native_chapter':
                                self._finalize_pending.add(marker['video_path'])
                        except (ValueError, KeyError):
                            pass
            for path in list(self._finalize_pending):
                self._reconcile_file(path)
                self._finalize_pending.discard(path)
        self._state.update(recording=active, paused=paused, time=seconds,
                           video_path=self._recording_path if active else None,
                           can_mark=active and not paused and self._session_safe,
                           error=error)
        return record

    def _reconcile_file(self, path):
        """Use finalized media chapters as the precise clock (encoder buffering
        can make GetRecordStatus trail chapter placement by a few frames)."""
        if not self.log_path.exists() or not Path(path).is_file():
            return
        ffprobe = shutil.which('ffprobe')
        if not ffprobe:
            return
        try:
            raw = subprocess.check_output([ffprobe, '-v', 'error', '-show_chapters',
                                           '-of', 'json', str(path)], timeout=12,
                                          stderr=subprocess.DEVNULL,
                                          creationflags=0x08000000 if os.name == 'nt' else 0)
            chapters = json.loads(raw).get('chapters', [])
            times = {}
            for chapter in chapters:
                title = chapter.get('tags', {}).get('title', '')
                identifier = title.rsplit('｜', 1)[-1]
                if len(identifier) == 32:
                    times[identifier] = float(chapter['start_time'])
            if not times:
                return
            rows = []
            changed = False
            for line in self.log_path.read_text(encoding='utf-8').splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    rows.append(line)
                    continue
                if row.get('id') in times and Path(row.get('video_path', '')) == Path(path):
                    row['time_estimate'] = row.get('time_estimate', row['time'])
                    row['time'] = times[row['id']]
                    row['timestamp_source'] = 'native_chapter'
                    if (self._state.get('last_marker') or {}).get('id') == row['id']:
                        self._state['last_marker'] = row
                    changed = True
                rows.append(json.dumps(row, ensure_ascii=False))
            if changed:
                temp = self.log_path.with_suffix('.jsonl.tmp')
                with temp.open('w', encoding='utf-8', newline='\n') as f:
                    f.write('\n'.join(rows) + '\n')
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp, self.log_path)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            # A crash-damaged file may have no chapters. Its durable OBS frame
            # estimate stays searchable and is explicitly labelled as such.
            return

    def _run(self):
        while not self._stop.is_set():
            try:
                with self._lock:
                    if not self._socket:
                        self._connect()
                    self._poll()
                self._stop.wait(0.6)
            except Exception as exc:
                with self._lock:
                    self._disconnect()
                    self._state['error'] = ('无法连接 OBS，请打开 OBS 并启用 WebSocket。'
                                            if isinstance(exc, (OSError, ConnectionError)) else str(exc))
                self._stop.wait(3)

    def mark(self, kind, name=''):
        if kind not in KINDS:
            raise ValueError('未知标记类型。')
        name = str(name or '').strip()[:200]
        with self._lock:
            self._poll()
            if not self._state['recording']:
                raise RuntimeError('OBS 未录制，标记未保存。')
            if self._state['paused']:
                raise RuntimeError('OBS 录制已暂停，请恢复后再标记。')
            if not self._state['can_mark']:
                raise RuntimeError(self._state.get('error') or '当前录像时间无法可靠确认。')
            marker = {'id': uuid.uuid4().hex, 'video_path': self._recording_path,
                      'time': round(self._state['time'], 3), 'kind': kind,
                      'name': name or KINDS[kind], 'source': 'marker',
                      'timestamp_source': 'obs_encoded_frames',
                      'created_at': datetime.now(timezone.utc).isoformat(),
                      'chapter_written': False}
            chapter_name = f"{KINDS[kind]}｜{marker['name']}｜{marker['id']}"
            try:
                self._request('CreateRecordChapter', {'chapterName': chapter_name})
                marker['chapter_written'] = True
            except RuntimeError as exc:
                marker['chapter_error'] = str(exc)
            # A split can arrive interleaved with the chapter request. Never write a
            # misleading file/time pair even though the native chapter may succeed.
            if not self._session_safe:
                raise RuntimeError('标记时录像发生分段，请录完后导入原生章节。')
            with self.log_path.open('a', encoding='utf-8', newline='\n') as f:
                f.write(json.dumps(marker, ensure_ascii=False) + '\n')
                f.flush()
                os.fsync(f.fileno())
            self._state['last_marker'] = marker
            self._state['error'] = None
            return marker

    def _hotkeys(self):
        user32 = ctypes.windll.user32
        self._hotkey_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        bindings = [(0xA100 + i, ord(str(i + 1)), kind) for i, kind in enumerate(KINDS)]
        installed = {}
        try:
            for hid, key, kind in bindings:
                success = bool(user32.RegisterHotKey(None, hid, 0x0001 | 0x0002 | 0x4000, key))
                self._state['hotkeys'][kind] = {
                    'key': f'Ctrl+Alt+{chr(key)}', 'registered': success,
                    'error': None if success else '快捷键已被其他程序占用'}
                if success:
                    installed[hid] = kind
            msg = wintypes.MSG()
            while not self._stop.is_set() and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312 and int(msg.wParam) in installed:
                    try:
                        self.mark(installed[int(msg.wParam)])
                    except Exception as exc:
                        with self._lock:
                            self._state['error'] = str(exc)
        finally:
            for hid in installed:
                user32.UnregisterHotKey(None, hid)
            self._hotkey_tid = 0

