import json

import httpx
import pytest

from devrag.distill import Distiller
from scripts import build_knowledge
from scripts.build_knowledge import RUN_ABORT, make_batches, process_batches, summarize, validate_result, write_index


@pytest.fixture(autouse=True)
def independent_model_run(monkeypatch):
    RUN_ABORT.clear()
    monkeypatch.setattr(build_knowledge, 'RETRY_WAIT', lambda state: 0)
    yield
    RUN_ABORT.clear()


def test_large_file_and_small_files_are_all_preserved_in_order():
    files = {'app/core.py': 'first\nsecond\nthird\nfourth\n', 'README.md': 'Purpose'}
    batches = make_batches(files, characters=12)
    restored = {path: '' for path in files}
    for batch in batches:
        for item in batch:
            restored[item['path']] += item['content']
    assert restored == files
    assert all(sum(len(item['content']) for item in batch) <= 12 for batch in batches)


def test_model_cannot_publish_a_citation_to_an_unseen_file():
    with pytest.raises(ValueError, match='citation'):
        validate_result({'markdown': '# Architecture\n[[invented.py]]'}, {'app.py'})


def test_citation_whitespace_is_normalized_only_when_real_path_exists():
    assert validate_result({'markdown': 'Entry [[ app.py ]]'}, {'app.py'}) == 'Entry [[app.py]]'


def test_model_result_requires_source_evidence():
    with pytest.raises(ValueError, match='citation'):
        validate_result({'markdown': '# Architecture\nEverything works.'}, {'app.py'})
    assert validate_result({'markdown': '# Architecture\nEntrypoint [[app.py]]'}, {'app.py'})


def test_batch_boundary_handles_one_long_line_and_unicode():
    files = {'中文.py': '开' * 31, '空.md': '', 'b.py': 'b\n'}
    batches = make_batches(files, characters=10)
    assert ''.join(item['content'] for batch in batches for item in batch if item['path'] == '中文.py') == '开' * 31
    assert all(sum(len(item['content']) for item in batch) <= 10 for batch in batches)


def test_http_failure_is_recorded_when_retry_is_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(524, request=request))
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=transport))
    with pytest.raises(httpx.HTTPStatusError):
        summarize(Distiller(), {'repository': {'name': 'example'}, 'files': []}, tmp_path, {'app.py'}, retry_transient=False)
    progress = json.loads(next(tmp_path.glob('*.progress.json')).read_text(encoding='utf-8'))
    assert progress['state'] == 'failed'
    assert progress['status_code'] == 524
    assert RUN_ABORT.is_set()
    with pytest.raises(InterruptedError):
        summarize(Distiller(), {'repository': {'name': 'another'}, 'files': []}, tmp_path, {'app.py'})


def test_rejected_citations_record_failure_and_preserve_model_output(tmp_path, monkeypatch):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    client = httpx.Client
    event = {'choices': [{'delta': {'content': json.dumps({'markdown': 'Entry [[missing.py]]'})},
                          'finish_reason': 'stop'}]}
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n', request=request))
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=transport))
    with pytest.raises(ValueError, match='citation'):
        summarize(Distiller(), {'repository': {'name': 'example'}, 'files': []}, tmp_path, {'app.py'})
    progress = json.loads(next(tmp_path.glob('*.progress.json')).read_text(encoding='utf-8'))
    assert progress['state'] == 'failed'
    assert progress['error_type'] == 'ValueError'
    assert next(tmp_path.glob('*.rejected.json')).exists()


def test_failed_checkout_is_visible_without_claiming_code_was_pulled(tmp_path):
    write_index([{'name': 'broken', 'url': 'https://github.com/owner/broken.git', 'branch': 'main',
                  'status': 'failed', 'error': 'network failure'}], tmp_path)
    row = json.loads((tmp_path / 'catalog.json').read_text(encoding='utf-8'))['projects'][0]
    assert row['status'] == 'unavailable'
    assert row['commit'] is None
    assert row['documents'] == []


def test_upstream_failure_does_not_launch_remaining_paid_batches():
    calls = []

    def failure(index, batch):
        calls.append(index)
        raise RuntimeError('upstream failure')

    with pytest.raises(RuntimeError, match='upstream failure'):
        process_batches(failure, ['first', 'second', 'third'])
    assert calls == [1]


def test_provider_stream_error_stops_requests_when_retry_is_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    client = httpx.Client
    event = {'error': {'type': 'upstream_error', 'message': 'Upstream HTTP/2 stream failed'}}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text='data: ' + json.dumps(event) + '\n', request=request))
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=transport))
    with pytest.raises(RuntimeError):
        summarize(Distiller(), {'repository': {'name': 'example'}, 'files': []}, tmp_path, {'app.py'}, retry_transient=False)
    assert RUN_ABORT.is_set()


