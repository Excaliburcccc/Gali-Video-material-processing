import json
import os
import shutil
import subprocess
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0
FFMPEG = shutil.which('ffmpeg') or r'D:\ffmpeg-7.1-full_build\bin\ffmpeg.exe'
FFPROBE = shutil.which('ffprobe') or str(Path(FFMPEG).with_name('ffprobe.exe'))

def probe(path):
    result = subprocess.run([FFPROBE,'-v','error','-show_format','-show_streams','-show_chapters','-of','json',str(path)], capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=CREATE_NO_WINDOW, timeout=45)
    if result.returncode:
        raise ValueError('无法读取录像：'+result.stderr[-500:])
    data = json.loads(result.stdout)
    video = next((x for x in data.get('streams',[]) if x.get('codec_type')=='video'),None)
    if video is None:
        raise ValueError('文件没有视频轨道')
    duration = float(data.get('format',{}).get('duration') or video.get('duration') or 0)
    if duration <= 0:
        raise ValueError('录像时长不可读，请结束录制后再导入')
    return {'duration':duration,'width':int(video.get('width',0)),'height':int(video.get('height',0)),'codec':video.get('codec_name',''),'chapters':data.get('chapters',[])}

def screenshot(path, time, output):
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    result=subprocess.run([FFMPEG,'-hide_banner','-loglevel','error','-ss',str(max(0,time)),'-i',str(path),'-frames:v','1','-vf','scale=960:-2','-y',str(output)],capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=35)
    return str(output) if result.returncode==0 and output.exists() else ''

def render(path, output, duration, logfile, progress, start=None, end=None, proxy=False):
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    partial=output.with_name(output.stem+'.partial.mp4')
    args=[FFMPEG,'-hide_banner','-nostdin','-loglevel','error','-y']
    if start is not None:
        args += ['-ss',str(start)]
    args += ['-i',str(path)]
    if end is not None:
        args += ['-t',str(end-start)]
    args += ['-map','0:v:0','-map','0:a:0?']
    if proxy:
        args += ['-vf',"scale=w='min(1280,iw)':h=-2",'-c:v','libx264','-preset','veryfast','-crf','26','-threads','4']
    else:
        args += ['-c:v','libx264','-preset','fast','-crf','18','-threads','6']
    args += ['-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-movflags','+faststart','-progress','pipe:1',str(partial)]
    try:
        with open(logfile,'w',encoding='utf-8') as log:
            proc=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=log,text=True,encoding='utf-8',errors='replace',creationflags=CREATE_NO_WINDOW)
            for line in proc.stdout:
                if line.startswith('out_time_us='):
                    try: progress(min(.99,float(line.strip().split('=')[1])/1e6/max(duration,.01)))
                    except ValueError: pass
            rc=proc.wait()
        if rc:
            raise RuntimeError('视频处理失败：'+Path(logfile).read_text(encoding='utf-8')[-600:])
        os.replace(partial,output)
        progress(1.0)
    finally:
        if partial.exists(): partial.unlink()
