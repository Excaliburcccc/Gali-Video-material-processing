from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from app.analyzer import analyze, EventAccumulator
from app.game_profile import BossNameTracker
from app.rules import OCRLine


ROOT=Path(__file__).resolve().parents[1]


def boss_line(name,score=.95):
    return OCRLine(name,score,[[125,18],[195,18],[195,27],[125,27]])


class BossNameTrackerTests(unittest.TestCase):
    def test_new_boss_replaces_long_history_after_two_readings(self):
        tracker=BossNameTracker()
        for stamp in range(20): tracker.observe([boss_line('旧敌：旧首领')],(180,320,3),stamp)
        tracker.observe([boss_line('新敌：新首领')],(180,320,3),20)
        self.assertEqual(tracker.current(20)[0],'旧敌:旧首领')
        tracker.observe([boss_line('新敌：新首领')],(180,320,3),21)
        self.assertEqual(tracker.current(21.5)[0],'新敌:新首领')

    def test_one_wrong_character_does_not_replace_stable_name(self):
        tracker=BossNameTracker()
        for stamp in (1,2,3): tracker.observe([boss_line('剑痴：花千笑')],(180,320,3),stamp)
        tracker.observe([boss_line('剑痴：花干笑',.99)],(180,320,3),4)
        self.assertEqual(tracker.current(4.5)[0],'剑痴:花千笑')

    def test_duplicate_boxes_do_not_supply_two_name_votes(self):
        tracker=BossNameTracker()
        for stamp in (1,2): tracker.observe([boss_line('剑痴：花千笑')],(180,320,3),stamp)
        tracker.observe([boss_line('剑痴：花干笑'),boss_line('剑痴：花干笑')],(180,320,3),3)
        self.assertEqual(tracker.current(3.5)[0],'剑痴:花千笑')

    def test_names_expire_after_twenty_five_seconds(self):
        tracker=BossNameTracker()
        tracker.observe([boss_line('剑痴：花千笑')],(180,320,3),10)
        self.assertIsNotNone(tracker.current(35))
        self.assertIsNone(tracker.current(35.01))
        self.assertIsNone(tracker.current(9))


class ProfileIntegrationTests(unittest.TestCase):
    def run_profile(self,source,output,interval,resume):
        events={}; progress=[]; main_ocr_times=[]; decode_options=[]

        def frames(_path,_info,options,_cancelled):
            decode_options.append(dict(options))
            step=options['sample_interval']
            start=options['resume_from']
            for index in range(1+int(5/step)):
                stamp=round(index*step,8)
                if stamp+1e-7<start: continue
                frame=np.zeros((180,320,3),np.uint8)
                frame[0,0,0]=round(stamp*10)
                yield stamp,frame

        def ocr(frame,regions,_engine,_confidence):
            stamp=int(frame[0,0,0])/10
            if regions:
                return [boss_line('剑痴：花千笑')] if stamp<=1 else []
            main_ocr_times.append(stamp)
            return [OCRLine('获得：测试玉佩',.98)] if stamp==2 else []

        def victory(frame):
            return {'confidence':.88} if int(frame[0,0,0])==30 else None

        with (patch('app.analyzer.probe_video',return_value={'duration':5.,'width':320,'height':180}),
              patch('app.analyzer._engine',return_value=object()),
              patch('app.analyzer._sampled_frames',side_effect=frames),
              patch('app.analyzer._ocr_lines',side_effect=ocr),
              patch('app.game_panels.detect_panels',return_value=[]),
              patch('app.victory_detector.VictoryDetector') as detector):
            detector.return_value.detect.side_effect=victory
            analyze(str(source),output,{'game_profile':'phantom_blade_zero_demo','sample_interval':interval,
                                      'resume_from':resume,'include_text':False},
                    lambda e:events.update({e['id']:dict(e)}),progress.append,lambda:False)
        return events,progress,main_ocr_times,decode_options[0]

    def test_half_second_resume_preserves_ocr_grid_and_warms_boss_name_only(self):
        audit=ROOT/'data/demo-audit/second-half'
        audit.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=audit) as temporary:
            path=Path(temporary); source=path/'fixture.mp4'; source.write_bytes(b'')
            full,_,full_ocr,_=self.run_profile(source,path/'full',1,0)
            resumed,progress,resumed_ocr,options=self.run_profile(source,path/'resumed',1,2.5)
            self.assertEqual(resumed_ocr,[t for t in full_ocr if t>=2])
            self.assertEqual(set(resumed),set(full))
            self.assertTrue(all(e['time']>=2 for e in resumed.values()))
            self.assertTrue(all(p['time']>=2.5 for p in progress))
            enemy=next(e for e in resumed.values() if e['kind']=='enemy')
            self.assertEqual(enemy['name'],'剑痴:花千笑')
            self.assertEqual(enemy['time'],3.)
            self.assertEqual(options['sample_interval'],.5)

    def test_point_seven_interval_uses_a_shared_grid_on_resume(self):
        audit=ROOT/'data/demo-audit/second-half'
        audit.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=audit) as temporary:
            path=Path(temporary); source=path/'fixture.mp4'; source.write_bytes(b'')
            _,_,full_ocr,_=self.run_profile(source,path/'full',.7,0)
            _,_,resumed_ocr,options=self.run_profile(source,path/'resumed',.7,2.5)
            self.assertEqual(resumed_ocr,[t for t in full_ocr if t>=2.1])
            self.assertAlmostEqual(options['sample_interval'],.35)
            self.assertEqual(full_ocr,[0,.7,1.4,2.1,2.8,3.5,4.2,4.9])


class AccumulatorEvidenceTests(unittest.TestCase):
    def test_later_stronger_evidence_survives_flush_without_moving_start(self):
        events=[]
        accumulator=EventAccumulator('source',1,events.append)
        initial={'kind':'enemy','name':'首领甲','confidence':.8,'text':'提示'}
        richer={**initial,'confidence':.95,'detection_score':.88,'evidence':'victory_template',
                'text':'斩字匹配；首领名称待核实','_interval':.5}
        accumulator.add([initial],10,lambda:'first.jpg')
        accumulator.add([richer],10,lambda:'second.jpg')
        accumulator.flush()
        self.assertEqual(len({event['id'] for event in events}),1)
        final=events[-1]
        self.assertEqual(final['time'],10)
        self.assertEqual(final['end_time'],11)
        self.assertEqual(final['screenshot'],'first.jpg')
        self.assertEqual(final['confidence'],.95)
        self.assertEqual(final['detection_score'],.88)
        self.assertEqual(final['evidence'],'victory_template')
        self.assertEqual(final['text'],richer['text'])
        self.assertNotIn('_interval',final)


if __name__=='__main__': unittest.main()
