import json
import threading

import httpx
import pytest
from test_history import setup_history, sync

from devrag.distill import Distiller, HistoryWorker


def result_for(records):
    return {'records': [{'id': item['id'], 'category': 'tool_observation', 'claim_status': 'supported_as_record',
                         'finding': '当次端口占用', 'related_ids': []} for item in records], 'lessons': [], 'gaps': []}


def test_streaming_uses_requested_model_without_manual_token_limit(tmp_path, monkeypatch):
    _, history, pid = setup_history(tmp_path)
    created = sync(history, pid, auto=True)
    records = [history.record(rid) for rid in history.job(created['job_id'])['record_ids']]
    monkeypatch.setenv('DISTILL_API_KEY', 'local-test-key')
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://example.invalid/v1')
    seen = []

    def response(request):
        body = json.loads(request.content)
        assert body['model'] == 'gpt-6-astra' and body['reasoning_effort'] == 'max'
        assert body['stream']
        assert 'max_completion_tokens' not in body and 'max_tokens' not in body
        chunks = [{'choices': [{'delta': {'content': json.dumps(result_for(records))}, 'finish_reason': None}]},
                  {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
                  {'choices': [], 'usage': {'prompt_tokens': 80, 'completion_tokens': 300}}]
        return httpx.Response(200, text='\n\n'.join('data: ' + json.dumps(item) for item in chunks) + '\n\ndata: [DONE]\n\n')

    model = Distiller(transport=httpx.MockTransport(response))
    assert model.distill(records, seen.append, lambda: False) == result_for(records)
    assert seen[-1] == {'input_tokens': 80, 'output_tokens': 300}


def test_exhausted_reasoning_budget_cannot_complete_a_job(monkeypatch):
    monkeypatch.setenv('DISTILL_API_KEY', 'local-test-key')
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://example.invalid/v1')
    model = Distiller(transport=httpx.MockTransport(lambda _: httpx.Response(200, text='data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\ndata: [DONE]\n')))
    with pytest.raises(RuntimeError, match='截断'):
        model.distill([], lambda _: None, lambda: False)


def test_worker_failure_retry_and_cancel_during_generation(tmp_path):
    _, history, pid = setup_history(tmp_path)
    created = sync(history, pid, auto=True)
    jid = created['job_id']

    class Model:
        model = 'test-model'
        configured = True
        mode = 'fail'

        def distill(self, records, usage, cancelled):
            if self.mode == 'fail':
                raise RuntimeError('测试模拟连接失败')
            usage({'input_tokens': 50, 'output_tokens': 100})
            if self.mode == 'cancel':
                history.action(jid, 'cancel')
                assert cancelled()
            return result_for(records)

    model = Model()
    worker = HistoryWorker(history, model)
    assert worker.process_next()
    assert history.job(jid)['status'] == 'failed'
    history.action(jid, 'retry')
    model.mode = 'cancel'
    assert worker.process_next()
    job = history.job(jid)
    assert job['status'] == 'cancelled' and job['result'] is None
    assert job['usage']['total_tokens'] == 150


def test_worker_shutdown_leaves_recoverable_inputs(tmp_path):
    _, history, pid = setup_history(tmp_path)
    created = sync(history, pid, auto=True)
    stop = threading.Event()

    class Model:
        model = 'test-model'
        configured = True

        def distill(self, records, usage, cancelled):
            stop.set()
            assert cancelled()
            raise InterruptedError('停止')

    assert HistoryWorker(history, Model()).process_next(stop)
    assert history.job(created['job_id'])['status'] == 'generating'
    history.recover()
    assert history.job(created['job_id'])['status'] == 'queued'
