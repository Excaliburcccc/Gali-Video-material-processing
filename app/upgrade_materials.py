"""Exclude material balance changes shown inside the weapon rollback screen.

This layout check is specific to the opt-in game UI adapter. It uses menu
controls and OCR boxes, never a material dictionary or recording timestamp.
Ordinary pickups outside the upgrade/rollback interface are unaffected.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re

from .rules import clean_text, identity


_DELTA = re.compile(r'^(?P<name>[\u3400-\u9fffA-Za-z][\u3400-\u9fffA-Za-z0-9· ]{1,39}?)\s*[+\-−]\s*\d+(?:[.,]\d+)?$')
_PREFIX = re.compile(r'^(?:获得物品|獲得物品|已获得|已獲得|获得|獲得|取得|拾取|返还|返還|退还|退還)\s*[:：]?\s*')
_NOISE = re.compile(r'伤害|傷害|破防|等级|等級|升级|升級|耗费|耗費|消耗|当前|當前|拥有|擁有')


@dataclass(frozen=True)
class _Line:
    text: str
    score: float
    left: float
    top: float
    right: float
    bottom: float

    @property
    def x(self): return (self.left+self.right)/2

    @property
    def y(self): return (self.top+self.bottom)/2


def _lines(lines, shape):
    try:
        height, width = float(shape[0]), float(shape[1])
        if not all(math.isfinite(v) and v > 0 for v in (height, width)): return []
    except (TypeError, ValueError, IndexError):
        return []
    result=[]
    for line in lines or ():
        try:
            if not line.box or len(line.box)<4: continue
            xs=[float(p[0])/width for p in line.box]
            ys=[float(p[1])/height for p in line.box]
            score=float(line.score)
            if not all(math.isfinite(v) for v in xs+ys+[score]) or score<.55: continue
            if min(xs)<-.02 or max(xs)>1.02 or min(ys)<-.02 or max(ys)>1.02: continue
            text=clean_text(line.text)
            if text and len(text)<=200:
                result.append(_Line(text,score,min(xs),min(ys),max(xs),max(ys)))
        except (TypeError,ValueError,IndexError,AttributeError,OverflowError):
            continue
    return result


def _label(value):
    # Controller icons beside a menu action can be OCR'd as punctuation/Latin.
    return re.sub(r'^[^\u3400-\u9fff]+','',clean_text(value)).strip()


def detect_upgrade_materials(lines, frame_shape):
    """Return material-change evidence only in the verified rollback layout.

    Results have ``name`` (without the delta), ``text`` (original OCR line),
    confidence, and evidence='upgrade_material'. The helper can also tag saved
    unreviewed OCR rows during an existing-library refinement.
    """
    observed=_lines(lines,frame_shape)
    tabs={_label(line.text) for line in observed
          if .28<=line.x<=.72 and 0<=line.y<=.10}
    rollback=any(_label(line.text) in {'回退','回退至始','返还','返還'}
                 and line.left>=.60 and .85<=line.y<=.99 for line in observed)
    if not ({'武器','影之武装'}<=tabs or {'武器','影之武裝'}<=tabs) or not rollback:
        return []
    result={}
    for line in observed:
        if not (line.left>=.70 and .35<=line.y<=.82 and 0<line.bottom-line.top<=.075): continue
        value=_PREFIX.sub('',_label(line.text))
        match=_DELTA.fullmatch(value)
        if not match: continue
        name=clean_text(match.group('name'))
        if _NOISE.search(name): continue
        record={'name':name,'text':line.text,'confidence':round(line.score,4),'evidence':'upgrade_material'}
        key=identity(line.text)
        if key not in result or result[key]['confidence']<record['confidence']: result[key]=record
    return list(result.values())


def _material_key(value):
    value=_PREFIX.sub('',clean_text(value))
    match=_DELTA.fullmatch(value)
    return identity(match.group('name') if match else value)


def is_upgrade_material(candidate, evidence):
    """Whether this ordinary candidate corresponds to a detected balance row."""
    if candidate.get('kind') not in {'text','item'} or candidate.get('source')=='manual': return False
    if candidate.get('evidence') in {'inventory_selection','item_panel'}: return False
    names={identity(e['name']) for e in evidence}
    raw={identity(e['text']) for e in evidence}
    values=[candidate.get('name',''),*str(candidate.get('text','')).splitlines()]
    return any(value and (identity(value) in raw or _material_key(value) in names) for value in values)


def filter_upgrade_materials(candidates, lines, frame_shape):
    """Drop rollback material text/item candidates, preserving all other kinds."""
    evidence=detect_upgrade_materials(lines,frame_shape)
    if not evidence: return list(candidates)
    return [candidate for candidate in candidates if not is_upgrade_material(candidate,evidence)]
