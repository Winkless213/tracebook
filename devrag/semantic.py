"""本地嵌入与重排模型：fastembed 在 CPU 上运行，只在启用向量类检索时才加载。模型是预注册的，见 evaluation/README.md。"""
from threading import Lock

EMBEDDING_MODEL = 'jinaai/jina-embeddings-v2-base-zh'
RERANK_MODEL = 'BAAI/bge-reranker-v2-m3-int8'


class LocalModels:
    name = EMBEDDING_MODEL

    def __init__(self, cache_dir=None):
        self.cache_dir = str(cache_dir) if cache_dir else None
        self.embedder = self.reranker = None
        self.lock = Lock()

    def embedding_model(self):
        with self.lock:
            if self.embedder is None:
                from fastembed import TextEmbedding
                self.embedder = TextEmbedding(EMBEDDING_MODEL, cache_dir=self.cache_dir)
            return self.embedder

    def rerank_model(self):
        with self.lock:
            if self.reranker is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder
                self.reranker = TextCrossEncoder(RERANK_MODEL, cache_dir=self.cache_dir)
            return self.reranker

    def embed(self, texts):
        return list(self.embedding_model().passage_embed(texts, batch_size=16))

    def embed_query(self, text):
        return next(iter(self.embedding_model().query_embed(text)))

    def rerank(self, query, texts):
        return list(self.rerank_model().rerank(query, texts))
