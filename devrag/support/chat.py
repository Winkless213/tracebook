from contextlib import contextmanager
from uuid import UUID

from filelock import FileLock, Timeout

from ..store import now
from .model import WEB_SEARCH_TOOL
from .redact import mask_data, mask_text
from .schemas import ChatMessage, ChatReply
from .web import WebSearch


class SupportChat:
    def __init__(self, root, store, model, retriever, search=None):
        self.root, self.store, self.model, self.retriever = root, store, model, retriever
        self.search = search or WebSearch()

    def _file_lock(self, conversation_id):
        return FileLock(self.root / 'locks' / f'chat-{UUID(str(conversation_id))}.lock')

    @contextmanager
    def _lock(self, conversation_id):
        lock = self._file_lock(conversation_id)
        try:
            lock.acquire(timeout=0)
        except Timeout:
            raise ValueError('这段对话正在回复，请稍后检查最新状态') from None
        try:
            yield
        finally:
            lock.release()

    def view(self, conversation_id):
        conversation = self.store.conversation(str(UUID(str(conversation_id))))
        last = conversation['turns'][-1]
        status = 'ready' if last['status'] == 'completed' else last['status']
        if status == 'processing':
            lock = self._file_lock(conversation_id)
            try:
                lock.acquire(timeout=0)
            except Timeout:
                pass
            else:
                lock.release()
                status = 'interrupted'
        return {**conversation, 'status': status, 'error': last.get('error')}

    def send(self, conversation_id, payload):
        conversation_id = str(UUID(str(conversation_id)))
        request = ChatMessage.model_validate(payload)
        safe, _ = mask_data(request.model_dump(mode='json'))
        if safe['search_query'] != request.search_query:
            raise ValueError('搜索词请只填写公开技术名词，不包含凭据或私人信息')
        with self._lock(conversation_id):
            try:
                conversation = self.store.conversation(conversation_id)
            except KeyError:
                if request.project_id and self.retriever:
                    self.retriever.store.resolve(request.project_id)
                conversation = {'id': conversation_id, 'project_id': request.project_id, 'title': safe['message'][:100],
                                'created_at': now(), 'turns': []}
            if request.project_id != conversation['project_id']:
                raise ValueError('对话关联项目不能中途更换，请新建对话')
            duplicate = next((turn for turn in conversation['turns'] if turn['request_id'] == safe['request_id']), None)
            if duplicate:
                if any(duplicate[key] != safe[key] for key in ('message', 'search_query')):
                    raise ValueError('同一请求编号不能用于不同内容')
                return self.view(conversation_id)
            if conversation['turns'] and conversation['turns'][-1]['status'] != 'completed':
                raise ValueError('上一条回复尚未完成，请先检查状态或重试')
            if len(conversation['turns']) >= 40:
                raise ValueError('这段对话已满 40 轮，请新建对话继续')
            conversation['turns'].append({**safe, 'created_at': now(), 'status': 'processing', 'response': None, 'error': None})
            return self._answer(conversation)

    def retry(self, conversation_id):
        with self._lock(conversation_id):
            conversation = self.store.conversation(conversation_id)
            if conversation['turns'][-1]['status'] == 'completed':
                return self.view(conversation_id)
            return self._answer(conversation)

    def _knowledge(self, conversation):
        evidence, warnings = [], []
        if conversation['project_id'] and self.retriever:
            question = '\n'.join(item['message'] for item in conversation['turns'][-3:])[-5000:]
            found = self.retriever.search(conversation['project_id'], question, top_k=4)
            warnings.extend(found['warnings'])
            evidence.extend({**source, 'id': f'K{number}', 'kind': 'knowledge', 'source_kind': source['kind'],
                             'snapshot_id': found['snapshot']['id']} for number, source in enumerate(found['sources'], 1))
        return mask_data(evidence, preserve_provenance=True)[0], warnings

    @staticmethod
    def _search_state(allow_web, web_sources, web_queries, failed):
        if not allow_web:
            return {'status': 'not_requested', 'query': '', 'notice': ''}
        query = '、'.join(web_queries)
        if web_sources:
            return {'status': 'found', 'query': query, 'notice': ''}
        if failed:
            return {'status': 'unavailable', 'query': query,
                    'notice': '本次联网查证未完成，暂按已有知识回答，公开资料可稍后再查。'}
        if web_queries:
            return {'status': 'empty', 'query': query, 'notice': '本次没有找到可用的公开资料，尚未完成联网查证。'}
        return {'status': 'not_requested', 'query': '', 'notice': ''}

    def _answer(self, conversation):
        turn = conversation['turns'][-1]
        turn.update(status='processing', error=None, response=None)
        self.store.save_conversation(conversation)

        def record(model, attempt, status, latency, usage, error):
            if attempt is None:
                return self.store.begin_attempt(conversation['id'], mask_text(model)[0])
            self.store.end_attempt(attempt, status, latency, usage, mask_text(error)[0] if error else None)

        # 模型自主联网：执行器按 query 去重、分配全局 W 编号，检索失败降级而不抛出。
        allow_web = bool(turn.get('allow_web'))
        web_sources, web_queries, cache = [], [], {}
        failed = False

        def tool_executor(args):
            nonlocal failed
            query = str(args.get('query') or '').strip()[:160]
            if not query:
                return {'status': 'empty', 'sources': []}
            if query in cache:
                return cache[query]
            web_queries.append(query)
            try:
                result = self.search.search(query)
            except Exception:
                failed = True
                cache[query] = {'status': 'unavailable', 'sources': []}
                return cache[query]
            given = []
            for source in mask_data(result['sources'], preserve_provenance=True)[0]:
                source = {**source, 'id': f'W{len(web_sources) + 1}'}
                web_sources.append(source)
                given.append({key: source[key] for key in ('id', 'title', 'url', 'content', 'scope') if key in source})
            cache[query] = {'status': result['status'], 'sources': given}
            return cache[query]

        try:
            knowledge, warnings = self._knowledge(conversation)
            history = [{'message': item['message'], 'reply': item['response']['reply']}
                       for item in conversation['turns'][-7:-1] if item['response']]
            payload = {'message': turn['message'], 'project_id': conversation['project_id'], 'history': history,
                       'evidence': knowledge, 'search_hint': turn.get('search_query', ''), 'warnings': warnings}
            result = ChatReply.model_validate(self.model.reply(
                payload, record, WEB_SEARCH_TOOL if allow_web else None, tool_executor if allow_web else None))
            evidence = knowledge + web_sources
            sources = {item['id']: item for item in evidence}
            if any(identifier not in sources for identifier in result.source_ids):
                raise ValueError('回答引用了不存在的资料')
            turn['response'] = mask_data({'reply': result.reply, 'needs_diagnosis': result.needs_diagnosis,
                                         'sources': [sources[key] for key in dict.fromkeys(result.source_ids)],
                                         'search': self._search_state(allow_web, web_sources, web_queries, failed),
                                         'warnings': warnings}, preserve_provenance=True)[0]
            turn['status'] = 'completed'
        except Exception:
            turn.update(status='failed', error='这次回复未完成，问题已保存，可以重试。')
        self.store.save_conversation(conversation)
        return self.view(conversation['id'])