def test_interrupted_stream_retries_then_reuses_only_complete_cached_result(tmp_path, monkeypatch):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    client, calls = httpx.Client, []

    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"discarded partial output"}}]}\n\n'
            raise httpx.RemoteProtocolError('incomplete chunked read')

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, stream=BrokenStream(), request=request)
        event = {'choices': [{'delta': {'content': json.dumps({'markdown': 'Entry [[app.py]]'})}, 'finish_reason': 'stop'}]}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n', request=request)

    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(respond)))
    data = {'repository': {'name': 'example'}, 'files': []}
    result = summarize(Distiller(), data, tmp_path, {'app.py'})
    assert result['markdown'] == 'Entry [[app.py]]'
    assert len(calls) == 2
    assert not RUN_ABORT.is_set()
    assert summarize(Distiller(), data, tmp_path, {'app.py'})['markdown'] == result['markdown']
    assert len(calls) == 2


@pytest.mark.parametrize(('status', 'body'), [
    (524, ''),
    (503, ''),
    (429, ''),
    (200, 'data: {"error":{"type":"upstream_error","message":"Upstream HTTP/2 stream failed"}}\n\n'),
    (200, 'data: {"choices":[{"delta":{"content":"incomplete"}}]}\n\n'),
    (200, 'data: {"choices":'),
])
def test_transient_failures_retry_complete_request(tmp_path, monkeypatch, status, body):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    client, calls = httpx.Client, []
    success = {'choices': [{'delta': {'content': json.dumps({'markdown': 'Entry [[app.py]]'})}, 'finish_reason': 'stop'}]}
    replies = [(status, body), (200, 'data: ' + json.dumps(success) + '\n\ndata: [DONE]\n\n')]

    def respond(request):
        calls.append(request)
        reply_status, reply_body = replies.pop(0)
        return httpx.Response(reply_status, text=reply_body, request=request)

    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(respond)))
    result = summarize(Distiller(), {'repository': {'name': 'example'}, 'files': []}, tmp_path, {'app.py'})
    assert result['markdown'] == 'Entry [[app.py]]'
    assert result['attempts'] == 2
    assert len(result['failed_attempts']) == 1
    assert len(calls) == 2
    assert not RUN_ABORT.is_set()
    payload = json.loads(calls[-1].content)
    assert 'max_tokens' not in payload and 'max_completion_tokens' not in payload


def test_repeated_read_timeouts_retry_without_an_attempt_quota(tmp_path, monkeypatch):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    client, calls = httpx.Client, []

    def respond(request):
        calls.append(request)
        if len(calls) <= 4:
            raise httpx.ReadTimeout('temporary stream timeout')
        event = {'choices': [{'delta': {'content': json.dumps({'markdown': 'Entry [[app.py]]'})}, 'finish_reason': 'stop'}]}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n', request=request)

    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(respond)))
    result = summarize(Distiller(), {'repository': {'name': 'example'}, 'files': []}, tmp_path, {'app.py'})
    assert result['attempts'] == 5
    assert len(result['failed_attempts']) == 4
    assert not RUN_ABORT.is_set()


@pytest.mark.parametrize(('status', 'body'), [
    (401, ''),
    (200, 'data: {"error":{"type":"invalid_api_key","message":"Upstream authentication failed"}}\n\n'),
])
def test_authentication_failure_is_not_retried(tmp_path, monkeypatch, status, body):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    client, calls = httpx.Client, []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, text=body, request=request)

    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(respond)))
    with pytest.raises((httpx.HTTPStatusError, RuntimeError)):
        summarize(Distiller(), {'repository': {'name': 'example'}, 'files': []}, tmp_path, {'app.py'})
    assert len(calls) == 1
    assert RUN_ABORT.is_set()


def test_cancellation_during_backoff_does_not_send_another_request(tmp_path, monkeypatch):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    client, calls = httpx.Client, []
    cache = tmp_path / 'knowledge/example/.generation-cache'
    cache.mkdir(parents=True)
    real_wait = build_knowledge.wait_for_retry

    def cancel_wait(seconds, stop_path):
        if seconds:
            stop_path.touch()
        real_wait(0, stop_path)

    def respond(request):
        calls.append(request)
        return httpx.Response(524, request=request)

    monkeypatch.setattr(build_knowledge, 'RETRY_WAIT', lambda state: 1)
    monkeypatch.setattr(build_knowledge, 'wait_for_retry', cancel_wait)
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(respond)))
    with pytest.raises(InterruptedError):
        summarize(Distiller(), {'repository': {'name': 'example'}, 'files': []}, cache, {'app.py'})
    assert len(calls) == 1
    progress = json.loads(next(cache.glob('*.progress.json')).read_text(encoding='utf-8'))
    assert progress['state'] == 'cancelled'
    assert len(progress['failed_attempts']) == 1
