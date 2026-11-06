"""Cross-game project isolation and immutable recognition settings over real HTTP.

Run in its own pytest process with FOOTAGE_TEST=1 and an empty FOOTAGE_ROOT.
The videos here are generated fixtures; no production recordings are imported.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

import pytest
from fastapi.testclient import TestClient

assert os.environ.get('FOOTAGE_TEST') == '1', 'Use an isolated FOOTAGE_ROOT and FOOTAGE_TEST=1.'
TEST_ROOT = Path(os.environ['FOOTAGE_ROOT']).resolve()
PROJECT_ROOT = Path(__file__).resolve().parents[1]
assert TEST_ROOT != PROJECT_ROOT, 'Never run project API tests against the real library.'
(TEST_ROOT / 'static').mkdir(parents=True, exist_ok=True)

from app import server
from app.storage import execute, query


def good(response):
    assert 200 <= response.status_code < 300, response.text
    return response.json()


def unwrapped(value, key):
    return value.get(key, value)


def scheme(*, policy='first', label='遗物'):
    return {
        'id': 'test_' + uuid.uuid4().hex[:12], 'name': '测试游戏方案',
        'adapter': 'generic', 'language': 'zh', 'version': 1,
        'categories': [{'id': 'relic', 'label': label, 'group': '收集',
                        'enabled': True, 'policy': policy}],
        'rules': [{'id': 'relic_popup', 'category': 'relic', 'trigger': '获得',
                   'semantic': 'obtained', 'region': None, 'name_region': None,
                   'exclude': ['获取途径']}],
        'regions': [], 'exclude_text': ['背包', '商店'], 'aliases': {},
    }


def new_profile(c, payload=None):
    return unwrapped(good(c.post('/api/profiles', json=payload or scheme())), 'profile')


def new_project(c, profile=None, name='测试周目'):
    profile = profile or new_profile(c)
    p = unwrapped(good(c.post('/api/projects', json={
        'name': name, 'profile_id': profile['id'], 'profile_version': profile['version'],
    })), 'project')
    return p, profile


def activate(c, p):
    return good(c.post('/api/projects/' + p['id'] + '/activate', json={}))


def import_fixture(c, path, p):
    result = good(c.post('/api/import', json={
        'paths': [str(path)], 'auto_analyze': False, 'project_id': p['id'],
    }))
    assert result['errors'] == []
    assert len(result['ids']) == 1
    return result['ids'][0]


def catalog(c, p, **params):
    return good(c.get('/api/events', params={'project_id': p['id'], **params}))


def machine(vid, name, stamp, *, ident=None, evidence='profile:obtained:relic_popup'):
    return server.insert_event(vid, {'id': ident or uuid.uuid4().hex, 'kind': 'relic',
        'name': name, 'time': stamp, 'end_time': stamp + .1, 'source': 'ocr',
        'text': '获得：' + name, 'evidence': evidence, 'confidence': .95})


@pytest.fixture(scope='module')
def client():
    clip = TEST_ROOT / 'multi-game-fixture.mp4'
    subprocess.run([server.media.FFMPEG, '-v', 'error', '-y', '-f', 'lavfi',
        '-i', 'color=c=gray:s=640x360:r=10:d=4', '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p', str(clip)], check=True, creationflags=server.media.CREATE_NO_WINDOW)
    with TestClient(server.app) as c:
        yield c, clip


def test_builtin_profiles_and_default_project_are_exposed(client):
    c, _ = client
    state = good(c.get('/api/state'))
    assert state['active_project_id']
    assert any(p['id'] == state['active_project_id'] for p in state['projects'])
    assert state['profile']['adapter'] == 'phantom_blade_zero_demo'
    assert state['categories']
    profiles = good(c.get('/api/profiles'))['profiles']
    assert {'generic', 'phantom_blade_zero_demo'} <= {p['adapter'] for p in profiles}
    assert {p['id'] for p in state['profiles']} == {p['id'] for p in profiles}


def test_same_file_is_idempotent_within_project_and_independent_between_projects(client):
    c, clip = client
    p1, profile = new_project(c, name='第一周目')
    p2, _ = new_project(c, profile, name='第二周目')
    v1 = import_fixture(c, clip, p1)
    assert import_fixture(c, clip, p1) == v1
    v2 = import_fixture(c, clip, p2)
    assert v2 != v1
    assert query('SELECT path FROM videos WHERE id=?', (v1,), True)['path'] == str(clip)
    assert query('SELECT path FROM videos WHERE id=?', (v2,), True)['path'] == str(clip)
    machine(v1, '同名古印', .5)
    machine(v2, '同名古印', .7)
    for p, vid in ((p1, v1), (p2, v2)):
        state = good(c.get('/api/state', params={'project_id': p['id']}))
        assert [v['id'] for v in state['videos']] == [vid]
        rows = catalog(c, p, q='同名古印')
        assert rows['total'] == 1 and rows['events'][0]['video_id'] == vid
    activate(c, p2)
    active = good(c.get('/api/state'))
    assert active['active_project_id'] == p2['id']
    assert [v['id'] for v in active['videos']] == [v2]
    assert good(c.get('/api/events', params={'q': '同名古印'}))['events'][0]['video_id'] == v2


def test_dedup_uses_import_order_before_single_video_filter(client, tmp_path):
    c, clip = client
    p, _ = new_project(c)
    second = tmp_path / 'second.mp4'
    second.write_bytes(clip.read_bytes())
    v1, v2 = import_fixture(c, clip, p), import_fixture(c, second, p)
    # Recognition completion and timestamps must not reverse video import order.
    machine(v2, '本周目唯一玉佩', .2)
    first = machine(v1, '本周目唯一玉佩', 2)
    rows = catalog(c, p, q='本周目唯一玉佩')
    assert rows['total'] == 1 and rows['events'][0]['id'] == first
    assert catalog(c, p, video_id=v2, q='本周目唯一玉佩')['total'] == 0
    assert catalog(c, p, video_id=v1, q='本周目唯一玉佩')['total'] == 1
    state = good(c.get('/api/state', params={'project_id': p['id']}))
    assert [v['id'] for v in state['videos']] == [v1, v2]
    assert [v['event_count'] for v in state['videos']] == [1, 0]


def test_custom_category_manual_edit_and_csv_respect_project(client):
    c, clip = client
    p1, _ = new_project(c)
    p2, _ = new_project(c)
    v1, v2 = import_fixture(c, clip, p1), import_fixture(c, clip, p2)
    first = good(c.post('/api/events', json={'video_id': v1, 'kind': 'relic',
                                           'name': '甲项目专属玉佩', 'time': 1}))['id']
    good(c.post('/api/events', json={'video_id': v2, 'kind': 'relic',
                                   'name': '乙项目专属古书', 'time': 1}))
    good(c.patch('/api/events/' + first, json={'kind': 'relic', 'name': '甲项目校对玉佩'}))
    assert c.patch('/api/events/' + first, json={'kind': 'nonexistent_kind'}).status_code == 400
    data = c.get('/api/export/events.csv', params={'project_id': p1['id']})
    assert data.status_code == 200
    assert '甲项目校对玉佩' in data.text and '遗物' in data.text
    assert '乙项目专属古书' not in data.text
    assert catalog(c, p1, q='乙项目专属古书')['total'] == 0


def test_settings_are_project_scoped_and_invalid_project_is_rejected(client):
    c, _ = client
    p1, _ = new_project(c)
    p2, _ = new_project(c)
    good(c.post('/api/settings', json={'project_id': p1['id'], 'sample_interval': .5}))
    good(c.post('/api/settings', json={'project_id': p2['id'], 'sample_interval': 2}))
    assert good(c.get('/api/settings', params={'project_id': p1['id']}))['sample_interval'] == .5
    assert good(c.get('/api/settings', params={'project_id': p2['id']}))['sample_interval'] == 2
    for route in ('/api/state', '/api/events', '/api/settings', '/api/export/events.csv'):
        response = c.get(route, params={'project_id': 'missing-project'})
        assert response.status_code == 404, (route, response.text)


def test_policy_change_restores_observations_without_reanalyzing_or_deleting(client):
    c, clip = client
    p, profile = new_project(c)
    activate(c, p)
    vid = import_fixture(c, clip, p)
    first = machine(vid, '反复获得的晶体', .5)
    later = machine(vid, '反复获得的晶体', 2.5)
    assert catalog(c, p, q='反复获得的晶体')['total'] == 1
    raw_before = query('SELECT * FROM events WHERE video_id=? ORDER BY id', (vid,))
    changed = copy.deepcopy(profile)
    changed['categories'][0]['policy'] = 'occurrence'
    saved = unwrapped(good(c.put('/api/profiles/' + profile['id'], json=changed)), 'profile')
    assert saved['version'] == profile['version'] + 1
    rows = catalog(c, p, q='反复获得的晶体')
    assert [e['id'] for e in rows['events']] == [first, later]
    assert query('SELECT * FROM events WHERE video_id=? ORDER BY id', (vid,)) == raw_before
    assert good(c.get('/api/state', params={'project_id': p['id']}))['profile']['version'] == saved['version']


def test_queued_analysis_keeps_its_profile_snapshot_when_scheme_changes(client, monkeypatch):
    c, clip = client
    p, profile = new_project(c)
    activate(c, p)
    vid = import_fixture(c, clip, p)
    captured = []
    monkeypatch.setattr(server.analysis_pool, 'submit', lambda function, *args, **kwargs: captured.append((function, args, kwargs)))
    good(c.post('/api/videos/' + vid + '/analyze', json={}))
    assert len(captured) == 1
    frozen = captured[0][1][1]['_profile']
    snapshot_before = copy.deepcopy(frozen)
    assert frozen['id'] == profile['id'] and frozen['version'] == profile['version']
    changed = copy.deepcopy(profile)
    changed['categories'][0]['label'] = '改名后的遗物'
    saved = unwrapped(good(c.put('/api/profiles/' + profile['id'], json=changed)), 'profile')
    assert saved['version'] > frozen['version']
    assert frozen == snapshot_before
    stored = json.loads(query('SELECT options FROM videos WHERE id=?', (vid,), True)['options'])
    assert stored['_profile'] == frozen
    # Removing a category while this queued run waits cannot relabel the run's
    # frozen candidates as notes when they are persisted.
    changed_again = copy.deepcopy(saved)
    changed_again['categories'][0]['id'] = 'new_relic'
    for rule in changed_again['rules']:
        if rule['category'] == 'relic': rule['category'] = 'new_relic'
    good(c.put('/api/profiles/' + profile['id'], json=changed_again))
    from app import analyzer
    def finishing_scan(path, out, options, on_event, on_progress, cancelled):
        on_event({'id':'frozen-category', 'kind':'relic', 'name':'排队前的类别', 'time':1,
                  'end_time':2, 'text':'获得 排队前的类别', 'source':'ocr',
                  'confidence':.99, 'evidence':'profile:obtained:relic_popup'})
        return {'status':'completed'}
    monkeypatch.setattr(analyzer, 'analyze', finishing_scan)
    function, args, kwargs = captured[0]
    function(*args, **kwargs)
    assert query('SELECT kind FROM events WHERE video_id=? AND name=?',
                 (vid, '排队前的类别'), True)['kind'] == 'relic'


def test_preview_keeps_absolute_time_and_never_writes_library_events(client, monkeypatch):
    from app import analyzer
    c, clip = client
    p, profile = new_project(c)
    vid = import_fixture(c, clip, p)
    before = query('SELECT * FROM events ORDER BY id')
    captured = []

    def fake_analyze(path, out, options, on_event, on_progress, cancelled):
        captured.append(copy.deepcopy(options))
        on_event({'id': 'preview-only', 'kind': 'relic', 'name': '只在试识别出现',
                  'time': 1.5, 'end_time': 1.8, 'text': '获得：只在试识别出现',
                  'source': 'ocr', 'confidence': .95, 'evidence': 'profile:obtained:relic_popup'})
        on_progress({'progress': 1, 'processed_seconds': 2, 'checkpoint': 2})
        return {'status': 'completed', 'events': 1, 'frames': 2}

    monkeypatch.setattr(analyzer, 'analyze', fake_analyze)
    result = good(c.post('/api/profiles/preview', json={'profile': profile, 'video_id': vid, 'start': 1, 'end': 2}))
    trial_id = result['id']
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        data = good(c.get('/api/trials/' + trial_id))
        if data['status'] in ('done', 'completed', 'error', 'failed'):
            break
        time.sleep(.02)
    assert data['status'] in ('done', 'completed'), data
    assert captured[0]['resume_from'] == 1 and captured[0]['analysis_end'] == 2
    assert data['events'][0]['time'] == 1.5
    assert data['events'][0]['name'] == '只在试识别出现'
    assert query('SELECT * FROM events ORDER BY id') == before
    assert c.post('/api/profiles/preview', json={'profile': profile, 'video_id': vid, 'start': 2, 'end': 1}).status_code == 400
    assert c.post('/api/profiles/preview', json={'profile': profile, 'video_id': vid, 'start': -1, 'end': 2}).status_code == 400
    assert c.post('/api/profiles/preview', json={'profile': profile, 'video_id': vid, 'start': 0, 'end': 100}).status_code == 400


def test_calibration_frame_is_a_real_image_and_timestamp_is_validated(client):
    c, clip = client
    p, _ = new_project(c)
    vid = import_fixture(c, clip, p)
    response = c.get('/api/videos/' + vid + '/frame', params={'time': 1})
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('image/')
    assert response.content.startswith((b'\xff\xd8', b'\x89PNG'))
    assert c.get('/api/videos/' + vid + '/frame', params={'time': -1}).status_code == 400
    assert c.get('/api/videos/' + vid + '/frame', params={'time': 1000}).status_code == 400


def test_recording_stays_in_project_where_first_mark_was_created(client,monkeypatch):
    from app import projects
    c,clip=client
    p1,_=new_project(c)
    p2,_=new_project(c)
    class Recording:
        def status(self): return {'recording':True,'video_path':str(clip)}
        def mark(self,kind,name): return {'id':'sample-mark','time':1,'video_path':str(clip)}
    monkeypatch.setattr(server,'bridge',Recording())
    activate(c,p1)
    good(c.post('/api/obs/mark',json={'kind':'note','name':'路线'}))
    activate(c,p2)
    good(c.post('/api/obs/mark',json={'kind':'note','name':'仍在同一录像中'}))
    assert projects.recording_project(clip)==p1['id']


def test_same_embedded_chapter_gets_an_independent_id_in_each_project(client,monkeypatch):
    c,clip=client
    p1,_=new_project(c)
    p2,_=new_project(c)
    original=server.media.probe
    def with_chapter(path):
        return {**original(path),'chapters':[{'start_time':'1','tags':{
            'title':'收集物品｜共同章节｜1234567890abcdef1234567890abcdef'}}]}
    monkeypatch.setattr(server.media,'probe',with_chapter)
    v1=import_fixture(c,clip,p1)
    v2=import_fixture(c,clip,p2)
    first=catalog(c,p1,q='共同章节')['events']
    second=catalog(c,p2,q='共同章节')['events']
    assert len(first)==len(second)==1
    assert first[0]['id']!=second[0]['id']
    assert first[0]['video_id']==v1 and second[0]['video_id']==v2


def test_editing_project_pinned_to_old_version_uses_its_own_config(client):
    c,_=client
    p1,profile=new_project(c)
    p2=unwrapped(good(c.post('/api/projects',json={'name':'独立周目','profile_id':profile['id']})),'project')
    first=copy.deepcopy(profile)
    first['categories'][0]['label']='第一项目的分类'
    first['exclude_text']=['第一项目独有的排除词']
    v2=unwrapped(good(c.put('/api/profiles/'+profile['id'],json={**first,'project_id':p1['id']})),'profile')
    pinned=good(c.get('/api/state',params={'project_id':p2['id']}))['profile']
    assert pinned['version']==profile['version']
    pinned['categories'][0]['label']='第二项目的分类'
    v3=unwrapped(good(c.put('/api/profiles/'+profile['id'],json={**pinned,'project_id':p2['id']})),'profile')
    assert v3['version']==v2['version']+1
    assert '第一项目独有的排除词' not in v3['exclude_text']
    assert good(c.get('/api/state',params={'project_id':p1['id']}))['profile']==v2
    assert good(c.get('/api/state',params={'project_id':p2['id']}))['profile']==v3
    assert c.put('/api/profiles/'+profile['id'],json={**pinned,'project_id':p2['id']}).status_code==400


def test_old_library_migrates_once_without_losing_records(tmp_path):
    # Isolate module globals, caches, and SQLite connections from the HTTP tests.
    legacy = tmp_path / 'legacy-library'
    (legacy / 'data').mkdir(parents=True)
    with sqlite3.connect(legacy / 'data/library.sqlite3') as db:
        db.executescript('''
CREATE TABLE videos(id TEXT PRIMARY KEY,path TEXT NOT NULL,fingerprint TEXT UNIQUE NOT NULL,name TEXT NOT NULL,
 duration REAL NOT NULL,width INTEGER,height INTEGER,codec TEXT,size INTEGER,mtime REAL,
 status TEXT DEFAULT 'ready',progress REAL DEFAULT 0,checkpoint REAL DEFAULT 0,message TEXT DEFAULT '',
 proxy_path TEXT DEFAULT '',proxy_status TEXT DEFAULT 'none',options TEXT DEFAULT '{}',created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE events(id TEXT PRIMARY KEY,video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
 kind TEXT NOT NULL,name TEXT NOT NULL,time REAL NOT NULL,end_time REAL,confidence REAL DEFAULT 1,
 text TEXT DEFAULT '',screenshot TEXT DEFAULT '',source TEXT DEFAULT 'manual',verified INTEGER DEFAULT 0,
 edited INTEGER DEFAULT 0,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
INSERT INTO videos(id,path,fingerprint,name,duration) VALUES ('legacy-video','E:/fixture.mp4','legacy-fingerprint','原素材',10);
INSERT INTO events(id,video_id,kind,name,time,end_time,source,verified,edited)
 VALUES ('legacy-manual','legacy-video','book','已人工校对的书籍',1,2,'manual',1,1);
INSERT INTO settings(key,value) VALUES ('sample_interval','0.5');
''')
    code = '''
import json
from app.storage import init_db,query
init_db()
first=query('SELECT * FROM events')
video=query('SELECT * FROM videos',one=True)
assert first[0]['id']=='legacy-manual' and first[0]['name']=='已人工校对的书籍'
assert first[0]['verified']==1 and first[0]['edited']==1
assert video['project_id']
projects=query('SELECT * FROM projects')
assert any(p['id']==video['project_id'] for p in projects)
init_db()
assert query('SELECT * FROM events')==first
assert query('SELECT * FROM projects')==projects
print(json.dumps({'records':len(first),'project_id':video['project_id']}))
'''
    env = {**os.environ, 'FOOTAGE_ROOT': str(legacy), 'FOOTAGE_TEST': '1',
           'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'}
    result = subprocess.run([sys.executable, '-c', code], cwd=PROJECT_ROOT, env=env,
                            capture_output=True, text=True, encoding='utf-8',
                            creationflags=server.media.CREATE_NO_WINDOW, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['records'] == 1
