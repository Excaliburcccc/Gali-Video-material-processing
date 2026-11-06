"""Local, scene-gated OCR of small collection titles.

Gold rank badges are removed only in a structured weapon title slot. Map
labels require both the bell pictogram and its selection cursor; neither a
world title nor an unselected point can establish a bell event.
"""
from functools import lru_cache
from pathlib import Path
import re
import threading
import weakref

from .rules import OCRLine
from .scene_focus import _lines, _map_name

SHADOW_CATEGORIES = frozenset({'武学', '武學', '远程', '遠程', '破坏', '破壞'})
_WEAPON_CATEGORY = re.compile(r'^(?:单手|雙手|双手|單手|双持|雙持|双刀|雙刀)武器$')
_STATS = re.compile(r'(?:伤害能力|傷害能力|破防能力).*\d')
_MAP = re.compile(r'地图完整度|地圖完整度|查看收集详情|查看收集詳情|进入全域地图|進入全域地圖')
_MENU_TABS = {'地图','地圖','任务','任務','角色','道具','读物','讀物','系统','系統'}
_ITEM_HEADERS = {'剧情道具', '劇情道具', '任务物品', '任務物品',
                 '妙手小张宝藏', '妙手小張寶藏', '炒手小张宝藏', '妙手小张的宝藏', '妙手小張的寶藏'}
_LOCAL = threading.local()


def is_equipment_menu(observed):
    """Recognize inventory/upgrade navigation, never the shared item stats.

    A world pickup uses the same title and damage stats as the inventory.
    Navigation across pages and inventory-only footer controls establish the
    menu; the weapon title itself cannot establish acquisition.
    """
    top = {line.text for line in observed if line.y < .10}
    if len(top & _MENU_TABS) >= 2:
        return True
    upgrade_tabs = {'武器', '影之武装', '影之武裝', '饰品', '飾品', '药瓶', '藥瓶'}
    if len(top & upgrade_tabs) >= 2:
        return True
    footer = ' '.join(line.text for line in observed if line.y > .90)
    upgrade_action = any(line.y > .70 and ('最大升级' in line.text or
                         ('消耗' in line.text and '升级' in line.text)) for line in observed)
    if top & upgrade_tabs and ('回退' in footer or upgrade_action) and is_weapon_panel(observed):
        return True
    return is_weapon_panel(observed) and (
        ('装备' in footer or '裝備' in footer) and ('卸下' in footer or '查看进阶' in footer))


def is_weapon_panel(observed):
    categories = [line for line in observed if .60 <= line.left <= .76 and .13 <= line.y <= .20
                  and (line.text in SHADOW_CATEGORIES or _WEAPON_CATEGORY.fullmatch(line.text))]
    stats = [line for line in observed if line.left >= .60 and .24 <= line.y <= .33 and _STATS.search(line.text)]
    distinct = {'damage' if '伤害' in line.text or '傷害' in line.text else 'guard' for line in stats}
    # Acquisition screens can lose their tiny category heading in full-frame
    # OCR. Both numeric weapon stats still provide a bounded title-panel gate.
    return bool(categories and stats) or len(distinct) == 2


def is_map_panel(observed):
    return sum(bool(_MAP.search(line.text)) and line.y > .76 for line in observed) >= 2


def needs_story_item_refinement(observed):
    """Re-read a near-miss category only in the compact item-heading layout.

    A one-character OCR error is a crop gate, never classification evidence.
    The focused pass must still read the exact game category before accepting it.
    """
    def near_label(text):
        if text in _ITEM_HEADERS:
            return False
        for target in _ITEM_HEADERS:
            if len(text) == len(target) and sum(a != b for a,b in zip(text,target)) == 1:
                return True
            longer,shorter = (text,target) if len(text)>len(target) else (target,text)
            if len(longer) == len(shorter)+1 and any(longer[:i]+longer[i+1:] == shorter for i in range(len(longer))):
                return True
        return False

    for category in observed:
        if not (.65 <= category.left <= .76 and .18 <= category.y <= .22
                and category.bottom-category.top <= .04 and near_label(category.text)):
            continue
        if any(abs(title.left-category.left) <= .025 and .02 <= title.y-category.y <= .075
               and title.bottom <= .29 and title.bottom-title.top >= (category.bottom-category.top)*1.15
               and re.search(r'[\u3400-\u9fff]{2}',title.text) for title in observed):
            return True
    return False


