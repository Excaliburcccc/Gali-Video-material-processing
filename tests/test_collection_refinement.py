import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from app.collection_refinement import (
    _bell_template, _bell_templates, selected_bell_location, refine_collection_lines, is_world_book_panel,
    needs_story_item_refinement,
)
from app.collection_taxonomy import detect_collection_candidates
from app.rules import OCRLine
from app.scene_focus import _lines


def line(text, x, y, w=.12, h=.025, score=.95):
    return OCRLine(text, score, [[x*1600,y*900],[(x+w)*1600,y*900],
                                [(x+w)*1600,(y+h)*900],[x*1600,(y+h)*900]])


def map_controls():
    return [line('地图完整度：88%', .04,.87), line('查看收集详情',.04,.93),
            line('悲泣溪谷',.04,.80)]


class CollectionRefinementTests(unittest.TestCase):
    def test_treasure_category_near_miss_requires_confirmation_from_crop(self):
        frame=np.zeros((900,1600,3),np.uint8)
        rows=[line('妙手小张宝减',.665,.185,.08,.02),line('古代弩机',.665,.225,.09,.04)]
        self.assertTrue(needs_story_item_refinement(_lines(rows,frame.shape)))
        self.assertEqual(detect_collection_candidates(rows,frame.shape,1,frame),[])
        recovered=[line('妙手小张宝藏',.665,.185,.08,.02),rows[1]]
        with patch('app.collection_refinement._read_region',return_value=recovered):
            result=detect_collection_candidates(refine_collection_lines(rows,frame,Mock()),frame.shape,1,frame)
            self.assertEqual([(e['kind'],e['name']) for e in result],[('task_item','古代弩机')])

    def test_flower_refinement_recovers_chapter_without_indexing_menu(self):
        frame=np.zeros((900,1600,3),np.uint8)
        rows=[line('龙体反抗者的记忆之花',.67,.22)]
        recovered=[line('记忆之花花瓣',.67,.18),rows[0],line('其二：分道扬镳',.67,.28)]
        with patch('app.collection_refinement._read_region',return_value=recovered):
            refined=refine_collection_lines(rows,frame,Mock())
            events=detect_collection_candidates(refined,frame.shape,349,frame)
            self.assertEqual([(e['kind'],e['name']) for e in events],
                             [('memory_flower','龙体反抗者的记忆之花 · 其二:分道扬镳')])
            menu=[line('地图',.26,.03),line('角色',.44,.03),line('读物',.61,.03)]
            refined=refine_collection_lines(menu+rows,frame,Mock())
            self.assertEqual(detect_collection_candidates(refined,frame.shape,349,frame),[])

    def test_story_category_near_miss_only_triggers_bounded_reread(self):
        frame=np.zeros((900,1600,3),np.uint8)
        for label in ('副情道具','剧刷情道具','剧情通具'):
            rows=[line(label,.665,.185,.04,.02),line('凝脂叶',.665,.225,.065,.04)]
            self.assertTrue(needs_story_item_refinement(_lines(rows,frame.shape)))
            # Crop offsets (.65,.15), enlargement x2, original title positions.
            engine=Mock(return_value=([
                [[[48,63],[176,63],[176,99],[48,99]],'剧情道具',.75],
                [[[48,135],[256,135],[256,207],[48,207]],'凝脂叶',.78]],None))
            refined=refine_collection_lines(rows,frame,engine)
            engine.assert_called_once()
            events=detect_collection_candidates(refined,frame.shape,3790,frame)
            self.assertEqual([(e['kind'],e['name']) for e in events],[('story_item','凝脂叶')])
        # Neither prose mentioning a category nor a missing title can trigger it.
        for rows in ([line('剧情通具',.2,.5),line('凝脂叶',.2,.55)],
                     [line('剧情通具',.665,.185,.04,.02)],
                     [line('剧情道具',.665,.185,.04,.02),line('凝脂叶',.665,.225,.065,.04)]):
            engine=Mock()
            self.assertEqual(refine_collection_lines(rows,frame,engine),rows)
            engine.assert_not_called()

    def test_story_category_is_not_guessed_if_focused_read_is_still_wrong(self):
        frame=np.zeros((900,1600,3),np.uint8)
        rows=[line('副情道具',.665,.185,.04,.02),line('凝脂叶',.665,.225,.065,.04)]
        engine=Mock(return_value=([[[[48,63],[176,63],[176,99],[48,99]],'副情道具',.8]],None))
        refined=refine_collection_lines(rows,frame,engine)
        self.assertEqual(refined,rows)
        self.assertEqual(detect_collection_candidates(refined,frame.shape,1,frame),[])

    def test_shadow_categories_need_weapon_stats_and_only_keep_title(self):
        for category,name in [('武学','追命锁镰'),('远程','羽弓'),('破坏','巨凶斧'),('武学','另一个武装')]:
            rows=[line(category,.67,.15),line(name,.67,.20),line('伤害能力 247',.67,.27),
                  line('破防能力 213',.86,.27),line('钩锁飞渡',.67,.56),line('长篇介绍与操作按键',.67,.40)]
            events=detect_collection_candidates(rows,(900,1600),7)
            self.assertEqual([(e['kind'],e['name']) for e in events],[('weapon',name)])
            self.assertEqual(detect_collection_candidates(rows[:2],(900,1600),7),[])

    def test_arbitrary_bare_map_label_does_not_become_bell(self):
        frame=np.zeros((900,1600,3),np.uint8)
        events=detect_collection_candidates(map_controls()+[line('开端之地',.58,.64)],frame.shape,4,frame)
        self.assertEqual(events,[])

    def test_selected_bell_icon_can_move_but_unrelated_map_text_is_ignored(self):
        template=cv2.cvtColor(_bell_template(),cv2.COLOR_GRAY2BGR)
        for left,top in [(300,200),(800,420),(1100,580)]:
            frame=np.zeros((900,1600,3),np.uint8)
            frame[top:top+82,left:left+79]=template
            cx,cy=(left+40)/1600,(top+40)/900
            rows=map_controls()+[line('X 开端之地',cx+.02,cy-.01,.13,.02),
                                 line('其他铃铛',.75,.31),line('武器收藏',.30,.65)]
            events=detect_collection_candidates(rows,frame.shape,31,frame)
            self.assertEqual([(e['kind'],e['name'],e['time']) for e in events],[('bell','开端之地',31)])

    def test_travel_map_large_bell_and_white_location_marker(self):
        template, (cx,cy), _ = _bell_templates()[1]
        frame=np.zeros((900,1600,3),np.uint8)
        frame[409:491,756:840]=cv2.cvtColor(template,cv2.COLOR_GRAY2BGR)
        rows=map_controls()+[line('开端之地',(756+cx)/1600+.02,(409+cy)/900-.01)]
        events=detect_collection_candidates(rows,frame.shape,1380,frame)
        self.assertEqual([(e['kind'],e['name']) for e in events],[('bell','开端之地')])

    def test_dark_and_offset_current_location_bells(self):
        for template,(cx,cy),_ in _bell_templates()[2:]:
            frame=np.zeros((900,1600,3),np.uint8)
            h,w=template.shape
            frame[409:409+h,756:756+w]=cv2.cvtColor(template,cv2.COLOR_GRAY2BGR)
            rows=map_controls()+[line('圣母像裂缝',(756+cx)/1600+.011,(409+cy)/900-.01)]
            events=detect_collection_candidates(rows,frame.shape,6380,frame)
            self.assertEqual([(e['kind'],e['name']) for e in events],[('bell','圣母像裂缝')])

    def test_cursor_with_another_icon_and_unselected_bell_are_rejected(self):
        template=_bell_template()
        for mode in ('other-icon','unselected'):
            frame=np.zeros((900,1600),np.uint8)
            patch=template.copy()
            if mode=='other-icon':
                patch[22:57,29:49]=0
                patch[30:45,32:46]=255
            else:
                patch[:]=0
                patch[22:57,29:49]=template[22:57,29:49]
            frame[420:502,800:879]=patch
            self.assertIsNone(selected_bell_location(frame))

    def test_no_secondary_ocr_on_dialogue_or_text_dense_reading_page(self):
        engine=Mock()
        frame=np.zeros((900,1600,3),np.uint8)
        for rows in ([line('武学',.2,.55),line('远程',.2,.60)],
                     [line('书籍',.67,.16),line('杀气改造之谜',.67,.20),line('破坏一切',.67,.40)]):
            self.assertEqual(refine_collection_lines(rows,frame,engine),rows)
        engine.assert_not_called()

    def test_world_book_page_diamonds_gate_small_heading_ocr(self):
        frame=np.zeros((900,1600,3),np.uint8)
        for x in (1070,1090,1110,1130,1150):
            cv2.fillConvexPoly(frame,np.array([[x,309],[x+6,315],[x,322],[x-6,316]]),(110,110,110))
        rows=[line('沉思杂记',.665,.23),line('龙柱封着何物?',.665,.28)]
        self.assertTrue(is_world_book_panel(_lines(rows,frame.shape),frame))
        engine=Mock(return_value=([[[[30,30],[80,30],[80,60],[30,60]],'书籍',.66]],None))
        refined=refine_collection_lines(rows,frame,engine)
        engine.assert_called_once()
        self.assertIn('书籍',[item.text for item in refined])
        # The same book opened from the inventory is excluded even with a page row.
        menu=rows+[line('地图',.26,.03),line('角色',.44,.03),line('读物',.61,.03)]
        engine.reset_mock()
        refine_collection_lines(menu,frame,engine)
        engine.assert_not_called()

    def test_refinement_removes_rank_badge_and_replaces_corrupted_title(self):
        frame=np.zeros((900,1600,3),np.uint8)
        # A gold badge in the title slot, independent of the weapon name.
        cv2.rectangle(frame,(1200,176),(1220,208),(60,170,210),2)
        rows=[line('远程',.67,.15),line('未知武装自',.67,.20),line('伤害能力198',.67,.27)]
        captured=[]
        def engine(crop,**_):
            captured.append(crop)
            # OCR coordinates in the 2x (.64,.13,.97,.33) crop.
            return [[[[[94,126],[300,126],[300,170],[94,170]],'未知武装',.8]],None]
        refined=refine_collection_lines(rows,frame,engine)
        self.assertNotIn('未知武装自',[item.text for item in refined])
        self.assertIn('未知武装',[item.text for item in refined])
        self.assertEqual(captured[0].shape[:2],(360,1056))
        # All badge pixels were masked before recognition; no name dictionary.
        self.assertEqual(int(captured[0].max()),0)

    def test_two_numeric_weapon_stats_recover_missing_small_category(self):
        frame=np.zeros((900,1600,3),np.uint8)
        rows=[line('伤害能力309',.67,.27),line('破防能力208',.86,.27)]
        engine=Mock(return_value=(None,None))
        refine_collection_lines(rows,frame,engine)
        self.assertEqual(engine.call_count,2)
        engine.reset_mock()
        refine_collection_lines(rows[:1],frame,engine)
        engine.assert_not_called()

    def test_equip_control_heading_is_not_a_weapon(self):
        rows=[line('影之武装',.67,.16),line('装备影之武装',.67,.20)]
        self.assertEqual(detect_collection_candidates(rows,(900,1600),6389),[])

    def test_unreadable_refinement_does_not_erase_existing_title(self):
        frame=np.zeros((900,1600,3),np.uint8)
        rows=[line('远程',.67,.15),line('羽弓',.67,.20),line('伤害能力198',.67,.27)]
        self.assertEqual(refine_collection_lines(rows,frame,lambda *_a,**_k:(None,None)),rows)


if __name__=='__main__':
    unittest.main()
