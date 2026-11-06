from app.event_catalog import group_events, filter_groups


def test_collection_catalog_rejects_prose_and_keeps_first_typed_object():
    rows=[event('a',1,'accessory','影动猎杀仪',evidence='collection_panel',text='攻击类饰品'),
          event('b',30,'accessory','影动猎杀仪',evidence='collection_panel',text='攻击类饰品'),
          event('noise',2,'text','你们究竟是什么人'),
          event('book',3,'book','新娘阿花',evidence='reading_selection')]
    out=group_events(rows,collection_only=True)
    assert [(e['id'],e['kind']) for e in out]==[('a','accessory'),('book','book')]
    assert out[0]['first_occurrence_only'] and not out[1]['first_occurrence_only']
    assert all(e['occurrence_count']==1 for e in out)


def test_typed_accessory_inventory_is_not_coerced_to_weapon():
    out=group_events([event('a',1,'accessory','药葫芦',evidence='inventory_selection')],collection_only=True)
    assert len(out)==1 and out[0]['kind']=='accessory'


def event(key,time,kind='text',name='地点甲',**kwargs):
    return dict(id=key,video_id='video',kind=kind,name=name,time=time,end_time=time+2,
                source='ocr',verified=0,**kwargs)


def test_memory_petals_merge_ocr_variants_without_losing_other_chapters():
    rows = [event('one',10,'memory_flower','某人的记忆之花 · 其三:崭新的力量'),
            event('typo',11,'memory_flower','某人的记忆之花 · 其三:斩新的力量'),
            event('missing',12,'memory_flower','某人的记忆之花'),
            event('two',30,'memory_flower','某人的记忆之花 · 其二:归来'),
            event('unknown-later',90,'memory_flower','某人的记忆之花')]
    out = group_events(rows, collection_only=True)
    assert [row['id'] for row in out] == ['one','two','unknown-later']
    assert rows[2]['name'] == '某人的记忆之花'
    rows[2]['verified'] = 1
    assert 'missing' in {row['id'] for row in group_events(rows,collection_only=True)}


def test_memory_menu_evidence_is_excluded():
    assert group_events([event('menu',1,'reading_memory','某人的记忆之花',evidence='memory_menu')],
                        collection_only=True) == []


def test_memory_quote_artifact_and_transient_missing_chapter_merge():
    rows=[event('first',10,'memory_flower','有关龙体之术白的记忆之花 · 其二:同流合污还是贪生怕死?'),
          event('clear',10.5,'memory_flower','有关龙体之术的记忆之花 · 其二:同流合污还是贪生怕死?'),
          event('partial',11,'memory_flower','有关龙体之术自的记忆之花'),
          event('later',90,'memory_flower','有关龙体之术的记忆之花 · 其四:抗争之法')]
    out=group_events(rows,collection_only=True)
    assert [row['id'] for row in out] == ['first','later']
    assert out[0]['name'] == '有关龙体之术的记忆之花 · 其二:同流合污还是贪生怕死?'


def test_memory_numeral_misread_cannot_erase_a_different_chapter():
    rows=[event('one',10,'memory_flower','陆家村村民的记忆之花 · 其一:爱人重逢'),
          event('misread',10.5,'memory_flower','陆家村村民的记忆之花 · 其二:爱人重逢'),
          event('two',60,'memory_flower','陆家村村民的记忆之花 · 其二:是我儿吗?')]
    out=group_events(rows,collection_only=True)
    assert [row['id'] for row in out] == ['one','two']


def test_memory_confirmed_missing_rare_character_is_one_chapter():
    rows=[event('first',10,'memory_flower','白赤练的记忆之花 · 其二:圣母言'),
          event('typo',12,'memory_flower','白赤练的记忆之花 · 其二:圣母識言')]
    out=group_events(rows,collection_only=True)
    assert len(out) == 1
    assert out[0]['name'] == '白赤练的记忆之花 · 其二:圣母谶言'


