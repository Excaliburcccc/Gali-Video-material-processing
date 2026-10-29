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
    def test_book_panel_keeps_heading_and_discards_description(self):
        self.assertEqual(detect([line('书籍', .67, .16), line('新娘阿花', .67, .20),
                                 line('约定的人没有来，来的是他的死讯。', .67, .33)]), [('book', '新娘阿花')])

    def test_book_chapters_remain_distinct_and_quotes_are_balanced(self):
        for chapter in ('四凶斧之乱·祸乱', '四凶斧之乱·变故'):
            self.assertEqual(detect([line('书籍', .67, .16), line('“四凶斧”案卷', .67, .20),
                                     line(chapter, .67, .265)]), [('book', '四凶斧案卷 · ' + chapter)])
        converted = classify_existing_event({'kind':'book','name':'“四凶斧”案卷 · 四凶斧之乱·祸乱'})
        self.assertEqual(converted['name'], '四凶斧案卷 · 四凶斧之乱·祸乱')

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
                                      ('秘药', '凝锋丹', 'accessory'),
                                      ('回复药', '药葫芦', 'accessory'),
                                      ('单手武器', '孤影', 'weapon')]:
            with self.subTest(title=title):
                self.assertEqual(detect([line(category, .66, .16), line(title, .66, .20)]), [(kind, title)])

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
        self.assertEqual(first.filter([{'kind':'amber','name':'杀气琥珀碎片(2/4)'}]), [])
        self.assertEqual(len(first.filter([{'kind':'blackstone','name':'黑石碎片'}])), 1)

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
