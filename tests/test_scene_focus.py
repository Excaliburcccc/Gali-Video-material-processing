import unittest

from app.rules import OCRLine, classify_lines
from app.scene_focus import apply_scene_focus, detect_scene_focus


SHAPE = (900, 1600)


def line(text, left, top, right, bottom, score=.8):
    return OCRLine(text, score, [[left * 1600, top * 900], [right * 1600, top * 900],
                              [right * 1600, bottom * 900], [left * 1600, bottom * 900]])


def controls():
    return [line('地图完整度：15%', .04, .87, .15, .91),
            line('进入全域地图', .79, .93, .89, .97),
            line('悲泣溪谷', .04, .79, .13, .84)]


class SceneFocusTests(unittest.TestCase):
    def test_real_map_geometry_keeps_only_center_location(self):
        observed = controls() + [
            line('英红丝线障壁', 1310/1600, 390/900, 1418/1600, 414/900, .6835),
            line('α残偶谷底', 807/1600, 437/900, 932/1600, 465/900, .7106),
            line('龚初见玉儿处', 1410/1600, 700/900, 1517/1600, 731/900, .7584)]
        focus = detect_scene_focus(observed, SHAPE)
        self.assertEqual(focus.context, 'map')
        self.assertEqual(focus.names, ('残偶谷底',))
        output = apply_scene_focus(classify_lines(observed), focus)
        self.assertEqual([item['name'] for item in output], ['残偶谷底'])
        self.assertEqual(output[0]['evidence'], 'map_selection')
        self.assertEqual(output[0]['text'], 'α残偶谷底')

    def test_another_map_does_not_select_nearby_other_floor(self):
        observed = controls() + [
            line('龚溪谷杀气塔顶', .4825, .30, .5575, .331),
            line('龚恶弓手营地', .114, .418, .183, .455),
            line('溪谷杀气塔一层', .49375, 437/900, .62, 465/900)]
        focus = detect_scene_focus(observed, SHAPE)
        self.assertEqual(focus.names, ('溪谷杀气塔一层',))

    def test_unknown_map_selection_drops_background_labels(self):
        observed = controls() + [line('远方地点', .8, .40, .9, .45)]
        focus = detect_scene_focus(observed, SHAPE)
        self.assertEqual(focus.names, ())
        self.assertEqual(apply_scene_focus(classify_lines(observed), focus), [])

    def test_ambiguous_center_labels_do_not_guess(self):
        observed = controls() + [line('中心甲', .50, .48, .60, .52),
                                 line('中心乙', .50, .50, .60, .54)]
        # Both have near-identical eligibility; uncertain selection yields none.
        observed[-1] = line('中心乙', .50, .49, .60, .53)
        self.assertEqual(detect_scene_focus(observed, SHAPE).names, ())

    def test_a_map_word_alone_does_not_enable_scene_filter(self):
        observed = [line('进入全域地图', .79, .93, .89, .97),
                    line('地点名', .5, .48, .65, .52)]
        self.assertIsNone(detect_scene_focus(observed, SHAPE))

    def test_boss_hud_suppresses_components_and_unrelated_text(self):
        observed = [line('剑痴：花千笑', .40, .05, .60, .10),
                    line('花千笑', .47, .06, .58, .11),
                    line('旁白中的词', .40, .88, .60, .94)]
        focus = detect_scene_focus(observed, SHAPE)
        output = apply_scene_focus(classify_lines(observed), focus)
        self.assertEqual([item['name'] for item in output], ['剑痴:花千笑'])
        self.assertEqual(output[0]['evidence'], 'boss_name')

    def test_boss_name_in_dialogue_is_not_a_hud(self):
        self.assertIsNone(detect_scene_focus([line('剑痴：花千笑', .40, .85, .60, .90)], SHAPE))

    def test_specialized_events_survive_focus(self):
        focus = detect_scene_focus(controls(), SHAPE)
        events = [{'kind': kind, 'name': '事件', 'confidence': .9}
                  for kind in ('item', 'enemy', 'achievement', 'quest', 'waypoint')]
        self.assertEqual(apply_scene_focus(events, focus), events)

    def test_no_focus_is_a_no_op(self):
        events = [{'kind': 'text', 'name': '另一个场景'}]
        self.assertEqual(apply_scene_focus(events, None), events)

    def test_bad_boxes_are_ignored(self):
        broken = OCRLine('剑痴：花千笑', .9, [[float('nan'), 1]] * 4)
        self.assertIsNone(detect_scene_focus([broken], SHAPE))
        self.assertIsNone(detect_scene_focus([broken], (0, 0)))

    def test_task_catalogue_does_not_index_other_tasks_and_world_title(self):
        observed = [line('地图', .26, .03, .30, .065),
                    line('角色', .44, .03, .48, .065),
                    line('读物', .62, .03, .66, .065),
                    line('故事', .17, .20, .20, .23),
                    line('钢铁巨塔', .10, .27, .19, .31),
                    line('溪谷涤处', .10, .38, .18, .42),
                    line('泣声何来', .10, .44, .18, .48),
                    line('逆流而上', .10, .50, .18, .54),
                    line('悲泣溪谷', .87, .83, .95, .87)]
        focus = detect_scene_focus(observed, SHAPE)
        self.assertEqual(focus.context, 'task_menu')
        self.assertEqual(apply_scene_focus(classify_lines(observed), focus), [])
        event = {'kind': 'quest', 'name': '在塔顶寻找钥匙'}
        self.assertEqual(apply_scene_focus([event], focus), [event])

    def test_inventory_navigation_alone_is_not_task_catalogue(self):
        observed = [line('地图', .26, .03, .30, .065),
                    line('角色', .44, .03, .48, .065),
                    line('读物', .62, .03, .66, .065),
                    line('装备', .17, .20, .20, .23),
                    line('剑', .10, .30, .15, .34)]
        self.assertIsNone(detect_scene_focus(observed, SHAPE))

    def test_dialogue_keeps_full_speaker_and_removes_short_subtitle(self):
        observed = [line('友善的“组织”鬼差', 682/1600, 718/900, 915/1600, 751/900),
                    line('确是此物！', 760/1600, 764/900, 872/1600, 797/900)]
        focus = detect_scene_focus(observed, SHAPE)
        self.assertEqual(focus.context, 'dialogue')
        output = apply_scene_focus(classify_lines(observed), focus)
        self.assertEqual([item['name'] for item in output], ['友善的“组织”鬼差'])
        self.assertEqual(output[0]['evidence'], 'dialogue_speaker')

    def test_dialogue_joins_three_name_fragments_in_one_row(self):
        observed = [line('友善的', 685/1600, 720/900, 770/1600, 749/900),
                    line('“组织”', 758/1600, 720/900, 858/1600, 748/900),
                    line('鬼差', 857/1600, 723/900, 913/1600, 748/900),
                    line('啊！确是此物！', 707/1600, 769/900, 875/1600, 797/900)]
        focus = detect_scene_focus(observed, SHAPE)
        output = apply_scene_focus(classify_lines(observed), focus)
        self.assertEqual([item['name'] for item in output], ['友善的“组织”鬼差'])
        self.assertIn('鬼差', output[0]['text'])

    def test_single_lower_text_line_is_not_invented_as_a_speaker(self):
        self.assertIsNone(detect_scene_focus([line('这是人物对白', .40, .85, .60, .89)], SHAPE))

    def test_weapon_inventory_keeps_main_heading_not_left_tabs(self):
        observed = [line('双持武器', 1072/1600, 141/900, 1133/1600, 164/900),
                    line('黑伤双刃 (', 1068/1600, 168/900, 1216/1600, 219/900),
                    line('伤害能力268', 1073/1600, 242/900, 1222/1600, 265/900),
                    line('破防能力140', 1380/1600, 242/900, 1525/1600, 265/900),
                    line('药瓶', 390/1600, 255/900, 438/1600, 280/900),
                    line('双刀乱舞', .67, .66, .74, .70)]
        focus = detect_scene_focus(observed, SHAPE)
        self.assertEqual(focus.context, 'inventory')
        output = apply_scene_focus(classify_lines(observed), focus)
        self.assertEqual([item['name'] for item in output], ['黑伤双刃'])
        self.assertEqual(output[0]['evidence'], 'inventory_selection')
        self.assertEqual(output[0]['text'], '黑伤双刃 (')

    def test_weapon_upgrade_drops_passive_body(self):
        observed = [line('单手武器', 1070/1600, 137/900, 1135/1600, 165/900),
                    line('痴者残剑', 1070/1600, 175/900, 1203/1600, 210/900),
                    line('伤害能力381>408', 1072/1600, 240/900, 1270/1600, 266/900),
                    line('自海量', 1072/1600, 291/900, 1143/1600, 319/900),
                    line('回难得糊涂·之一', .67, .45, .78, .48)]
        output = apply_scene_focus(classify_lines(observed), detect_scene_focus(observed, SHAPE))
        self.assertEqual([item['name'] for item in output], ['痴者残剑'])

    def test_refund_toasts_without_weapon_heading_are_unaffected(self):
        observed = [line('武器', .43, .03, .47, .065),
                    line('影之武装', .51, .03, .58, .065),
                    line('溶剂+8', 1456/1600, 508/900, 1555/1600, 541/900),
                    line('铸造金属+16', 1415/1600, 597/900, 1557/1600, 625/900)]
        focus = detect_scene_focus(observed, SHAPE)
        self.assertIsNone(focus)
        output = apply_scene_focus(classify_lines(observed), focus)
        self.assertIn('溶剂+8', [item['name'] for item in output])
        self.assertIn('铸造金属+16', [item['name'] for item in output])

    def test_weapon_name_panel_without_stats_is_not_inventory(self):
        observed = [line('单手武器', .67, .15, .72, .18),
                    line('新武器', .67, .20, .78, .24)]
        self.assertIsNone(detect_scene_focus(observed, SHAPE))

    def test_weapon_menu_recovers_faded_category_without_requiring_stats(self):
        # Actual inventory geometry; the category's 武 was read as 式 while
        # the menu/navigation and selected name remained visible.
        observed = [line('武器', .439, .029, .467, .065, .652),
                    line('影之武装', .514, .029, .578, .065, .796),
                    line('双手式器', .669, .152, .710, .178, .768),
                    line('测试长刀', .664, .189, .756, .235, .741),
                    line('回退', .839, .934, .887, .965, .591),
                    line('全部', .171, .202, .198, .230)]
        focus=detect_scene_focus(observed,SHAPE)
        self.assertEqual(focus.context,'inventory')
        self.assertEqual(focus.names,('测试长刀',))
        out=apply_scene_focus(classify_lines(observed),focus)
        self.assertEqual([e['name'] for e in out],['测试长刀'])

    def test_weak_weapon_category_needs_navigation_and_back_control(self):
        observed = [line('武器', .439, .029, .467, .065),
                    line('影之武装', .514, .029, .578, .065),
                    line('双手式器', .669, .152, .710, .178),
                    line('测试长刀', .664, .189, .756, .235),
                    line('伤害能力100', .67, .26, .80, .29)]
        self.assertIsNone(detect_scene_focus(observed,SHAPE))
        observed[0]=line('武器', .439, .50, .467, .54)
        observed.append(line('回退', .839, .934, .887, .965))
        self.assertIsNone(detect_scene_focus(observed,SHAPE))


if __name__ == '__main__':
    unittest.main()
