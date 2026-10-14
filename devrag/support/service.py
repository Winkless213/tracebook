import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

from filelock import FileLock, Timeout
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from langsmith import tracing_context

from ..store import now
from .chat import SupportChat
from .model import SupportModel
from .redact import mask_data, mask_text
from .report import build_report, collect_evidence, render_report
from .schemas import SessionConfirm, SessionStart, SessionSupplement
from .store import SupportStore

LOG_GUIDANCE = ('请提供软件版本、运行环境和可重复操作；日志请粘贴故障前后片段（最多 64000 UTF-8 字节），'
                '保留时间、错误码和调用栈，先去除密码、令牌及私人资料。没有日志可先提供版本与复现步骤；'
                '本接口不读取本机路径、不解压文件。')


class SupportState(TypedDict):
    thread_id: str
    project_id: str | None
    symptom: str
    product_version: str
    reproduction: str
    masked_log: str
    redaction_notes: list[str]
    evidence: list[dict]
    warnings: list[str]
    report: dict | None
    status: str
    error: str | None
    case_id: str | None


def missing_fields(state):
    return [key for key in ('product_version', 'reproduction') if not state.get(key)]


def merge_supplement(state, supplied):
    fields = {key: value for key, value in supplied.items() if key in ('product_version', 'reproduction', 'masked_log') and value}
    return {**fields, 'redaction_notes': sorted(set(state['redaction_notes'] + supplied.get('redaction_notes', []))),
            'report': None, 'error': None, 'case_id': None, 'status': 'processing'}


