"""Focused collection indexing. UI titles are evidence of appearance, not acquisition.

Only structured game panels, current task selections and boss HUD names are
eligible. Descriptions, tutorials and map legends never become a text index.
"""
from __future__ import annotations

import re

from .rules import clean_text, identity, classify_lines
from .scene_focus import _lines, detect_scene_focus
from .navigation_events import detect_navigation
from .quest_events import detect_quests

LABELS = {
    'bell': '铃铛', 'weapon': '武器及影之武装', 'accessory': '饰品及药品',
    'challenge': '挑战地点', 'amber': '杀气琥珀碎片', 'blackstone': '黑石碎片',
    'memory_flower': '记忆之花', 'task_story': '故事', 'task_rumor': '异闻',
    'task_collection': '收集', 'book': '书籍', 'reading_memory': '记忆之花',
    'boss': 'BOSS',
}
COLLECTION_KINDS = frozenset(LABELS)
_TASK_LABELS = {'故事': 'task_story', '主线': 'task_story', '异闻': 'task_rumor',
                '異聞': 'task_rumor', '收集': 'task_collection'}
_PANEL_LABELS = {
    '书籍': 'book', '書籍': 'book', '记忆之花': 'reading_memory',
    '單手武器': 'weapon', '单手武器': 'weapon', '双手武器': 'weapon',
    '雙手武器': 'weapon', '双持武器': 'weapon', '双刀武器': 'weapon',
    '影之武装': 'weapon', '影之武裝': 'weapon', '魂器': 'weapon', '暗器': 'weapon',
    '生存类饰品': 'accessory', '战斗类饰品': 'accessory', '特殊类饰品': 'accessory',
    '攻击类饰品': 'accessory', '辅助类饰品': 'accessory', '秘药': 'accessory', '回复药': 'accessory',
    '饰品': 'accessory', '飾品': 'accessory', '药瓶': 'accessory', '药品': 'accessory',
    '藥瓶': 'accessory', '藥品': 'accessory', '挑战地点': 'challenge',
}
# These title/category associations are visible in the supplied task-menu shots.
KNOWN_TASKS = {
    'task_story': {'血脉觉醒', '红房子', '虚空幻象', '昆仑三杰', '泣声何来',
                   '钢铁巨塔', '逆流而上', '溪谷深处'},
    'task_rumor': {'血肉花', '三面混沌', '凶斧', '隔绝楔子', '难言之隐',
                   '见习医师', '清理门户'},
}
_NAV = {'地图', '任务', '角色', '道具', '读物', '系统'}
_PROSE = re.compile(r'[。！？!?；;，,]|(?:按住|按下|治疗量|恢复量|伤害能力|破防能力|首次获取|触摸铃铛)')
_CONTROLS = re.compile(r'^(?:返回|确认|确定|选择|查看|查看内容|装备|装备新武器|当前拥有|已获得|地图完整度|获得方式)$')
_COUNTER = re.compile(r'\s*(?:[（(]\s*\d+\s*/\s*\d+\s*[)）]|[xX×+]\s*\d+)\s*$')
_BOSS = re.compile(r'^[\u3400-\u9fff]{2,12}\s*[:：]\s*[\u3400-\u9fff]{2,12}$')
_WEAPON_NAMES = {'黑伤', '长形黑伤', '木制黑伤', '黑伤双刃', '残钢刃'}
_BELL_ALIASES = {'斧老大落处': '斧老大斩落处', '远跳龙柱处': '远眺龙柱处',
                 '聋老大关押处': '斧老大关押处', '爷老大关押处': '斧老大关押处',
                 '溪谷平原入': '溪谷平原入口'}


def _canonical_name(kind, name):
    if kind == 'weapon':
        # Verified rank-badge OCR artifacts, only beside these exact weapon
        # names. Never strip a final 口/自 from arbitrary object names.
        for suffix in ('口', '自', '1'):
            if name.endswith(suffix) and name[:-len(suffix)] in _WEAPON_NAMES:
                return name[:-len(suffix)]
    if kind == 'bell':
        return _BELL_ALIASES.get(name, name)
    return name


def _title(value):
    value = _COUNTER.sub('', clean_text(value)).translate(str.maketrans('', '', '“”\"')).strip(' :：[]【】')
    value = re.sub(r'\s*[(（\[【]+\s*$', '', value)
    if not (2 <= len(identity(value)) <= 30) or _PROSE.search(value) or _CONTROLS.fullmatch(value):
        return None
    if re.fullmatch(r'[\d\W_]+', value):
        return None
    return value


