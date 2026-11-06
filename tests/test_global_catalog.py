"""Whole-library chronology and provenance, with an isolated HTTP database."""
import csv
import io
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.event_catalog import group_events, library_events, import_order_key


def video(key, name, **kwargs):
    return {'id':key,'name':name,'path':'F:/recordings/'+name,'duration':120,
            'created_at':'2026-10-29 20:00:00',**kwargs}


def event(key, vid, stamp, name='开端之地', kind='bell', **kwargs):
    return {'id':key,'video_id':vid,'time':stamp,'end_time':stamp+2,'name':name,
            'kind':kind,'source':'ocr','first_occurrence_only':True,'verified':0,
            'confidence':.8,'screenshot':key+'.jpg','text':'首次画面',**kwargs}


def test_memory_chapter_identity_is_stable_across_videos():
    rows=[event('one','early',10,'某人的记忆之花 · 其三:崭新的力量',kind='memory_flower'),
          event('typo','late',1,'某人的记忆之花 · 其三:斩新的力量',kind='memory_flower'),
          event('two','late',20,'某人的记忆之花 · 其二:归来',kind='memory_flower')]
    result=library_events(rows,[video('early','1'),video('late','2')])
    assert [row['id'] for row in result] == ['one','two']


def test_november_frame_verified_aliases_keep_distinct_items_and_chapters():
    videos=[video('a','first.mkv',import_order=1),video('b','next.mkv',import_order=2)]
    rows=[event('drunk','a',90,'痴者醉剑',kind='weapon'),
          event('badge','b',1,'痴者醉剑切',kind='weapon'),
          event('broken','b',2,'痴者残剑',kind='weapon'),
          event('yard','a',100,'校场深处'),event('yard-typo','b',3,'校场滦处'),
          event('yard-distinct','b',4,'校场'),event('pillar','b',5,'龙柱'),
          event('mirror-pillar','b',6,'镜林龙柱'),
          event('flower','a',110,'关于怪物的记忆之花 · 其三:主谋',kind='memory_flower'),
          event('flower-typo','b',7,'怪物的记忆之花 · 其三:主谋',kind='memory_flower'),
          event('flower-other','b',8,'关于怪物的记忆之花 · 其二:真相',kind='memory_flower'),
          event('boss','a',120,'亿月刀偶:玉鼎',kind='boss'),
          event('boss-typo','b',9,'十二号:偃月刀偶',kind='boss')]
    result=library_events(group_events(rows,collection_only=True),videos)
    assert [r['id'] for r in result]==[
        'drunk','yard','flower','boss','broken','yard-distinct','pillar','mirror-pillar','flower-other']
    assert result[3]['name']=='十二号·偃月刀偶:玉鼎'


def test_verified_book_spelling_only_folds_same_continuous_chapter():
    videos=[video('a','first.mkv',import_order=1),video('b','next.mkv',import_order=2)]
    extra={'kind':'book','evidence':'collection_panel','edited':0}
    rows=[event('first','a',10,'庞镇众生相 · 字迹纤细的日记',**extra),
          event('typo','a',11,'庞镇众生相 · 宇迹纤细的日记',**extra),
          event('reopen','a',30,'庞镇众生相 · 宇迹纤细的日记',**extra),
          event('other-chapter','a',31,'庞镇众生相 · 问诊簿',**extra),
          event('other-video','b',1,'庞镇众生相 · 宇迹纤细的日记',**extra)]
    result=library_events(group_events(rows,collection_only=True),videos)
    assert [r['id'] for r in result]==['first','reopen','other-chapter','other-video']


def test_global_order_uses_import_sequence_and_preserves_first_provenance():
    early=video('early','2026-10-29-12-36-40.mkv',import_order=1,created_at='2026-10-30 20:00:00')
    late=video('late','2026-10-29-10-18-21.mkv',import_order=2,created_at='2026-10-29 20:00:00')
    first=event('first','early',110)
    duplicate=event('duplicate','late',1,verified=1,confidence=.99,text='后续确认画面')
    distinct=event('next','late',2,'红房子')
    rows=library_events([duplicate,distinct,first],[late,early])
    assert rows==[first,distinct]
    assert rows[0]['video_id']=='early' and rows[0]['time']==110
    assert rows[0]['screenshot']=='first.jpg' and rows[0]['verified']==0
    assert duplicate['verified']==1 and duplicate['time']==1


