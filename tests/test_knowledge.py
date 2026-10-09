import hashlib

import pytest

from devrag.indexing import split_file
from devrag.models import Bundle
from devrag.retrieval import Retriever
from devrag.store import Store


def bundle(files, commit='a' * 40, branch='main', project='example'):
    return Bundle.model_validate({'schema': 1, 'project': project, 'branch': branch, 'commit': commit,
        'exported_at': '2026-10-09T00:00:00Z', 'excluded': [],
        'files': [{'path': path, 'content': text, 'sha256': hashlib.sha256(text.encode()).hexdigest()} for path, text in files.items()]})


def test_lines_are_exact_and_python_symbols_retained():
    text = '# header\n\ndef refund(order):\n    """退款必须检查重复请求。"""\n    return order\n'
    chunks = split_file('app/payments.py', text)
    assert any('refund' in item['symbol'] for item in chunks)
    for item in chunks:
        assert item['content'] == '\n'.join(text.splitlines()[item['start_line'] - 1:item['end_line']])


def test_import_update_delete_and_pinned_history(tmp_path):
    store = Store(tmp_path / 'kb.sqlite')
    first = store.ingest(bundle({'docs/cache.md': '# 缓存\n缓存采用 Redis，过期时间 30 秒。', 'old.py': 'legacy_cache = True'}))
    pid = first['project']['id']
    retriever = Retriever(store)
    assert retriever.search(pid, '缓存 Redis')["sources"]
    job = store.create_question(pid, '缓存使用什么存储？', 'request-1')
    second = store.ingest(bundle({'docs/cache.md': '# 缓存\n缓存已停用，每次从数据库读取。'}, commit='b' * 40))
    assert second['snapshot']['changes'] == {'added': 0, 'modified': 1, 'removed': 1}
    active = retriever.search(pid, 'legacy_cache')
    assert active['sources'] == []
    assert store.question(job['id'])['snapshot_id'] == first['snapshot']['id']
    assert store.question(job['id'])['stale']
    assert store.source(first['snapshot']['id'], 'old.py')['content'] == 'legacy_cache = True'


def test_identical_import_is_idempotent_and_wrong_hash_rejected(tmp_path):
    store = Store(tmp_path / 'kb.sqlite')
    data = bundle({'README.md': '缓存采用数据库读取。'})
    first = store.ingest(data)
    assert store.ingest(data)['unchanged']
    assert len(store.snapshots(first['project']['id'])) == 1
    raw = data.model_dump(by_alias=True)
    raw['files'][0]['sha256'] = 'f' * 64
    with pytest.raises(ValueError, match='哈希'):
        Bundle.model_validate(raw)


def test_project_and_branch_scope(tmp_path):
    store = Store(tmp_path / 'kb.sqlite')
    main = store.ingest(bundle({'README.md': '主分支缓存已停用'}))
    other = store.ingest(bundle({'README.md': '特性分支缓存启用'}, branch='feature/cache'))
    found = Retriever(store).search(main['project']['id'], '缓存')
    assert all(source['commit'] == 'a' * 40 for source in found['sources'])
    assert all('特性' not in source['content'] for source in found['sources'])
    with pytest.raises(ValueError):
        Retriever(store).search(main['project']['id'], '缓存', other['snapshot']['id'])


@pytest.mark.parametrize('path,text', [('../secret.md', 'x'), ('.env', 'x'), ('docs/key.md', '-----BEGIN PRIVATE KEY-----'), ('config.py', 'api_key = "sk-abcdefghijklmnopqrstuvwxyz1234567890"')])
def test_secret_and_unsafe_inputs_are_rejected(path, text):
    with pytest.raises(ValueError):
        bundle({path: text})


def test_unknown_query_does_not_return_arbitrary_sources(tmp_path):
    store = Store(tmp_path / 'kb.sqlite')
    result = store.ingest(bundle({'README.md': '部署使用 Docker Compose，失败恢复旧版本。'}))
    assert Retriever(store).search(result['project']['id'], '量子香蕉时空旅行')['sources'] == []


def test_request_idempotency_is_content_bound(tmp_path):
    store = Store(tmp_path / 'kb.sqlite')
    result = store.ingest(bundle({'README.md': 'source'}))
    pid = result['project']['id']
    first = store.create_question(pid, '缓存使用什么存储？', 'request-1')
    assert store.create_question(pid, '缓存使用什么存储？', 'request-1')['id'] == first['id']
    with pytest.raises(ValueError):
        store.create_question(pid, '主备模型如何切换？', 'request-1')


def test_qualified_class_method_can_be_found(tmp_path):
    store = Store(tmp_path / 'kb.sqlite')
    data = bundle({'app/index.py': 'class BM25Index:\n    def search(self, query):\n        """小语料可为负分，以实际词项匹配。"""\n        return []\n'})
    pid = store.ingest(data)['project']['id']
    sources = Retriever(store).search(pid, 'BM25Index.search 为什么不能只保留正分结果？')['sources']
    assert sources and sources[0]['symbol'] == 'BM25Index.search'


def test_knowledge_map_preserves_scope_and_anchors_without_inventing_decisions(tmp_path):
    store = Store(tmp_path / 'kb.sqlite')
    data = bundle({'README.md': '# 启动\n运行应用。', 'app/cache.py': 'def load_cache():\n    return {}\n'})
    raw = data.model_dump(by_alias=True)
    raw['scope'] = {'selected_roots': ['README.md', 'app'], 'tracked_file_count': 10, 'out_of_scope_count': 8}
    result = store.ingest(Bundle.model_validate(raw))
    catalog = store.knowledge_map(result['project']['id'])
    assert catalog['scope']['out_of_scope_count'] == 8
    assert catalog['counts'] == {'document': 1, 'code': 1, 'test': 0, 'config': 0}
    code = next(item for item in catalog['files'] if item['path'] == 'app/cache.py')
    assert code['anchors'][0] == {'label': 'load_cache', 'start_line': 1, 'end_line': 2}
    assert any('测试' in gap for gap in catalog['gaps'])
    assert any('设计原因' in gap for gap in catalog['gaps'])
