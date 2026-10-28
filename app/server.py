import csv
import hashlib
import io
import json
import logging
import math
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from . import media
from .event_catalog import group_events, filter_groups
from .storage import ROOT, DATA, connect, query, execute, init_db, settings, save_settings, update_video, update_job

KINDS={'item','weapon','boss','enemy','achievement','waypoint','quest','text','note'}
LABELS={'item':'收集','weapon':'武器','boss':'BOSS','enemy':'击败','achievement':'成就','waypoint':'传送点','quest':'任务','text':'画面文字','note':'备注'}
EXTENSIONS={'.mp4','.mkv','.mov','.avi','.webm','.m4v','.ts'}
analysis_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='ocr')
media_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='media')
cancel_flags={}
job_lock=threading.RLock()
bridge=None
stop_flag=threading.Event()
catalog_cache=OrderedDict()
catalog_counts={}
event_views=OrderedDict()
catalog_lock=threading.RLock()
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',handlers=[logging.FileHandler(DATA/'logs'/'server.log',encoding='utf-8')])

def uid(): return uuid.uuid4().hex

def video_or_404(video_id):
    video=query('SELECT * FROM videos WHERE id=?',(video_id,),True)
    if not video: raise HTTPException(404,'找不到这段录像')
    if not Path(video['path']).is_file(): raise HTTPException(404,'原视频已移动或不可用，请重新导入')
    stat=Path(video['path']).stat()
    if stat.st_size!=video['size'] or abs(stat.st_mtime-video['mtime'])>.001:
        raise HTTPException(409,'原录像已改变，请重新导入，避免旧标记指向新文件')
    return video

def finite(value, name, low=0, high=1e10):
    try: number=float(value)
    except (TypeError,ValueError): raise HTTPException(400,name+'必须是数字')
    if not math.isfinite(number) or not low<=number<=high: raise HTTPException(400,name+'超出允许范围')
    return number

def validate_options(body):
    if not isinstance(body,dict): raise HTTPException(400,'设置格式无效')
    values={k:body[k] for k in ('sample_interval','max_width','regions','keywords','game_profile','text_mode') if k in body}
    if 'game_profile' in values and values['game_profile'] not in ('generic','phantom_blade_zero_demo'):
        raise HTTPException(400,'未知的游戏识别方案')
    if 'text_mode' in values and values['text_mode'] not in ('compact','all','off'):
        raise HTTPException(400,'未知的文字保留方式')
    if 'sample_interval' in values: values['sample_interval']=finite(values['sample_interval'],'采样间隔',.2,30)
    if 'max_width' in values: values['max_width']=int(finite(values['max_width'],'识别宽度',640,3840))
    if 'regions' in values:
        if not isinstance(values['regions'],list) or len(values['regions'])>8: raise HTTPException(400,'识别区域最多8个')
        for r in values['regions']:
            if not isinstance(r,dict) or not all(k in r for k in ('x','y','w','h')): raise HTTPException(400,'区域需要 x、y、w、h')
            for k in ('x','y','w','h'): r[k]=finite(r[k],'区域坐标',0,1)
            if r['w']<=0 or r['h']<=0 or r['x']+r['w']>1.00001 or r['y']+r['h']>1.00001: raise HTTPException(400,'区域必须位于画面内')
    if 'keywords' in values:
        words=values['keywords']
        if not isinstance(words,dict) or not set(words)<={'item','enemy','achievement'}: raise HTTPException(400,'提示词只支持 item、enemy、achievement 三类')
        for group in words.values():
            if not isinstance(group,list) or len(group)>50 or any(not isinstance(word,str) or not 2<=len(word)<=80 for word in group):
                raise HTTPException(400,'每类提示词需要是文字列表，最多50项，每项2至80个字')
    return values