def test_global_dedup_keeps_categories_chapters_unknowns_and_manual_markers_separate():
    videos=[video('a','2026-10-29-10-00-00.mkv'),video('b','2026-10-29-11-00-00.mkv')]
    rows=[event('bell-a','a',1),event('bell-b','b',1),
          event('manual-a','a',2,source='manual'),event('manual-b','b',2,source='marker'),
          event('book','a',3,kind='book'),
          event('chapter1','a',4,'卷宗 · 之一',kind='book'),
          event('chapter2','b',4,'卷宗 · 之二',kind='book'),
          event('unknown1','a',5,'首领击败（名称待核实）',kind='boss'),
          event('unknown2','b',5,'首领击败（名称待核实）',kind='boss')]
    result=library_events(rows,videos)
    assert len(result)==8
    assert 'bell-b' not in [row['id'] for row in result]
    assert 'manual-b' in [row['id'] for row in result]


def test_books_keep_same_title_at_different_times_and_in_different_videos():
    videos=[video('a','first.mkv',import_order=1),video('b','second.mkv',import_order=2)]
    rows=[event('first','a',10,'四凶斧案卷 · 斧老大的绝笔信',kind='book'),
          event('later','a',120,'四凶斧案卷 · 斧老大的绝笔信',kind='book'),
          event('next-video','b',2,'四凶斧案卷 · 斧老大的绝笔信',kind='book')]
    grouped=group_events(rows,collection_only=True)
    result=library_events(grouped,videos)
    assert [row['id'] for row in result]==['first','later','next-video']
    assert all(not row['first_occurrence_only'] for row in result)
    assert len(library_events(rows,videos))==3  # stale first-only flags ignored


def test_books_only_merge_identical_title_timestamp_video_and_saved_frame():
    videos=[video('a','first.mkv',import_order=1),video('b','second.mkv',import_order=2)]
    shared={'kind':'book','screenshot':'E:/frames/book.jpg'}
    rows=[event('a-first','a',10,'书卷 · 第一章',**shared),
          event('b-duplicate','a',10,'书卷 · 第一章',**shared),
          event('chapter','a',10,'书卷 · 第二章',**shared),
          event('later','a',11,'书卷 · 第一章',**shared),
          event('next-video','b',10,'书卷 · 第一章',**shared),
          event('other-frame','a',10,'书卷 · 第一章',kind='book',screenshot='E:/frames/other.jpg'),
          event('no-frame-1','a',10,'书卷 · 第一章',kind='book',screenshot=''),
          event('no-frame-2','a',10,'书卷 · 第一章',kind='book',screenshot=''),
          event('manual','a',10,'书卷 · 第一章',source='manual',**shared)]
    expected={row['id'] for row in rows}-{'b-duplicate'}
    assert {row['id'] for row in group_events(rows,collection_only=True)}==expected
    assert {row['id'] for row in library_events(rows,videos)}==expected


def test_legacy_catalog_does_not_reclassify_saved_book_as_same_named_weapon():
    rows=[event('book','a',10,'同名卷宗',kind='book',evidence='inventory_selection'),
          event('weapon','a',20,'同名卷宗',kind='weapon')]
    result=group_events(rows,collection_only=False)
    assert [(row['id'],row['kind']) for row in result]==[('book','book'),('weapon','weapon')]
    assert not result[0]['first_occurrence_only']


