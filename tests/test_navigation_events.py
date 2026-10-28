import math
import unittest

from app.navigation_events import detect_navigation
from app.rules import OCRLine

# OCR boxes extracted from actual 1600x900 demo frames; independent of the video
# and audit directories at test time. Preserve observed OCR spelling mistakes.
REAL_FRAMES = {
    123: [
    ],
    127: [
        OCRLine('英红丝线障壁', 0.6835, [[1310.0, 390.0], [1418.0, 390.0], [1418.0, 414.0], [1310.0, 414.0]]),
        OCRLine('α残偶谷底', 0.7106, [[807.0, 437.0], [932.0, 437.0], [932.0, 465.0], [807.0, 465.0]]),
        OCRLine('龚初见玉儿处', 0.7584, [[1410.0, 702.0], [1516.0, 700.0], [1517.0, 730.0], [1410.0, 731.0]]),
        OCRLine('悲泣溪谷', 0.7956, [[73.0, 716.0], [207.0, 716.0], [207.0, 756.0], [73.0, 756.0]]),
        OCRLine('地图完整度：15%', 0.8462, [[70.0, 784.0], [240.0, 788.0], [240.0, 816.0], [70.0, 812.0]]),
        OCRLine('口查看收集详情', 0.6919, [[75.0, 839.0], [225.0, 839.0], [225.0, 862.0], [75.0, 862.0]]),
        OCRLine('?缩放重置视图', 0.7484, [[1027.0, 842.0], [1245.0, 842.0], [1245.0, 869.0], [1027.0, 869.0]]),
        OCRLine('进入全域地图', 0.8566, [[1265.0, 842.0], [1423.0, 842.0], [1423.0, 870.0], [1265.0, 870.0]]),
        OCRLine('① 返回', 0.6106, [[1440.0, 841.0], [1521.0, 839.0], [1522.0, 868.0], [1441.0, 870.0]]),
    ],
    130: [
        OCRLine('能力修行', 0.7969, [[768.0, 665.0], [858.0, 665.0], [858.0, 693.0], [768.0, 693.0]]),
        OCRLine('保存记忆', 0.7987, [[770.0, 706.0], [862.0, 706.0], [862.0, 736.0], [770.0, 736.0]]),
        OCRLine('离开', 0.6466, [[777.0, 753.0], [813.0, 753.0], [813.0, 773.0], [777.0, 773.0]]),
        OCRLine('残偶谷底', 0.6932, [[75.0, 787.0], [214.0, 793.0], [212.0, 838.0], [73.0, 832.0]]),
    ],
    443: [
    ],
    445: [
        OCRLine('穿行', 0.6644, [[763.0, 599.0], [823.0, 599.0], [823.0, 635.0], [763.0, 635.0]]),
        OCRLine('装备升级', 0.7995, [[772.0, 644.0], [862.0, 644.0], [862.0, 672.0], [772.0, 672.0]]),
        OCRLine('能力修行', 0.7943, [[768.0, 687.0], [858.0, 687.0], [858.0, 715.0], [768.0, 715.0]]),
        OCRLine('保存记忆', 0.7986, [[770.0, 728.0], [862.0, 728.0], [862.0, 756.0], [770.0, 756.0]]),
        OCRLine('离开', 0.6566, [[772.0, 771.0], [818.0, 771.0], [818.0, 796.0], [772.0, 796.0]]),
        OCRLine('溪谷杀气塔顶', 0.8322, [[78.0, 794.0], [280.0, 794.0], [280.0, 834.0], [78.0, 834.0]]),
    ],
    561: [
        OCRLine('地图', 0.6663, [[417.0, 28.0], [478.0, 28.0], [478.0, 58.0], [417.0, 58.0]]),
        OCRLine('角色', 0.6264, [[700.0, 28.0], [760.0, 28.0], [760.0, 58.0], [700.0, 58.0]]),
        OCRLine('读物', 0.6429, [[982.0, 28.0], [1045.0, 28.0], [1045.0, 58.0], [982.0, 58.0]]),
        OCRLine('系统', 0.6664, [[1125.0, 28.0], [1188.0, 28.0], [1188.0, 58.0], [1125.0, 58.0]]),
        OCRLine('+ 8,150', 0.7895, [[1440.0, 30.0], [1523.0, 30.0], [1523.0, 60.0], [1440.0, 60.0]]),
        OCRLine('故事', 0.6473, [[270.0, 182.0], [317.0, 182.0], [317.0, 207.0], [270.0, 207.0]]),
        OCRLine('钢铁巨塔', 0.7764, [[165.0, 243.0], [300.0, 243.0], [300.0, 278.0], [165.0, 278.0]]),
        OCRLine('铁藜垂，影蔽日', 0.7923, [[163.0, 281.0], [277.0, 281.0], [277.0, 309.0], [163.0, 309.0]]),
        OCRLine('溪谷涤处', 0.6826, [[165.0, 341.0], [280.0, 341.0], [280.0, 374.0], [165.0, 374.0]]),
        OCRLine('泣声何来', 0.7009, [[163.0, 394.0], [280.0, 394.0], [280.0, 427.0], [163.0, 427.0]]),
        OCRLine('逆流而上', 0.7941, [[152.0, 452.0], [277.0, 452.0], [277.0, 480.0], [152.0, 480.0]]),
        OCRLine('悲泣溪谷', 0.7373, [[1393.0, 748.0], [1523.0, 748.0], [1523.0, 783.0], [1393.0, 783.0]]),
        OCRLine('① 返回', 0.6258, [[1441.0, 843.0], [1521.0, 838.0], [1522.0, 866.0], [1443.0, 871.0]]),
    ],
    697: [
        OCRLine('装备升级', 0.7997, [[772.0, 644.0], [862.0, 644.0], [862.0, 673.0], [772.0, 673.0]]),
        OCRLine('能力修行', 0.7946, [[768.0, 687.0], [862.0, 687.0], [862.0, 715.0], [768.0, 715.0]]),
        OCRLine('保存记忆', 0.6596, [[768.0, 728.0], [862.0, 728.0], [862.0, 756.0], [768.0, 756.0]]),
        OCRLine('离开', 0.6601, [[775.0, 771.0], [817.0, 771.0], [817.0, 797.0], [775.0, 797.0]]),
        OCRLine('溪谷杀气塔一层', 0.8132, [[78.0, 794.0], [308.0, 794.0], [308.0, 832.0], [78.0, 832.0]]),
    ],
    700: [
        OCRLine('龚溪谷杀气塔顶', 0.6983, [[772.0, 270.0], [892.0, 270.0], [892.0, 298.0], [772.0, 298.0]]),
        OCRLine('龚恶弓手营地', 0.7016, [[182.0, 376.0], [292.0, 376.0], [292.0, 409.0], [182.0, 409.0]]),
        OCRLine('溪谷杀气塔一层', 0.8542, [[790.0, 437.0], [992.0, 437.0], [992.0, 465.0], [790.0, 465.0]]),
        OCRLine('悲泣溪谷', 0.7973, [[74.0, 713.0], [207.0, 717.0], [206.0, 756.0], [73.0, 752.0]]),
        OCRLine('地图完整度：52%', 0.8799, [[70.0, 784.0], [242.0, 788.0], [241.0, 816.0], [70.0, 812.0]]),
        OCRLine('口查看收集详情', 0.7405, [[75.0, 839.0], [225.0, 839.0], [225.0, 862.0], [75.0, 862.0]]),
        OCRLine('①移动', 0.6061, [[930.0, 842.0], [1017.0, 842.0], [1017.0, 870.0], [930.0, 870.0]]),
        OCRLine('缩放心重置视图', 0.7725, [[1023.0, 840.0], [1245.0, 840.0], [1245.0, 869.0], [1023.0, 869.0]]),
        OCRLine('?进入全域地图', 0.675, [[1265.0, 842.0], [1420.0, 842.0], [1420.0, 865.0], [1265.0, 865.0]]),
        OCRLine('①返回', 0.6713, [[1440.0, 841.0], [1521.0, 839.0], [1522.0, 868.0], [1441.0, 870.0]]),
    ],
    704: [
    ],
    1075: [
        OCRLine('远跳庞镇之桥', 0.8486, [[77.0, 794.0], [277.0, 794.0], [277.0, 834.0], [77.0, 834.0]]),
    ],
    1076: [
        OCRLine('穿行', 0.6636, [[760.0, 602.0], [822.0, 602.0], [822.0, 632.0], [760.0, 632.0]]),
        OCRLine('装备升级', 0.7964, [[770.0, 642.0], [862.0, 642.0], [862.0, 670.0], [770.0, 670.0]]),
        OCRLine('能力修行', 0.7922, [[770.0, 688.0], [857.0, 688.0], [857.0, 711.0], [770.0, 711.0]]),
        OCRLine('保存记忆', 0.7981, [[772.0, 728.0], [862.0, 728.0], [862.0, 756.0], [772.0, 756.0]]),
        OCRLine('离开', 0.642, [[773.0, 771.0], [815.0, 771.0], [815.0, 794.0], [773.0, 794.0]]),
        OCRLine('远跳庞镇之桥', 0.8348, [[72.0, 791.0], [275.0, 794.0], [274.0, 834.0], [71.0, 830.0]]),
    ],
    1096: [
        OCRLine('穿行', 0.6657, [[775.0, 609.0], [813.0, 609.0], [813.0, 627.0], [775.0, 627.0]]),
        OCRLine('装备升级', 0.7996, [[757.0, 642.0], [878.0, 642.0], [878.0, 675.0], [757.0, 675.0]]),
        OCRLine('能力修行', 0.794, [[770.0, 688.0], [858.0, 688.0], [858.0, 711.0], [770.0, 711.0]]),
        OCRLine('保存记忆', 0.7125, [[770.0, 728.0], [862.0, 728.0], [862.0, 756.0], [770.0, 756.0]]),
        OCRLine('离开', 0.6536, [[775.0, 771.0], [817.0, 771.0], [817.0, 794.0], [775.0, 794.0]]),
        OCRLine('远跳庞镇之桥', 0.8385, [[77.0, 794.0], [277.0, 794.0], [277.0, 834.0], [77.0, 834.0]]),
    ],
}


