import unittest

from app.boss_text_index import BossTextIndex
from app.rules import OCRLine


def line(text,score=.9,left=.4,right=.6,y=.12):
    return OCRLine(text,score,[[left*1000,(y-.01)*1000],[right*1000,(y-.01)*1000],
                              [right*1000,(y+.01)*1000],[left*1000,(y+.01)*1000]])


def candidate(text,kind='text'):
    return {'kind':kind,'name':text,'text':text,'confidence':.9}


class BossTextIndexTests(unittest.TestCase):
    def call(self,index,stamp,readings,others=()):
        return index.filter([candidate(r.text) for r in readings]+list(others),readings,(1000,1000),stamp)

    def test_waits_for_independent_frames_and_folds_typo(self):
        index=BossTextIndex()
        self.assertEqual(self.call(index,1,[line('剑痴:花干笑')]),[])
        self.assertEqual(self.call(index,2,[line('剑痴:花千笑')]),[])
        result=self.call(index,3,[line('剑痴:花千笑')])
        self.assertEqual([r['name'] for r in result],['剑痴:花千笑'])
        key=result[0]['_identity_key']
        result=self.call(index,4,[line('剑痴:花干笑',.99)])
        self.assertEqual(result[0]['name'],'剑痴:花千笑')
        self.assertEqual(result[0]['_identity_key'],key)

    def test_duplicate_boxes_are_one_vote(self):
        index=BossTextIndex()
        reads=[line('剑痴:花千笑'),line('剑痴:花千笑',.98)]
        self.assertEqual(self.call(index,1,reads),[])
        self.assertEqual(self.call(index,1,reads),[])
        self.assertEqual(self.call(index,2,reads)[0]['name'],'剑痴:花千笑')

    def test_split_title_and_name_reconstructed(self):
        index=BossTextIndex()
        reads=[line('剑痴:',left=.40,right=.47),line('花千笑',left=.49,right=.58)]
        self.assertEqual(self.call(index,1,reads),[])
        result=self.call(index,2,reads)
        self.assertEqual([r['name'] for r in result],['剑痴:花千笑'])

    def test_fragments_fold_into_known_name(self):
        index=BossTextIndex()
        for t in (1,2): self.call(index,t,[line('斧老大:彭猛')])
        result=self.call(index,3,[line('爷老大'),line('彭猛',left=.52,right=.58)])
        self.assertEqual([r['name'] for r in result],['斧老大:彭猛'])

    def test_active_boss_is_only_text_but_typed_candidates_survive(self):
        index=BossTextIndex()
        for t in (1,2): self.call(index,t,[line('剑痴:花千笑')])
        reads=[line('剑痴:花千笑'),line('寻找钥匙',left=.7,right=.9,y=.7)]
        enemy=candidate('剑痴:花千笑','enemy')
        result=self.call(index,3,reads,[enemy])
        self.assertIn(enemy,result)
        self.assertEqual([r['name'] for r in result if r['kind']=='text'],['剑痴:花千笑'])

    def test_menu_without_current_boss_reading_preserves_independent_text(self):
        index=BossTextIndex()
        for t in (1,2): self.call(index,t,[line('剑痴:花千笑')])
        read=line('寻找钥匙',left=.7,right=.9,y=.7)
        self.assertEqual(self.call(index,3,[read]),[candidate('寻找钥匙')])

    def test_pending_full_boss_title_suppresses_parallel_dialogue(self):
        index=BossTextIndex()
        reads=[line('剑痴:花千笑'),line('那柄锈刃会使人流血',left=.3,right=.7,y=.9)]
        self.assertEqual(self.call(index,1,reads),[])

    def test_bottom_title_cannot_establish_a_boss(self):
        index=BossTextIndex();read=line('路人:张三',y=.85)
        for t in (1,2): self.assertEqual(self.call(index,t,[read]),[candidate(read.text)])

    def test_actual_intro_card_boxes_link_to_hud_and_cutscene_speaker(self):
        # Saved full-frame OCR at 1600 x 900 from the supplied recording.
        index=BossTextIndex()
        marker=OCRLine('BOSS',.79,[[670,121],[933,121],[933,207],[670,207]])
        wrong=OCRLine('剑痴：花干笑',.809,[[473,235],[1122,235],[1122,352],[473,352]])
        correct=OCRLine('剑痴：花千笑',.804,[[477,233],[1122,233],[1122,349],[477,349]])
        def run(t,reads):
            return index.filter([candidate(r.text) for r in reads],reads,(900,1600),t)
        self.assertEqual(run(0,[marker,wrong]),[])
        self.assertEqual(run(1,[marker,correct]),[])
        result=run(2,[marker,correct])
        self.assertEqual([r['name'] for r in result],['剑痴:花千笑'])
        key=result[0]['_identity_key']
        speaker=OCRLine('花千笑',.738,[[758,718],[843,718],[843,753],[758,753]])
        dialogue=OCRLine('好酒！哈哈哈哈！',.86,[[515,768],[1070,766],[1070,799],[515,801]])
        result=run(5,[speaker,dialogue])
        self.assertEqual([r['name'] for r in result],['剑痴:花千笑'])
        self.assertEqual(result[0]['_identity_key'],key)
        hud=OCRLine('剑痴：花千笑',.809,[[720,96],[879,96],[879,123],[720,123]])
        self.assertEqual(run(6,[hud])[0]['_identity_key'],key)

    def test_large_title_without_boss_marker_is_not_intro_evidence(self):
        index=BossTextIndex()
        read=OCRLine('路人：张三',.9,[[477,233],[1122,233],[1122,349],[477,349]])
        for t in (1,2):
            self.assertEqual(index.filter([candidate(read.text)],[read],(900,1600),t),[candidate(read.text)])

    def test_hud_near_upper_edge_matches_existing_tracker_bounds(self):
        index=BossTextIndex();read=line('剑痴:花千笑',y=.035)
        self.assertEqual(self.call(index,1,[read]),[])
        self.assertEqual(self.call(index,2,[read])[0]['name'],'剑痴:花千笑')

    def test_restore_middle_of_dialogue_keeps_full_name_only(self):
        index=BossTextIndex()
        self.assertTrue(index.restore_context('剑痴：花千笑',.85,844))
        speaker=OCRLine('花千笑',.738,[[758,718],[843,718],[843,753],[758,753]])
        dialogue=OCRLine('好酒！哈哈哈哈！',.86,[[515,768],[1070,766],[1070,799],[515,801]])
        result=index.filter([candidate(speaker.text),candidate(dialogue.text)],
                            [speaker,dialogue],(900,1600),845)
        self.assertEqual([r['name'] for r in result],['剑痴:花千笑'])
        self.assertEqual(result[0]['evidence'],'boss_name')
        self.assertEqual(result[0]['_identity_key'],'boss:剑痴花千笑')

    def test_restored_context_expires_or_rejects_future_timestamp(self):
        speaker=line('花千笑',left=.46,right=.54,y=.82)
        for stored,actual in [(800,845),(846,845)]:
            with self.subTest(stored=stored,actual=actual):
                index=BossTextIndex()
                self.assertTrue(index.restore_context('剑痴:花千笑',.85,stored))
                self.assertEqual(self.call(index,actual,[speaker]),[candidate('花千笑')])

    def test_restore_rejects_weak_titles_scores_and_invalid_timestamps(self):
        for name,score,stamp in [('花千笑',.9,10),('任务目标:寻找钥匙',.9,10),
                                 ('剑痴:花千笑',.59,10),('剑痴:花千笑',1.1,10),
                                 ('剑痴:花千笑',float('nan'),10),
                                 ('剑痴:花千笑',.9,-1),('剑痴:花千笑',.9,float('inf')),
                                 ('剑痴:花千笑',.9,'invalid')]:
            with self.subTest(name=name,score=score,stamp=stamp):
                index=BossTextIndex()
                self.assertFalse(index.restore_context(name,score,stamp))
                self.assertIsNone(index._name)

    def test_unrelated_new_boss_gets_separate_key(self):
        index=BossTextIndex()
        for t in (1,2): old=self.call(index,t,[line('狂徒:残钢')])
        self.assertEqual(self.call(index,3,[line('剑痴:花千笑')]),[])
        new=self.call(index,4,[line('剑痴:花千笑')])
        self.assertEqual(new[0]['name'],'剑痴:花千笑')
        self.assertNotEqual(old[0]['_identity_key'],new[0]['_identity_key'])

    def test_shared_title_with_different_person_is_new_encounter(self):
        index=BossTextIndex()
        for t in (1,2): old=self.call(index,t,[line('守门人:张三')])
        self.assertEqual(self.call(index,3,[line('守门人:李四')]),[])
        new=self.call(index,4,[line('守门人:李四')])
        self.assertEqual(new[0]['name'],'守门人:李四')
        self.assertNotEqual(old[0]['_identity_key'],new[0]['_identity_key'])

    def test_absence_expires_context_without_fabricating_observations(self):
        index=BossTextIndex()
        for t in (1,2): self.call(index,t,[line('剑痴:花千笑')])
        self.assertEqual(self.call(index,10,[]),[])
        result=self.call(index,28,[line('花千笑')])
        self.assertEqual(result,[candidate('花千笑')])

    def test_reverse_time_resets_votes(self):
        index=BossTextIndex()
        for t in (10,11): self.call(index,t,[line('剑痴:花千笑')])
        self.assertEqual(self.call(index,1,[line('剑痴:花千笑')]),[])

    def test_unrelated_speech_cannot_extend_active_encounter(self):
        index=BossTextIndex()
        for t in (1,2): self.call(index,t,[line('剑痴:花千笑')])
        other=line('任务目标',y=.12)
        self.assertEqual(self.call(index,3,[other]),[candidate('任务目标')])


if __name__=='__main__': unittest.main()
