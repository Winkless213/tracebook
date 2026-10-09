import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .indexing import kind_for, split_file


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def digest(value):
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT,branch TEXT,active_snapshot TEXT);
                CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY,project_id TEXT,commit_sha TEXT,branch TEXT,created_at TEXT,file_count INTEGER,chunk_count INTEGER,changes TEXT);
                CREATE TABLE IF NOT EXISTS snapshot_meta(snapshot_id TEXT PRIMARY KEY,body TEXT);
                CREATE TABLE IF NOT EXISTS files(snapshot_id TEXT,path TEXT,content TEXT,sha256 TEXT,kind TEXT,PRIMARY KEY(snapshot_id,path));
                CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY,snapshot_id TEXT,body TEXT);
                CREATE INDEX IF NOT EXISTS chunk_snap ON chunks(snapshot_id);
                CREATE TABLE IF NOT EXISTS questions(id TEXT PRIMARY KEY,project_id TEXT,snapshot_id TEXT,question TEXT,status TEXT,idem TEXT UNIQUE,fingerprint TEXT,
                    sources TEXT NOT NULL DEFAULT '[]',result TEXT,error TEXT,latency_ms INTEGER DEFAULT 0,recovery_count INTEGER DEFAULT 0,created_at TEXT,updated_at TEXT);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,question_id TEXT,kind TEXT,message TEXT,created_at TEXT);
                CREATE INDEX IF NOT EXISTS event_question ON events(question_id,id);
                CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY,question_id TEXT,model TEXT,status TEXT,latency_ms INTEGER,input_tokens INTEGER,output_tokens INTEGER,error TEXT);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, bundle):
        pid = digest([bundle.project, bundle.branch])[:24]
        sid = digest([pid, bundle.commit, sorted((file.path, file.sha256) for file in bundle.files)])
        chunks = [(file, split_file(file.path, file.content)) for file in bundle.files]
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT active_snapshot FROM projects WHERE id=?', (pid,)).fetchone()
            exists = db.execute('SELECT id FROM snapshots WHERE id=?', (sid,)).fetchone()
            if not exists:
                old = {row['path']: row['sha256'] for row in db.execute('SELECT path,sha256 FROM files WHERE snapshot_id=?', (previous[0] if previous else '',))}
                new = {file.path: file.sha256 for file in bundle.files}
                changes = {'added': len(new.keys() - old.keys()), 'removed': len(old.keys() - new.keys()), 'modified': sum(old[path] != new[path] for path in new.keys() & old.keys())}
                db.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?)', (sid, pid, bundle.commit, bundle.branch, now(), len(bundle.files), sum(len(items) for _, items in chunks), encode(changes)))
                for file, items in chunks:
                    db.execute('INSERT INTO files VALUES(?,?,?,?,?)', (sid, file.path, file.content, file.sha256, kind_for(file.path)))
                    db.executemany('INSERT INTO chunks(snapshot_id,body) VALUES(?,?)', [(sid, encode({**item, 'commit': bundle.commit, 'sha256': file.sha256})) for item in items])
            db.execute('INSERT INTO projects VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET active_snapshot=excluded.active_snapshot', (pid, bundle.project, bundle.branch, sid))
            if bundle.scope:
                db.execute('INSERT INTO snapshot_meta VALUES(?,?) ON CONFLICT(snapshot_id) DO UPDATE SET body=excluded.body', (sid, encode(bundle.scope.model_dump())))
        return {'project': next(project for project in self.projects() if project['id'] == pid), 'snapshot': self.snapshot(sid), 'unchanged': bool(previous and previous[0] == sid), 'excluded_count': len(bundle.excluded)}

    def projects(self):
        with self.db() as db:
            return [dict(row) for row in db.execute('''SELECT p.*,s.commit_sha AS "commit",s.file_count,s.chunk_count,s.created_at AS imported_at
                FROM projects p JOIN snapshots s ON p.active_snapshot=s.id ORDER BY s.created_at DESC''')]

    def snapshot(self, sid):
        with self.db() as db:
            row = db.execute('SELECT s.*,p.active_snapshot=s.id AS active FROM snapshots s JOIN projects p ON s.project_id=p.id WHERE s.id=?', (sid,)).fetchone()
        if row is None:
            raise KeyError(sid)
        data = dict(row)
        data['commit'] = data.pop('commit_sha')
        data['changes'] = json.loads(data['changes'])
        data['active'] = bool(data['active'])
        return data

    def resolve(self, pid, sid=None):
        with self.db() as db:
            project = db.execute('SELECT active_snapshot FROM projects WHERE id=?', (pid,)).fetchone()
        if project is None:
            raise KeyError(pid)
        snapshot = self.snapshot(sid or project[0])
        if snapshot['project_id'] != pid:
            raise ValueError('快照不属于所选项目与分支')
        return snapshot

    def snapshots(self, pid):
        with self.db() as db:
            ids = [row[0] for row in db.execute('SELECT id FROM snapshots WHERE project_id=? ORDER BY created_at DESC', (pid,))]
        return [self.snapshot(sid) for sid in ids]

    def corpus(self, sid):
        with self.db() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT body FROM chunks WHERE snapshot_id=? ORDER BY id', (sid,))]

    def source(self, sid, path):
        with self.db() as db:
            row = db.execute('SELECT * FROM files WHERE snapshot_id=? AND path=?', (sid, path)).fetchone()
        if row is None:
            raise KeyError(path)
        return {**dict(row), 'commit': self.snapshot(sid)['commit']}

    def knowledge_map(self, pid, sid=None):
        snapshot = self.resolve(pid, sid)
        with self.db() as db:
            files = [dict(row) for row in db.execute('SELECT path,kind,sha256 FROM files WHERE snapshot_id=? ORDER BY path', (snapshot['id'],))]
            meta = db.execute('SELECT body FROM snapshot_meta WHERE snapshot_id=?', (snapshot['id'],)).fetchone()
        counts = {kind: 0 for kind in ['document', 'code', 'test', 'config']}
        corpus = self.corpus(snapshot['id'])
        for file in files:
            counts[file['kind']] += 1
            chunks = [item for item in corpus if item['path'] == file['path']]
            file['line_count'] = max((item['end_line'] for item in chunks), default=0)
            file['anchors'] = [{'label': item['symbol'], 'start_line': item['start_line'], 'end_line': item['end_line']} for item in chunks if item['symbol']]
        gaps = ['这份导航仅反映已导入文件。未检索到不代表项目没有该功能；未记录的设计原因不能从代码反推。']
        for kind, label in [('document', '说明文档'), ('test', '测试'), ('config', '配置')]:
            if not counts[kind]:
                gaps.append(f'本快照未包含{label}；相关问题可能缺少依据。')
        return {'snapshot': snapshot, 'scope': json.loads(meta[0]) if meta else None, 'counts': counts, 'files': files, 'gaps': gaps}

    def create_question(self, pid, question, key, sid=None):
        snapshot = self.resolve(pid, sid)
        fingerprint = digest([pid, question, sid])
        identifier = str(uuid4())
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT id,fingerprint FROM questions WHERE idem=?', (key,)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise ValueError('提交编号对应其他问题，请重新提交')
                identifier = old['id']
            else:
                if db.execute("SELECT count(*) FROM questions WHERE status IN ('queued','retrieving','generating')").fetchone()[0] >= 5:
                    raise ValueError('最多排队5个问题，请稍后再提交')
                db.execute('INSERT INTO questions(id,project_id,snapshot_id,question,status,idem,fingerprint,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                           (identifier, pid, snapshot['id'], question, 'queued', key, fingerprint, now(), now()))
        if not old:
            self.event(identifier, 'queued', '问题已保存，已固定资料版本')
        return self.question(identifier)

    def question(self, identifier):
        with self.db() as db:
            row = db.execute('SELECT * FROM questions WHERE id=?', (identifier,)).fetchone()
            if row is None:
                raise KeyError(identifier)
            events = [dict(item) for item in db.execute('SELECT id,kind,message,created_at FROM events WHERE question_id=? ORDER BY id', (identifier,))]
            attempts = [dict(item) for item in db.execute('SELECT model,status,latency_ms,input_tokens,output_tokens,error FROM attempts WHERE question_id=? ORDER BY id', (identifier,))]
        data = dict(row)
        data.pop('idem')
        data.pop('fingerprint')
        data['sources'] = json.loads(data['sources'])
        data['result'] = json.loads(data['result']) if data['result'] else None
        snap = self.snapshot(data['snapshot_id'])
        usage = {key: sum(item[key] or 0 for item in attempts) for key in ['input_tokens', 'output_tokens']}
        usage['total_tokens'] = sum(usage.values())
        usage['unreported_calls'] = sum(item['input_tokens'] is None for item in attempts)
        data.update(events=events, attempts=attempts, usage=usage, commit=snap['commit'], stale=not snap['active'], method='bm25-symbol')
        return data

    def questions(self, pid):
        with self.db() as db:
            ids = [row[0] for row in db.execute('SELECT id FROM questions WHERE project_id=? ORDER BY created_at DESC LIMIT 100', (pid,))]
        return [self.question(identifier) for identifier in ids]

    def event(self, identifier, kind, message):
        with self.db() as db:
            db.execute('INSERT INTO events(question_id,kind,message,created_at) VALUES(?,?,?,?)', (identifier, kind, message, now()))

    def claim(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT id FROM questions WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE questions SET status='retrieving',error=NULL,updated_at=? WHERE id=?", (now(), row[0]))
        return row[0] if row else None

    def update(self, identifier, **values):
        allowed = {'status', 'sources', 'result', 'error', 'latency_ms'}
        if not values.keys() <= allowed:
            raise ValueError('unsupported update')
        values = {key: encode(value) if key in {'sources', 'result'} else value for key, value in values.items()}
        values['updated_at'] = now()
        with self.db() as db:
            db.execute('UPDATE questions SET ' + ','.join(f'{key}=?' for key in values) + " WHERE id=? AND status!='cancelled'", [*values.values(), identifier])

    def action(self, identifier, action):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM questions WHERE id=?', (identifier,)).fetchone()
            if row is None:
                raise KeyError(identifier)
            if action == 'retry' and row[0] != 'failed':
                raise ValueError('只有失败的问题可以重试')
            if action == 'retry' and db.execute("SELECT count(*) FROM questions WHERE status IN ('queued','retrieving','generating')").fetchone()[0] >= 5:
                raise ValueError('最多排队5个问题，请稍后再提交')
            if action == 'cancel' and row[0] in {'completed', 'cancelled'}:
                raise ValueError('问题已经结束')
            status = 'queued' if action == 'retry' else 'cancelled'
            db.execute('UPDATE questions SET status=?,error=NULL,updated_at=? WHERE id=?', (status, now(), identifier))
        self.event(identifier, action, '已安排重试，沿用原快照' if action == 'retry' else '已取消；已发出的模型请求可能仍产生费用')
        return self.question(identifier)

    def recover(self):
        with self.db() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM questions WHERE status IN ('retrieving','generating')")]
            db.execute("UPDATE attempts SET status='interrupted',error='进程中断，上游用量未知' WHERE status='running'")
            db.execute("""UPDATE questions SET status=CASE WHEN recovery_count=0 THEN 'queued' ELSE 'failed' END,
                error='上次处理被中断；未返回的模型请求可能重复计费',recovery_count=recovery_count+1,updated_at=?
                WHERE status IN ('retrieving','generating')""", (now(),))
        for identifier in ids:
            self.event(identifier, 'recovered', '服务恢复，保留原问题、快照与检索证据')

    def begin_attempt(self, identifier, model):
        with self.db() as db:
            return db.execute("INSERT INTO attempts(question_id,model,status) VALUES(?,?,'running')", (identifier, model)).lastrowid

    def end_attempt(self, attempt, status, elapsed, usage, error=None):
        with self.db() as db:
            db.execute('UPDATE attempts SET status=?,latency_ms=?,input_tokens=?,output_tokens=?,error=? WHERE id=?',
                       (status, elapsed, usage.get('input_tokens'), usage.get('output_tokens'), error, attempt))
