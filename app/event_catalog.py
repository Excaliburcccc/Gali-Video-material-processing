"""A first-appearance catalog; raw evidence remains available for correction."""
import re
from difflib import SequenceMatcher
from .rules import identity, clean_text
from .collection_taxonomy import COLLECTION_KINDS, classify_existing_event, collection_identity

_FOCUS={'boss_name','dialogue_speaker','map_selection'}
_QUANTITY=re.compile(r'\s*[+＋\-－−]\s*\d+(?:\.\d+)?\s*$')
_WEAPON=re.compile(r'武器展示面板|(?:单手|雙手|双手|單手|双持|雙持)武器')
# These are the analyzer's existing fallbacks, not identifiable object names.
_ANONYMOUS_NAMES={identity(value) for value in (
    '物品获得提示（名称未识别）', '战斗胜利提示（名称未识别）',
    '成就解锁提示（名称未识别）', '传送点激活提示（名称未识别）',
    '首领击败（名称待核实）',
)}
# Each fragment pickup is a distinct map collectible, even when the label is
# identical. The analyzer already folds a continuously visible prompt into one
# stable event ID; catalog views must not discard any of these occurrences.
_REPEATABLE_FRAGMENTS={'amber','blackstone'}
_PRESERVE_OCCURRENCES=_REPEATABLE_FRAGMENTS | {'book'}


def _base_name(value):
    return _QUANTITY.sub('',clean_text(value)).strip()


def _book_frame_key(row):
    """Only collapse duplicate OCR records for the very same book frame.

    A repeated title at a different time can be another reading or collection
    moment. Without a saved screenshot there is no same-frame proof, so retain
    the record. Keep different chapter titles and manual records independent.
    """
    shot=str(row.get('screenshot') or '')
    name=identity(row.get('name',''))
    if row.get('source')=='ocr' and shot and name and row.get('time') is not None:
        return ('book-frame',row['video_id'],row['time'],name,shot.replace('\\','/').casefold())
    return ('id',row['id'])


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
    rows=[dict(e) for e in rows if e.get('evidence') not in {'upgrade_material','weapon_menu'}]
    bindings={}
    families={}
    for row in rows:
        if row['kind']=='book' or row.get('evidence')=='scene_secondary': continue
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
        if row['kind'] in ('book','note','quest','achievement') or row.get('evidence')=='scene_secondary': continue
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


def _resolve_memory_chapters(rows):
    """Recover a missing chapter only from the same continuous flower panel.

    Raw evidence is retained. A chapter from another recording or a later
    collection cannot hide an unknown petal, and ambiguous chapters stay apart.
    """
    named = [row for row in rows if row['kind'] == 'memory_flower' and ' · ' in row['name']]
    result = []
    for row in rows:
        if (row['kind'] == 'memory_flower' and ' · ' not in row['name'] and
                row.get('source') == 'ocr' and not row.get('edited') and not row.get('verified')):
            nearby = [other for other in named if other['video_id'] == row['video_id'] and
                      abs(other['time']-row['time']) <= 3 and
                      identity(other['name'].split(' · ')[0]) == identity(row['name'])]
            if len({collection_identity(other['kind'],other['name']) for other in nearby}) == 1:
                row = {**row, 'name': min(nearby,key=lambda other:other['time'])['name']}
        result.append(row)
    return result


def _continuous_panels(rows):
    """Fold only touching observations of one explicit collectible panel.

    Books require the complete book + chapter identity; bare cover titles are
    ambiguous. A gap, another chapter, another video or a reviewed record breaks
    continuity. Fragments still keep every independent pickup. Raw rows survive.
    """
    result=[]
    active={}
    for row in sorted(rows,key=lambda e:(e['video_id'],e['time'],e['id'])):
        kind=row['kind']
        previous=active.get((row['video_id'],kind))
        if (kind=='book' and ' · ' not in row['name'] and previous
                and row.get('source')=='ocr' and row.get('evidence')=='collection_panel'
                and not row.get('edited') and not row.get('verified')
                and previous['time']<row['time']<=previous['end_time']+.01
                and identity(row['name'])==identity(previous['name'].partition(' · ')[2])):
            # The cover heading fades before the chapter. Exact chapter text
            # in a touching panel can inherit its own just-observed book title.
            row={**row,'name':previous['name']}
        eligible=(kind in _PRESERVE_OCCURRENCES and row.get('source')=='ocr'
                  and row.get('evidence')=='collection_panel'
                  and not row.get('edited') and not row.get('verified')
                  and (kind!='book' or ' · ' in row['name']))
        key=(row['video_id'],kind)
        previous=active.get(key) if eligible else None
        if (previous is not None and row['time']>previous['time']
                and row['time']<=float(previous.get('end_time') or previous['time'])+.01
                and collection_identity(kind,row['name'])==collection_identity(kind,previous['name'])):
            previous['end_time']=max(float(previous.get('end_time') or previous['time']),
                                     float(row.get('end_time') or row['time']))
            continue
        copy=dict(row)
        result.append(copy)
        if eligible: active[key]=copy
        else: active.pop(key,None)
    return result


