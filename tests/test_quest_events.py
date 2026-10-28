"""Actual OCR fixtures and semantic negatives; no video/model dependency."""
import unittest

from app.quest_events import detect_quests
from app.rules import OCRLine

# Original-pixel OCR boxes from demo-audit/quests/ocr.json.
REAL_OCR = {'106': [('+ 7,715 ', 0.7364, [[1734.0, 36.0], [1832.4, 36.0], [1832.4, 69.6], [1734.0, 69.6]]),
         ('查明关于“四凶斧”的来龙去脉', 0.8499, [[1440.0, 324.0], [1782.0, 324.0], [1782.0, 357.6], [1440.0, 357.6]]),
         ('意识尘埃+390', 0.8426, [[1646.4, 712.8], [1868.4, 716.4], [1867.2, 751.2], [1645.2, 746.4]])],
 '553': [('隔绝楔子', 0.7316, [[1437.6, 270.0], [1569.6, 270.0], [1569.6, 309.6], [1437.6, 309.6]]),
         ('帮助友善的鬼差找到浑源石（悲', 0.8474, [[1440.0, 324.0], [1782.0, 324.0], [1782.0, 357.6], [1440.0, 357.6]]),
         ('泣溪谷）', 0.6994, [[1435.2, 356.4], [1538.4, 362.4], [1537.2, 397.2], [1434.0, 392.4]])],
 '563': [('地图', 0.6664, [[500.4, 33.6], [573.6, 33.6], [573.6, 69.6], [500.4, 69.6]]),
         ('读物', 0.649, [[1178.4, 33.6], [1254.0, 33.6], [1254.0, 69.6], [1178.4, 69.6]]),
         ('系统', 0.6663, [[1350.0, 33.6], [1425.6, 33.6], [1425.6, 69.6], [1350.0, 69.6]]),
         ('故事', 0.6402, [[324.0, 218.4], [380.4, 218.4], [380.4, 248.4], [324.0, 248.4]]),
         ('溪谷涤处', 0.7493, [[147.6, 822.0], [284.4, 822.0], [284.4, 864.0], [147.6, 864.0]]),
         ('在钢铁巨塔的顶层寻找丢失的钥匙', 0.9298, [[150.0, 897.6], [513.6, 897.6], [513.6, 928.8], [150.0, 928.8]])],
 '686': [('隔绝楔子', 0.7417, [[1437.6, 270.0], [1569.6, 270.0], [1569.6, 309.6], [1437.6, 309.6]]),
         ('将找到的浑源石交给鬼差（悲泣', 0.8967, [[1440.0, 324.0], [1778.4, 324.0], [1778.4, 357.6], [1440.0, 357.6]]),
         ('溪谷)', 0.6412, [[1436.4, 361.2], [1514.4, 361.2], [1514.4, 394.8], [1436.4, 394.8]])],
 '101': [('书籍', 0.662, [[1281.6, 204.0], [1317.6, 204.0], [1317.6, 228.0], [1281.6, 228.0]]),
         ('“四凶爷”案卷', 0.6818, [[1308.0, 240.0], [1554.0, 240.0], [1554.0, 285.6], [1308.0, 285.6]]),
         ('四凶爷之札記', 0.7142, [[477.6, 256.8], [531.6, 261.6], [502.8, 567.6], [448.8, 562.8]]),
         ('四凶斧之乱·混乱', 0.8483, [[1278.0, 301.2], [1494.0, 301.2], [1494.0, 333.6], [1278.0, 333.6]]),
         ('今个会审斧老二，竟被他道出桩惊天大事！', 0.9237, [[1278.0, 460.8], [1728.0, 460.8], [1728.0, 494.4], [1278.0, 494.4]])],
 '463': [('登高跳望的鬼差', 0.8057, [[854.4, 861.6], [1068.0, 861.6], [1068.0, 901.2], [854.4, 901.2]]),
         ('现今多半虽已残破不堪，不过看这热气腾腾的样子，浑源石应，',
          0.9387,
          [[525.6, 921.6], [1416.0, 921.6], [1416.0, 961.2], [525.6, 961.2]]),
         ('该还在啊o。', 0.6051, [[524.4, 974.4], [711.6, 974.4], [711.6, 1014.0], [524.4, 1014.0]])]}

def real(stamp):
    return [OCRLine(*row) for row in REAL_OCR[str(stamp)]]


def line(text, x=.75, y=.31, w=.18, h=.025, score=.9):
    return OCRLine(text, score, [[x*1920,y*1080],[(x+w)*1920,y*1080],[(x+w)*1920,(y+h)*1080],[x*1920,(y+h)*1080]])


