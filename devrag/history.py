import json
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .models import SECRET, Strict
from .store import digest, encode, now


class Provenance(Strict):
    file: str = Field(min_length=1, max_length=600)
    line: int = Field(ge=1)
    record_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    part: int = Field(default=0, ge=0)


class HistoryRecord(Strict):
    id: str = Field(pattern=r'^[a-f0-9]{64}$')
    role: Literal['user', 'assistant', 'tool-output']
    text: str = Field(min_length=1, max_length=12000)
    time: str = Field(max_length=60)
    provenance: Provenance

    @field_validator('time')
    @classmethod
    def timestamp(cls, value):
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date.tzinfo is None:
            raise ValueError('会话时间必须带时区')
        return value

    @model_validator(mode='after')
    def no_secrets(self):
        if '\x00' in self.text or SECRET.search(encode(self.model_dump())):
            raise ValueError('记录疑似含密钥，请在本机脱敏后同步')
        return self


class SyncBatch(Strict):
    project_id: str = Field(min_length=1, max_length=64)
    harness: Literal['codex', 'claude']
    native_id: str = Field(min_length=1, max_length=150)
    title: str = Field(min_length=1, max_length=200)
    records: list[HistoryRecord] = Field(min_length=1, max_length=100)
    auto_distill: bool = False

    @model_validator(mode='after')
    def bounded(self):
        if len({item.id for item in self.records}) != len(self.records):
            raise ValueError('同步批次中有重复记录')
        if SECRET.search(self.title + self.native_id):
            raise ValueError('会话标题疑似含密钥')
        if sum(len(item.text.encode('utf-8')) for item in self.records) > 1_000_000:
            raise ValueError('同步批次超过1MB，请分批')
        return self


class Classification(Strict):
    id: str
    category: Literal['user_decision', 'user_proposal', 'assistant_hypothesis', 'tool_observation', 'tool_claim']
    claim_status: Literal['supported_as_record', 'unverified', 'contradicted']
    finding: str = Field(min_length=1, max_length=600)
    related_ids: list[str] = Field(max_length=20)


class Lesson(Strict):
    candidate: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    scope: str = Field(min_length=1, max_length=1200)


class Distillation(Strict):
    records: list[Classification] = Field(min_length=1, max_length=20)
    lessons: list[Lesson] = Field(max_length=5)
    gaps: list[str] = Field(max_length=12)


class DistillRequest(Strict):
    record_ids: list[str] | None = Field(default=None, min_length=1, max_length=20)


class HistoryProject(Strict):
    name: str = Field(min_length=1, max_length=100)

    @field_validator('name')
    @classmethod
    def safe_name(cls, value):
        if not value.strip() or SECRET.search(value):
            raise ValueError('项目名称不能为空或包含密钥')
        return value.strip()


def validate_distillation(value, records):
    result = Distillation.model_validate(value)
    mapping = {item['id']: item for item in records}
    if len(result.records) != len(mapping) or {item.id for item in result.records} != set(mapping):
        raise ValueError('提炼引用必须恰好覆盖本次输入记录')
    categories = {'user': {'user_decision', 'user_proposal'}, 'assistant': {'assistant_hypothesis'}, 'tool-output': {'tool_observation', 'tool_claim'}}
    for item in result.records:
        if not set(item.related_ids) <= mapping.keys():
            raise ValueError('提炼引用指向未知记录')
        if item.category not in categories[mapping[item.id]['role']]:
            raise ValueError('提炼混淆了用户、AI与工具来源')
        if item.category == 'assistant_hypothesis' and item.claim_status == 'supported_as_record':
            raise ValueError('AI陈述不能直接标为已核实事实')
    for lesson in result.lessons:
        if not set(lesson.evidence_ids) <= mapping.keys():
            raise ValueError('经验引用指向未知记录')
    if SECRET.search(encode(result.model_dump())):
        raise ValueError('提炼结果疑似含密钥')
    return result.model_dump()


