import json

import httpx
import pytest

from devrag.support.model import WEB_SEARCH_TOOL, SupportModel
from devrag.support.redact import mask_text
from devrag.support.schemas import Analysis, SessionConfirm, SessionSupplement


@pytest.mark.parametrize(('text', 'secret'), [
    ('Authorization: Basic dXNlcjpwYXNzd29yZA==', 'dXNlcjpwYXNzd29yZA=='),
    ('{"api_key": "sk-synthetic-key-123456789"}', 'sk-synthetic-key-123456789'),
    ('{"password": "two word secret"}', 'two word secret'),
    ('password=short', 'short'),
    ('request failed sk-synthetic-bare-key-123456789', 'sk-synthetic-bare-key-123456789'),
    ('postgres://worker:db-secret-value@localhost:5432/app', 'db-secret-value'),
    ('Authorization: Bearer tiny', 'tiny'),
])
def test_redaction_covers_common_log_credentials(text, secret):
    masked, _ = mask_text(text)
    assert secret not in masked
    assert mask_text(masked)[0] == masked


def test_log_limit_counts_utf8_bytes():
    with pytest.raises(ValueError):
        SessionSupplement(log_text='错' * 30_000)


def test_confirmation_requires_a_json_boolean():
    with pytest.raises(ValueError):
        SessionConfirm(resolved='yes')


def test_insufficient_evidence_cannot_also_claim_a_cause():
    with pytest.raises(ValueError):
        Analysis(status='insufficient', insufficient_reason='缺少日志', suggestions=[],
                 cause_hypotheses=[{'text': '已经确定是数据库连接池导致', 'basis': ['U1']}])


