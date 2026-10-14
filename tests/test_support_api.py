import pytest
from fastapi.testclient import TestClient
from test_answers import FakeModel
from test_knowledge import bundle
from test_support import LOG, OTHER, RAW, SYMPTOM, FakeSupportModel

from devrag.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_PASSWORD', 'local-test-password-only')
    monkeypatch.setenv('SESSION_SECRET', 'local-test-session-secret-at-least-32-characters')
    app = create_app(tmp_path, FakeModel(), start_worker=False, support_model=FakeSupportModel())
    with TestClient(app) as browser:
        yield browser, app


def login(browser):
    browser.headers['X-Requested-With'] = 'Tracebook'
    assert browser.post('/api/login', json={'password': 'local-test-password-only'}).status_code == 200


def test_support_requires_login_and_origin_header(client):
    browser, _ = client
    assert browser.get('/api/support/cases').status_code == 401
    assert browser.post('/api/support/sessions', json={'symptom': SYMPTOM}).status_code == 403


def test_support_flow_waits_resumes_confirms_and_lists_case(client):
    browser, _ = client
    login(browser)
    created = browser.post('/api/support/sessions', json={'symptom': SYMPTOM})
    assert created.status_code == 201
    thread = created.json()['thread_id']
    assert created.json()['status'] == 'awaiting_info'
    assert browser.get(f'/api/support/sessions/{thread}').json()['pending']['missing'] == ['product_version', 'reproduction']

    assert browser.post(f'/api/support/sessions/{thread}/supplement', json={'product_version': 'v4.1.0'}).json()['status'] == 'awaiting_info'

    report = browser.post(f'/api/support/sessions/{thread}/supplement', json={
        'product_version': 'v4.1.0', 'reproduction': '升级后重启即复现，任务持续排队', 'log_text': LOG})
    assert report.status_code == 200
    body = report.json()
    assert body['status'] == 'awaiting_confirmation'
    assert body['report']['verification']['status'] == 'analyzed'
    assert body['report']['hypotheses'][0]['basis'][0]['id']
    assert body['report']['redaction']['applied']
    assert '已脱敏' in body['masked_log']
    for secret in RAW:
        assert secret not in report.text

    confirmed = browser.post(f'/api/support/sessions/{thread}/confirm', json={'resolved': True, 'resolution_note': '调整连接池上限后恢复消费'})
    assert confirmed.json()['status'] == 'resolved'
    assert confirmed.json()['case_id']
    assert [case['thread_id'] for case in browser.get('/api/support/cases').json()] == [thread]
    assert [row['thread_id'] for row in browser.get('/api/support/sessions').json()] == [thread]
    for secret in RAW:
        assert secret not in browser.get(f'/api/support/sessions/{thread}').text
        assert secret not in browser.get('/api/support/cases').text


def test_support_session_retrieves_imported_knowledge(client):
    browser, _ = client
    login(browser)
    data = bundle({'docs/queue.md': '# 任务队列\n任务队列消费者在升级后需要重启；DB_POOL_MAX 控制连接池上限，默认 5。'}).model_dump(by_alias=True)
    pid = browser.post('/api/import', json=data).json()['project']['id']
    thread = browser.post('/api/support/sessions', json={'symptom': SYMPTOM, 'project_id': pid}).json()['thread_id']
    body = browser.post(f'/api/support/sessions/{thread}/supplement', json={
        'product_version': 'v4.1.0', 'reproduction': '升级后重启即复现，任务持续排队', 'log_text': LOG}).json()
    assert body['status'] == 'awaiting_confirmation'
    kinds = {item['kind'] for item in body['report']['evidence']}
    assert 'knowledge' in kinds
    assert any(item['kind'] == 'knowledge' and item['path'] == 'docs/queue.md' for item in body['report']['evidence'])


