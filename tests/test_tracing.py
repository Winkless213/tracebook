import logging
import sys
from contextlib import contextmanager
from uuid import uuid4

import pytest
from test_answers import FakeModel
from test_knowledge import bundle
from test_support import SYMPTOM, FakeSupportModel, ready
from test_support_chat import SearchFixture, ToolReplyModel, message

from devrag import tracing
from devrag.retrieval import Retriever
from devrag.store import Store
from devrag.support import SupportService
from devrag.support.chat import SupportChat
from devrag.worker import Worker


class FakeObservation:
    def __init__(self, name, as_type, **fields):
        self.name, self.as_type, self.fields = name, as_type, fields
        self.children, self.scores, self.ended = [], [], False

    def start_observation(self, *, name, as_type='span', **fields):
        child = FakeObservation(name, as_type, **fields)
        self.children.append(child)
        return child

    def update(self, **fields):
        self.fields.update(fields)

    def end(self):
        self.ended = True

    def score_trace(self, **score):
        self.scores.append(score)


class FakeLangfuse:
    """Stands in for the langfuse module and its client: records what would be sent."""

    def __init__(self):
        self.roots, self.attributes, self.scores = [], [], []

    def get_client(self):
        return self

    @contextmanager
    def propagate_attributes(self, **attributes):
        self.attributes.append(attributes)
        yield

    @contextmanager
    def start_as_current_observation(self, *, name, as_type='span', **fields):
        root = FakeObservation(name, as_type, **fields)
        self.roots.append(root)
        yield root
        root.ended = True

    def create_score(self, **score):
        self.scores.append(score)


@pytest.fixture
def langfuse(monkeypatch):
    fake = FakeLangfuse()
    monkeypatch.setattr(tracing, 'client', lambda: fake)
    return fake


def ask(tmp_path, model):
    store = Store(tmp_path / 'kb.sqlite')
    pid = store.ingest(bundle({'README.md': '缓存已经停用，每次查询数据库。'}))['project']['id']
    question = store.create_question(pid, '缓存现在采用什么策略？', 'trace-request')
    Worker(store, Retriever(store), model).process_next()
    return store.question(question['id'])


def test_tracing_stays_off_without_keys(monkeypatch):
    monkeypatch.delenv('LANGFUSE_PUBLIC_KEY', raising=False)
    monkeypatch.delenv('LANGFUSE_SECRET_KEY', raising=False)
    assert tracing.client() is None
    with tracing.run('question') as run:
        assert run.record(lambda *args: 'attempt-1')('model', None, None, 0, {}, None) == 'attempt-1'


def test_keys_without_the_sdk_log_a_warning_and_stay_off(monkeypatch, caplog):
    monkeypatch.setenv('LANGFUSE_PUBLIC_KEY', 'pk-lf-test')
    monkeypatch.setenv('LANGFUSE_SECRET_KEY', 'sk-lf-test')
    monkeypatch.setitem(sys.modules, 'langfuse', None)
    with caplog.at_level(logging.WARNING, logger='devrag.tracing'):
        assert tracing.client() is None
    assert 'langfuse package is not installed' in caplog.text


def test_question_trace_has_retrieval_model_attempt_and_scores(tmp_path, langfuse):
    question = ask(tmp_path, FakeModel())
    assert question['status'] == 'completed'
    assert langfuse.attributes == [{'trace_name': 'question', 'tags': ['qa'], 'session_id': None}]
    root = langfuse.roots[0]
    assert root.fields['input']['question'] == '缓存现在采用什么策略？'
    retrieval, generation = root.children
    assert (retrieval.name, retrieval.as_type, retrieval.ended) == ('retrieval', 'retriever', True)
    assert retrieval.fields['metadata'] == {'method': question['method'], 'sources': len(question['sources'])}
    assert (generation.name, generation.as_type, generation.fields['model']) == ('test-primary', 'generation', 'test-primary')
    assert generation.fields['usage_details'] == {'input': 10, 'output': 5}
    assert generation.ended
    assert root.fields['output'] == question['result']
    assert root.scores == [{'name': 'outcome', 'value': 'answered', 'data_type': 'CATEGORICAL'},
                           {'name': 'first_pass', 'value': 1, 'data_type': 'BOOLEAN'}]