class HistoryStore:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS history_sessions(id TEXT PRIMARY KEY,project_id TEXT,harness TEXT,native_id TEXT,title TEXT,updated_at TEXT);
                CREATE TABLE IF NOT EXISTS history_records(id TEXT PRIMARY KEY,session_id TEXT,client_id TEXT,body TEXT,created_at TEXT);
                CREATE INDEX IF NOT EXISTS history_record_session ON history_records(session_id,created_at,id);
                CREATE TABLE IF NOT EXISTS history_jobs(id TEXT PRIMARY KEY,session_id TEXT,status TEXT,record_ids TEXT,result TEXT,error TEXT,
                    recovery_count INTEGER DEFAULT 0,created_at TEXT,updated_at TEXT);
                CREATE TABLE IF NOT EXISTS history_attempts(id INTEGER PRIMARY KEY,job_id TEXT,model TEXT,status TEXT,latency_ms INTEGER,
                    input_tokens INTEGER,output_tokens INTEGER,error TEXT);
            ''')

    def create_project(self, name):
        name = HistoryProject(name=name).name
        pid = digest([name, 'conversation'])[:24]
        sid = digest(['conversation-project', pid])
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?,?,?,?)',
                       (sid, pid, '', '会话归档', now(), 0, 0, encode({'added': 0, 'modified': 0, 'removed': 0})))
            db.execute('INSERT OR IGNORE INTO projects VALUES(?,?,?,?)', (pid, name, '会话归档', sid))
        return next(project for project in self.store.projects() if project['id'] == pid)

    def sync(self, batch):
        self.store.resolve(batch.project_id)
        sid = digest([batch.project_id, batch.harness, batch.native_id])
        added, identifiers = 0, []
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO history_sessions VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,updated_at=excluded.updated_at',
                       (sid, batch.project_id, batch.harness, batch.native_id, batch.title, now()))
            for record in batch.records:
                rid = digest([sid, record.id])
                body = record.model_dump()
                body['id'], body['session_id'] = rid, sid
                old = db.execute('SELECT body FROM history_records WHERE id=?', (rid,)).fetchone()
                if old:
                    # 文件持续追加会改变全文件哈希，记录本身必须保持不可变。
                    prior, candidate = json.loads(old[0]), json.loads(encode(body))
                    prior['provenance'].pop('source_sha256', None)
                    candidate['provenance'].pop('source_sha256', None)
                    if prior != candidate:
                        raise ValueError('同步内容与原记录冲突，未覆盖原记录')
                    continue
                db.execute('INSERT INTO history_records VALUES(?,?,?,?,?)', (rid, sid, record.id, encode(body), now()))
                added += 1
                identifiers.append(rid)
        job = self.enqueue(sid, quiet=True) if batch.auto_distill and identifiers else None
        return {'session': self.session_info(sid), 'added': added, 'job_id': job['id'] if job else None}

    def sessions(self, pid=None):
        if pid:
            self.store.resolve(pid)
        with self.store.db() as db:
            rows = db.execute('SELECT id FROM history_sessions' + (' WHERE project_id=?' if pid else '') + ' ORDER BY updated_at DESC', (pid,) if pid else ()).fetchall()
        return [self.session_info(row[0]) for row in rows]

    def session_info(self, sid):
        with self.store.db() as db:
            row = db.execute('SELECT * FROM history_sessions WHERE id=?', (sid,)).fetchone()
            if row is None:
                raise KeyError(sid)
            ids = {item[0] for item in db.execute('SELECT id FROM history_records WHERE session_id=?', (sid,))}
            complete = db.execute("SELECT record_ids FROM history_jobs WHERE session_id=? AND status='completed'", (sid,)).fetchall()
        processed = {identifier for item in complete for identifier in json.loads(item[0])}
        return {**dict(row), 'record_count': len(ids), 'pending_count': len(ids - processed)}

    def session(self, sid, offset=0, limit=50):
        info = self.session_info(sid)
        with self.store.db() as db:
            records = [json.loads(row[0]) for row in db.execute("SELECT body FROM history_records WHERE session_id=? ORDER BY json_extract(body,'$.provenance.line'),json_extract(body,'$.provenance.part'),id LIMIT ? OFFSET ?", (sid, limit, offset))]
            jobs = [self.job(row[0]) for row in db.execute('SELECT id FROM history_jobs WHERE session_id=? ORDER BY created_at DESC LIMIT 50', (sid,))]
        return {'session': info, 'records': records, 'jobs': jobs, 'total': info['record_count'], 'offset': offset}

    def record(self, rid):
        with self.store.db() as db:
            row = db.execute('SELECT body FROM history_records WHERE id=?', (rid,)).fetchone()
        if row is None:
            raise KeyError(rid)
        return json.loads(row[0])

    def enqueue(self, sid, record_ids=None, quiet=False):
        self.session_info(sid)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT id,length(json_extract(body,'$.text')) AS size FROM history_records WHERE session_id=? ORDER BY json_extract(body,'$.provenance.line') DESC,json_extract(body,'$.provenance.part') DESC,id DESC", (sid,)).fetchall()
            existing = db.execute("SELECT record_ids FROM history_jobs WHERE session_id=? AND status IN ('completed','queued','generating')", (sid,)).fetchall()
            covered = {rid for row in existing for rid in json.loads(row[0])}
            if record_ids is None:
                record_ids, size = [], 0
                for row in rows:
                    if row[0] in covered:
                        continue
                    if len(record_ids) == 20 or size + row['size'] > 60000:
                        break
                    record_ids.append(row[0])
                    size += row['size']
                record_ids.reverse()
            if not record_ids or len(record_ids) > 20 or len(set(record_ids)) != len(record_ids):
                raise ValueError('没有待提炼记录，或选择超过20段')
            if not set(record_ids) <= {row[0] for row in rows}:
                raise ValueError('记录不属于该会话')
            if sum(row['size'] for row in rows if row[0] in record_ids) > 60000:
                raise ValueError('单次提炼超过60000字符，请减少选择')
            record_ids = [row[0] for row in reversed(rows) if row[0] in record_ids]
            same = db.execute("SELECT id FROM history_jobs WHERE session_id=? AND record_ids=? AND status!='cancelled' ORDER BY created_at DESC LIMIT 1", (sid, encode(record_ids))).fetchone()
            if same:
                return self.job(same[0])
            jid = digest([sid, sorted(record_ids)])
            old = db.execute('SELECT id,status FROM history_jobs WHERE id=?', (jid,)).fetchone()
            if old and old['status'] != 'cancelled':
                return self.job(jid)
            if old:
                jid = digest([jid, now()])
            if db.execute("SELECT count(*) FROM history_jobs WHERE status IN ('queued','generating')").fetchone()[0] >= 5:
                if quiet:
                    return None
                raise ValueError('最多排队5个提炼任务，请稍后重试')
            db.execute("INSERT INTO history_jobs(id,session_id,status,record_ids,created_at,updated_at) VALUES(?,?,'queued',?,?,?)",
                       (jid, sid, encode(record_ids), now(), now()))
        return self.job(jid)

    def job(self, jid):
        with self.store.db() as db:
            row = db.execute('SELECT * FROM history_jobs WHERE id=?', (jid,)).fetchone()
            if row is None:
                raise KeyError(jid)
            attempts = [dict(item) for item in db.execute('SELECT * FROM history_attempts WHERE job_id=? ORDER BY id', (jid,))]
        result = dict(row)
        result['record_ids'] = json.loads(result['record_ids'])
        result['result'] = json.loads(result['result']) if result['result'] else None
        usage = {key: sum(item[key] or 0 for item in attempts) for key in ['input_tokens', 'output_tokens']}
        usage['total_tokens'] = sum(usage.values())
        usage['unreported_calls'] = sum(item['input_tokens'] is None for item in attempts)
        return {**result, 'attempts': attempts, 'usage': usage}

    def claim(self):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT id FROM history_jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE history_jobs SET status='generating',error=NULL,updated_at=? WHERE id=?", (now(), row[0]))
        return row[0] if row else None

    def finish(self, jid, result):
        job = self.job(jid)
        value = validate_distillation(result, [self.record(rid) for rid in job['record_ids']])
        with self.store.db() as db:
            db.execute("UPDATE history_jobs SET status='completed',result=?,error=NULL,updated_at=? WHERE id=? AND status='generating'", (encode(value), now(), jid))

    def fail(self, jid, error):
        with self.store.db() as db:
            db.execute("UPDATE history_jobs SET status='failed',error=?,updated_at=? WHERE id=? AND status='generating'", (error, now(), jid))

    def action(self, jid, action):
        if action not in {'retry', 'cancel'}:
            raise ValueError('未知任务操作')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM history_jobs WHERE id=?', (jid,)).fetchone()
            if row is None:
                raise KeyError(jid)
            if action == 'retry' and row[0] != 'failed':
                raise ValueError('只有失败的提炼可以续跑')
            if action == 'cancel' and row[0] in {'completed', 'cancelled'}:
                raise ValueError('任务已经结束')
            if action == 'retry' and db.execute("SELECT count(*) FROM history_jobs WHERE status IN ('queued','generating')").fetchone()[0] >= 5:
                raise ValueError('最多排队5个提炼任务')
            db.execute('UPDATE history_jobs SET status=?,error=NULL,updated_at=? WHERE id=?', ('queued' if action == 'retry' else 'cancelled', now(), jid))
        return self.job(jid)

    def recover(self):
        with self.store.db() as db:
            db.execute("UPDATE history_attempts SET status='interrupted',error='进程中断，上游用量未知' WHERE status='running'")
            db.execute("""UPDATE history_jobs SET status=CASE WHEN recovery_count=0 THEN 'queued' ELSE 'failed' END,
                error='上次提炼中断，已保存原始输入；可能产生未知费用',recovery_count=recovery_count+1,updated_at=? WHERE status='generating'""", (now(),))

    def begin_attempt(self, jid, model):
        with self.store.db() as db:
            return db.execute("INSERT INTO history_attempts(job_id,model,status) VALUES(?,?,'running')", (jid, model)).lastrowid

    def end_attempt(self, attempt, status, elapsed, usage, error=None):
        with self.store.db() as db:
            db.execute('UPDATE history_attempts SET status=?,latency_ms=?,input_tokens=?,output_tokens=?,error=? WHERE id=?',
                       (status, elapsed, usage.get('input_tokens'), usage.get('output_tokens'), error, attempt))

    def corpus(self, pid):
        with self.store.db() as db:
            records = db.execute('SELECT r.body,s.harness,s.title FROM history_records r JOIN history_sessions s ON r.session_id=s.id WHERE s.project_id=? ORDER BY r.created_at,r.id', (pid,)).fetchall()
            jobs = db.execute("SELECT j.id,j.result FROM history_jobs j JOIN history_sessions s ON j.session_id=s.id WHERE s.project_id=? AND j.status='completed'", (pid,)).fetchall()
        sources, mapping = [], {}
        for row in records:
            item = json.loads(row['body'])
            source = {'path': f"会话/{row['harness']}/{row['title']}/{item['id'][:10]}", 'start_line': item['provenance']['line'],
                      'end_line': item['provenance']['line'], 'content': item['text'], 'kind': 'document', 'symbol': item['role'],
                      'commit': '', 'sha256': item['provenance']['record_sha256'], 'origin': 'conversation', 'record_id': item['id'],
                      'provenance': item['provenance'], 'time': item['time'], 'role': item['role']}
            sources.append(source)
            mapping[item['id']] = source
        for job in jobs:
            for index, lesson in enumerate(json.loads(job['result'])['lessons']):
                referenced = [mapping[rid] for rid in lesson['evidence_ids'] if rid in mapping]
                if not referenced:
                    continue
                text = '候选经验（AI提炼，需核实）：' + lesson['candidate'] + '\n适用范围：' + lesson['scope']
                excerpt_limit = min(1800, 8000 // len(referenced))
                text += '\n原始依据摘录（完整记录可回查）：\n' + '\n'.join(f"{item['role']} · {item['time']}：{item['content'][:excerpt_limit]}" for item in referenced)
                sources.append({**referenced[0], 'content': text, 'origin': 'experience', 'symbol': '候选经验',
                                'path': f"经验候选/{job['id'][:10]}/{index + 1}", 'record_ids': lesson['evidence_ids']})
        return sources

    def revision(self, pid):
        with self.store.db() as db:
            records = db.execute('SELECT count(*),max(r.rowid) FROM history_records r JOIN history_sessions s ON r.session_id=s.id WHERE s.project_id=?', (pid,)).fetchone()
            jobs = db.execute("SELECT count(*),max(j.updated_at) FROM history_jobs j JOIN history_sessions s ON j.session_id=s.id WHERE s.project_id=? AND j.status='completed'", (pid,)).fetchone()
        return (*records, *jobs)
