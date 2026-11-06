"""Local, ordered clip exports; merge audio is encoded only once."""
import json
import math
import os
import re
import shutil
import subprocess
import uuid
from fractions import Fraction
from pathlib import Path

from . import media


def safe_name(value, fallback='片段'):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value or '')).strip(' .')[:70].rstrip(' .')
    if not name:
        return fallback
    if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name):
        name = '_' + name
    return name


def _probe(path):
    result = subprocess.run(
        [media.FFPROBE, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        creationflags=media.CREATE_NO_WINDOW, timeout=45,
    )
    if result.returncode:
        raise ValueError('无法读取录像：' + result.stderr[-500:])
    data = json.loads(result.stdout)
    video = next((s for s in data.get('streams', []) if s.get('codec_type') == 'video'), None)
    if not video:
        raise ValueError('文件没有视频轨道')
    return data, video


def _fps(video):
    for key in ('avg_frame_rate', 'r_frame_rate'):
        try:
            rate = Fraction(video.get(key, '0/1'))
            if 1 <= rate <= 120:
                return rate
        except (ValueError, ZeroDivisionError):
            pass
    return Fraction(30)


def _run(args, logfile, duration, progress):
    proc = None
    try:
        with logfile.open('a', encoding='utf-8') as log:
            proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=log, text=True,
                encoding='utf-8', errors='replace', creationflags=media.CREATE_NO_WINDOW,
            )
            for line in proc.stdout:
                if line.startswith('out_time_us='):
                    try:
                        value = float(line.strip().split('=', 1)[1]) / 1e6 / max(duration, .01)
                    except ValueError:
                        continue
                    progress(min(.99, max(0., value)))
            rc = proc.wait()
        if rc:
            raise RuntimeError('视频处理失败：' + logfile.read_text(encoding='utf-8')[-600:])
        progress(1.)
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if proc.stdout:
                proc.stdout.close()


def _normalize(clip, destination, info, width, height, rate, logfile, progress):
    # Every intermediate has the same clocks and PCM audio. A final AAC encode
    # avoids inserting a fresh AAC encoder delay at every selected timestamp.
    frames = max(1, round((clip['end'] - clip['start']) * rate))
    duration = float(Fraction(frames, 1) / rate)
    fps = f'{rate.numerator}/{rate.denominator}'
    args = [media.FFMPEG, '-hide_banner', '-nostdin', '-loglevel', 'error', '-y',
            '-ss', str(clip['start']), '-i', str(clip['path'])]
    has_audio = any(s.get('codec_type') == 'audio' for s in info.get('streams', []))
    if not has_audio:
        args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
    vf = (f'setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=decrease:'
          f'force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,'
          f'fps={fps},tpad=stop_mode=clone:stop_duration={duration:.9f},'
          f'trim=end_frame={frames},setpts=N/({fps}*TB),format=yuv420p')
    # Input seeking keeps the audio's offset relative to the requested cut.
    # first_pts fills an initial gap; apad covers sources with short audio tracks.
    af = f'aresample=48000:async=1:first_pts=0,apad,atrim=duration={duration:.9f},asetpts=PTS-STARTPTS'
    args += ['-map', '0:v:0', '-map', '0:a:0' if has_audio else '1:a:0',
             '-vf', vf, '-af', af, '-t', f'{duration:.9f}', '-c:v', 'libx264',
             '-preset', 'fast', '-crf', '18', '-threads', '6', '-pix_fmt', 'yuv420p',
             '-r', fps, '-video_track_timescale', str(rate.numerator),
             '-c:a', 'pcm_s16le', '-ar', '48000', '-ac', '2',
             '-progress', 'pipe:1', str(destination)]
    _run(args, logfile, duration, progress)
    return duration


