import asyncio
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from filelock import FileLock
from pydantic import Field

from .auth import Auth
from .llm import LLM
from .models import Bundle, Question, Search, Strict
from .retrieval import Retriever
from .store import Store
from .worker import Worker

SAMPLES = [
    {'id': 'flow', 'title': '核心流程', 'description': '从代码和文档定位入口', 'question': '这个项目从请求进入到生成回答的核心流程是什么？请核对代码与文档并给出具体入口。'},
    {'id': 'fallback', 'title': '故障恢复', 'description': '核对已经实现的故障处理', 'question': '调用模型失败时怎样处理？请核对代码与文档中的重试和备用模型机制。'},
    {'id': 'release', 'title': '发布证据', 'description': '区分验证记录与计划', 'question': '项目的部署与验证流程是什么？哪些已有证据，哪些只是计划？'},
    {'id': 'missing', 'title': '知识边界', 'description': '检查库内没有依据的请求', 'question': '这个项目现在的生产系统在线用户数和今天的真实付费转化率是多少？'},
]


class Login(Strict):
    password: str = Field(max_length=300)


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') != 'POST':
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            size += len(message.get('body', b''))
            if size > 3_000_000:
                return await JSONResponse({'detail': '请求超过3MB，请缩小快照范围'}, 413)(scope, receive, send)
            chunks.append(message)
            if not message.get('more_body'):
                break

        async def replay():
            return chunks.pop(0) if chunks else await receive()
        await self.app(scope, replay, send)


def create_app(data_dir=None, model=None, start_worker=True):
    root = Path(data_dir or os.getenv('DATA_DIR', './data'))
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root / 'knowledge.sqlite')
    retriever, auth, llm = Retriever(store), Auth(), model or LLM()
    worker, stop = Worker(store, retriever, llm), threading.Event()

    @asynccontextmanager
    async def lifespan(app):
        lock = FileLock(root / 'worker.lock')
        lock.acquire(timeout=0)
        thread = threading.Thread(target=worker.run, args=(stop,), daemon=True)
        if start_worker:
            thread.start()
        try:
            yield
        finally:
            stop.set()
            if start_worker:
                await asyncio.to_thread(thread.join, 155)
            lock.release()

    app = FastAPI(title='Tracebook', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store, app.state.worker = store, worker
    app.add_middleware(BodyLimit)

    @app.middleware('http')
    async def guard(request, call_next):
        path = request.url.path
        public = {'/api/info', '/api/session', '/api/login'}
        if request.method == 'POST' and request.headers.get('X-Requested-With') != 'Tracebook':
            return JSONResponse({'detail': '请求缺少来源校验'}, 403)
        if path.startswith('/api/') and path not in public and not auth.valid(request.cookies.get('tracebook_session')):
            return JSONResponse({'detail': '请先登录'}, 401)
        response = await call_next(request)
        response.headers.update({'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"})
        if path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(KeyError)
    async def missing(_request, _error):
        return JSONResponse({'detail': '项目、快照或记录不存在'}, 404)

    @app.exception_handler(ValueError)
    async def conflict(_request, error):
        return JSONResponse({'detail': str(error)}, 409)

    @app.get('/health')
    def health():
        with store.db() as db:
            db.execute('SELECT 1').fetchone()
        return {'status': 'ok', 'app': 'devrag', 'release': os.getenv('APP_RELEASE', 'dev')}

    @app.get('/api/info')
    def info():
        return {'name': 'Tracebook', 'release': os.getenv('APP_RELEASE', 'dev'), 'provider_configured': llm.configured, 'primary_model': llm.primary, 'fallback_model': llm.fallback}

    @app.get('/api/session')
    def session(request: Request):
        return {'authenticated': auth.valid(request.cookies.get('tracebook_session'))}

    @app.post('/api/login')
    def login(body: Login, request: Request):
        try:
            token = auth.login(body.password)
        except ValueError as exc:
            raise HTTPException(401, str(exc)) from None
        response = JSONResponse({'authenticated': True})
        response.set_cookie('tracebook_session', token, httponly=True, samesite='strict', secure=request.url.scheme == 'https', max_age=28800)
        return response

    @app.post('/api/logout')
    def logout():
        response = JSONResponse({'authenticated': False})
        response.delete_cookie('tracebook_session')
        return response

    @app.get('/api/projects')
    def projects():
        return store.projects()

    @app.get('/api/projects/{identifier}/snapshots')
    def snapshots(identifier: str):
        store.resolve(identifier)
        return store.snapshots(identifier)

    @app.get('/api/projects/{identifier}/knowledge-map')
    def knowledge_map(identifier: str, snapshot_id: str | None = None):
        return store.knowledge_map(identifier, snapshot_id)

    @app.post('/api/import')
    def ingest(bundle: Bundle):
        return store.ingest(bundle)

    @app.get('/api/samples')
    def samples():
        return SAMPLES

    @app.post('/api/search')
    def search(body: Search):
        return retriever.search(body.project_id, body.question, body.snapshot_id, body.top_k)

    @app.post('/api/questions', status_code=201)
    def create(body: Question, idempotency_key: str = Header(min_length=8, max_length=100)):
        if not llm.configured:
            raise HTTPException(503, '服务器尚未配置模型；仍可使用仅检索')
        return store.create_question(body.project_id, body.question, idempotency_key, body.snapshot_id)

    @app.get('/api/questions')
    def questions(project_id: str):
        return [{key: value for key, value in question.items() if key in {'id', 'project_id', 'question', 'status', 'created_at', 'updated_at', 'snapshot_id', 'commit', 'error', 'stale'}} for question in store.questions(project_id)]

    @app.get('/api/questions/{identifier}')
    def question(identifier: str):
        return store.question(identifier)

    @app.post('/api/questions/{identifier}/retry')
    def retry(identifier: str):
        return store.action(identifier, 'retry')

    @app.post('/api/questions/{identifier}/cancel')
    def cancel(identifier: str):
        return store.action(identifier, 'cancel')

    @app.get('/api/sources/{snapshot_id}')
    def source(snapshot_id: str, path: str):
        return store.source(snapshot_id, path)

    @app.get('/api/questions/{identifier}/export')
    def export(identifier: str):
        question = store.question(identifier)
        if question['status'] != 'completed':
            raise HTTPException(409, '尚无可导出结果')
        result = question['result']
        lines = ['# ' + question['question'], '', '提交：' + question['commit'], '结论状态：' + result['status'], '', result['summary']]
        for claim in result['claims']:
            lines.extend(['', '- ' + claim['text']])
            for evidence in claim['evidence']:
                source = next(item for item in question['sources'] if item['id'] == evidence['source_id'])
                lines.extend(['', f'  [{source["id"]}] {source["path"]}:{source["start_line"]}–{source["end_line"]}', '', '  > ' + evidence['quote'].replace('\n', '\n  > ')])
        if result['gaps']:
            lines.extend(['', '## 资料缺口', '', *['- ' + gap for gap in result['gaps']]])
        return Response('\n'.join(lines), media_type='text/markdown; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="tracebook-{identifier}.md"'})

    frontend = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
    if (frontend / 'assets').exists():
        app.mount('/assets', StaticFiles(directory=frontend / 'assets'), name='assets')

    @app.get('/')
    def index():
        if not (frontend / 'index.html').exists():
            raise HTTPException(503, '前端尚未构建')
        return FileResponse(frontend / 'index.html')

    @app.get('/favicon.svg')
    def icon():
        path = frontend / 'favicon.svg'
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path)

    return app