def test_failed_question_trace_is_marked_as_an_error(tmp_path, langfuse):
    question = ask(tmp_path, FakeModel(fail=True))
    assert question['status'] == 'failed'
    root = langfuse.roots[0]
    generation = root.children[-1]
    assert (generation.fields['level'], generation.fields['status_message']) == ('ERROR', '测试失败')
    assert (root.fields['level'], root.fields['status_message']) == ('ERROR', question['error'])
    assert root.scores == [{'name': 'outcome', 'value': 'failed', 'data_type': 'CATEGORICAL'},
                           {'name': 'first_pass', 'value': 0, 'data_type': 'BOOLEAN'}]


def test_review_attempt_keeps_the_model_name_and_a_rejection_is_a_warning(langfuse):
    with tracing.run('question') as run:
        record = run.record(lambda *args: 7)
        record('verifier / 依据核验', record('verifier / 依据核验', None, None, 0, {}, None), 'rejected', 5, {}, '无依据')
    review = langfuse.roots[0].children[0]
    assert (review.name, review.fields['model']) == ('verifier / 依据核验', 'verifier')
    assert (review.fields['level'], review.fields['status_message']) == ('WARNING', '无依据')


def test_an_attempt_that_never_finished_is_ended_as_an_error(langfuse):
    with tracing.run('question') as run:
        run.record(lambda *args: 7)('test-primary', None, None, 0, {}, None)
    generation = langfuse.roots[0].children[0]
    assert (generation.ended, generation.fields.get('level')) == (True, 'ERROR')


def test_tracing_failures_never_break_the_answer(tmp_path, langfuse, monkeypatch, caplog):
    def broken(self, **fields):
        raise RuntimeError('langfuse is down')

    monkeypatch.setattr(FakeObservation, 'update', broken)
    with caplog.at_level(logging.WARNING, logger='devrag.tracing'):
        question = ask(tmp_path, FakeModel())
    assert question['status'] == 'completed'
    assert 'Langfuse tracing call failed' in caplog.text


def test_support_chat_turn_is_traced_per_conversation_with_the_search_tool(tmp_path, langfuse):
    model, search = ToolReplyModel(), SearchFixture()
    service = SupportService(tmp_path, model)
    cid = str(uuid4())
    SupportChat(service.root, service.store, model, None, search).send(cid, message('连接池是什么？', allow_web=True))
    service.close()
    assert langfuse.attributes == [{'trace_name': 'support-chat', 'tags': ['support', 'chat'], 'session_id': cid}]
    root = langfuse.roots[0]
    assert root.as_type == 'agent'
    tool = root.children[0]
    assert (tool.name, tool.as_type, tool.fields['input'], tool.ended) == ('web_search', 'tool', 'SQLAlchemy connection pooling', True)
    assert tool.fields['output'] == {'status': 'found', 'sources': 1}
    assert root.scores == [{'name': 'outcome', 'value': 'completed', 'data_type': 'CATEGORICAL'}]


def test_ticket_analysis_is_traced_and_confirmation_scores_the_session(tmp_path, langfuse):
    service = SupportService(tmp_path, FakeSupportModel())
    thread = service.start(symptom=SYMPTOM)['thread_id']
    ready(service, thread)
    service.confirm(thread, resolved=True, resolution_note='调整连接池上限后恢复消费')
    service.close()
    assert {'trace_name': 'support-analysis', 'tags': ['support', 'ticket'], 'session_id': thread} in langfuse.attributes
    analysis = next(root for root in langfuse.roots if root.name == 'support-analysis')
    assert analysis.children[0].fields['usage_details'] == {'input': 10, 'output': 4}
    assert analysis.scores[0] == {'name': 'outcome', 'value': 'awaiting_confirmation', 'data_type': 'CATEGORICAL'}
    assert langfuse.scores == [{'session_id': thread, 'name': 'resolved', 'value': 1, 'data_type': 'BOOLEAN',
                                'score_id': f'{thread}-resolved'}]


def test_a_broken_langfuse_client_never_breaks_the_ticket_confirmation(tmp_path, langfuse, monkeypatch, caplog):
    def broken():
        raise ValueError('LANGFUSE_SAMPLE_RATE must be a number')

    monkeypatch.setattr(langfuse, 'get_client', broken)
    service = SupportService(tmp_path, FakeSupportModel())
    thread = service.start(symptom=SYMPTOM)['thread_id']
    ready(service, thread)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger='devrag.tracing'):
        confirmed = service.confirm(thread, resolved=True, resolution_note='调整连接池上限后恢复消费')
    service.close()
    assert confirmed['status'] == 'resolved'
    assert 'Langfuse tracing call failed' in caplog.text
