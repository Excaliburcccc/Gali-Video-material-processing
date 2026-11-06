import unittest

from app.rules import OCRLine
from app.analyzer import _validate_options
from app.collection_taxonomy import (classify_existing_event, detect_collection_candidates,
                                     FirstCollectionFilter)


def line(text, x, y, w=.15, h=.03):
    return OCRLine(text, .95, [[x * 1600, y * 900], [(x+w) * 1600, y * 900],
                             [(x+w) * 1600, (y+h) * 900], [x * 1600, (y+h) * 900]])


def detect(lines):
    return [(x['kind'], x['name']) for x in detect_collection_candidates(lines, (900, 1600), 10)]


class CollectionTaxonomyTests(unittest.TestCase):
    def test_xiaozhang_treasures_are_task_items_with_separate_item_names(self):
        # Positions and title from the supplied 古代弩机 screenshot, not the
        # descriptive sentence mentioning 小张 lower down the panel.
        rows=[line('妙手小张宝藏',.664,.183,.061,.032),
              line('古代弩机',.664,.219,.086,.050),
              line('似为稀世珍宝，可在妙手小张处换得实用之物。',.664,.491,.26,.032)]
        events=detect_collection_candidates(rows,(900,1600),843)
        self.assertEqual([(e['kind'],e['name']) for e in events],[('task_item','古代弩机')])
        self.assertEqual(events[0]['time'],843)
        first=FirstCollectionFilter()
        self.assertEqual(len(first.filter(events)),1)
        self.assertEqual(first.filter(events),[])
        self.assertEqual(len(first.filter([{**events[0],'name':'染血印章','text':'妙手小张宝藏\n染血印章'}])),1)
        self.assertEqual(detect(rows[1:]),[])

    def test_task_item_heading_does_not_reclassify_related_books_accessories_or_tasks(self):
        for category, title, kind in [('任务物品','古代弩机','task_item'),
                                     ('炒手小张宝藏','古代弩机','task_item'),
                                     ('任務物品','染血印章','task_item'),
                                     ('书籍','妙手小张珍宝清单','book'),
                                     ('攻击类饰品','妙手在握','accessory')]:
            self.assertEqual(detect([line(category,.66,.16),line(title,.66,.20)]),[(kind,title)])
        old={'kind':'item','name':'古代驾机','text':'妙手小张宝藏\n古代驾机','evidence':'item_panel'}
        mapped=classify_existing_event(old)
        self.assertEqual((mapped['kind'],mapped['name']),('task_item','古代弩机'))
        edited=classify_existing_event({**mapped,'name':'用户修正名称','edited':1})
        self.assertEqual(edited['name'],'用户修正名称')
        self.assertIsNone(classify_existing_event({'kind':'text','name':'妙手小张宝藏',
                                                 'text':'妙手小张宝藏'}))

    def test_audited_aliases_apply_to_future_recognition_first_filter(self):
        from app.collection_aliases import ALIASES
        from app.collection_taxonomy import collection_identity
        for kind, aliases in ALIASES.items():
            for alias, canonical in aliases.items():
                with self.subTest(kind=kind,alias=alias):
                    candidates=[{'kind':kind,'name':alias,'time':1},
                                {'kind':kind,'name':canonical,'time':20}]
                    rows=FirstCollectionFilter().filter(candidates)
                    self.assertEqual(len(rows),2 if kind in {'book','amber','blackstone'} else 1)
                    self.assertEqual(rows[0]['name'],canonical)
                    self.assertEqual(collection_identity(kind,alias),collection_identity(kind,canonical))

    def test_empty_accessory_slot_is_not_a_collectible(self):
        self.assertEqual(detect([line('饰品',.67,.16),line('装备饰品',.67,.20)]),[])
        self.assertIsNone(classify_existing_event({'kind':'accessory','name':'装备饰品',
            'text':'饰品\n装备饰品','source':'ocr','evidence':'collection_panel'}))
        self.assertEqual(detect([line('攻击类饰品',.67,.16),line('舞扇',.67,.20)]),
                         [('accessory','舞扇')])

    def test_named_memory_flower_panel_and_ocr_variants(self):
        for label in ('记忆之花', '記憶之花', '記忆之花'):
            self.assertEqual(detect([line(label,.67,.16),line('虚空留下的记忆之花',.67,.20)]),
                             [('reading_memory','虚空留下的记忆之花')])
        self.assertEqual(detect([line('记忆之花',.67,.16),line('确认',.67,.20)]),[])
        fallback=[line('虚空留下的记忆之花',.67,.20),line('(1/1)已获得完整记忆，生命值+5%',.67,.28)]
        self.assertEqual(detect(fallback),[('reading_memory','虚空留下的记忆之花')])
        self.assertEqual(detect(fallback[:1]),[])
        self.assertEqual(detect([line('地图',.26,.03),line('角色',.44,.03),line('读物',.61,.03)]+fallback),[])

    def test_memory_flower_petals_keep_distinct_chapters(self):
        title=line('龙体反抗者的记忆之花',.67,.22)
        chapter=line('其二：分道扬镳',.67,.278)
        counter=line('(1/2)集齐全部记忆残余可使生命值+5%',.67,.42)
        expected=[('memory_flower','龙体反抗者的记忆之花 · 其二:分道扬镳')]
        self.assertEqual(detect([line('记忆之花花瓣',.67,.18),title,chapter,counter]),expected)
        self.assertEqual(detect([title,chapter,counter]),expected)
        missed=line('其二：分道扬',.67,.278)
        self.assertEqual(detect([line('记忆之花花瓣',.67,.18),title,missed,counter]),expected)

    def test_flower_split_title_and_misread_category_do_not_create_extra_objects(self):
        for label in ('记忆之花花瓣', '记忆之花花辨'):
            rows = [line(label,.665,.19,w=.06,h=.018),
                    line('龙体”',.682,.222,w=.052,h=.039),
                    line('反抗者的记忆之花',.745,.225,w=.16,h=.035),
                    line('其二：分道扬',.667,.281,w=.09,h=.024),
                    line('(1/2)集齐全部记忆残余可使生命值+5%',.667,.424)]
            self.assertEqual(detect(rows),[('memory_flower','龙体反抗者的记忆之花 · 其二:分道扬镳')])
            menu=[line('地图',.26,.03),line('角色',.44,.03),line('读物',.61,.03)]
            self.assertEqual(detect(menu+rows),[])

    def test_flower_chapters_are_distinct_in_first_collection_filter(self):
        from app.collection_taxonomy import FirstCollectionFilter
        first=FirstCollectionFilter()
        candidates=[{'kind':'memory_flower','name':name,'time':stamp} for stamp,name in enumerate([
            '龙体反抗者的记忆之花 · 其一:旧日', '龙体反抗者的记忆之花 · 其二:分道扬镳',
            '龙体反抗者的记忆之花 · 其二:分道扬镳'])]
        self.assertEqual(len(first.filter(candidates)),2)

    def test_flower_title_boxes_can_overlap_at_quotes(self):
        rows=[line('记忆之花花瓣',.664,.191,w=.057,h=.017),
              line('有关',.666,.224,w=.043,h=.037),
              line('“同门',.697,.222,w=.070,h=.041),
              line('的记忆之花',.785,.226,w=.1,h=.034),
              line('其一：同门之谊',.666,.28)]
        self.assertEqual(detect(rows),[('memory_flower','有关同门的记忆之花 · 其一:同门之谊')])

    def test_memory_chapter_question_survives_persistence_filter(self):
        rows=[line('记忆之花花瓣',.67,.18),line('陆家村村民的记忆之花',.67,.22),
              line('其二：是我儿吗？',.67,.28)]
        candidates=detect_collection_candidates(rows,(900,1600),1254.5)
        kept=FirstCollectionFilter().filter(candidates)
        self.assertEqual([(e['kind'],e['name']) for e in kept],
                         [('memory_flower','陆家村村民的记忆之花 · 其二:是我儿吗?')])

    def test_fading_flower_header_keeps_chapter_when_colon_or_category_is_missed(self):
        rows=[line('有关同门的记忆之花',.67,.22),line('其三绝不姑息',.67,.28),
              line('(3/3)集齐全部记忆线余,生命值78',.67,.42)]
        self.assertEqual(detect(rows),[('memory_flower','有关同门的记忆之花 · 其三:绝不姑息')])
        self.assertEqual(detect(rows[:2]),[])
        rows=[line('记忆之花花瓣',.67,.18),line('有关龙体之术的记忆之花',.67,.22),
              line('共一:手谕',.67,.28)]
        self.assertEqual(detect(rows),[('memory_flower','有关龙体之术的记忆之花 · 其一:手谕')])
    def test_book_panel_keeps_heading_and_discards_description(self):
        self.assertEqual(detect([line('书籍', .67, .16), line('新娘阿花', .67, .20),
                                 line('约定的人没有来，来的是他的死讯。', .67, .33)]), [('book', '新娘阿花')])

    def test_book_chapters_remain_distinct_and_quotes_are_balanced(self):
        for chapter in ('四凶斧之乱·祸乱', '四凶斧之乱·变故'):
            self.assertEqual(detect([line('书籍', .67, .16), line('“四凶斧”案卷', .67, .20),
                                     line(chapter, .67, .265)]), [('book', '四凶斧案卷 · ' + chapter)])
        converted = classify_existing_event({'kind':'book','name':'“四凶斧”案卷 · 四凶斧之乱·祸乱'})
        self.assertEqual(converted['name'], '四凶斧案卷 · 四凶斧之乱·祸乱')

    def test_books_keep_repeat_appearances_and_long_punctuated_titles(self):
        title='边城旧事:关于一封辗转多年才送达故乡的长信,以及未被写进卷宗的往事'
        chapter='卷末附录:那些远赴他乡而从未在史册中留下姓名的人的记录'
        mapped=classify_existing_event({'kind':'book','name':title+' · '+chapter,'time':10})
        self.assertEqual(mapped['name'],title+' · '+chapter)
        self.assertFalse(mapped['first_occurrence_only'])
        first=FirstCollectionFilter()
        self.assertEqual(len(first.filter([mapped])),1)
        self.assertEqual(len(first.filter([{**mapped,'time':20}])),1)
        rows=[line('书籍',.67,.16),line(title,.67,.20,.28,.03),line(chapter,.67,.265,.28,.03)]
        self.assertEqual(detect(rows),[('book',title+' · '+chapter)])
        # Existing classified records must not disappear just for punctuation.
        self.assertEqual(classify_existing_event({'kind':'book','name':'他回来了吗？'})['name'],'他回来了吗?')

    def test_book_menu_covers_and_opened_chapters_are_excluded(self):
        tabs=[line('地图',.26,.03),line('角色',.44,.03),line('读物',.61,.03)]
        for title,chapter in [('新娘阿花',None),('四凶斧案卷','斧老大的绝笔信')]:
            rows=[line('书籍',.67,.16),line(title,.67,.20)]
            if chapter:
                rows.append(line(chapter,.67,.265))
            self.assertEqual(detect(tabs+rows),[])
            expected=title+(' · '+chapter if chapter else '')
            self.assertEqual(detect(rows),[('book',expected)])
        self.assertIsNone(classify_existing_event({'kind':'book','name':'新娘阿花','evidence':'book_menu'}))

    def test_world_book_question_chapters_are_preserved(self):
        for title,chapter in [('沉思杂记','龙柱封着何物?'),('杀气改造之谜','杀气是什么?')]:
            self.assertEqual(detect([line('书籍',.67,.18),line(title,.67,.23),line(chapter,.67,.28)]),
                             [('book',title+' · '+chapter)])

    def test_non_imperative_task_banners_are_task_appearances(self):
        import numpy as np
        frame=np.zeros((900,1600,3),np.uint8)
        frame[195:355,1060:1590]=220
        for title,description,kind in [('三面混沌','那有三张面孔的人偶究竟是什么','task_rumor'),
                                       ('昆仑三杰','昆仑三杰究竟发生了什么？','task_story'),
                                       ('红房子','在未来探索中留意进入红房子的办法','task_story')]:
            rows=[line(title,.747,.24,.09,.045),line(description,.748,.294,.18,.03)]
            events=detect_collection_candidates(rows,frame.shape,1909,frame)
            self.assertEqual([(e['kind'],e['name']) for e in events],[(kind,title)])
            self.assertEqual(events[0]['evidence'],'task_banner')
            self.assertNotIn('完成',events[0]['text'])
            self.assertEqual(detect_collection_candidates(rows,frame.shape,1909,np.zeros_like(frame)),[])
        self.assertEqual(detect_collection_candidates([line('红房子',.747,.24,.09,.045)],frame.shape,1909,frame),[])

    def test_selected_story_and_rumor_rows_only(self):
        tabs = [line('地图', .26, .03), line('角色', .44, .03), line('系统', .71, .03)]
        for category, expected, title in [('故事', 'task_story', '血脉觉醒'), ('异闻', 'task_rumor', '血肉花'),
                                          ('收集', 'task_collection', '古城遗珍')]:
            with self.subTest(category=category):
                # OCR may include the selected-row icon in the title box.
                rows = tabs + [line(category, .17, .20), line(title, .04, .265, .20, .05),
                               line('红房子', .10, .38), line('虚空幻象', .10, .44)]
                self.assertEqual(detect(rows), [(expected, title)])

    def test_empty_task_category_does_not_invent_a_task(self):
        self.assertEqual(detect([line('地图', .26, .03), line('角色', .44, .03), line('系统', .71, .03),
                                 line('收集', .17, .20)]), [])

    def test_highlighted_task_can_be_lower_in_list(self):
        import numpy as np
        frame = np.zeros((900, 1600, 3), dtype=np.uint8)
        frame[380:450, :560] = 215
        rows = [line('地图', .26, .03), line('角色', .44, .03), line('系统', .71, .03),
                line('故事', .17, .20), line('血脉觉醒', .10, .27), line('红房子', .10, .44)]
        events = detect_collection_candidates(rows, frame.shape, 10, frame)
        self.assertEqual([(e['kind'],e['name']) for e in events], [('task_story','红房子')])

    def test_map_legend_and_tutorial_words_are_not_collectibles(self):
        self.assertEqual(detect([line('铃铛', .05, .50), line('武器及影之武装', .20, .50),
                                 line('黑石碎片', .65, .50), line('记忆之花', .80, .50)]), [])
        self.assertEqual(detect([line('铃铛', .42, .20), line('触摸铃铛可以回复生命', .3, .40)]), [])

    def test_typed_materials_and_accessories(self):
        for category, title, kind in [('重要养成材料', '杀气琥珀碎片', 'amber'),
                                      ('重要养成材料', '黑石碎片', 'blackstone'),
                                      ('重要养成材料', '杀气琥珀', 'amber'),
                                      ('生存类饰品', '香薰药盒', 'accessory'),
                                      ('攻击类饰品', '影动猎杀仪', 'accessory'),
                                      ('秘药', '凝锋丹', 'medicine'),
                                      ('回复药', '药葫芦', 'medicine'),
                                      ('单手武器', '孤影', 'weapon')]:
            with self.subTest(title=title):
                self.assertEqual(detect([line(category, .66, .16), line(title, .66, .20)]), [(kind, title)])

    def test_story_items_require_typed_panel_and_remain_first_only(self):
        for category in ('剧情道具','劇情道具'):
            events=detect_collection_candidates([line(category,.66,.16),line('锈蚀的龙形钥匙',.66,.20),
                                                  line('用于打开某处机关的钥匙。',.66,.35)],(900,1600),1987)
            self.assertEqual([(e['kind'],e['name']) for e in events],[('story_item','锈蚀的龙形钥匙')])
            first=FirstCollectionFilter()
            self.assertEqual(len(first.filter(events)),1)
            self.assertEqual(first.filter(events),[])
        self.assertEqual(detect([line('锈蚀的龙形钥匙',.66,.20)]),[])
        self.assertIsNone(classify_existing_event({'kind':'text','name':'锈蚀的龙形钥匙'}))
        mapped=classify_existing_event({'kind':'item','name':'锈蚀的龙形钥匙',
            'text':'剧情道具\n锈蚀的龙形钥匙','evidence':'item_panel'})
        self.assertEqual(mapped['kind'],'story_item')
        self.assertTrue(mapped['first_occurrence_only'])

    def test_generic_text_and_rollbacks_are_rejected(self):
        for name in ['黑石碎片', '铃铛', '鹤型铃铛', '按住进入架势状态', '溶剂+8']:
            self.assertIsNone(classify_existing_event({'kind':'text', 'name':name, 'text':name}))
        self.assertEqual(detect([line('武器', .3, .04), line('影之武装', .5, .04),
                                 line('溶剂+8', .75, .45), line('铸造金属+16', .75, .55),
                                 line('回退', .9, .94)]), [])

    def test_legacy_task_subtype_requires_evidence(self):
        mapped = classify_existing_event({'kind':'quest', 'name':'找到哭声的来历',
            'text':'任务提示；未判定接取、更新或完成\n泣声何来\n找到哭声的来历', 'evidence':'quest_notice'})
        self.assertEqual((mapped['kind'], mapped['name']), ('task_story', '泣声何来'))
        self.assertIsNone(classify_existing_event({'kind':'quest','name':'收集十个果实','text':'收集十个果实','evidence':'quest_notice'}))

    def test_legacy_book_uses_title_not_prose(self):
        mapped = classify_existing_event({'kind':'item','name':'随十一人阁一同散播混乱的是一种',
            'text':'书籍展示面板；首次获取待核实\n书籍\n杀气改造之谜\n随十一人阁一同散播混乱的是一种', 'evidence':'item_panel'})
        self.assertEqual((mapped['kind'], mapped['name']), ('book', '杀气改造之谜'))

    def test_bells_require_waypoint_context(self):
        self.assertEqual(classify_existing_event({'kind':'waypoint', 'name':'残偶谷底', 'evidence':'waypoint_menu'})['kind'], 'bell')
        self.assertIsNone(classify_existing_event({'kind':'text','name':'悲泣溪谷龙柱','evidence':'map_selection'}))

    def test_only_named_boss_evidence(self):
        self.assertEqual(detect([line('冥使:判官', .43, .06)]), [('boss','冥使:判官')])
        self.assertEqual(detect([line('冥使:判官', .43, .88)]), [])
        self.assertIsNone(classify_existing_event({'kind':'enemy','name':'首领击败（名称待核实）','evidence':'victory_template'}))

    def test_first_only_and_quantity_normalization(self):
        first = FirstCollectionFilter()
        a = {'kind':'amber','name':'杀气琥珀碎片(1/4)'}
        self.assertEqual(first.filter([a])[0]['name'], '杀气琥珀碎片')
        self.assertEqual(first.filter([{'kind':'amber','name':'杀气琥珀碎片(2/4)'}])[0]['name'], '杀气琥珀碎片')
        self.assertEqual(len(first.filter([{'kind':'blackstone','name':'黑石碎片'}])), 1)
        self.assertEqual(len(first.filter([{'kind':'blackstone','name':'黑石碎片'}])), 1)
        self.assertEqual(len(first.filter([{'kind':'weapon','name':'羽弓'}])), 1)
        self.assertEqual(first.filter([{'kind':'weapon','name':'羽弓'}]), [])

    def test_legacy_medicine_split_requires_category_not_name_guess(self):
        event={'kind':'accessory','name':'药葫芦','text':'回复药\n药葫芦'}
        self.assertEqual(classify_existing_event(event)['kind'],'medicine')
        self.assertEqual(classify_existing_event({**event,'text':'药葫芦'})['kind'],'accessory')
        event={'kind':'accessory','name':'香薰药盒','text':'生存类饰品\n香薰药盒'}
        self.assertEqual(classify_existing_event(event)['kind'],'accessory')

    def test_repeat_fragments_merge_only_while_same_prompt_is_visible(self):
        from app.analyzer import EventAccumulator
        saved=[]
        accumulator=EventAccumulator('test',1,saved.append)
        first=FirstCollectionFilter()
        for timestamp in (1,2,3,20,21):
            events=first.filter([{'kind':'blackstone','name':'黑石碎片'}])
            accumulator.add(events,timestamp,lambda:'example.jpg')
        accumulator.flush()
        by_id={e['id']:e for e in saved}
        self.assertEqual([(e['time'],e['end_time']) for e in by_id.values()],[(1,4),(20,22)])

    def test_profile_default_and_explicit_legacy(self):
        self.assertEqual(_validate_options({'game_profile':'phantom_blade_zero_demo'})['text_mode'], 'off')
        self.assertEqual(_validate_options({'game_profile':'phantom_blade_zero_demo','recognition_scope':'legacy'})['text_mode'], 'compact')
        self.assertEqual(_validate_options({})['recognition_scope'], 'legacy')
        with self.assertRaises(ValueError):
            _validate_options({'recognition_scope':'all'})

    def test_user_marker_is_not_discarded(self):
        marker = {'kind':'note', 'name':'路线起点', 'source':'marker'}
        self.assertEqual(classify_existing_event(marker), marker)

    def test_verified_aliases_are_kind_scoped_and_do_not_merge_unknown_names(self):
        cases = [('weapon','黑伤口','黑伤'), ('weapon','黑伤自','黑伤'),
                 ('weapon','长形黑伤口','长形黑伤'), ('weapon','木制黑伤[','木制黑伤'),
                 ('weapon','黑伤双刃口','黑伤双刃'), ('weapon','残钢刃口','残钢刃'),
                 ('bell','斧老大落处','斧老大斩落处'), ('bell','远跳龙柱处','远眺龙柱处'),
                 ('bell','聋老大关押处','斧老大关押处'), ('bell','爷老大关押处','斧老大关押处'),
                 ('bell','溪谷平原入','溪谷平原入口'), ('bell','溪谷杀气','溪谷杀气'),
                 ('bell','溪谷杀','溪谷杀'), ('weapon','未知山口','未知山口'),
                 ('book','黑伤口','黑伤口')]
        for kind, name, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(classify_existing_event({'kind':kind,'name':name})['name'], expected)


if __name__ == '__main__':
    unittest.main()
