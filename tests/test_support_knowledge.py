import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from test_answers import FakeModel
from test_support import FakeSupportModel
from test_support_api import login

from devrag.app import create_app


@pytest.fixture
def published(tmp_path, monkeypatch):
    root = tmp_path / 'published'
    root.mkdir()
    entries = []
    for name, status in [('worker', 'ready'), ('other-worker', 'ready'), ('pending-worker', 'pending')]:
        (root / name).mkdir()
        (root / name / 'CODEBASE.md').write_text('# 队列连接池\n任务队列超时需检查连接池上限。\npassword=synthetic-published-secret', encoding='utf-8')
        (root / name / 'private.md').write_text('PRIVATE-UNPUBLISHED-DATA', encoding='utf-8')
        entries.append({'name': name, 'branch': 'main', 'commit': 'ab01' * 10, 'status': status, 'documents': ['CODEBASE.md']})
    (root / 'catalog.json').write_text(json.dumps({'projects': entries}), encoding='utf-8')
    monkeypatch.setenv('DEVELOPER_KNOWLEDGE_DIR', str(root))
    monkeypatch.setenv('APP_PASSWORD', 'local-test-password-only')
    monkeypatch.setenv('SESSION_SECRET', 'local-test-session-secret-at-least-32-characters')
    model = FakeSupportModel()
    app = create_app(tmp_path / 'isolated', FakeModel(), start_worker=False, support_model=model)
    with TestClient(app) as client:
        login(client)
        yield client, model, root


def analyze(client, project_id):
    thread = client.post('/api/support/sessions', json={'symptom': '任务队列超时，需要检查连接池上限', 'project_id': project_id}).json()['thread_id']
    view = client.post(f'/api/support/sessions/{thread}/supplement', json={
        'product_version': 'v1 Windows', 'reproduction': '任务队列连续提交后全部超时',
    }).json()
    return thread, view


def test_homepage_published_knowledge_is_searchable_without_importing_or_modifying_it(published):
    client, model, root = published
    response = client.get('/api/support/projects')
    assert response.status_code == 200
    data = response.json()
    project = next(item for item in data['projects'] if item['name'] == 'worker')
    assert project['knowledge_available'] and data['model_configured']
    before = {str(path): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    thread, view = analyze(client, project['id'])
    assert view['status'] == 'awaiting_confirmation'
    source = next(item for item in view['report']['evidence'] if item['kind'] == 'knowledge')
    assert source['origin'] == 'published' and source['published_project'] == 'worker'
    assert source['path'] == 'CODEBASE.md' and source['commit'] == 'ab01' * 10
    assert source['sha256'] == hashlib.sha256(before[str(root / 'worker' / 'CODEBASE.md')]).hexdigest()
    assert source['sha256'] in client.get(f'/api/support/sessions/{thread}/export').text
    assert 'synthetic-published-secret' not in json.dumps(model.received)
    assert 'PRIVATE-UNPUBLISHED-DATA' not in json.dumps(model.received)
    assert client.get('/api/projects').json() == []
    assert before == {str(path): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_pending_knowledge_does_not_masquerade_as_retrieved_evidence(published):
    client, _, _ = published
    project = next(item for item in client.get('/api/support/projects').json()['projects'] if item['name'] == 'pending-worker')
    assert project['knowledge_available'] is False
    _, view = analyze(client, project['id'])
    assert not any(item['kind'] == 'knowledge' for item in view['report']['evidence'])
    assert any('知识' in warning for warning in view['warnings'])


def test_published_snapshot_changes_without_rewriting_saved_reports(published):
    client, _, root = published
    project = client.get('/api/support/projects').json()['projects'][0]
    first, old = analyze(client, project['id'])
    old_source = next(item for item in old['evidence'] if item['kind'] == 'knowledge')
    (root / project['name'] / 'CODEBASE.md').write_text('# 队列连接池\n任务队列超时的新排查步骤：先检查慢查询。', encoding='utf-8')
    _, new = analyze(client, project['id'])
    new_source = next(item for item in new['evidence'] if item['kind'] == 'knowledge')
    assert new_source['snapshot_id'] != old_source['snapshot_id']
    assert new_source['sha256'] != old_source['sha256']
    assert client.get(f'/api/support/sessions/{first}').json()['report'] == old['report']


def test_unknown_published_project_cannot_open_arbitrary_paths(published):
    client, _, _ = published
    response = client.post('/api/support/sessions', json={'symptom': '请读取本机任意目录中的文件', 'project_id': 'published:../../private'})
    assert response.status_code == 404
