"""Conservative quest notices and task-menu views for the opt-in demo profile.

Objective wording is a search anchor, never proof that an objective was met.
This module uses OCR text and layout only; no known quest names or video times.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re

from .rules import OCRLine, clean_text, identity

_OBJECTIVE = re.compile(
    r'^(?:查明|调查|調查|寻找|尋找|找到|前往|抵达|抵達|到达|到達|返回|进入|進入|潜入|潛入|'
    r'击败|擊敗|消灭|消滅|击杀|擊殺|救出|救援|营救|營救|护送|護送|打开|打開|收集|取回|'
    r'帮助.{1,20}(?:找到|寻找|尋找|收集|击败|擊敗)|'
    r'幫助.{1,20}(?:找到|寻找|尋找|收集|击败|擊敗)|'
    r'将.{1,30}(?:交给|交給|送到|带回|帶回)|'
    r'將.{1,30}(?:交给|交給|送到|带回|帶回)|'
    r'在.{1,30}(?:寻找|尋找|找到|调查|調查)|与.{1,20}交谈|與.{1,20}交談)')
_ITEM_CATEGORY = {'书籍', '書籍', '单手武器', '雙手武器', '双手武器', '單手武器', '魂器', '暗器', '钥匙', '鑰匙'}
_NAV_LABELS = {'地图', '地圖', '角色', '道具', '读物', '讀物', '系统', '系統'}
_QUEST_CATEGORIES = {'故事', '主线', '主線', '支线', '支線', '委托', '委託', '任务目标', '任務目標'}
_NOTICE = re.compile(r'^(任务已完成|任務已完成|任务完成|任務完成|已完成任务|已完成任務|'
                     r'任务已接取|任務已接取|已接取任务|已接取任務|接受任务|接受任務|'
                     r'新任务|新任務|任务更新|任務更新|任务已更新|任務已更新)'
                     r'(?:\s*[:：!！\-]\s*(.+))?$')
_NOT_NOTICE = re.compile(r'尚未|未完成|完成后|完成後|完成条件|完成條件|完成方法|如何|需要|必须|必須|成就')


@dataclass(frozen=True)
class _Line:
    text: str
    score: float
    left: float
    top: float
    right: float
    bottom: float

    @property
    def center_y(self):
        return (self.top + self.bottom) / 2


def _normalized(lines, height, width):
    output = []
    for line in lines:
        if not line.box or len(line.box) < 4:
            continue
        try:
            xs = [float(point[0]) / width for point in line.box]
            ys = [float(point[1]) / height for point in line.box]
            score = float(line.score)
        except (TypeError, ValueError, IndexError):
            continue
        text = clean_text(line.text)
        if not text or score < .5 or not all(math.isfinite(value) for value in xs + ys + [score]):
            continue
        item = _Line(text, min(score, 1.), min(xs), min(ys), max(xs), max(ys))
        if not any(identity(old.text) == identity(item.text) and abs(old.top-item.top) < .012
                   and abs(old.left-item.left) < .025 for old in output):
            output.append(item)
    return sorted(output, key=lambda line: (line.top, line.left))


def _is_objective(line):
    return 7 <= len(identity(line.text)) <= 100 and bool(_OBJECTIVE.search(line.text)) and not _NOT_NOTICE.search(line.text)


def _continuation(lines, first, max_y):
    """Join a wrapped objective only while the source has an open parenthesis."""
    parts = [first]
    while len(parts) < 3 and ''.join(part.text for part in parts).count('(') > ''.join(part.text for part in parts).count(')'):
        anchor = parts[-1]
        following = [line for line in lines if line.top >= anchor.bottom-.003
                     and line.top-anchor.bottom <= .035 and line.bottom <= max_y
                     and abs(line.left-anchor.left) <= .035 and line is not anchor]
        if not following:
            break
        parts.append(following[0])
    return parts


def _candidate(parts, timestamp, evidence, description, title=None):
    name = ''.join(line.text for line in parts).strip()
    source = ([title.text] if title else []) + [line.text for line in parts]
    return {'kind': 'quest', 'name': name, 'time': max(0., timestamp),
            'text': description + '\n' + '\n'.join(source),
            'confidence': round(min(line.score for line in parts), 4),
            'verified': 0, 'evidence': evidence}


def detect_quests(lines: list[OCRLine], frame_shape: tuple[int, int], timestamp: float) -> list[dict]:
    """Recognize objective notices, task-menu views and explicit completion toasts.

    Input boxes and (height, width) must share a pixel coordinate space. Notices
    without explicit acceptance/update/completion labels retain neutral wording.
    """
    try:
        height, width = int(frame_shape[0]), int(frame_shape[1])
        timestamp = float(timestamp)
    except (TypeError, ValueError, IndexError):
        return []
    if height <= 0 or width <= 0 or not math.isfinite(timestamp):
        return []
    normalized = _normalized(lines, height, width)
    top_nav = {line.text for line in normalized if line.top < .12}
    quest_category = any(line.text in _QUEST_CATEGORIES and line.left < .45 and line.top < .35 for line in normalized)
    has_task_tab = bool({'任务', '任務'} & top_nav)
    menu = (len(top_nav & _NAV_LABELS) >= 3 and (quest_category or has_task_tab))
    results = []
    if menu:
        # A menu is a view, even if it contains completed objectives or tracking.
        for objective in normalized:
            if objective.left < .40 and .73 <= objective.center_y <= .91 and _is_objective(objective):
                titles = [line for line in normalized if .70 <= line.top < objective.top
                          and objective.top-line.bottom < .10 and abs(line.left-objective.left) < .04
                          and 2 <= len(identity(line.text)) <= 24]
                results.append(_candidate(_continuation(normalized, objective, .93), timestamp, 'quest_view',
                                          '任务查看；当前目标，未判定接取或完成', titles[-1] if titles else None))
        return _unique(results)

    # Item descriptions share the right side but have a distinct category label.
    if any(line.text in _ITEM_CATEGORY and line.left > .55 and .12 <= line.center_y <= .30 for line in normalized):
        return []

    for marker in normalized:
        if not (.10 <= marker.center_y <= .70) or _NOT_NOTICE.search(marker.text):
            continue
        match = _NOTICE.fullmatch(marker.text)
        if not match:
            continue
        label, inline_name = match.groups()
        title = None
        if inline_name:
            title = _Line(inline_name, marker.score, marker.left, marker.top, marker.right, marker.bottom)
        else:
            nearby = [line for line in normalized if line.top >= marker.bottom-.005 and line.top-marker.bottom <= .09
                      and abs(line.left-marker.left) <= .10 and 2 <= len(identity(line.text)) <= 60
                      and not _NOTICE.fullmatch(line.text) and not _NOT_NOTICE.search(line.text)]
            title = nearby[0] if nearby else None
        if not title:
            continue
        completed = '完成' in label
        results.append(_candidate([title], timestamp, 'quest_completed' if completed else 'quest_notice',
                                  ('明确任务完成提示；自动识别待核实' if completed else '任务提示：' + label + '；自动识别待核实')))
        results[-1]['text'] += '\n' + marker.text
        results[-1]['confidence'] = round(min(marker.score, title.score), 4)

    if results:
        return _unique(results)
    for objective in normalized:
        # Demo notice banner: objective baseline sits below the short title.
        # Keep this narrow to reject bottom subtitles and right-side prose panels.
        if not (.72 <= objective.left <= .82 and .29 <= objective.center_y <= .35
                and objective.right <= .98 and objective.bottom-objective.top <= .055 and _is_objective(objective)):
            continue
        titles = [line for line in normalized if .23 <= line.center_y < .29
                  and 0 <= objective.top-line.bottom <= .055 and abs(line.left-objective.left) < .035
                  and 2 <= len(identity(line.text)) <= 20]
        parts = _continuation(normalized, objective, .40)
        results.append(_candidate(parts, timestamp, 'quest_notice', '任务提示；未判定接取、更新或完成',
                                  titles[-1] if titles else None))
    return _unique(results)


def _unique(results):
    unique = {}
    for result in results:
        key = (result['evidence'], identity(result['name']))
        if key not in unique or result['confidence'] > unique[key]['confidence']:
            unique[key] = result
    return list(unique.values())