def insert_event(video_id, e):
    event_id=str(e.get('id') or uid())
    if e.get('source')=='ocr' and not event_id.startswith(video_id+'_'):
        event_id=video_id+'_'+event_id
    kind=e.get('kind','note')
    if kind not in KINDS: kind='note'
    stamp=max(0,float(e.get('time',0)))
    name=str(e.get('name') or LABELS[kind])[:300]
    with connect() as db:
        db.execute('''INSERT INTO events(id,video_id,kind,name,time,end_time,confidence,text,screenshot,source,verified,evidence,detection_score)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
name=CASE WHEN events.source='ocr' AND events.kind='text' THEN excluded.name ELSE events.name END,
time=CASE WHEN events.edited=0 AND events.source='ocr' THEN MIN(events.time,excluded.time) ELSE events.time END,
end_time=MAX(events.end_time,excluded.end_time),
screenshot=CASE WHEN events.screenshot='' THEN excluded.screenshot ELSE events.screenshot END,
 confidence=CASE WHEN events.kind='text' THEN excluded.confidence ELSE MAX(events.confidence,excluded.confidence) END,
 evidence=CASE WHEN excluded.evidence!='' THEN excluded.evidence ELSE events.evidence END,
 detection_score=CASE WHEN excluded.detection_score IS NULL THEN events.detection_score WHEN events.detection_score IS NULL THEN excluded.detection_score ELSE MAX(events.detection_score,excluded.detection_score) END,
text=CASE WHEN length(excluded.text)>length(events.text) THEN excluded.text ELSE events.text END
WHERE NOT (events.source='ocr' AND (events.edited=1 OR events.verified=1))''',
 (event_id,video_id,kind,name,stamp,float(e.get('end_time') or stamp),float(e.get('confidence',1)),str(e.get('text','')),str(e.get('screenshot','')),str(e.get('source','manual')),int(bool(e.get('verified',0))),str(e.get('evidence','')),e.get('detection_score')))
    return event_id

def save_ocr_event(video_id,e):
    event_id=str(e.get('id',''))
    full_id=event_id if event_id.startswith(video_id+'_') else video_id+'_'+event_id
    start=float(e['time']);end=float(e.get('end_time',start))
    rejected=query("SELECT id FROM rejected_events WHERE video_id=? AND source='ocr' AND (id=? OR (kind=? AND time<? AND end_time>? AND (name=? OR text=?)))",(video_id,full_id,e['kind'],end,start,str(e.get('name','')),str(e.get('text',''))),True)
    if rejected: return None
    reviewed=query("SELECT id FROM events WHERE video_id=? AND source='ocr' AND (verified=1 OR edited=1) AND (id=? OR (kind=? AND time<? AND end_time>? AND (name=? OR text=?)))",(video_id,full_id,e['kind'],end,start,str(e.get('name','')),str(e.get('text',''))),True)
    if reviewed: return reviewed['id']
    overlap=query("SELECT id,time FROM events WHERE video_id=? AND source='ocr' AND kind=? AND name=? AND time<? AND end_time>? ORDER BY time LIMIT 1",(video_id,e['kind'],e['name'],end,start),True)
    if overlap: e={**e,'id':overlap['id'],'time':min(start,overlap['time'])}
    return insert_event(video_id,e)