def test_support_without_runtime_credentials_reports_failure_not_guess(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_PASSWORD', 'local-test-password-only')
    monkeypatch.setenv('SESSION_SECRET', 'local-test-session-secret-at-least-32-characters')
    monkeypatch.delenv('SUPPORT_API_KEY', raising=False)
    app = create_app(tmp_path, FakeModel(), start_worker=False)
    with TestClient(app) as browser:
        browser.headers['X-Requested-With'] = 'Tracebook'
        browser.post('/api/login', json={'password': 'local-test-password-only'})
        thread = browser.post('/api/support/sessions', json={'symptom': OTHER}).json()['thread_id']
        body = browser.post(f'/api/support/sessions/{thread}/supplement', json={
            'product_version': 'v4.1.0', 'reproduction': '偶发 500，重试可恢复', 'log_text': 'ERROR export failed 500\n'}).json()
        assert body['status'] == 'failed'
        assert body['report'] is None
        assert '客服模型' in body['error']


def test_support_unknown_session_is_404(client):
    browser, _ = client
    login(browser)
    assert browser.get('/api/support/sessions/missing').status_code == 404
    assert browser.post('/api/support/sessions/missing/confirm', json={'resolved': True}).status_code == 404


def test_support_rejects_blank_and_oversized_input(client):
    browser, _ = client
    login(browser)
    assert browser.post('/api/support/sessions', json={'symptom': '短'}).status_code == 422
    thread = browser.post('/api/support/sessions', json={'symptom': SYMPTOM}).json()['thread_id']
    assert browser.post(f'/api/support/sessions/{thread}/supplement', json={'log_text': 'x' * 200_000}).status_code == 422
    assert browser.post(f'/api/support/sessions/{thread}/supplement', json={}).status_code == 422


def test_support_validation_error_does_not_echo_raw_input(client):
    browser, _ = client
    login(browser)
    thread = browser.post('/api/support/sessions', json={'symptom': SYMPTOM}).json()['thread_id']
    secret = 'synthetic-error-response-secret-321'
    invalid = {'log_text': 'password=' + secret + 'x' * 70_000}
    response = browser.post(f'/api/support/sessions/{thread}/supplement', json=invalid)
    assert response.status_code == 422
    assert secret not in response.text
    assert browser.get(f'/api/support/sessions/{thread}').json()['status'] == 'awaiting_info'


def test_support_cases_do_not_cross_project_boundaries(client):
    browser, app = client
    login(browser)
    first = bundle({'docs/queue.md': '# queue\n任务队列连接池不足。'})
    second = first.model_copy(update={'project': 'separate-project'})
    pids = [browser.post('/api/import', json=item.model_dump(by_alias=True)).json()['project']['id'] for item in (first, second)]
    thread = browser.post('/api/support/sessions', json={'symptom': SYMPTOM, 'project_id': pids[0]}).json()['thread_id']
    info = {'product_version': 'v1', 'reproduction': '升级后每次任务都排队', 'log_text': LOG}
    browser.post(f'/api/support/sessions/{thread}/supplement', json=info)
    browser.post(f'/api/support/sessions/{thread}/confirm', json={'resolved': True, 'resolution_note': '调整连接池后恢复'})
    other = browser.post('/api/support/sessions', json={'symptom': SYMPTOM, 'project_id': pids[1]}).json()['thread_id']
    browser.post(f'/api/support/sessions/{other}/supplement', json=info)
    assert not any(item['kind'] == 'case' for item in app.state.support.model.received[-1]['evidence'])


def test_support_retry_export_and_confirmation_validation(client):
    browser, app = client
    login(browser)
    thread = browser.post('/api/support/sessions', json={'symptom': SYMPTOM}).json()['thread_id']
    assert browser.get(f'/api/support/sessions/{thread}/export').status_code == 409
    assert browser.post(f'/api/support/sessions/{thread}/confirm', json={'resolved': True}).status_code == 409
    app.state.support.model.basis = ['fake-id']
    browser.post(f'/api/support/sessions/{thread}/supplement', json={'product_version': 'v1', 'reproduction': '启动后任务持续排队'})
    app.state.support.model.basis = None
    assert browser.post(f'/api/support/sessions/{thread}/retry').json()['status'] == 'awaiting_confirmation'
    assert browser.post(f'/api/support/sessions/{thread}/confirm', json={'resolved': 'yes'}).status_code == 422
    exported = browser.get(f'/api/support/sessions/{thread}/export')
    assert exported.status_code == 200
    assert 'text/markdown' in exported.headers['content-type']
    assert SYMPTOM in exported.text


def test_knowledge_citations_preserve_verified_commit_and_hash(client):
    browser, _ = client
    login(browser)
    data = bundle({'docs/queue.md': '# 任务队列\n任务队列连接池不足会导致排队。'}, commit='ab01' * 10).model_dump(by_alias=True)
    imported = browser.post('/api/import', json=data).json()
    thread = browser.post('/api/support/sessions', json={'symptom': SYMPTOM, 'project_id': imported['project']['id']}).json()['thread_id']
    view = browser.post(f'/api/support/sessions/{thread}/supplement', json={
        'product_version': 'v1', 'reproduction': '升级后任务持续排队', 'log_text': LOG,
    }).json()
    source = next(item for item in view['report']['evidence'] if item['kind'] == 'knowledge')
    assert source['commit'] == data['commit']
    assert source['sha256'] == data['files'][0]['sha256']
    assert source['snapshot_id'] == imported['snapshot']['id']
    exported = browser.get(f'/api/support/sessions/{thread}/export').text
    assert data['commit'] in exported
    assert imported['snapshot']['id'] in exported
