import httpx
import pytest

from devrag.support import web
from devrag.support.web import WebSearch


@pytest.fixture
def ddgs(monkeypatch):
    queries = []

    class FakeDDGS:
        def __init__(self, timeout):
            pass

        def text(self, query, **options):
            queries.append(query)
            return [{'title': 'DDGS 结果', 'href': 'https://example.org/ddgs', 'body': 'DDGS 摘要'}]

    monkeypatch.setattr(web, 'DDGS', FakeDDGS)
    return queries


def refused(request):
    raise httpx.ConnectError('connection refused', request=request)


def test_searxng_results_are_used_when_configured(monkeypatch, ddgs):
    monkeypatch.setenv('SUPPORT_SEARXNG_URL', 'http://127.0.0.1:8888/')
    requests = []

    def searxng(request):
        requests.append(request)
        return httpx.Response(200, json={'results': [
            {'title': 'Connection Pooling', 'url': 'https://example.org/pooling', 'content': 'Pools reuse connections.'}]})

    found = WebSearch(transport=httpx.MockTransport(searxng)).search('SQLAlchemy pooling')
    assert str(requests[0].url.copy_with(query=None)) == 'http://127.0.0.1:8888/search'
    assert requests[0].url.params['q'] == 'SQLAlchemy pooling'
    assert requests[0].url.params['format'] == 'json'
    assert ddgs == []
    assert found['status'] == 'found'
    assert found['sources'][0] == {**found['sources'][0], 'url': 'https://example.org/pooling',
                                   'content': 'Pools reuse connections.', 'scope': 'snippet', 'id': 'W1'}


@pytest.mark.parametrize('handler', [
    refused,
    lambda request: httpx.Response(502),
    lambda request: httpx.Response(200, json={'results': []}),
    lambda request: httpx.Response(200, text='<html>not json</html>'),
], ids=['refused', 'http-error', 'empty', 'not-json'])
def test_unavailable_or_empty_searxng_falls_back_to_ddgs(monkeypatch, ddgs, handler):
    monkeypatch.setenv('SUPPORT_SEARXNG_URL', 'http://127.0.0.1:8888')
    found = WebSearch(transport=httpx.MockTransport(handler)).search('React useEffect cleanup')
    assert ddgs == ['React useEffect cleanup']
    assert found['status'] == 'found'
    assert found['sources'][0]['url'] == 'https://example.org/ddgs'


def test_without_searxng_only_ddgs_is_used(monkeypatch, ddgs):
    monkeypatch.delenv('SUPPORT_SEARXNG_URL', raising=False)
    found = WebSearch(transport=httpx.MockTransport(refused)).search('FastAPI lifespan')
    assert ddgs == ['FastAPI lifespan']
    assert found['status'] == 'found'


def test_searxng_address_must_be_http(monkeypatch):
    monkeypatch.setenv('SUPPORT_SEARXNG_URL', 'file:///etc/searxng')
    with pytest.raises(ValueError, match='SUPPORT_SEARXNG_URL'):
        WebSearch()
