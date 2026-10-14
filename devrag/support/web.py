import os
from urllib.parse import urlsplit

import httpx
from ddgs import DDGS

from ..indexing import split_file
from ..retrieval import tokenize
from ..store import now

# 首版只读取这些公开文档站的正文，其他搜索结果保留为明确标注的摘要。
DOCUMENT_HOSTS = frozenset({
    'react.dev', 'docs.python.org', 'docs.sqlalchemy.org', 'fastapi.tiangolo.com',
    'docs.langchain.com', 'developer.mozilla.org', 'www.postgresql.org', 'sqlite.org',
    'www.sqlite.org', 'nodejs.org', 'docs.docker.com', 'git-scm.com',
})


def relevant_excerpt(content, query):
    chunks = split_file('web.txt', content[:150_000])
    terms = set(tokenize(query))
    ranked = sorted(chunks, key=lambda item: len(terms & set(tokenize(item['content']))), reverse=True)
    selected = [item['content'][:1800] for item in ranked[:2] if terms & set(tokenize(item['content']))]
    return '\n\n'.join(selected)


class WebSearch:
    def __init__(self, transport=None):
        self.searxng_url = os.getenv('SUPPORT_SEARXNG_URL', '').strip().rstrip('/')
        if self.searxng_url and urlsplit(self.searxng_url).scheme not in ('http', 'https'):
            raise ValueError('SUPPORT_SEARXNG_URL 只接受 http(s) 地址')
        self.transport = transport

    def _searxng(self, query):
        # 自建 SearXNG 的 JSON 接口，调用方式参照 Vane（MIT）的 searchSearxng。
        with httpx.Client(timeout=httpx.Timeout(10, connect=2), transport=self.transport, trust_env=False) as client:
            response = client.get(self.searxng_url + '/search', params={'q': query, 'format': 'json'})
            response.raise_for_status()
            return [{'title': row.get('title'), 'href': row.get('url'), 'body': row.get('content')}
                    for row in response.json().get('results') or []]

    def search(self, query):
        client = DDGS(timeout=8)
        rows = []
        if self.searxng_url:
            try:
                rows = self._searxng(query)
            except Exception:
                rows = []
        rows = rows or client.text(query, max_results=5, safesearch='on')
        sources, seen, pages = [], set(), 0
        for row in rows:
            url = row.get('href', '')
            parsed = urlsplit(url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or url in seen:
                continue
            seen.add(url)
            content, scope = (row.get('body') or '')[:1800], 'snippet'
            if parsed.netloc in DOCUMENT_HOSTS and pages < 2:
                pages += 1
                try:
                    page = client.extract(url, fmt='text_plain')
                    if excerpt := relevant_excerpt(page.get('content') or '', query):
                        content, scope = excerpt, 'page_excerpt'
                except Exception:
                    pass
            if content.strip():
                sources.append({'id': f'W{len(sources) + 1}', 'kind': 'web', 'title': (row.get('title') or parsed.hostname)[:200],
                                'url': url, 'content': content, 'scope': scope, 'retrieved_at': now()})
            if len(sources) == 4:
                break
        return {'status': 'found' if sources else 'empty', 'query': query, 'sources': sources,
                'notice': '' if sources else '本次没有找到可用的公开资料，尚未完成联网查证。'}
