import logging
import re
from collections import OrderedDict
from threading import Lock

import jieba
from rank_bm25 import BM25Okapi

jieba.setLogLevel(logging.ERROR)
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


class Retriever:
    def __init__(self, store):
        self.store = store
        self.cache = OrderedDict()
        self.lock = Lock()

    def index(self, sid):
        with self.lock:
            if sid not in self.cache:
                corpus = self.store.corpus(sid)
                terms = [tokenize(item['path'] + ' ' + item['symbol'] + '\n' + item['content']) for item in corpus]
                self.cache[sid] = (corpus, [set(words) for words in terms], BM25Okapi(terms) if any(terms) else None)
                if len(self.cache) > 4:
                    self.cache.popitem(last=False)
            return self.cache[sid]

    def search(self, pid, question, sid=None, top_k=6, baseline=False):
        snapshot = self.store.resolve(pid, sid)
        corpus, tokens, index = self.index(snapshot['id'])
        query = tokenize(question)
        scores = index.get_scores(query) if index and query else [0] * len(corpus)
        identifiers = re.findall(r'[a-zA-Z_][a-zA-Z_0-9.]{3,}', question)
        explicit_symbols = [word.lower() for word in identifiers if '_' in word or '.' in word]
        ranked = []
        for number, score in enumerate(scores):
            overlap = set(query) & tokens[number]
            if not overlap:
                continue
            item = corpus[number]
            # 明确点名的符号已删除时，不能用拆开的通用单词制造命中。
            if explicit_symbols and not any(word in tokens[number] for word in explicit_symbols):
                continue
            exact = sum(word.lower() in (item['path'] + ' ' + item['symbol']).lower() for word in identifiers)
            value = len(overlap) if baseline else float(score) + exact * 5
            ranked.append((value, number))
        sources, budget, paths = [], 24000, {}
        for score, number in sorted(ranked, reverse=True):
            item = corpus[number]
            if paths.get(item['path'], 0) >= 3 or len(item['content']) > budget:
                continue
            sources.append({**item, 'id': f'S{len(sources) + 1}', 'score': round(score, 4)})
            paths[item['path']] = paths.get(item['path'], 0) + 1
            budget -= len(item['content'])
            if len(sources) == top_k:
                break
        warnings = [] if snapshot['active'] else ['当前选择的是历史快照，不能据此断言当前运行状态']
        return {'snapshot': snapshot, 'sources': sources, 'query': question, 'method': 'term-overlap' if baseline else 'bm25-symbol', 'warnings': warnings}