def test_waypoint_first_only_preserves_first_segment_and_raw_edits():
    a,b=event('a',10,'waypoint',edited=1),event('b',90,'waypoint',edited=1)
    out=group_events([b,a])
    assert len(out)==1 and out[0]['occurrence_count']==1 and out[0]['occurrences']==[]
    assert out[0]['first_occurrence_only'] and out[0]['time']==10 and out[0]['end_time']==12
    assert b['end_time']==92


def test_cross_category_weapon_boss_and_map_keep_actual_first_evidence():
    rows=[event('weapon',10,'item','古剑',evidence='item_panel',text='武器展示面板\n单手武器'),
          event('inventory',40,name='古剑',evidence='inventory_selection'),
          event('boss',20,name='剑客:张三',evidence='boss_name'),
          event('victory',60,'enemy','剑客:张三',evidence='victory_template'),
          event('map',30,name='山下渡口',evidence='map_selection'),
          event('menu',70,'waypoint','山下渡口')]
    for compact in (True,False):
        out=group_events(rows,compact)
        assert [(e['id'],e['kind']) for e in out]==[('weapon','weapon'),('boss','boss'),('map','waypoint')]
        assert all(e['occurrence_count']==1 and not e['occurrences'] for e in out)


def test_collectibles_first_only_quests_notes_ordinary_enemies_remain_separate():
    rows=[event('item1',1,'item','玉佩'),event('item2',10,'item','玉佩')]
    for kind in ('quest','note','enemy','achievement'):
        rows.extend(event(f'{kind}-{i}',i*20,kind,kind) for i in (1,2))
    assert len(group_events(rows))==9


def test_first_only_does_not_cross_video_or_merge_floors_and_chapters():
    rows=[event('a',0,'waypoint','杀气塔一层'),event('b',5,'waypoint','杀气塔二层'),
          event('book1',10,'item','卷宗·之一'),event('book2',12,'item','卷宗·之二')]
    other=event('other',20,'waypoint','杀气塔一层');other['video_id']='other'
    rows.append(other)
    assert len(group_events(rows))==5


def test_edited_multiline_panel_does_not_alias_other_floors_when_name_is_present():
    first=event('floor1',1,'waypoint','杀气塔一层',text='杀气塔一层')
    second=event('floor2',10,'waypoint','杀气塔二层',edited=1,
                 text='大地图\n杀气塔一层\n杀气塔二层\n穿行')
    assert [(e['id'],e['name']) for e in group_events([first,second])]==[
        ('floor1','杀气塔一层'),('floor2','杀气塔二层')]


def test_edited_multiline_panel_needs_one_unambiguous_spelling_alias():
    rows=[event('floor1',1,'waypoint','杀气塔一层',text='杀气塔一层'),
          event('floor2',5,'waypoint','杀气塔二层',text='杀气塔二层'),
          event('floor3',10,'waypoint','杀气塔三层',edited=1,
                text='大地图\n杀气塔一层\n杀气塔二层\n穿行')]
    assert [(e['id'],e['name']) for e in group_events(rows)]==[
        ('floor1','杀气塔一层'),('floor2','杀气塔二层'),('floor3','杀气塔三层')]


def test_edited_single_line_spelling_alias_still_joins_first_appearance():
    first=event('first',5,'waypoint','远跳庞镇之桥',text='远跳庞镇之桥')
    corrected=event('fixed',6,'waypoint','远眺庞镇之桥',edited=1,text='远跳庞镇之桥')
    assert [(e['id'],e['name']) for e in group_events([first,corrected])]==[
        ('first','远眺庞镇之桥')]


def test_verified_filter_and_search_cannot_resurrect_later_occurrences():
    a=event('a',0,'waypoint','残偶谷底',text='首次原文')
    b=event('b',10,'waypoint','残偶谷底',text='仅在后面出现的关键词');b['verified']=1
    groups=group_events([a,b])
    assert not filter_groups(groups,verified='1')
    assert not filter_groups(groups,q='仅在后面')
    assert len(filter_groups(groups,verified='0'))==1


