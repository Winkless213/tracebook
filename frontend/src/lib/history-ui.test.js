import assert from 'node:assert/strict';
import { after, afterEach, test } from 'node:test';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { Window } from 'happy-dom';
import { createServer } from 'vite';

const server = await createServer({ server: { middlewareMode: true }, optimizeDeps: { noDiscovery: true, include: [] }, appType: 'custom', logLevel: 'error' });
const { default: HistoryKnowledge } = await server.ssrLoadModule('/src/components/HistoryKnowledge.jsx');
const { default: Evidence } = await server.ssrLoadModule('/src/components/Evidence.jsx');
const { default: App } = await server.ssrLoadModule('/src/App.jsx');
const browser = new Window();
globalThis.window = browser;
globalThis.document = browser.document;
Object.defineProperty(globalThis, 'navigator', { value: browser.navigator, configurable: true });
Object.defineProperty(globalThis, 'localStorage', { value: browser.localStorage, configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.requestAnimationFrame = (callback) => { callback(); return 0; };
let root;
afterEach(async () => { if (root) await act(() => root.unmount()); root = null; document.body.innerHTML = ''; browser.localStorage.clear(); });
after(async () => { await server.close(); await browser.happyDOM.close(); });

const session = { id: 's1', harness: 'codex', native_id: 'native-1', title: '端口冲突排查', record_count: 51, updated_at: '2026-10-09T03:00:00Z', pending_count: 31 };
const record = (id, text = `脱敏正文 ${id}`) => ({ id, session_id: 's1', role: 'assistant', text, time: '2025-01-02T03:04:00Z', provenance: { file: 'session.jsonl', line: 29, part: 0, record_sha256: 'record-hash', source_sha256: 'source-hash' } });
const detail = (overrides = {}) => ({ session, records: [record('r1')], total: 51, offset: 0, jobs: [], ...overrides });
const job = (status, overrides = {}) => ({ id: 'j1', session_id: 's1', status, record_ids: ['r1', 'r2'], result: null, error: null, attempts: [], usage: {}, created_at: '2026-10-09T03:00:00Z', updated_at: '2026-10-09T03:00:00Z', ...overrides });
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } });
const settle = () => act(async () => { await new Promise((resolve) => setImmediate(resolve)); });
async function mount(Component, props) {
  const container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(React.createElement(Component, props)));
  await settle();
}
const findButton = (label) => [...document.querySelectorAll('button')].find((button) => (button.getAttribute('aria-label') || button.textContent).trim() === label);
async function click(label) {
  const button = findButton(label);
  assert.ok(button, `找不到按钮：${label}`);
  assert.equal(button.disabled, false, `按钮不可用：${label}`);
  await act(async () => button.dispatchEvent(new browser.MouseEvent('click', { bubbles: true })));
  await settle();
}
const props = { project: { id: 'p1', name: 'Tracebook' }, onUnauthorized: () => {}, online: true };

test('诊断入口可以进入会话知识并返回项目问答', async (t) => {
  browser.happyDOM.setURL('http://localhost/?view=diagnostics');
  t.after(() => browser.happyDOM.setURL('http://localhost/'));
  t.mock.method(globalThis, 'fetch', async (url) => {
    if (url === '/api/session') return json({ authenticated: true });
    if (url === '/api/info') return json({ provider_configured: false });
    if (url === '/api/projects') return json([{ ...props.project, branch: 'main', commit: 'abc12345', file_count: 1, chunk_count: 1 }]);
    if (url.includes('/snapshots')) return json([{ id: 'snap1', active: true, commit: 'abc12345' }]);
    return json([]);
  });
  await mount(App, {});
  assert.ok(document.getElementById('question-input'));
  await click('会话知识');
  assert.ok(document.getElementById('history-knowledge'));
  assert.match(document.body.textContent, /尚未同步会话/);
  await click('项目问答');
  assert.ok(document.getElementById('question-input'));
  assert.equal(document.getElementById('history-knowledge'), null);
});

test('会话列表覆盖加载、空、错误和手动恢复，始终带项目过滤', async (t) => {
  let release;
  let response = new Promise((resolve) => { release = resolve; });
  t.mock.method(globalThis, 'fetch', async (url) => { assert.equal(url, '/api/history/sessions?project_id=p1'); return response; });
  await mount(HistoryKnowledge, props);
  assert.match(document.body.textContent, /读取会话/);
  await act(async () => release(json({ detail: '连接暂时不可用' }, 503)));
  await settle();
  assert.match(document.body.textContent, /连接暂时不可用/);
  assert.doesNotMatch(document.body.textContent, /尚未同步会话/);
  response = json([]);
  await click('刷新会话');
  assert.match(document.body.textContent, /尚未同步会话/);
  assert.match(document.body.textContent, /本机连接器/);
  assert.doesNotMatch(document.body.textContent, /模型 Key|API Key/);
});

