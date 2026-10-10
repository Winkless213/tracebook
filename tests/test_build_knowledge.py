import hashlib
import json

import httpx
import pytest
from openai import APIError

from devrag.distill import Distiller
from scripts import build_knowledge
from scripts.build_knowledge import RUN_ABORT, GenerationPending, make_batches, process_batches, summarize, validate_result, write_index


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


@pytest.mark.parametrize('terminal', ['completed', 'missing', 'incomplete', 'disconnect_after_completed'])
def test_native_responses_preserves_valid_results_and_rejects_unfinished_streams(tmp_path, monkeypatch, terminal):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    monkeypatch.setenv('DISTILL_API', 'responses')
    client, calls = httpx.Client, []
    raw = json.dumps({'markdown': 'Entry [[app.py]]'})

    def respond(request):
        calls.append(request)
        body = json.loads(request.content)
        assert request.url.path == '/v1/responses'
        assert body['reasoning'] == {'effort': 'max'}
        assert body['store'] is False
        assert not {'max_tokens', 'max_completion_tokens', 'max_output_tokens'} & body.keys()
        events = [{'type': 'response.output_text.delta', 'delta': raw, 'sequence_number': 1,
                   'item_id': 'message', 'output_index': 0, 'content_index': 0}]
        if terminal != 'missing':
            status = 'completed' if terminal == 'disconnect_after_completed' else terminal
            events.append({'type': 'response.' + status, 'sequence_number': 2,
                'response': {'id': 'resp_test', 'object': 'response', 'created_at': 1, 'model': 'gpt-6.1-sol',
                    'status': status, 'output': [],
                    'incomplete_details': {'reason': 'max_output_tokens'} if status == 'incomplete' else None,
                    'usage': {'input_tokens': 10, 'output_tokens': 20, 'total_tokens': 30,
                              'input_tokens_details': {'cached_tokens': 0},
                              'output_tokens_details': {'reasoning_tokens': 12}}}})
        text = ''.join('event: ' + row['type'] + '\ndata: ' + json.dumps(row) + '\n\n' for row in events)
        if terminal == 'disconnect_after_completed':
            class CompletedThenDisconnected(httpx.SyncByteStream):
                def __iter__(self):
                    yield text.encode('utf-8')
                    raise httpx.RemoteProtocolError('Connection closed after completed response')

            return httpx.Response(200, stream=CompletedThenDisconnected(),
                                  headers={'content-type': 'text/event-stream'}, request=request)
        return httpx.Response(200, text=text, headers={'content-type': 'text/event-stream'}, request=request)

    class FixtureClient(client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond))

    monkeypatch.setattr(httpx, 'Client', FixtureClient)
    model = Distiller()
    model.model = 'gpt-6.1-sol'
    data = {'repository': {'name': 'example'}, 'files': []}
    if terminal in {'completed', 'disconnect_after_completed'}:
        saved = summarize(model, data, tmp_path, {'app.py'}, retry_transient=False)
        assert saved['markdown'] == 'Entry [[app.py]]'
        assert saved['usage'] == {'input_tokens': 10, 'output_tokens': 20}
        assert saved['api'] == 'responses'
        assert saved['provider_response_id'] == 'resp_test'
        assert summarize(model, data, tmp_path, {'app.py'})['reused']
        assert len(calls) == 1
    else:
        with pytest.raises(RuntimeError, match='未完整结束|输出长度限制'):
            summarize(model, data, tmp_path, {'app.py'}, retry_transient=False)
        assert not [path for path in tmp_path.glob('*.json') if not path.name.endswith('.progress.json')]


@pytest.mark.parametrize('failure', ['connection', 'upstream', 'authentication', 'stream_upstream',
                                    'stream_authentication', 'stream_missing_completed'])
