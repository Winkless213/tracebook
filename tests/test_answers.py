import httpx
import pytest
from test_knowledge import bundle

from devrag.llm import LLM, validate_answer
from devrag.models import Answer
from devrag.retrieval import Retriever
from devrag.store import Store
from devrag.worker import Worker


class FakeModel:
    configured = True
    primary = 'test-primary'
    fallback = 'test-backup'

    def __init__(self, fail=False):
        self.fail = fail

    def answer(self, question, sources, record, cancelled):
        attempt = record(self.primary, None, None, 0, {}, None)
        if self.fail:
            record(self.primary, attempt, 'failed', 1, {}, '测试失败')
            raise RuntimeError('测试上游失败')
        record(self.primary, attempt, 'completed', 1, {'input_tokens': 10, 'output_tokens': 5}, None)
        return {'status': 'answered', 'summary': '缓存已停用', 'claims': [{'text': '当前代码关闭缓存', 'evidence': [{'source_id': sources[0]['id'], 'quote': sources[0]['content']}]}], 'gaps': []}


def test_bad_quote_and_missing_reference_rejected():
    sources = [{'id': 'S1', 'content': '缓存已经停用，每次查询数据库。'}]
    data = {'status': 'answered', 'summary': '停用', 'claims': [{'text': '停用', 'evidence': [{'source_id': 'S1', 'quote': '缓存仍然启用'}]}], 'gaps': []}
    with pytest.raises(ValueError, match='原文'):
        validate_answer(Answer.model_validate(data), sources)
    data['claims'][0]['evidence'][0] = {'source_id': 'S9', 'quote': '缓存已经停用'}
    with pytest.raises(ValueError, match='编号'):
        validate_answer(Answer.model_validate(data), sources)


def test_failed_retry_keeps_snapshot_sources_and_usage(tmp_path):
    store = Store(tmp_path / 'kb.sqlite')
    pid = store.ingest(bundle({'README.md': '缓存已经停用，每次查询数据库。'}))['project']['id']
    question = store.create_question(pid, '缓存现在采用什么策略？', 'same-request')
    worker = Worker(store, Retriever(store), FakeModel(fail=True))
    worker.process_next()
    failed = store.question(question['id'])
    assert failed['status'] == 'failed'
    store.ingest(bundle({'README.md': '缓存变成 Redis。'}, commit='b' * 40))
    store.action(question['id'], 'retry')
    worker.model.fail = False
    worker.process_next()
    result = store.question(question['id'])
    assert result['status'] == 'completed' and result['stale']
    assert result['sources'] == failed['sources']
    assert result['usage']['total_tokens'] == 15
    assert result['usage']['unreported_calls'] == 1


def test_restart_recovers_once_then_requires_retry(tmp_path):
    store = Store(tmp_path / 'kb.sqlite')
    pid = store.ingest(bundle({'README.md': '缓存已经停用，每次查询数据库。'}))['project']['id']
    question = store.create_question(pid, '缓存现在采用什么策略？', 'restart-id')
    store.claim()
    store.begin_attempt(question['id'], 'test')
    reopened = Store(store.path)
    reopened.recover()
    assert reopened.question(question['id'])['status'] == 'queued'
    assert reopened.question(question['id'])['attempts'][0]['status'] == 'interrupted'
    reopened.claim()
    reopened.recover()
    assert reopened.question(question['id'])['status'] == 'failed'


def test_retry_cannot_bypass_queue_limit(tmp_path):
    store = Store(tmp_path / 'kb.sqlite')
    pid = store.ingest(bundle({'README.md': '缓存已经停用，每次查询数据库。'}))['project']['id']
    failed = store.create_question(pid, '缓存现在采用什么策略？', 'failed-job')
    store.update(failed['id'], status='failed', error='测试错误')
    for index in range(5):
        store.create_question(pid, '缓存现在采用什么策略？', f'queued-job-{index}')
    with pytest.raises(ValueError, match='最多排队'):
        store.action(failed['id'], 'retry')
    assert store.question(failed['id'])['status'] == 'failed'


def test_llm_primary_bad_json_falls_back_with_accounting(monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL', 'https://model.example/v1/chat/completions')
    monkeypatch.setenv('LLM_API_KEY', 'test-only')
    monkeypatch.setenv('LLM_PRIMARY_MODEL', 'primary')
    monkeypatch.setenv('LLM_FALLBACK_MODEL', 'backup')
    calls = []

    def post(self, url, **kwargs):
        assert url.endswith('/v1/chat/completions')
        calls.append(kwargs['json']['model'])
        text = 'bad json' if len(calls) == 1 else '{"status":"insufficient","summary":"资料不足","claims":[],"gaps":["缺少资料"]}'
        return httpx.Response(200, request=httpx.Request('POST', url), json={'choices': [{'message': {'content': text}}], 'usage': {'prompt_tokens': 20, 'completion_tokens': 5}})

    monkeypatch.setattr(httpx.Client, 'post', post)
    records = []

    def record(*args):
        records.append(args)
        return len(records)

    result = LLM().answer('问题', [], record, lambda: False)
    assert result['status'] == 'insufficient'
    assert calls == ['primary', 'backup']
    assert records[1][2] == 'failed'
    assert records[1][4]['input_tokens'] == 20
