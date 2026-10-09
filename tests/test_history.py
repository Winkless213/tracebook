import hashlib

import pytest
from fastapi.testclient import TestClient
from test_answers import FakeModel
from test_api import login
from test_knowledge import bundle

from devrag.app import create_app
from devrag.history import HistoryStore, SyncBatch
from devrag.retrieval import Retriever
from devrag.store import Store


def record(text='部署失败：8080端口已被占用。', number=1, role='tool-output'):
    return {'id': hashlib.sha256(f'record-{number}'.encode()).hexdigest(), 'role': role, 'text': text,
            'time': '2026-10-09T05:00:00+00:00', 'provenance': {'file': 'rollout-example.jsonl', 'line': number,
            'record_sha256': hashlib.sha256(f'raw-{number}'.encode()).hexdigest(), 'source_sha256': 'a' * 64, 'part': 0}}


def setup_history(tmp_path):
    store = Store(tmp_path / 'knowledge.sqlite')
    pid = store.ingest(bundle({'README.md': '项目提供可追溯的部署诊断。'}))['project']['id']
    return store, HistoryStore(store), pid


def sync(history, pid, records=None, auto=False):
    return history.sync(SyncBatch(project_id=pid, harness='codex', native_id='native-session', title='端口冲突排查',
                                  records=records or [record()], auto_distill=auto))


def test_sync_immutable_idempotent_records_and_project_boundary(tmp_path):
    store, history, pid = setup_history(tmp_path)
    first = sync(history, pid)
    assert first['added'] == 1
    assert sync(history, pid)['added'] == 0
    altered = record('新内容不能覆盖原记录。')
    with pytest.raises(ValueError, match='原记录'):
        sync(history, pid, [altered])
    detail = history.session(first['session']['id'])
    assert detail['total'] == 1 and detail['records'][0]['text'] == record()['text']
    other = store.ingest(bundle({'README.md': '另一个项目'}, project='other'))['project']['id']
    assert history.sessions(other) == []
    assert history.record(detail['records'][0]['id'])['provenance']['line'] == 1


def test_auto_job_only_for_new_records_and_durable_result(tmp_path):
    store, history, pid = setup_history(tmp_path)
    first = sync(history, pid, auto=True)
    assert first['job_id']
    assert sync(history, pid, auto=True)['job_id'] is None
    claimed = history.claim()
    assert claimed == first['job_id']
    job = history.job(claimed)
    evidence_id = job['record_ids'][0]
    result = {'records': [{'id': evidence_id, 'category': 'tool_observation', 'claim_status': 'supported_as_record',
                          'finding': '当次端口绑定失败', 'related_ids': []}],
              'lessons': [{'candidate': '启动成功需结合绑定结果核验', 'evidence_ids': [evidence_id], 'scope': '仅指当次启动'}], 'gaps': []}
    history.finish(claimed, result)
    reopened = HistoryStore(Store(store.path))
    assert reopened.job(claimed)['result'] == result
    assert reopened.session(first['session']['id'])['session']['pending_count'] == 0
    assert any(item['origin'] == 'experience' for item in reopened.corpus(pid))


def test_restart_and_cancel_do_not_lose_or_resurrect_jobs(tmp_path):
    store, history, pid = setup_history(tmp_path)
    created = sync(history, pid, auto=True)
    history.claim()
    attempt = history.begin_attempt(created['job_id'], 'test-model')
    reopened = HistoryStore(Store(store.path))
    reopened.recover()
    assert reopened.job(created['job_id'])['status'] == 'queued'
    assert reopened.job(created['job_id'])['attempts'][0]['status'] == 'interrupted'
    reopened.action(created['job_id'], 'cancel')
    reopened.recover()
    assert reopened.job(created['job_id'])['status'] == 'cancelled'
    assert reopened.claim() is None
    assert attempt
    replacement = reopened.enqueue(created['session']['id'])
    assert replacement['id'] != created['job_id'] and replacement['status'] == 'queued'
    assert reopened.job(created['job_id'])['status'] == 'cancelled'


def test_invalid_model_citation_and_secrets_cannot_enter_knowledge(tmp_path):
    _, history, pid = setup_history(tmp_path)
    created = sync(history, pid, auto=True)
    history.claim()
    invalid = {'records': [{'id': 'z' * 64, 'category': 'tool_observation', 'claim_status': 'supported_as_record',
                           'finding': '没有依据', 'related_ids': []}], 'lessons': [], 'gaps': []}
    with pytest.raises(ValueError, match='引用'):
        history.finish(created['job_id'], invalid)
    assert history.job(created['job_id'])['result'] is None
    with pytest.raises(ValueError, match='密钥'):
        sync(history, pid, [record('sk-proj-' + 'TestSecretNotARealCredential' * 3, 2)])


def test_history_in_latest_retrieval_but_not_old_code_snapshot(tmp_path):
    store, history, pid = setup_history(tmp_path)
    old = store.resolve(pid)['id']
    store.ingest(bundle({'README.md': '项目提供第二版部署诊断。'}, commit='b' * 40))
    created = sync(history, pid)
    retriever = Retriever(store)
    latest = retriever.search(pid, '8080端口占用部署失败')
    assert any(source.get('origin') == 'conversation' for source in latest['sources'])
    assert all(source.get('origin') != 'conversation' for source in retriever.search(pid, '8080端口占用部署失败', old)['sources'])
    assert history.session(created['session']['id'])['records']


def test_authenticated_history_api_and_pagination(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_PASSWORD', 'local-test-password-only')
    monkeypatch.setenv('SESSION_SECRET', 'local-test-session-secret-at-least-32-characters')
    app = create_app(tmp_path, FakeModel(), start_worker=False)
    with TestClient(app) as browser:
        assert browser.get('/api/history/sessions').status_code == 401
        login(browser)
        pid = browser.post('/api/import', json=bundle({'README.md': '本项目支持部署诊断。'}).model_dump(by_alias=True)).json()['project']['id']
        payload = {'project_id': pid, 'harness': 'claude', 'native_id': 'native-id', 'title': '真实排障', 'records': [record(number=i) for i in range(1, 4)], 'auto_distill': False}
        response = browser.post('/api/history/sync', json=payload)
        assert response.status_code == 200
        sid = response.json()['session']['id']
        detail = browser.get(f'/api/history/sessions/{sid}?offset=1&limit=1').json()
        assert detail['total'] == 3 and len(detail['records']) == 1
        assert browser.get(f'/api/history/records/{detail["records"][0]["id"]}').json()['text']


def test_conversation_project_has_no_fabricated_git_commit(tmp_path):
    store = Store(tmp_path / 'knowledge.sqlite')
    history = HistoryStore(store)
    project = history.create_project('trade-inquiry-history')
    assert history.create_project('trade-inquiry-history')['id'] == project['id']
    snapshot = store.resolve(project['id'])
    assert snapshot['origin'] == 'conversation' and snapshot['commit'] == ''
    assert snapshot['file_count'] == 0
    sync(history, project['id'])
    found = Retriever(store).search(project['id'], '8080端口占用')
    assert found['sources'] and all(item['origin'] == 'conversation' for item in found['sources'])
    assert all(item['commit'] == '' for item in found['sources'])
