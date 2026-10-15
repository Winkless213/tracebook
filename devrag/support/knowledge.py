import hashlib
import re

from rank_bm25 import BM25Okapi

from ..indexing import split_file
from ..knowledge_status import DOCUMENT_STAGES
from ..retrieval import Retriever, tokenize
from ..store import digest

DOCUMENT_LIMIT = 1_000_000


def published_id(name):
    return 'published:' + digest(name)[:32]


class PublishedSnapshot(Retriever):
    # 复用现有排序与引用预算，发布文件只在本次请求内建索引，不复制或改写知识库。
    def __init__(self, snapshot, corpus):
        self.store, self.snapshot, self.corpus = self, snapshot, corpus

    def resolve(self, pid, sid=None):
        return self.snapshot

    def index(self, sid, pid=None):
        terms = [tokenize(item['path'] + ' ' + item['symbol'] + '\n' + item['content']) for item in self.corpus]
        return self.corpus, [set(words) for words in terms], BM25Okapi(terms) if any(terms) else None


class SupportKnowledge:
    def __init__(self, existing, files):
        self.existing, self.files, self.store = existing, files, self

    def _entry(self, pid):
        entry = next((item for item in self.files.catalog()['projects'] if published_id(item['name']) == pid), None)
        if entry is None:
            raise KeyError(pid)
        return entry

    def _documents(self, entry):
        paths = []
        for name in DOCUMENT_STAGES:
            try:
                paths.append((name, self.files.document(entry['name'], name)))
            except (KeyError, OSError):
                continue
        return paths

    def projects(self):
        published = [{'id': published_id(item['name']), 'name': item['name'], 'branch': item.get('branch', ''),
                      'source': 'published', 'knowledge_available': bool(self._documents(item))}
                     for item in self.files.catalog()['projects']]
        existing = [{'id': item['id'], 'name': item['name'], 'branch': item['branch'], 'source': 'snapshot',
                     'knowledge_available': bool(item['chunk_count'])} for item in self.existing.store.projects()]
        return published + existing

    def resolve(self, pid, sid=None):
        return self._entry(pid) if pid.startswith('published:') else self.existing.store.resolve(pid, sid)

    def search(self, pid, question, top_k=4):
        if not pid.startswith('published:'):
            return self.existing.search(pid, question, top_k=top_k)
        entry = self._entry(pid)
        corpus, fingerprints, warnings = [], [], ['引用来自已发布的项目知识归纳，不能据此断言当前代码或运行状态。']
        commit = entry.get('commit') or ''
        if not re.fullmatch(r'[a-f0-9]{40,64}', commit):
            commit = ''
        for name, path in self._documents(entry):
            try:
                with path.open('rb') as handle:
                    raw = handle.read(DOCUMENT_LIMIT + 1)
                if len(raw) > DOCUMENT_LIMIT:
                    raise ValueError('Published document exceeds retrieval budget')
                checksum = hashlib.sha256(raw).hexdigest()
                chunks = split_file(name, raw.decode('utf-8'))
                corpus.extend({**item, 'commit': commit, 'sha256': checksum} for item in chunks)
                fingerprints.append((name, checksum))
            except (OSError, UnicodeError, ValueError):
                warnings.append(f'{name} 暂无法读取或超过检索限额，本次未使用该文件。')
        if not corpus:
            warnings.append('该项目暂没有可用的已发布知识，本次仅依据用户材料和已确认案例分析。')
        if entry.get('sync_status') and entry['sync_status'] != 'ok':
            warnings.append('知识同步尚未完成，本次使用上次保留的已发布资料。')
        snapshot = {'id': digest([pid, commit, fingerprints]), 'project_id': pid, 'active': True}
        result = PublishedSnapshot(snapshot, corpus).search(pid, question, top_k=top_k)
        if not result['sources']:
            # 意图/泛问经停词过滤后查询词为空，词项重叠取不到任何片段；回退补入项目概述真实片段。
            # 首个块常为 metadata 引言行，需累积开头若干块才有实质概述。
            overview, budget = [], 2400
            for item in corpus:
                if item['path'] != 'CODEBASE.md':
                    continue
                if all(line.startswith('#') for line in item['content'].splitlines() if line.strip()):
                    continue
                overview.append(item)
                budget -= len(item['content'])
                if budget <= 0:
                    break
            if overview:
                result['sources'] = [{**overview[0], 'id': 'S1', 'score': 0.0,
                                      'content': '\n\n'.join(item['content'] for item in overview)}]
                warnings.append('本次未命中具体章节，已回退到项目概述片段。')
        result['warnings'].extend(warnings)
        for item in result['sources']:
            item.update(origin='published', published_project=entry['name'])
        return result