@pytest.mark.parametrize(('kind','name'),[('amber','杀气琥珀碎片'),('blackstone','黑石碎片')])
def test_same_named_fragments_keep_each_occurrence_within_and_across_videos(kind,name):
    videos=[video('a','first.mkv',import_order=1),video('b','second.mkv',import_order=2)]
    rows=[event('first','a',10,name,kind=kind,evidence='collection_panel'),
          event('another','a',120,name,kind=kind,evidence='collection_panel'),
          event('next-video','b',2,name,kind=kind,evidence='collection_panel')]
    grouped=group_events(rows,collection_only=True)
    assert len(grouped)==3
    assert all(not row['first_occurrence_only'] for row in grouped)
    result=library_events(grouped,videos)
    assert [row['id'] for row in result]==['first','another','next-video']
    # Preexisting catalogs can still have the old flag; it must not override
    # the explicit fragment exception at the whole-library boundary.
    legacy=library_events(rows,videos)
    assert len(legacy)==3 and all(not row['first_occurrence_only'] for row in legacy)


def test_fragment_exception_does_not_change_memory_flower_or_weapon_first_only():
    videos=[video('a','first.mkv',import_order=1),video('b','second.mkv',import_order=2)]
    rows=[]
    for kind,name in [('memory_flower','记忆之花'),('weapon','羽弓')]:
        rows.extend([event(kind+'-first','a',10,name,kind=kind),
                     event(kind+'-repeat','a',20,name,kind=kind),
                     event(kind+'-next','b',1,name,kind=kind)])
    result=library_events(group_events(rows,collection_only=True),videos)
    assert {row['id'] for row in result}=={'memory_flower-first','weapon-first'}


def test_fragment_rollback_is_still_excluded_and_continuous_prompt_has_one_id():
    from app.analyzer import EventAccumulator
    saved={}
    accumulator=EventAccumulator('fragment-video',1,lambda row:saved.update({row['id']:row}))
    candidate=event('ignored','a',10,'黑石碎片',kind='blackstone',evidence='collection_panel')
    for stamp in (10,11,12): accumulator.add([candidate],stamp,lambda:'same-pickup.jpg')
    accumulator.flush()
    assert len(saved)==1
    prompt=next(iter(saved.values()))
    assert prompt['time']==10 and prompt['end_time']==13
    refund=event('refund','a',20,'黑石碎片',kind='blackstone',evidence='upgrade_material')
    assert [row['id'] for row in group_events([prompt,refund],collection_only=True)]==[prompt['id']]


def test_import_order_ignores_names_mtime_and_clock_changes():
    a=video('a','2026-10-29-23-00-00.mkv',import_order=1,mtime=9999999,created_at='2026-10-30 20:00:00')
    b=video('b','2026-10-29-01-00-00.mkv',import_order=2,mtime=1,created_at='2026-10-29 20:00:00')
    c=video('c','other.mkv',import_order=3,created_at=b['created_at'])
    assert [v['id'] for v in sorted([c,b,a],key=import_order_key)]==['a','b','c']
    assert import_order_key(a)==import_order_key({**a,'name':'renamed','mtime':0})
    assert import_order_key(video('x','unknown',created_at='2026-01-01')) < import_order_key(video('y','unknown',created_at='2026-02-01'))


@pytest.fixture
def catalog_client(tmp_path,monkeypatch):
    assert os.environ.get('FOOTAGE_TEST')=='1'
    configured=Path(os.environ['FOOTAGE_ROOT']).resolve()
    assert configured!=Path(__file__).resolve().parents[1]
    (configured/'static').mkdir(parents=True,exist_ok=True)
    from app import server,storage
    test_root=tmp_path/'global-catalog'
    data=test_root/'data'
    data.mkdir(parents=True)
    monkeypatch.setattr(storage,'DB',data/'library.sqlite3')
    monkeypatch.setattr(server,'DATA',data)
    monkeypatch.setattr(server,'ROOT',test_root)
    storage.init_db()
    server.catalog_cache.clear();server.catalog_counts.clear()
    server.event_views.clear();server.library_cache.clear()
    client=TestClient(server.app)
    yield client,server,storage
    client.close()


