import pytest
from test_answers import FakeModel
from test_history import setup_history, sync
from test_knowledge import bundle

from devrag.app import create_app
from devrag.retrieval import Retriever, fuse
from devrag.semantic import LocalModels
from devrag.store import Store
from devrag.worker import Worker

FILES = {'cache.py': 'def put(key):\n    """写入 Redis，30 秒后失效。"""\n    return key\n',
         'pay.py': 'def refund(order):\n    """退款前检查重复请求。"""\n    return order\n'}


class FakeModels:
    """按概念词给向量：问题和片段没有共同词，也会因为同一概念而相近。记录送进模型的全部文本。"""
    name = 'fake-embedding'
    concepts = [('缓存', 'Redis'), ('退款', 'refund'), ('日志', 'log')]

    def __init__(self):
        self.embedded, self.reranked = [], []

    def vector(self, text):
        return [sum(word in text for word in words) + 0.01 for words in self.concepts]

    def embed(self, texts):
        self.embedded.extend(texts)
        return [self.vector(text) for text in texts]

    def embed_query(self, text):
        return self.vector(text)

    def rerank(self, query, texts):
        self.reranked.extend(texts)
        question = self.vector(query)
        return [sum(a * b for a, b in zip(question, self.vector(text))) + len(text) / 1000 for text in texts]


def project(tmp_path):
    store = Store(tmp_path / 'kb.sqlite')
    return store, store.ingest(bundle(FILES))['project']['id']


def test_vector_search_recalls_a_chunk_that_shares_no_words_with_the_question(tmp_path):
    store, pid = project(tmp_path)
    retriever = Retriever(store, models=FakeModels())
    assert retriever.search(pid, '缓存保留多长时间？')['sources'] == []
    for method in ('vector', 'hybrid', 'hybrid-rerank'):
        found = retriever.search(pid, '缓存保留多长时间？', method=method)
        assert found['method'] == method and found['sources'][0]['path'] == 'cache.py'


def test_rrf_puts_items_ranked_high_by_both_lists_first():
    assert [number for _, number in fuse([1, 2, 3], [3, 1, 4])] == [1, 3, 2, 4]


def test_rerank_reorders_candidates_but_leaves_history_where_fusion_put_it(tmp_path):
    models = FakeModels()
    corpus = [{'path': 'a.py', 'symbol': '', 'content': 'x'},
              {'path': '会话/codex/排查', 'symbol': 'user', 'content': '历史记录', 'origin': 'conversation'},
              {'path': 'b.py', 'symbol': '', 'content': 'a longer chunk'}]
    reordered = Retriever(Store(tmp_path / 'kb.sqlite'), models=models).rerank('问题', corpus, [(0.3, 0), (0.2, 1), (0.1, 2)])
    assert [number for _, number in reordered] == [2, 1, 0]
    assert not any('历史记录' in text for text in models.reranked)


def test_session_history_is_never_embedded_or_reranked(tmp_path):
    store, history, pid = setup_history(tmp_path)
    sync(history, pid)
    models = FakeModels()
    found = Retriever(store, models=models).search(pid, '8080端口占用部署失败', method='hybrid-rerank')
    assert any(source.get('origin') == 'conversation' for source in found['sources'])
    assert models.embedded and models.reranked
    assert not any('8080' in text for text in models.embedded + models.reranked)


def test_chunk_embeddings_are_cached_in_the_store(tmp_path):
    store, pid = project(tmp_path)
    Retriever(store, models=FakeModels()).search(pid, '退款', method='vector')
    again = FakeModels()
    assert Retriever(store, models=again).search(pid, '退款', method='vector')['sources'][0]['path'] == 'pay.py'
    assert again.embedded == []


def test_vector_methods_need_models_and_unknown_methods_fail(tmp_path):
    store, pid = project(tmp_path)
    with pytest.raises(ValueError):
        Retriever(store, method='hybrid')
    with pytest.raises(ValueError):
        Retriever(store).search(pid, '退款', method='vector')
    with pytest.raises(ValueError):
        Retriever(store).search(pid, '退款', method='dense')


def test_reranker_scores_one_pair_at_a_time():
    sizes = []

    class CrossEncoder:
        def rerank(self, query, documents, batch_size=64):
            sizes.append(batch_size)
            return iter([0.5] * len(documents))

    models = LocalModels()
    models.reranker = CrossEncoder()
    assert models.rerank('问题', ['a', 'b']) == [0.5, 0.5]
    assert sizes == [1]


def test_questions_record_the_retrieval_method_they_used(tmp_path):
    store, pid = project(tmp_path)
    job = store.create_question(pid, '缓存保留多长时间？', 'method-check-1')
    assert job['method'] is None
    Worker(store, Retriever(store, 'hybrid', FakeModels()), FakeModel()).process_next()
    assert store.question(job['id'])['method'] == 'hybrid'


def test_old_databases_keep_showing_bm25_for_existing_questions(tmp_path):
    store, pid = project(tmp_path)
    job = store.create_question(pid, '缓存保留多长时间？', 'method-check-2')
    with store.db() as db:
        db.execute('ALTER TABLE questions DROP COLUMN method')
    assert Store(tmp_path / 'kb.sqlite').question(job['id'])['method'] == 'bm25-symbol'


def test_app_rejects_an_unknown_retrieval_method(tmp_path, monkeypatch):
    monkeypatch.setenv('RETRIEVAL_METHOD', 'dense')
    with pytest.raises(ValueError):
        create_app(tmp_path, FakeModel(), start_worker=False)