test('原文分页使用 offset/limit，并显示 session.pending_count 和真实来源', async (t) => {
  const urls = [];
  t.mock.method(globalThis, 'fetch', async (url) => {
    urls.push(url);
    if (url.includes('?project_id=')) return json([session]);
    return json(url.includes('offset=50') ? detail({ records: [record('r51')], offset: 50 }) : detail());
  });
  await mount(HistoryKnowledge, props);
  assert.match(document.body.textContent, /31 段待提炼/);
  assert.match(document.body.textContent, /脱敏正文 r1/);
  assert.match(document.body.textContent, /session.jsonl · L29 · 分段 0/);
  await click('下一页');
  assert.ok(urls.includes('/api/history/sessions/s1?offset=50&limit=50'));
  assert.match(document.body.textContent, /脱敏正文 r51/);
  assert.doesNotMatch(document.body.textContent, /脱敏正文 r1/);
  assert.equal(findButton('下一页').disabled, true);
  await click('上一页');
  assert.match(document.body.textContent, /脱敏正文 r1/);
});

test('切换会话取消旧请求，迟到的原文不能覆盖新会话', async (t) => {
  let release;
  let oldSignal;
  const second = { ...session, id: 's2', title: '另一条会话' };
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url.includes('?project_id=')) return json([session, second]);
    if (url.includes('/sessions/s1?')) { oldSignal = options.signal; return new Promise((resolve) => { release = resolve; }); }
    return json(detail({ session: second, records: [{ ...record('r2', '新会话的原文'), session_id: 's2' }] }));
  });
  await mount(HistoryKnowledge, props);
  await click('打开会话：另一条会话');
  assert.equal(oldSignal.aborted, true);
  assert.match(document.body.textContent, /新会话的原文/);
  await act(async () => release(json(detail({ records: [record('r1', '迟到的旧会话原文')] }))));
  await settle();
  assert.doesNotMatch(document.body.textContent, /迟到的旧会话原文/);
});

test('提炼和 retry/cancel 使用直接返回的 Job，等待响应时不假造排队状态', async (t) => {
  let currentJob = null;
  let release;
  const posts = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url.includes('?project_id=')) return json([session]);
    if (options.method === 'POST') {
      posts.push(url);
      if (url.endsWith('/distill')) return new Promise((resolve) => { release = () => { currentJob = job('failed', { error: '提炼调用超时' }); resolve(json(currentJob)); }; });
      currentJob = job(url.endsWith('/retry') ? 'generating' : 'cancelled');
      return json(currentJob);
    }
    return json(detail({ jobs: currentJob ? [currentJob] : [] }));
  });
  await mount(HistoryKnowledge, props);
  await click('提炼候选经验');
  assert.match(document.body.textContent, /提交中/);
  assert.doesNotMatch(document.body.textContent, /排队中/);
  await act(async () => release());
  await settle();
  assert.match(document.body.textContent, /提炼失败/);
  assert.match(document.body.textContent, /提炼调用超时/);
  await click('续跑任务');
  assert.match(document.body.textContent, /提炼中/);
  assert.equal(findButton('提炼候选经验').disabled, true);
  await click('取消任务');
  assert.match(document.body.textContent, /已取消/);
  assert.deepEqual(posts, ['/api/history/sessions/s1/distill', '/api/history/jobs/j1/retry', '/api/history/jobs/j1/cancel']);
});

test('候选经验点击依据回查脱敏记录，HTML 保持文本且来源校验值完整', async (t) => {
  const urls = [];
  const completed = job('completed', { result: { records: [], lessons: [{ candidate: '重启前确认端口归属', evidence_ids: ['r2'], scope: '仅该项目开发环境' }], gaps: ['缺少生产复测'] } });
  t.mock.method(globalThis, 'fetch', async (url) => {
    urls.push(url);
    if (url.includes('?project_id=')) return json([session]);
    if (url.includes('/records/')) return json(record('r2', '<script>unsafe()</script> [已脱敏]'));
    return json(detail({ jobs: [completed] }));
  });
  await mount(HistoryKnowledge, props);
  await click('候选经验与任务');
  assert.match(document.body.textContent, /候选经验 · 待核实/);
  assert.match(document.body.textContent, /仅该项目开发环境/);
  assert.match(document.body.textContent, /缺少生产复测/);
  await click('依据 1r2');
  assert.ok(urls.includes('/api/history/records/r2'));
  assert.equal(document.querySelector('script'), null);
  assert.match(document.body.textContent, /<script>unsafe\(\)<\/script> \[已脱敏\]/);
  assert.match(document.body.textContent, /record-hash/);
  assert.match(document.body.textContent, /source-hash/);
});

