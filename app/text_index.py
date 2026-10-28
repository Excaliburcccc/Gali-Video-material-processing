"""A bounded, optional compact index of useful on-screen names and short hints.

Only kind=text is filtered. Acquisition, victory, quest and waypoint candidates
pass through unchanged. This is an OCR index, not another completion detector.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass
from difflib import SequenceMatcher
import math
import re

from .rules import clean_text, identity


_CJK=re.compile(r'[\u3400-\u9fff]')
_BUTTONS=set(('返回 确认 确定 取消 继续 关闭 跳过 离开 回退 回退至始 全部 左右 '
              '地图 任务 故事 角色 系统 武器 道具 饰品 读物 书籍 剧情道具 影之武装 '
              '单手武器 双手武器 双持武器 当前拥有 耗费 普通连招 基础连招 杀气连招 '
              '招式 装备升级 能力修行 保存记忆 穿行 食用 食用了 替换 返还 卸下 移动 BOSS 目标 任务目标').split())
_CONTROL=re.compile(r'查看(?:收集详情|进阶内容|基础内容)|进入全域地图|选择槽位|取消追踪|重置视图|缩放|^按住|装备新的?武器')
_STATS=re.compile(r'伤害能力|破防能力|异常效果|地图完整度|(?:^|\s)(?:HP|MP|FPS)(?:\s|\d|:)|(?:意识尘埃|识尘埃)\s*[+＋]\s*\d|(?:消耗|耗).{0,12}\d.{0,8}(?:升级|升机)',re.I)
_CONTROLLER=re.compile(r'^(?:[LR][123]?|RC[1-4]|[K(]?X{2,}[Y)]?|[口四]\d)$',re.I)
_SENTENCE_MARK=re.compile(r'[,，。!！?？;；]')
_ORDINAL=re.compile(r'[一二三四五六七八九十百零〇两0-9]+(?:层|層|章|节|節|阶|階|之|号|號)|之[一二三四五六七八九十0-9]+')
_QUEST_CUE=re.compile(r'^(?:任务|目标|前往|寻找|找到|查明|调查|收集|帮助|将找到|在.{0,18}(?:寻找|找到|调查))')
_SPEECH_FRAGMENT=re.compile(r'^(?:我(?:们|也|是|在|喝|要|决)|你(?:们|是|自己|可|喝)|这(?:种|所)|那(?:是|一战)|只是|现今|所以|不过|不错|该还|楔子命令是|食用之(?:后|启))')
_PANEL_CATEGORIES={'书籍','單手武器','单手武器','双手武器','双持武器','双刀武器','剧情道具','魂器','暗器'}


@dataclass(frozen=True)
class _Rect:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def cx(self): return (self.left+self.right)/2
    @property
    def cy(self): return (self.top+self.bottom)/2


def _rect(line,shape):
    box=getattr(line,'box',None)
    if not box or not shape or len(shape)<2: return None
    try:
        h,w=float(shape[0]),float(shape[1])
        xs=[float(p[0])/w for p in box]; ys=[float(p[1])/h for p in box]
        if not all(math.isfinite(v) for v in xs+ys): return None
        return _Rect(min(xs),min(ys),max(xs),max(ys))
    except (TypeError,ValueError,IndexError,ZeroDivisionError): return None


def _near(a,b):
    if a is None or b is None: return False
    return abs(a.cx-b.cx)<.12 and abs(a.cy-b.cy)<.035


def _fuzzy(a,b,rect_a,rect_b,speaker=False):
    """Deliberately protect short names, chapter suffixes and numbered places."""
    ia,ib=identity(a),identity(b)
    if ia==ib: return True
    if not _near(rect_a,rect_b): return False
    if speaker and min(len(ia),len(ib))>=4 and (ia.endswith(ib) or ib.endswith(ia)):
        return True
    # A qualified boss title and its separately OCR'd name are the same label.
    aa,bb=clean_text(a).replace('：',':'),clean_text(b).replace('：',':')
    if ':' in aa and identity(aa.split(':',1)[1])==ib: return True
    if ':' in bb and identity(bb.split(':',1)[1])==ia: return True
    if any(ch in aa+bb for ch in '·•') and aa.rsplit('·',1)[-1]!=bb.rsplit('·',1)[-1]: return False
    if _ORDINAL.findall(aa)!=_ORDINAL.findall(bb): return False
    if min(len(ia),len(ib))<4: return False
    if abs(len(ia)-len(ib))>1: return False
    minimum=.80 if ':' in aa and ':' in bb and rect_a.cy<.2 and rect_b.cy<.2 else .86
    if speaker and min(len(ia),len(ib))>=6: minimum=.82
    return SequenceMatcher(None,ia,ib,autojunk=False).ratio()>=minimum


class TextIndexFilter:
    """``compact`` keeps useful short labels; ``all`` passes through; ``off`` removes text.

    Call once per OCR frame, after specialized classifiers, with the same pixel
    space for OCRLine.box and frame_shape. No images are retained. At most 128
    local text tracks and 100 decisions from the latest call are kept.

    Compact text output carries an accumulator contract:
      _identity_key: stable key while a local text occurrence is being tracked;
      _merge_gap: 20 seconds (text only); _replace_name: best OCR spelling may
      replace a previous machine spelling. A gap >20 seconds creates a new
      occurrence. The accumulator must honor these fields to join OCR dropouts.
    """
    def __init__(self,mode='compact',merge_gap=20.0,max_tracks=128):
        if mode not in ('compact','all','off'): raise ValueError('Unknown text index mode')
        if not 15<=merge_gap<=30: raise ValueError('Text merge gap must be 15..30 seconds')
        if not 8<=max_tracks<=512: raise ValueError('max_tracks must be 8..512')
        self.mode=mode; self.merge_gap=float(merge_gap); self.max_tracks=int(max_tracks)
        self._tracks=OrderedDict(); self._serial=0; self._last_time=None
        self.stats=Counter(); self.last_decisions=[]

    def reset(self):
        self._tracks.clear(); self._last_time=None; self.last_decisions=[]

    @property
    def state_size(self): return len(self._tracks)

    def _context(self,lines,shape):
        values=[]
        for line in lines or []:
            text=clean_text(getattr(line,'text',''))
            if text: values.append((text,_rect(line,shape),float(getattr(line,'score',0))))
        panel=any(text in _PANEL_CATEGORIES and rect and rect.left>.55 and .12<=rect.cy<=.32
                  for text,rect,_ in values)
        return values,panel

    @staticmethod
    def _find_rect(text,values):
        exact=[(rect,score) for value,rect,score in values if identity(value)==identity(text) and rect]
        if exact: return max(exact,key=lambda x:x[1])[0]
        # Also supports evaluating saved text against re-OCR'd JPEG evidence.
        # No box is invented when there is no sufficiently close observed line.
        key=identity(text); near=[]
        for value,rect,score in values:
            if not rect: continue
            normalized=clean_text(value).replace('：',':')
            if ':' in normalized and identity(normalized.split(':',1)[1])==key:
                near.append((1.,score,rect))
            elif min(len(key),len(identity(value)))>=4:
                similarity=SequenceMatcher(None,key,identity(value),autojunk=False).ratio()
                if similarity>=.78: near.append((similarity,score,rect))
        return max(near,key=lambda x:(x[0],x[1]))[2] if near else None

    @staticmethod
    def _reason(text,rect,panel,specialized,qualified):
        key=identity(text)
        # A classified acquisition/menu/quest is the principal content of this
        # frame. Its book heading, map legends and dialogue are not new events.
        if specialized: return 'specialized_scene'
        if len(key)<2: return 'fragment'
        if text.upper() in _BUTTONS or _CONTROLLER.fullmatch(text) or _CONTROL.search(text): return 'button'
        # OCR often turns controller icons into one stray letter/digit/口/四.
        button=re.sub(r'^[^\u3400-\u9fff]{0,8}','',text).strip()
        if button in _BUTTONS or (len(button)>1 and button[0] in '口四回' and button[1:] in _BUTTONS): return 'button'
        if _STATS.search(text): return 'numeric_hud'
        cjk=len(_CJK.findall(text)); digits=sum(c.isdigit() for c in text)
        if digits and cjk<2 and digits>=len(key)*.35: return 'numeric_hud'
        if text.rstrip().endswith((':','：',';','；')): return 'incomplete_label'
        if key in qualified: return 'duplicate_name_component'
        # Preserve task/location hints even when they are sentence-like.
        quest=bool(_QUEST_CUE.search(text)) and len(key)<=36
        if panel and rect and rect.left>.55 and .34<=rect.cy<=.99: return 'panel_body'
        if not quest:
            if len(key)>22: return 'long_prose'
            if _SENTENCE_MARK.search(text) and len(key)>=2:
                return 'dialogue_or_prose'
            if _SPEECH_FRAGMENT.search(text) or (len(key)<=5 and text.endswith('的')): return 'dialogue_fragment'
            # This is the subtitle band, not a blanket rule for the lower screen.
            if rect and .20<rect.cx<.80 and .78<rect.cy<.98 and rect.right-rect.left>.20 and len(key)>=12:
                return 'subtitle'
        return None

    def filter(self,candidates,lines=(),frame_shape=None,timestamp=0.0):
        candidates=list(candidates)
        self.last_decisions=[]
        if self.mode=='all': return candidates
        if self.mode=='off': return [c for c in candidates if c.get('kind')!='text']
        timestamp=float(timestamp)
        if not math.isfinite(timestamp): raise ValueError('timestamp must be finite')
        if self._last_time is not None and timestamp<self._last_time: self.reset()
        self._last_time=timestamp
        for token in list(self._tracks):
            if timestamp-self._tracks[token]['last_seen']>self.merge_gap: del self._tracks[token]
        values,panel=self._context(lines,frame_shape)
        specialized=set()
        for c in candidates:
            if c.get('kind')!='text':
                specialized.add(identity(c.get('name','')))
                specialized.update(identity(part) for part in str(c.get('text','')).splitlines() if part.strip())
        qualified=set()
        for c in candidates:
            value=clean_text(c.get('name','')).replace('：',':')
            if c.get('kind')=='text' and re.fullmatch(r'[\u3400-\u9fff]{2,12}:[\u3400-\u9fff]{2,12}',value):
                qualified.add(identity(value.split(':',1)[1]))
        output=[c for c in candidates if c.get('kind')!='text']; emitted={}
        for candidate in candidates:
            if candidate.get('kind')!='text': continue
            text=clean_text(candidate.get('name') or candidate.get('text',''))
            rect=self._find_rect(text,values)
            reason=self._reason(text,rect,panel,specialized,qualified)
            self.stats['input_text']+=1
            if reason:
                self.stats['dropped_'+reason]+=1
                if len(self.last_decisions)<100: self.last_decisions.append({'name':text,'reason':reason})
                continue
            exact=identity(text)
            track=None; token=None
            exact_tracks=[(k,v) for k,v in self._tracks.items() if exact in v['aliases']]
            if exact_tracks:
                token,track=max(exact_tracks,key=lambda pair:(pair[1]['quality'],pair[1]['last_seen']))
            else:
                for old_token,old in reversed(self._tracks.items()):
                    if timestamp-old['last_seen']<=4 and _fuzzy(text,old['name'],rect,old['rect'],candidate.get('evidence')=='dialogue_speaker' and old.get('evidence')=='dialogue_speaker'):
                        token,track=old_token,old; break
            confidence=float(candidate.get('confidence',0) or 0)
            quality=confidence+(.06 if ':' in text or '：' in text else 0)
            if candidate.get('evidence')=='dialogue_speaker': quality+=len(exact)*.08
            if track is None:
                self._serial+=1; token=self._serial
                track={'key':exact,'name':text,'text':candidate.get('text',text),'confidence':confidence,'aliases':[exact],
                       'quality':quality,'rect':rect,'last_seen':timestamp,'evidence':candidate.get('evidence','')}
                self._tracks[token]=track
            else:
                if exact not in track['aliases']:
                    track['aliases'].append(exact)
                    track['aliases']=track['aliases'][-8:]
                if quality>track['quality']+.005:
                    track.update(name=text,text=candidate.get('text',text),confidence=confidence,quality=quality)
                track['last_seen']=timestamp
                if rect: track['rect']=rect
                self._tracks.move_to_end(token)
                self.stats['same_local_track']+=1
            result={**candidate,'name':track['name'],'text':track['text'],'confidence':track['confidence'],
                    '_identity_key':track['key'],'_merge_gap':self.merge_gap,'_replace_name':True}
            emitted[track['key']]=result
            self.stats['kept_text']+=1
            if len(self.last_decisions)<100:
                self.last_decisions.append({'name':text,'reason':'keep','output_name':track['name'],'identity_key':track['key']})
            while len(self._tracks)>self.max_tracks: self._tracks.popitem(last=False)
        output.extend(emitted.values())
        return output