def line(text, x, y, w=.12, h=.03, score=.9):
    return OCRLine(text, score, [[x*1600,y*900],[(x+w)*1600,y*900],
                                [(x+w)*1600,(y+h)*900],[x*1600,(y+h)*900]])


def menu(name='新地点清河口'):
    return [line('穿行',.48,.67), line('能力修行',.48,.76),
            line('保存记忆',.48,.81), line('离开',.48,.86),
            line(name,.05,.88,.20,.04)]


class NavigationTests(unittest.TestCase):
    def test_five_real_menu_interactions_four_places_without_inventing_activation(self):
        names = {130:'残偶谷底',445:'溪谷杀气塔顶',697:'溪谷杀气塔一层',
                 1076:'远跳庞镇之桥',1096:'远跳庞镇之桥'}
        for timestamp, name in names.items():
            with self.subTest(timestamp=timestamp):
                found=detect_navigation(REAL_FRAMES[timestamp],(900,1600),timestamp)
                self.assertEqual(len(found),1)
                self.assertEqual(found[0]['name'],name)
                self.assertEqual(found[0]['kind'],'waypoint')
                self.assertEqual(found[0]['evidence'],'waypoint_menu')
                self.assertEqual(found[0]['verified'],0)
                self.assertIn('首次激活或完成传送待核实',found[0]['text'])
                self.assertEqual(found[0]['time'],timestamp)

    def test_real_maps_task_map_loading_and_bare_location_do_not_make_waypoints(self):
        for timestamp in (123,127,443,561,700,704,1075):
            with self.subTest(timestamp=timestamp):
                self.assertEqual(detect_navigation(REAL_FRAMES[timestamp],(900,1600),timestamp),[])

    def test_unknown_name_timestamp_and_scaled_resolution(self):
        source=menu()
        scaled=[OCRLine(x.text,x.score,[[a*2,b*2] for a,b in x.box]) for x in source]
        result=detect_navigation(scaled,(1800,3200),92831.4)
        self.assertEqual(result[0]['name'],'新地点清河口')
        self.assertEqual(result[0]['time'],92831.4)

    def test_reopening_a_menu_remains_a_new_candidate(self):
        first=detect_navigation(menu(),(900,1600),5)
        later=detect_navigation(menu(),(900,1600),500)
        self.assertEqual(first[0]['name'],later[0]['name'])
        self.assertNotEqual(first[0]['time'],later[0]['time'])

    def test_missing_highlighted_travel_option_still_recognizes_distinctive_menu(self):
        result=detect_navigation(REAL_FRAMES[130],(900,1600),130)
        self.assertEqual(result[0]['name'],'残偶谷底')

    def test_plain_name_and_save_memory_words_are_insufficient(self):
        self.assertEqual(detect_navigation([menu()[-1]],(900,1600),1),[])
        self.assertEqual(detect_navigation([menu()[2],menu()[-1]],(900,1600),1),[])
        self.assertEqual(detect_navigation([menu()[2]]*4+[menu()[-1]],(900,1600),1),[])

    def test_disconnected_menu_words_and_subtitles_are_not_interactions(self):
        scattered=[line('穿行',.1,.2),line('能力修行',.2,.4),
                   line('保存记忆',.48,.81),line('离开',.8,.86),menu()[-1]]
        self.assertEqual(detect_navigation(scattered,(900,1600),1),[])
        subtitles=[line('穿行能力修行保存记忆离开',.3,.9,.4,.03),menu()[-1]]
        self.assertEqual(detect_navigation(subtitles,(900,1600),1),[])

    def test_map_context_vetoes_even_spurious_menu_ocr(self):
        self.assertEqual(detect_navigation(menu()+[line('地图完整度：50%',.05,.80)],(900,1600),1),[])

    def test_menu_without_location_is_not_assigned_a_distant_map_label(self):
        data=menu()[:-1]+[line('陌生地点',.5,.3)]
        self.assertEqual(detect_navigation(data,(900,1600),1),[])

    def test_explicit_activation_notice_and_adjacent_name(self):
        result=detect_navigation([line('传送点已激活：青岩渡',.65,.25,.30)],(900,1600),4)
        self.assertEqual(result[0]['name'],'青岩渡')
        self.assertEqual(result[0]['evidence'],'waypoint_notice')
        self.assertEqual(result[0]['verified'],0)
        result=detect_navigation([line('残偶已解锁',.6,.3),line('新地点',.6,.35)],(900,1600),4)
        self.assertEqual(result[0]['name'],'新地点')

    def test_action_objectives_negative_status_and_dialogue_are_not_notices(self):
        for text in ('激活传送点：青岩渡','前往青岩渡激活传送点','传送点尚未激活：青岩渡',
                     '解锁条件：传送点已激活','传送点已激活：按住确定'):
            self.assertEqual(detect_navigation([line(text,.5,.3,.45)],(900,1600),4),[])
        self.assertEqual(detect_navigation([line('传送点已激活：青岩渡',.3,.9,.4)],(900,1600),4),[])

    def test_explicit_notice_does_not_take_a_distant_place_as_its_name(self):
        result=detect_navigation([line('传送点已激活',.6,.3),line('无关地点',.1,.6)],(900,1600),4)
        self.assertEqual(result[0]['name'],'传送点激活提示（名称未识别）')

    def test_invalid_geometry_scores_and_timestamp_are_ignored(self):
        for shape,stamp in [((0,1600),1),((900,0),1),((900,),1),((900,1600),math.nan)]:
            self.assertEqual(detect_navigation(menu(),shape,stamp),[])
        self.assertEqual(detect_navigation([OCRLine(x.text,x.score,None) for x in menu()],(900,1600),1),[])
        self.assertEqual(detect_navigation([OCRLine(x.text,math.nan,x.box) for x in menu()],(900,1600),1),[])
        self.assertEqual(detect_navigation([OCRLine(x.text,.4,x.box) for x in menu()],(900,1600),1),[])


if __name__ == '__main__':
    unittest.main()