def _material_kind(name):
    return {'杀气琥珀碎片': 'amber', '杀气琥珀': 'amber', '黑石碎片': 'blackstone',
            '记忆之花': 'memory_flower'}.get(_COUNTER.sub('', clean_text(name)))


def _task_kind(name):
    return next((kind for kind, names in KNOWN_TASKS.items() if name in names), None)


def classify_existing_event(event):
    """Translate saved structured evidence; bare OCR words are intentionally rejected.

    For old generic rows, the migration should re-read their saved screenshot
    through ``detect_collection_candidates`` rather than infer an item from prose.
    """
    record = dict(event)
    if record.get('source') in {'manual', 'chapter', 'obs', 'marker'}:
        return record
    evidence = record.get('evidence', '')
    if evidence in {'upgrade_material', 'scene_secondary', 'dialogue_speaker'}:
        return None
    name = _title(record.get('name', ''))
    lines = [clean_text(x) for x in str(record.get('text', '')).splitlines() if x.strip()]
    kind = record.get('kind')
    if kind in COLLECTION_KINDS:
        if name:
            return {**record, 'name': _canonical_name(kind, name), 'first_occurrence_only': True}
        return None
    if evidence in {'boss_name', 'victory_template'} and name and _BOSS.fullmatch(name):
        return {**record, 'kind': 'boss', 'name': name.replace('：', ':'), 'first_occurrence_only': True}
    if evidence in {'waypoint_menu', 'waypoint_notice'} or kind == 'waypoint':
        return ({**record, 'kind': 'bell', 'name': _canonical_name('bell', name), 'first_occurrence_only': True}
                if name and name not in {'铃铛', '鹤型铃铛'} else None)
    if evidence == 'inventory_selection' and name:
        # This legacy evidence was only emitted for the weapon category/stats
        # layout, not for every inventory screen.
        return {**record, 'kind': 'weapon', 'name': _canonical_name('weapon', name), 'first_occurrence_only': True}
    if evidence == 'item_panel':
        for index, label in enumerate(lines[:-1]):
            panel_kind = _PANEL_LABELS.get(label)
            panel_name = _title(lines[index + 1])
            if panel_kind and panel_name:
                return {**record, 'kind': panel_kind, 'name': panel_name, 'first_occurrence_only': True}
    if kind == 'quest' or evidence.startswith('quest_'):
        # Old detectors stored objective prose as name; the preceding title is
        # the task identity and avoids recording each new objective separately.
        titles = [x for x in lines[1:] if _title(x)]
        for title in titles:
            category = _task_kind(title)
            if category:
                return {**record, 'kind': category, 'name': title, 'first_occurrence_only': True}
        if name and _task_kind(name):
            return {**record, 'kind': _task_kind(name), 'name': name, 'first_occurrence_only': True}
        return None
    if kind == 'item' and name and any(re.match(r'^(?:获得|已获得|拾取|取得|收集到)', line) for line in lines):
        category = _material_kind(name)
        if category:
            return {**record, 'kind': category, 'name': name, 'first_occurrence_only': True}
    return None


def _candidate(kind, title, timestamp, category='', evidence='collection_panel'):
    return {'kind': kind, 'name': title.text if hasattr(title, 'text') else title,
            'time': max(0., float(timestamp)), 'confidence': round(getattr(title, 'score', .8), 4),
            'text': '\n'.join(x for x in (category, title.text if hasattr(title, 'text') else title) if x),
            'evidence': evidence, 'verified': 0, 'first_occurrence_only': True}


def _selected_task_row(line, frame):
    if frame is None:
        return .265 <= line.y <= .315
    # The selected task is black lettering on a broad pale horizontal strip;
    # the other rows sit on black. Inspect that strip, not text brightness.
    try:
        height, width = frame.shape[:2]
        patch = frame[max(0, int((line.top-.006)*height)):min(height, int((line.bottom+.006)*height)),
                      int(.015*width):int(.32*width)]
        if not patch.size:
            return False
        gray = patch.mean(axis=2) if patch.ndim == 3 else patch
        return float((gray > 145).mean()) >= .48
    except (AttributeError, TypeError, ValueError, IndexError):
        return False


