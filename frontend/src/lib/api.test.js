import assert from 'node:assert/strict';
import test from 'node:test';
import { api, errorMessage, ApiError } from './api.js';

test('所有写请求带同源凭据、Tracebook 标识和幂等键', async () => {
  const response = await api('/questions', {
    method: 'POST', body: { question: '项目如何恢复失败任务？' }, idempotencyKey: 'intent-1',
    fetcher: async (path, options) => {
      assert.equal(path, '/api/questions');
      assert.equal(options.credentials, 'same-origin');
      assert.equal(options.headers['X-Requested-With'], 'Tracebook');
      assert.equal(options.headers['Idempotency-Key'], 'intent-1');
      assert.deepEqual(JSON.parse(options.body), { question: '项目如何恢复失败任务？' });
      return new Response(JSON.stringify({ id: 'q1', status: 'queued' }), { headers: { 'content-type': 'application/json' } });
    },
  });
  assert.equal(response.status, 'queued');
});

test('FastAPI 多字段校验错误能显示具体输入位置', () => {
  assert.equal(errorMessage({ detail: [{ loc: ['body', 'files', 0, 'path'], type: 'missing', msg: 'Field required' }] }), '文件 1 路径：请填写此项');
});

test('网关 HTML 不能误当成功响应', async () => {
  await assert.rejects(api('/projects', { fetcher: async () => new Response('<html>proxy</html>', { headers: { 'content-type': 'text/html' } }) }), /无法读取/);
});

test('401 保留状态供界面触发重新登录', async () => {
  await assert.rejects(api('/projects', { fetcher: async () => new Response('', { status: 401 }) }), (error) => error instanceof ApiError && error.status === 401);
});

test('网络错误提供保留当前内容的恢复提示', async () => {
  await assert.rejects(api('/projects', { fetcher: async () => { throw new TypeError('Failed to fetch'); } }), (error) => error.status === 0 && error.message.includes('保留'));
});

test('超时会终止请求并允许重新连接', async () => {
  const fetcher = (_, { signal }) => new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))));
  await assert.rejects(api('/questions/q1', { fetcher, timeoutMs: 5 }), (error) => error.status === 0 && error.message.includes('超时'));
});

test('主动取消的请求不会被改写为网络错误', async () => {
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(api('/projects', {
    signal: controller.signal,
    fetcher: async (_, { signal }) => { signal.throwIfAborted(); },
  }), (error) => error.name === 'AbortError');
});