def test_native_responses_retries_temporary_errors_and_preserves_hard_error_evidence(tmp_path, monkeypatch, failure):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    monkeypatch.setenv('DISTILL_API', 'responses')
    client, calls = httpx.Client, []
    authentication = failure.endswith('authentication')
    code = 'invalid_api_key' if authentication else ('missing_completed' if failure == 'stream_missing_completed' else 'upstream_error')

    def respond(request):
        calls.append(request)
        if failure == 'connection':
            raise httpx.ConnectError('Temporary upstream disconnection', request=request)
        message = 'Authentication failed for test-key' if authentication else 'Upstream disconnected test-key'
        if failure == 'stream_missing_completed':
            message = 'gateway error: stream ended without response.completed'
        if failure.startswith('stream_'):
            body = {'type': 'error', 'code': code, 'message': message, 'param': None, 'sequence_number': 1}
            return httpx.Response(200, text='event: error\ndata: ' + json.dumps(body) + '\n\n',
                                  headers={'content-type': 'text/event-stream'}, request=request)
        return httpx.Response(500, json={'error': {'code': code, 'message': message}}, request=request)

    class FixtureClient(client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond))

    monkeypatch.setattr(httpx, 'Client', FixtureClient)
    model = Distiller()
    model.yield_between_batches = True
    error = (RuntimeError if failure == 'stream_authentication' else APIError) if authentication else build_knowledge.RecoverableStreamError
    with pytest.raises(error):
        summarize(model, {'repository': {'name': 'example'}}, tmp_path, {'app.py'})
    progress = json.loads(next(tmp_path.glob('*.progress.json')).read_text(encoding='utf-8'))
    assert len(calls) == (1 if authentication else 2)
    assert RUN_ABORT.is_set() == authentication
    assert progress['api'] == 'responses'
    assert 'test-key' not in json.dumps(progress)
    if failure != 'connection':
        assert code in progress['provider_error']


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


def test_http_gateway_failure_keeps_the_sanitized_provider_reason(tmp_path, monkeypatch):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    client = httpx.Client
    reply = {'error': {'type': 'upstream_error', 'message': 'Upstream rejected api_key=secret-value'}}
    transport = httpx.MockTransport(lambda request: httpx.Response(500, json=reply, request=request))
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=transport))
    with pytest.raises(httpx.HTTPStatusError):
        summarize(Distiller(), {'repository': {'name': 'example'}, 'files': []}, tmp_path,
                  {'app.py'}, retry_transient=False)
    progress = json.loads(next(tmp_path.glob('*.progress.json')).read_text(encoding='utf-8'))
    assert 'upstream_error' in progress['provider_error']
    assert 'secret-value' not in progress['provider_error']


@pytest.mark.parametrize('body', ['Upstream connection failed api_key=secret-value',
                                  '{"message":"Upstream connection failed api_key=secret-value"}'])
def test_gateway_error_without_an_error_object_keeps_its_reason(tmp_path, monkeypatch, body):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(502, text=body, request=request))
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=transport))
    with pytest.raises(httpx.HTTPStatusError):
        summarize(Distiller(), {'repository': {'name': 'example'}, 'files': []}, tmp_path,
                  {'app.py'}, retry_transient=False)
    progress = json.loads(next(tmp_path.glob('*.progress.json')).read_text(encoding='utf-8'))
    assert 'Upstream connection failed' in progress['provider_error']
    assert 'secret-value' not in progress['provider_error']


def test_gateway_wrapped_auth_failure_is_not_treated_as_a_transient_500(tmp_path, monkeypatch):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    client, calls = httpx.Client, []

    def respond(request):
        calls.append(request)
        return httpx.Response(500, json={'error': {'type': 'invalid_api_key', 'message': 'Authentication failed'}},
                              request=request)

    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(respond)))
    model = Distiller()
    model.yield_between_batches = True
    with pytest.raises(httpx.HTTPStatusError):
        summarize(model, {'repository': {'name': 'example'}, 'files': []}, tmp_path, {'app.py'})
    assert len(calls) == 1
    assert RUN_ABORT.is_set()


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


def test_index_exposes_completed_topics_and_revokes_them_on_source_change(tmp_path):
    project = tmp_path / 'example'
    (project / 'history').mkdir(parents=True)
    source = project / 'history/session.json'
    source.write_text(json.dumps({'session': {'id': 'session'}, 'source_sha256': 'old'}), encoding='utf-8')
    document = project / 'SESSION-TOPICS.md'
    document.write_text('# Topics', encoding='utf-8')
    (project / 'session-topics.json').write_text(json.dumps({
        'source_commit_for_topic_context': 'head', 'all_sessions_classified': True,
        'sessions': [{'id': 'session', 'source_sha256': 'old', 'complete': True}],
        'markdown_sha256': hashlib.sha256(document.read_bytes()).hexdigest()}), encoding='utf-8')
    repo = {'name': 'example', 'url': 'https://github.com/owner/example.git', 'branch': 'main',
            'commit': 'head', 'status': 'verified'}
    write_index([repo], tmp_path)
    row = json.loads((tmp_path / 'catalog.json').read_text(encoding='utf-8'))['projects'][0]
    assert row['status'] == 'pending' and row['topics_status'] == 'ready'
    assert row['history_status'] == 'pending' and row['history_sessions'] == 1
    assert row['documents'] == ['SESSION-TOPICS.md']
    source.write_text(json.dumps({'session': {'id': 'session'}, 'source_sha256': 'new'}), encoding='utf-8')
    write_index([repo], tmp_path)
    row = json.loads((tmp_path / 'catalog.json').read_text(encoding='utf-8'))['projects'][0]
    assert row['topics_status'] == 'pending' and row['documents'] == []


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


