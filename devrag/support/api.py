from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import Response

from .schemas import LOG_LIMIT, ChatMessage, SessionConfirm, SessionStart, SessionSupplement


def support_router(service):
    router = APIRouter(prefix='/api/support')

    @router.get('/projects')
    def projects():
        return {'projects': service.retriever.projects(), 'model_configured': bool(getattr(service.model, 'configured', True)),
                'log_limit': LOG_LIMIT, 'analysis_timeout_ms': int(getattr(service.model, 'timeout', 120) * 2000 + 65000)}

    @router.get('/conversations')
    def conversations():
        return service.store.conversations()

    @router.get('/conversations/{conversation_id}')
    def conversation(conversation_id: UUID):
        return service.chat.view(str(conversation_id))

    @router.post('/conversations/{conversation_id}/messages')
    def message(conversation_id: UUID, body: ChatMessage):
        return service.chat.send(str(conversation_id), body.model_dump(mode='json'))

    @router.post('/conversations/{conversation_id}/retry')
    def retry_conversation(conversation_id: UUID):
        return service.chat.retry(str(conversation_id))

    @router.post('/sessions', status_code=201)
    def start(body: SessionStart):
        return service.start(**body.model_dump())

    @router.get('/sessions')
    def sessions():
        return service.sessions()

    @router.get('/sessions/{thread_id}')
    def session(thread_id: str):
        return service.view(thread_id)

    @router.post('/sessions/{thread_id}/supplement')
    def supplement(thread_id: str, body: SessionSupplement):
        return service.supplement(thread_id, body.model_dump(exclude_none=True))

    @router.post('/sessions/{thread_id}/confirm')
    def confirm(thread_id: str, body: SessionConfirm):
        return service.confirm(thread_id, **body.model_dump())

    @router.post('/sessions/{thread_id}/retry')
    def retry(thread_id: str):
        return service.retry(thread_id)

    @router.get('/sessions/{thread_id}/export')
    def export(thread_id: str):
        return Response(service.export(thread_id), media_type='text/markdown; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="support-{thread_id}.md"'})

    @router.get('/cases')
    def cases():
        return service.cases()

    return router