class SupportService:
    def __init__(self, root, model=None, retriever=None, search=None):
        self.root = Path(root) / 'support'
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'locks').mkdir(exist_ok=True)
        self.store = SupportStore(self.root / 'support.sqlite')
        self.model, self.retriever = model or SupportModel(), retriever
        self.chat = SupportChat(self.root, self.store, self.model, self.retriever, search)
        self.connection = sqlite3.connect(self.root / 'checkpoints.sqlite', check_same_thread=False)
        saver = SqliteSaver(self.connection)
        saver.setup()
        graph = StateGraph(SupportState)
        for name, node in (('collect', self._collect), ('retrieve', self._retrieve), ('analyze', self._analyze),
                           ('feedback', self._feedback), ('save_case', self._save_case)):
            graph.add_node(name, node)
        graph.add_edge(START, 'collect')
        graph.add_conditional_edges('collect', lambda state: 'collect' if missing_fields(state) else 'retrieve')
        graph.add_edge('retrieve', 'analyze')
        graph.add_conditional_edges('analyze', lambda state: 'feedback' if state['report'] else END)
        graph.add_conditional_edges('feedback', lambda state: 'collect' if state['status'] == 'processing'
                                    else 'save_case' if state['status'] == 'resolved' else END)
        graph.add_edge('save_case', END)
        self.graph = graph.compile(checkpointer=saver)

    def close(self):
        self.connection.close()

    @staticmethod
    def _config(thread_id):
        return {'configurable': {'thread_id': thread_id}}

    @contextmanager
    def _lock(self, thread_id):
        self.store.session(thread_id)
        lock = FileLock(self.root / 'locks' / (thread_id + '.lock'))
        try:
            lock.acquire(timeout=0)
        except Timeout:
            raise ValueError('此会话正在处理，请稍后重试') from None
        try:
            yield
        finally:
            lock.release()

    def _invoke(self, thread_id, payload):
        # 本地会话不进入其他观测平台；只有模型客户端负责明确的外部请求。
        with tracing_context(enabled=False):
            self.graph.invoke(payload, self._config(thread_id))
        view = self.view(thread_id)
        self.store.mark(thread_id, view['status'], view['pending'], view['error'])
        if view['case_id']:
            self.store.attach_case(thread_id, view['case_id'])
        return view

    def start(self, symptom, project_id=None):
        request = SessionStart(symptom=symptom, project_id=project_id)
        if request.project_id and self.retriever:
            self.retriever.store.resolve(request.project_id)
        text, notes = mask_text(request.symptom)
        thread_id = str(uuid4())
        self.store.create_session(thread_id, request.project_id, text)
        state = {'thread_id': thread_id, 'project_id': request.project_id, 'symptom': text, 'product_version': '',
                 'reproduction': '', 'masked_log': '', 'redaction_notes': notes, 'evidence': [], 'warnings': [],
                 'report': None, 'status': 'processing', 'error': None, 'case_id': None}
        with self._lock(thread_id):
            return self._invoke(thread_id, state)

    def supplement(self, thread_id, payload):
        request = SessionSupplement.model_validate(payload)
        safe, notes = mask_data(request.model_dump(exclude_none=True))
        if 'log_text' in safe:
            safe['masked_log'] = safe.pop('log_text')
        safe['redaction_notes'] = notes
        with self._lock(thread_id):
            view = self.view(thread_id)
            if view['status'] in ('awaiting_info', 'awaiting_confirmation'):
                return self._invoke(thread_id, Command(resume={'action': 'supplement', **safe}))
            if view['status'] in ('unresolved', 'failed'):
                return self._invoke(thread_id, merge_supplement(view, safe))
            raise ValueError('当前会话不能补充资料')

    def confirm(self, thread_id, resolved, resolution_note=''):
        request = SessionConfirm(resolved=resolved, resolution_note=resolution_note)
        safe, _ = mask_data(request.model_dump())
        with self._lock(thread_id):
            view = self.view(thread_id)
            if view['status'] == 'resolved' and request.resolved:
                return view
            if view['status'] != 'awaiting_confirmation':
                raise ValueError('只有已生成报告、等待确认的会话可以确认')
            return self._invoke(thread_id, Command(resume={'action': 'confirm', **safe}))

    def retry(self, thread_id):
        with self._lock(thread_id):
            view = self.view(thread_id)
            if view['status'] == 'failed':
                return self._invoke(thread_id, {'status': 'processing', 'error': None, 'report': None})
            if view['status'] == 'processing':
                return self._invoke(thread_id, None)
            raise ValueError('当前会话无需重试，请补充信息或确认报告')

    def view(self, thread_id):
        row = self.store.session(thread_id)
        snapshot = self.graph.get_state(self._config(thread_id))
        state = dict(snapshot.values)
        pending = next((item.value for task in snapshot.tasks for item in task.interrupts), None)
        status = ('awaiting_info' if pending['kind'] == 'need_info' else 'awaiting_confirmation') if pending else (
            'processing' if snapshot.next else state.get('status', 'processing'))
        return {**row, **state, 'status': status, 'pending': pending, 'report': state.get('report'),
                'attempts': self.store.attempts(thread_id)}

    def sessions(self):
        return [{key: value for key, value in self.view(row['thread_id']).items()
                 if key in ('thread_id', 'project_id', 'symptom', 'status', 'error', 'case_id', 'created_at', 'updated_at')}
                for row in self.store.sessions()]

    def cases(self):
        return self.store.cases()

    def export(self, thread_id):
        return render_report(self.view(thread_id))

    def _collect(self, state):
        if missing := missing_fields(state):
            supplied = interrupt({'kind': 'need_info', 'missing': missing, 'log_guidance': LOG_GUIDANCE})
            return merge_supplement(state, supplied)
        return {'status': 'processing', 'error': None, 'report': None}

    def _retrieve(self, state):
        evidence, notes, warnings = collect_evidence(state, self.retriever, self.cases())
        return {'evidence': evidence, 'warnings': warnings, 'redaction_notes': sorted(set(state['redaction_notes'] + notes))}

    def _analyze(self, state):
        def record(model, attempt, status, latency, usage, error):
            if attempt is None:
                return self.store.begin_attempt(state['thread_id'], mask_text(model)[0])
            self.store.end_attempt(attempt, status, latency, usage, mask_text(error)[0] if error else None)

        payload = {key: state[key] for key in ('symptom', 'product_version', 'reproduction', 'evidence')}
        try:
            raw = self.model.analyze(payload, record)
            report = build_report(state, raw)
            return {'report': report, 'status': 'awaiting_confirmation', 'error': None}
        except ValueError:
            error = '客服模型结构或引用校验未通过，未采纳该结果'
        except Exception:
            error = '客服模型分析未完成，请检查模型配置与调用记录后重试；脱敏材料已保存'
        return {'report': None, 'status': 'failed', 'error': error}

    def _feedback(self, state):
        supplied = interrupt({'kind': 'confirmation', 'message': '请验证建议，再明确确认是否解决；仍有问题可继续补充资料。'})
        if supplied['action'] == 'supplement':
            return merge_supplement(state, supplied)
        report = {**state['report'], 'confirmation': {'resolved': supplied['resolved'],
                  'resolution_note': supplied['resolution_note'], 'confirmed_at': now()}}
        return {'report': report, 'status': 'resolved' if supplied['resolved'] else 'unresolved'}

    def _save_case(self, state):
        return {'case_id': self.store.save_case(state), 'status': 'resolved'}