def detect_collection_candidates(lines, frame_shape, timestamp, frame=None):
    """Read only the principal heading in recognized collection UI layouts."""
    observed = _lines(lines, frame_shape)
    if not observed:
        return []
    result = []
    tabs = {line.text for line in observed if line.y < .10 and line.text in _NAV}
    menu = len(tabs) >= 3
    # The full-map collection summary contains category labels/counts, no
    # acquisition events. Exclude it before considering material names.
    legend = sum(any(label in line.text and .38 < line.y < .70 for line in observed)
                 for label in ('铃铛', '武器', '饰品', '挑战地点', '琥珀碎片', '黑石碎片', '记忆之花')) >= 3
    if legend:
        return []
    categories = [line for line in observed if line.left >= .55 and .115 <= line.y <= .285
                  and (line.text in _PANEL_LABELS or line.text in {'重要养成材料', '养成材料', '重要材料'})]
    for category in categories:
        titles = [line for line in observed if line.top >= category.bottom - .004
                  and line.top - category.bottom <= .085 and abs(line.left - category.left) <= .075
                  and line.y < .34 and _title(line.text) and line.text not in _PANEL_LABELS]
        if not titles:
            continue
        title = min(titles, key=lambda line: (line.top, -line.score))
        kind = _PANEL_LABELS.get(category.text) or _material_kind(title.text)
        if kind:
            candidate = _candidate(kind, title, timestamp, category.text)
            candidate['name'] = _title(title.text)
            # Newly found book pages have a compact chapter line directly
            # below the book title. The library cover view has body prose
            # below its divider; that prose must never become another name.
            if kind == 'book':
                chapters = [line for line in observed if line.top >= title.bottom + .003
                            and line.top - title.bottom <= .055 and line.y <= .335
                            and abs(line.left - title.left) <= .045 and _title(line.text)
                            and len(identity(line.text)) <= 22 and line.text not in _PANEL_LABELS]
                if chapters:
                    chapter = min(chapters, key=lambda line: line.top)
                    candidate['name'] += ' · ' + _title(chapter.text)
                    candidate['text'] += '\n' + chapter.text
            result.append(candidate)
    # Task tabs are headings; only the selected white row supplies a title.
    # Nearby rows and the map/region title are ignored.
    task_categories = [line for line in observed if line.text in _TASK_LABELS
                       and .06 <= line.left <= .27 and .17 <= line.y <= .245]
    if menu and task_categories:
        selected = [line for line in observed if .025 <= line.left <= .27 and line.right < .36
                    and .255 <= line.y <= .85 and line.bottom - line.top >= .018
                    and _title(line.text) and _selected_task_row(line, frame)]
        if selected:
            title = max(selected, key=lambda line: (line.bottom - line.top, line.score))
            result.append(_candidate(_TASK_LABELS[task_categories[0].text], title, timestamp,
                                     task_categories[0].text, 'task_selection'))
    # Rest/travel locations are the game's bells, not generic waypoint text.
    for candidate in detect_navigation(lines, frame_shape, timestamp):
        translated = classify_existing_event(candidate)
        if translated:
            result.append(translated)
    for candidate in detect_quests(lines, frame_shape, timestamp):
        translated = classify_existing_event(candidate)
        if translated:
            result.append(translated)
    for candidate in classify_lines(lines, include_text=False):
        translated = classify_existing_event(candidate)
        if translated:
            result.append(translated)
    focus = detect_scene_focus(lines, frame_shape)
    if focus and focus.context == 'boss':
        for title in focus.names:
            result.append(_candidate('boss', title, timestamp, evidence='boss_name'))
    elif focus and focus.context == 'inventory' and not any(
            candidate['kind'] != 'weapon' and candidate.get('evidence') == 'collection_panel'
            for candidate in result):
        for title in focus.names:
            result.append(_candidate('weapon', title, timestamp, evidence='inventory_selection'))
    from .boss_text_index import _readings
    for read in _readings(lines, frame_shape):
        if _BOSS.fullmatch(read.text):
            result.append(_candidate('boss', read, timestamp, evidence='boss_name'))
    unique = {}
    for candidate in result:
        candidate['name'] = _canonical_name(candidate['kind'], candidate['name'])
        key = candidate['kind'], identity(candidate['name'])
        if key not in unique or candidate.get('confidence', 0) > unique[key].get('confidence', 0):
            unique[key] = candidate
    return list(unique.values())


class FirstCollectionFilter:
    """A per-video first-appearance filter; only accepted identities consume memory."""
    def __init__(self):
        self.seen = set()

    def filter(self, candidates):
        result = []
        for candidate in candidates:
            translated = classify_existing_event(candidate)
            if not translated:
                continue
            key = translated['kind'], identity(translated['name'])
            if key not in self.seen:
                self.seen.add(key)
                result.append(translated)
        return result