def group_events(rows, compact=True, collection_only=False):
    groups = {}
    if collection_only:
        rows=[mapped for row in rows if (mapped := classify_existing_event(row)) is not None]
        rows=[{**row,'first_occurrence_only':row['kind'] in COLLECTION_KINDS and row['kind'] not in _PRESERVE_OCCURRENCES} for row in rows]
    else:
        rows=entity_rows(rows)
        if compact: rows=primary_rows(rows)
    rows = _continuous_panels(_resolve_memory_chapters(rows))
    for row in sorted(rows, key=lambda e: (e['time'], e['id'])):
        first_only=(row.get('source')=='ocr' and row['kind'] not in _PRESERVE_OCCURRENCES
                    and identity(_base_name(row['name'])) not in _ANONYMOUS_NAMES
                    and (bool(row.get('first_occurrence_only'))
                         or (compact and row['kind']=='text' and row.get('source')=='ocr')))
        key=(_book_frame_key(row) if row['kind']=='book' else
             (row['video_id'],row['kind'],collection_identity(row['kind'],row['name'])) if first_only else ('id',row['id']))
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
    kinds = {'memory_flower', 'reading_memory'} if kind in {'memory_flower', 'reading_memory'} else {kind}
    return [e for e in groups if (not kind or e['kind'] in kinds)
            and (verified not in ('0', '1') or bool(e['verified']) == (verified == '1'))
            and (not needle or any(needle in str(e.get(field, '')).casefold()
                 for field in ('name', 'text')))]


def import_order_key(video):
    """Keep the user's insertion sequence, even for a same-second batch import.

    Production queries supply SQLite rowid as import_order. Creation time is
    only a fallback for callers without that metadata; filenames and filesystem
    timestamps never alter the order of the library.
    """
    imported=video.get('import_order')
    if imported is not None:
        return (0,int(imported),'',str(video['id']))
    return (1,0,str(video.get('created_at') or ''),str(video['id']))


def library_events(rows, videos):
    """Deduplicate the entire library before any view filters or pagination.

    ``rows`` have already been normalized by each video's recognition scope.
    Keep a first record intact: a later review or screenshot cannot change the
    source video, timestamp, confidence or review status of that first record.
    Explicit manual markers and anonymous detections remain separate records.
    """
    order={video['id']:index for index,video in enumerate(sorted(videos,key=import_order_key))}
    ordered=sorted(rows,key=lambda row:(order.get(row['video_id'],len(order)),row['time'],row['id']))
    result=[]; seen=set()
    for row in ordered:
        name=identity(_base_name(row['name']))
        first_only=(row['kind'] not in _PRESERVE_OCCURRENCES
                    and row.get('source')=='ocr' and bool(row.get('first_occurrence_only'))
                    and bool(name) and name not in _ANONYMOUS_NAMES)
        key=(_book_frame_key(row) if row['kind']=='book' else
             (row['kind'],collection_identity(row['kind'],_base_name(row['name']))) if first_only else ('id',row['id']))
        if key in seen: continue
        seen.add(key)
        result.append({**row,'first_occurrence_only':False} if row['kind'] in _PRESERVE_OCCURRENCES else dict(row))
    return result


def timestamp_events(rows, library_catalog):
    """Intersect a selection with the complete catalog before exporting.

    All result surfaces share the same representatives, including a single
    video's timeline. The catalog already preserves independent fragments,
    book frames, flower chapters and manual markers. Keep raw evidence so an
    earlier imported video analyzed later can become the first representative.
    Preserve the caller's order for frozen compilation time mappings.
    """
    first_ids={row['id'] for row in library_catalog}
    return [row for row in rows if row['id'] in first_ids]
