"""Focused collection indexing. UI titles are evidence of appearance, not acquisition.

Only structured game panels, current task selections and boss HUD names are
eligible. Descriptions, tutorials and map legends never become a text index.
"""
from __future__ import annotations

import re
from dataclasses import replace

from .rules import clean_text, identity, classify_lines
from .collection_aliases import verified_name
from .scene_focus import _lines, detect_scene_focus
from .navigation_events import detect_navigation
from .quest_events import detect_quests
from .collection_refinement import SHADOW_CATEGORIES, is_weapon_panel, is_equipment_menu, detect_selected_bell

LABELS = {
    'bell': '铃铛', 'weapon': '武器及影之武装', 'accessory': '饰品', 'medicine': '药品',
    'story_item': '剧情道具', 'task_item': '任务物品',
    'challenge': '挑战地点', 'amber': '杀气琥珀碎片', 'blackstone': '黑石碎片',
    'memory_flower': '记忆之花', 'task_story': '故事', 'task_rumor': '异闻',
    'task_collection': '收集', 'book': '书籍', 'reading_memory': '记忆之花',
    'boss': 'BOSS',
}
COLLECTION_KINDS = frozenset(LABELS)
REPEATABLE_KINDS = frozenset({'amber', 'blackstone', 'book'})
_TASK_LABELS = {'故事': 'task_story', '主线': 'task_story', '异闻': 'task_rumor',
                '異聞': 'task_rumor', '收集': 'task_collection'}
