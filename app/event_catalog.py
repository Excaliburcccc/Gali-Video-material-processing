"""A first-appearance catalog; raw evidence remains available for correction."""
import re
from difflib import SequenceMatcher
from .rules import identity, clean_text

_FOCUS={'boss_name','dialogue_speaker','map_selection'}
_QUANTITY=re.compile(r'\s*[+＋\-－−]\s*\d+(?:\.\d+)?\s*$')
_WEAPON=re.compile(r'武器展示面板|(?:单手|雙手|双手|單手|双持|雙持)武器')
# These are the analyzer's existing fallbacks, not identifiable object names.
_ANONYMOUS_NAMES={identity(value) for value in (
    '物品获得提示（名称未识别）', '战斗胜利提示（名称未识别）',
    '成就解锁提示（名称未识别）', '传送点激活提示（名称未识别）',
    '首领击败（名称待核实）',
)}


def _base_name(value):
    return _QUANTITY.sub('',clean_text(value)).strip()


def _aliases(row):
    name=_base_name(row['name']); names={identity(name)}
    raw=str(row.get('text','')).strip().splitlines()
    # Only same-row OCR spelling/correction evidence can supply aliases. Never
    # merge two arbitrary similar names (floors and book chapters can differ).
    if len(raw)==1 or row.get('edited'):
        # A panel can list several nearby floors or chapters. If its actual
        # name is already present, other similar lines are separate objects;
        # otherwise a spelling correction needs one unambiguous candidate.
        if any(identity(_base_name(value)) in names for value in raw):
            return names
        candidates=set()
        for value in raw:
            other=_base_name(value)
            if min(len(identity(name)),len(identity(other)))>=4 and abs(len(other)-len(name))<=2 and SequenceMatcher(None,identity(name),identity(other),autojunk=False).ratio()>=.75:
                candidates.add(identity(other))
        if len(candidates)==1:
            names.update(candidates)
    return names


def entity_rows(rows):
    """Unify appearance/menus/victory evidence BEFORE category/status filters."""
    rows=[dict(e) for e in rows if e.get('evidence')!='upgrade_material']
    bindings={}
    families={}
    for row in rows:
        if row.get('evidence')=='scene_secondary': continue
        kind=row['kind']; evidence=row.get('evidence','')
        family=None
        if kind=='weapon' or evidence=='inventory_selection' or (kind=='item' and _WEAPON.search(row.get('text',''))): family='weapon'
        elif kind=='boss' or evidence in ('boss_name','victory_template'): family='boss'
        elif kind=='waypoint' or evidence=='map_selection': family='waypoint'
        elif kind=='item': family='item'
        if not family: continue
        families[row['id']]=family
        name=_base_name(row['name'])
        # Weapon inventory evidence upgrades the generic item family; a human
        # corrected spelling is used without borrowing their review status.
        rank=(family=='weapon',bool(row.get('edited')),bool(row.get('verified')),-row['time'])
        for alias in _aliases(row):
            key=(row['video_id'],alias,family)
            if key not in bindings or rank>bindings[key]['rank']:
                bindings[key]={'kind':family,'name':name,'rank':rank}
    for row in rows:
        if row['kind'] in ('note','quest','achievement') or row.get('evidence')=='scene_secondary': continue
        own=families.get(row['id'])
        allowed={'item','weapon'} if own=='item' else ({own} if own else {'boss'} if row['kind']=='enemy' else {'item','weapon','boss','waypoint'})
        matches=[bindings[(row['video_id'],alias,family)] for alias in _aliases(row) for family in allowed if (row['video_id'],alias,family) in bindings]
        if not matches: continue
        matched_kinds={e['kind'] for e in matches}
        if 'weapon' in matched_kinds: matched_kinds.discard('item')
        if not own and row['kind']=='text' and len(matched_kinds)>1: continue
        chosen=max(matches,key=lambda entry:entry['rank'])
        row['kind']=chosen['kind'];row['name']=chosen['name']
        row['first_occurrence_only']=True
    return rows


def primary_rows(rows):
    """Bridge isolated OCR failures inside an already established UI scene.

    Keep the raw rows in the database; only the compact catalog is filtered.
    Reviewed or manually edited records are never suppressed. The active
    windows contain current scene labels, not every label in the recording.
    """
    videos={}
    for row in rows: videos.setdefault(row['video_id'],[]).append(row)
    result=[]
    for video_rows in videos.values():
        ordered=sorted(video_rows,key=lambda e:(e['time'],e['id']))
        primary=[e for e in ordered if e.get('source')=='ocr' and (
            e['kind'] in ('item','weapon','boss','quest','waypoint','enemy','achievement')
            or e.get('evidence') in _FOCUS)]
        active=[]; cursor=0
        for row in ordered:
            stamp=row['time']
            while cursor<len(primary) and primary[cursor]['time']<=stamp+1:
                active.append(primary[cursor]);cursor+=1
            active=[e for e in active if (e.get('end_time') or e['time'])>=stamp-1]
            if row['kind']!='text' or row.get('source')!='ocr' or row.get('edited') or row.get('verified'):
                result.append(row);continue
            if row.get('evidence')=='scene_secondary': continue
            if row.get('evidence')=='inventory_selection':
                result.append(row);continue
            other=[e for e in active if e['id']!=row['id']]
            if row.get('evidence') not in _FOCUS and other:
                continue
            key=identity(row['name'])
            if row.get('evidence')=='dialogue_speaker' and any(
                e.get('evidence')=='dialogue_speaker' and len(identity(e['name']))>len(key)>=4
                and key in identity(e['name']) for e in other):
                continue
            result.append(row)
    return result


def group_events(rows, compact=True):
    groups = {}
    rows=entity_rows(rows)
    if compact: rows=primary_rows(rows)
    for row in sorted(rows, key=lambda e: (e['time'], e['id'])):
        first_only=(identity(_base_name(row['name'])) not in _ANONYMOUS_NAMES
                    and (bool(row.get('first_occurrence_only'))
                         or (compact and row['kind']=='text' and row.get('source')=='ocr')))
        key=(row['video_id'],row['kind'],identity(row['name'])) if first_only else ('id',row['id'])
        row={**row,'first_occurrence_only':first_only}
        groups.setdefault(key, []).append(dict(row))
    result = []
    for members in groups.values():
        first = dict(members[0])
        first['occurrence_count'] = 1
        first['occurrences'] = []
        # Keep the first occurrence's end_time, confidence, screenshot and
        # review state together. A group is not one continuous video segment.
        result.append(first)
    return sorted(result, key=lambda e: (e['time'], e['id']))


def filter_groups(groups, q='', kind='', verified=''):
    needle = q.casefold().strip()
    return [e for e in groups if (not kind or e['kind'] == kind)
            and (verified not in ('0', '1') or bool(e['verified']) == (verified == '1'))
            and (not needle or any(needle in str(e.get(field, '')).casefold()
                 for field in ('name', 'text')))]
