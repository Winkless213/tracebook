import pytest
from fastapi.testclient import TestClient
from test_answers import FakeModel
from test_knowledge import bundle

from devrag.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_PASSWORD', 'local-test-password-only')
    monkeypatch.setenv('SESSION_SECRET', 'local-test-session-secret-at-least-32-characters')
    app = create_app(tmp_path, FakeModel(), start_worker=False)
    with TestClient(app) as browser:
        yield browser, app


def login(browser):
    browser.headers['X-Requested-With'] = 'Tracebook'
    assert browser.post('/api/login', json={'password': 'local-test-password-only'}).status_code == 200


def test_auth_and_csrf(client):
    browser, _ = client
    assert browser.get('/api/projects').status_code == 401
    assert browser.post('/api/login', json={'password': 'local-test-password-only'}).status_code == 403
    login(browser)
    assert browser.get('/api/session').json()['authenticated']
    browser.post('/api/logout', json={})
    assert browser.get('/api/projects').status_code == 401


def test_full_import_search_question_source_and_export(client):
    browser, app = client
    login(browser)
    data = bundle({'docs/cache.md': '# 缓存\n缓存已停用，每次查询数据库。'}).model_dump(by_alias=True)
    imported = browser.post('/api/import', json=data)
    assert imported.status_code == 200
    pid = imported.json()['project']['id']
    question = '缓存当前是怎样工作的？'
    response = browser.post('/api/search', json={'project_id': pid, 'question': question})
    source = response.json()['sources'][0]
    assert source['start_line'] == 1
    sid = response.json()['snapshot']['id']
    assert browser.get(f'/api/sources/{sid}', params={'path': source['path']}).json()['content'].startswith('# 缓存')
    job = browser.post('/api/questions', json={'project_id': pid, 'question': question}, headers={'Idempotency-Key': 'test-question'}).json()
    assert browser.get(f'/api/questions/{job["id"]}/export').status_code == 409
    app.state.worker.process_next()
    result = browser.get(f'/api/questions/{job["id"]}').json()
    assert result['status'] == 'completed'
    assert 'docs/cache.md' in browser.get(f'/api/questions/{job["id"]}/export').text


def test_oversized_and_invalid_path_rejected(client):
    browser, _ = client
    login(browser)
    assert browser.post('/api/import', content=b'x' * 3_000_001, headers={'Content-Type': 'application/json'}).status_code == 413
    data = bundle({'README.md': 'text'}).model_dump(by_alias=True)
    data['files'][0]['path'] = '../.env'
    assert browser.post('/api/import', json=data).status_code == 422