def seed_library(storage,server):
    # Both imported within the same second; the later insertion has an earlier
    # recording filename and an earlier local event timestamp.
    videos=[video('early','2026-10-29-12-00-00.mkv',created_at='2026-10-29 01:00:00'),
            video('late','2026-10-29-10-00-00.mkv',created_at='2026-10-29 01:00:00')]
    for v in videos:
        storage.execute('INSERT INTO videos(id,path,fingerprint,name,duration,mtime,created_at,options) VALUES (?,?,?,?,?,?,?,?)',
            (v['id'],v['path'],v['id'],v['name'],v['duration'],None,v['created_at'],
             json.dumps({'recognition_scope':'collection_only','text_mode':'off'})))
    for row in [event('late-duplicate','late',1,verified=1,text='只在后段出现的文字'),
                event('early-first','early',110),event('late-next','late',2,'红房子'),
                event('early-weapon','early',100,'羽弓',kind='weapon')]:
        server.insert_event(row['video_id'],row)


def test_typed_boss_consensus_updates_one_id_and_preserves_manual_correction(catalog_client):
    client,server,storage=catalog_client
    seed_library(storage,server)
    base=event('hud-track','late',10,'山门守卫:林远锋',kind='boss',evidence='boss_name')
    key=server.save_ocr_event('late',base)
    server.save_ocr_event('late',{**base,'name':'山门守卫:林远峰','end_time':20})
    row=storage.query('SELECT * FROM events WHERE id=?',(key,),True)
    assert row['name']=='山门守卫:林远峰' and row['time']==10 and row['end_time']==20
    client.patch('/api/events/'+key,json={'name':'人工校对名','verified':True}).raise_for_status()
    server.save_ocr_event('late',{**base,'name':'后续机器读数','end_time':25})
    assert storage.query('SELECT name FROM events WHERE id=?',(key,),True)['name']=='人工校对名'


def test_continuous_books_counts_video_filter_and_export_agree(catalog_client):
    client,server,storage=catalog_client
    seed_library(storage,server)
    for stamp in range(10,24):
        server.save_ocr_event('early',event('page-'+str(stamp),'early',stamp,'组织 · 四大蛇屋',
            kind='book',end_time=stamp+1,evidence='collection_panel'))
    # Raw evidence survives, while every result surface shares one prompt.
    assert len(storage.query("SELECT id FROM events WHERE kind='book'"))==14
    rows=client.get('/api/events',params={'video_id':'early','kind':'book'}).json()
    assert rows['total']==1 and rows['events'][0]['time']==10 and rows['events'][0]['end_time']==24
    assert client.get('/api/export/events.csv',params={'video_id':'early'}).text.count('组织 · 四大蛇屋')==1


def test_http_filters_pagination_timeline_and_csv_share_global_representatives(catalog_client):
    client,server,storage=catalog_client
    seed_library(storage,server)
    data=client.get('/api/events').json()
    assert data['total']==3
    assert [(e['video_id'],e['time']) for e in data['events']]==[('early',100),('early',110),('late',2)]
    bells=client.get('/api/events',params={'kind':'bell','limit':1,'offset':1}).json()
    assert bells['total']==2 and bells['events'][0]['name']=='红房子'
    assert client.get('/api/events',params={'q':'开端之地','verified':'1'}).json()['total']==0
    assert client.get('/api/events',params={'q':'只在后段'}).json()['total']==0
    assert client.get('/api/events',params={'q':'开端之地','video_id':'late'}).json()['total']==0
    assert client.get('/api/events',params={'video_id':'missing'}).json()['total']==0
    first=client.get('/api/events',params={'q':'开端之地'}).json()['events'][0]
    assert first['video_id']=='early' and first['time']==110 and first['verified']==0
    assert first['screenshot_url']=='/api/events/early_early-first/screenshot'
    export=list(csv.reader(io.StringIO(client.get('/api/export/events.csv').text.lstrip('\ufeff'))))
    assert [row[4] for row in export[1:]]==['羽弓','开端之地','红房子']
    assert export[2][0].endswith('2026-10-29-12-00-00.mkv') and export[2][2]=='110.0'
    video_export=client.get('/api/export/events.csv',params={'video_id':'late'}).text
    assert '红房子' in video_export and '开端之地' not in video_export
    state=client.get('/api/state').json()
    assert state['event_count']==3
    assert [(v['id'],v['event_count'],v['detected_event_count']) for v in state['videos']]==[
        ('early',2,2),('late',1,2)]


