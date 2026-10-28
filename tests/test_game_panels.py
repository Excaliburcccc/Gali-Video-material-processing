import unittest

from app.game_panels import detect_panels
from app.rules import OCRLine

# Representative original-pixel OCR boxes from the supplied demo. Keep these
# inline so normal tests do not require the user's video or audit scratch files.
BOOK_OCR = [
    OCRLine('书籍', .662, [[1281.6,204.0],[1317.6,204.0],[1317.6,228.0],[1281.6,228.0]]),
    OCRLine('“四凶爷”案卷', .6818, [[1308.0,240.0],[1554.0,240.0],[1554.0,285.6],[1308.0,285.6]]),
    OCRLine('四凶斧之乱·混乱', .8483, [[1278.0,301.2],[1494.0,301.2],[1494.0,333.6],[1278.0,333.6]]),
]
WEAPON_OCR = [
    OCRLine('单手武器', .7936, [[1286.4,169.2],[1359.6,169.2],[1359.6,196.8],[1286.4,196.8]]),
    OCRLine('痴者残剑', .7985, [[1281.6,208.8],[1494.0,208.8],[1494.0,254.4],[1281.6,254.4]]),
    OCRLine('则会放出强化攻击，具备更强的伤害和破防能力，', .8708, [[1281.6,700.8],[1800.0,700.8],[1800.0,732.0],[1281.6,732.0]]),
    OCRLine('装备新的武器？', .8589, [[1287.6,933.6],[1443.6,933.6],[1443.6,961.2],[1287.6,961.2]]),
]
UPGRADE_OCR = [
    OCRLine('单手武器', .7964, [[1286.4,169.2],[1359.6,169.2],[1359.6,196.8],[1286.4,196.8]]),
    OCRLine('痴者残剑', .7989, [[1284.0,210.0],[1442.4,210.0],[1442.4,252.0],[1284.0,252.0]]),
    OCRLine('当前拥有 ', .699, [[1605.6,738.0],[1682.4,738.0],[1682.4,766.8],[1605.6,766.8]]),
    OCRLine('耗费', .6625, [[1743.6,740.4],[1782.0,740.4],[1782.0,764.4],[1743.6,764.4]]),
    OCRLine('按住消耗+1620升级', .8325, [[1431.6,913.2],[1680.0,913.2],[1680.0,945.6],[1431.6,945.6]]),
]
QUEST_ITEM_OCR = [
    OCRLine('剧情道具',.7805,[[1065,169],[1127,169],[1127,192],[1065,192]]),
    OCRLine('杀气炉探火孔开关',.8155,[[1067,200],[1325,200],[1325,238],[1067,238]]),
    OCRLine('满是油污的圆形开关，在高温的熏陶下已有些许形',.917,[[1063,309],[1508,309],[1508,336],[1063,336]]),
    OCRLine('8确定',.6011,[[1447,834],[1522,834],[1522,862],[1447,862]]),
]
QUEST_STONE_OCR = [
    OCRLine('剧情道具',.7707,[[1063,167],[1128,167],[1128,195],[1063,195]]),
    OCRLine('浑源石',.7445,[[1063,200],[1167,200],[1167,240],[1063,240]]),
    OCRLine('表面遍布裂纹却永不碎裂的奇石，炽热的光芒在',.9282,[[1063,308],[1493,306],[1493,334],[1063,336]]),
    OCRLine('确定',.6646,[[1445,835],[1520,835],[1520,859],[1445,859]]),
]


def line(text, x, y, w=.18, h=.03, score=.85):
    return OCRLine(text, score, [[x*1920,y*1080],[(x+w)*1920,y*1080],[(x+w)*1920,(y+h)*1080],[x*1920,(y+h)*1080]])


