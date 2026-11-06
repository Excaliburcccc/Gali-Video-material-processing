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
from . import media, batch_export
from . import projects
from .event_catalog import group_events, filter_groups, library_events, import_order_key, timestamp_events
from .collection_taxonomy import LABELS as COLLECTION_LABELS, COLLECTION_KINDS
from .collection_aliases import verified_name
from .storage import ROOT, DATA, connect, query, execute, init_db, settings, save_settings, update_video, update_job

KINDS={'item','weapon','boss','enemy','achievement','waypoint','quest','text','note'} | COLLECTION_KINDS
LABELS={'item':'收集','weapon':'武器','boss':'BOSS','enemy':'击败','achievement':'成就','waypoint':'传送点','quest':'任务','text':'画面文字','note':'备注',**COLLECTION_LABELS}
EXTENSIONS={'.mp4','.mkv','.mov','.avi','.webm','.m4v','.ts'}
analysis_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='ocr')
media_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='media')
trial_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='sample-ocr')
trials=OrderedDict()
cancel_flags={}
job_lock=threading.RLock()
bridge=None
stop_flag=threading.Event()
catalog_cache=OrderedDict()
catalog_counts={}
event_views=OrderedDict()
library_cache=OrderedDict()
catalog_lock=threading.RLock()
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',handlers=[logging.FileHandler(DATA/'logs'/'server.log',encoding='utf-8')])

def uid(): return uuid.uuid4().hex

def project_or_404(project_id=None):
    try: return projects.get_project(project_id)
    except ValueError as exc: raise HTTPException(404,str(exc))

def video_project(video_id):
    row=query('SELECT project_id FROM videos WHERE id=?',(video_id,),True)
    return row['project_id'] if row else projects.DEFAULT_PROJECT

def project_kinds(project_id):
    return KINDS | projects.known_kinds(project_id)

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
    values={k:body[k] for k in ('sample_interval','max_width','regions','keywords','game_profile','text_mode','recognition_scope') if k in body}
    if 'recognition_scope' in values and values['recognition_scope'] not in ('collection_only','legacy'):
        raise HTTPException(400,'未知的识别范围')
    if values.get('recognition_scope')=='collection_only': values['text_mode']='off'
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