def test_runtime_rejects_unapproved_model_before_request(monkeypatch):
    monkeypatch.setenv('SUPPORT_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('SUPPORT_PRIMARY_MODEL', 'some-other-model')
    with pytest.raises(ValueError, match='模型'):
        SupportModel()


def test_runtime_does_not_send_key_to_a_different_gateway(monkeypatch):
    monkeypatch.setenv('SUPPORT_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('SUPPORT_BASE_URL', 'https://unapproved.example/v1')
    with pytest.raises(ValueError, match='网关'):
        SupportModel()


def test_runtime_uses_separate_model_profiles_and_records_usage(monkeypatch):
    monkeypatch.setenv('SUPPORT_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('SUPPORT_PRIMARY_MODEL', 'deepseek-v4.1-flash')
    monkeypatch.setenv('SUPPORT_FALLBACK_MODEL', 'gemini-3.8-flash-high')
    monkeypatch.delenv('SUPPORT_DEEPSEEK_REASONING_EFFORT', raising=False)
    monkeypatch.delenv('SUPPORT_GEMINI_REASONING_EFFORT', raising=False)
    requests, attempts = [], []

    def gateway(request):
        body = json.loads(request.content)
        assert request.url.path == '/v1/responses'
        assert request.headers['authorization'] == 'Bearer synthetic-test-key'
        assert body['store'] is False and body['stream'] is False
        assert json.loads(body['input']) == {'evidence': []}
        assert '不可信数据' in body['instructions']
        assert 'additionalProperties' not in body['instructions']
        assert 'messages' not in body and 'max_tokens' not in body
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(503, json={'error': 'temporary upstream failure'})
        return httpx.Response(200, json={'status': 'completed', 'output': [
            {'type': 'reasoning', 'summary': []},
            {'type': 'message', 'status': 'completed', 'role': 'assistant', 'content': [
                {'type': 'output_text', 'text': json.dumps({
                    'status': 'insufficient', 'cause_hypotheses': [], 'suggestions': [], 'insufficient_reason': '需要连接池配置',
                })},
            ]},
        ], 'usage': {'input_tokens': 11, 'output_tokens': 12}})

    runtime = SupportModel(transport=httpx.MockTransport(gateway))
    result = runtime.analyze({'evidence': []}, lambda *args: attempts.append(args) or len(attempts))
    assert result['status'] == 'insufficient'
    assert requests[0]['reasoning'] == {'effort': 'max'}
    assert requests[1]['reasoning'] == {'effort': 'max'}
    assert all(item['max_output_tokens'] == 16000 for item in requests)
    fmt = requests[0]['text']['format']
    assert fmt['type'] == 'json_schema' and fmt['strict'] is True
    assert fmt['schema']['additionalProperties'] is False
    assert set(fmt['schema']['required']) == set(fmt['schema']['properties'])
    assert fmt['schema']['$defs']['Hypothesis']['additionalProperties'] is False
    assert 'text' not in requests[1]
    assert [item['model'] for item in requests] == ['deepseek-v4.1-flash', 'gemini-3.8-flash-high']
    assert attempts[-1][4] == {'input_tokens': 11, 'output_tokens': 12}


@pytest.mark.parametrize('status', ['incomplete', 'failed', 'in_progress', 'cancelled'])
def test_runtime_rejects_unfinished_json_even_if_it_parses(monkeypatch, status):
    monkeypatch.setenv('SUPPORT_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('SUPPORT_FALLBACK_MODEL', '')
    payload = {'status': 'insufficient', 'cause_hypotheses': [], 'suggestions': [], 'insufficient_reason': '需更多日志'}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        'status': status, 'usage': {'input_tokens': 10, 'output_tokens': 20},
        'output': [{'type': 'message', 'status': 'completed', 'role': 'assistant',
                    'content': [{'type': 'output_text', 'text': json.dumps(payload)}]}],
    }))
    attempts = []
    with pytest.raises(RuntimeError, match='截断'):
        SupportModel(transport=transport).analyze({}, lambda *args: attempts.append(args) or 1)
    assert attempts[-1][2] == 'failed'
    assert attempts[-1][4] == {'input_tokens': 10, 'output_tokens': 20}


@pytest.mark.parametrize('output', [
    [],
    [{'type': 'reasoning', 'summary': []}],
    [{'type': 'message', 'status': 'completed', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': ''}]}],
    [{'type': 'message', 'status': 'completed', 'role': 'assistant', 'content': [{'type': 'refusal', 'refusal': 'private-detail'}]}],
])
def test_runtime_rejects_empty_or_refused_response(monkeypatch, output):
    monkeypatch.setenv('SUPPORT_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('SUPPORT_FALLBACK_MODEL', '')
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={'status': 'completed', 'output': output}))
    with pytest.raises(RuntimeError) as caught:
        SupportModel(transport=transport).analyze({}, lambda *args: 1)
    assert 'private-detail' not in str(caught.value)


def test_runtime_requires_dedicated_key_even_if_other_model_key_exists(monkeypatch):
    monkeypatch.delenv('SUPPORT_API_KEY', raising=False)
    monkeypatch.setenv('CIYUAN_API_KEY', 'synthetic-other-group-key')
    with pytest.raises(RuntimeError, match='SUPPORT_API_KEY'):
        SupportModel().analyze({}, lambda *args: 1)


def test_authentication_failure_does_not_leak_error_body_or_try_another_model(monkeypatch):
    monkeypatch.setenv('SUPPORT_API_KEY', 'synthetic-test-key')
    requests, attempts = [], []

    def gateway(request):
        requests.append(request)
        return httpx.Response(401, json={'error': 'synthetic-test-key private-detail'})

    with pytest.raises(RuntimeError, match='HTTP 401') as caught:
        SupportModel(transport=httpx.MockTransport(gateway)).analyze({}, lambda *args: attempts.append(args) or 1)
    assert len(requests) == 1 and attempts[-1][2] == 'failed'
    assert 'private-detail' not in str(caught.value)
    assert 'synthetic-test-key' not in json.dumps(attempts)


def test_inconsistent_analysis_falls_back_with_original_evidence(monkeypatch):
    monkeypatch.setenv('SUPPORT_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('SUPPORT_PRIMARY_MODEL', 'deepseek-v4.1-flash')
    monkeypatch.setenv('SUPPORT_FALLBACK_MODEL', 'gemini-3.8-flash-high')
    requests, attempts = [], []

    def gateway(request):
        requests.append(json.loads(request.content))
        analysis = {'status': 'insufficient', 'cause_hypotheses': [], 'suggestions': [], 'insufficient_reason': '缺少现场日志'}
        if len(requests) == 1:
            analysis['cause_hypotheses'] = [{'text': '未经证实的版本行为', 'basis': ['U1']}]
        return httpx.Response(200, json={'status': 'completed', 'output': [
            {'type': 'message', 'status': 'completed', 'role': 'assistant',
             'content': [{'type': 'output_text', 'text': json.dumps(analysis)}]},
        ]})

    evidence = {'evidence': [{'id': 'U1', 'content': '偶发卡住，没有日志或稳定复现'}]}
    result = SupportModel(transport=httpx.MockTransport(gateway)).analyze(evidence, lambda *args: attempts.append(args) or 1)
    assert result['status'] == 'insufficient' and result['cause_hypotheses'] == []
    assert [entry[2] for entry in attempts if entry[2]] == ['failed', 'completed']
    assert len(requests) == 2
    assert json.loads(requests[0]['input']) == json.loads(requests[1]['input']) == evidence


def _chat_message(reply='最新稳定版是 3.15.0', source_ids=None):
    return {'type': 'message', 'status': 'completed', 'role': 'assistant', 'content': [
        {'type': 'output_text', 'text': json.dumps(
            {'reply': reply, 'source_ids': source_ids or [], 'needs_diagnosis': False})}]}


def test_chat_prefers_gemini_while_analysis_keeps_deepseek_first(monkeypatch):
    monkeypatch.setenv('SUPPORT_API_KEY', 'synthetic-test-key')
    models = []

    def gateway(request):
        body = json.loads(request.content)
        models.append(body['model'])
        if body['model'] == 'gemini-3.8-flash-high':
            return httpx.Response(503, json={'error': 'upstream'})
        return httpx.Response(200, json={'status': 'completed', 'output': [_chat_message(reply='回落成功')]})

    model = SupportModel(transport=httpx.MockTransport(gateway))
    assert model.reply({'message': 'hi', 'evidence': []}, lambda *args: 1)['reply'] == '回落成功'
    assert models == ['gemini-3.8-flash-high', 'deepseek-v4.1-flash']
    models.clear()
    with pytest.raises(RuntimeError):
        model.analyze({'evidence': []}, lambda *args: 1)
    assert models[0] == 'deepseek-v4.1-flash'


def test_chat_tool_loop_searches_then_synthesizes_with_strict_schema_kept(monkeypatch):
    monkeypatch.setenv('SUPPORT_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('SUPPORT_CHAT_PRIMARY_MODEL', 'deepseek-v4.1-flash')
    monkeypatch.setenv('SUPPORT_CHAT_FALLBACK_MODEL', '')
    requests, executed = [], []

    def gateway(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(200, json={'status': 'completed', 'output': [
                {'type': 'reasoning', 'summary': []},
                {'type': 'function_call', 'call_id': 'call_1', 'name': 'web_search',
                 'arguments': json.dumps({'query': 'Python latest stable version'})},
            ], 'usage': {'input_tokens': 10, 'output_tokens': 5}})
        return httpx.Response(200, json={'status': 'completed', 'output': [
            _chat_message(source_ids=['W1'])], 'usage': {'input_tokens': 30, 'output_tokens': 9}})

    def executor(args):
        executed.append(args)
        return {'status': 'found', 'sources': [
            {'id': 'W1', 'title': 'Python', 'url': 'https://python.org', 'content': '3.15.0', 'scope': 'snippet'}]}

    model = SupportModel(transport=httpx.MockTransport(gateway))
    result = model.reply({'message': 'Python 最新版本', 'evidence': []}, lambda *args: 1,
                         tool=WEB_SEARCH_TOOL, tool_executor=executor)
    assert result['reply'] == '最新稳定版是 3.15.0' and result['source_ids'] == ['W1']
    assert executed == [{'query': 'Python latest stable version'}]
    assert len(requests) == 2
    # round1 保留 DS 严格 json_schema，同时挂工具、tool_choice=auto
    assert requests[0]['tools'] == [WEB_SEARCH_TOOL] and requests[0]['tool_choice'] == 'auto'
    assert requests[0]['text']['format']['strict'] is True
    # round2 用 tool_choice=none，并把工具结果按 call_id 回填
    assert requests[1]['tool_choice'] == 'none'
    outputs = [item for item in requests[1]['input'] if item.get('type') == 'function_call_output']
    assert outputs and outputs[0]['call_id'] == 'call_1'
    assert json.loads(outputs[0]['output'])['sources'][0]['id'] == 'W1'


def test_chat_without_tool_call_answers_directly_and_skips_search(monkeypatch):
    monkeypatch.setenv('SUPPORT_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('SUPPORT_CHAT_FALLBACK_MODEL', '')
    requests, executed = [], []

    def gateway(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'status': 'completed', 'output': [_chat_message(reply='is 比身份，== 比值。')]})

    def executor(args):
        executed.append(args)
        return {'status': 'found', 'sources': []}

    model = SupportModel(transport=httpx.MockTransport(gateway))
    result = model.reply({'message': 'is 和 == 区别', 'evidence': []}, lambda *args: 1,
                         tool=WEB_SEARCH_TOOL, tool_executor=executor)
    assert result['reply'] == 'is 比身份，== 比值。'
    assert executed == [] and len(requests) == 1


def test_chat_reply_without_tool_config_is_single_shot(monkeypatch):
    monkeypatch.setenv('SUPPORT_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('SUPPORT_CHAT_FALLBACK_MODEL', '')
    requests = []

    def gateway(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={'status': 'completed', 'output': [_chat_message(reply='你好。')]})

    result = SupportModel(transport=httpx.MockTransport(gateway)).reply({'message': 'hi', 'evidence': []}, lambda *args: 1)
    assert result['reply'] == '你好。'
    assert 'tools' not in requests[0] and 'tool_choice' not in requests[0]


def test_transient_timeout_retries_same_model_without_falling_back(monkeypatch):
    monkeypatch.setenv('SUPPORT_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('SUPPORT_CHAT_PRIMARY_MODEL', 'deepseek-v4.1-flash')
    monkeypatch.setenv('SUPPORT_CHAT_FALLBACK_MODEL', 'gemini-3.8-flash-high')
    monkeypatch.setattr('devrag.support.model.time.sleep', lambda *_: None)
    models = []

    def gateway(request):
        models.append(json.loads(request.content)['model'])
        if len(models) == 1:
            raise httpx.ReadTimeout('transient handshake', request=request)
        return httpx.Response(200, json={'status': 'completed', 'output': [_chat_message(reply='重试后成功')]})

    result = SupportModel(transport=httpx.MockTransport(gateway)).reply({'message': 'hi', 'evidence': []}, lambda *args: 1)
    assert result['reply'] == '重试后成功'
    assert models == ['deepseek-v4.1-flash', 'deepseek-v4.1-flash']


def test_empty_response_retries_same_model_then_succeeds(monkeypatch):
    monkeypatch.setenv('SUPPORT_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('SUPPORT_CHAT_FALLBACK_MODEL', '')
    monkeypatch.setattr('devrag.support.model.time.sleep', lambda *_: None)
    calls = []

    def gateway(request):
        calls.append(json.loads(request.content)['model'])
        if len(calls) == 1:
            return httpx.Response(200, json={'status': 'completed', 'output': [
                {'type': 'message', 'status': 'completed', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': ''}]}]})
        return httpx.Response(200, json={'status': 'completed', 'output': [_chat_message(reply='第二次有内容')]})

    result = SupportModel(transport=httpx.MockTransport(gateway)).reply({'message': 'hi', 'evidence': []}, lambda *args: 1)
    assert result['reply'] == '第二次有内容' and len(calls) == 2


def test_retry_attempts_are_bounded_then_fail(monkeypatch):
    monkeypatch.setenv('SUPPORT_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('SUPPORT_FALLBACK_MODEL', '')
    monkeypatch.setenv('SUPPORT_MAX_ATTEMPTS', '3')
    monkeypatch.setattr('devrag.support.model.time.sleep', lambda *_: None)
    calls = []

    def gateway(request):
        calls.append(request)
        raise httpx.ConnectTimeout('always down', request=request)

    with pytest.raises(RuntimeError, match='超时'):
        SupportModel(transport=httpx.MockTransport(gateway)).analyze({'evidence': []}, lambda *args: 1)
    assert len(calls) == 3


def test_chat_uses_lighter_budget_than_analysis(monkeypatch):
    monkeypatch.setenv('SUPPORT_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('SUPPORT_FALLBACK_MODEL', '')
    monkeypatch.setenv('SUPPORT_CHAT_FALLBACK_MODEL', '')
    monkeypatch.setattr('devrag.support.model.time.sleep', lambda *_: None)
    bodies, timeouts = [], []

    def gateway(request):
        bodies.append(json.loads(request.content))
        timeouts.append(request.extensions['timeout']['read'])
        raise httpx.ReadTimeout('down', request=request)

    model = SupportModel(transport=httpx.MockTransport(gateway))
    with pytest.raises(RuntimeError, match='超时'):
        model.reply({'message': 'hi', 'evidence': []}, lambda *args: 1)
    assert len(bodies) == 2
    assert {body['reasoning']['effort'] for body in bodies} == {'high'}
    assert set(timeouts) == {60}
    bodies.clear()
    timeouts.clear()
    with pytest.raises(RuntimeError, match='超时'):
        model.analyze({'evidence': []}, lambda *args: 1)
    assert len(bodies) == 3
    assert {body['reasoning']['effort'] for body in bodies} == {'max'}
    assert set(timeouts) == {120}
