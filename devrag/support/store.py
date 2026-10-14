import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from ..store import encode, now


class SupportStore:
    """客服会话索引与已验证案例。图状态本身由 LangGraph 检查点保存，这里只存可查询的投影。"""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS support_sessions(thread_id TEXT PRIMARY KEY,created_at TEXT,updated_at TEXT,project_id TEXT,
                    symptom TEXT,status TEXT,pending TEXT,error TEXT,case_id TEXT);
                CREATE TABLE IF NOT EXISTS support_cases(id TEXT PRIMARY KEY,thread_id TEXT UNIQUE,created_at TEXT,symptom TEXT,
                    product_version TEXT,reproduction TEXT,masked_log TEXT,report TEXT,resolution_note TEXT);
                CREATE TABLE IF NOT EXISTS support_attempts(id INTEGER PRIMARY KEY,thread_id TEXT,model TEXT,status TEXT,
                    latency_ms INTEGER,input_tokens INTEGER,output_tokens INTEGER,error TEXT,created_at TEXT);
                CREATE TABLE IF NOT EXISTS support_conversations(id TEXT PRIMARY KEY,project_id TEXT,title TEXT,
                    created_at TEXT,updated_at TEXT,turns TEXT);
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

    def create_session(self, thread_id, project_id, symptom):
        with self.db() as db:
            db.execute('INSERT INTO support_sessions(thread_id,created_at,updated_at,project_id,symptom,status,pending,error,case_id) '
                       "VALUES(?,?,?,?,?,'starting',NULL,NULL,NULL)", (thread_id, now(), now(), project_id, symptom))

    def mark(self, thread_id, status, pending=None, error=None):
        with self.db() as db:
            touched = db.execute('UPDATE support_sessions SET status=?,pending=?,error=?,updated_at=? WHERE thread_id=?',
                                 (status, encode(pending) if pending else None, error, now(), thread_id)).rowcount
        if not touched:
            raise KeyError(thread_id)

    def attach_case(self, thread_id, case_id):
        with self.db() as db:
            db.execute('UPDATE support_sessions SET case_id=?,updated_at=? WHERE thread_id=?', (case_id, now(), thread_id))

    def session(self, thread_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM support_sessions WHERE thread_id=?', (thread_id,)).fetchone()
        if row is None:
            raise KeyError(thread_id)
        return {**dict(row), 'pending': json.loads(row['pending']) if row['pending'] else None}

    def sessions(self):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT thread_id,project_id,symptom,status,error,case_id,created_at,updated_at '
                                                    'FROM support_sessions ORDER BY created_at DESC LIMIT 100')]

    def save_case(self, state):
        thread_id = state['thread_id']
        with self.db() as db:
            case_id = str(uuid4())
            db.execute('INSERT INTO support_cases(id,thread_id,created_at,symptom,product_version,reproduction,masked_log,report,resolution_note) '
                       'VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(thread_id) DO NOTHING',
                       (case_id, thread_id, now(), state['symptom'], state['product_version'], state['reproduction'],
                        state['masked_log'], encode(state['report']), state['report']['confirmation']['resolution_note']))
            return db.execute('SELECT id FROM support_cases WHERE thread_id=?', (thread_id,)).fetchone()[0]

    def cases(self):
        with self.db() as db:
            rows = [dict(row) for row in db.execute('SELECT c.*,s.project_id FROM support_cases c '
                                                  'JOIN support_sessions s ON s.thread_id=c.thread_id ORDER BY c.created_at DESC LIMIT 200')]
        for row in rows:
            row['report'] = json.loads(row['report'])
        return rows

    def begin_attempt(self, thread_id, model):
        with self.db() as db:
            return db.execute("INSERT INTO support_attempts(thread_id,model,status,created_at) VALUES(?,?,'running',?)",
                              (thread_id, model, now())).lastrowid

    def end_attempt(self, attempt, status, latency_ms, usage, error=None):
        with self.db() as db:
            db.execute('UPDATE support_attempts SET status=?,latency_ms=?,input_tokens=?,output_tokens=?,error=? WHERE id=?',
                       (status, latency_ms, usage.get('input_tokens'), usage.get('output_tokens'), error, attempt))

    def attempts(self, thread_id):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM support_attempts WHERE thread_id=? ORDER BY id', (thread_id,))]

    def conversation(self, conversation_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM support_conversations WHERE id=?', (conversation_id,)).fetchone()
        if row is None:
            raise KeyError(conversation_id)
        return {**dict(row), 'turns': json.loads(row['turns'])}

    def save_conversation(self, conversation):
        conversation['updated_at'] = now()
        with self.db() as db:
            db.execute('INSERT INTO support_conversations(id,project_id,title,created_at,updated_at,turns) VALUES(?,?,?,?,?,?) '
                       'ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,turns=excluded.turns',
                       (conversation['id'], conversation['project_id'], conversation['title'], conversation['created_at'],
                        conversation['updated_at'], encode(conversation['turns'])))

    def conversations(self):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT id,project_id,title,created_at,updated_at '
                                                    'FROM support_conversations ORDER BY updated_at DESC LIMIT 100')]
