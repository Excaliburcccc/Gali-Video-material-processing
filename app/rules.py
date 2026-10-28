"""Conservative notification rules. All matches are candidates, not proof of completion."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable


@dataclass
class OCRLine:
    text: str
    score: float = 1.0
    box: list | None = None


def clean_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text)).strip()
    # OCR sometimes inserts spaces between every Chinese character.
    value = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", value)
    return re.sub(r"\s+", " ", value)


def identity(text: str) -> str:
    return re.sub(r"[\W_]+", "", clean_text(text).casefold(), flags=re.UNICODE)


_MENU_CONTEXT = re.compile(r"(?:获得|取得|拾取|击败|解锁)(?:方式|条件|方法|途径|要求)|(?:需要|必须|前往|请先|如何|尚未|未能|未曾|未获得|未解锁|未完成|未达成|未击败|尚需|可获得|可以获得|首次击败后)")
_MENU_LABEL = re.compile(r"^(?:背包|物品栏|道具栏|成就列表|成就进度|任务目标|任务详情|收集进度|获取途径|获得方式|解锁条件)$")
_COUNT = re.compile(r"(?:[xX×*]\s*\d+|数量\s*[:：]?\s*\d+)\s*$")
_PROGRESS = re.compile(r"\d+\s*/\s*\d+")
_NOISE = re.compile(r"^(?:[\d\W_]+|HP|MP|FPS|返回|确认|取消|继续|关闭|设置|菜单|暂停|确定|下一页|上一页|装备|使用|丢弃|排序|筛选)$", re.I)

_DEFAULT_RULES = [
    ("achievement", re.compile(r"^(?:成就(?:已)?(?:解锁|达成|完成)|(?:解锁|达成|完成)(?:了)?成就|(?:ACHIEVEMENT|TROPHY)\s+(?:UNLOCKED|EARNED))\s*[:：!！\-]?\s*(.*)$", re.I)),
    ("achievement", re.compile(r"^(.+?)\s*[:：\-]?\s*(?:成就(?:已)?(?:解锁|达成)|成就完成)$", re.I)),
    ("enemy", re.compile(r"^(?:已击败|成功击败|击败了|已击破|成功讨伐|讨伐成功|战斗胜利|战斗成功|首领已击败|BOSS\s*DEFEATED|ENEMY\s*(?:DEFEATED|FELLED))\s*[:：!！\-]?\s*(.*)$", re.I)),
    ("enemy", re.compile(r"^(.+?)\s*[:：\-]?\s*(?:已击败|已击破|讨伐成功|击败成功)$", re.I)),
    ("enemy", re.compile(r"^击败\s*[:：]\s*(.+)$", re.I)),
    ("item", re.compile(r"^(?:获得物品|獲得物品|取得物品|拾取物品|已获得|已獲得|已取得|已拾取|收集到|获得了|获得|獲得|取得|拾取|ITEM\s*(?:OBTAINED|ACQUIRED)|OBTAINED)\s*[:：!！\-]?\s*(.*)$", re.I)),
    ("item", re.compile(r"^(.+?)\s*[:：\-]?\s*(?:已获得|已獲得|已取得|已拾取)$", re.I)),
]


def _name(text: str) -> str:
    text = _COUNT.sub("", clean_text(text)).strip(" :：!！-—【】[]「」『』\"' ")
    return text[:160]


def _nearby(a: OCRLine, b: OCRLine) -> bool:
    if not a.box or not b.box:
        return True
    ax = [float(p[0]) for p in a.box]
    ay = [float(p[1]) for p in a.box]
    bx = [float(p[0]) for p in b.box]
    by = [float(p[1]) for p in b.box]
    height = max(max(ay) - min(ay), max(by) - min(by), 12)
    vertical_gap = min(by) - max(ay)
    x_overlap = min(max(ax), max(bx)) - max(min(ax), min(bx))
    centers_close = abs((min(ax) + max(ax) - min(bx) - max(bx)) / 2) < max(80, 3 * height)
    return -height <= vertical_gap <= 4 * height and (x_overlap > 0 or centers_close)


def classify_lines(lines: Iterable[OCRLine | str], keywords: dict | None = None, include_text: bool = True) -> list[dict]:
    """Return explicit-notification candidates plus searchable unclassified OCR lines.

    Custom keywords are literal notification prefixes, not object-name dictionaries:
    {"item": ["得到道具"], "enemy": ["斩杀完成"], "achievement": ["奖杯获得"]}.
    A bare objective such as ``击败敌人 A`` deliberately stays searchable text.
    """
    items = [line if isinstance(line, OCRLine) else OCRLine(str(line)) for line in lines]
    items = [OCRLine(clean_text(x.text), max(0.0, min(1.0, float(x.score))), x.box) for x in items]
    items = [x for x in items if x.text and len(x.text) <= 500]
    rules = list(_DEFAULT_RULES)
    if keywords:
        for kind in ("achievement", "enemy", "item"):
            for keyword in (keywords.get(kind) or [])[:50]:
                keyword = clean_text(keyword)
                if len(keyword) >= 2:
                    rules.insert(0, (kind, re.compile(r"^" + re.escape(keyword) + r"\s*[:：!！\-]?\s*(.*)$", re.I)))
    results: list[dict] = []
    consumed: set[int] = set()
    for index, line in enumerate(items):
        if _MENU_CONTEXT.search(line.text) or _MENU_LABEL.match(line.text) or _PROGRESS.search(line.text):
            continue
        for kind, rule in rules:
            match = rule.match(line.text)
            if not match:
                continue
            name = _name(match.group(1))
            evidence = line.text
            confidence = line.score
            used = {index}
            if not name:
                # The name on the line immediately below must be spatially adjacent.
                for candidate_index in range(index + 1, min(len(items), index + 3)):
                    candidate = items[candidate_index]
                    if not _nearby(line, candidate):
                        continue
                    if _MENU_CONTEXT.search(candidate.text) or _MENU_LABEL.match(candidate.text) or _NOISE.match(candidate.text):
                        continue
                    if any(pattern.match(candidate.text) for _, pattern in rules):
                        continue
                    name = _name(candidate.text)
                    evidence += "\n" + candidate.text
                    confidence = min(confidence, candidate.score)
                    used.add(candidate_index)
                    break
            if not name:
                name = {"item": "物品获得提示（名称未识别）", "enemy": "战斗胜利提示（名称未识别）", "achievement": "成就解锁提示（名称未识别）"}[kind]
            if _MENU_CONTEXT.search(name) or _PROGRESS.search(name):
                break
            consumed.update(used)
            results.append({"kind": kind, "name": name, "text": evidence, "confidence": round(confidence, 4)})
            break
    if include_text:
        for index, line in enumerate(items):
            if index in consumed or len(identity(line.text)) < 2 or _NOISE.match(line.text):
                continue
            results.append({"kind": "text", "name": line.text[:160], "text": line.text, "confidence": round(line.score, 4)})
    unique = {}
    for result in results:
        key = (result["kind"], identity(result["name"]))
        if key not in unique or result["confidence"] > unique[key]["confidence"]:
            unique[key] = result
    return list(unique.values())