def is_world_book_panel(observed,frame):
    """Require the heading slot and the horizontal book-page diamonds.

    This visual gate recovers tiny missing '书籍' labels without OCR of every
    text-heavy frame. The inventory navigation always excludes library views.
    """
    if len({line.text for line in observed if line.y<.10 and line.text in _MENU_TABS}) >= 3:
        return False
    if not any(.65<=line.left<=.84 and .19<=line.y<=.31 and re.search(r'[\u3400-\u9fff]{2}',line.text)
               for line in observed):
        return False
    import cv2
    import numpy as np
    height,width=frame.shape[:2]
    patch=frame[int(.322*height):int(.378*height),int(.65*width):int(.96*width)]
    if not patch.size:
        return False
    gray=cv2.cvtColor(patch,cv2.COLOR_BGR2GRAY) if patch.ndim==3 else patch
    gray=cv2.resize(gray,(496,50))
    _,mask=cv2.threshold(gray,50,255,cv2.THRESH_BINARY)
    centers=[]
    for contour in cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]:
        x,y,w,h=cv2.boundingRect(contour)
        if 7<=w<=24 and 10<=h<=27 and .25<cv2.contourArea(contour)/(w*h)<.85:
            centers.append((x+w/2,y+h/2))
    for _,cy in centers:
        row=sorted(x for x,y in centers if abs(y-cy)<=3)
        # Repeated diamonds have a regular ~20 px pitch; missing dark page
        # markers may leave a multiple of the same pitch.
        aligned=[(a,b) for a,b in zip(row,row[1:]) if any(abs((b-a)-20.4*n)<=3 for n in (1,2,3))]
        if len(aligned)>=2:
            return True
    return False


@lru_cache(maxsize=1)
def _bell_template():
    import cv2
    import numpy as np
    path = Path(__file__).with_name('assets') / 'selected_bell.png'
    template = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
    return template


@lru_cache(maxsize=1)
def _bell_templates():
    import cv2
    import numpy as np
    variants = [(_bell_template(), (40,40), (29,22,20,35))]
    for filename, anchor, bounds in (
            ('selected_bell_travel.png',(43,40),(28,20,30,39)),
            ('selected_bell_dark.png',(43,40),(28,20,30,39)),
            ('selected_bell_current.png',(79,40),(65,20,30,39))):
        path = Path(__file__).with_name('assets') / filename
        template = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
        variants.append((template,anchor,bounds))
    return variants


def selected_bell_location(frame):
    """Return normalized (x, y, score), or None. Holds at most a weak frame ref."""
    if frame is None:
        return None
    previous = getattr(_LOCAL, 'bell', None)
    if previous and previous[0]() is frame:
        return previous[1]
    import cv2
    import numpy as np
    height, width = frame.shape[:2]
    if min(height, width) < 100:
        return None
    scale = 900 / height
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    gray = cv2.resize(gray, (round(width*scale), 900))
    # Both the ring and the bell itself must match. A challenge/quest icon
    # inside the same cursor is deliberately not accepted.
    x0, y0 = int(gray.shape[1]*.04), 100
    region = gray[y0:730, x0:int(gray.shape[1]*.96)]
    result = None
    for template, (cx,cy), (bx,by,bw,bh) in _bell_templates():
        match = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, position = cv2.minMaxLoc(match)
        if score < .64:
            continue
        x, y = position
        # Icon edges shift slightly while the cursor animates. Check a local
        # two-pixel neighborhood without accepting a different map marker.
        patch = region[max(0,y+by-2):y+by+bh+2, max(0,x+bx-2):x+bx+bw+2]
        bell = template[by:by+bh, bx:bx+bw]
        icon_score = float(cv2.matchTemplate(patch, bell, cv2.TM_CCOEFF_NORMED).max())
        if np.isfinite(icon_score) and icon_score >= .67:
            confidence = min(float(score), icon_score)
            if result is None or confidence > result[2]:
                result = ((x+x0+cx)/gray.shape[1], (y+y0+cy)/900, confidence)
    _LOCAL.bell = (weakref.ref(frame), result)
    return result


