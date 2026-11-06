import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.game_schemes import builtin_profiles, validate_profile, detect_profile_candidates, catalog_events
from app.rules import OCRLine
from app.analyzer import EventAccumulator, OCREvidenceCache, _validate_options, analyze


def line(text, x=600, y=150, width=250, score=.95):
    return OCRLine(text, score, [[x, y], [x+width, y], [x+width, y+25], [x, y+25]])


def row(number, name='青铜钥匙', stamp=1, end=2, kind='item', video='v1', **extra):
    return {'id': str(number), 'video_id': video, 'kind': kind, 'name': name, 'time': stamp,
            'end_time': end, 'source': 'ocr', 'verified': 0, 'edited': 0,
            'evidence': 'profile:obtained:obtain', 'text': '获得：' + name,
            'screenshot': 'sample.jpg', **extra}


class ProfileValidationTests(unittest.TestCase):
    def test_builtins_are_independent_and_valid(self):
        a = builtin_profiles()
        for profile in a:
            self.assertEqual(validate_profile(profile), profile)
        a[0]['categories'].clear()
        self.assertTrue(builtin_profiles()[0]['categories'])

    def test_pbz_uses_existing_category_groups(self):
        categories = {c['id']: c for c in builtin_profiles()[0]['categories']}
        self.assertEqual(categories['task_item']['group'], '任务')
        self.assertEqual(categories['story_item']['group'], '')
        self.assertEqual(categories['boss']['group'], '')

    def test_invalid_config_rejected(self):
        base = builtin_profiles()[1]
        bad_values = [{'adapter': 'remote'}, {'version': True}, {'language': 'unknown'},
                      {'id': '../file'}, {'categories': []}, {'regions': [{'x': .9, 'y': 0, 'w': .4, 'h': 1}]},
                      {'aliases': []}, {'exclude_text': '背包'},
                      {'rules': [{'id': 'a', 'category': 'missing', 'trigger': '获得'}]}]
        for values in bad_values:
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_profile({**base, **values})

    def test_profile_controls_adapter_and_disables_free_text(self):
        result = _validate_options({'game_profile': 'phantom_blade_zero_demo',
                                    'recognition_scope': 'collection_only', '_profile': builtin_profiles()[1]})
        self.assertEqual(result['game_profile'], 'generic')
        self.assertEqual(result['recognition_scope'], 'legacy')
        self.assertEqual(result['text_mode'], 'off')
        self.assertFalse(result['include_text'])

    def test_categories_can_be_ungrouped(self):
        profile = builtin_profiles()[1]
        profile['categories'][0]['group'] = ''
        self.assertEqual(validate_profile(profile)['categories'][0]['group'], '')


