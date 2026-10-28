"""Opt-in, spatial panel candidates for the Phantom Blade Zero demo UI.

A book detail, quest-item or new-weapon panel is useful evidence, not proof of acquisition.
The caller chooses the game profile; no filename, timestamp or known item name is
used here. Generic OCR text is retained separately by the normal analyzer.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re

from .rules import OCRLine, clean_text, identity

_BOOK_CATEGORIES = {'书籍', '書籍'}
_QUEST_ITEM_CATEGORIES = {'剧情道具', '劇情道具'}
_WEAPON_CATEGORIES = {'单手武器', '雙手武器', '双手武器', '單手武器', '魂器', '暗器'}
_MENU_LABEL = re.compile(r'背包|物品栏|物品欄|道具栏|道具欄|装备栏|裝備欄|成就列表|当前拥有|當前擁有|目前拥有|耗费|耗費|所需材料|升级材料|升級材料|升级|升級|强化|強化|购买|購買|出售|售价|售價')
_UPGRADE_ACTION = re.compile(r'(?:按住|消耗|耗费|耗費).{0,30}(?:升级|升級|强化|強化)|(?:升级|升級|强化|強化)(?:费用|費用|所需|材料)')
_TITLE_NOISE = re.compile(r'书籍|書籍|武器|魂器|伤害能力|傷害能力|破防能力|普通连招|普通連招|杀气连招|殺氣連招|装备新|裝備新|确认|確認|确定|確定|返回|查看|当前|當前|耗费|耗費')
_NEW_WEAPON = re.compile(r'装[备備]新(?:的)?武器')
_SENTENCE = re.compile(r'[。！？!?]|.{0,12}[:,：].{12,}')
_CONFIRM = re.compile(r'^[^\u3400-\u9fff]{0,5}(?:确定|確定|确认|確認)$')
_INVENTORY_TABS = {'地图','地圖','任务','任務','角色','道具','读物','讀物','系统','系統'}


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


def _normalize(lines: list[OCRLine], height: int, width: int) -> list[_Line]:
    output = []
    for line in lines:
        if not line.box or len(line.box) < 4:
            continue
        try:
            xs = [float(p[0]) / width for p in line.box]
            ys = [float(p[1]) / height for p in line.box]
            score = float(line.score)
        except (TypeError, ValueError, IndexError):
            continue
        if not all(math.isfinite(v) for v in xs + ys + [score]):
            continue
        text = clean_text(line.text)
        if not text or score < .5:
            continue
        output.append(_Line(text, min(1.0, max(0.0, score)), min(xs), min(ys), max(xs), max(ys)))
    return sorted(output, key=lambda line: (line.top, line.left))


def _eligible_title(line: _Line) -> bool:
    # Keep only a short heading, not a stat, a button row or a paragraph.
    return (2 <= len(identity(line.text)) <= 40 and line.left > .55 and line.right <= 1.02
            and line.bottom - line.top <= .09 and not _TITLE_NOISE.fullmatch(line.text)
            and not _SENTENCE.search(line.text)
            and not re.fullmatch(r'[\d\W_]+', line.text))


def _following(lines: list[_Line], anchor: _Line, max_gap: float) -> list[_Line]:
    return [line for line in lines if _eligible_title(line)
            and line.top >= anchor.bottom - .006
            and line.top - anchor.bottom <= max_gap
            and abs(line.left - anchor.left) <= .10]


def detect_panels(lines: list[OCRLine], frame_shape: tuple[int, int], timestamp: float) -> list[dict]:
    """Return unverified item candidates from category + adjacent heading structure.

    ``frame_shape`` is (height, width), in the same pixel space as OCR boxes.
    Book panels require a category, main heading and separate chapter heading.
    Weapons additionally require the explicit new-weapon equip question. Quest
    items require their category, adjacent heading and lower-right confirmation.
    Inventory and upgrade UI text veto panels. No bare name is acquisition evidence.
    """
    try:
        height, width = int(frame_shape[0]), int(frame_shape[1])
        timestamp = float(timestamp)
    except (TypeError, ValueError, IndexError):
        return []
    if height <= 0 or width <= 0 or not math.isfinite(timestamp):
        return []
    normalized = _normalize(lines, height, width)
    # Descriptions can mention "强化攻击" without being an upgrade menu.
    if any(_MENU_LABEL.fullmatch(line.text) or _UPGRADE_ACTION.search(line.text) for line in normalized):
        return []
    inventory_tabs={line.text for line in normalized
                    if line.center_y<.10 and line.text in _INVENTORY_TABS}
    quest_menu= len(inventory_tabs)>=3 or any(
        line.left>.60 and line.center_y>.80
        and re.fullmatch(r'[^\u3400-\u9fff]{0,5}(?:回退|回退至始|返还|返還)',line.text)
        for line in normalized)
    candidates = []
    for category in normalized:
        if not (category.left > .55 and .12 <= category.center_y <= .30):
            continue
        if category.text not in _BOOK_CATEGORIES | _WEAPON_CATEGORIES | _QUEST_ITEM_CATEGORIES:
            continue
        titles = _following(normalized, category, .105)
        if not titles:
            continue
        title = titles[0]
        if category.text in _BOOK_CATEGORIES:
            chapters = _following(normalized, title, .08)
            # Body prose is lower in this panel, following a divider and pagination.
            chapters = [line for line in chapters if line.center_y <= .40 and line.text != title.text]
            if not chapters:
                continue
            chapter = chapters[0]
            candidates.append({
                'kind': 'item', 'name': chapter.text,
                'text': '书籍展示面板；首次获取待核实\n' + '\n'.join((category.text, title.text, chapter.text)),
                'confidence': round(min(category.score, title.score, chapter.score), 4),
                'evidence': 'item_panel', 'panel_type': 'book', 'verified': 0,
                'time': max(0.0, timestamp),
            })
        elif category.text in _QUEST_ITEM_CATEGORIES:
            if quest_menu or category.center_y>.26 or title.center_y>.32:
                continue
            confirms=[line for line in normalized if line.left>.75
                      and .80<=line.center_y<=.99 and line.bottom-line.top<=.08
                      and _CONFIRM.fullmatch(line.text)]
            if not confirms:
                continue
            confirm=max(confirms,key=lambda line:line.score)
            candidates.append({
                'kind':'item','name':title.text,
                'text':'剧情道具展示面板；首次获取待核实\n'+'\n'.join((category.text,title.text,confirm.text)),
                'confidence':round(min(category.score,title.score,confirm.score),4),
                'evidence':'item_panel','panel_type':'quest_item','verified':0,
                'time':max(0.0,timestamp),
            })
        else:
            questions = [line for line in normalized if line.left > .55 and line.top >= .62
                         and _NEW_WEAPON.search(line.text)]
            if not questions:
                continue
            question = questions[0]
            candidates.append({
                'kind': 'item', 'name': title.text,
                'text': '武器展示面板；首次获取待核实\n' + '\n'.join((category.text, title.text, question.text)),
                'confidence': round(min(category.score, title.score, question.score), 4),
                'evidence': 'item_panel', 'panel_type': 'weapon', 'verified': 0,
                'time': max(0.0, timestamp),
            })
    unique = {}
    for candidate in candidates:
        key = (candidate['panel_type'], identity(candidate['name']))
        if key not in unique or candidate['confidence'] > unique[key]['confidence']:
            unique[key] = candidate
    return list(unique.values())