def test_queued_generation_yields_between_new_batches_but_still_reads_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    client, calls = httpx.Client, []

    def respond(request):
        calls.append(request)
        event = {'choices': [{'delta': {'content': json.dumps({'markdown': 'Entry [[app.py]]'})}, 'finish_reason': 'stop'}]}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n', request=request)

    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(respond)))
    model = Distiller()
    model.yield_between_batches = True
    first = {'repository': {'name': 'example'}, 'files': ['first']}
    second = {'repository': {'name': 'example'}, 'files': ['second']}
    summarize(model, first, tmp_path, {'app.py'})
    assert summarize(model, first, tmp_path, {'app.py'})['markdown'] == 'Entry [[app.py]]'
    with pytest.raises(GenerationPending):
        summarize(model, second, tmp_path, {'app.py'})
    assert len(calls) == 1
    resumed = Distiller()
    resumed.yield_between_batches = True
    summarize(resumed, second, tmp_path, {'app.py'})
    assert len(calls) == 2


def test_temporary_failure_yields_to_other_jobs_and_can_retry_in_the_next_round(tmp_path, monkeypatch):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    client, calls = httpx.Client, []

    def respond(request):
        calls.append(request)
        if len(calls) <= 2:
            raise httpx.ReadTimeout('temporary upstream failure')
        event = {'choices': [{'delta': {'content': json.dumps({'markdown': 'Entry [[app.py]]'})}, 'finish_reason': 'stop'}]}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n', request=request)

    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(respond)))
    model = Distiller()
    model.yield_between_batches = True
    with pytest.raises(build_knowledge.RecoverableStreamError):
        summarize(model, {'repository': {'name': 'example'}}, tmp_path, {'app.py'})
    assert not RUN_ABORT.is_set()
    resumed = Distiller()
    resumed.yield_between_batches = True
    assert summarize(resumed, {'repository': {'name': 'example'}}, tmp_path, {'app.py'})['attempts'] == 1
    assert len(calls) == 3


def test_unchanged_source_batch_is_reused_on_new_commit_and_changed_content_generates_again(tmp_path, monkeypatch):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    client, calls = httpx.Client, []

    def respond(request):
        calls.append(request)
        event = {'choices': [{'delta': {'content': json.dumps({'markdown': 'Entry [[app.py]]'})}, 'finish_reason': 'stop'}]}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n', request=request)

    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(respond)))
    first = {'repository': {'name': 'example', 'commit': 'old'}, 'files': [{'path': 'app.py', 'content': 'original'}]}
    summarize(Distiller(), first, tmp_path, {'app.py'})
    newer = {**first, 'repository': {'name': 'example', 'commit': 'new'}}
    summarize(Distiller(), newer, tmp_path, {'app.py'})
    assert len(calls) == 1
    summarize(Distiller(), {**newer, 'files': [{'path': 'app.py', 'content': 'changed'}]}, tmp_path, {'app.py'})
    assert len(calls) == 2


def test_approved_previous_model_cache_keeps_its_identity_without_new_requests(tmp_path, monkeypatch):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    monkeypatch.setenv('DISTILL_MODEL', 'gpt-6-astra')
    client, calls = httpx.Client, []

    def respond(request):
        calls.append(json.loads(request.content)['model'])
        event = {'choices': [{'delta': {'content': json.dumps({'markdown': 'Entry [[app.py]]'})}, 'finish_reason': 'stop'}]}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n', request=request)

    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(respond)))
    source = {'repository': {'name': 'example', 'commit': 'head'}, 'files': ['original']}
    original = summarize(Distiller(), source, tmp_path, {'app.py'})
    monkeypatch.setenv('DISTILL_MODEL', 'gpt-6.1-sol')
    monkeypatch.setenv('KNOWLEDGE_REUSE_MODELS', 'gpt-6-astra')
    reused = summarize(Distiller(), source, tmp_path, {'app.py'})
    assert calls == ['gpt-6-astra']
    assert reused['model'] == 'gpt-6-astra'
    assert reused['cache_key_sha256'] == original['cache_key_sha256']
    assert reused['reused']
    changed = summarize(Distiller(), {**source, 'files': ['changed']}, tmp_path, {'app.py'})
    assert calls == ['gpt-6-astra', 'gpt-6.1-sol']
    assert changed['model'] == 'gpt-6.1-sol'
