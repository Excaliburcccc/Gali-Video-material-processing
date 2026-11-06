"""One stable, unverified text label for a visible boss HUD encounter.

This does not detect a victory. Use only for the opt-in game profile in compact
text mode, after TextIndexFilter; no image, video-specific time or name is used.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from difflib import SequenceMatcher
import math
import re

from .rules import clean_text, identity
from .collection_aliases import verified_name

_FULL = re.compile(r'^([\u3400-\u9fff]{2,12}):([\u3400-\u9fff]{2,12})$')
_TOP_TEXT = re.compile(r'^[\u3400-\u9fff:]{1,26}$')
_NON_BOSS = re.compile(r'任务|地图|完整度|获得|解锁|保存|设置|返回|伤害|生命|杀气|精力|能力|升级|武器|装备')


@dataclass(frozen=True)
class _Read:
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


def _boxed(lines, shape):
    if not shape or len(shape)<2: return []
    h,w=shape[:2]
    if not h or not w: return []
    found=[]
    for line in lines or ():
        try:
            if not line.box: continue
            xs=[float(p[0])/w for p in line.box]
            ys=[float(p[1])/h for p in line.box]
            score=float(line.score)
            if not all(math.isfinite(v) for v in xs+ys+[score]): continue
            read=_Read(re.sub(r'\s+','',clean_text(line.text).replace('：',':')),
                       score,min(xs),min(ys),max(xs),max(ys))
        except (AttributeError,TypeError,ValueError,IndexError,ZeroDivisionError):
            continue
        if score>=.55: found.append(read)
    return found


def _readings(lines, shape):
    boxed=_boxed(lines,shape)
    # Intro cards have a large explicit BOSS label above a large full title;
    # their title is much lower than the in-fight HUD and must be linked to it.
    intro=any(read.text.upper()=='BOSS' and .35<=read.x<=.65 and .10<=read.y<=.26
              and .04<=read.bottom-read.top<=.16 for read in boxed)
    found=[]
    for read in boxed:
        hud=(.32<=read.x<=.68 and .025<=read.y<=.16 and .25<=read.left
             and read.right<=.75 and 0<read.bottom-read.top<=.075)
        intro_name=(intro and _FULL.fullmatch(read.text) and .35<=read.x<=.65
                    and .24<=read.y<=.48 and .15<=read.left and read.right<=.85
                    and .04<=read.bottom-read.top<=.18)
        if (hud or intro_name) and _TOP_TEXT.fullmatch(read.text) and not _NON_BOSS.search(read.text):
            found.append(read)
    # The full frame and the extra HUD crop often contain the same OCR box.
    unique={}
    for read in found:
        if read.text not in unique or read.score>unique[read.text].score:
            unique[read.text]=read
    return list(unique.values())


def _speaker_readings(lines,shape,known_full_name):
    """Bridge an established intro card into its following spoken cutscene.

    A bare subtitle speaker cannot establish a boss. It must match the personal
    name of a recently established full boss title and occupy the speaker slot.
    """
    personal=known_full_name.split(':',1)[-1]
    return [read for read in _boxed(lines,shape)
            if .30<=read.x<=.70 and .76<=read.y<=.86
            and 0<read.right-read.left<=.20 and 0<read.bottom-read.top<=.06
            and re.fullmatch(r'[\u3400-\u9fff]{2,12}',read.text)
            and _related(read.text,personal)]


def _related(a,b):
    """Same title/name plus OCR corruption is not a second boss encounter."""
    aa,bb=identity(a),identity(b)
    if aa==bb: return True
    if ':' in a and ':' in b:
        at,an=a.split(':',1);bt,bn=b.split(':',1)
        if an==bn and (at in bt or bt in at): return True
        # A shared rank/title alone cannot merge two different people. Require
        # agreement in the personal name as well as the title for OCR variants.
        name_sim=SequenceMatcher(None,an,bn,autojunk=False).ratio()
        title_sim=SequenceMatcher(None,at,bt,autojunk=False).ratio()
        return min(len(an),len(bn))>=2 and name_sim>=.60 and title_sim>=.60
    if min(len(aa),len(bb))>=2 and (aa in bb or bb in aa): return True
    return min(len(aa),len(bb))>=3 and SequenceMatcher(None,aa,bb,autojunk=False).ratio()>=.60


class BossTextIndex:
    """Collapse top-center boss OCR into a stable principal text candidate.

    Two independent frames establish a full title. Counts, then mean confidence,
    select spelling; one high-confidence typo cannot replace repeated evidence.
    A 25-second absence expires the context. At most 64 full-name variants and
    128 observed fragments are retained, with one vote per timestamp.
    """
    def __init__(self, merge_gap=25.0):
        if not 1<=merge_gap<=30: raise ValueError('merge_gap must be 1..30 seconds')
        self.merge_gap=float(merge_gap)
        self._last_time=None
        self._clear()

    def filter_collections(self,candidates,lines,frame_shape,timestamp):
        """Use HUD evidence and repeated readings in the typed collection path.

        Category labels alone cannot seed a boss, and phase changes still need
        their own complete title. Audited OCR spellings are normalized before
        voting. The same encounter key survives a later spelling correction.
        """
        from dataclasses import replace
        normalized=[replace(line,text=verified_name('boss',clean_text(line.text).replace('：',':')))
                    for line in lines]
        tracked=self.filter([],normalized,frame_shape,timestamp)
        result=[c for c in candidates if not (c['kind']=='boss' and c.get('evidence')=='boss_name')]
        for candidate in tracked:
            candidate={**candidate,'kind':'boss'}
            result.append(candidate)
        return result

    def _clear(self):
        self._key=None; self._name=None; self._seed=None
        self._votes=OrderedDict(); self._aliases=OrderedDict()
        self._last_seen=None; self._pending=None

    def restore_context(self,name,confidence,last_seen):
        """Restore a previously persisted full boss label before a resumed scan.

        Invalid or weak records are ignored. The next filter call discards a
        future-dated or expired context using the actual video timestamp; this
        method intentionally has no wall-clock assumptions about video time.
        """
        try:
            name=re.sub(r'\s+','',clean_text(name).replace('：',':'))
            confidence=float(confidence);last_seen=float(last_seen)
        except (TypeError,ValueError,OverflowError):
            return False
        if (not _FULL.fullmatch(name) or _NON_BOSS.search(name)
                or not math.isfinite(confidence) or not .6<=confidence<=1
                or not math.isfinite(last_seen) or last_seen<0):
            return False
        self._clear()
        self._name=name;self._seed=name;self._key='boss:'+identity(name)
        self._last_seen=last_seen;self._last_time=last_seen
        self._votes[name]={'count':2,'sum':confidence*2,'stamp':last_seen}
        self._remember((name,name.split(':',1)[1]))
        return True

    def _remember(self,values):
        for value in values:
            self._aliases[identity(value)]=True
            self._aliases.move_to_end(identity(value))
        while len(self._aliases)>128: self._aliases.popitem(last=False)

    def _vote(self,read,timestamp):
        old=self._votes.setdefault(read.text,{'count':0,'sum':0.,'stamp':None})
        if old['stamp']!=timestamp:
            old['count']+=1;old['sum']+=read.score;old['stamp']=timestamp
        self._votes.move_to_end(read.text)
        while len(self._votes)>64:
            oldest=next(name for name in self._votes if name!=self._name)
            del self._votes[oldest]
        stable=[(name,v) for name,v in self._votes.items() if v['count']>=2]
        if stable:
            # Retain the already established spelling on tied frame counts;
            # a single extra high-confidence typo must not make it oscillate.
            self._name=max(stable,key=lambda pair:(pair[1]['count'],pair[0]==self._name,
                                                  pair[1]['sum']/pair[1]['count']))[0]

    def filter(self,candidates,lines=(),frame_shape=None,timestamp=0.0):
        timestamp=float(timestamp)
        if not math.isfinite(timestamp): raise ValueError('timestamp must be finite')
        if self._last_time is not None and timestamp<self._last_time: self._clear()
        self._last_time=timestamp
        if self._last_seen is not None and timestamp-self._last_seen>self.merge_gap: self._clear()
        reads=_readings(lines,frame_shape)
        if self._name: reads.extend(_speaker_readings(lines,frame_shape,self._name))
        full=[r for r in reads if _FULL.fullmatch(r.text)]
        # OCR can split a title ending in ':' from its adjacent personal name.
        for left in reads:
            if not re.fullmatch(r'[\u3400-\u9fff]{2,12}:',left.text): continue
            for right in reads:
                if (re.fullmatch(r'[\u3400-\u9fff]{2,12}',right.text)
                        and -.01<=right.left-left.right<=.07 and abs(right.y-left.y)<.025):
                    full.append(_Read(left.text+right.text,min(left.score,right.score),left.left,
                                      min(left.top,right.top),right.right,max(left.bottom,right.bottom)))
        chosen=max(full,key=lambda r:r.score) if full else None
        blocked_new=False
        if chosen:
            reference=self._name or self._seed
            if reference and not _related(chosen.text,reference):
                pending=self._pending
                if pending and _related(chosen.text,pending['read'].text) and timestamp-pending['last']<=3:
                    if timestamp!=pending['last']: pending['count']+=1
                    pending['last']=timestamp
                    if pending['count']>=2:
                        first=pending['read'];first_time=pending['first']
                        self._clear();self._seed=first.text;self._key='boss:'+identity(first.text)
                        self._vote(first,first_time)
                else:
                    self._pending={'read':chosen,'first':timestamp,'last':timestamp,'count':1}
                blocked_new=self._pending is not None
            else: self._pending=None
            if not blocked_new:
                if not self._seed:
                    self._seed=chosen.text;self._key='boss:'+identity(chosen.text)
                self._vote(chosen,timestamp)
                self._last_seen=timestamp
        reference=self._name or self._seed
        related=[r for r in reads if reference and _related(r.text,reference)]
        active=bool(chosen or related)
        if active:
            self._remember(r.text for r in reads)
            self._remember(r.text for r in full)
            if related and not blocked_new: self._last_seen=timestamp
        # In a confirmed boss HUD context the encounter name is the principal
        # searchable text for the whole frame. Dialogue and ancillary HUD words
        # must not create parallel timestamps; typed events are always retained.
        # Without currently observed boss text, menus and other scenes pass on.
        output=[candidate for candidate in candidates
                if candidate.get('kind')!='text' or not active]
        if active and self._name and not blocked_new:
            v=self._votes[self._name]
            output.append({'kind':'text','name':self._name,'text':self._name,
                           'confidence':round(v['sum']/v['count'],4),'evidence':'boss_name',
                           '_identity_key':self._key,'_replace_name':True,'_merge_gap':self.merge_gap})
        return output
