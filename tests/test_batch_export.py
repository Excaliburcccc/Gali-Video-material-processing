"""Real renders exercise clip ordering, clocks, silence and atomic failures."""
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from app import batch_export, media


def run(*args):
    return subprocess.run([media.FFMPEG, '-v', 'error', '-nostdin', '-y', *map(str, args)],
                          capture_output=True, check=True, creationflags=media.CREATE_NO_WINDOW).stdout


@pytest.fixture
def sources(tmp_path):
    red, blue = tmp_path / '红色 with audio.mp4', tmp_path / '蓝色 silent.mp4'
    run('-f', 'lavfi', '-i', 'color=red:size=320x180:rate=12:duration=3',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100:duration=3',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', red)
    run('-f', 'lavfi', '-i', 'color=blue:size=240x240:rate=25:duration=3',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', blue)
    return red, blue


def clip(path, start, end, event='event'):
    return {'event_id': event, 'video_id': path.stem, 'video_name': path.name, 'path': str(path),
            'name': '同名:物品', 'time': (start + end) / 2, 'start': start, 'end': end}


def frame(path, second):
    return np.frombuffer(run('-ss', second, '-i', path, '-frames:v', 1,
                            '-vf', 'scale=1:1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'), dtype=np.uint8)


def test_separate_outputs_keep_each_selection_and_sources(sources, tmp_path):
    original = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    clips = [clip(sources[1], .4, 1.4, 'second'), clip(sources[0], .2, 1.7, 'first')]
    progress = []
    result = batch_export.render_batch(clips, tmp_path / '单独', 'separate', '导出', progress.append)
    assert Path(result['output_path']).is_dir()
    assert len(result['files']) == 2
    assert [c['event_id'] for c in result['clips']] == ['second', 'first']
    assert all(Path(p).is_file() for p in result['files'])
    assert Path(result['files'][0]).name == '001_同名_物品.mp4'
    assert Path(result['files'][1]).name == '002_同名_物品.mp4'
    assert media.probe(result['files'][0])['duration'] == pytest.approx(1., abs=.1)
    assert media.probe(result['files'][1])['duration'] == pytest.approx(1.5, abs=.1)
    assert frame(result['files'][0], .4)[2] > 150
    assert frame(result['files'][1], .4)[0] > 150
    assert [c['output_time'] for c in result['clips']] == pytest.approx([.5, .75])
    assert progress[-1] == 1 and progress == sorted(progress)
    assert not list((tmp_path / '单独').glob('.batch-*'))
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources} == original


def test_merge_normalizes_sources_and_encodes_continuous_audio(sources, tmp_path):
    clips = [clip(sources[0], .3, 1.7, 'red'), clip(sources[1], .3, 1.5, 'blue'),
             clip(sources[0], 1.7, 2.7, 'red-again')]
    progress = []
    result = batch_export.render_batch(clips, tmp_path / '合并', 'merge', '跨录像合并', progress.append)
    path = Path(result['output_path'])
    data, video = batch_export._probe(path)
    audio = next(s for s in data['streams'] if s['codec_type'] == 'audio')
    assert (video['width'], video['height']) == (320, 180)
    assert video['r_frame_rate'] == '12/1'
    assert audio['sample_rate'] == '48000' and audio['channels'] == 2
    target = sum(round((c['end'] - c['start']) * 12) / 12 for c in clips)
    assert float(data['format']['duration']) == pytest.approx(target, abs=.04)
    assert result['clips'][1]['output_start'] == pytest.approx(17 / 12)
    assert result['clips'][2]['output_start'] == pytest.approx(31 / 12)
    assert frame(path, .6)[0] > 150
    assert frame(path, 2.)[2] > 100
    assert frame(path, 3.)[0] > 150
    pcm = np.frombuffer(run('-i', path, '-map', '0:a:0', '-ac', 1, '-f', 'f32le', 'pipe:1'), dtype='<f4')
    rms = lambda a, b: float(np.sqrt(np.mean(pcm[round(a * 48000):round(b * 48000)] ** 2)))
    assert rms(.2, .8) > .02
    assert rms(1.7, 2.3) < .0001
    assert rms(2.8, 3.3) > .02
    assert abs(float(audio['duration']) - float(video['duration'])) < .03
    manifest = json.loads(Path(result['manifest_path']).read_text(encoding='utf-8'))
    assert manifest['mode'] == 'merge' and len(manifest['clips']) == 3
    assert result['files'] == [str(path)]
    assert progress[-1] == 1 and progress == sorted(progress)
    assert not list((tmp_path / '合并').glob('.batch-*'))


def test_many_short_clips_do_not_accumulate_audio_encoder_delay(sources, tmp_path):
    clips = [clip(sources[i % 2], .2, .45, str(i)) for i in range(16)]
    result = batch_export.render_batch(clips, tmp_path / '短片拼接', 'merge', '短片', lambda p: None)
    data, video = batch_export._probe(result['output_path'])
    audio = next(s for s in data['streams'] if s['codec_type'] == 'audio')
    assert float(video['duration']) == pytest.approx(4., abs=.02)
    assert float(audio['duration']) == pytest.approx(4., abs=.03)
    pcm = np.frombuffer(run('-i', result['output_path'], '-map', '0:a:0', '-ac', 1,
                           '-f', 'f32le', 'pipe:1'), dtype='<f4')
    # Validate the last audible/silent pair, where encoder padding would accumulate.
    assert np.sqrt(np.mean(pcm[int(3.55 * 48000):int(3.68 * 48000)] ** 2)) > .02
    assert np.sqrt(np.mean(pcm[int(3.80 * 48000):int(3.94 * 48000)] ** 2)) < .0001


@pytest.mark.parametrize('bounds', [(1, 1), (-1, 1), (float('nan'), 2), (0, float('inf'))])
def test_invalid_bounds_fail_before_output(tmp_path, bounds):
    output = tmp_path / 'invalid'
    with pytest.raises(ValueError, match='起止时间'):
        batch_export.render_batch([clip(tmp_path / 'none.mp4', *bounds)], output, 'merge', '', lambda p: None)
    assert not output.exists()


def test_render_failure_removes_partial_outputs(sources, tmp_path, monkeypatch):
    def fail(path, output, *args, **kwargs):
        Path(output).write_bytes(b'incomplete')
        raise RuntimeError('render failed')
    monkeypatch.setattr(media, 'render', fail)
    output = tmp_path / 'failed'
    with pytest.raises(RuntimeError, match='render failed'):
        batch_export.render_batch([clip(sources[0], 0, 1)], output, 'separate', '', lambda p: None)
    assert not list(output.glob('*.mp4')) and not list(output.glob('.batch-*'))


@pytest.mark.parametrize('value,expected', [('CON', '_CON'), ('A/B:C', 'A_B_C'), (' ... ', '片段')])
def test_names_are_windows_safe(value, expected):
    assert batch_export.safe_name(value) == expected