def _read_region(frame, region, engine, *, badge=False, upscale=1, confidence=.55):
    import cv2
    import numpy as np
    height, width = frame.shape[:2]
    x, y, right, bottom = [int(value*size) for value, size in zip(region, (width,height,width,height))]
    x, y, right, bottom = max(0,x), max(0,y), min(width,right), min(height,bottom)
    crop = frame[y:bottom, x:right].copy()
    if not crop.size:
        return []
    if badge:
        # The narrow gold outline encloses the rank digit. Remove the whole
        # badge so OCR cannot append that digit or an invented CJK character.
        mask = cv2.inRange(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV), np.array([12,15,70]), np.array([45,255,255]))
        mask[:max(0,int(.175*height)-y)] = 0
        mask[max(0,int(.25*height)-y):] = 0
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3,3), np.uint8))
        for contour in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
            bx, by, bw, bh = cv2.boundingRect(contour)
            if (.015*height < bh < .07*height and .004*width < bw < .04*width
                    and .4 <= bw/bh <= .9):
                crop[max(0,by-2):by+bh+2, max(0,bx-2):bx+bw+2] = 0
    if upscale != 1:
        crop = cv2.resize(crop, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    recognized, _ = engine(crop, use_cls=False)
    return [OCRLine(str(text), float(score), [[float(p[0])/upscale+x, float(p[1])/upscale+y] for p in box])
            for box,text,score,*_ in recognized or [] if float(score) >= confidence]


def refine_collection_lines(lines, frame, engine):
    """Return existing readings plus one gated title crop, no generic re-scan."""
    observed = _lines(lines, frame.shape)
    result = list(lines)
    if any(line.left >= .60 and .13 <= line.y <= .32 and
           re.search(r'[记記][忆憶]之花', line.text) for line in observed):
        # The petal category is tiny, while the chapter below the flower name
        # distinguishes different collectibles. Keep the full heading block.
        extra = _read_region(frame, (.64,.13,.99,.48), engine, upscale=2)
        if any(re.search(r'[记記][忆憶]之花', item.text) for item in _lines(extra, frame.shape)):
            result = [line for line in result if not any(item.left >= .64 and .13 <= item.y <= .48
                      for item in _lines([line], frame.shape))]
            result.extend(extra)
    elif needs_story_item_refinement(observed):
        extra = _read_region(frame,(.65,.15,.99,.29),engine,upscale=2,confidence=.55)
        if any(item.text in _ITEM_HEADERS for item in _lines(extra,frame.shape)):
            result = [line for line in result if not any(item.left >= .65 and .15 <= item.y <= .29
                      for item in _lines([line],frame.shape))]
            result.extend(extra)
    elif is_world_book_panel(observed,frame):
        extra=_read_region(frame,(.65,.15,.99,.34),engine,upscale=2,confidence=.55)
        if any(item.text in {'书籍','書籍'} for item in _lines(extra,frame.shape)):
            # Replace the same header slot, avoiding old truncated titles.
            result=[line for line in result if not any(item.left>=.65 and .15<=item.y<=.335
                    for item in _lines([line],frame.shape))]
            result.extend(extra)
    elif is_weapon_panel(observed) and not is_equipment_menu(observed):
        extra = _read_region(frame, (.64,.13,.97,.33), engine, badge=True, upscale=2)
        # Replace only the title slot when the focused pass actually found a
        # title. This avoids retaining a badge-corrupted alternative identity.
        titles = [line for line in _lines(extra, frame.shape) if .185 <= line.y <= .25 and line.left >= .65
                  and re.search(r'[\u3400-\u9fff]{2}', line.text)]
        if not titles:
            # A short brush-font title may survive at native size when the
            # enlarged version is below threshold; try only this small crop.
            extra = _read_region(frame, (.64,.13,.97,.33), engine, badge=True)
            titles = [line for line in _lines(extra, frame.shape) if .185 <= line.y <= .25 and line.left >= .65
                      and re.search(r'[\u3400-\u9fff]{2}', line.text)]
        if titles:
            result = [line for line in result if not any(.185 <= item.y <= .25 and item.left >= .65
                       for item in _lines([line], frame.shape))]
            result.extend(extra)
    elif is_map_panel(observed):
        selected = selected_bell_location(frame)
        if selected:
            x,y,_ = selected
            extra = _read_region(frame, (x+.004,y-.032,min(.98,x+.30),min(.84,y+.035)), engine, upscale=2, confidence=.6)
            focused_titles = [item for item in _lines(extra,frame.shape)
                              if x+.008 <= item.left <= x+.09 and abs(item.y-y)<=.026 and _map_name(item.text)]
            if focused_titles:
                result = [line for line in result if not any(x+.008 <= item.left <= x+.09 and abs(item.y-y)<=.026
                           for item in _lines([line],frame.shape))]
            result.extend(extra)
    return result


def detect_selected_bell(lines, frame, timestamp):
    if frame is None:
        return []
    observed = _lines(lines, frame.shape)
    if not is_map_panel(observed):
        return []
    selected = selected_bell_location(frame)
    if not selected:
        return []
    x,y,score = selected
    titles = [(line, _map_name(line.text)) for line in observed
              if x+.008 <= line.left <= x+.09 and abs(line.y-y) <= .026
              and line.right <= min(.99,x+.34) and line.bottom-line.top <= .05]
    titles = [(line,name) for line,name in titles if name]
    if not titles:
        return []
    line, name = min(titles, key=lambda pair: (abs(pair[0].y-y), -pair[0].score))
    return [{'kind':'bell','name':name,'time':float(timestamp),'confidence':round(line.score,4),
             'detection_score':round(score,4),'evidence':'map_bell_selection',
             'text':'地图选中铃铛；首次显示\n'+name,'verified':0,'first_occurrence_only':True}]