def insert_event(video_id, e, allowed_kinds=None):
    event_id=str(e.get('id') or uid())
    scoped_source=(e.get('source')=='ocr' or
                   (e.get('source') in {'chapter','marker'} and video_project(video_id)!=projects.DEFAULT_PROJECT))
    if scoped_source and not event_id.startswith(video_id+'_'):
        event_id=video_id+'_'+event_id
    kind=e.get('kind','note')
    if kind not in (allowed_kinds if allowed_kinds is not None else project_kinds(video_project(video_id))): kind='note'
    stamp=max(0,float(e.get('time',0)))
    name=str(e.get('name') or projects.labels(video_project(video_id)).get(kind,LABELS.get(kind,kind)))[:300]
    with connect() as db:
        db.execute('''INSERT INTO events(id,video_id,kind,name,time,end_time,confidence,text,screenshot,source,verified,evidence,detection_score)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
name=CASE WHEN events.source='ocr' AND (events.kind='text' OR (events.kind='boss' AND events.evidence='boss_name' AND excluded.evidence='boss_name')) THEN excluded.name ELSE events.name END,
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

def save_ocr_event(video_id,e,profile=None):
    # Correct only audited OCR aliases. The evidence text and source frame
    # remain unchanged; the global catalog chooses representatives by import
    # order on every revision, independent of recognition completion order.
    profile=profile or projects.project_profile(video_project(video_id))
    allowed_kinds=KINDS | {c['id'] for c in profile['categories']}
    if profile['adapter']!='phantom_blade_zero_demo':
        event_id=str(e.get('id',''))
        full_id=event_id if event_id.startswith(video_id+'_') else video_id+'_'+event_id
        if query('SELECT id FROM rejected_events WHERE id=?',(full_id,),True): return None
        return insert_event(video_id,e,allowed_kinds)
    e={**e,'name':verified_name(e['kind'],e['name'])}
    event_id=str(e.get('id',''))
    full_id=event_id if event_id.startswith(video_id+'_') else video_id+'_'+event_id
    start=float(e['time']);end=float(e.get('end_time',start))
    if e['kind']=='book':
        # Book pages can share both a title and overlapping display intervals.
        # A saved frame at the exact same timestamp is the only cross-ID proof
        # of duplication. Rejections lack screenshots, so honor their ID only.
        if query("SELECT id FROM rejected_events WHERE video_id=? AND source='ocr' AND id=?",(video_id,full_id),True):
            return None
        shot=str(e.get('screenshot') or '')
        name=str(e.get('name',''))
        reviewed=query("SELECT id FROM events WHERE video_id=? AND source='ocr' AND (verified=1 OR edited=1) AND (id=? OR (kind='book' AND time=? AND name=? AND screenshot=? AND screenshot!='')) ORDER BY id LIMIT 1",(video_id,full_id,start,name,shot),True)
        if reviewed: return reviewed['id']
        duplicate=query("SELECT id FROM events WHERE video_id=? AND source='ocr' AND kind='book' AND time=? AND name=? AND screenshot=? AND screenshot!='' ORDER BY id LIMIT 1",(video_id,start,name,shot),True)
        if duplicate: e={**e,'id':duplicate['id']}
        return insert_event(video_id,e,allowed_kinds)
    rejected=query("SELECT id FROM rejected_events WHERE video_id=? AND source='ocr' AND (id=? OR (kind=? AND time<? AND end_time>? AND (name=? OR text=?)))",(video_id,full_id,e['kind'],end,start,str(e.get('name','')),str(e.get('text',''))),True)
    if rejected: return None
    reviewed=query("SELECT id FROM events WHERE video_id=? AND source='ocr' AND (verified=1 OR edited=1) AND (id=? OR (kind=? AND time<? AND end_time>? AND (name=? OR text=?)))",(video_id,full_id,e['kind'],end,start,str(e.get('name','')),str(e.get('text',''))),True)
    if reviewed: return reviewed['id']
    overlap=query("SELECT id,time FROM events WHERE video_id=? AND source='ocr' AND kind=? AND name=? AND time<? AND end_time>? ORDER BY time LIMIT 1",(video_id,e['kind'],e['name'],end,start),True)
    if overlap: e={**e,'id':overlap['id'],'time':min(start,overlap['time'])}
    return insert_event(video_id,e,allowed_kinds)

def import_path(value, auto=True, project_id=None):
    project=project_or_404(project_id)
    raw=str(value).strip().strip('"')
    p=Path(raw)
    if not p.is_absolute() or not p.is_file() or p.suffix.lower() not in EXTENSIONS: raise ValueError('请选择有效的视频绝对路径：'+raw)
    p=p.resolve()
    st=p.stat()
    fingerprint=hashlib.sha256((str(p).casefold()+':'+str(st.st_size)+':'+str(st.st_mtime_ns)).encode()).hexdigest()
    if project['id']!=projects.DEFAULT_PROJECT:
        fingerprint=hashlib.sha256((project['id']+':'+fingerprint).encode()).hexdigest()
    existing=query('SELECT * FROM videos WHERE fingerprint=?',(fingerprint,),True)
    if existing:
        if auto and existing['status'] in ('ready','error','interrupted','cancelled'): queue_analysis(existing['id'],{})
        return existing['id']
    info=media.probe(p)
    video_id=uid()
    with connect() as db:
        db.execute('INSERT INTO videos(id,path,fingerprint,name,duration,width,height,codec,size,mtime,project_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (video_id,str(p),fingerprint,p.name,info['duration'],info['width'],info['height'],info['codec'],st.st_size,st.st_mtime,project['id']))
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
        opts={**projects.project_settings(video['project_id']),**validated}
        profile=projects.project_profile(video['project_id'])
        opts.update(_profile=profile,game_profile=profile['adapter'],
                    recognition_scope='collection_only' if profile['adapter']=='phantom_blade_zero_demo' else 'legacy',
                    text_mode='off',ocr_cache_path=str(DATA/'ocr-cache'/(video_id+'.sqlite3')))
        if opts.get('recognition_scope')=='collection_only': opts['text_mode']='off'
        resume=video['status'] in ('interrupted','cancelled','error') and prior==opts
        opts['resume_from']=max(0,video['checkpoint']-2*opts['sample_interval']) if resume else 0
        # Fresh scans replace machine candidates only. User-confirmed/edited work is retained.
        if not resume:
            execute("DELETE FROM events WHERE video_id=? AND source='ocr' AND verified=0 AND edited=0",(video_id,))
        flag=threading.Event()
        cancel_flags[video_id]=flag
        update_video(video_id,status='queued',progress=video['progress'] if resume else 0,checkpoint=opts['resume_from'],message='等待识别',options=json.dumps({k:v for k,v in opts.items() if k!='resume_from'},ensure_ascii=False))
        history=DATA/'recognition-runs'
        history.mkdir(exist_ok=True)
        (history/(uid()+'.json')).write_text(json.dumps({'video_id':video_id,'project_id':video['project_id'],
            'submitted_at':time.time(),'options':opts},ensure_ascii=False,indent=2),encoding='utf-8')
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
            save_ocr_event(video_id,e,opts.get('_profile'))
        analysis_opts=dict(opts)
        if opts.get('resume_from',0)>0:
            resume=float(opts['resume_from'])
            context=query("SELECT name,confidence,end_time FROM events WHERE video_id=? AND source='ocr' AND kind IN ('text','boss') AND evidence='boss_name' AND time<=? AND end_time>=? ORDER BY time DESC LIMIT 1",(video_id,resume,resume-25),True)
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
    focused=options.get('recognition_scope')=='collection_only'
    profile=projects.project_profile(video.get('project_id',projects.DEFAULT_PROJECT))
    return (str(DATA),video['id'],revision['revision'] if revision else 0,compact,focused,
            video.get('name',''),video.get('path',''),profile['id'],profile['version'])


def event_catalog(video):
    """Cache a small number of video catalogs, invalidated by SQLite writes."""
    key=catalog_key(video)
    with catalog_lock:
        cached=catalog_cache.get(key)
        if cached is not None:
            catalog_cache.move_to_end(key)
            return cached
    rows=query('SELECT e.*,v.name video_name,v.path video_path FROM events e JOIN videos v ON v.id=e.video_id WHERE e.video_id=? ORDER BY e.time,e.id',(video['id'],))
    profile=projects.project_profile(video.get('project_id',projects.DEFAULT_PROJECT))
    if profile['id']=='phantom_blade_zero_demo' and profile['version']==1:
        result=group_events(rows,compact=key[3],collection_only=key[4])
    else:
        from .game_schemes import catalog_events
        result=catalog_events(rows,[video],profile)
    with catalog_lock:
        for old in list(catalog_cache):
            if old[:2]==key[:2]: del catalog_cache[old]
        catalog_cache[key]=result
        catalog_counts[key[:2]]=(key,len(result))
        while len(catalog_cache)>32: catalog_cache.popitem(last=False)
    return result


def catalog_count(video):
    key=catalog_key(video)
    with catalog_lock:
        cached=catalog_counts.get(key[:2])
        if cached and cached[0]==key: return cached[1]
    return len(event_catalog(video))


def library_snapshot(videos=None,project_id=None):
    """One chronological catalog shared by search, counts, timeline and CSV."""
    project=project_or_404(project_id)
    if videos is None: videos=query('SELECT v.*,v.rowid AS import_order FROM videos v WHERE v.project_id=?',(project['id'],))
    videos=[v for v in videos if v.get('project_id',projects.DEFAULT_PROJECT)==project['id']]
    videos=sorted(videos,key=import_order_key)
    profile=projects.project_profile(project['id'])
    key=(str(DATA),project['id'],profile['id'],profile['version'],tuple((catalog_key(video),import_order_key(video)) for video in videos))
    with catalog_lock:
        cached=library_cache.get(key)
        if cached is not None:
            library_cache.move_to_end(key)
            return key,cached
    if profile['id']=='phantom_blade_zero_demo' and profile['version']==1:
        rows=library_events([event for video in videos for event in event_catalog(video)],videos)
    else:
        from .game_schemes import catalog_events
        # Apply the selected policy to raw evidence, so changing a policy never
        # depends on candidates already hidden by a previous policy.
        raw=query('SELECT e.*,v.name video_name,v.path video_path FROM events e JOIN videos v ON v.id=e.video_id WHERE v.project_id=? ORDER BY v.rowid,e.time,e.id',(project['id'],))
        allowed={v['id'] for v in videos}
        rows=catalog_events([e for e in raw if e['video_id'] in allowed],videos,profile)
    with catalog_lock:
        library_cache[key]=rows
        while len(library_cache)>2: library_cache.popitem(last=False)
    return key,rows

def sync_markers_once():
    log=DATA/'markers.jsonl'
    current=bridge.status() if bridge else {}
    # Never probe a live output. Ended recordings are imported on the next tick.
    if current.get('recording') or current.get('output_active'):
        projects.bind_recording(current.get('video_path'))
        return
    if not log.exists(): return
    with log.open(encoding='utf-8') as source:
        for line in source:
            try: marker=json.loads(line)
            except (ValueError,TypeError): continue
            marker_id=str(marker.get('id',''))
            if not marker_id or query('SELECT id FROM seen_markers WHERE id=?',(marker_id,),True): continue
            path=marker.get('video_path') or marker.get('path')
            if not path or not Path(path).is_file(): continue
            try:
                video_id=import_path(path,False,projects.recording_project(path))
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
    trial_pool.shutdown(wait=False,cancel_futures=True)

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
    if request.url.path.startswith('/static/'):
        response.headers['Cache-Control']='no-cache'
    return response

@app.get('/')
def index(): return FileResponse(ROOT/'static'/'index.html',headers={'Cache-Control':'no-cache'})

@app.get('/api/state')
def state(project_id:str=''):
    project=project_or_404(project_id)
    videos=query('SELECT v.*,v.rowid AS import_order,(SELECT count(*) FROM events e WHERE e.video_id=v.id) event_count FROM videos v WHERE v.project_id=? ORDER BY v.rowid',(project['id'],))
    _,catalog=library_snapshot(videos,project['id'])
    counts={video['id']:0 for video in videos}
    for event in catalog: counts[event['video_id']]+=1
    for v in videos:
        v.pop('fingerprint',None)
        v['detected_event_count']=catalog_count(v)
        v['event_count']=counts[v['id']]
    videos.sort(key=import_order_key)
    obs=bridge.status() if bridge else {'connected':False,'recording':False,'message':'OBS 标记服务未连接'}
    profile=projects.project_profile(project['id'])
    return {'videos':videos,'event_count':len(catalog),'jobs':query('SELECT j.* FROM jobs j JOIN videos v ON v.id=j.video_id WHERE v.project_id=? ORDER BY j.created_at DESC,j.rowid DESC LIMIT 20',(project['id'],)),
            'obs':obs,'settings':projects.project_settings(project['id']), 'active_project_id':project['id'],
            'project':project,'projects':projects.project_list(),'profile':profile,
            'profiles':[{k:p[k] for k in ('id','name','adapter','version','language')} for p in projects.profile_list()],
            'categories':profile['categories']}

@app.get('/api/settings')
def get_settings(project_id:str=''): return projects.project_settings(project_or_404(project_id)['id'])

@app.post('/api/settings')
def set_settings(body:dict):
    project=project_or_404(body.get('project_id'))
    return projects.update_settings(project['id'],validate_options(body))

@app.get('/api/projects')
def get_projects():
    return {'projects':projects.project_list(),'active_project_id':projects.active_id()}

@app.post('/api/projects')
def new_project(body:dict):
    try:
        project=projects.create_project(body.get('name',''),body.get('profile_id','generic'),body.get('profile_version'))
        return {'project':project,'id':project['id']}
    except (ValueError,TypeError) as exc: raise HTTPException(400,str(exc))

@app.post('/api/projects/{project_id}/activate')
def activate_project(project_id:str):
    project_or_404(project_id)
    projects.activate(project_id)
    return {'project':projects.get_project(project_id),'active_project_id':project_id}

@app.get('/api/profiles')
def get_profiles():
    return {'profiles':projects.profile_list()}

@app.post('/api/profiles')
def new_profile(body:dict):
    try: return {'profile':projects.save_profile(body.get('profile',body))}
    except (ValueError,TypeError,KeyError) as exc: raise HTTPException(400,str(exc))

@app.get('/api/profiles/{profile_id}')
def get_profile(profile_id:str,version:int|None=None):
    try: return projects.get_profile(profile_id,version)
    except ValueError as exc: raise HTTPException(404,str(exc))

@app.put('/api/profiles/{profile_id}')
def update_profile(profile_id:str,body:dict):
    try:
        with job_lock:
            profile=projects.save_profile(body.get('profile',body),profile_id,body.get('project_id'))
        return {'profile':profile}
    except (ValueError,TypeError,KeyError) as exc: raise HTTPException(400,str(exc))

@app.get('/api/videos/{video_id}/frame')
def calibration_frame(video_id:str,time:float=0):
    video=video_or_404(video_id)
    stamp=finite(time,'样例时间',0,max(0,video['duration']-.001))
    frame_id=hashlib.sha256(f'{video["fingerprint"]}:{stamp:.3f}'.encode()).hexdigest()[:20]
    target=DATA/'calibration'/video_id/(frame_id+'.jpg')
    if not target.is_file() and not media.screenshot(video['path'],stamp,target):
        raise HTTPException(422,'无法读取该位置的画面，请换一个时间')
    return FileResponse(target,headers={'Cache-Control':'no-cache'})

def run_trial(trial_id,video,profile,opts):
    from .analyzer import analyze
    from .game_schemes import catalog_events
    folder=DATA/'trials'/trial_id
    folder.mkdir(parents=True,exist_ok=True)
    captured={}
    try:
        with job_lock: trials[trial_id].update(status='running',message='正在试识别')
        def on_event(event):
            captured[event['id']]={**event,'video_id':video['id'],'video_name':video['name'],'video_path':video['path']}
        def on_progress(progress):
            with job_lock: trials[trial_id]['progress']=progress.get('progress',0)
        result=analyze(video['path'],folder,opts,on_event,on_progress,lambda:stop_flag.is_set())
        raw=list(captured.values())
        events=catalog_events(raw,[video],profile)
        public=[]
        for index,event in enumerate(events):
            row={**event,'screenshot_url':f'/api/trials/{trial_id}/screenshots/{index}' if event.get('screenshot') else ''}
            row.pop('screenshot',None)
            public.append(row)
        with job_lock:
            trials[trial_id].update(status='done',progress=1,events=public,_events=events,result=result,
                                   message=f'试识别完成，找到 {len(events)} 条记录')
        # Retain the tested scheme and evidence as a calibration sample, never
        # inserting trial candidates into the actual video catalog.
        (folder/'sample.json').write_text(json.dumps({'profile':profile,'video_id':video['id'],
            'start':opts['resume_from'],'end':opts['analysis_end'],'events':events,'result':result},ensure_ascii=False,indent=2),encoding='utf-8')
    except Exception as exc:
        logging.exception('Sample recognition failed: %s',trial_id)
        with job_lock: trials[trial_id].update(status='error',error=str(exc)[:500],message='试识别失败')

@app.post('/api/profiles/preview')
def preview_profile(body:dict):
    from .game_schemes import validate_profile
    video=video_or_404(body.get('video_id',''))
    start=finite(body.get('start',0),'起点',0,video['duration'])
    end=finite(body.get('end',min(start+20,video['duration'])),'终点',0,video['duration'])
    if not 0<end-start<=60: raise HTTPException(400,'每次试识别请选择1分钟以内的片段，终点须晚于起点')
    draft=body.get('profile')
    if not isinstance(draft,dict): raise HTTPException(400,'请提供待测试的游戏方案')
    try: profile=validate_profile({'id':'trial','version':1,**draft})
    except (ValueError,TypeError,KeyError) as exc: raise HTTPException(400,str(exc))
    opts=projects.project_settings(video['project_id'])
    opts.update(_profile=profile,game_profile=profile['adapter'],text_mode='off',
                recognition_scope='collection_only' if profile['adapter']=='phantom_blade_zero_demo' else 'legacy',
                resume_from=start,analysis_end=end,ocr_cache_path=str(DATA/'ocr-cache'/(video['id']+'.sqlite3')))
    trial_id=uid()
    with job_lock:
        if sum(t['status'] in ('queued','running') for t in trials.values())>=3:
            raise HTTPException(409,'已有样例等待识别，请等完成后再试')
        for key in list(trials):
            if len(trials)<30: break
            if trials[key]['status'] not in ('queued','running'): del trials[key]
        trials[trial_id]={'id':trial_id,'status':'queued','video_id':video['id'],'start':start,'end':end,
                          'events':[],'progress':0,'message':'等待试识别'}
        trial_pool.submit(run_trial,trial_id,video,profile,opts)
    return {'id':trial_id,'status':'queued'}

@app.get('/api/trials/{trial_id}')
def get_trial(trial_id:str):
    with job_lock:
        trial=trials.get(trial_id)
        if trial is None: raise HTTPException(404,'试识别记录已过期，请重新试识别')
        return {k:v for k,v in trial.items() if not k.startswith('_')}

@app.get('/api/trials/{trial_id}/screenshots/{index}')
def trial_screenshot(trial_id:str,index:int):
    with job_lock:
        events=trials.get(trial_id,{}).get('_events',[])
        if not 0<=index<len(events): raise HTTPException(404,'没有该样例截图')
        shot=Path(events[index].get('screenshot','')).resolve()
    if not shot.is_relative_to((DATA/'trials'/trial_id).resolve()) or not shot.is_file():
        raise HTTPException(404,'样例截图不存在')
    return FileResponse(shot)

@app.post('/api/import')
def import_videos(body:dict):
    paths=body.get('paths',[])
    if not isinstance(paths,list) or not 1<=len(paths)<=100: raise HTTPException(400,'每次选择1至100段录像')
    imported=[]; errors=[]
    for path in paths:
        try: imported.append(import_path(path,bool(body.get('auto_analyze',True)),body.get('project_id')))
        except Exception as e: errors.append({'path':str(path),'error':str(e)})
    return {'ids':imported,'errors':errors}

@app.post('/api/pick-files')
def pick_files():
    try:
        script=Path(__file__).resolve().parents[1]/'scripts'/'pick_files.py'
        executable=Path(sys.executable)
        if sys.platform=='win32':
            # The Windows venv GUI redirector may start console python.exe.
            # This standalone, stdlib-only helper can use the real base GUI
            # executable directly, without a redirector or a command window.
            base=Path(sys.base_prefix)
            for name in ('pythonw.exe','python.exe'):
                candidate=base/name
                if candidate.is_file():
                    executable=candidate
                    break
        p=subprocess.run(
            [str(executable),'-X','utf8',str(script)],
            stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',errors='replace',
            env={**os.environ,'PYTHONIOENCODING':'utf-8'},
            creationflags=media.CREATE_NO_WINDOW,timeout=300)
        if p.returncode:
            raise RuntimeError((p.stderr or p.stdout or f'选择器退出，状态码 {p.returncode}')[-1500:])
        paths=json.loads(p.stdout or '')
        if not isinstance(paths,list) or any(not isinstance(path,str) for path in paths):
            raise ValueError('选择器返回的文件路径格式无效')
        return {'paths':paths}
    except subprocess.TimeoutExpired:
        raise HTTPException(408,'选择文件已超时，请重新点击“选择本地录像”。')
    except Exception:
        logging.exception('Local video file picker failed')
        raise HTTPException(500,'文件选择器未能打开，请重试或粘贴录像的完整路径。')

@app.post('/api/videos/{video_id}/analyze')
def start_analysis(video_id:str,body:dict):
    queue_analysis(video_id,body); return {'ok':True}

@app.post('/api/videos/{video_id}/cancel')
def cancel_analysis(video_id:str):
    video_or_404(video_id)
    if video_id in cancel_flags: cancel_flags[video_id].set()
    return {'ok':True}

@app.get('/api/events')
def events(video_id:str='',q:str='',kind:str='',verified:str='',limit:int=1000,offset:int=0,scope:str='library',project_id:str=''):
    library_key,catalog=library_snapshot(project_id=project_id)
    # Apply the video filter AFTER global dedup, including legacy scope=video
    # callers. Never reintroduce an older collectible via a narrower view.
    offset=max(0,offset);limit=min(max(limit,1),5000)
    key=(library_key,video_id,q,kind,verified,limit,offset,scope)
    with catalog_lock:
        if key in event_views:
            event_views.move_to_end(key)
            return event_views[key]
    rows=[e for e in filter_groups(catalog,q,kind,verified) if not video_id or e['video_id']==video_id]
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
    if kind not in project_kinds(video['project_id']): raise HTTPException(400,'未知类别')
    stamp=finite(body.get('time',0),'时间',0,video['duration'])
    event_id=uid()
    shot=media.screenshot(video['path'],stamp,DATA/'thumbnails'/video['id']/(event_id+'.jpg'))
    insert_event(video['id'],{**body,'id':event_id,'time':stamp,'screenshot':shot,'source':'manual','verified':1})
    return {'id':event_id}

@app.patch('/api/events/{event_id}')
def edit_event(event_id:str,body:dict):
    row=query('SELECT e.*,v.duration,v.project_id FROM events e JOIN videos v ON e.video_id=v.id WHERE e.id=?',(event_id,),True)
    if not row: raise HTTPException(404,'事件不存在')
    changes={k:body[k] for k in ('name','kind','verified','time','end_time') if k in body}
    if 'name' in changes:
        changes['name']=str(changes['name']).strip()[:300]
        if not changes['name']: raise HTTPException(400,'名称不能为空')
    if 'kind' in changes and changes['kind'] not in project_kinds(row['project_id']): raise HTTPException(400,'未知类别')
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

def batch_export_plan(body):
    """Resolve selection IDs against the same project catalog used by the UI.

    Never trust client-supplied paths or timestamps, or fall back to raw OCR
    rows that the catalog removed. A queue entry owns its own frozen plan.
    """
    project_id=body.get('project_id')
    if not isinstance(project_id,str) or not project_id:
        raise HTTPException(400,'请先选择项目')
    project=project_or_404(project_id)
    mode=body.get('mode','separate')
    if mode not in ('separate','merge'):
        raise HTTPException(400,'请选择分别导出或合并导出')
    items=body.get('items')
    if not isinstance(items,list) or not 1<=len(items)<=5000:
        raise HTTPException(400,'请选择 1 至 5000 个片段')

    def padding(value,label):
        if isinstance(value,bool): raise HTTPException(400,label+'必须是数字')
        return finite(value,label,0,86400)

    before=padding(body.get('before',5),'时间戳前秒数')
    after=padding(body.get('after',5),'时间戳后秒数')
    requested={}
    for item in items:
        if not isinstance(item,dict) or not isinstance(item.get('event_id'),str) or not item['event_id']:
            raise HTTPException(400,'片段需要有效的事件编号')
        event_id=item['event_id']
        if event_id in requested:
            raise HTTPException(400,'同一事件不能重复选择')
        pre=padding(item.get('before',before),'时间戳前秒数')
        post=padding(item.get('after',after),'时间戳后秒数')
        if pre+post<=0: raise HTTPException(400,'时间戳前后秒数不能同时为 0')
        requested[event_id]=(pre,post)
    _,catalog=library_snapshot(project_id=project['id'])
    rows=timestamp_events([row for row in catalog if row['id'] in requested],catalog)
    if len(rows)!=len(requested):
        raise HTTPException(409,'部分所选事件已删除、去重或不属于当前项目，请刷新列表后重新选择')
    videos=query('SELECT id,rowid AS import_order FROM videos WHERE project_id=? ORDER BY rowid',(project['id'],))
    order={video['id']:i for i,video in enumerate(videos)}
    rows.sort(key=lambda e:(order.get(e['video_id'],len(order)),e['time'],e['id']))
    sources={}
    clips=[]
    for event in rows:
        video_id=event['video_id']
        if video_id not in sources: sources[video_id]=video_or_404(video_id)
        video=sources[video_id]
        stamp=finite(event['time'],'事件时间',0,video['duration'])
        pre,post=requested[event['id']]
        start=max(0,stamp-pre)
        end=min(float(video['duration']),stamp+post)
        if end<=start:
            raise HTTPException(400,'“'+event['name']+'”的导出范围为空，请调整前后秒数')
        clips.append({'event_id':event['id'],'video_id':video_id,'video_name':video['name'],
                      'name':event['name'],'kind':event['kind'],'time':stamp,
                      'before':pre,'after':post,'start':start,'end':end,'duration':end-start,
                      'path':video['path'],'source_size':video['size'],'source_mtime':video['mtime']})
    return {'project_id':project['id'],'mode':mode,'name':str(body.get('name') or '批量片段')[:160],
            'clips':clips,'count':len(clips),'duration':sum(c['duration'] for c in clips)}

@app.post('/api/export/batch/preview')
def preview_batch_export(body:dict):
    plan=batch_export_plan(body)
    return {**{k:plan[k] for k in ('count','duration','mode')},
            'clips':[{k:v for k,v in clip.items() if k not in ('path','source_size','source_mtime')} for clip in plan['clips']]}

def run_batch_render(job_id,plan,output_dir):
    try:
        # The source must still match the plan when its turn in the media queue
        # arrives; later event edits must not move already-submitted clips.
        for clip in {c['video_id']:c for c in plan['clips']}.values():
            source=Path(clip['path'])
            if not source.is_file(): raise ValueError('原视频已移动或不可用：'+clip['video_name'])
            stat=source.stat()
            if stat.st_size!=clip['source_size'] or abs(stat.st_mtime-clip['source_mtime'])>.001:
                raise ValueError('原录像已改变，请重新导入后导出：'+clip['video_name'])
        update_job(job_id,status='running',message=f'正在{"合并" if plan["mode"]=="merge" else "分别"}导出 {plan["count"]} 个片段')
        last=[0.0]
        def progress(value):
            now=time.monotonic()
            if now-last[0]>.5 or value>=1:
                update_job(job_id,progress=min(.999,max(0,float(value))))
                last[0]=now
        result=batch_export.render_batch(plan['clips'],output_dir,plan['mode'],plan['name'],progress)
        update_job(job_id,status='done',progress=1,output_path=result['output_path'],
                   message=f'已导出 {len(result["files"])} 个视频')
    except Exception as exc:
        logging.exception('Batch export failed')
        update_job(job_id,status='error',message=str(exc)[:500])

@app.post('/api/export/batch')
def export_batch(body:dict):
    plan=batch_export_plan(body)
    with job_lock:
        job_id=uid()
        safe=batch_export.safe_name(plan['name'],'批量片段')
        output_dir=ROOT/'exports'/(safe+'_'+time.strftime('%Y%m%d_%H%M%S')+'_'+job_id[:8])
        execute('INSERT INTO jobs(id,video_id,kind,status,message,output_path) VALUES (?,?,?,?,?,?)',
                (job_id,plan['clips'][0]['video_id'],'export_batch','queued',f'等待导出 {plan["count"]} 个片段',str(output_dir)))
        try: media_pool.submit(run_batch_render,job_id,plan,output_dir)
        except Exception:
            update_job(job_id,status='error',message='导出队列已关闭，请重启素材助手后重试')
            raise HTTPException(503,'导出队列已关闭，请重启素材助手后重试')
    return {'job_id':job_id,**{k:plan[k] for k in ('count','duration','mode')}}

@app.get('/api/jobs/{job_id}/download')
def download_job(job_id:str):
    job=query("SELECT * FROM jobs WHERE id=? AND status='done'",(job_id,),True)
    if not job or not Path(job['output_path']).is_file(): raise HTTPException(404,'导出尚未完成')
    return FileResponse(job['output_path'],filename=Path(job['output_path']).name)

@app.post('/api/jobs/{job_id}/reveal')
def reveal_job(job_id:str):
    job=query("SELECT * FROM jobs WHERE id=? AND status='done'",(job_id,),True)
    if not job or not Path(job['output_path']).exists(): raise HTTPException(404,'文件尚未生成')
    path=Path(job['output_path']).resolve()
    if not path.is_relative_to(ROOT.resolve()): raise HTTPException(400,'输出目录不正确')
    os.startfile(str(path if path.is_dir() else path.parent))
    return {'path':str(path)}

@app.get('/api/export/events.csv')
def export_csv(video_id:str='',scope:str='library',project_id:str=''):
    _,catalog=library_snapshot(project_id=project_id)
    rows=[e for e in catalog if not video_id or e['video_id']==video_id]
    output=io.StringIO(newline='');writer=csv.writer(output)
    writer.writerow(['视频文件','时间','秒','类别','名称','已核实','识别文字','来源'])
    def safe(s):
        value=str(s)
        return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
    labels=projects.labels(project_id)
    for e in rows: writer.writerow([safe(e['video_path']),format_time(e['time']),round(e['time'],3),safe(labels.get(e['kind'],LABELS.get(e['kind'],e['kind']))),safe(e['name']),'是' if e['verified'] else '否',safe(e['text']),e['source']])
    return Response('\ufeff'+output.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="events.csv"'})

@app.post('/api/export/events-file')
def export_csv_file(body:dict):
    output=ROOT/'exports'/('素材事件目录_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uid()[:4]+'.csv')
    output.write_bytes(export_csv(str(body.get('video_id','')),str(body.get('scope','library')),str(body.get('project_id',''))).body)
    os.startfile(str(output.parent))
    return {'path':str(output)}

@app.post('/api/obs/mark')
def obs_mark(body:dict):
    if not bridge: raise HTTPException(503,'OBS标记服务未就绪')
    kind=body.get('kind','note')
    if kind not in {'item','enemy','achievement','note'}: raise HTTPException(400,'此类别请在播放器中添加标记')
    try:
        result=bridge.mark(kind,str(body.get('name',''))[:300])
        projects.bind_recording(result.get('video_path') or bridge.status().get('video_path'))
        return result
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
