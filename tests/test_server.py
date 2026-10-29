"""Real HTTP, database, range playback, validation and export tests."""
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Run this file with FOOTAGE_ROOT pointing to a dedicated test directory.
assert os.environ.get('FOOTAGE_TEST')=='1', 'Set FOOTAGE_TEST=1 and a dedicated FOOTAGE_ROOT for API tests.'
TEST_ROOT=Path(os.environ['FOOTAGE_ROOT'])
(TEST_ROOT/'static').mkdir(parents=True,exist_ok=True)
from app import server
from app.storage import query, execute, DATA

@pytest.fixture(scope='module')
def client():
    clip=TEST_ROOT/'range-test.mp4'
    subprocess.run([server.media.FFMPEG,'-v','error','-y','-f','lavfi','-i','color=c=gray:s=320x180:r=10:d=3','-c:v','libx264','-pix_fmt','yuv420p',str(clip)],check=True,creationflags=server.media.CREATE_NO_WINDOW)
    with TestClient(server.app) as c:
        yield c,clip

def test_import_is_idempotent_and_not_copied(client):
    c,path=client
    first=c.post('/api/import',json={'paths':[str(path)],'auto_analyze':False}).json()
    second=c.post('/api/import',json={'paths':[str(path)],'auto_analyze':False}).json()
    assert first['ids']==second['ids'] and not first['errors']
    state=c.get('/api/state').json()
    assert len(state['videos'])==1
    assert state['videos'][0]['path']==str(path.resolve())
    assert state['videos'][0]['duration']==pytest.approx(3,.1)

def test_range_playback(client):
    c,_=client
    vid=c.get('/api/state').json()['videos'][0]['id']
    response=c.get('/api/media/'+vid,headers={'Range':'bytes=0-99'})
    assert response.status_code==206
    assert len(response.content)==100
    assert response.headers['content-range'].startswith('bytes 0-99/')
    assert c.get('/api/media/'+vid,headers={'Range':'bytes=999999999-'}).status_code==416

def test_event_search_edit_and_csv(client):
    c,_=client
    vid=c.get('/api/state').json()['videos'][0]['id']
    event=c.post('/api/events',json={'video_id':vid,'time':1,'kind':'item','name':'测试玉佩'}).json()['id']
    rows=c.get('/api/events',params={'q':'玉佩','kind':'item'}).json()
    assert rows['total']==1 and rows['events'][0]['verified']==1
    assert c.get(rows['events'][0]['screenshot_url']).status_code==200
    assert c.patch('/api/events/'+event,json={'name':'=危险公式','verified':False}).status_code==200
    result=c.get('/api/export/events.csv').text
    assert "'=危险公式" in result
    assert c.patch('/api/events/'+event,json={'kind':'unknown'}).status_code==400
    assert c.patch('/api/events/'+event,json={'time':-1}).status_code==400
    assert c.patch('/api/events/'+event,json={'end_time':0}).status_code==400
    assert c.delete('/api/events/'+event).status_code==200
    assert c.get('/api/events',params={'q':'危险公式'}).json()['total']==0

def test_local_access_and_validation(client):
    c,path=client
    assert c.post('/api/import',json={'paths':[]}).status_code==400
    assert c.post('/api/import',json={'paths':[str(path)],'auto_analyze':False},headers={'Origin':'https://external.example'}).status_code==403
    assert c.get('/api/state',headers={'Host':'external.example'}).status_code==403
    assert c.post('/api/settings',json={'sample_interval':0}).status_code==400
    assert c.post('/api/settings',json={'regions':[{'x':.8,'y':0,'w':.5,'h':1}]}).status_code==400
    assert c.post('/api/settings',json={'keywords':{'item':'bad'}}).status_code==400
    assert c.post('/api/settings',json={'sample_interval':.5,'regions':[]}).status_code==200
    vid=c.get('/api/state').json()['videos'][0]['id']
    assert c.post('/api/export',json={'video_id':vid,'start':2,'end':1}).status_code==400