class ProfileDetectionTests(unittest.TestCase):
    def setUp(self):
        self.profile = builtin_profiles()[1]

    def detect(self, *lines):
        return detect_profile_candidates(lines, (720, 1280, 3), 10, self.profile)

    def test_default_notification_and_longest_trigger(self):
        found = self.detect(line('获得物品：青铜钥匙 × 1'))
        self.assertEqual([(r['kind'], r['name']) for r in found], [('item', '青铜钥匙')])
        self.assertEqual(found[0]['semantic'], 'obtained')

    def test_title_and_neighbor_name(self):
        found = self.detect(line('成就已解锁'), line('初露锋芒', y=190))
        self.assertEqual(found[0]['name'], '初露锋芒')
        self.assertEqual(found[0]['kind'], 'achievement')

    def test_custom_category_spatial_name_region(self):
        self.profile['categories'].append({'id': 'relic', 'label': '遗物', 'group': '地图', 'enabled': True, 'policy': 'first'})
        self.profile['rules'] = [{'id': 'relic', 'category': 'relic', 'trigger': '遗物', 'semantic': 'obtained',
            'region': {'x': .4, 'y': .1, 'w': .3, 'h': .2},
            'name_region': {'x': .4, 'y': .25, 'w': .5, 'h': .1}, 'exclude': []}]
        found = self.detect(line('遗物'), line('失落王冠', y=195), line('无关说明', y=400, score=.99))
        self.assertEqual([(r['kind'], r['name']) for r in found], [('relic', '失落王冠')])
        self.assertFalse(self.detect(line('遗物', x=100), line('失落王冠', y=195)))

    def test_menu_negatives_and_plain_text_never_indexed(self):
        for marker in ('背包', '商店', '强化', 'Inventory'):
            self.assertEqual(self.detect(line(marker, x=50), line('获得：青铜钥匙')), [])
        self.assertEqual(self.detect(line('青铜钥匙'), line('很长的剧情对白内容')), [])
        self.assertEqual(self.detect(line('获得方式：击败首领')), [])
        self.assertEqual(self.detect(line('尚未获得：青铜钥匙')), [])

    def test_menu_word_in_item_name_does_not_exclude(self):
        self.assertEqual(self.detect(line('获得：商店钥匙'))[0]['name'], '商店钥匙')

    def test_english_word_boundary(self):
        self.assertEqual(self.detect(line('Obtained: Bronze Key'))[0]['name'], 'Bronze Key')
        self.assertEqual(self.detect(line('Obtainedness Bronze Key')), [])

    def test_disabled_and_exact_alias(self):
        self.profile['aliases'] = {'青铜锁匙': '青铜钥匙'}
        self.assertEqual(self.detect(line('获得：青铜锁匙'))[0]['name'], '青铜钥匙')
        self.profile['categories'][0]['enabled'] = False
        self.assertEqual(self.detect(line('获得：青铜钥匙')), [])

    def test_fails_closed_if_name_region_empty(self):
        self.profile['rules'][0]['name_region'] = {'x': .01, 'y': .01, 'w': .1, 'h': .1}
        self.profile['rules'] = self.profile['rules'][:1]
        self.assertEqual(self.detect(line('获得物品：青铜钥匙')), [])


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.profile = builtin_profiles()[1]
        self.videos = [{'id': 'v1', 'import_order': 1, 'project_id': 'one'},
                       {'id': 'v2', 'import_order': 2, 'project_id': 'one'}]

    def catalog(self, records, policy='first'):
        self.profile['categories'][0]['policy'] = policy
        return catalog_events(records, self.videos, self.profile)

    def test_first_uses_import_order_before_time(self):
        output = self.catalog([row('b', stamp=1, video='v2'), row('a', stamp=300, end=301)])
        self.assertEqual([r['id'] for r in output], ['a'])

    def test_projects_and_semantics_never_collide(self):
        self.videos[1]['project_id'] = 'two'
        output = self.catalog([row('a'), row('b', video='v2'), row('c', evidence='profile:seen:display')])
        self.assertEqual(len(output), 3)

    def test_occurrence_merges_touching_evidence_not_later_acquisitions(self):
        output = self.catalog([row('a', stamp=1, end=2), row('b', stamp=2, end=3),
                               row('c', stamp=5, end=6), row('d', stamp=6, end=7, video='v2')], 'occurrence')
        self.assertEqual([r['id'] for r in output], ['a', 'c', 'd'])
        self.assertEqual(output[0]['end_time'], 3)

    def test_point_observations_are_not_continuity_proof(self):
        self.assertEqual(len(self.catalog([row('a', stamp=1, end=1), row('b', stamp=1.1, end=1.1)], 'occurrence')), 2)

    def test_all_keeps_raw_candidates(self):
        self.assertEqual(len(self.catalog([row('a'), row('b', stamp=2, end=3)], 'all')), 2)

    def test_manual_corrected_and_unknown_preserved(self):
        rows = [row('a'), row('b', source='manual'), row('c', edited=1), row('d', verified=1),
                row('e', name='名称未识别'), row('f', name='名称未识别')]
        self.assertEqual(len(self.catalog(rows)), 6)

    def test_names_are_not_fuzzy_merged(self):
        self.assertEqual(len(self.catalog([row('a', name='青铜钥匙'), row('b', name='青钢钥匙')])), 2)

    def test_books_keep_chapter_identity_and_reopening(self):
        records = [row('a', kind='book', name='旧日笔记 · 第一章', stamp=1, end=2),
                   row('b', kind='book', name='旧日笔记 · 第一章', stamp=2, end=3),
                   row('c', kind='book', name='旧日笔记 · 第二章', stamp=3, end=4),
                   row('d', kind='book', name='旧日笔记 · 第一章', stamp=8, end=9)]
        self.assertEqual([r['id'] for r in self.catalog(records)], ['a', 'c', 'd'])

    def test_pbz_defaults_preserve_fragments_and_reject_menu(self):
        self.profile = builtin_profiles()[0]
        records = [row('a', kind='blackstone', name='黑石碎片', evidence='collection_panel'),
                   row('b', kind='blackstone', name='黑石碎片', stamp=7, end=8, evidence='collection_panel'),
                   row('c', kind='weapon', name='黑伤', evidence='weapon_menu')]
        output = catalog_events(records, self.videos, self.profile)
        self.assertEqual([r['id'] for r in output], ['a', 'b'])

    def test_pbz_label_edit_keeps_approved_first_representative(self):
        from app.event_catalog import group_events, library_events
        profile = builtin_profiles()[0]
        profile['version'] = 2
        profile['categories'][0]['label'] = '铃铛位置'
        profile['categories'][0]['group'] = ''
        records = [row('a', kind='bell', name='残偶谷底', stamp=1, end=2, edited=1, evidence='map_bell_selection'),
                   row('b', kind='bell', name='残偶谷底', stamp=4, end=5, evidence='waypoint_menu'),
                   row('c', kind='boss', name='剑痴:花千笑', stamp=8, end=9, evidence='boss_name'),
                   row('d', kind='boss', name='剑痴:花千笑', stamp=15, end=16, evidence='victory_template')]
        expected = library_events(group_events(records, collection_only=True), self.videos)
        actual = catalog_events(records, self.videos, profile)
        self.assertEqual([r['id'] for r in actual], [r['id'] for r in expected])

    def test_pbz_can_add_a_custom_category_without_losing_it(self):
        profile = builtin_profiles()[0]
        profile['categories'].append({'id': 'relic', 'label': '遗物', 'group': '', 'enabled': True, 'policy': 'first'})
        profile['rules'] = [{'id': 'relic', 'category': 'relic', 'trigger': '遗物', 'semantic': 'obtained',
                             'region': None, 'name_region': None, 'exclude': []}]
        output = catalog_events([row('a', kind='relic', name='青铜王冠')], self.videos, profile)
        self.assertEqual(output[0]['kind'], 'relic')