def render_batch(clips, output_dir, mode, name, progress):
    """Render a frozen, already ordered selection, without changing library data.

    Returns output_path, files, clips, manifest_path. Each result clip has source
    bounds plus output_start/output_end/output_time (seconds in its output file).
    Overlapping selections intentionally remain independent clips.
    """
    if mode not in ('separate', 'merge'):
        raise ValueError('请选择分别导出或合并导出')
    if not clips:
        raise ValueError('请至少选择一个片段')
    clips = [dict(clip) for clip in clips]
    infos = {}
    for clip in clips:
        start, end = float(clip['start']), float(clip['end'])
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError('片段起止时间无效')
        clip.update(start=start, end=end)
        path = str(Path(clip['path']).resolve())
        if path not in infos:
            infos[path] = _probe(path)
        source_duration = float(infos[path][0].get('format', {}).get('duration', 0))
        if end > source_duration + .05:
            raise ValueError('片段终点超出了录像长度')
        clip['path'] = path

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    work = output_dir / ('.batch-' + uuid.uuid4().hex)
    work.mkdir()
    logfile = work / 'render.log'
    files, result_clips = [], []
    total = sum(c['end'] - c['start'] for c in clips)
    done, offset = 0., 0.
    title = safe_name(name, '合并片段')
    pending = []
    try:
        progress(0.)
        if mode == 'separate':
            for index, clip in enumerate(clips, 1):
                filename = f'{index:03d}_{safe_name(clip.get("name"))}.mp4'
                target = output_dir / filename
                if target.exists():
                    raise ValueError('导出目录已有同名文件，请新建导出任务')
                part = work / filename
                duration = clip['end'] - clip['start']
                media.render(clip['path'], part, duration, logfile,
                             lambda p, base=done, length=duration: progress(.98 * (base + p * length) / total),
                             start=clip['start'], end=clip['end'])
                actual = media.probe(part)['duration']
                output_time = min(actual, max(0., float(clip.get('time', clip['start'])) - clip['start']))
                result_clips.append({**clip, 'output_file': str(target), 'output_start': 0.,
                                     'output_end': actual, 'output_time': output_time})
                files.append(str(target))
                pending.append((part, target))
                done += duration
            output_path = str(output_dir)
        else:
            _, first_video = infos[clips[0]['path']]
            width = max(2, int(first_video['width']) // 2 * 2)
            height = max(2, int(first_video['height']) // 2 * 2)
            rate = _fps(first_video)
            segments = []
            target = output_dir / (title + '.mp4')
            if target.exists():
                raise ValueError('导出目录已有同名文件，请新建导出任务')
            for index, clip in enumerate(clips):
                part = work / f'part_{index:05d}.mov'
                source_length = clip['end'] - clip['start']
                duration = _normalize(clip, part, infos[clip['path']][0], width, height, rate, logfile,
                                      lambda p, base=done, length=source_length: progress(.85 * (base + p * length) / total))
                segments.append((part, duration))
                output_time = offset + min(duration, max(0., float(clip.get('time', clip['start'])) - clip['start']))
                result_clips.append({**clip, 'output_file': str(target), 'output_start': offset,
                                     'output_end': offset + duration, 'output_time': output_time})
                offset += duration
                done += source_length
            concat = work / 'concat.txt'
            concat.write_text('ffconcat version 1.0\n' + ''.join(
                f"file '{part.name}'\nduration {duration:.9f}\n" for part, duration in segments), encoding='utf-8')
            merged = work / 'merged.mp4'
            args = [media.FFMPEG, '-hide_banner', '-nostdin', '-loglevel', 'error', '-y',
                    '-f', 'concat', '-safe', '0', '-i', str(concat), '-map', '0:v:0', '-map', '0:a:0',
                    '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                    '-af', 'aresample=48000:async=1:first_pts=0', '-t', f'{offset:.9f}',
                    '-movflags', '+faststart', '-progress', 'pipe:1', str(merged)]
            _run(args, logfile, offset, lambda p: progress(.85 + .13 * p))
            pending.append((merged, target))
            files.append(str(target))
            output_path = str(target)

        manifest_path = output_dir / 'manifest.json'
        if manifest_path.exists():
            raise ValueError('导出目录已有任务记录，请新建导出任务')
        result = {'output_path': output_path, 'files': files, 'clips': result_clips,
                  'manifest_path': str(manifest_path)}
        manifest = {**result, 'version': 1, 'mode': mode, 'name': title}
        manifest_work = work / 'manifest.json'
        manifest_work.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        for part, target in pending:
            os.replace(part, target)
        os.replace(manifest_work, manifest_path)
        progress(1.)
        return result
    except Exception:
        if logfile.exists():
            os.replace(logfile, output_dir / f'error-{work.name[7:]}.log')
        raise
    finally:
        # Only the unique directory created by this invocation is disposable.
        resolved = work.resolve()
        if resolved.parent == output_dir and resolved.name.startswith('.batch-'):
            shutil.rmtree(resolved, ignore_errors=True)
