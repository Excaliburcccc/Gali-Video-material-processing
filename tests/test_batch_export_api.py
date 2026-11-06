"""Batch selection, project isolation and frozen render jobs through HTTP.

Run alone with FOOTAGE_TEST=1, an empty FOOTAGE_ROOT under E:/CodexFiles/.tmp,
and --basetemp pointing there too. Render correctness lives in test_batch_export.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import subprocess
import uuid

import pytest
from fastapi.testclient import TestClient

assert os.environ.get('FOOTAGE_TEST') == '1', 'Set FOOTAGE_TEST=1 for API tests.'
TEST_ROOT = Path(os.environ['FOOTAGE_ROOT']).resolve()
PROJECT_ROOT = Path(__file__).resolve().parents[1]
assert TEST_ROOT != PROJECT_ROOT, 'Never test against the real footage library.'
assert TEST_ROOT.is_relative_to(Path('E:/CodexFiles/.tmp/codex').resolve())
(TEST_ROOT / 'static').mkdir(parents=True, exist_ok=True)

from app import server
from app.storage import execute, query


def good(response):
    assert 200 <= response.status_code < 300, response.text
    return response.json()


@pytest.fixture(scope='module')
def client():
    clip = TEST_ROOT / 'batch-fixture.mp4'
    subprocess.run([server.media.FFMPEG, '-v', 'error', '-y', '-f', 'lavfi',
        '-i', 'color=c=gray:s=320x180:r=10:d=8', '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p', str(clip)], check=True,
        creationflags=server.media.CREATE_NO_WINDOW)
    with TestClient(server.app) as c:
        yield c, clip


def new_project(c):
    profile = {
        'id': 'batch_test_' + uuid.uuid4().hex[:12], 'name': '批量导出测试',
        'adapter': 'generic', 'language': 'zh', 'version': 1,
        'categories': [{'id': 'relic', 'label': '遗物', 'group': '收集',
                        'enabled': True, 'policy': 'first'}],
        'rules': [{'id': 'pickup', 'category': 'relic', 'trigger': '获得',
                   'semantic': 'obtained', 'region': None, 'name_region': None,
                   'exclude': []}],
        'regions': [], 'exclude_text': [], 'aliases': {},
    }
    saved = good(c.post('/api/profiles', json=profile))
    saved = saved.get('profile', saved)
    p = good(c.post('/api/projects', json={
        'name': '批量导出测试项目', 'profile_id': saved['id'],
        'profile_version': saved['version'],
    }))
    return p.get('project', p)


def import_fixture(c, path, p):
    result = good(c.post('/api/import', json={
        'paths': [str(path)], 'auto_analyze': False, 'project_id': p['id'],
    }))
    assert not result['errors']
    return result['ids'][0]


def event(video_id, stamp=3, name='玉佩', end=None):
    return server.insert_event(video_id, {
        'id': uuid.uuid4().hex, 'kind': 'relic', 'name': name, 'time': stamp,
        'end_time': stamp + .1 if end is None else end, 'source': 'ocr',
        'text': '获得：' + name, 'confidence': .99,
        'evidence': 'profile:obtained:pickup',
    })


@pytest.fixture
def selection(client):
    c, path = client
    p = new_project(c)
    vid = import_fixture(c, path, p)
    eid = event(vid)
    return c, p, vid, eid


def payload(p, eid, **options):
    return {'project_id': p['id'], 'items': [{'event_id': eid}], **options}


def preview(c, body):
    return good(c.post('/api/export/batch/preview', json=body))


def capture_pool(monkeypatch):
    captured = []
    monkeypatch.setattr(server.media_pool, 'submit',
                        lambda function, *args, **kwargs: captured.append((function, args, kwargs)))
    return captured


def test_preview_sorts_by_video_import_then_time_not_selection_or_filename(client, tmp_path):
    c, source = client
    p = new_project(c)
    first_path, second_path = tmp_path / 'z-imported-first.mp4', tmp_path / 'a-imported-second.mp4'
    for path in (first_path, second_path):
        path.write_bytes(source.read_bytes())
    first = import_fixture(c, first_path, p)
    second = import_fixture(c, second_path, p)
    later = event(first, 6, '第一录像后段')
    early = event(first, 2, '第一录像前段')
    second_early = event(second, 1, '第二录像开头')
    body = {'project_id': p['id'], 'before': .5, 'after': .5, 'mode': 'merge',
            'items': [{'event_id': second_early}, {'event_id': later}, {'event_id': early}]}
    result = preview(c, body)
    assert [r['event_id'] for r in result['clips']] == [early, later, second_early]
    assert [r['video_id'] for r in result['clips']] == [first, first, second]
    assert result['count'] == 3 and result['duration'] == pytest.approx(3)
    assert result['mode'] == 'merge'


def test_padding_is_around_timestamp_not_observed_end_and_can_be_per_item(selection):
    c, p, vid, eid = selection
    execute('UPDATE events SET end_time=7 WHERE id=?', (eid,))
    at_end = event(vid, 7.75, '结尾处')
    result = preview(c, {
        'project_id': p['id'], 'before': 1, 'after': 2, 'mode': 'separate',
        'items': [{'event_id': eid}, {'event_id': at_end, 'before': .25, 'after': 3.5}],
    })
    first, second = result['clips']
    assert (first['start'], first['end'], first['duration']) == (2, 5, 3)
    assert (second['start'], second['end'], second['duration']) == (7.5, 8, .5)
    assert (second['before'], second['after']) == (.25, 3.5)
    assert result['duration'] == pytest.approx(3.5)
    assert {'event_id', 'video_id', 'video_name', 'name', 'time'} <= first.keys()


def test_default_padding_is_five_seconds_and_bounds_are_clipped(selection):
    c, p, _, eid = selection
    clip = preview(c, payload(p, eid))['clips'][0]
    assert (clip['before'], clip['after']) == (5, 5)
    assert (clip['start'], clip['end'], clip['duration']) == (0, 8, 8)


@pytest.mark.parametrize('changes', [
    {'items': []}, {'items': None}, {'items': 'not a list'},
    {'items': [{}]}, {'items': [None]}, {'items': [{'event_id': 3}]},
    {'before': -1}, {'after': -1}, {'before': 'NaN'}, {'after': 'Infinity'},
    {'before': 86400.1}, {'after': 86400.1}, {'before': []},
    {'before': 0, 'after': 0}, {'mode': 'invalid'},
    {'before': True}, {'after': False}, {'project_id': ''}, {'project_id': None},
])
def test_invalid_batch_input_cannot_create_a_job(selection, changes):
    c, p, _, eid = selection
    before = query('SELECT * FROM jobs ORDER BY id')
    body = {**payload(p, eid), **changes}
    for route in ('/api/export/batch/preview', '/api/export/batch'):
        response = c.post(route, json=body)
        assert response.status_code == 400, (route, body, response.text)
    assert query('SELECT * FROM jobs ORDER BY id') == before


def test_duplicate_selection_is_rejected(selection):
    c, p, _, eid = selection
    response = c.post('/api/export/batch', json=payload(p, eid,
        items=[{'event_id': eid}, {'event_id': eid, 'before': 1}]))
    assert response.status_code == 400, response.text


def test_zero_length_after_boundary_clamping_is_rejected(selection):
    c, p, vid, _ = selection
    at_start, at_end = event(vid, 0, '开头'), event(vid, 8, '结尾')
    for eid, before, after in ((at_start, 5, 0), (at_end, 0, 5)):
        response = c.post('/api/export/batch/preview', json=payload(p, eid, before=before, after=after))
        assert response.status_code == 400, response.text


def test_hidden_deleted_and_unknown_events_never_fall_back_to_raw_rows(selection):
    c, p, vid, visible = selection
    hidden = event(vid, 5, '玉佩')
    assert [r['id'] for r in good(c.get('/api/events', params={'project_id': p['id']}))['events']] == [visible]
    deleted = event(vid, 6, '删除的条目')
    good(c.delete('/api/events/' + deleted))
    for eid in (hidden, deleted, 'does-not-exist'):
        for route in ('/api/export/batch/preview', '/api/export/batch'):
            response = c.post(route, json=payload(p, eid))
            assert response.status_code == 409, (route, eid, response.text)


def test_explicit_project_controls_export_even_if_active_project_differs(selection, client):
    c, p, _, eid = selection
    _, source = client
    other = new_project(c)
    foreign_vid = import_fixture(c, source, other)
    foreign = event(foreign_vid, 1, '其他项目物品')
    good(c.post('/api/projects/' + other['id'] + '/activate', json={}))
    assert preview(c, payload(p, eid))['clips'][0]['event_id'] == eid
    for body in (payload(p, foreign), payload(p, eid, items=[{'event_id': eid}, {'event_id': foreign}])):
        response = c.post('/api/export/batch', json=body)
        assert response.status_code == 409, response.text


def test_changed_source_is_rejected_before_queuing(client, tmp_path):
    c, source = client
    p = new_project(c)
    path = tmp_path / 'changed-source.mp4'
    path.write_bytes(source.read_bytes())
    vid = import_fixture(c, path, p)
    eid = event(vid)
    with path.open('ab') as handle:
        handle.write(b'changed after import')
    before = query('SELECT id FROM jobs ORDER BY id')
    response = c.post('/api/export/batch', json=payload(p, eid))
    assert response.status_code == 409, response.text
    assert query('SELECT id FROM jobs ORDER BY id') == before


def test_preview_does_not_write_events_or_jobs(selection):
    c, p, _, eid = selection
    events_before = query('SELECT * FROM events ORDER BY id')
    jobs_before = query('SELECT * FROM jobs ORDER BY id')
    preview(c, payload(p, eid))
    assert query('SELECT * FROM events ORDER BY id') == events_before
    assert query('SELECT * FROM jobs ORDER BY id') == jobs_before


def test_client_paths_timestamps_and_duration_never_override_catalog(selection):
    c, p, vid, eid = selection
    clip = preview(c, payload(p, eid, items=[{
        'event_id': eid, 'video_id': 'another-video', 'path': 'C:/wrong.mp4',
        'time': 1000, 'start': 0, 'end': 1000, 'duration': 1000,
        'before': .5, 'after': 1,
    }]))['clips'][0]
    assert clip['video_id'] == vid and clip['time'] == 3
    assert (clip['start'], clip['end'], clip['duration']) == (2.5, 4, 1.5)
    assert not {'path', 'source_size', 'source_mtime'}.intersection(clip)


def test_queued_batch_freezes_selected_timestamps_and_settings(selection, monkeypatch):
    from app import batch_export
    c, p, vid, eid = selection
    captured = capture_pool(monkeypatch)
    response = good(c.post('/api/export/batch', json=payload(p, eid,
        before=.5, after=1.25, mode='separate', name='冻结的批量导出')))
    assert response['count'] == 1 and response['duration'] == pytest.approx(1.75)
    assert response['mode'] == 'separate' and len(captured) == 1
    job_id = response['job_id']
    job = query('SELECT * FROM jobs WHERE id=?', (job_id,), True)
    assert job['kind'] == 'export_batch' and job['status'] == 'queued'
    assert job['video_id'] == vid
    frozen = []
    def fake_render(clips, output_dir, mode, name, progress):
        frozen.append(copy.deepcopy(clips))
        assert mode == 'separate' and name == '冻结的批量导出'
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / '001_原事件.mp4'
        target.write_bytes(b'fixture output')
        progress(1)
        return {'output_path': str(output_dir), 'files': [str(target)], 'clips': clips}
    monkeypatch.setattr(batch_export, 'render_batch', fake_render)
    good(c.patch('/api/events/' + eid, json={'name': '提交后改名', 'time': 6, 'end_time': 6.1}))
    # A project switch after submission must not redirect the job.
    other = new_project(c)
    good(c.post('/api/projects/' + other['id'] + '/activate', json={}))
    fn, args, kwargs = captured[0]
    fn(*args, **kwargs)
    assert len(frozen) == 1
    assert frozen[0][0]['name'] == '玉佩' and frozen[0][0]['time'] == 3
    assert (frozen[0][0]['start'], frozen[0][0]['end']) == (2.5, 4.25)
    finished = query('SELECT * FROM jobs WHERE id=?', (job_id,), True)
    assert finished['status'] == 'done' and finished['progress'] == 1
    in_project = good(c.get('/api/state', params={'project_id': p['id']}))['jobs']
    in_other = good(c.get('/api/state', params={'project_id': other['id']}))['jobs']
    assert job_id in {r['id'] for r in in_project}
    assert job_id not in {r['id'] for r in in_other}
    opened = []
    monkeypatch.setattr(server.os, 'startfile', lambda path: opened.append(Path(path)))
    good(c.post('/api/jobs/' + job_id + '/reveal', json={}))
    assert opened == [Path(finished['output_path'])]


def test_render_failure_is_not_reported_as_a_completed_export(selection, monkeypatch):
    from app import batch_export
    c, p, _, eid = selection
    captured = capture_pool(monkeypatch)
    job_id = good(c.post('/api/export/batch', json=payload(p, eid, mode='merge')))['job_id']
    def failed(*args, **kwargs):
        raise RuntimeError('测试编码器失败')
    monkeypatch.setattr(batch_export, 'render_batch', failed)
    fn, args, kwargs = captured[0]
    fn(*args, **kwargs)
    job = query('SELECT * FROM jobs WHERE id=?', (job_id,), True)
    assert job['status'] == 'error' and '测试编码器失败' in job['message']
    assert c.get('/api/jobs/' + job_id + '/download').status_code == 404
    assert c.post('/api/jobs/' + job_id + '/reveal', json={}).status_code == 404


def test_source_changed_while_waiting_fails_without_starting_renderer(client, tmp_path, monkeypatch):
    from app import batch_export
    c, source = client
    p = new_project(c)
    path = tmp_path / 'source-change-in-queue.mp4'
    path.write_bytes(source.read_bytes())
    vid = import_fixture(c, path, p)
    eid = event(vid)
    captured = capture_pool(monkeypatch)
    job_id = good(c.post('/api/export/batch', json=payload(p, eid)))['job_id']
    with path.open('ab') as handle:
        handle.write(b'changed while queued')
    called = []
    monkeypatch.setattr(batch_export, 'render_batch', lambda *a, **k: called.append((a, k)))
    fn, args, kwargs = captured[0]
    fn(*args, **kwargs)
    job = query('SELECT * FROM jobs WHERE id=?', (job_id,), True)
    assert not called and job['status'] == 'error'
    assert '改变' in job['message']


def test_completed_merge_has_one_downloadable_output_and_reveals_its_parent(selection, monkeypatch):
    from app import batch_export
    c, p, _, eid = selection
    captured = capture_pool(monkeypatch)
    job_id = good(c.post('/api/export/batch', json=payload(p, eid, mode='merge')))['job_id']
    def merged(clips, output_dir, mode, name, progress):
        assert mode == 'merge'
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / '合并片段.mp4'
        target.write_bytes(b'merged-fixture-output')
        progress(1)
        return {'output_path': str(target), 'files': [str(target)], 'clips': clips}
    monkeypatch.setattr(batch_export, 'render_batch', merged)
    fn, args, kwargs = captured[0]
    fn(*args, **kwargs)
    job = query('SELECT * FROM jobs WHERE id=?', (job_id,), True)
    assert job['status'] == 'done'
    response = c.get('/api/jobs/' + job_id + '/download')
    assert response.status_code == 200 and response.content == b'merged-fixture-output'
    opened = []
    monkeypatch.setattr(server.os, 'startfile', lambda path: opened.append(Path(path)))
    good(c.post('/api/jobs/' + job_id + '/reveal', json={}))
    assert opened == [Path(job['output_path']).parent]


def test_single_export_still_uses_exact_explicit_bounds(selection, monkeypatch):
    c, _, vid, _ = selection
    captured = capture_pool(monkeypatch)
    result = good(c.post('/api/export', json={'video_id': vid, 'start': 1.25, 'end': 5.75, 'name': '原精确导出'}))
    job = query('SELECT * FROM jobs WHERE id=?', (result['job_id'],), True)
    assert job['kind'] == 'export' and len(captured) == 1
    fn, args, _ = captured[0]
    assert fn == server.run_render and args[-2:] == (1.25, 5.75)
    execute("UPDATE jobs SET status='interrupted' WHERE id=?", (result['job_id'],))