def test_manual_spelling_correction_is_applied_to_first_evidence_without_review_inheritance():
    first=event('first',5,name='远跳庞镇之桥',text='远跳庞镇之桥')
    corrected=event('fixed',6,'waypoint','远眺庞镇之桥',edited=1,text='残偶菜单交互\n远跳庞镇之桥\n穿行')
    corrected['verified']=1
    out=group_events([first,corrected])
    assert len(out)==1 and out[0]['name']=='远眺庞镇之桥'
    assert out[0]['id']=='first' and out[0]['time']==5 and not out[0]['verified']


def test_used_quantity_joins_known_collectible():
    first=event('first',5,'item','杀气炉探火孔开关',text='剧情道具\n杀气炉探火孔开关')
    later=event('later',30,name='杀气炉探火孔开关 -1',text='杀气炉探火孔开关 -1')
    assert [(e['id'],e['kind']) for e in group_events([first,later])]==[('first','item')]


def test_primary_scene_suppresses_noise_but_keeps_reviewed_manual_notes():
    main=event('main',10,name='友善的守门人',evidence='dialogue_speaker');main['end_time']=30
    noise=event('noise',12,name='不完整对白')
    manual=event('manual',15,'note','我的备注',edited=1)
    assert [e['id'] for e in group_events([main,noise,manual])]==['main','manual']


def test_upgrade_materials_excluded_in_every_text_mode_but_world_pickup_survives():
    refund=event('refund',10,name='溶剂+8',evidence='upgrade_material')
    pickup=event('pickup',30,'item','溶剂')
    for compact in (True,False):
        assert [e['id'] for e in group_events([refund,pickup],compact)]==['pickup']


def test_inventory_selection_survives_menu_transition_secondary_text_does_not():
    menu=event('menu',10,'waypoint','桥')
    weapon=event('weapon',11,name='当前武器',evidence='inventory_selection')
    secondary=event('noise',11,name='左侧栏目',evidence='scene_secondary')
    out=group_events([menu,weapon,secondary])
    assert [(e['id'],e['kind']) for e in out]==[('menu','waypoint'),('weapon','weapon')]


def test_same_named_ordinary_enemy_is_not_reclassified_as_item():
    out=group_events([event('enemy',1,'enemy','灰石'),event('item',2,'item','灰石')])
    assert [(e['id'],e['kind']) for e in out]==[('enemy','enemy'),('item','item')]


def test_anonymous_program_placeholders_remain_independent_occurrences():
    cases=[('item','物品获得提示（名称未识别）',''),
           ('enemy','战斗胜利提示（名称未识别）',''),
           ('achievement','成就解锁提示（名称未识别）',''),
           ('waypoint','传送点激活提示（名称未识别）',''),
           ('enemy','首领击败（名称待核实）','victory_template')]
    for kind,name,evidence in cases:
        rows=[event('first',10,kind,name,evidence=evidence),
              event('later',100,kind,name,evidence=evidence)]
        for compact in (True,False):
            out=group_events(rows,compact)
            assert [e['id'] for e in out]==['first','later']
            assert all(not e['first_occurrence_only'] for e in out)
            assert all(e['occurrence_count']==1 and not e['occurrences'] for e in out)


def test_corrected_anonymous_record_joins_only_its_identified_object():
    rows=[event('unknown',10,'item','物品获得提示（名称未识别）'),
          event('fixed',20,'item','古代玉佩',edited=1,text='物品获得提示（名称未识别）'),
          event('repeat',40,'item','古代玉佩')]
    out=group_events(rows)
    assert [(e['id'],e['name']) for e in out]==[
        ('unknown','物品获得提示(名称未识别)'),('fixed','古代玉佩')]
    assert not out[0]['first_occurrence_only'] and out[1]['first_occurrence_only']