def test_collection_scope_filters_catalog_and_accepts_new_categories(client):
    import json
    from app.collection_taxonomy import COLLECTION_KINDS
    c,path=client
    vid=c.post('/api/import',json={'paths':[str(path)],'auto_analyze':False}).json()['ids'][0]
    prior=query('SELECT options FROM videos WHERE id=?',(vid,),True)['options']
    execute('UPDATE videos SET options=? WHERE id=?',(json.dumps({'recognition_scope':'collection_only','text_mode':'off'}),vid))
    ids=[]
    try:
        for kind in COLLECTION_KINDS:
            ids.append(server.insert_event(vid,{'name':'分类验证'+kind,'kind':kind,'time':1,'source':'ocr','evidence':'collection_panel'}))
        ids.append(server.insert_event(vid,{'name':'普通教程字幕验证','kind':'text','time':1,'source':'ocr'}))
        assert c.get('/api/events',params={'video_id':vid,'q':'普通教程字幕验证'}).json()['total']==0
        for kind in COLLECTION_KINDS:
            result=c.get('/api/events',params={'video_id':vid,'kind':kind,'q':'分类验证'}).json()
            assert result['total']==1 and result['events'][0]['kind']==kind
        assert c.post('/api/settings',json={'recognition_scope':'unknown'}).status_code==400
        options=server.validate_options({'recognition_scope':'collection_only','text_mode':'all'})
        assert options['text_mode']=='off'
    finally:
        for eid in ids: execute('DELETE FROM events WHERE id=?',(eid,))
        execute('UPDATE videos SET options=? WHERE id=?',(prior,vid))

def test_machine_upsert_preserves_human_edits(client):
    c,_=client
    vid=c.get('/api/state').json()['videos'][0]['id']
    eid=server.insert_event(vid,{'id':'upsert-test','kind':'enemy','name':'训练敌人','time':1,'end_time':1,'source':'ocr'})
    c.patch('/api/events/'+eid,json={'name':'人工修正的敌人','verified':True})
    server.insert_event(vid,{'id':'upsert-test','kind':'enemy','name':'训练敌人','time':1,'end_time':2,'source':'ocr'})
    row=query('SELECT * FROM events WHERE id=?',(eid,),True)
    assert row['name']=='人工修正的敌人' and row['verified']==1 and row['end_time']==1

def test_exact_clip_export(client):
    c,_=client
    vid=c.get('/api/state').json()['videos'][0]['id']
    video=server.video_or_404(vid)
    jid='export-test'
    output=TEST_ROOT/'exports'/'short.mp4'
    execute("INSERT INTO jobs(id,video_id,kind,status) VALUES (?,?,?,?)",(jid,vid,'export','queued'))
    server.run_render(jid,video,'export',output,.5,1.5)
    job=query('SELECT * FROM jobs WHERE id=?',(jid,),True)
    assert job['status']=='done',job
    assert server.media.probe(output)['duration']==pytest.approx(1,abs=.15)

def test_resume_merges_overlap_but_preserves_new_acquisition(client):
    c,_=client
    vid=c.get('/api/state').json()['videos'][0]['id']
    base={'kind':'item','name':'持续提示物品','text':'获得：持续提示物品','source':'ocr'}
    a=server.save_ocr_event(vid,{**base,'id':'first-pass','time':.2,'end_time':1.5})
    b=server.save_ocr_event(vid,{**base,'id':'resumed-pass','time':1,'end_time':2})
    d=server.save_ocr_event(vid,{**base,'id':'new-acquisition','time':2.4,'end_time':2.9})
    assert a==b and d!=a
    rows=query('SELECT * FROM events WHERE name=? ORDER BY time',('持续提示物品',))
    assert len(rows)==2 and rows[0]['time']==.2 and rows[0]['end_time']==2
    c.patch('/api/events/'+a,json={'name':'已校正名称','time':.1,'end_time':1.9,'verified':True})
    server.save_ocr_event(vid,{**base,'id':'another-resume','time':1,'end_time':2.1})
    checked=query('SELECT * FROM events WHERE id=?',(a,),True)
    assert checked['name']=='已校正名称' and checked['end_time']==1.9

def test_replaced_file_cannot_play_through_old_index_and_ids_do_not_collide(client):
    c,path=client
    old_id=c.get('/api/state').json()['videos'][0]['id']
    # A changed length at the same path is a distinct import, even if video data still decodes.
    with path.open('ab') as f: f.write(b'changed')
    assert c.get('/api/media/'+old_id).status_code==409
    new_id=c.post('/api/import',json={'paths':[str(path)],'auto_analyze':False}).json()['ids'][0]
    assert old_id!=new_id
    e={'id':'same-analyzer-id','kind':'item','name':'同位置物品','time':1,'source':'ocr'}
    first=server.insert_event(old_id,e)
    second=server.insert_event(new_id,e)
    assert first!=second
    assert query('SELECT video_id FROM events WHERE id=?',(second,),True)['video_id']==new_id