def import_path(value, auto=True):
    raw=str(value).strip().strip('"')
    p=Path(raw)
    if not p.is_absolute() or not p.is_file() or p.suffix.lower() not in EXTENSIONS: raise ValueError('请选择有效的视频绝对路径：'+raw)
    p=p.resolve()
    st=p.stat()
    fingerprint=hashlib.sha256((str(p).casefold()+':'+str(st.st_size)+':'+str(st.st_mtime_ns)).encode()).hexdigest()
    existing=query('SELECT * FROM videos WHERE fingerprint=?',(fingerprint,),True)
    if existing:
        if auto and existing['status'] in ('ready','error','interrupted','cancelled'): queue_analysis(existing['id'],{})
        return existing['id']
    info=media.probe(p)
    video_id=uid()
    with connect() as db:
        db.execute('INSERT INTO videos(id,path,fingerprint,name,duration,width,height,codec,size,mtime) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (video_id,str(p),fingerprint,p.name,info['duration'],info['width'],info['height'],info['codec'],st.st_size,st.st_mtime))
    for chapter in info.get('chapters',[]):
        title=chapter.get('tags',{}).get('title','章节标记')
        parts=title.split('｜')
        chapter_event={'name':title,'kind':'note','time':float(chapter.get('start_time',0)),'end_time':float(chapter.get('start_time',0)),'source':'chapter','verified':0}
        if len(parts)>=3 and re.fullmatch(r'[a-f0-9]{32}',parts[-1]):
            chapter_event.update(id=parts[-1],kind={'收集物品':'item','击败敌人':'enemy','完成成就':'achievement','路线起点':'note'}.get(parts[0],'note'),name='｜'.join(parts[1:-1]) or parts[0])
        insert_event(video_id,chapter_event)
    if auto: queue_analysis(video_id,{})
    return video_id

def queue_analysis(video_id, options):
    with job_lock:
        video=video_or_404(video_id)
        if video['status'] in ('queued','analyzing'): return
        validated=validate_options(options)
        prior=json.loads(video['options'] or '{}')
        opts={**settings(),**validated}
        resume=video['status'] in ('interrupted','cancelled','error') and prior==opts
        opts['resume_from']=max(0,video['checkpoint']-2*opts['sample_interval']) if resume else 0
        # Fresh scans replace machine candidates only. User-confirmed/edited work is retained.
        if not resume:
            execute("DELETE FROM events WHERE video_id=? AND source='ocr' AND verified=0 AND edited=0",(video_id,))
        flag=threading.Event()
        cancel_flags[video_id]=flag
        update_video(video_id,status='queued',progress=video['progress'] if resume else 0,checkpoint=opts['resume_from'],message='等待识别',options=json.dumps({k:v for k,v in opts.items() if k!='resume_from'},ensure_ascii=False))
        analysis_pool.submit(run_analysis,video_id,opts,flag)

def run_analysis(video_id, opts, flag):
    try:
        if flag.is_set():
            update_video(video_id,status='cancelled',message='已取消，可继续识别'); return
        video=video_or_404(video_id)
        update_video(video_id,status='analyzing',message='正在加载本地文字识别')
        from .analyzer import analyze
        last=[0]
        def on_progress(p):
            if time.monotonic()-last[0]<.7 and float(p.get('progress',0))<1: return
            last[0]=time.monotonic()
            stamp=float(p.get('processed_seconds',p.get('time',0)))
            checkpoint=p.get('checkpoint',stamp)
            if not isinstance(checkpoint,(float,int)): checkpoint=stamp
            update_video(video_id,progress=max(0,min(1,float(p.get('progress',stamp/video['duration'])))),checkpoint=checkpoint,message='已扫描 '+format_time(stamp)+' / '+format_time(video['duration']))
        def on_event(e):
            save_ocr_event(video_id,e)
        analysis_opts=dict(opts)
        if opts.get('resume_from',0)>0:
            resume=float(opts['resume_from'])
            context=query("SELECT name,confidence,end_time FROM events WHERE video_id=? AND source='ocr' AND kind='text' AND evidence='boss_name' AND time<=? AND end_time>=? ORDER BY time DESC LIMIT 1",(video_id,resume,resume-25),True)
            if context:
                analysis_opts['_boss_text_resume']={'name':context['name'],'confidence':context['confidence'],'last_seen':min(resume,context['end_time'])}
        result=analyze(video['path'],DATA/'thumbnails'/video_id,analysis_opts,on_event,on_progress,lambda:flag.is_set() or stop_flag.is_set())
        (DATA/'logs'/(video_id+'-analysis.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        cancelled=flag.is_set() or stop_flag.is_set() or result.get('cancelled',False)
        if cancelled: update_video(video_id,status='cancelled',message='已停止，点击识别可继续')
        else: update_video(video_id,status='done',progress=1,checkpoint=video['duration'],message='识别完成，自动结果待核实')
    except Exception as e:
        logging.exception('Analysis failed: %s',video_id)
        update_video(video_id,status='error',message=str(e)[:500])

def format_time(value):
    n=max(0,int(value)); return f'{n//3600:02}:{n//60%60:02}:{n%60:02}'

def public_event(e):
    e=dict(e)
    e['screenshot_url']='/api/events/'+e['id']+'/screenshot' if e['screenshot'] else ''
    e.pop('screenshot',None)
    if e.get('occurrences'):
        e['occurrences']=[public_event(member) for member in e['occurrences']]
    return e


def catalog_key(video):
    revision=query('SELECT revision FROM event_revisions WHERE video_id=?',(video['id'],),True)
    options=json.loads(video.get('options') or '{}')
    compact=options.get('text_mode','compact')=='compact'
    return (str(DATA),video['id'],revision['revision'] if revision else 0,compact)


def event_catalog(video):
    """Cache a small number of video catalogs, invalidated by SQLite writes."""
    key=catalog_key(video)
    with catalog_lock:
        cached=catalog_cache.get(key)
        if cached is not None:
            catalog_cache.move_to_end(key)
            return cached
    rows=query('SELECT e.*,v.name video_name,v.path video_path FROM events e JOIN videos v ON v.id=e.video_id WHERE e.video_id=? ORDER BY e.time,e.id',(video['id'],))
    result=group_events(rows,compact=key[-1])
    with catalog_lock:
        for old in list(catalog_cache):
            if old[:2]==key[:2]: del catalog_cache[old]
        catalog_cache[key]=result
        catalog_counts[key[:2]]=(key,len(result))
        while len(catalog_cache)>4: catalog_cache.popitem(last=False)
    return result


def catalog_count(video):
    key=catalog_key(video)
    with catalog_lock:
        cached=catalog_counts.get(key[:2])
        if cached and cached[0]==key: return cached[1]
    return len(event_catalog(video))

def sync_markers_once():
    log=DATA/'markers.jsonl'
    if not log.exists(): return
    current=bridge.status() if bridge else {}
    # Never probe a live output. Ended recordings are imported on the next tick.
    if current.get('recording') or current.get('output_active'): return
    with log.open(encoding='utf-8') as source:
        for line in source:
            try: marker=json.loads(line)
            except (ValueError,TypeError): continue
            marker_id=str(marker.get('id',''))
            if not marker_id or query('SELECT id FROM seen_markers WHERE id=?',(marker_id,),True): continue
            path=marker.get('video_path') or marker.get('path')
            if not path or not Path(path).is_file(): continue
            try:
                video_id=import_path(path,False)
                marker.update(source='marker',verified=0)
                insert_event(video_id,marker)
                execute('INSERT OR IGNORE INTO seen_markers VALUES (?)',(marker_id,))
            except Exception: logging.exception('Marker import failed')

def marker_loop():
    while not stop_flag.wait(4):
        try: sync_markers_once()
        except Exception: logging.exception('Marker sync failed')

@asynccontextmanager
async def lifespan(app):
    global bridge
    init_db()
    if os.environ.get('FOOTAGE_TEST')!='1':
        try:
            from .obs_bridge import OBSBridge
            bridge=OBSBridge(ROOT)
            bridge.start()
        except Exception: logging.exception('OBS bridge unavailable')
    threading.Thread(target=marker_loop,daemon=True).start()
    yield
    stop_flag.set()
    for flag in cancel_flags.values(): flag.set()
    if bridge: bridge.stop()
    analysis_pool.shutdown(wait=False,cancel_futures=True)
    media_pool.shutdown(wait=False,cancel_futures=True)

app=FastAPI(title='素材助手',lifespan=lifespan,docs_url=None,redoc_url=None)

@app.middleware('http')
async def local_only(request: Request, call_next):
    host=request.headers.get('host','').split(':')[0]
    if host not in ('127.0.0.1','localhost','testserver'): return JSONResponse({'detail':'仅允许本机访问'},403)
    origin=request.headers.get('origin')
    if origin and urlparse(origin).netloc!=request.headers.get('host'): return JSONResponse({'detail':'拒绝跨站请求'},403)
    if request.headers.get('sec-fetch-site')=='cross-site': return JSONResponse({'detail':'拒绝跨站请求'},403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    return response

@app.get('/')
def index(): return FileResponse(ROOT/'static'/'index.html',headers={'Cache-Control':'no-cache'})

@app.get('/api/state')
def state():
    videos=query('SELECT v.*,(SELECT count(*) FROM events e WHERE e.video_id=v.id) event_count FROM videos v ORDER BY v.created_at DESC,v.rowid DESC')
    for v in videos:
        v.pop('fingerprint',None)
        v['event_count']=catalog_count(v)
    obs=bridge.status() if bridge else {'connected':False,'recording':False,'message':'OBS 标记服务未连接'}
    return {'videos':videos,'jobs':query('SELECT * FROM jobs ORDER BY created_at DESC,rowid DESC LIMIT 20'),'obs':obs,'settings':settings()}

@app.get('/api/settings')
def get_settings(): return settings()

@app.post('/api/settings')
def set_settings(body:dict):
    save_settings(validate_options(body)); return settings()

@app.post('/api/import')
def import_videos(body:dict):
    paths=body.get('paths',[])
    if not isinstance(paths,list) or not 1<=len(paths)<=100: raise HTTPException(400,'每次选择1至100段录像')
    imported=[]; errors=[]
    for path in paths:
        try: imported.append(import_path(path,bool(body.get('auto_analyze',True))))
        except Exception as e: errors.append({'path':str(path),'error':str(e)})
    return {'ids':imported,'errors':errors}

@app.post('/api/pick-files')
def pick_files():
    try:
        p=subprocess.run([sys.executable,str(ROOT/'scripts'/'pick_files.py')],capture_output=True,text=True,encoding='utf-8',creationflags=media.CREATE_NO_WINDOW,timeout=300)
        if p.returncode: raise ValueError(p.stderr[-300:])
        return {'paths':json.loads(p.stdout)}
    except Exception as e: raise HTTPException(500,'文件选择器不可用，请粘贴路径：'+str(e))

@app.post('/api/videos/{video_id}/analyze')
def start_analysis(video_id:str,body:dict):
    queue_analysis(video_id,body); return {'ok':True}

@app.post('/api/videos/{video_id}/cancel')
def cancel_analysis(video_id:str):
    video_or_404(video_id)
    if video_id in cancel_flags: cancel_flags[video_id].set()
    return {'ok':True}

@app.get('/api/events')
def events(video_id:str='',q:str='',kind:str='',verified:str='',limit:int=1000,offset:int=0):
    videos=query('SELECT id,options FROM videos'+(' WHERE id=?' if video_id else '')+' ORDER BY created_at DESC,rowid DESC',(video_id,) if video_id else ())
    offset=max(0,offset);limit=min(max(limit,1),5000)
    key=(tuple(catalog_key(v) for v in videos),q,kind,verified,limit,offset)
    with catalog_lock:
        if key in event_views:
            event_views.move_to_end(key)
            return event_views[key]
    rows=[e for v in videos for e in filter_groups(event_catalog(v),q,kind,verified)]
    result={'events':[public_event(e) for e in rows[offset:offset+limit]],'total':len(rows)}
    with catalog_lock:
        event_views[key]=result
        while len(event_views)>8: event_views.popitem(last=False)
    return result

@app.get('/api/events/{event_id}/screenshot')
def event_screenshot(event_id:str):
    event=query('SELECT screenshot FROM events WHERE id=?',(event_id,),True)
    if not event or not event['screenshot']: raise HTTPException(404,'没有截图')
    path=Path(event['screenshot']).resolve()
    if not path.is_relative_to(DATA.resolve()) or not path.is_file(): raise HTTPException(404,'截图不存在')
    return FileResponse(path)

@app.post('/api/events')
def add_event(body:dict):
    video=video_or_404(body.get('video_id',''))
    kind=body.get('kind','note')
    if kind not in KINDS: raise HTTPException(400,'未知类别')
    stamp=finite(body.get('time',0),'时间',0,video['duration'])
    event_id=uid()
    shot=media.screenshot(video['path'],stamp,DATA/'thumbnails'/video['id']/(event_id+'.jpg'))
    insert_event(video['id'],{**body,'id':event_id,'time':stamp,'screenshot':shot,'source':'manual','verified':1})
    return {'id':event_id}

@app.patch('/api/events/{event_id}')
def edit_event(event_id:str,body:dict):
    row=query('SELECT e.*,v.duration FROM events e JOIN videos v ON e.video_id=v.id WHERE e.id=?',(event_id,),True)
    if not row: raise HTTPException(404,'事件不存在')
    changes={k:body[k] for k in ('name','kind','verified','time','end_time') if k in body}
    if 'name' in changes:
        changes['name']=str(changes['name']).strip()[:300]
        if not changes['name']: raise HTTPException(400,'名称不能为空')
    if 'kind' in changes and changes['kind'] not in KINDS: raise HTTPException(400,'未知类别')
    if 'verified' in changes: changes['verified']=int(bool(changes['verified']))
    for key in ('time','end_time'):
        if key in changes: changes[key]=finite(changes[key],'事件时间',0,row['duration'])
    if changes.get('end_time',row['end_time'])<changes.get('time',row['time']): raise HTTPException(400,'结束时间不能早于起点')
    changes['edited']=1
    execute('UPDATE events SET '+','.join(k+'=?' for k in changes)+' WHERE id=?',(*changes.values(),event_id))
    return {'ok':True}

@app.delete('/api/events/{event_id}')
def delete_event(event_id:str):
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO rejected_events SELECT id,video_id,kind,name,text,time,end_time,source FROM events WHERE id=?',(event_id,))
        db.execute('DELETE FROM events WHERE id=?',(event_id,))
    return {'ok':True}

@app.get('/api/media/{video_id}')
def get_media(video_id:str):
    video=video_or_404(video_id)
    path=video['proxy_path'] if video['proxy_status']=='done' and Path(video['proxy_path']).is_file() else video['path']
    return FileResponse(path,content_disposition_type='inline',headers={'Cache-Control':'no-cache'})

def queue_render(video,kind,start=0,end=None,name=''):
    with job_lock:
        if kind=='proxy':
            existing=query("SELECT id FROM jobs WHERE video_id=? AND kind='proxy' AND status IN ('queued','running')",(video['id'],),True)
            if existing: return existing['id']
        job_id=uid()
        safe=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',name).strip(' .')[:60] or Path(video['name']).stem[:60]
        output=DATA/'proxies'/(video['id']+'.mp4') if kind=='proxy' else ROOT/'exports'/(safe+'_'+format_time(start).replace(':','-')+'_'+job_id[:6]+'.mp4')
        execute('INSERT INTO jobs(id,video_id,kind,status,message,output_path) VALUES (?,?,?,?,?,?)',(job_id,video['id'],kind,'queued','等待处理',str(output)))
        if kind=='proxy': update_video(video['id'],proxy_status='queued')
        media_pool.submit(run_render,job_id,video,kind,output,start,end)
        return job_id

def run_render(job_id,video,kind,output,start,end):
    try:
        update_job(job_id,status='running',message='生成轻量预览' if kind=='proxy' else '正在导出剪映片段')
        if kind=='proxy': update_video(video['id'],proxy_status='running')
        media.render(video['path'],output,video['duration'] if kind=='proxy' else end-start,DATA/'logs'/(job_id+'.log'),lambda p:update_job(job_id,progress=p),start=None if kind=='proxy' else start,end=None if kind=='proxy' else end,proxy=kind=='proxy')
        update_job(job_id,status='done',progress=1,message='已完成')
        if kind=='proxy': update_video(video['id'],proxy_status='done',proxy_path=str(output))
    except Exception as e:
        logging.exception('Render failed')
        update_job(job_id,status='error',message=str(e)[:500])
        if kind=='proxy': update_video(video['id'],proxy_status='error')

@app.post('/api/videos/{video_id}/proxy')
def make_proxy(video_id:str): return {'job_id':queue_render(video_or_404(video_id),'proxy')}

@app.post('/api/export')
def export_clip(body:dict):
    video=video_or_404(body.get('video_id',''))
    start=finite(body.get('start'),'起点',0,video['duration'])
    end=finite(body.get('end'),'终点',0,video['duration'])
    if end<=start: raise HTTPException(400,'终点必须晚于起点')
    return {'job_id':queue_render(video,'export',start,end,str(body.get('name','')))}

@app.get('/api/jobs/{job_id}/download')
def download_job(job_id:str):
    job=query("SELECT * FROM jobs WHERE id=? AND status='done'",(job_id,),True)
    if not job or not Path(job['output_path']).is_file(): raise HTTPException(404,'导出尚未完成')
    return FileResponse(job['output_path'],filename=Path(job['output_path']).name)

@app.post('/api/jobs/{job_id}/reveal')
def reveal_job(job_id:str):
    job=query("SELECT * FROM jobs WHERE id=? AND status='done'",(job_id,),True)
    if not job or not Path(job['output_path']).is_file(): raise HTTPException(404,'文件尚未生成')
    path=Path(job['output_path']).resolve()
    if not path.is_relative_to(ROOT.resolve()): raise HTTPException(400,'输出目录不正确')
    os.startfile(str(path.parent))
    return {'path':str(path)}

@app.get('/api/export/events.csv')
def export_csv(video_id:str=''):
    videos=query('SELECT id,options FROM videos'+(' WHERE id=?' if video_id else '')+' ORDER BY created_at,rowid',(video_id,) if video_id else ())
    rows=[e for v in videos for e in event_catalog(v)]
    output=io.StringIO(newline='');writer=csv.writer(output)
    writer.writerow(['视频文件','时间','秒','类别','名称','已核实','识别文字','来源'])
    def safe(s):
        value=str(s)
        return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
    for e in rows: writer.writerow([e['video_path'],format_time(e['time']),round(e['time'],3),LABELS[e['kind']],safe(e['name']),'是' if e['verified'] else '否',safe(e['text']),e['source']])
    return Response('\ufeff'+output.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="events.csv"'})

@app.post('/api/export/events-file')
def export_csv_file(body:dict):
    output=ROOT/'exports'/('素材事件目录_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uid()[:4]+'.csv')
    output.write_bytes(export_csv(str(body.get('video_id',''))).body)
    os.startfile(str(output.parent))
    return {'path':str(output)}

@app.post('/api/obs/mark')
def obs_mark(body:dict):
    if not bridge: raise HTTPException(503,'OBS标记服务未就绪')
    kind=body.get('kind','note')
    if kind not in {'item','enemy','achievement','note'}: raise HTTPException(400,'此类别请在播放器中添加标记')
    try: return bridge.mark(kind,str(body.get('name',''))[:300])
    except Exception as e: raise HTTPException(409,str(e))

@app.post('/api/shutdown')
def shutdown():
    if query("SELECT id FROM jobs WHERE status IN ('queued','running') LIMIT 1",one=True):
        raise HTTPException(409,'有视频正在处理，请等待导出或预览生成完成后再退出')
    for flag in cancel_flags.values(): flag.set()
    if bridge: bridge.stop()
    callback=getattr(app.state,'request_shutdown',None)
    if callback: threading.Timer(.5,callback).start()
    return {'ok':True}

app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
