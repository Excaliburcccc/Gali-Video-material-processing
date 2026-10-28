from app.analyzer import EventAccumulator


def test_compact_text_tracks_bridge_short_gaps_but_preserve_later_visits():
    updates=[]
    a=EventAccumulator('fixture',1,updates.append)
    base={'kind':'text','name':'测试地名错字','text':'测试地名错字','confidence':.7,
          '_identity_key':'stable-local-name','_merge_gap':20,'_replace_name':True}
    a.add([base],0,lambda:'first.jpg')
    a.add([],3,lambda:'unused.jpg')
    a.add([{**base,'name':'测试地名正字','text':'测试地名正字','confidence':.95}],8,lambda:'later.jpg')
    a.add([{**base,'name':'测试地名正字','confidence':.95}],31,lambda:'new-visit.jpg')
    a.flush()
    final={e['id']:e for e in updates}
    assert len(final)==2
    early=min(final.values(),key=lambda e:e['time'])
    assert early['time']==0 and early['end_time']==9 and early['name']=='测试地名正字'
    assert early['screenshot']=='first.jpg'
    assert not any(k.startswith('_') for e in final.values() for k in e)


def test_compact_text_gap_never_merges_separate_game_events():
    for kind in ('item','enemy','waypoint','quest'):
        updates=[]
        a=EventAccumulator('fixture',1,updates.append)
        candidate={'kind':kind,'name':'相同名称','confidence':.9,'_merge_gap':20}
        a.add([candidate],1,lambda:'a.jpg')
        a.add([candidate],5,lambda:'b.jpg')
        a.flush()
        assert len({e['id'] for e in updates})==2
