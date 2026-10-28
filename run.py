import os
from pathlib import Path

ROOT=Path(__file__).resolve().parent
os.chdir(ROOT)
for key,value in {'TEMP':r'E:\CodexFiles\.tmp\codex','TMP':r'E:\CodexFiles\.tmp\codex','TMPDIR':r'E:\CodexFiles\.tmp\codex','XDG_CACHE_HOME':r'E:\CodexFiles\.cache','HF_HOME':r'E:\CodexFiles\.cache\huggingface','PIP_CACHE_DIR':r'E:\CodexFiles\.cache\pip','MPLCONFIGDIR':r'E:\CodexFiles\.cache\matplotlib'}.items():
    os.environ[key]=value
    Path(value).mkdir(parents=True,exist_ok=True)

if __name__=='__main__':
    import uvicorn
    from app.server import app
    # Paused players may leave a large range response backpressured indefinitely.
    # Bound connection draining so the old process releases global hotkeys.
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8877,log_level='warning',access_log=False,timeout_graceful_shutdown=3))
    app.state.request_shutdown=lambda: setattr(server,'should_exit',True)
    server.run()