def test_video_scope_filters_global_results_without_reintroducing_duplicates(catalog_client):
    client,server,storage=catalog_client
    seed_library(storage,server)
    result=client.get('/api/events',params={'video_id':'late','scope':'video'}).json()
    assert [(e['video_id'],e['name']) for e in result['events']]==[('late','红房子')]
    assert client.get('/api/events',params={'q':'开端之地'}).json()['events'][0]['video_id']=='early'
    scoped=client.get('/api/export/events.csv',params={'video_id':'late','scope':'video'}).text
    assert '开端之地' not in scoped and '红房子' in scoped


def test_scoped_timestamp_export_excludes_historical_equipment_but_keeps_evidence(catalog_client):
    client,server,storage=catalog_client
    seed_library(storage,server)
    for row in [event('old-accessory','early',119,'神奇刀鞘',kind='accessory'),
                event('repeat-shadow-gear','late',3,'羽弓',kind='weapon'),
                event('repeat-accessory','late',4,'神奇刀鞘',kind='accessory'),
                event('new-weapon','late',5,'妙手飞刀',kind='weapon'),
                event('new-accessory','late',6,'细线球',kind='accessory'),
                event('old-fragment','early',118,'黑石碎片',kind='blackstone'),
                event('another-fragment','late',7,'黑石碎片',kind='blackstone')]:
        server.insert_event(row['video_id'],row)
    visible=client.get('/api/events',params={'video_id':'late','scope':'video'}).json()['events']
    assert not {'羽弓','神奇刀鞘'} & {e['name'] for e in visible}
    scoped=client.get('/api/export/events.csv',params={'video_id':'late','scope':'video'})
    rows=list(csv.reader(io.StringIO(scoped.text.lstrip('\ufeff'))))[1:]
    assert [r[4] for r in rows]==['红房子','妙手飞刀','细线球','黑石碎片']
    first=client.get('/api/export/events.csv',params={'video_id':'early','scope':'video'}).text
    assert '羽弓' in first and '神奇刀鞘' in first
    # Reading/exporting must never delete the later raw evidence.
    assert len(client.get('/api/events',params={'video_id':'late','scope':'video'}).json()['events'])==len(visible)
    assert storage.query("SELECT count(*) n FROM events WHERE video_id='late' AND name IN ('羽弓','神奇刀鞘')",one=True)['n']==2


def test_compilation_timestamp_filter_uses_history_before_selected_videos():
    from app.event_catalog import timestamp_events
    videos=[video('old','old.mkv',import_order=1),video('new1','new1.mkv',import_order=2),
            video('new2','new2.mkv',import_order=3)]
    rows=[event('old-item','old',115,'羽弓',kind='weapon'),
          event('repeat','new1',1,'羽弓',kind='weapon'),
          event('first-new','new1',100,'细线球',kind='accessory'),
          event('next-repeat','new2',2,'细线球',kind='accessory'),
          event('book1','new1',105,'同名书页',kind='book'),
          event('book2','new2',4,'同名书页',kind='book'),
          event('flower1','new1',106,'某人的记忆之花 · 其一',kind='memory_flower'),
          event('flower2','new2',5,'某人的记忆之花 · 其二',kind='memory_flower')]
    selected=[e for e in rows if e['video_id']!='old']
    saved=[dict(e) for e in selected]
    result=timestamp_events(selected,library_events(rows,videos))
    assert [e['id'] for e in result]==['first-new','book1','book2','flower1','flower2']
    assert selected==saved


@pytest.mark.parametrize('kind', ['bell','weapon','accessory','medicine','story_item','task_item',
                                 'challenge','task_story','task_rumor','task_collection','boss'])
