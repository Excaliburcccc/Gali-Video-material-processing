"""Keep the principal label in recognized demo UI scenes.

This is an optional layout adapter, not a waypoint/unlock or victory detector.
A map selection is only a searchable label. No game names or video times are
stored here; map controls and normalized box positions establish the context.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re

from .rules import clean_text, identity


_CJK = re.compile(r'[\u3400-\u9fff]')
_BOSS_NAME = re.compile(r'[\u3400-\u9fff]{2,12}\s*:\s*[\u3400-\u9fff]{2,12}')
_CONTROLS = ('地图完整度', '地圖完整度', '进入全域地图', '進入全域地圖',
             '查看收集详情', '查看收集詳情', '重置视图', '重置視圖')
_NAME_NOISE = re.compile(r'地图|地圖|查看|返回|确认|確認|完整度|缩放|縮放|重置|移动|移動|进入|進入|取消|按住|按下')
_NAV_TABS = {'地图', '地圖', '任务', '任務', '角色', '道具', '读物', '讀物', '系统', '系統'}
_WEAPON_CATEGORIES = {'单手武器', '單手武器', '双手武器', '雙手武器', '双持武器', '雙持武器',
                      '双刀武器', '雙刀武器'}
_WEAPON_STATS = re.compile(r'(?:伤害能力|傷害能力|破防能力).*[0-9]')
_WEAPON_CATEGORY_VARIANT = re.compile(r'^(?:单手|單手|双手|雙手|双持|雙持|双刀|雙刀)式器$')


@dataclass(frozen=True)
class SceneFocus:
    context: str
    names: tuple[str, ...]
    source_names: tuple[str, ...]


@dataclass(frozen=True)
class _Line:
    text: str
    score: float
    left: float
    top: float
    right: float
    bottom: float

    @property
    def x(self): return (self.left + self.right) / 2

    @property
    def y(self): return (self.top + self.bottom) / 2


def _lines(lines, shape):
    try:
        height, width = float(shape[0]), float(shape[1])
        if not all(math.isfinite(v) and v > 0 for v in (height, width)):
            return []
    except (TypeError, ValueError, IndexError):
        return []
    result = []
    for line in lines:
        try:
            if not line.box or len(line.box) < 4:
                continue
            xs = [float(p[0]) / width for p in line.box]
            ys = [float(p[1]) / height for p in line.box]
            score = float(line.score)
            if not all(math.isfinite(v) for v in xs + ys + [score]) or score < .55:
                continue
            if min(xs) < -.02 or max(xs) > 1.02 or min(ys) < -.02 or max(ys) > 1.02:
                continue
            value = clean_text(line.text)
            if value and len(value) <= 200:
                result.append(_Line(value, score, min(xs), min(ys), max(xs), max(ys)))
        except (TypeError, ValueError, IndexError, AttributeError, OverflowError):
            continue
    return result


def _map_name(value):
    # Only a selected map-label slot is eligible for icon cleanup. OCR can read
    # its controller icon as alpha, X or punctuation; never strip CJK letters.
    if _CJK.search(value):
        value = re.sub(r'^[^\u3400-\u9fff]+', '', value)
    value = value.strip(' :：[]【】·•')
    if (2 <= len(identity(value)) <= 30 and _CJK.search(value)
            and not _NAME_NOISE.search(value)
            and not re.search(r'[。！？!?；;]|\d\s*[/:%％]', value)):
        return value
    return None


def detect_scene_focus(lines, shape) -> SceneFocus | None:
    """Return a map/boss text focus, or None when the layout is unrecognized.

    An empty ``names`` tuple means a map is recognized but its center selection
    is unreadable/ambiguous. In that case compact indexing must not substitute
    a world title or an unrelated nearby point. No images/state are retained.
    """
    observed = _lines(lines, shape)
    weapon_tabs = {line.text for line in observed if .35 <= line.x <= .65 and line.y < .09}
    weapon_back = any(line.left >= .80 and line.y > .90 and '回退' in line.text for line in observed)
    weapon_menu = {'武器', '影之武装'} <= weapon_tabs and weapon_back
    weapon_categories = [line for line in observed if (line.text in _WEAPON_CATEGORIES
                         or (weapon_menu and _WEAPON_CATEGORY_VARIANT.fullmatch(line.text)))
                         and .60 <= line.left <= .75 and .13 <= line.y <= .19]
    weapon_stats = [line for line in observed if line.left >= .60 and .24 <= line.y <= .33
                    and _WEAPON_STATS.search(line.text)]
    if weapon_categories and (weapon_stats or weapon_menu):
        for category in weapon_categories:
            headings = [line for line in observed if abs(line.left - category.left) <= .065
                        and .19 <= line.y <= .255 and line.top >= category.bottom - .003
                        and line.bottom <= min((stat.top for stat in weapon_stats), default=.30) + .005
                        and _CJK.search(line.text) and 2 <= len(identity(line.text)) <= 24
                        and not _WEAPON_STATS.search(line.text)]
            if headings:
                heading = max(headings, key=lambda line: line.score)
                # A rank badge beside the title can be read as a trailing open
                # bracket. Remove this unpaired edge artifact, not name words.
                name = re.sub(r'\s*[(（\[【]+\s*$', '', heading.text).strip()
                if name:
                    return SceneFocus('inventory', (name,), (heading.text,))
    tabs = {line.text for line in observed if line.y < .09 and line.text in _NAV_TABS}
    story_header = any(line.text == '故事' and line.right < .35 and .15 < line.y < .26
                       for line in observed)
    list_rows = [line for line in observed if line.right < .35 and .26 < line.y < .62
                 and 2 <= len(identity(line.text)) <= 18 and _CJK.search(line.text)]
    if len(tabs) >= 3 and story_header and len(list_rows) >= 3:
        # A task catalogue contains other tasks and the broad map name. The
        # selected objective is indexed by detect_quests once its detail opens.
        # Do not treat unselected catalogue rows as visit/task occurrences.
        return SceneFocus('task_menu', (), ())
    controls = {control for control in _CONTROLS for line in observed
                if line.y >= .78 and control in line.text}
    map_ui = (any('完整度' in value for value in controls)
              and any('完整度' not in value for value in controls))
    if map_ui:
        # The travel-map cursor stays at screen center and its selected label
        # begins immediately to its right. World titles sit at the lower left.
        selected = []
        for line in observed:
            name = _map_name(line.text)
            if (name and .47 <= line.left <= .61 and line.right <= .79
                    and abs(line.y - .5) <= .055 and 0 < line.bottom - line.top <= .065):
                distance = abs(line.y - .5) + .35 * abs(line.left - .5)
                selected.append((distance, line, name))
        selected.sort(key=lambda item: (item[0], -item[1].score))
        if not selected:
            return SceneFocus('map', (), ())
        best = selected[0]
        other = next((item for item in selected[1:] if identity(item[2]) != identity(best[2])), None)
        if other and other[0] - best[0] < .018:
            return SceneFocus('map', (), ())
        return SceneFocus('map', (best[2],), (best[1].text,))

    # A boss title must be inside the narrow top HUD band and include both title
    # and name. A boss mention in dialogue or a map/task list is not a HUD.
    bosses = []
    for line in observed:
        value = line.text.replace('：', ':')
        if (.30 < line.x < .70 and .025 < line.y < .16 and line.score >= .6
                and _BOSS_NAME.fullmatch(value)):
            bosses.append((line, re.sub(r'\s+', '', value)))
    if bosses:
        line, name = max(bosses, key=lambda pair: pair[0].score)
        return SceneFocus('boss', (name,), (line.text,))

    speaker_lines = [line for line in observed
                     if .28 <= line.left and line.right <= .72 and .785 <= line.y <= .84
                     and 0 < line.bottom - line.top <= .055 and _CJK.search(line.text)
                     and 1 <= len(identity(line.text)) <= 30
                     and not re.search(r'[，。！？!?；;]', line.text)]
    if speaker_lines:
        # Speaker labels sometimes become two/three overlapping OCR boxes.
        # Their row is above (not inside) the centered subtitle row.
        anchor = max(speaker_lines, key=lambda line: line.score)
        parts = sorted((line for line in speaker_lines if abs(line.y - anchor.y) < .018),
                       key=lambda line: (line.left, -line.score))
        lower = [line for line in observed if .22 <= line.left and line.right <= .78
                 and .845 <= line.y <= .92 and line.top - max(p.bottom for p in parts) >= .004]
        skip = any('跳过' in line.text and line.left > .80 and line.y > .90 for line in observed)
        dialogue = bool(lower) and (skip or any(re.search(r'[，。！？!?；;]', line.text) for line in lower))
        gaps_ok = all(b.left - a.right < .035 for a, b in zip(parts, parts[1:]))
        middle = (parts[0].left + max(p.right for p in parts)) / 2
        if dialogue and gaps_ok and abs(middle - .5) <= .08:
            merged = ''
            raw_parts = []
            for part in parts:
                if identity(part.text) in identity(merged):
                    continue
                # Prefer a full OCR box when it already covers a previous part.
                if merged and identity(merged) in identity(part.text):
                    merged = part.text
                else:
                    overlap = max((size for size in range(1, min(len(merged), len(part.text)) + 1)
                                   if merged.endswith(part.text[:size])), default=0)
                    merged += part.text[overlap:]
                raw_parts.append(part.text)
            if 2 <= len(identity(merged)) <= 30:
                return SceneFocus('dialogue', (merged,), tuple(raw_parts))
    return None


def apply_scene_focus(candidates, focus: SceneFocus | None):
    """Filter only ordinary text; specialized event evidence always survives.

    Kept text receives an evidence label so later per-video grouping can treat
    map selections and recurring boss HUD names as names rather than events.
    ``None`` is a no-op. A known scene emits at most one principal text row.
    """
    if focus is None:
        return list(candidates)
    allowed = {identity(raw): name for raw, name in zip(focus.source_names, focus.names)}
    if focus.context == 'dialogue' and focus.names:
        allowed.update({identity(raw): focus.names[0] for raw in focus.source_names})
    allowed.update({identity(name): name for name in focus.names})
    output, selected = [], {}
    for candidate in candidates:
        if candidate.get('kind') != 'text':
            output.append(candidate)
            continue
        name = allowed.get(identity(candidate.get('name', '')))
        if name is None:
            continue
        evidence = {'map': 'map_selection', 'boss': 'boss_name', 'dialogue': 'dialogue_speaker',
                    'inventory': 'inventory_selection'}.get(focus.context)
        chosen = {**candidate, 'name': name, 'evidence': evidence}
        if focus.context == 'dialogue':
            chosen['text'] = '\n'.join(focus.source_names)
        if name not in selected or float(chosen.get('confidence', 0)) > float(selected[name].get('confidence', 0)):
            selected[name] = chosen
    output.extend(selected.values())
    return output
