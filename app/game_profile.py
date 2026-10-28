"""Optional Phantom Blade Zero demo adapter; no video timestamps or names stored."""
from collections import deque
import math
import re
from .rules import clean_text

class BossNameTracker:
    def __init__(self):
        self.readings=deque()

    def observe(self,lines,shape,timestamp):
        height,width=shape[:2]
        while self.readings and timestamp-self.readings[0][0]>30:
            self.readings.popleft()
        accepted=[]
        for line in lines:
            if not line.box: continue
            x=sum(p[0] for p in line.box)/len(line.box)/width
            y=sum(p[1] for p in line.box)/len(line.box)/height
            if not (.30<x<.70 and .025<y<.16): continue
            value=clean_text(line.text).replace('：',':')
            if re.fullmatch(r'[\u3400-\u9fff]{2,12}\s*:\s*[\u3400-\u9fff]{2,12}',value) and math.isfinite(line.score) and line.score>=.6:
                accepted.append((timestamp,re.sub(r'\s+','',value),line.score))
        # A repeated OCR box in one frame is one reading, not a second vote.
        if accepted:
            chosen=max(accepted,key=lambda reading:reading[2])
            if self.readings and self.readings[-1][0]==timestamp:
                if chosen[2]>self.readings[-1][2]: self.readings[-1]=chosen
            else:
                self.readings.append(chosen)

    def current(self,timestamp):
        recent=[r for r in self.readings if 0<=timestamp-r[0]<=25][-5:]
        if not recent: return None
        votes={}
        for stamp,name,score in recent:
            votes.setdefault(name,[]).append((stamp,score))
        stable=[name for name,readings in votes.items() if len(readings)>=2]
        # Two recent independent readings let a new boss replace long old-boss
        # history. One transient wrong character cannot replace a stable name.
        if stable:
            chosen=max(stable,key=lambda name:votes[name][-1][0])
        else:
            chosen=max(votes,key=lambda name:sum(score for _,score in votes[name]))
        scores=[r[2] for r in recent if r[1]==chosen]
        return chosen,sum(scores)/len(scores)
