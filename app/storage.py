import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(os.environ.get('FOOTAGE_ROOT', Path(__file__).resolve().parents[1]))
DATA = ROOT / 'data'
for folder in (DATA, DATA/'thumbnails', DATA/'proxies', DATA/'logs', ROOT/'exports', ROOT/'recordings'):
    folder.mkdir(parents=True, exist_ok=True)
DB = DATA / 'library.sqlite3'
DEFAULTS = {'sample_interval': 1.0, 'max_width': 1600, 'regions': [], 'keywords': {}, 'game_profile':'generic', 'text_mode':'compact'}

def connect():
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db

def init_db():
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
CREATE TABLE IF NOT EXISTS videos (
 id TEXT PRIMARY KEY, path TEXT NOT NULL, fingerprint TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
 duration REAL NOT NULL, width INTEGER, height INTEGER, codec TEXT, size INTEGER, mtime REAL,
 status TEXT DEFAULT 'ready', progress REAL DEFAULT 0, checkpoint REAL DEFAULT 0,
 message TEXT DEFAULT '', proxy_path TEXT DEFAULT '', proxy_status TEXT DEFAULT 'none',
 options TEXT DEFAULT '{}', created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, name TEXT NOT NULL, time REAL NOT NULL, end_time REAL,
 confidence REAL DEFAULT 1, text TEXT DEFAULT '', screenshot TEXT DEFAULT '',
 source TEXT DEFAULT 'manual', verified INTEGER DEFAULT 0, edited INTEGER DEFAULT 0,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS event_video_time ON events(video_id,time);
CREATE INDEX IF NOT EXISTS event_kind ON events(kind,verified);
CREATE INDEX IF NOT EXISTS event_video_kind_time ON events(video_id,kind,time);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, video_id TEXT, kind TEXT, status TEXT, progress REAL DEFAULT 0,
 message TEXT DEFAULT '', output_path TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS seen_markers (id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS rejected_events (
 id TEXT PRIMARY KEY, video_id TEXT, kind TEXT, name TEXT, text TEXT, time REAL, end_time REAL, source TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS event_revisions (video_id TEXT PRIMARY KEY, revision INTEGER DEFAULT 0);
CREATE TRIGGER IF NOT EXISTS event_revision_insert AFTER INSERT ON events BEGIN
 INSERT INTO event_revisions VALUES (NEW.video_id,1) ON CONFLICT(video_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER IF NOT EXISTS event_revision_update AFTER UPDATE ON events BEGIN
 INSERT INTO event_revisions VALUES (NEW.video_id,1) ON CONFLICT(video_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER IF NOT EXISTS event_revision_delete AFTER DELETE ON events BEGIN
 INSERT INTO event_revisions VALUES (OLD.video_id,1) ON CONFLICT(video_id) DO UPDATE SET revision=revision+1;
END;
''')
        db.execute("UPDATE videos SET status='interrupted',message='上次识别已中断，可继续识别' WHERE status IN ('queued','analyzing')")
        db.execute("UPDATE videos SET proxy_status='error' WHERE proxy_status IN ('queued','running')")
        db.execute("UPDATE jobs SET status='interrupted',message='上次处理已中断，请重新执行' WHERE status IN ('queued','running')")
        columns={row['name'] for row in db.execute('PRAGMA table_info(events)')}
        if 'evidence' not in columns:
            db.execute("ALTER TABLE events ADD COLUMN evidence TEXT DEFAULT ''")
        if 'detection_score' not in columns:
            db.execute('ALTER TABLE events ADD COLUMN detection_score REAL')
        # Old indexes used only the source pathname; namespace machine IDs by
        # imported-file fingerprint so replacing a recording never steals rows.
        for row in db.execute("SELECT id,video_id FROM events WHERE source='ocr'").fetchall():
            if not row['id'].startswith(row['video_id']+'_'):
                db.execute('UPDATE events SET id=? WHERE id=?',(row['video_id']+'_'+row['id'],row['id']))

def query(sql, args=(), one=False):
    with connect() as db:
        rows = db.execute(sql, args).fetchall()
    values = [dict(x) for x in rows]
    return (values[0] if values else None) if one else values

def execute(sql, args=()):
    with connect() as db:
        return db.execute(sql, args).rowcount

def settings():
    return {**DEFAULTS, **{r['key']:json.loads(r['value']) for r in query('SELECT * FROM settings')}}

def save_settings(values):
    with connect() as db:
        for key, value in values.items():
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key,json.dumps(value,ensure_ascii=False)))

def update_video(video_id, **values):
    allowed = {'status','progress','checkpoint','message','proxy_path','proxy_status','options'}
    assert set(values) <= allowed
    execute('UPDATE videos SET '+','.join(k+'=?' for k in values)+' WHERE id=?', (*values.values(),video_id))

def update_job(job_id, **values):
    allowed = {'status','progress','message','output_path'}
    assert set(values) <= allowed
    execute('UPDATE jobs SET '+','.join(k+'=?' for k in values)+' WHERE id=?', (*values.values(),job_id))
