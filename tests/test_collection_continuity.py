"""Regression coverage for repeated HUD and continuous collection popups."""
from app.analyzer import EventAccumulator
from app.boss_text_index import BossTextIndex
from app.collection_taxonomy import FirstCollectionFilter
from app.event_catalog import group_events, library_events
from app.rules import OCRLine


def row(key,time,name,kind='book',**extra):
    return dict(id=key,video_id='a',time=time,end_time=time+1,name=name,kind=kind,
                text=name,source='ocr',evidence='collection_panel',confidence=.8,
                screenshot=key+'.jpg',verified=0,edited=0,**extra)


def hud(name):
    return OCRLine(name,.9,[[400,90],[600,90],[600,110],[400,110]])


def test_fourteen_frames_are_one_book_but_reopening_chapter_and_video_survive():
    rows=[row(str(t),t,'组织 · 四大蛇屋') for t in range(10,24)]
    rows += [row('new-chapter',24,'组织 · 秘密酒馆'),row('reopen',25,'组织 · 四大蛇屋'),
             row('later',30,'组织 · 四大蛇屋')]
    other={**row('next-video',1,'组织 · 四大蛇屋'),'video_id':'b'}
    grouped=group_events(rows+[other],collection_only=True)
    result=library_events(grouped,[{'id':'a','import_order':1},{'id':'b','import_order':2}])
    assert [r['id'] for r in result]==['10','new-chapter','reopen','later','next-video']
    assert result[0]['time']==10 and result[0]['end_time']==24
    assert rows[0]['end_time']==11  # Read-time cleanup does not mutate evidence.


def test_bare_book_title_and_reviewed_observations_are_not_suppressed():
    rows=[row('bare1',10,'同名书卷'),row('bare2',11,'同名书卷'),
          row('chapter',20,'书卷 · 第一章'),
          {**row('reviewed',21,'书卷 · 第一章'),'verified':1}]
    assert len(group_events(rows,collection_only=True))==4


def test_fragments_merge_only_overlapping_prompt_not_separate_pickups():
    rows=[{**row('first',10,'杀气琥珀碎片',kind='amber'),'end_time':13},
          row('same-popup',12,'杀气琥珀碎片',kind='amber'),
          row('another',14,'杀气琥珀碎片',kind='amber')]
    result=group_events(rows,collection_only=True)
    assert [r['id'] for r in result]==['first','another']


def test_accumulator_books_continue_through_visual_frames_but_stop_on_observed_absence():
    saved={}
    a=EventAccumulator('video',1,lambda r:saved.update({r['id']:r}))
    candidate=row('ignored',10,'组织 · 四大蛇屋')
    a.add([candidate],10,lambda:'10.jpg')
    a.add([],10.5,lambda:'10.5.jpg',observed=False)
    a.add([candidate],11,lambda:'11.jpg')
    a.add([],12,lambda:'12.jpg')
    a.add([candidate],13,lambda:'13.jpg')
    a.flush()
    assert [(r['time'],r['end_time']) for r in saved.values()]==[(10,12),(13,14)]


def test_focused_pipeline_stabilizes_boss_spellings_and_keeps_second_form():
    tracker=BossTextIndex();first=FirstCollectionFilter();saved={}
    a=EventAccumulator('boss',1,lambda r:saved.update({r['id']:r}))
    names=['鬼差队长:火流星','鬼差拟长:火流星','差队长:火流星',
           '鬼差队长:火流望','鬼差队长:火流',
           '火流星:磷火意识','火流星:磷火意识','火流星:磷火意证']
    for t,name in enumerate(names):
        raw=[{**row('candidate',t,name,'boss'),'evidence':'boss_name'}]
        tracked=tracker.filter_collections(raw,[hud(name)],(1000,1000),t)
        a.add(first.filter(tracked),t,lambda:f'{t}.jpg')
    a.flush()
    assert [r['name'] for r in saved.values()]==['鬼差队长:火流星','火流星:磷火意识']


def test_unseen_boss_typo_uses_one_encounter_id_then_improves_name():
    tracker=BossTextIndex();first=FirstCollectionFilter();saved={}
    a=EventAccumulator('new-boss',1,lambda r:saved.update({r['id']:r}))
    for t,name in enumerate(['山门守卫:林远锋','山门守卫:林远锋',
                             '山门守卫:林远峰','山门守卫:林远峰','山门守卫:林远峰']):
        tracked=tracker.filter_collections([], [hud(name)],(1000,1000),t)
        a.add(first.filter(tracked),t,lambda:f'{t}.jpg')
    a.flush()
    assert len(saved)==1
    assert next(iter(saved.values()))['name']=='山门守卫:林远峰'


def test_fading_book_heading_inherits_only_its_exact_contiguous_chapter():
    rows=[row('full',10,'组织 · 灭龙魔'),row('fading',11,'灭龙魔'),row('later',13,'灭龙魔')]
    result=group_events(rows,collection_only=True)
    assert [r['id'] for r in result]==['full','later']
    saved={};a=EventAccumulator('book',1,lambda r:saved.update({r['id']:r}))
    for item in rows: a.add([item],item['time'],lambda:'frame.jpg')
    a.flush()
    assert len(saved)==2


def test_complete_amber_and_fragment_are_different_items_even_in_adjacent_panels():
    rows=[row('fragment',10,'杀气琥珀碎片',kind='amber'),row('complete',11,'杀气琥珀',kind='amber')]
    assert len(group_events(rows,collection_only=True))==2


def test_same_person_with_different_title_is_a_distinct_boss_form():
    tracker=BossTextIndex();names=[]
    for t,name in enumerate(['山门守卫:林峰','山门守卫:林峰','雷霆化身:林峰','雷霆化身:林峰']):
        names.extend(r['name'] for r in tracker.filter_collections([], [hud(name)],(1000,1000),t))
    assert names==['山门守卫:林峰','雷霆化身:林峰']


def test_analyzer_collection_mode_uses_consensus_even_with_text_index_off(tmp_path,monkeypatch):
    import numpy as np
    from app import analyzer,collection_refinement,victory_detector
    source=tmp_path/'fixture.mkv';source.touch()
    names=['鬼差队长:火流星','鬼差拟长:火流星','差队长:火流星',
           '火流星:磷火意识','火流星:磷火意识','火流星:磷火意证']
    monkeypatch.setattr(analyzer,'probe_video',lambda *a:{'duration':6,'width':1000,'height':1000})
    monkeypatch.setattr(analyzer,'_engine',lambda:object())
    monkeypatch.setattr(analyzer,'_sampled_frames',lambda *a:iter(
        (t,np.full((1000,1000,3),int(t),dtype=np.uint8)) for t in np.arange(0,6,.5)))
    monkeypatch.setattr(analyzer,'_ocr_lines',lambda frame,*a:[hud(names[int(frame[0,0,0])])])
    monkeypatch.setattr(collection_refinement,'refine_collection_lines',lambda lines,*a:lines)
    monkeypatch.setattr(victory_detector.VictoryDetector,'detect',lambda *a:None)
    saved={}
    result=analyzer.analyze(str(source),tmp_path/'frames',
        {'game_profile':'phantom_blade_zero_demo','recognition_scope':'collection_only','text_mode':'off'},
        lambda r:saved.update({r['id']:r}),lambda p:None,lambda:False)
    assert result['status']=='completed'
    assert [r['name'] for r in saved.values()]==['鬼差队长:火流星','火流星:磷火意识']