class ProfileAccumulatorTests(unittest.TestCase):
    def test_observed_absence_closes_generic_prompt(self):
        events = []
        accumulator = EventAccumulator('video', 1, events.append)
        candidate = detect_profile_candidates([line('获得：青铜钥匙')], (720, 1280, 3), 0, builtin_profiles()[1])[0]
        accumulator.add([candidate], 0, lambda: 'a.jpg')
        accumulator.add([], .5, lambda: 'b.jpg')
        accumulator.add([candidate], 1, lambda: 'c.jpg')
        accumulator.flush()
        self.assertEqual(len({e['id'] for e in events}), 2)

    def test_appearance_and_acquisition_have_distinct_ids(self):
        events = []
        acc = EventAccumulator('video', 1, events.append)
        p = builtin_profiles()[1]
        seen = detect_profile_candidates([line('获得：青铜钥匙')], (720, 1280, 3), 0, p)[0]
        acc.add([seen, {**seen, 'semantic': 'seen', '_identity_key': '青铜钥匙|seen'}], 0, lambda: 'a.jpg')
        self.assertEqual(len({e['id'] for e in events}), 2)


class CacheAndPipelineTests(unittest.TestCase):
    def test_concurrent_connections_do_not_hold_scan_length_transactions(self):
        import numpy as np
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'video.mkv'
            source.write_bytes(b'test')
            location = Path(directory) / 'shared.sqlite3'
            first = OCREvidenceCache(location, source, 1600)
            first.close()
            def worker(offset):
                cache = OCREvidenceCache(location, source, 1600)
                frame = np.zeros((90, 160, 3), dtype=np.uint8)
                try:
                    for index in range(20):
                        lines = cache.read(frame, [], None, .6, offset + index)
                        self.assertEqual(lines[0].text, '获得：青铜钥匙')
                        self.assertFalse(cache.db.in_transaction)
                finally:
                    cache.close()
            with patch('app.analyzer._ocr_lines', return_value=[line('获得：青铜钥匙')]), \
                 ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(worker, [0, 100]))

    def test_shared_cache_writes_are_immediately_visible_without_locks(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'video.mkv'
            source.write_bytes(b'test')
            location = Path(directory) / 'shared.sqlite3'
            first = OCREvidenceCache(location, source, 1600)
            second = OCREvidenceCache(location, source, 1600)
            frame = np.zeros((90, 160, 3), dtype=np.uint8)
            with patch('app.analyzer._ocr_lines', return_value=[line('获得：青铜钥匙')]) as engine:
                first.read(frame, [], None, .6, 1)
                # First stays open as it would during a multi-hour background scan.
                second.read(frame, [], None, .6, 1)
                second.read(frame, [], None, .6, 2)
                first.read(frame, [], None, .6, 2)
                self.assertEqual(engine.call_count, 2)
                self.assertEqual(first.hits, 1)
                self.assertEqual(second.hits, 1)
                self.assertFalse(first.db.in_transaction)
                self.assertFalse(second.db.in_transaction)
            first.close()
            second.close()

    def test_cache_busy_is_optional_and_does_not_abort_ocr(self):
        import numpy as np
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'video.mkv'
            source.write_bytes(b'test')
            cache = OCREvidenceCache(Path(directory) / 'cache.sqlite3', source, 1600)
            frame = np.zeros((90, 160, 3), dtype=np.uint8)
            with patch.object(cache, 'db') as db, patch('app.analyzer._ocr_lines', return_value=[line('获得：青铜钥匙')]):
                db.execute.side_effect = sqlite3.OperationalError('database is locked')
                lines = cache.read(frame, [], None, .6, 1)
                self.assertEqual(lines[0].text, '获得：青铜钥匙')
            cache.close()

    def test_cache_keys_include_source_and_region(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'video.mkv'
            source.write_bytes(b'test')
            cache = OCREvidenceCache(Path(directory) / 'cache.sqlite3', source, 1600)
            frame = np.zeros((90, 160, 3), dtype=np.uint8)
            with patch('app.analyzer._ocr_lines', return_value=[line('获得：青铜钥匙')]) as engine:
                cache.read(frame, [], None, .6, 1)
                cache.read(frame, [], None, .6, 1)
                cache.read(frame, [{'x': 0, 'y': 0, 'w': .5, 'h': 1}], None, .6, 1)
                self.assertEqual(engine.call_count, 2)
                self.assertEqual(cache.hits, 1)
            cache.close()

    def test_trial_range_and_frozen_profile_reuse_cached_ocr(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'video.mkv'
            source.write_bytes(b'test')
            frames = [(float(i), np.full((90, 160, 3), i, dtype=np.uint8)) for i in range(6)]
            profile = builtin_profiles()[1]
            options = {'_profile': profile, 'resume_from': 1, 'analysis_end': 4,
                       'ocr_cache_path': str(Path(directory) / 'shared.sqlite3')}
            events = []
            with patch('app.analyzer.probe_video', return_value={'width':160, 'height':90, 'duration':6}), \
                 patch('app.analyzer._sampled_frames', return_value=iter(frames)), \
                 patch('app.analyzer._engine', return_value=object()), \
                 patch('app.analyzer._ocr_lines', return_value=[line('获得：青铜钥匙')]) as ocr:
                result = analyze(str(source), Path(directory)/'trial', options, events.append, lambda p: None, lambda: False)
                self.assertEqual(ocr.call_count, 3)
            self.assertEqual(result['checkpoint'], 4)
            self.assertTrue(all(1 <= e['time'] < 4 and e['end_time'] <= 4 for e in events))
            self.assertEqual(result['profile_id'], 'generic')
            changed = copy.deepcopy(profile)
            changed['version'] = 2
            changed['aliases'] = {'青铜钥匙': '青铜门钥匙'}
            with patch('app.analyzer.probe_video', return_value={'width':160, 'height':90, 'duration':6}), \
                 patch('app.analyzer._sampled_frames', return_value=iter(frames)), \
                 patch('app.analyzer._engine', return_value=object()), \
                 patch('app.analyzer._ocr_lines', side_effect=AssertionError('cached OCR should be reused')):
                events = []
                result = analyze(str(source), Path(directory)/'second', {**options, '_profile':changed},
                                 events.append, lambda p:None, lambda:False)
            self.assertEqual(result['ocr_cache']['hits'], 3)
            self.assertEqual(events[0]['name'], '青铜门钥匙')
            self.assertEqual(events[0]['profile_version'], 2)


if __name__ == '__main__':
    unittest.main()
