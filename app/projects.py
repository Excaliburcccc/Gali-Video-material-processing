"""Project-scoped libraries and immutable, reusable game scheme versions."""
import json
import uuid
from .storage import connect, query, settings, DEFAULTS

DEFAULT_PROJECT = 'default'


def initialize(db):
    from .game_schemes import builtin_profiles
    db.executescript('''
CREATE TABLE IF NOT EXISTS game_profiles (
 id TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL,
 config TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(id,version)
);
CREATE TABLE IF NOT EXISTS projects (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, profile_id TEXT NOT NULL,
 profile_version INTEGER NOT NULL, options TEXT NOT NULL DEFAULT '{}',
 created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(profile_id,profile_version) REFERENCES game_profiles(id,version)
);
CREATE TABLE IF NOT EXISTS recording_projects (
 path TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
''')
    for profile in builtin_profiles():
        db.execute('INSERT OR IGNORE INTO game_profiles(id,version,name,config) VALUES (?,?,?,?)',
                   (profile['id'], profile['version'], profile['name'], json.dumps(profile, ensure_ascii=False)))
    legacy = {r['key']: json.loads(r['value']) for r in db.execute('SELECT * FROM settings')}
    options = {k: legacy.get(k, v) for k, v in DEFAULTS.items()}
    db.execute('INSERT OR IGNORE INTO projects(id,name,profile_id,profile_version,options) VALUES (?,?,?,?,?)',
               (DEFAULT_PROJECT, '影之刃零', 'phantom_blade_zero_demo', 1, json.dumps(options, ensure_ascii=False)))
    columns = {r['name'] for r in db.execute('PRAGMA table_info(videos)')}
    if 'project_id' not in columns:
        db.execute("ALTER TABLE videos ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'")
    db.execute('CREATE INDEX IF NOT EXISTS video_project ON videos(project_id)')
    db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES ('active_project_id',?)", (json.dumps(DEFAULT_PROJECT),))


def active_id():
    value = query("SELECT value FROM settings WHERE key='active_project_id'", one=True)
    return json.loads(value['value']) if value else DEFAULT_PROJECT


def get_project(project_id=None):
    project = query('SELECT * FROM projects WHERE id=?', (project_id or active_id(),), True)
    if not project:
        raise ValueError('项目不存在')
    return project


def project_list():
    return query('SELECT p.*, (SELECT count(*) FROM videos v WHERE v.project_id=p.id) video_count FROM projects p ORDER BY p.rowid')


def get_profile(profile_id, version=None):
    if version is None:
        row = query('SELECT config FROM game_profiles WHERE id=? ORDER BY version DESC LIMIT 1', (profile_id,), True)
    else:
        row = query('SELECT config FROM game_profiles WHERE id=? AND version=?', (profile_id, version), True)
    if not row:
        raise ValueError('游戏方案不存在')
    return json.loads(row['config'])


def profile_list():
    return [json.loads(r['config']) for r in query('SELECT config FROM game_profiles g WHERE version=(SELECT MAX(version) FROM game_profiles WHERE id=g.id) ORDER BY g.rowid')]


def project_profile(project_id=None):
    p = get_project(project_id)
    return get_profile(p['profile_id'], p['profile_version'])


def project_settings(project_id=None):
    p = get_project(project_id)
    profile = project_profile(p['id'])
    opts = {**DEFAULTS, **json.loads(p['options'])}
    opts.update(game_profile=profile['adapter'], recognition_scope='collection_only' if profile['adapter']=='phantom_blade_zero_demo' else 'legacy')
    return opts


def update_settings(project_id, values):
    p = get_project(project_id)
    opts = {**json.loads(p['options']), **values}
    with connect() as db:
        db.execute('UPDATE projects SET options=? WHERE id=?', (json.dumps(opts, ensure_ascii=False), p['id']))
    return project_settings(p['id'])


def create_project(name, profile_id, version=None):
    name = str(name).strip()
    if not name or len(name)>100:
        raise ValueError('项目名称需要1至100个字')
    profile = get_profile(profile_id, version)
    project_id = uuid.uuid4().hex
    with connect() as db:
        db.execute('INSERT INTO projects(id,name,profile_id,profile_version,options) VALUES (?,?,?,?,?)',
                   (project_id, name, profile['id'], profile['version'], '{}'))
    return get_project(project_id)


def activate(project_id):
    project = get_project(project_id)
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('active_project_id',?)", (json.dumps(project_id),))
    return project


def save_profile(data, profile_id=None, project_id=None):
    from .game_schemes import validate_profile
    data = dict(data)
    old_version = data.get('version')
    if profile_id:
        old = get_profile(profile_id)
        bound = get_project(project_id)
        expected_version = bound['profile_version'] if bound['profile_id']==profile_id else old['version']
        if old_version is not None and int(old_version) != expected_version:
            raise ValueError('本项目方案已被更新，请重新打开后修改')
        data['id'] = profile_id
        data['version'] = old['version'] + 1
        if data.get('adapter', old['adapter']) != old['adapter']:
            raise ValueError('已有方案不能更换识别方式，请新建游戏方案')
    else:
        data['id'] = 'game_' + uuid.uuid4().hex
        data['version'] = 1
    profile = validate_profile(data)
    with connect() as db:
        db.execute('INSERT INTO game_profiles(id,version,name,config) VALUES (?,?,?,?)',
                   (profile['id'], profile['version'], profile['name'], json.dumps(profile, ensure_ascii=False)))
        if profile_id:
            p = bound
            if p['profile_id'] == profile_id:
                db.execute('UPDATE projects SET profile_version=? WHERE id=?', (profile['version'], p['id']))
    return profile


def known_kinds(project_id=None):
    return {c['id'] for c in project_profile(project_id)['categories']} | {'note'}


def labels(project_id=None):
    return {**{c['id']:c['label'] for c in project_profile(project_id)['categories']}, 'note':'备注'}


def bind_recording(path, project_id=None):
    from pathlib import Path
    if not path: return
    key=str(Path(path).resolve()).casefold()
    project=get_project(project_id)
    with connect() as db:
        db.execute('INSERT OR IGNORE INTO recording_projects(path,project_id) VALUES (?,?)',(key,project['id']))


def recording_project(path):
    from pathlib import Path
    row=query('SELECT project_id FROM recording_projects WHERE path=?',(str(Path(path).resolve()).casefold(),),True)
    return row['project_id'] if row else DEFAULT_PROJECT
