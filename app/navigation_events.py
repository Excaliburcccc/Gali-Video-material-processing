"""Conservative waypoint evidence for the opt-in Phantom Blade Zero UI profile.

An opened checkpoint menu establishes an interaction, not a first activation or
completed teleport. Map labels and bare place names deliberately produce nothing.
No known place name, recording timestamp or video-specific exception is used.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re

from .rules import OCRLine, clean_text, identity


_ACTIONS = {
    '穿行': '穿行', '能力修行': '能力修行', '保存记忆': '保存记忆',
    '保存記憶': '保存记忆', '装备升级': '装备升级', '裝備升級': '装备升级',
    '离开': '离开', '離開': '离开',
}
_ACTION = re.compile(r'^[^\u3400-\u9fff]{0,5}(穿行|能力修行|保存记忆|保存記憶|装备升级|裝備升級|离开|離開)$')
_MAP_CONTEXT = re.compile(r'地图完整度|地圖完整度|进入全域地图|進入全域地圖|查看收集详情|查看收集詳情')
_NAME_NOISE = re.compile(r'地图|地圖|完整度|查看|返回|确定|確定|确认|確認|取消|保存|修行|升级|升級|任务|任務|目标|目標|详情|詳情|按住|按下|离开|離開|穿行|解锁条件|解鎖條件')
_INSTRUCTION = re.compile(r'尚未|未能|未解锁|未解鎖|未激活|需要|必须|必須|如何|解锁条件|解鎖條件|前往|按下|按住')
_NOTICE = re.compile(
    r'^(?:(?:传送点|傳送點|残偶|殘偶)(?:已(?:激活|解锁|解鎖)|(?:激活|解锁|解鎖)成功)'
    r'|已(?:激活|解锁|解鎖)(?:传送点|傳送點|残偶|殘偶))\s*[:：!！\-]?\s*(.*)$'
)


@dataclass(frozen=True)
class _Line:
    text: str
    score: float
    left: float
    top: float
    right: float
    bottom: float

    @property
    def x(self) -> float:
        return (self.left + self.right) / 2

    @property
    def y(self) -> float:
        return (self.top + self.bottom) / 2


def _normalize(lines: list[OCRLine], height: int, width: int) -> list[_Line]:
    result = []
    for line in lines:
        try:
            if not line.box or len(line.box) < 4:
                continue
            xs = [float(p[0]) / width for p in line.box]
            ys = [float(p[1]) / height for p in line.box]
            score = float(line.score)
        except (TypeError, ValueError, IndexError, AttributeError, OverflowError):
            continue
        if not all(math.isfinite(v) for v in xs + ys + [score]) or score < .55:
            continue
        if min(xs) < -.02 or max(xs) > 1.02 or min(ys) < -.02 or max(ys) > 1.02:
            continue
        text = clean_text(line.text)
        if not text or len(text) > 200:
            continue
        result.append(_Line(text, min(1.0, score), min(xs), min(ys), max(xs), max(ys)))
    return sorted(result, key=lambda line: (line.top, line.left))


def _name(text: str) -> str | None:
    text = text.strip(' \t:：!！-—·•○●◇◆□[]【】')
    if (2 <= len(identity(text)) <= 32 and not _NAME_NOISE.search(text)
            and not _INSTRUCTION.search(text)
            and not re.search(r'[。！？!?；;]|\d\s*[/:%％]|[+×]\s*\d', text)
            and re.search(r'[\u3400-\u9fffA-Za-z]', text)):
        return text
    return None


def _menu_candidate(lines: list[_Line], timestamp: float) -> dict | None:
    actions = []
    for line in lines:
        match = _ACTION.fullmatch(line.text)
        if (match and .34 <= line.x <= .68 and .50 <= line.y <= .94
                and 0 < line.bottom - line.top <= .07):
            actions.append((_ACTIONS[match.group(1)], line))
    # Save-memory plus two adjacent options is distinctive, even when OCR misses
    # the highlighted travel option. Repeated boxes cannot supply extra options.
    for label, anchor in actions:
        if label != '保存记忆':
            continue
        cluster = [(kind, line) for kind, line in actions
                   if abs(line.left - anchor.left) <= .075
                   and abs(line.y - anchor.y) <= .24]
        by_kind = {}
        for kind, line in cluster:
            if kind not in by_kind or line.score > by_kind[kind].score:
                by_kind[kind] = line
        if len(by_kind) < 3:
            continue
        ordered = sorted(by_kind.values(), key=lambda line: line.y)
        if any(b.y - a.y < .018 or b.y - a.y > .12 for a, b in zip(ordered, ordered[1:])):
            continue
        # This menu's current checkpoint title is separate at the lower left.
        locations = [(line, _name(line.text)) for line in lines
                     if .0 <= line.left < .28 and line.right <= .38
                     and .78 <= line.y <= .97 and 0 < line.bottom - line.top <= .075]
        locations = [(line, name) for line, name in locations if name]
        if not locations:
            continue
        location, name = max(locations, key=lambda pair: (pair[0].score, pair[0].y))
        return {
            'kind': 'waypoint', 'name': name,
            'text': '残偶菜单交互；首次激活或完成传送待核实\n' + name + '\n'
                    + '\n'.join(line.text for line in ordered),
            'confidence': round(min(location.score, *(line.score for line in ordered)), 4),
            'evidence': 'waypoint_menu', 'panel_type': 'waypoint',
            'verified': 0, 'time': max(0.0, timestamp),
        }
    return None


def _notices(lines: list[_Line], timestamp: float) -> list[dict]:
    candidates = []
    for line in lines:
        # Exclude dialogue subtitles and lower menu controls. Only an explicit
        # past-tense activation/unlock notice qualifies, never an action prompt.
        if not (.10 <= line.y <= .74) or _INSTRUCTION.search(line.text):
            continue
        match = _NOTICE.fullmatch(line.text)
        if not match:
            continue
        name = _name(match.group(1)) if match.group(1) else None
        score = line.score
        text = line.text
        if match.group(1) and not name:
            continue
        if not name:
            following = [other for other in lines if 0 <= other.top - line.bottom <= .085
                         and abs(other.left - line.left) <= .08 and _name(other.text)]
            if following:
                other = min(following, key=lambda item: item.top - line.bottom)
                name = _name(other.text)
                score = min(score, other.score)
                text += '\n' + other.text
        candidates.append({
            'kind': 'waypoint', 'name': name or '传送点激活提示（名称未识别）',
            'text': '明确激活或解锁提示；自动识别待核实\n' + text,
            'confidence': round(score, 4), 'evidence': 'waypoint_notice',
            'panel_type': 'waypoint', 'verified': 0, 'time': max(0.0, timestamp),
        })
    return candidates


def detect_navigation(lines: list[OCRLine], frame_shape: tuple[int, int], timestamp: float) -> list[dict]:
    """Return waypoint menu/explicit unlock candidates, never map-label events.

    ``frame_shape`` is (height, width), in the same pixel space as OCR boxes.
    Calls are stateless, so reopening a menu later is a separate occurrence for
    the caller's time-window accumulator. Generic OCR remains searchable there.
    """
    try:
        height, width = int(frame_shape[0]), int(frame_shape[1])
        timestamp = float(timestamp)
    except (TypeError, ValueError, IndexError, OverflowError):
        return []
    if height <= 0 or width <= 0 or not math.isfinite(timestamp):
        return []
    normalized = _normalize(lines, height, width)
    # Map controls and progression labels establish a map view, not a visit.
    if any(_MAP_CONTEXT.search(line.text) for line in normalized):
        return []
    candidates = _notices(normalized, timestamp)
    menu = _menu_candidate(normalized, timestamp)
    if menu:
        candidates.append(menu)
    unique = {}
    for candidate in candidates:
        key = (candidate['evidence'], identity(candidate['name']))
        if key not in unique or candidate['confidence'] > unique[key]['confidence']:
            unique[key] = candidate
    return list(unique.values())