class PanelTests(unittest.TestCase):
    def test_real_quest_item_panels_require_spatial_confirm_and_keep_name(self):
        for lines,name,stamp in [(QUEST_ITEM_OCR,'杀气炉探火孔开关',628),
                                 (QUEST_STONE_OCR,'浑源石',674)]:
            with self.subTest(name=name):
                result=detect_panels(lines,(900,1600),stamp)
                self.assertEqual(len(result),1)
                self.assertEqual(result[0]['name'],name)
                self.assertEqual(result[0]['panel_type'],'quest_item')
                self.assertEqual(result[0]['evidence'],'item_panel')
                self.assertEqual(result[0]['verified'],0)
                self.assertIn('首次获取待核实',result[0]['text'])

    def test_quest_item_needs_category_heading_and_confirmation(self):
        for indexes in [(0,1,2),(1,2,3),(0,2,3)]:
            self.assertEqual(detect_panels([QUEST_ITEM_OCR[i] for i in indexes],(900,1600),1),[])
        wrong_confirm=QUEST_ITEM_OCR[:-1]+[OCRLine('确定',.9,[[100,200],[200,200],[200,230],[100,230]])]
        self.assertEqual(detect_panels(wrong_confirm,(900,1600),1),[])

    def test_inventory_navigation_and_rollback_veto_quest_item_panel(self):
        scaled=[OCRLine(v.text,v.score,[[x*1.2,y*1.2] for x,y in v.box]) for v in QUEST_ITEM_OCR]
        nav=[line('地图',.2,.03),line('任务',.35,.03),line('道具',.6,.03)]
        self.assertEqual(detect_panels(scaled+nav,(1080,1920),1),[])
        self.assertEqual(detect_panels(scaled+[line('回退',.85,.92,.05)],(1080,1920),1),[])
        self.assertEqual(detect_panels(scaled+[line('当前拥有',.84,.68)],(1080,1920),1),[])

    def test_arbitrary_quest_item_names_and_scale_work_without_dictionary(self):
        lines=[line('劇情道具',.66,.18,.07),line('未见过的铜钥匙',.66,.23,.18,.04),
               line('A確認',.89,.92,.05)]
        result=detect_panels(lines,(1080,1920),15)
        self.assertEqual(result[0]['name'],'未见过的铜钥匙')
        half=[OCRLine(v.text,v.score,[[x/2,y/2] for x,y in v.box]) for v in lines]
        self.assertEqual(detect_panels(half,(540,960),15)[0]['panel_type'],'quest_item')

    def test_real_book_ocr_boxes_keep_chapter_name_and_uncertainty(self):
        result=detect_panels(BOOK_OCR,(1080,1920),101)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['name'],'四凶斧之乱·混乱')
        self.assertEqual(result[0]['evidence'],'item_panel')
        self.assertEqual(result[0]['verified'],0)
        self.assertIn('首次获取待核实',result[0]['text'])
        self.assertIn('四凶爷',result[0]['text']) # preserve imperfect OCR evidence

    def test_real_new_weapon_description_does_not_trigger_upgrade_veto(self):
        result=detect_panels(WEAPON_OCR,(1080,1920),1050)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['name'],'痴者残剑')
        self.assertEqual(result[0]['confidence'],.7936)
        self.assertEqual(result[0]['evidence'],'item_panel')
        self.assertEqual(result[0]['verified'],0)

    def test_real_upgrade_menu_is_excluded_even_if_stale_prompt_exists(self):
        self.assertEqual(detect_panels(UPGRADE_OCR,(1080,1920),1090),[])
        self.assertEqual(detect_panels(UPGRADE_OCR+[WEAPON_OCR[-1]],(1080,1920),1090),[])

    def test_different_chapters_are_distinct_without_known_names(self):
        base=[line('书籍',.66,.19,.04,.025),line('任意案卷',.66,.235,.18,.04)]
        first=detect_panels(base+[line('章节甲',.66,.29)],(1080,1920),1)
        second=detect_panels(base+[line('章节乙',.66,.29)],(1080,1920),500)
        self.assertEqual(first[0]['name'],'章节甲')
        self.assertEqual(second[0]['name'],'章节乙')

    def test_missing_chapter_does_not_use_distant_body(self):
        result=detect_panels([line('书籍',.66,.19,.04,.025),line('某案卷',.66,.235),line('正文开始',.66,.44)],(1080,1920),10)
        self.assertEqual(result,[])

    def test_subtitle_names_are_not_panels(self):
        result=detect_panels([line('书籍',.3,.80),line('某案卷',.3,.85),line('章节甲',.3,.90)],(1080,1920),10)
        self.assertEqual(result,[])

    def test_inventory_and_upgrade_menus_are_excluded(self):
        base=[line('书籍',.66,.19,.04,.025),line('某案卷',.66,.235),line('章节甲',.66,.29)]
        self.assertEqual(detect_panels(base+[line('背包',.1,.04)],(1080,1920),1),[])
        weapon=[line('单手武器',.67,.16,.06,.02),line('任意武器',.67,.20,.15,.04),line('装备新的武器？',.67,.86)]
        self.assertEqual(detect_panels(weapon+[line('按住消耗1620升级',.66,.85)],(1080,1920),1),[])

    def test_weapon_requires_new_equip_prompt(self):
        weapon=[line('单手武器',.67,.16,.06,.02),line('任意武器',.67,.20,.15,.04)]
        self.assertEqual(detect_panels(weapon,(1080,1920),1),[])
        result=detect_panels(weapon+[line('装备新的武器？',.67,.86)],(1080,1920),1)
        self.assertEqual(result[0]['name'],'任意武器')
        self.assertEqual(result[0]['panel_type'],'weapon')
        self.assertEqual(result[0]['verified'],0)

    def test_wrong_space_and_missing_boxes_are_rejected(self):
        result=detect_panels([OCRLine('书籍'),OCRLine('任意案卷'),OCRLine('章节甲')],(1080,1920),1)
        self.assertEqual(result,[])
        result=detect_panels([line('书籍',.66,.19,.04,.025),line('任意案卷',.20,.235),line('章节甲',.20,.29)],(1080,1920),1)
        self.assertEqual(result,[])

    def test_frame_scaling_does_not_change_candidate(self):
        lines=[line('书籍',.66,.19,.04,.025),line('任意案卷',.66,.235),line('章节甲',.66,.29)]
        half=[OCRLine(l.text,l.score,[[x/2,y/2] for x,y in l.box]) for l in lines]
        self.assertEqual(detect_panels(half,(540,960),42)[0]['name'],'章节甲')


if __name__=='__main__':
    unittest.main()
