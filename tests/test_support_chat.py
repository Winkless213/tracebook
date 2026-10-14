from uuid import uuid4

import pytest

from devrag.support import SupportService
from devrag.support.chat import SupportChat


class ReplyModel:
    def __init__(self):
        self.received = []
        self.fail = False

    def reply(self, payload, record, tool=None, tool_executor=None):
        self.received.append(payload)
        if self.fail:
            raise RuntimeError('离线模拟暂不可用')
        return {'reply': '连接池就像共享工具箱：每次使用后归还，下次就不用重新准备。',
                'source_ids': [item['id'] for item in payload['evidence']][:2], 'needs_diagnosis': False}


class ToolReplyModel:
    """模拟模型自主联网：拿到工具就自拟 query 调用执行器，再综合出引用 W 来源的回答。"""

    def __init__(self, query='SQLAlchemy connection pooling'):
        self.query = query
        self.received = []
        self.got_tool = []

    def reply(self, payload, record, tool=None, tool_executor=None):
        self.received.append(payload)
        self.got_tool.append(bool(tool and tool_executor))
        if tool and tool_executor:
            result = tool_executor({'query': self.query})
            ids = [source['id'] for source in result.get('sources', [])]
            return {'reply': '连接池复用数据库连接，详见来源。' if ids else '这次没有找到可用的公开资料。',
                    'source_ids': ids[:2], 'needs_diagnosis': False}
        return {'reply': '连接池就像共享工具箱。', 'source_ids': [], 'needs_diagnosis': False}


class SearchFixture:
    def __init__(self):
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        return {'status': 'found', 'query': query, 'notice': '', 'sources': [
            {'id': 'W1', 'kind': 'web', 'title': 'Connection Pooling',
             'url': 'https://docs.sqlalchemy.org/en/20/core/pooling.html',
             'content': 'Connection pooling reuses connections.', 'scope': 'snippet'}]}


def message(text='连接池是什么？能用白话解释吗？', **overrides):
    return {'request_id': str(uuid4()), 'message': text, **overrides}


def test_consultation_answers_without_version_or_log_and_survives_restart(tmp_path):
    model = ReplyModel()
    service = SupportService(tmp_path, model)
    cid = str(uuid4())
    view = service.chat.send(cid, message())
    assert view['status'] == 'ready'
    assert view['turns'][0]['response']['reply'].startswith('连接池')
    assert view['turns'][0]['status'] == 'completed'
    assert service.sessions() == []
    assert service.cases() == []
    service.close()
    restored = SupportService(tmp_path, model)
    view = restored.chat.send(cid, message('所以可以重复利用，对吗？'))
    assert len(view['turns']) == 2
    assert model.received[-1]['history'][0]['message'] == '连接池是什么？能用白话解释吗？'
    assert restored.chat.view(cid)['turns'] == view['turns']
    restored.close()


def test_duplicate_submission_returns_saved_answer_without_second_model_call(tmp_path):
    service = SupportService(tmp_path, ReplyModel())
    cid, payload = str(uuid4()), message()
    first = service.chat.send(cid, payload)
    second = service.chat.send(cid, payload)
    assert second['turns'] == first['turns']
    assert len(service.model.received) == 1
    with pytest.raises(ValueError):
        service.chat.send(cid, {**payload, 'message': '用同一个编号提交不同的问题'})
    service.close()


def test_web_disabled_does_not_attach_tool_even_with_hint(tmp_path):
    model, search = ToolReplyModel(), SearchFixture()
    service = SupportService(tmp_path, model)
    chat = SupportChat(service.root, service.store, model, None, search)
    view = chat.send(str(uuid4()), message('连接池是什么？', search_query='SQLAlchemy connection pooling'))
    assert model.got_tool == [False]
    assert search.queries == []
    assert view['turns'][-1]['response']['search']['status'] == 'not_requested'
    service.close()


def test_model_autonomously_searches_when_web_allowed_and_cites_sources(tmp_path):
    model, search = ToolReplyModel(query='SQLAlchemy connection pooling'), SearchFixture()
    service = SupportService(tmp_path, model)
    chat = SupportChat(service.root, service.store, model, None, search)
    view = chat.send(str(uuid4()), message('连接池是什么？', search_query='pooling', allow_web=True))
    assert model.got_tool == [True]
    # 搜索词由模型自拟；用户提示词只作为 payload 线索透传
    assert search.queries == ['SQLAlchemy connection pooling']
    assert model.received[0]['search_hint'] == 'pooling'
    answer = view['turns'][-1]['response']
    assert answer['search']['status'] == 'found'
    assert answer['search']['query'] == 'SQLAlchemy connection pooling'
    assert answer['sources'][0]['url'] == 'https://docs.sqlalchemy.org/en/20/core/pooling.html'
    assert answer['sources'][0]['scope'] == 'snippet'
    assert answer['sources'][0]['id'] == 'W1'
    service.close()


def test_failed_answer_can_retry_without_duplicate_message(tmp_path):
    model = ReplyModel()
    model.fail = True
    service = SupportService(tmp_path, model)
    cid, payload = str(uuid4()), message()
    failed = service.chat.send(cid, payload)
    assert failed['status'] == 'failed'
    assert failed['turns'][0]['response'] is None
    model.fail = False
    fixed = service.chat.retry(cid)
    assert fixed['status'] == 'ready'
    assert len(fixed['turns']) == 1
    assert len(model.received) == 2
    service.close()


def test_search_failure_is_explicit_and_does_not_fabricate_sources(tmp_path):
    class UnavailableSearch:
        def search(self, query):
            raise TimeoutError('offline fixture')

    model = ToolReplyModel()
    service = SupportService(tmp_path, model)
    chat = SupportChat(service.root, service.store, model, None, UnavailableSearch())
    view = chat.send(str(uuid4()), message('React useEffect 怎么清理？', allow_web=True))
    reply = view['turns'][0]['response']
    assert reply['search']['status'] == 'unavailable'
    assert '未完成' in reply['search']['notice']
    assert reply['sources'] == []
    service.close()


def test_project_knowledge_is_reused_with_original_provenance(tmp_path):
    class Knowledge:
        store = None

        def resolve(self, pid):
            assert pid == 'project1'

        def search(self, pid, question, top_k):
            return {'snapshot': {'id': 'snap1'}, 'warnings': [], 'sources': [
                {'id': 'S1', 'kind': 'document', 'path': 'README.md', 'start_line': 1,
                 'content': '项目连接池复用数据库连接。', 'sha256': 'a1' * 32}]}

    knowledge = Knowledge()
    knowledge.store = knowledge
    service = SupportService(tmp_path, ReplyModel(), knowledge)
    view = service.chat.send(str(uuid4()), message(project_id='project1'))
    source = view['turns'][0]['response']['sources'][0]
    assert source['kind'] == 'knowledge'
    assert source['snapshot_id'] == 'snap1'
    assert source['sha256'] == 'a1' * 32
    service.close()
