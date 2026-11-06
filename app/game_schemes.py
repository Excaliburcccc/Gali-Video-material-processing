"""Versioned, local game adapters and conservative user-configurable rules.

This module never reads or changes a library. Callers supply one project's rows
and an immutable profile snapshot, so changing a setting cannot mutate old jobs.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

from .rules import OCRLine, clean_text, identity, _nearby, _MENU_CONTEXT, _NOISE

POLICIES = frozenset({'first', 'occurrence', 'all'})
SEMANTICS = frozenset({'obtained', 'defeated', 'completed', 'seen'})
ADAPTERS = frozenset({'generic', 'phantom_blade_zero_demo'})
_ID = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]{0,63}$')
_UNKNOWN = re.compile(r'名称(?:未识别|待核实|未知)|未命名|unknown|unidentified', re.I)
_QUANTITY = re.compile(r'\s*(?:[xX×*]\s*\d+|数量\s*[:：]?\s*\d+)\s*$')
_PUNCT = ' :：!！-—【】[]「」『』"\' '


def builtin_profiles():
    from .collection_taxonomy import LABELS
    categories = []
    for key, label in LABELS.items():
        group = ('任务' if key.startswith('task_') else
                 '读物' if key in ('book', 'reading_memory') else
                 '' if key in ('boss', 'story_item') else '地图收集')
        categories.append({'id': key, 'label': label, 'group': group, 'enabled': True,
                           'policy': 'occurrence' if key in {'amber', 'blackstone', 'book'} else 'first'})
    pbz = {'id': 'phantom_blade_zero_demo', 'name': '影之刃零',
           'adapter': 'phantom_blade_zero_demo', 'version': 1, 'language': 'zh',
           'categories': categories, 'rules': [], 'exclude_text': [], 'regions': [], 'aliases': {}}
    generic = {'id': 'generic', 'name': '通用文字提示', 'adapter': 'generic', 'version': 1, 'language': 'zh',
               'categories': [{'id': key, 'label': label, 'group': group, 'enabled': True, 'policy': policy}
                              for key, label, group, policy in (
                                  ('item', '物品', '收集', 'occurrence'), ('weapon', '武器', '收集', 'first'),
                                  ('accessory', '饰品', '收集', 'first'), ('book', '书籍', '收集', 'occurrence'),
                                  ('quest', '任务', '进度', 'first'), ('boss', 'BOSS', '进度', 'first'),
                                  ('achievement', '成就', '进度', 'first'))],
               'rules': [{'id': key, 'category': category, 'trigger': trigger, 'semantic': semantic,
                          'region': None, 'name_region': None, 'exclude': []}
                         for key, category, trigger, semantic in (
                             ('obtain_item', 'item', '获得物品', 'obtained'),
                             ('obtained', 'item', '已获得', 'obtained'),
                             ('obtain', 'item', '获得', 'obtained'),
                             ('obtain_en', 'item', 'Obtained', 'obtained'),
                             ('defeat', 'boss', '已击败', 'defeated'),
                             ('defeat_en', 'boss', 'BOSS DEFEATED', 'defeated'),
                             ('quest_done', 'quest', '任务完成', 'completed'),
                             ('achievement', 'achievement', '成就已解锁', 'completed'))],
               'exclude_text': ['背包', '物品栏', '商店', '强化', '装备详情', 'Inventory', 'Shop', 'Upgrade'],
               'regions': [], 'aliases': {}}
    return [deepcopy(pbz), deepcopy(generic)]


def _text(value, label, limit=100, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError(f'{label}需要是1至{limit}个字的文字')
    return clean_text(value)


def _rect(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('识别区域格式不正确')
    try:
        x, y, w, h = (float(value[k]) for k in ('x', 'y', 'w', 'h'))
    except (KeyError, TypeError, ValueError):
        raise ValueError('识别区域需要 x、y、w、h') from None
    if not all(math.isfinite(n) for n in (x, y, w, h)) or not (
            0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1 and
            x + w <= 1.000001 and y + h <= 1.000001):
        raise ValueError('识别区域必须位于画面内')
    return dict(x=x, y=y, w=w, h=h)


def _words(value, label):
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError(label + '需要是最多100项的文字列表')
    return list(dict.fromkeys(_text(word, label) for word in value))


def validate_profile(profile):
    if not isinstance(profile, dict):
        raise ValueError('游戏方案格式不正确')
    value = deepcopy(profile)
    scheme_id = _text(value.get('id', ''), '方案标识', 64)
    if not _ID.fullmatch(scheme_id):
        raise ValueError('方案标识只能使用字母、数字、下划线和短横线，且须以字母开头')
    adapter = value.get('adapter', 'generic')
    if adapter not in ADAPTERS:
        raise ValueError('未知的识别方式')
    version = value.get('version', 1)
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= 1000000:
        raise ValueError('方案版本必须是正整数')
    language = value.get('language', 'zh')
    if language not in {'zh', 'en'}:
        raise ValueError('当前本地文字模型支持中文和英文方案')
    raw_categories = value.get('categories', [])
    if not isinstance(raw_categories, list) or not 1 <= len(raw_categories) <= 64:
        raise ValueError('每个方案需要1至64个分类')
    categories, known = [], set()
    for row in raw_categories:
        if not isinstance(row, dict):
            raise ValueError('分类格式不正确')
        key = _text(row.get('id', ''), '分类标识', 64)
        if not _ID.fullmatch(key) or key in known:
            raise ValueError('分类标识必须有效且不重复')
        known.add(key)
        policy = row.get('policy', 'first')
        if policy not in POLICIES:
            raise ValueError('未知的去重方式')
        enabled = row.get('enabled', True)
        if not isinstance(enabled, bool):
            raise ValueError('分类启用状态必须是布尔值')
        categories.append({'id': key, 'label': _text(row.get('label', key), '分类名称', 60),
                           'group': _text(row.get('group', '收集'), '分组名称', 60, empty=True),
                           'enabled': enabled, 'policy': policy})
    raw_rules = value.get('rules', [])
    if not isinstance(raw_rules, list) or len(raw_rules) > 128:
        raise ValueError('每个方案最多128条识别规则')
    rules, rule_ids = [], set()
    for index, row in enumerate(raw_rules):
        if not isinstance(row, dict):
            raise ValueError('识别规则格式不正确')
        rule_id = _text(row.get('id', f'rule_{index + 1}'), '规则标识', 64)
        if not _ID.fullmatch(rule_id) or rule_id in rule_ids:
            raise ValueError('规则标识必须有效且不重复')
        rule_ids.add(rule_id)
        category = row.get('category')
        if category not in known:
            raise ValueError('识别规则引用了不存在的分类')
        semantic = row.get('semantic', 'obtained')
        if semantic not in SEMANTICS:
            raise ValueError('未知的事件类型')
        rules.append({'id': rule_id, 'category': category,
                      'trigger': _text(row.get('trigger', ''), '提示文字'), 'semantic': semantic,
                      'region': _rect(row.get('region')), 'name_region': _rect(row.get('name_region')),
                      'exclude': _words(row.get('exclude', []), '排除文字')})
    regions = value.get('regions', [])
    if not isinstance(regions, list) or len(regions) > 8 or any(r is None for r in regions):
        raise ValueError('全局识别区域最多8个')
    aliases = value.get('aliases', {})
    if not isinstance(aliases, dict) or len(aliases) > 2000:
        raise ValueError('名称纠错表最多2000项')
    aliases = {_text(key, '原名称', 200): _text(name, '正确名称', 200) for key, name in aliases.items()}
    return {'id': scheme_id, 'name': _text(value.get('name', ''), '游戏方案名称', 100),
            'adapter': adapter, 'version': version, 'language': language, 'categories': categories,
            'rules': rules, 'regions': [_rect(region) for region in regions],
            'exclude_text': _words(value.get('exclude_text', []), '排除界面文字'), 'aliases': aliases}


def _inside(line, rect, shape):
    if not rect:
        return True
    if not line.box:
        return False
    height, width = shape[:2]
    x = sum(float(point[0]) for point in line.box) / len(line.box) / width
    y = sum(float(point[1]) for point in line.box) / len(line.box) / height
    return rect['x'] <= x <= rect['x'] + rect['w'] and rect['y'] <= y <= rect['y'] + rect['h']


def _menu_marker(text, marker):
    # Match a screen label, not a collectible named e.g. “商店钥匙”.
    value, needle = clean_text(text).casefold(), clean_text(marker).casefold()
    return value == needle or value.startswith(needle + ':') or value.startswith(needle + '：')


def _clean_name(text):
    return _QUANTITY.sub('', clean_text(text)).strip(_PUNCT)


def _valid_name(text):
    return bool(text and len(text) <= 160 and len(identity(text)) >= 2 and
                not _NOISE.fullmatch(text) and not _MENU_CONTEXT.search(text) and
                not re.search(r'\d+\s*/\s*\d+|[。！？!?；;]', text))


def detect_profile_candidates(lines, shape, timestamp, profile):
    """Read explicit typed notifications; never index arbitrary screen prose."""
    observed = [line if isinstance(line, OCRLine) else OCRLine(str(line)) for line in lines]
    observed = [OCRLine(clean_text(line.text), line.score, line.box) for line in observed if line.text]
    if any(_menu_marker(line.text, marker) for line in observed for marker in profile.get('exclude_text', [])):
        return []
    categories = {c['id']: c for c in profile['categories'] if c.get('enabled', True)}
    aliases = {identity(key): value for key, value in profile.get('aliases', {}).items()}
    output, claimed = {}, set()
    for rule in sorted(profile.get('rules', []), key=lambda item: -len(item['trigger'])):
        if rule['category'] not in categories:
            continue
        if any(word.casefold() in line.text.casefold() for line in observed for word in rule.get('exclude', [])):
            continue
        trigger = clean_text(rule['trigger'])
        for line in observed:
            claim = (id(line), rule['category'], rule['semantic'])
            if claim in claimed:
                continue
            if not _inside(line, rule.get('region'), shape) or _MENU_CONTEXT.search(line.text):
                continue
            if not line.text.casefold().startswith(trigger.casefold()):
                continue
            suffix = line.text[len(trigger):]
            if trigger[-1:].isascii() and trigger[-1:].isalnum() and suffix[:1].isalnum():
                continue
            name = _clean_name(suffix)
            candidate_line = line
            if rule.get('name_region'):
                names = [other for other in observed if other is not line and
                         _inside(other, rule['name_region'], shape) and _valid_name(_clean_name(other.text)) and
                         not any(other.text.casefold() == r['trigger'].casefold() for r in profile.get('rules', []))]
                if names:
                    # The highest-confidence title, with reading order as stable tie break.
                    candidate_line = max(names, key=lambda other: other.score)
                    name = _clean_name(candidate_line.text)
                elif not _inside(line, rule['name_region'], shape):
                    name = ''
            elif not name:
                names = [other for other in observed if other is not line and _nearby(line, other)
                         and _valid_name(_clean_name(other.text)) and
                         not any(other.text.casefold() == r['trigger'].casefold() for r in profile.get('rules', []))]
                if names:
                    candidate_line = names[0]
                    name = _clean_name(candidate_line.text)
            if not _valid_name(name):
                continue
            claimed.add(claim)
            name = aliases.get(identity(name), name)
            semantic = rule['semantic']
            category = rule['category']
            key = (category, identity(name), semantic)
            result = {'kind': category, 'name': name, 'semantic': semantic,
                      'time': timestamp, 'end_time': timestamp,
                      'text': line.text + ('\n' + candidate_line.text if candidate_line is not line else ''),
                      'confidence': min(float(line.score), float(candidate_line.score)),
                      'evidence': f'profile:{semantic}:{rule["id"]}', 'verified': 0,
                      '_profile_rule': True, '_identity_key': identity(name) + '|' + semantic,
                      '_keep_all': categories[category]['policy'] == 'all'}
            if key not in output or result['confidence'] > output[key]['confidence']:
                output[key] = result
    return list(output.values())


def _semantic(row):
    evidence = row.get('evidence', '')
    if evidence.startswith('profile:'):
        return evidence.split(':', 2)[1]
    # Existing adapters historically treat boss HUD/victory as one object.
    # Only explicit configurable rules introduce separate event semantics.
    return row.get('semantic') or 'seen'


def catalog_events(rows, videos, profile):
    """Apply per-kind policies within the supplied projects, retaining evidence.

    Even if a caller accidentally supplies several projects, their names never
    collide. Order is import order then file time; reviewed/manual rows survive.
    """
    from .event_catalog import import_order_key, _continuous_panels, _resolve_memory_chapters, group_events, library_events
    from .collection_taxonomy import classify_existing_event, collection_identity
    categories = {c['id']: c for c in profile['categories']}
    videos_by_id = {video['id']: video for video in videos}
    order = {video['id']: index for index, video in enumerate(sorted(videos, key=import_order_key))}
    if profile['adapter'] == 'phantom_blade_zero_demo' and not profile.get('rules') and not profile.get('aliases'):
        baseline = {c['id']: (c['enabled'], c['policy']) for c in builtin_profiles()[0]['categories']}
        selected = {c['id']: (c['enabled'], c['policy']) for c in profile['categories']}
        if selected == baseline:
            # Renaming a scheme/category/group must not resurrect old corrected
            # observations or change first representatives in an approved library.
            # The mature PBZ policy also protects ambiguous book chapters.
            project_groups = {}
            for video in videos:
                project_groups.setdefault(video.get('project_id', ''), []).append(video)
            legacy = []
            for project_videos in project_groups.values():
                grouped = []
                for video in project_videos:
                    grouped.extend(group_events([dict(r) for r in rows if r['video_id'] == video['id']],
                                                compact=False, collection_only=True))
                legacy.extend(library_events(grouped, project_videos))
            return sorted(legacy, key=lambda row: (order[row['video_id']], row['time'], row['id']))
    candidates = []
    aliases = {identity(key): value for key, value in profile.get('aliases', {}).items()}
    for original in rows:
        if original.get('video_id') not in videos_by_id:
            continue
        row = dict(original)
        reviewed = row.get('source') != 'ocr' or row.get('edited') or row.get('verified')
        if profile['adapter'] == 'phantom_blade_zero_demo' and not reviewed and not row.get('evidence', '').startswith('profile:'):
            row = classify_existing_event(row)
            if row is None:
                continue
        if not reviewed and (row['kind'] not in categories or not categories[row['kind']]['enabled']):
            continue
        row['name'] = aliases.get(identity(row.get('name', '')), row.get('name', '')) if not reviewed else row.get('name', '')
        candidates.append(row)
    if profile['adapter'] == 'phantom_blade_zero_demo':
        untouched = [r for r in candidates if categories.get(r['kind'], {}).get('policy') == 'all']
        mergeable = [r for r in candidates if categories.get(r['kind'], {}).get('policy') != 'all']
        candidates = _continuous_panels(_resolve_memory_chapters(mergeable)) + untouched
    candidates.sort(key=lambda row: (order[row['video_id']], row['time'], row['id']))
    result, seen, ids, continuous = [], set(), set(), {}
    for original in candidates:
        row = {**original, 'occurrence_count': 1, 'occurrences': []}
        if row['id'] in ids:
            continue
        ids.add(row['id'])
        category = categories.get(row['kind'], {})
        policy = category.get('policy', 'all')
        name = identity(row.get('name', ''))
        protected = row.get('source') != 'ocr' or row.get('edited') or row.get('verified')
        project = videos_by_id[row['video_id']].get('project_id', '')
        entity = (collection_identity(row['kind'], row['name'])
                  if profile['adapter'] == 'phantom_blade_zero_demo' else name)
        key = (project, row['kind'], entity, _semantic(row))
        identifiable = bool(name) and not _UNKNOWN.search(row['name'])
        if not protected and identifiable and policy == 'first':
            if key in seen:
                continue
            seen.add(key)
        if not protected and identifiable and policy == 'occurrence' and row.get('evidence', '').startswith('profile:'):
            local = (row['video_id'], *key)
            previous = continuous.get(local)
            # A positive observed interval proves continuity; proximity alone does not.
            if previous and float(previous.get('end_time') or previous['time']) > previous['time'] and \
                    previous['time'] <= row['time'] <= float(previous['end_time']) + .0001:
                previous['end_time'] = max(float(previous['end_time']), float(row.get('end_time') or row['time']))
                continue
            continuous[local] = row
        row['first_occurrence_only'] = policy == 'first' and not protected and identifiable
        result.append(row)
    return result