_PANEL_LABELS = {
    '书籍': 'book', '書籍': 'book', '记忆之花': 'reading_memory', '記憶之花': 'reading_memory', '記忆之花': 'reading_memory',
    '记忆之花花瓣': 'memory_flower', '記憶之花花瓣': 'memory_flower', '記忆之花花瓣': 'memory_flower',
    '剧情道具': 'story_item', '劇情道具': 'story_item',
    '任务物品': 'task_item', '任務物品': 'task_item',
    '妙手小张宝藏': 'task_item', '妙手小張寶藏': 'task_item',
    # Verified on the provided 古代弩机 panel: enlarged OCR confuses 妙/炒.
    '炒手小张宝藏': 'task_item',
    '妙手小张的宝藏': 'task_item', '妙手小張的寶藏': 'task_item',
    '單手武器': 'weapon', '单手武器': 'weapon', '双手武器': 'weapon',
    '雙手武器': 'weapon', '双持武器': 'weapon', '双刀武器': 'weapon',
    '影之武装': 'weapon', '影之武裝': 'weapon', '魂器': 'weapon', '暗器': 'weapon',
    **{label: 'weapon' for label in SHADOW_CATEGORIES},
    '生存类饰品': 'accessory', '战斗类饰品': 'accessory', '特殊类饰品': 'accessory',
    '攻击类饰品': 'accessory', '辅助类饰品': 'accessory', '秘药': 'medicine', '回复药': 'medicine',
    '饰品': 'accessory', '飾品': 'accessory', '药瓶': 'medicine', '药品': 'medicine',
    '藥瓶': 'medicine', '藥品': 'medicine', '挑战地点': 'challenge',
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
_CONTROLS = re.compile(r'^(?:返回|确认|确定|选择|查看|查看内容|装备|装备饰品|装备新武器|装备(?:新的?)?影之武装|当前拥有|已获得|地图完整度|获得方式)$')
_COUNTER = re.compile(r'\s*(?:[（(]\s*\d+\s*/\s*\d+\s*[)）]|[xX×+]\s*\d+)\s*$')
_BOSS = re.compile(r'^[\u3400-\u9fff]{2,12}\s*[:：]\s*[\u3400-\u9fff]{2,12}$')
_WEAPON_NAMES = {'黑伤', '长形黑伤', '木制黑伤', '黑伤双刃', '残钢刃'}
_BELL_ALIASES = {'斧老大落处': '斧老大斩落处', '远跳龙柱处': '远眺龙柱处',
                 '聋老大关押处': '斧老大关押处', '爷老大关押处': '斧老大关押处',
                 '溪谷平原入': '溪谷平原入口'}
_FLOWER_LABEL = re.compile(r'^[记記][忆憶]之花(?:花[瓣辨])?$')
_FLOWER_NAME = re.compile(r'[记記][忆憶]之花$')
_FLOWER_CHAPTER = re.compile(r'^[其共]([一二三四五六七八九十\d]+)\s*[:：]?\s*(\S.*)$')


def _flower_headings(observed):
    """Join OCR boxes on the same title row, never nearby chapters or prose."""
    rows = [replace(line, text=line.text[:-1]+'瓣')
            if .55 <= line.left and .13 <= line.y <= .23 and
            _FLOWER_LABEL.fullmatch(line.text) and line.text.endswith('辨') else line
            for line in observed]
    removed = set()
    replacements = {}
    for index, title in enumerate(rows):
        if not (.60 <= title.left and .18 <= title.y <= .33 and
                _FLOWER_NAME.search(title.text) and not _FLOWER_LABEL.fullmatch(title.text)):
            continue
        parts = [(index, title)]
        left = title.left
        for other_index, other in sorted(enumerate(rows), key=lambda pair: -pair[1].right):
            if other_index == index or not (.60 <= other.left < left-.015 and -.02 <= left-other.right <= .04):
                continue
            if abs(other.y-title.y) > .012 or other.bottom-other.top < .024 or not _title(other.text):
                continue
            parts.insert(0, (other_index, other))
            removed.add(other_index)
            left = other.left
        if len(parts) > 1:
            replacements[index] = replace(title, text=''.join(line.text for _,line in parts),
                                          left=left, score=min(line.score for _,line in parts))
    return [replacements.get(index, line) for index, line in enumerate(rows) if index not in removed]


def _canonical_name(kind, name):
    name = verified_name(kind, name)
    if kind in {'memory_flower', 'reading_memory'}:
        base, separator, chapter = name.partition(' · ')
        # Verified title-box errors in the recordings: a missed prefix or a
        # closing quote read as 白/自. Restrict aliases to these exact names.
        base = {'同门的记忆之花': '有关同门的记忆之花',
                '有关龙体之术白的记忆之花': '有关龙体之术的记忆之花',
                '有关龙体之术自的记忆之花': '有关龙体之术的记忆之花'}.get(base, base)
        return base + (' · ' + _flower_chapter(chapter) if separator else '')
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


def _book_title(value):
    """A heading's length/punctuation alone cannot discard a book record."""
    value = clean_text(value).translate(str.maketrans('', '', '“”\"')).strip(' :：[]【】')
    if not value or _CONTROLS.fullmatch(value) or re.fullmatch(r'[\d\W_]+',value):
        return None
    return value


def _flower_chapter(value):
    chapter=re.sub(r'\s*[:：]\s*', ':', clean_text(value))
    chapter=_FLOWER_CHAPTER.sub(r'其\1:\2',chapter)
    # Confirmed against the user's in-game screenshot; OCR often drops or
    # misreads the last character of this particular chapter heading.
    chapter = re.sub(r'^(其二\s*[:：]\s*分道扬)(?:镀)?$', r'\1镳', chapter)
    return {'其三:斩新的力量': '其三:崭新的力量', '其三:轿新的力量': '其三:崭新的力量',
            '其五:查无音信': '其五:杳无音信',
            '其二:爱人重逢': '其一:爱人重逢',
            '其三:子然一身的解脱': '其三:孑然一身的解脱',
            '其一:弹': '其一:弹奏', '其一:弹秦': '其一:弹奏',
            '其三:金王阁': '其三:金玉阁',
            '其二:圣母言': '其二:圣母谶言', '其二:圣母識言': '其二:圣母谶言'}.get(chapter, chapter)


def collection_identity(kind, name):
    """Use verified aliases while retaining complete chapter/form identities."""
    return identity(_canonical_name(kind, name))


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
    if record.get('evidence') in {'book_menu', 'memory_menu', 'weapon_menu'}:
        return None
    if record.get('source') in {'manual', 'chapter', 'obs', 'marker'}:
        return record
    evidence = record.get('evidence', '')
    if evidence in {'upgrade_material', 'scene_secondary', 'dialogue_speaker'}:
        return None
    # A structured flower chapter can legitimately be a question. The panel
    # detector has already separated its heading from dialogue/body text.
    name = (_book_title if record.get('kind') in {'book','memory_flower','reading_memory'} else _title)(record.get('name', ''))
    lines = [clean_text(x) for x in str(record.get('text', '')).splitlines() if x.strip()]
    kind = record.get('kind')
    if name and lines and not record.get('edited') and _PANEL_LABELS.get(lines[0]) == 'task_item' and kind in {
            'story_item', 'item', 'text'}:
        # A saved typed heading can repair a legacy category. Merely mentioning
        # 小张 in a book, accessory, task description or dialogue cannot.
        title = _title(lines[1]) if len(lines) > 1 else None
        if title and name not in _PANEL_LABELS:
            return {**record, 'kind': 'task_item', 'name': _canonical_name('task_item', title), 'first_occurrence_only': True}
    if kind == 'accessory' and any(_PANEL_LABELS.get(label) == 'medicine' for label in lines):
        # Older catalogs combined these categories. Split only with the saved
        # game's exact category heading, never from a medicine-sounding name.
        kind = 'medicine'
    if kind in COLLECTION_KINDS:
        if name:
            return {**record, 'kind': kind, 'name': _canonical_name(kind, name),
                    'first_occurrence_only': kind not in REPEATABLE_KINDS}
        return None
    if evidence in {'boss_name', 'victory_template'} and name and _BOSS.fullmatch(name):
        return {**record, 'kind': 'boss', 'name': _canonical_name('boss', name), 'first_occurrence_only': True}
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
            panel_name = (_book_title if panel_kind == 'book' else _title)(lines[index + 1])
            if not panel_kind and label in {'重要养成材料', '养成材料', '重要材料'} and panel_name:
                panel_kind = _material_kind(panel_name)
            if panel_kind and panel_name:
                return {**record, 'kind': panel_kind, 'name': _canonical_name(panel_kind, panel_name),
                        'first_occurrence_only': panel_kind not in REPEATABLE_KINDS}
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
            return {**record, 'kind': category, 'name': name,
                    'first_occurrence_only': category not in REPEATABLE_KINDS}
    return None


def _candidate(kind, title, timestamp, category='', evidence='collection_panel'):
    return {'kind': kind, 'name': title.text if hasattr(title, 'text') else title,
            'time': max(0., float(timestamp)), 'confidence': round(getattr(title, 'score', .8), 4),
            'text': '\n'.join(x for x in (category, title.text if hasattr(title, 'text') else title) if x),
            'evidence': evidence, 'verified': 0, 'first_occurrence_only': kind not in REPEATABLE_KINDS}


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


def _task_banner_titles(observed, frame):
    """Known task headings on a pale notice, including non-imperative goals.

    This establishes only that the task was displayed. A question or a future
    instruction under it is not evidence of completion, nor an object name.
    """
    if frame is None:
        return []
    height,width=frame.shape[:2]
    patch=frame[int(.225*height):int(.38*height),int(.745*width):int(.91*width)]
    if not patch.size:
        return []
    gray=patch.mean(axis=2) if patch.ndim==3 else patch
    if float((gray>145).mean()) < .60:
        return []
    titles=[]
    for line in observed:
        if not (.72<=line.left<=.82 and .235<=line.y<=.29 and line.right<=.96
                and .025<=line.bottom-line.top<=.065 and _task_kind(line.text)):
            continue
        following=[other for other in observed if 0<=other.top-line.bottom<=.055
                   and abs(other.left-line.left)<=.035 and other.y<=.36 and other is not line]
        if following:
            titles.append(line)
    return titles


def detect_collection_candidates(lines, frame_shape, timestamp, frame=None):
    """Read only the principal heading in recognized collection UI layouts."""
    observed = _flower_headings(_lines(lines, frame_shape))
    if not observed:
        return []
    result = []
    tabs = {line.text for line in observed if line.y < .10 and line.text in _NAV}
    menu = len(tabs) >= 3
    equipment_menu = is_equipment_menu(observed)
    # The full-map collection summary contains category labels/counts, no
    # acquisition events. Exclude it before considering material names.
    legend = sum(any(label in line.text and .38 < line.y < .70 for line in observed)
                 for label in ('铃铛', '武器', '饰品', '挑战地点', '琥珀碎片', '黑石碎片', '记忆之花')) >= 3
    if legend:
        return []
    categories = [line for line in observed if line.left >= .55 and .115 <= line.y <= .285
                  and (line.text in _PANEL_LABELS or line.text in {'重要养成材料', '养成材料', '重要材料'})]
    for category in categories:
        if equipment_menu and _PANEL_LABELS.get(category.text) == 'weapon':
            continue
        if menu and _PANEL_LABELS.get(category.text) in {'book', 'reading_memory', 'memory_flower'}:
            # Covers and opened chapters in the library are both menu views.
            # World pickup panels have no top navigation and remain eligible.
            continue
        if category.text in SHADOW_CATEGORIES and not is_weapon_panel(observed):
            continue
        title_reader = _book_title if _PANEL_LABELS.get(category.text) == 'book' else _title
        titles = [line for line in observed if line.top >= category.bottom - .004
                  and line.top - category.bottom <= .085 and abs(line.left - category.left) <= .075
                  and line.y < .34 and title_reader(line.text) and line.text not in _PANEL_LABELS]
        if not titles:
            continue
        title = min(titles, key=lambda line: (line.top, -line.score))
        kind = _PANEL_LABELS.get(category.text) or _material_kind(title.text)
        if kind in {'reading_memory', 'memory_flower'} and not _FLOWER_NAME.search(title.text):
            continue
        if kind:
            candidate = _candidate(kind, title, timestamp, category.text)
            candidate['name'] = title_reader(title.text)
            # Newly found book pages have a compact chapter line directly
            # below the book title. The library cover view has body prose
            # below its divider; that prose must never become another name.
            if kind == 'book':
                chapters = [line for line in observed if line.top >= title.bottom + .003
                            and line.top - title.bottom <= .055 and line.y <= .335
                            and abs(line.left - title.left) <= .045 and _book_title(line.text)
                            and line.text not in _PANEL_LABELS]
                if chapters:
                    chapter = min(chapters, key=lambda line: line.top)
                    candidate['name'] += ' · ' + _book_title(chapter.text)
                    candidate['text'] += '\n' + chapter.text
            if kind == 'memory_flower':
                chapters = [line for line in observed if line.top >= title.bottom and
                            line.top-title.bottom <= .09 and abs(line.left-title.left) <= .08 and
                            _FLOWER_CHAPTER.match(line.text)]
                if chapters:
                    chapter=min(chapters,key=lambda line:line.top)
                    candidate['name'] += ' · ' + _flower_chapter(chapter.text)
                    candidate['text'] += '\n' + chapter.text
            result.append(candidate)
    # The small category label can be missed by OCR on the game's dark pickup
    # screen. Require both a named flower heading and its nearby completion
    # counter; a sentence or a title in a menu alone is not enough evidence.
    if not menu and not any(c['kind'] in {'reading_memory','memory_flower'} for c in result):
        for title in observed:
            name = _title(title.text)
            if not (name and len(identity(name)) > len('记忆之花') and
                    _FLOWER_NAME.search(name) and not _FLOWER_LABEL.fullmatch(name) and
                    title.left >= .55 and .14 <= title.y <= .33):
                continue
            confirmations=[line for line in observed if 0 <= line.top-title.bottom <= .24 and abs(line.left-title.left) <= .10
                           and re.search(r'(?:已[获獲]得完整[记記][忆憶]|集[齐齊]全部[记記][忆憶])',line.text)]
            if confirmations:
                partial='集' in confirmations[0].text
                candidate=_candidate('memory_flower' if partial else 'reading_memory',title,timestamp,'记忆之花')
                candidate['name'] = name
                if partial:
                    chapters=[line for line in observed if line.top >= title.bottom and line.top-title.bottom <= .09 and
                              abs(line.left-title.left) <= .08 and _FLOWER_CHAPTER.match(line.text)]
                    if chapters:
                        chapter=min(chapters,key=lambda line:line.top)
                        candidate['name'] += ' · ' + _flower_chapter(chapter.text)
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
    for title in _task_banner_titles(observed,frame):
        result.append(_candidate(_task_kind(title.text),title,timestamp,
                                 '', 'task_banner'))
    # Rest/travel locations are the game's bells, not generic waypoint text.
    for candidate in detect_navigation(lines, frame_shape, timestamp):
        translated = classify_existing_event(candidate)
        if translated:
            result.append(translated)
    result.extend(detect_selected_bell(lines, frame, timestamp))
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
    elif focus and focus.context == 'inventory' and not equipment_menu and not any(
            candidate['kind'] != 'weapon' and candidate.get('evidence') == 'collection_panel'
            for candidate in result):
        for title in focus.names:
            result.append(_candidate('weapon', title, timestamp, evidence='weapon_pickup_panel'))
    from .boss_text_index import _readings
    for read in _readings(lines, frame_shape):
        if _BOSS.fullmatch(read.text):
            result.append(_candidate('boss', read, timestamp, evidence='boss_name'))
    unique = {}
    for candidate in result:
        if equipment_menu and candidate['kind'] == 'weapon':
            continue
        candidate['name'] = _canonical_name(candidate['kind'], candidate['name'])
        key = candidate['kind'], identity(candidate['name'])
        if key not in unique or candidate.get('confidence', 0) > unique[key].get('confidence', 0):
            unique[key] = candidate
    return list(unique.values())


class FirstCollectionFilter:
    """Keep first named objects; books and fragment pickups remain separate."""
    def __init__(self):
        self.seen = set()

    def filter(self, candidates):
        result = []
        for candidate in candidates:
            translated = classify_existing_event(candidate)
            if not translated:
                continue
            if translated['kind'] in REPEATABLE_KINDS:
                result.append(translated)
                continue
            if translated['kind']=='boss' and translated.get('_identity_key'):
                # Stable encounter IDs let the accumulator update one prompt
                # while later independent frames improve its spelling.
                result.append(translated)
                continue
            key = translated['kind'], collection_identity(translated['kind'], translated['name'])
            if key not in self.seen:
                self.seen.add(key)
                result.append(translated)
        return result