def test_deleted_candidate_does_not_return_on_resume(client):
    c,_=client
    vid=c.get('/api/state').json()['videos'][0]['id']
    e={'id':'reject-original','kind':'item','name':'测试误报','text':'获得：测试误报','time':1,'end_time':2,'source':'ocr'}
    first=server.save_ocr_event(vid,e)
    assert c.delete('/api/events/'+first).status_code==200
    server.save_ocr_event(vid,{**e,'end_time':2.4})
    server.save_ocr_event(vid,{**e,'id':'reject-resumed','time':1.5,'end_time':2.4})
    assert c.get('/api/events',params={'q':'测试误报'}).json()['total']==0

def test_exports_stay_local_and_reveal_correct_folder(client,monkeypatch):
    c,_=client
    opened=[]
    monkeypatch.setattr(server.os,'startfile',lambda p:opened.append(Path(p)))
    result=c.post('/api/export/events-file',json={})
    assert result.status_code==200,result.text
    path=Path(result.json()['path'])
    assert path.parent==TEST_ROOT/'exports' and path.read_bytes().startswith(b'\xef\xbb\xbf')
    assert opened[-1]==TEST_ROOT/'exports'
    execute('UPDATE jobs SET output_path=? WHERE id=?',(str(TEST_ROOT/'exports'/'short.mp4'),'export-test'))
    assert c.post('/api/jobs/export-test/reveal').status_code==200
    assert opened[-1]==TEST_ROOT/'exports'

def test_visual_evidence_updates_and_reviewed_evidence_is_preserved(client):
    c,_=client
    vid=c.get('/api/state').json()['videos'][0]['id']
    base={'id':'visual-score-update','kind':'enemy','name':'测试首领','time':1,'end_time':1.5,'source':'ocr'}
    event_id=server.insert_event(vid,base)
    server.insert_event(vid,{**base,'evidence':'victory_template','detection_score':.7})
    server.insert_event(vid,{**base,'evidence':'victory_template','detection_score':.88,'end_time':2})
    server.insert_event(vid,{**base,'detection_score':.65})
    row=query('SELECT * FROM events WHERE id=?',(event_id,),True)
    assert row['evidence']=='victory_template' and row['detection_score']==.88 and row['end_time']==2
    execute('UPDATE events SET verified=1 WHERE id=?',(event_id,))
    server.insert_event(vid,{**base,'evidence':'changed','detection_score':.99,'end_time':2.5})
    row=query('SELECT * FROM events WHERE id=?',(event_id,),True)
    assert row['evidence']=='victory_template' and row['detection_score']==.88 and row['end_time']==2

def test_navigation_categories_and_text_modes(client):
    c,path=client
    vid=c.post('/api/import',json={'paths':[str(path)],'auto_analyze':False}).json()['ids'][0]
    for kind,name in [('waypoint','测试传送点'),('quest','测试任务目标'),('weapon','测试武器'),('boss','测试BOSS')]:
        response=c.post('/api/events',json={'video_id':vid,'kind':kind,'name':name,'time':1})
        assert response.status_code==200,response.text
        found=c.get('/api/events',params={'video_id':vid,'kind':kind,'q':name}).json()
        assert found['total']==1 and found['events'][0]['kind']==kind
        event_id=response.json()['id']
        assert c.patch('/api/events/'+event_id,json={'kind':kind,'verified':False}).status_code==200
    csv_text=c.get('/api/export/events.csv',params={'video_id':vid}).text
    assert all(label in csv_text for label in ('传送点','任务','武器','BOSS'))
    for mode in ('compact','all','off'):
        response=c.post('/api/settings',json={'text_mode':mode})
        assert response.status_code==200 and response.json()['text_mode']==mode
    assert c.post('/api/settings',json={'text_mode':'unsupported'}).status_code==400

