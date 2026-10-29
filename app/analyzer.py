"""Local, bounded-memory OCR indexing with actual presentation timestamps.

No footage or model inference is sent to an external service. RapidOCR ships the
Chinese ONNX models in its installed package. Recognized events remain unverified.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from typing import Callable

from .rules import OCRLine, classify_lines, identity

_LOCAL = threading.local()
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_FFMPEG = Path(r"D:\ffmpeg-7.1-full_build\bin\ffmpeg.exe")


def _binary(name: str, options: dict) -> str:
    override = options.get(name)
    result = str(override) if override else shutil.which(name)
    if not result:
        sibling = _FFMPEG.with_name(name + ".exe")
        if sibling.exists():
            result = str(sibling)
    if not result:
        raise RuntimeError(f"未找到 {name}，请安装 FFmpeg 后重试。")
    return result


def probe_video(video_path: str, options: dict | None = None) -> dict:
    options = options or {}
    result = subprocess.run([
        _binary("ffprobe", options), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration,start_time,avg_frame_rate:format=duration,start_time",
        "-of", "json", str(video_path),
    ], capture_output=True, timeout=60, creationflags=_NO_WINDOW)
    if result.returncode:
        raise RuntimeError("无法读取录像：" + result.stderr.decode("utf-8", "replace")[-1500:])
    data = json.loads(result.stdout)
    if not data.get("streams"):
        raise ValueError("文件没有可读取的视频轨道。")
    stream = data["streams"][0]
    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or stream.get("duration") or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("无法确定录像时长；请先结束录制并确认文件可播放。")
    return {"width": int(stream["width"]), "height": int(stream["height"]),
            "duration": duration, "start_time": float(fmt.get("start_time") or stream.get("start_time") or 0)}


def _scaled_size(width: int, height: int, limit: int) -> tuple[int, int]:
    ratio = min(1.0, limit / max(width, height))
    return max(2, int(width * ratio) // 2 * 2), max(2, int(height * ratio) // 2 * 2)


def _validate_options(options: dict) -> dict:
    result = dict(options or {})
    if result.get('game_profile','generic') not in ('generic','phantom_blade_zero_demo'):
        raise ValueError('未知的游戏识别方案。')
    result['text_mode']=result.get('text_mode','compact')
    if result['text_mode'] not in ('compact','all','off'):
        raise ValueError('未知的文字保留方式。')
    result['recognition_scope'] = result.get('recognition_scope',
        'collection_only' if result.get('game_profile') == 'phantom_blade_zero_demo' else 'legacy')
    if result['recognition_scope'] not in ('collection_only', 'legacy'):
        raise ValueError('未知的识别范围。')
    if result['recognition_scope'] == 'collection_only':
        result['text_mode'] = 'off'
    for key, default, low, high in (("sample_interval", 1.0, 0.2, 60), ("max_width", 1280, 320, 3840),
                                     ("resume_from", 0.0, 0, 1e9), ("min_confidence", .6, .1, 1)):
        value = float(result.get(key, default))
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key} 必须在 {low} 和 {high} 之间。")
        result[key] = int(value) if key == "max_width" else value
    regions = result.get("regions") or []
    if not isinstance(regions, list) or len(regions) > 8:
        raise ValueError("识别区域需要是列表，最多 8 个。")
    for region in regions:
        values = [float(region.get(k, -1)) for k in ("x", "y", "w", "h")]
        x, y, w, h = values
        if not all(math.isfinite(n) for n in values) or not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.000001 and y + h <= 1.000001):
            raise ValueError("识别区域 x、y、w、h 使用 0 到 1 的比例且不能超出画面。")
    if result.get("keywords") is not None:
        if not isinstance(result["keywords"], dict):
            raise ValueError("提示词必须按 item、enemy、achievement 分类。")
        for value in result["keywords"].values():
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError("每组提示词需要是文字列表。")
    return result


def _engine():
    if not hasattr(_LOCAL, "engine"):
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise RuntimeError("中文识别组件尚未安装：rapidocr-onnxruntime。") from exc
        # RapidOCR 1.2.x ignores the documented newer thread kwargs. Configure
        # its SessionOptions factory before sessions are created instead.
        import rapidocr_onnxruntime.utils as ort_utils
        if not getattr(ort_utils,'_footage_threads_configured',False):
            original_options=ort_utils.SessionOptions
            def bounded_options():
                opts=original_options()
                opts.intra_op_num_threads=4
                opts.inter_op_num_threads=1
                return opts
            ort_utils.SessionOptions=bounded_options
            ort_utils._footage_threads_configured=True
        _LOCAL.engine = RapidOCR(text_score=.5, det_model_path=None, det_limit_side_len=960, det_limit_type="max",
                                intra_op_num_threads=4, inter_op_num_threads=1,
                                use_angle_cls=False, print_verbose=False)
    return _LOCAL.engine


def _ocr_lines(frame, regions: list, engine, confidence: float) -> list[OCRLine]:
    height, width = frame.shape[:2]
    crops = regions or [{"x": 0, "y": 0, "w": 1, "h": 1}]
    lines = []
    for region in crops:
        x, y = int(float(region["x"]) * width), int(float(region["y"]) * height)
        right = min(width, math.ceil((float(region["x"]) + float(region["w"])) * width))
        bottom = min(height, math.ceil((float(region["y"]) + float(region["h"])) * height))
        if right - x < 8 or bottom - y < 8:
            continue
        recognized, _ = engine(frame[y:bottom, x:right], use_cls=False)
        for box, text, score, *_ in recognized or []:
            if float(score) < confidence:
                continue
            translated = [[float(p[0]) + x, float(p[1]) + y] for p in box]
            lines.append(OCRLine(str(text), float(score), translated))
    lines.sort(key=lambda line: (round(min(p[1] for p in line.box) / 15), min(p[0] for p in line.box)))
    return lines


class EventAccumulator:
    """Emit a stable ID immediately and UPSERT the same event when its prompt ends.

    A gap longer than 1.5 sample intervals creates a new occurrence, even for the
    same item. No whole-video 'already seen' set can swallow later acquisitions.
    """
    def __init__(self, video_key: str, interval: float, on_event: Callable[[dict], None]):
        self.video_key = video_key
        self.interval = interval
        self.gap = max(.35, interval * 1.5)
        self.on_event = on_event
        self.active: dict[tuple[str, str], dict] = {}
        self.count = 0

    def add(self, candidates: list[dict], timestamp: float, screenshot: Callable[[], str]) -> None:
        self.expire(timestamp)
        for candidate in candidates[:100]:
            text_group=candidate['kind']=='text' and candidate.get('_identity_key')
            key = (candidate["kind"], str(text_group) if text_group else identity(candidate["name"]))
            if not key[1]:
                continue
            current = self.active.get(key)
            if current:
                current["last_seen"] = timestamp
                current["event"]["end_time"] = max(current["event"]["end_time"], round(timestamp + float(candidate.get('_interval',self.interval)), 3))
                # Later frames often have clearer text or a stronger opaque
                # victory glyph. Keep the evidence instead of freezing the
                # weaker first frame's metadata for the entire occurrence.
                event=current["event"]
                if candidate['kind']=='text' and candidate.get('_replace_name'):
                    event['name']=candidate['name']
                    event['confidence']=float(candidate.get('confidence',0))
                for field in ('confidence','detection_score'):
                    if candidate.get(field) is not None:
                        event[field]=max(float(event.get(field) or 0),float(candidate[field]))
                for field in ('evidence','panel_type'):
                    if candidate.get(field) and not event.get(field):
                        event[field]=candidate[field]
                if len(candidate.get('text',''))>len(event.get('text','')):
                    event['text']=candidate['text']
                continue
            event = dict(candidate)
            for field in ('_interval','_merge_gap','_identity_key','_replace_name'):
                event.pop(field,None)
            signature = f"{self.video_key}|{key[0]}|{key[1]}|{timestamp:.3f}"
            event.update(id="ocr_" + hashlib.sha256(signature.encode("utf-8")).hexdigest()[:28],
                         time=round(max(0, timestamp), 3), end_time=round(timestamp + float(candidate.get('_interval',self.interval)), 3),
                         screenshot=screenshot(), source="ocr", verified=0)
            merge_gap=float(candidate.get('_merge_gap',self.gap)) if candidate['kind']=='text' else self.gap
            self.active[key] = {"event": event, "last_seen": timestamp, "gap":min(30,max(self.gap,merge_gap))}
            self.on_event(dict(event))
            self.count += 1
        # A malformed OCR frame cannot keep an unbounded active dictionary alive.
        while len(self.active) > 500:
            oldest = min(self.active, key=lambda k: self.active[k]["last_seen"])
            self.on_event(dict(self.active.pop(oldest)["event"]))

    def expire(self, timestamp: float) -> None:
        for key in list(self.active):
            if timestamp - self.active[key]["last_seen"] > self.active[key].get('gap',self.gap):
                self.on_event(dict(self.active.pop(key)["event"]))

    def flush(self) -> None:
        for item in self.active.values():
            self.on_event(dict(item["event"]))
        self.active.clear()


def _sampled_frames(video_path: str, info: dict, options: dict, cancelled: Callable[[], bool]):
    """Stream raw frames with matching showinfo PTS; never derive time from frame count.

    -copyts -start_at_zero removes the container start_time, while -ss preserves
    the original, file-relative timeline after seeking. showinfo reports the
    selected frame's timestamp before rawvideo discards timestamps.
    """
    import numpy as np
    width, height = _scaled_size(info["width"], info["height"], options["max_width"])
    interval = options["sample_interval"]
    # Sampling on absolute time buckets gives resumed runs the same frames/IDs.
    filter_text = f"select='isnan(prev_selected_t)+gt(floor((t+0.0000001)/{interval:.9f}),floor((prev_selected_t+0.0000001)/{interval:.9f}))',scale={width}:{height},showinfo"
    command = [_binary("ffmpeg", options), "-hide_banner", "-nostdin", "-loglevel", "info", "-copyts", "-start_at_zero"]
    seek_time = math.floor((options["resume_from"] + 1e-7) / interval) * interval
    if seek_time > 0:
        command += ["-ss", f"{seek_time:.6f}"]
    command += ["-i", str(video_path), "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", filter_text,
                "-fps_mode", "passthrough", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=_NO_WINDOW, bufsize=1024 * 1024)
    timestamps: queue.Queue = queue.Queue(maxsize=64)
    error_lines = deque(maxlen=30)
    finished = threading.Event()

    def read_errors():
        pattern = re.compile(r"\bn:\s*(\d+).*?\bpts_time:([\-\d.eE+]+)")
        try:
            for raw in iter(process.stderr.readline, b""):
                line = raw.decode("utf-8", "replace")
                match = pattern.search(line)
                if match:
                    while not finished.is_set():
                        try:
                            timestamps.put((int(match.group(1)), float(match.group(2))), timeout=.2)
                            break
                        except queue.Full:
                            continue
                elif "showinfo" not in line:
                    error_lines.append(line.strip())
        except (OSError, ValueError):
            pass

    def watch_cancel():
        while not finished.wait(.2):
            if cancelled():
                try:
                    process.kill()
                except OSError:
                    pass
                break

    reader = threading.Thread(target=read_errors, daemon=True)
    watcher = threading.Thread(target=watch_cancel, daemon=True)
    reader.start()
    watcher.start()
    frame_size = width * height * 3
    index = 0
    try:
        while not cancelled():
            payload = bytearray()
            while len(payload) < frame_size:
                chunk = process.stdout.read(frame_size - len(payload))
                if not chunk:
                    break
                payload.extend(chunk)
            if not payload:
                break
            if len(payload) != frame_size:
                if cancelled():
                    break
                raise RuntimeError("录像解码中断，当前帧不完整。")
            try:
                number, timestamp = timestamps.get(timeout=10)
            except queue.Empty as exc:
                raise RuntimeError("无法读取准确的视频时间戳，已停止识别以避免错误定位。") from exc
            if number != index or not math.isfinite(timestamp):
                raise RuntimeError("视频画面和时间戳不同步，已停止识别。")
            index += 1
            frame = np.frombuffer(payload, dtype=np.uint8).reshape((height, width, 3))
            yield timestamp, frame
        return_code = process.wait(timeout=15)
        if return_code and not cancelled():
            raise RuntimeError("录像解码失败：" + "\n".join(error_lines)[-2000:])
    finally:
        finished.set()
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        if process.stdout:
            process.stdout.close()
        reader.join(timeout=2)
        if process.stderr:
            process.stderr.close()
        watcher.join(timeout=1)


def analyze(video_path: str, output_dir: Path, options: dict,
            on_event: Callable[[dict], None], on_progress: Callable[[dict], None],
            cancelled: Callable[[], bool]) -> dict:
    """Index one local video; callbacks may raise to stop processing.

    on_event is an UPSERT contract: repeated IDs update end_time, not new events.
    on_progress.progress is 0..1 and checkpoint is safe to resume from (with a
    one-sample overlap). Every output event is unverified, including explicit UI
    notifications. Region/keyword calibration is supported through options.
    """
    options = _validate_options(options)
    focused = options['recognition_scope'] == 'collection_only'
    if focused:
        from .collection_taxonomy import detect_collection_candidates, FirstCollectionFilter
        first_collections = FirstCollectionFilter()
    source = Path(video_path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("请导入一个录像文件。")
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    info = probe_video(str(source), options)
    duration = info["duration"]
    interval = options["sample_interval"]
    last_time = min(options["resume_from"], duration)
    frames = 0
    started = time.monotonic()
    def persist_event(event: dict):
        event["end_time"] = min(duration, event["end_time"])
        on_event(event)

    accumulator = EventAccumulator(str(source).casefold(), interval, persist_event)

    def report(stage: str):
        on_progress({"stage": stage, "time": round(last_time, 3), "processed_seconds": round(last_time, 3),
                     "duration": duration, "progress": min(1.0, max(0.0, last_time / duration)),
                     "checkpoint": max(0, last_time - interval), "frames": frames, "events": accumulator.count,
                     "elapsed_seconds": round(time.monotonic() - started, 1)})

    report("loading")
    if cancelled():
        return {"status": "cancelled", "events": 0, "frames": 0, "checkpoint": last_time, "duration": duration}
    if options["resume_from"] >= duration:
        last_time = duration
        report("completed")
        return {"status": "completed", "events": 0, "frames": 0, "checkpoint": duration, "duration": duration}
    engine = _engine()
    from .text_index import TextIndexFilter
    text_index=TextIndexFilter(mode=options['text_mode'] if options.get('include_text',True) else 'off')
    import cv2
    demo_profile=options.get('game_profile')=='phantom_blade_zero_demo'
    decode_options=dict(options)
    name_tracker=None
    analysis_start=0.0
    if demo_profile:
        from .game_panels import detect_panels
        from .navigation_events import detect_navigation
        from .quest_events import detect_quests
        from .upgrade_materials import filter_upgrade_materials
        from .victory_detector import VictoryDetector
        from .game_profile import BossNameTracker
        from .scene_focus import detect_scene_focus, apply_scene_focus
        from .boss_text_index import BossTextIndex
        victory_detector=VictoryDetector()
        name_tracker=BossNameTracker()
        boss_text_index=BossTextIndex()
        if options.get('_boss_text_resume'):
            boss_text_index.restore_context(**options['_boss_text_resume'])
        # Subdivide the OCR interval rather than choosing an independent .5 s
        # grid: .7 s OCR becomes .35 s visual sampling, preserving every OCR
        # boundary on fresh and resumed runs. Victory checks are at least 2 Hz.
        decode_options['sample_interval']=interval/max(1,math.ceil(interval/.5))
        analysis_start=math.floor((options['resume_from']+1e-7)/interval)*interval
        # Restore recent name context without emitting old events or moving
        # progress backwards. Only the small boss-title crop is read here.
        warmup_start=max(0.0,analysis_start-25)
        decode_options['resume_from']=math.floor((warmup_start+1e-7)/interval)*interval
    last_ocr_bucket=None
    report("recognizing")
    try:
        for timestamp, frame in _sampled_frames(str(source), info, decode_options, cancelled):
            if cancelled():
                break
            bucket=math.floor((timestamp+1e-7)/interval)
            if demo_profile and timestamp+1e-7<analysis_start:
                if bucket!=last_ocr_bucket:
                    last_ocr_bucket=bucket
                    top_lines=_ocr_lines(frame,[{'x':.32,'y':.035,'w':.36,'h':.12}],engine,.6)
                    name_tracker.observe(top_lines,frame.shape,timestamp)
                continue
            last_time = max(last_time, timestamp)
            candidates=[]
            if bucket!=last_ocr_bucket:
                last_ocr_bucket=bucket
                lines = _ocr_lines(frame, options.get("regions") or [], engine, options["min_confidence"])
                if demo_profile:
                    top_lines=_ocr_lines(frame,[{'x':.32,'y':.035,'w':.36,'h':.12}],engine,.6)
                    name_tracker.observe(top_lines,frame.shape,timestamp)
                    # Keep independent raw text available for audit and search.
                    lines.extend(top_lines)
                candidates = (detect_collection_candidates(lines, frame.shape, timestamp, frame)
                              if focused else classify_lines(lines, options.get("keywords"), options.get("include_text", True)))
                if demo_profile and not focused:
                    candidates.extend(detect_panels(lines,frame.shape,timestamp))
                    candidates.extend(detect_navigation(lines,frame.shape,timestamp))
                    candidates.extend(detect_quests(lines,frame.shape,timestamp))
                    candidates=filter_upgrade_materials(candidates,lines,frame.shape)
                    if text_index.mode=='compact':
                        candidates=apply_scene_focus(candidates,detect_scene_focus(lines,frame.shape))
                candidates=text_index.filter(candidates,lines,frame.shape,timestamp)
                if demo_profile and text_index.mode=='compact' and not focused:
                    candidates=boss_text_index.filter(candidates,lines,frame.shape,timestamp)
            if demo_profile:
                match=victory_detector.detect(frame)
                if match:
                    boss=name_tracker.current(timestamp)
                    candidates.append({'kind':'enemy','name':boss[0] if boss else '首领击败（名称待核实）',
                        'text':'检测到与参考“斩”字匹配的胜利画面。'+('此前顶部首领名：'+boss[0] if boss else '未读到近期首领名称。')+'\n自动候选，需核实。',
                        'confidence':boss[1] if boss else 0,'detection_score':float(match.get('confidence',0)),
                        'evidence':'victory_template','_interval':decode_options['sample_interval']})
            frames += 1
            screenshot_path = None

            def save_screenshot():
                nonlocal screenshot_path
                if screenshot_path is None:
                    digest = hashlib.sha256(f"{str(source).casefold()}|{timestamp:.3f}".encode("utf-8")).hexdigest()[:16]
                    destination = output_dir / f"frame_{int(timestamp * 1000):012d}_{digest}.jpg"
                    success, image = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 86])
                    if not success:
                        raise RuntimeError("无法生成事件截图。")
                    temporary = destination.with_suffix(".jpg.tmp")
                    temporary.write_bytes(image.tobytes())
                    os.replace(temporary, destination)
                    screenshot_path = str(destination)
                return screenshot_path

            if focused:
                candidates = first_collections.filter(candidates)
            accumulator.add(candidates, max(0, timestamp), save_screenshot)
            report("recognizing")
    finally:
        accumulator.flush()
    status = "cancelled" if cancelled() else "completed"
    if status == "completed":
        last_time = duration
    report(status)
    return {"status": status, "events": accumulator.count, "frames": frames,
            "checkpoint": max(0, last_time - interval) if status == "cancelled" else duration,
            "duration": duration, "elapsed_seconds": round(time.monotonic() - started, 1),
            "text_mode":text_index.mode,"text_index_stats":dict(text_index.stats)}