def test_each_nonrepeatable_kind_uses_library_first_in_video_view_and_export(catalog_client,kind):
    from app.event_catalog import timestamp_events
    client,server,storage=catalog_client
    seed_library(storage,server)
    rows=[event('first-object','early',70,'独立对象',kind=kind),
          event('later-object','late',1,'独立对象',kind=kind)]
    for row in rows: server.save_ocr_event(row['video_id'],row)
    for scope in ('video','library'):
        response=client.get('/api/events',params={'video_id':'late','scope':scope,'kind':kind,'q':'独立对象'})
        assert response.json()['total']==0
        assert '独立对象' not in client.get('/api/export/events.csv',params={'video_id':'late','scope':scope}).text
    _,catalog=server.library_snapshot()
    assert timestamp_events([e for e in server.event_catalog(storage.query("SELECT * FROM videos WHERE id='late'",one=True))
                             if e['name']=='独立对象'],catalog)==[]


def test_new_recognition_aliases_and_completion_order_refresh_all_views(catalog_client):
    client,server,storage=catalog_client
    seed_library(storage,server)
    # The later import finishes analysis first. A subsequent earlier import
    # must replace its representative, without losing the original evidence.
    late=server.save_ocr_event('late',event('alias-late','late',1,'巨区斧',kind='weapon',text='巨区斧'))
    assert client.get('/api/events',params={'q':'巨凶斧'}).json()['events'][0]['id']==late
    early=server.save_ocr_event('early',event('alias-early','early',80,'巨凶斧',kind='weapon'))
    current=client.get('/api/events',params={'q':'巨凶斧'}).json()
    assert current['total']==1 and current['events'][0]['id']==early
    assert current['events'][0]['time']==80
    assert client.get('/api/events',params={'video_id':'late','scope':'video','q':'巨凶斧'}).json()['total']==0
    server.save_ocr_event('late',event('resumed-alias','late',60,'巨区爷',kind='weapon'))
    assert client.get('/api/events',params={'q':'巨凶斧'}).json()['total']==1
    assert storage.query('SELECT text FROM events WHERE id=?',(late,),True)['text']=='巨区斧'
    # Removing the first evidence promotes the next position automatically.
    client.delete('/api/events/'+early).raise_for_status()
    assert client.get('/api/events',params={'q':'巨凶斧'}).json()['events'][0]['id']==late
    scoped=client.get('/api/events',params={'video_id':'late','scope':'video'}).json()
    state=client.get('/api/state').json()
    assert next(v for v in state['videos'] if v['id']=='late')['event_count']==scoped['total']


def test_verified_aliases_merge_without_merging_boss_forms_or_similar_items():
    videos=[video('a','first.mkv',import_order=1),video('b','second.mkv',import_order=2)]
    rows=[event('first','a',70,'白赤练:献蔡之蛇',kind='boss'),
          event('repeat','b',1,'白赤练:献祭之蛇',kind='boss'),
          event('other-form','a',50,'双蛇夫人:白赤练',kind='boss'),
          event('weapon','a',30,'黑伤双刀',kind='weapon'),
          event('same-weapon','b',5,'黑伤双刃',kind='weapon'),
          event('different-weapon','b',6,'木制黑伤',kind='weapon')]
    original=[dict(e) for e in rows]
    result=library_events(group_events(rows,collection_only=True),videos)
    assert [(e['id'],e['name']) for e in result]==[
        ('weapon','黑伤双刃'),('other-form','双蛇夫人:白赤练'),
        ('first','白赤练:献祭之蛇'),('different-weapon','木制黑伤')]
    assert rows==original


def test_manual_same_named_markers_are_preserved_within_video():
    rows=[event('auto','a',1,'开端之地'),event('manual1','a',2,'开端之地',source='manual'),
          event('manual2','a',3,'开端之地',source='manual')]
    assert len(library_events(group_events(rows,collection_only=True),[video('a','a.mkv')]))==3


