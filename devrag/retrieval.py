import hashlib
import logging
import re
from collections import OrderedDict
from threading import Lock

import jieba
import numpy as np
from rank_bm25 import BM25Okapi

from .history import HistoryStore

jieba.setLogLevel(logging.ERROR)
METHODS = ('term-overlap', 'bm25-symbol', 'vector', 'hybrid', 'hybrid-rerank')
VECTOR_METHODS = {'vector', 'hybrid', 'hybrid-rerank'}
# 预注册参数（evaluation/README.md），不按评测题调整。
VECTOR_TOP, RRF_K, RERANK_TOP = 50, 60, 30
STOP = set('的 了 和 是 在 我 你 吗 什么 哪些 怎么 如何 请 需要 帮 一个 这个 那个 一下 可以 是否 能 要 给 有 都 只 还 为什么 项目 当前 the is a an in to for of what how do does can i my you your it are all must which please'.split())


def tokenize(text):
    expanded = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    raw = re.findall(r'[A-Za-z_][A-Za-z_0-9.]*|[\u4e00-\u9fff]+|\d+', expanded)
    result = []
    for part in raw:
        if re.search('[\u4e00-\u9fff]', part):
            result.extend(word for word in jieba.cut(part) if word not in STOP and len(word) > 1)
        else:
            result.extend(word for word in [part.lower(), *re.split('[_.]', part.lower())] if word not in STOP and len(word) > 1)
    return result


def comparison_order(ranked, corpus, top_k):
    # 中英文混合仓库中，设计文档可通过明确符号指向未命中中文词项的实现。
    ordered = sorted(ranked, reverse=True)
    documents = [number for _, number in ordered if corpus[number]['kind'] == 'document'][:top_k]
    references = set()
    for number in documents:
        references.update(re.findall(r'\b(?:[A-Z][A-Za-z0-9]+|[a-zA-Z]+_[a-zA-Z_0-9]+)\b', corpus[number]['content']))
    values = {number: value for value, number in ordered}
    linked = {}
    for number, item in enumerate(corpus):
        if item['kind'] != 'code':
            continue
        terms = set(re.findall(r'[A-Za-z_][A-Za-z_0-9]*', item['symbol'] + ' ' + item['content']))
        matches = references & terms
        if matches and references & set(item['symbol'].split('.')):
            values[number] = values.get(number, 0) + len(matches) * 5
            linked[number] = len(matches)
    code = sorted(((linked.get(number, 0), value, number) for number, value in values.items() if corpus[number]['kind'] == 'code'), reverse=True)[:2]
    symbols = {part for _, _, number in code for part in corpus[number]['symbol'].split('.')}
    linked_documents = [number for number in documents if symbols & set(re.findall(r'[A-Za-z_][A-Za-z_0-9]*', corpus[number]['content']))]
    priority = [number for _, _, number in code] + (linked_documents or documents)[:1]
    rest = [number for _, number in sorted(((value, number) for number, value in values.items()), reverse=True)]
    return [(values[number], number) for number in dict.fromkeys(priority + rest)]


def passage(item):
    return item['path'] + ' ' + item['symbol'] + '\n' + item['content']


def usable(item, words, explicit_symbols):
    if item['kind'] == 'document' and all(line.startswith('#') for line in item['content'].splitlines() if line.strip()):
        return False
    # 明确点名的符号已删除时，不能用拆开的通用单词制造命中。
    return not explicit_symbols or any(word in words for word in explicit_symbols)


def fuse(*rankings, k=RRF_K):
    """倒数排名融合：每个排名列表给第 r 名加 1/(k+r)。"""
    scores = {}
    for ranking in rankings:
        for rank, number in enumerate(ranking, 1):
            scores[number] = scores.get(number, 0) + 1 / (k + rank)
    return sorted(((value, number) for number, value in scores.items()), reverse=True)