test('提炼状态自动读取服务端更新，完成后采用服务端剩余量', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let current = job('queued');
  let pending = 31;
  t.mock.method(globalThis, 'fetch', async (url) => url.includes('?project_id=') ? json([session]) : json(detail({ session: { ...session, pending_count: pending }, jobs: [current] })));
  await mount(HistoryKnowledge, props);
  await click('候选经验与任务');
  assert.match(document.body.textContent, /排队中/);
  current = job('completed', { result: { records: [], lessons: [], gaps: [] } });
  pending = 11;
  await act(async () => t.mock.timers.tick(3000));
  await settle();
  assert.match(document.querySelector('[aria-label="会话详情"]').textContent, /11 段待提炼/);
  assert.match(document.body.textContent, /已完成/);
  assert.match(document.body.textContent, /本次未提炼出候选经验/);
  assert.equal(findButton('取消任务'), undefined);
});

test('提炼请求失败后保留原文并恢复按钮，断网时不能重复提交', async (t) => {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url.includes('?project_id=')) return json([session]);
    if (options.method === 'POST') return json({ detail: '任务队列已满，请稍后重试' }, 409);
    return json(detail());
  });
  await mount(HistoryKnowledge, props);
  await click('提炼候选经验');
  assert.match(document.body.textContent, /任务队列已满，请稍后重试/);
  assert.match(document.body.textContent, /脱敏正文 r1/);
  assert.equal(findButton('提炼候选经验').disabled, false);
  await act(async () => root.render(React.createElement(HistoryKnowledge, { ...props, online: false })));
  assert.match(document.body.textContent, /脱敏正文 r1/);
  assert.equal(findButton('提炼候选经验').disabled, true);
});

test('项目切换后不保留前一项目的会话、原文或依据', async (t) => {
  const urls = [];
  t.mock.method(globalThis, 'fetch', async (url) => {
    urls.push(url);
    if (url.endsWith('project_id=p1')) return json([session]);
    if (url.endsWith('project_id=p2')) return json([]);
    return json(detail());
  });
  await mount(HistoryKnowledge, props);
  assert.match(document.body.textContent, /脱敏正文 r1/);
  await act(async () => root.render(React.createElement(HistoryKnowledge, { ...props, project: { id: 'p2', name: '另一个项目' } })));
  await settle();
  assert.ok(urls.includes('/api/history/sessions?project_id=p2'));
  assert.doesNotMatch(document.body.textContent, /脱敏正文 r1|端口冲突排查/);
  assert.match(document.body.textContent, /尚未同步会话/);
});

test('Evidence 为多依据经验逐条读取 history records，不能访问 Git sources', async (t) => {
  const urls = [];
  t.mock.method(globalThis, 'fetch', async (url) => { urls.push(url); return json(record(url.endsWith('r2') ? 'r2' : 'r1')); });
  const source = { id: 'S1', origin: 'experience', record_ids: ['r1', 'r2'], path: '经验候选/任务/1', content: '候选经验（需核实）', time: '2025-01-02T03:04:00Z' };
  await mount(Evidence, { sources: [source], activeId: 'S1', snapshotId: 'code-snapshot', onSelect: () => {}, onUnauthorized: () => {} });
  await click('脱敏原文');
  assert.match(document.body.textContent, /脱敏正文 r1/);
  const select = document.querySelector('select[aria-label="选择原文依据"]');
  assert.ok(select);
  await act(async () => { select.value = 'r2'; select.dispatchEvent(new browser.Event('change', { bubbles: true })); });
  await settle();
  assert.match(document.body.textContent, /脱敏正文 r2/);
  assert.deepEqual(urls, ['/api/history/records/r1', '/api/history/records/r2']);
});

test('未报告的上游用量在任务卡片直接显示，不能冒充零费用', async (t) => {
  const failed = job('failed', { usage: { total_tokens: 0, unreported_calls: 2 } });
  t.mock.method(globalThis, 'fetch', async (url) => url.includes('?project_id=') ? json([session]) : json(detail({ jobs: [failed] })));
  await mount(HistoryKnowledge, props);
  await click('候选经验与任务');
  assert.match(document.querySelector('.conversation-job-meta').textContent, /2 次用量未知/);
  assert.doesNotMatch(document.querySelector('.conversation-job-meta').textContent, /0 tokens/);
});

test('401 交还登录流程；原文缺少 record ID 时明确提示且不访问 Git', async (t) => {
  let expired = 0;
  const requests = t.mock.method(globalThis, 'fetch', async () => json({ detail: '登录已失效' }, 401));
  await mount(HistoryKnowledge, { ...props, onUnauthorized: () => { expired += 1; } });
  assert.equal(expired, 1);
  await act(async () => root.render(React.createElement(Evidence, { sources: [{ id: 'S1', origin: 'conversation', content: '会话片段' }], onUnauthorized: () => {} })));
  await click('脱敏原文');
  assert.match(document.body.textContent, /未提供可回查的记录 ID/);
  assert.equal(requests.mock.callCount(), 1);
});