def test_six_video_audit_merges_verified_typos_without_merging_distinct_objects():
    videos=[video('a','first.mkv',import_order=1),video('b','second.mkv',import_order=2)]
    rows=[event('first-bell','a',100,'夕阳小径'),
          event('clipped-bell','b',1,'阳小径'),event('clipped-again','b',2,'阳径'),
          event('first-boss','b',10,'巨锤客:万钩',kind='boss'),
          event('correct-boss','b',12,'巨锤客:万钧',kind='boss'),
          event('slot','b',20,'装备饰品',kind='accessory'),
          event('fan','b',22,'舞扇',kind='accessory')]
    # These similar titles refer to genuinely distinct items/people, verified
    # in the audit; exact OCR aliases must never become generic fuzzy merging.
    distinct=[('story_item','龙形令牌·阴'),('story_item','龙形令牌·阳'),
              ('story_item','龙形令牌'),('boss','鬼差高手:斩骑'),
              ('boss','鬼差高手:勾魂'),('boss','灭法之法:炎茹'),
              ('boss','灭法之法:雷婷'),('boss','少主护卫:玉妖'),
              ('boss','少主护卫:铁鬼')]
    rows.extend(event('distinct-'+str(i),'b',30+i,name,kind=kind)
                for i,(kind,name) in enumerate(distinct))
    original=[dict(row) for row in rows]
    result=library_events(group_events(rows,collection_only=True),videos)
    assert [(e['id'],e['name']) for e in result[:3]]==[
        ('first-bell','夕阳小径'),('first-boss','巨锤客:万钧'),('fan','舞扇')]
    assert [(e['kind'],e['name']) for e in result[3:]]==distinct
    assert result[0]['video_id']=='a' and result[0]['time']==100
    assert result[1]['time']==10 and result[1]['screenshot']=='first-boss.jpg'
    assert rows==original


def test_both_memory_flower_filters_show_both_kinds():
    from app.event_catalog import filter_groups
    rows=[event('one','early',1,'第一朵',kind='memory_flower'),event('two','late',2,'第二朵',kind='reading_memory')]
    for kind in ('memory_flower','reading_memory'):
        assert [x['name'] for x in filter_groups(rows,kind=kind)]==['第一朵','第二朵']


def test_global_cache_invalidates_for_import_order_first_deletion_and_empty_library(catalog_client):
    client,server,storage=catalog_client
    assert client.get('/api/events').json()=={'events':[],'total':0}
    seed_library(storage,server)
    assert client.get('/api/events',params={'q':'开端之地'}).json()['events'][0]['video_id']=='early'
    storage.execute('UPDATE videos SET name=? WHERE id=?',('2026-10-29-09-00-00.mkv','late'))
    assert client.get('/api/events',params={'q':'开端之地'}).json()['events'][0]['video_id']=='early'
    storage.execute('UPDATE videos SET created_at=? WHERE id=?',('2026-01-01 00:00:00','late'))
    assert client.get('/api/events',params={'q':'开端之地'}).json()['events'][0]['video_id']=='early'
    storage.execute('UPDATE videos SET rowid=100 WHERE id=?',('early',))
    moved=client.get('/api/events',params={'q':'开端之地'}).json()['events'][0]
    assert moved['video_id']=='late' and moved['video_name']=='2026-10-29-09-00-00.mkv'
    client.delete('/api/events/'+moved['id']).raise_for_status()
    assert client.get('/api/events',params={'q':'开端之地'}).json()['events'][0]['video_id']=='early'
    storage.execute('DELETE FROM videos')
    assert client.get('/api/events').json()=={'events':[],'total':0}
    assert client.get('/api/state').json()['event_count']==0