class Retriever:
    def __init__(self, store, method='bm25-symbol', models=None):
        self.store = store
        self.history = HistoryStore(store)
        self.method, self.models = method, models
        self.check(method)
        self.cache = OrderedDict()
        self.vectors = OrderedDict()
        self.lock = Lock()

    def check(self, method):
        if method not in METHODS:
            raise ValueError(f'未知的检索方式：{method}')
        if method in VECTOR_METHODS and self.models is None:
            raise ValueError(f'{method} 需要本地嵌入和重排模型')

    def index(self, sid, pid=None):
        key = (sid, self.history.revision(pid) if pid else None)
        with self.lock:
            if key not in self.cache:
                corpus = self.store.corpus(sid)
                if pid:
                    corpus += self.history.corpus(pid)
                terms = [tokenize(passage(item)) for item in corpus]
                self.cache[key] = (corpus, [set(words) for words in terms], BM25Okapi(terms) if any(terms) else None)
                if len(self.cache) > 4:
                    self.cache.popitem(last=False)
            return self.cache[key]

    def embedded(self, sid, corpus):
        """快照里代码和文档片段的单位向量；会话来源不嵌入。按嵌入文本的哈希先查库里的缓存，缺的才算。"""
        key = (sid, self.models.name)
        with self.lock:
            if key in self.vectors:
                return self.vectors[key]
        numbers = [number for number, item in enumerate(corpus) if not item.get('origin')]
        texts = {number: passage(corpus[number]) for number in numbers}
        keys = {number: hashlib.sha256(text.encode('utf-8')).hexdigest() for number, text in texts.items()}
        saved = self.store.embeddings(self.models.name, keys.values())
        missing = {keys[number]: texts[number] for number in numbers if keys[number] not in saved}
        if missing:
            fresh = [(key, np.asarray(vector, dtype=np.float32).tobytes()) for key, vector in zip(missing, self.models.embed(list(missing.values())))]
            self.store.save_embeddings(self.models.name, fresh)
            saved.update(fresh)
        vectors = {}
        for number in numbers:
            vector = np.frombuffer(saved[keys[number]], dtype=np.float32)
            vectors[number] = vector / (np.linalg.norm(vector) or 1.0)
        with self.lock:
            self.vectors[key] = vectors
            if len(self.vectors) > 4:
                self.vectors.popitem(last=False)
        return vectors

    def vector_ranking(self, sid, question, corpus, candidates):
        vectors = self.embedded(sid, corpus)
        query = np.asarray(self.models.embed_query(question), dtype=np.float32)
        query = query / (np.linalg.norm(query) or 1.0)
        return sorted(((float(vectors[number] @ query), number) for number in candidates), reverse=True)[:VECTOR_TOP]

    def rerank(self, question, corpus, candidates):
        """融合后的候选交给重排模型重新排序；会话来源不送模型，留在融合给它的位置。"""
        movable = [number for _, number in candidates if not corpus[number].get('origin')]
        scores = self.models.rerank(question, [passage(corpus[number]) for number in movable]) if movable else []
        reordered = iter(sorted(zip((float(score) for score in scores), movable), reverse=True))
        return [entry if corpus[entry[1]].get('origin') else next(reordered) for entry in candidates]

    def search(self, pid, question, sid=None, top_k=6, method=None):
        method = method or self.method
        self.check(method)
        snapshot = self.store.resolve(pid, sid)
        corpus, tokens, index = self.index(snapshot['id'], pid if snapshot['active'] else None)
        query = tokenize(question)
        scores = index.get_scores(query) if index and query else [0] * len(corpus)
        identifiers = re.findall(r'[a-zA-Z_][a-zA-Z_0-9.]{3,}', question)
        explicit_symbols = [word.lower() for word in identifiers if '_' in word or '.' in word]
        eligible = [number for number, item in enumerate(corpus) if usable(item, tokens[number], explicit_symbols)]
        ranked = []
        for number in eligible:
            overlap = set(query) & tokens[number]
            if not overlap:
                continue
            item = corpus[number]
            exact = sum(word.lower() in (item['path'] + ' ' + item['symbol']).lower() for word in identifiers)
            value = len(overlap) if method == 'term-overlap' else float(scores[number]) + exact * 5
            ranked.append((value, number))
        ordered = sorted(ranked, reverse=True)
        if method != 'term-overlap' and '代码' in question and '文档' in question:
            ordered = comparison_order(ranked, corpus, top_k)
        if method in VECTOR_METHODS:
            semantic = self.vector_ranking(snapshot['id'], question, corpus, [number for number in eligible if not corpus[number].get('origin')])
            ordered = semantic if method == 'vector' else fuse([number for _, number in ordered], [number for _, number in semantic])
            if method == 'hybrid-rerank':
                ordered = self.rerank(question, corpus, ordered[:RERANK_TOP])
        sources, budget, paths = [], 24000, {}
        for score, number in ordered:
            item = corpus[number]
            if paths.get(item['path'], 0) >= 3 or len(item['content']) > budget:
                continue
            sources.append({**item, 'id': f'S{len(sources) + 1}', 'score': round(score, 4)})
            paths[item['path']] = paths.get(item['path'], 0) + 1
            budget -= len(item['content'])
            if len(sources) == top_k:
                break
        warnings = [] if snapshot['active'] else ['当前选择的是历史快照，不能据此断言当前运行状态']
        if any(source.get('origin') for source in sources):
            warnings.append('会话是历史记录；AI提炼仅为候选经验，不能据此断言当前项目或线上状态')
        return {'snapshot': snapshot, 'sources': sources, 'query': question, 'method': method, 'warnings': warnings}