class QuestTests(unittest.TestCase):
    def test_real_banner_without_title_is_searchable_neutral_notice(self):
        result=detect_quests(real(106),(1080,1920),106)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['name'],'查明关于“四凶斧”的来龙去脉')
        self.assertEqual(result[0]['evidence'],'quest_notice')
        self.assertEqual(result[0]['kind'],'quest')
        self.assertEqual(result[0]['verified'],0)
        self.assertIn('未判定接取、更新或完成',result[0]['text'])

    def test_real_wrapped_objectives_remain_different_steps(self):
        first=detect_quests(real(553),(1080,1920),553)[0]
        later=detect_quests(real(686),(1080,1920),686)[0]
        self.assertEqual(first['name'],'帮助友善的鬼差找到浑源石(悲泣溪谷)')
        self.assertEqual(later['name'],'将找到的浑源石交给鬼差(悲泣溪谷)')
        self.assertIn('隔绝楔子',first['text'])
        self.assertNotEqual(first['name'],later['name'])
        self.assertEqual(later['evidence'],'quest_notice')

    def test_real_menu_uses_stable_objective_not_imperfect_title(self):
        result=detect_quests(real(563),(1080,1920),563)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['name'],'在钢铁巨塔的顶层寻找丢失的钥匙')
        self.assertEqual(result[0]['evidence'],'quest_view')
        self.assertIn('溪谷涤处',result[0]['text']) # retain original OCR, not an invented correction

    def test_real_book_and_dialogue_are_not_quests(self):
        self.assertEqual(detect_quests(real(101),(1080,1920),101),[])
        self.assertEqual(detect_quests(real(463),(1080,1920),463),[])

    def test_dialogue_objective_phrase_is_not_a_trigger(self):
        self.assertEqual(detect_quests([line('帮助友善的旅人找到药草',.3,.86,.4)],(1080,1920),99),[])

    def test_book_description_in_notice_position_is_excluded(self):
        lines=[line('书籍',.66,.19,.04),line('查明旧城失踪事件的来龙去脉')]
        self.assertEqual(detect_quests(lines,(1080,1920),1),[])

    def test_explicit_completed_toast_is_a_quest_not_achievement(self):
        result=detect_quests([line('任务完成：山谷来客',.4,.3,.3)],(1080,1920),88)
        self.assertEqual(result[0]['name'],'山谷来客')
        self.assertEqual(result[0]['kind'],'quest')
        self.assertEqual(result[0]['evidence'],'quest_completed')
        self.assertIn('明确任务完成提示',result[0]['text'])
        self.assertEqual(result[0]['verified'],0)

    def test_separate_new_quest_label_and_title(self):
        result=detect_quests([line('新任务',.42,.2),line('旅人的来信',.42,.26)],(1080,1920),1)
        self.assertEqual(result[0]['name'],'旅人的来信')
        self.assertEqual(result[0]['evidence'],'quest_notice')

    def test_completion_hints_negation_and_achievement_are_not_completed_quests(self):
        for text in ['任务未完成','任务完成后领取奖励','任务完成条件：寻找钥匙','成就解锁：山谷来客']:
            self.assertEqual(detect_quests([line(text,.4,.3,.3)],(1080,1920),1),[],text)

    def test_static_menu_completion_label_does_not_emit_completed(self):
        result=detect_quests(real(563)+[line('任务完成：旧日传闻',.4,.3,.3)],(1080,1920),1)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['evidence'],'quest_view')

    def test_duplicate_ocr_passes_emit_one_objective(self):
        result=detect_quests(real(553)+real(553),(1080,1920),1)
        self.assertEqual(len(result),1)

    def test_arbitrary_names_and_scaled_boxes(self):
        lines=[line('查明陌生村落的来龙去脉')]
        half=[OCRLine(row.text,row.score,[[x/2,y/2] for x,y in row.box]) for row in lines]
        self.assertEqual(detect_quests(half,(540,960),321)[0]['name'],'查明陌生村落的来龙去脉')

    def test_missing_boxes_and_invalid_frame_do_not_guess(self):
        self.assertEqual(detect_quests([OCRLine('查明陌生村落的来龙去脉')],(1080,1920),1),[])
        self.assertEqual(detect_quests(real(106),(0,1920),1),[])
        self.assertEqual(detect_quests(real(106),(1080,1920),float('nan')),[])


if __name__=='__main__':
    unittest.main()