def test_book_persistence_only_folds_exact_timestamp_and_nonempty_same_frame(catalog_client):
    _,server,storage=catalog_client
    seed_library(storage,server)
    base=event('book-first','early',10,'同名卷宗',kind='book',end_time=12,screenshot='frame-a.jpg')
    first=server.save_ocr_event('early',base)
    same=server.save_ocr_event('early',{**base,'id':'same-frame-new-id'})
    other_frame=server.save_ocr_event('early',{**base,'id':'another-frame','screenshot':'frame-b.jpg'})
    other_time=server.save_ocr_event('early',{**base,'id':'another-time','time':11})
    missing1=server.save_ocr_event('early',{**base,'id':'missing-frame-1','screenshot':''})
    missing2=server.save_ocr_event('early',{**base,'id':'missing-frame-2','screenshot':''})
    assert same==first
    assert len({first,other_frame,other_time,missing1,missing2})==5
    assert storage.query("SELECT count(*) n FROM events WHERE kind='book'",one=True)['n']==5


def test_book_review_and_rejection_do_not_block_other_times_or_frames(catalog_client):
    client,server,storage=catalog_client
    seed_library(storage,server)
    base=event('reviewed-book','early',10,'同名卷宗',kind='book',end_time=12,screenshot='frame-a.jpg')
    reviewed=server.save_ocr_event('early',base)
    storage.execute('UPDATE events SET verified=1 WHERE id=?',(reviewed,))
    assert server.save_ocr_event('early',{**base,'id':'same-reviewed-frame'})==reviewed
    other=server.save_ocr_event('early',{**base,'id':'other-reviewed-frame','screenshot':'frame-b.jpg'})
    assert other!=reviewed
    later=server.save_ocr_event('early',{**base,'id':'other-reviewed-time','time':11})
    assert later not in {reviewed,other}
    client.delete('/api/events/'+reviewed).raise_for_status()
    assert server.save_ocr_event('early',base) is None
    # Rejected storage has no screenshot evidence. A different ID must not be
    # rejected merely because its title/time interval matches the deleted row.
    restored=server.save_ocr_event('early',{**base,'id':'different-after-rejection'})
    assert restored is not None and restored not in {other,later}


def test_story_items_roundtrip_through_storage_search_edit_and_csv(catalog_client):
    client,server,storage=catalog_client
    seed_library(storage,server)
    name='杀气炉探火孔开关'
    first=server.save_ocr_event('early',event('story-first','early',90,name,kind='story_item',evidence='collection_panel'))
    server.save_ocr_event('early',event('story-repeat','early',110,name,kind='story_item',evidence='collection_panel'))
    server.save_ocr_event('late',event('story-next-video','late',1,name,kind='story_item',evidence='collection_panel'))
    unique=server.save_ocr_event('late',event('story-unique','late',2,'旧塔钥匙',kind='story_item',evidence='collection_panel'))
    assert storage.query('SELECT kind FROM events WHERE id=?',(first,),True)['kind']=='story_item'
    found=client.get('/api/events',params={'kind':'story_item','q':'探火孔'}).json()
    assert found['total']==1
    assert (found['events'][0]['id'],found['events'][0]['video_id'],found['events'][0]['time'])==(first,'early',90)
    assert client.get('/api/events',params={'kind':'story_item','q':'探火孔','video_id':'late'}).json()['total']==0
    editable=server.insert_event('early',event('story-editable','early',80,'待分类道具',kind='note'))
    client.patch('/api/events/'+editable,json={'kind':'story_item','name':'旧宅钥匙','verified':True}).raise_for_status()
    edited=client.get('/api/events',params={'kind':'story_item','q':'旧宅钥匙'}).json()['events'][0]
    assert edited['id']==editable and edited['kind']=='story_item' and edited['verified']==1
    items=client.get('/api/events',params={'kind':'story_item'}).json()
    assert items['total']==3
    assert [row['id'] for row in items['events']]==[editable,first,unique]
    csv_rows=list(csv.reader(io.StringIO(client.get('/api/export/events.csv').text.lstrip('\ufeff'))))
    story_rows=[row for row in csv_rows[1:] if row[3]=='剧情道具']
    assert [row[4] for row in story_rows]==['旧宅钥匙',name,'旧塔钥匙']
    assert [float(row[2]) for row in story_rows]==[80,90,2]