def test_compact_text_spelling_updates_but_human_correction_survives(client):
    c,path=client
    vid=c.post('/api/import',json={'paths':[str(path)],'auto_analyze':False}).json()['ids'][0]
    base={'id':'text-local-track','kind':'text','name':'测试称号','text':'测试称号','time':1,'end_time':2,'source':'ocr','confidence':.94}
    event_id=server.save_ocr_event(vid,base)
    server.save_ocr_event(vid,{**base,'name':'测试称号:完整姓名','text':'测试称号:完整姓名','confidence':.9})
    row=query('SELECT * FROM events WHERE id=?',(event_id,),True)
    assert row['name']=='测试称号:完整姓名' and row['confidence']==.9
    c.patch('/api/events/'+event_id,json={'name':'人工校对名称','verified':True}).raise_for_status()
    server.save_ocr_event(vid,{**base,'name':'后续机器读数','confidence':.99})
    assert query('SELECT name FROM events WHERE id=?',(event_id,),True)['name']=='人工校对名称'


def test_first_waypoint_search_pagination_review_cache_and_csv(client):
    c,path=client
    vid=c.post('/api/import',json={'paths':[str(path)],'auto_analyze':False}).json()['ids'][0]
    base={'kind':'waypoint','name':'目录去重桥','source':'ocr','verified':0,'text':'残偶菜单'}
    first=server.insert_event(vid,{**base,'id':'group-first','time':.2,'end_time':.4})
    second=server.insert_event(vid,{**base,'id':'group-second','time':2,'end_time':2.2})
    params={'video_id':vid,'q':'目录去重桥','kind':'waypoint','limit':1}
    data=c.get('/api/events',params=params).json()
    assert data['total']==1 and len(data['events'])==1
    group=data['events'][0]
    assert group['first_occurrence_only'] and group['occurrence_count']==1
    assert group['id']==first and group['time']==.2 and group['end_time']==.4
    assert group['occurrences']==[]
    csv_text=c.get('/api/export/events.csv',params={'video_id':vid}).text
    assert csv_text.count('目录去重桥')==1 and '所有出现时间' not in csv_text
    c.patch('/api/events/'+second,json={'verified':True}).raise_for_status()
    assert c.get('/api/events',params=params).json()['total']==1
    assert c.get('/api/events',params={**params,'verified':'1'}).json()['total']==0
    c.delete('/api/events/'+first).raise_for_status()
    remaining=c.get('/api/events',params=params).json()
    assert remaining['total']==1 and remaining['events'][0]['id']==second
    c.patch('/api/events/'+second,json={'name':'修改之后桥'}).raise_for_status()
    assert c.get('/api/events',params=params).json()['total']==0


def test_idle_library_over_four_videos_does_not_rebuild_every_poll(client,monkeypatch):
    c,path=client
    for i in range(5):
        execute('INSERT INTO videos(id,path,fingerprint,name,duration,options) VALUES (?,?,?,?,?,?)',
                (f'cache-video-{i}',str(path),f'cache-fingerprint-{i}',f'cache {i}',3,'{}'))
        server.insert_event(f'cache-video-{i}',{'name':'缓存地点','kind':'text','time':1,'source':'ocr'})
    calls=[]; original=server.group_events
    def measured(*args,**kwargs):
        calls.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(server,'group_events',measured)
    server.catalog_cache.clear();server.catalog_counts.clear();server.event_views.clear()
    c.get('/api/state').raise_for_status()
    first=len(calls);assert first>=6
    c.get('/api/state').raise_for_status()
    assert len(calls)==first
    c.get('/api/events').raise_for_status()
    second=len(calls)
    c.get('/api/events').raise_for_status()
    assert len(calls)==second
    server.insert_event('cache-video-0',{'name':'新地点','kind':'text','time':2,'source':'ocr'})
    state=c.get('/api/state').json()
    assert next(v for v in state['videos'] if v['id']=='cache-video-0')['event_count']==2


def test_resume_restores_nearby_saved_boss_context(client,monkeypatch):
    import threading
    from app import analyzer
    c,path=client
    vid=c.post('/api/import',json={'paths':[str(path)],'auto_analyze':False}).json()['ids'][0]
    server.insert_event(vid,{'id':'resume-boss-context','kind':'text','name':'守门人:张三',
        'time':1.7,'end_time':2.5,'source':'ocr','evidence':'boss_name','confidence':.85})
    captured=[]
    def fake_analyze(path,out,options,*callbacks):
        captured.append(options)
        return {'status':'completed','events':0}
    monkeypatch.setattr(analyzer,'analyze',fake_analyze)
    server.run_analysis(vid,{'resume_from':1.8},threading.Event())
    context=captured[0]['_boss_text_resume']
    assert context=={'name':'守门人:张三','confidence':.85,'last_seen':1.8}
