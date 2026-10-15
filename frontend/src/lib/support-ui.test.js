import assert from 'node:assert/strict';
import { after, afterEach, test } from 'node:test';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { Window } from 'happy-dom';
import { createServer } from 'vite';

const server = await createServer({ server: { middlewareMode: true, hmr: false }, optimizeDeps: { noDiscovery: true, include: [] }, appType: 'custom', logLevel: 'error' });
after(() => server.close());
const { default: Support } = await server.ssrLoadModule('/src/components/Support.jsx');
const { default: SupportReport } = await server.ssrLoadModule('/src/components/SupportReport.jsx');
const { default: SupportProgress } = await server.ssrLoadModule('/src/components/SupportProgress.jsx');
const { KnowledgeOverview } = await server.ssrLoadModule('/src/components/ProjectKnowledge.jsx');
const browser = new Window({ url: 'http://localhost/?view=support&thread=t1' });
globalThis.window = browser;
globalThis.document = browser.document;
Object.defineProperty(globalThis, 'navigator', { value: browser.navigator, configurable: true });
globalThis.FormData = browser.FormData;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let root;
afterEach(async () => { if (root) await act(() => root.unmount()); root = null; document.body.innerHTML = ''; browser.history.replaceState({}, '', '?view=support&thread=t1'); });
after(() => browser.happyDOM.close());
const json = (value) => new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } });
const settle = () => act(async () => { await new Promise(resolve => setImmediate(resolve)); });
const initial = { thread_id: 't1', symptom: '队列任务持续排队无法完成', status: 'awaiting_info', product_version: '', reproduction: '', masked_log: '',
  updated_at: 'v1', report: null, attempts: [], pending: { missing: ['product_version', 'reproduction'], log_guidance: '请提供故障附近日志' } };
const report = { hypotheses: [{ text: '连接池不足', basis: [{ id: 'L1' }] }], suggestions: ['核对连接池上限'],
  evidence: [{ id: 'L1', kind: 'log', path: 'submitted-log.txt', start_line: 1, end_line: 1, content: '<script>throw 1</script>' }],
  verification: { status: 'analyzed', limitations: [] }, redaction: { applied: true }, confirmation: { resolved: false } };
const ready = { ...initial, status: 'awaiting_confirmation', report, product_version: 'v1', reproduction: '启动后排队', updated_at: 'v2', pending: { kind: 'confirmation' } };
async function mount(strict = false) {
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root'));
  const page = React.createElement(Support, { onUnauthorized() {}, onLogout() {} });
  await act(() => root.render(strict ? React.createElement(React.StrictMode, null, page) : page));
  await settle();
}
function mockApi(t, detail) {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url === '/api/support/projects') return json({ projects: [], model_configured: true, analysis_timeout_ms: 255000, log_limit: 64000 });
    if (url === '/api/support/sessions') return json([initial]);
    if (url === '/api/support/cases') return json([]);
    return detail(url, options);
  });
}
function mockChat(t) {
  t.mock.method(globalThis, 'fetch', async (url) => {
    if (url === '/api/support/projects') return json({ projects: [], model_configured: true, analysis_timeout_ms: 255000 });
    if (['/api/support/sessions', '/api/support/cases', '/api/support/conversations'].includes(url)) return json([]);
    return json({ id: 'c1', status: 'ready', project_id: null, turns: [] });
  });
}

test('知识首页项目卡片提供带项目身份的客服入口', () => {
  const html = renderToStaticMarkup(React.createElement(KnowledgeOverview, { data: { projects: [{ name: 'worker & helper', documents: [] }] } }));
  assert.match(html, /view=support/);
  assert.match(html, /worker%20%26%20helper/);
});

test('分析未完成或仍未解决时，引导补充材料而非验证结果', () => {
  for (const status of ['processing', 'failed', 'unresolved']) {
    const element = document.createElement('div');
    element.innerHTML = renderToStaticMarkup(React.createElement(SupportProgress, { status }));
    assert.match(element.querySelector('[aria-current="step"]').textContent, /补充材料/);
  }
});

test('从 URL 恢复会话，超限中文日志不会请求模型或写入浏览器存储', async t => {
  let posts = 0;
  mockApi(t, async (url, options) => { if (options.method === 'POST') posts++; return json(initial); });
  await mount();
  assert.match(document.body.textContent, /队列任务持续排队/);
  const form = document.querySelector('form[aria-label="补充排查材料"]');
  assert.ok(form);
  form.elements.log_text.value = '错'.repeat(22000);
  await act(() => form.dispatchEvent(new browser.Event('submit', { bubbles: true, cancelable: true })));
  assert.match(document.body.textContent, /64000/);
  assert.equal(posts, 0);
  assert.equal(browser.localStorage.length, 0);
});

test('分析请求失去响应后只查状态，不重复提交，拿到报告后可明确确认', async t => {
  let latest = initial;
  const posts = [];
  mockApi(t, async (url, options) => {
    if (options.method === 'POST') {
      posts.push(url);
      if (url.endsWith('/supplement')) { latest = ready; throw new TypeError('connection lost'); }
      if (url.endsWith('/confirm')) {
        assert.equal(JSON.parse(options.body).resolved, true);
        latest = { ...ready, status: 'resolved', case_id: 'case1', report: { ...report, confirmation: { resolved: true } } };
      }
    }
    return json(latest);
  });
  await mount();
  const form = document.querySelector('form[aria-label="补充排查材料"]');
  form.elements.product_version.value = 'v1';
  form.elements.reproduction.value = '启动后排队';
  await act(() => form.dispatchEvent(new browser.Event('submit', { bubbles: true, cancelable: true })));
  await settle();
  const refresh = [...document.querySelectorAll('button')].find(x => x.textContent.includes('检查最新状态'));
  if (refresh) { await act(() => refresh.click()); await settle(); }
  assert.match(document.body.textContent, /连接池不足/);
  assert.equal(posts.filter(x => x.endsWith('/supplement')).length, 1);
  const confirm = [...document.querySelectorAll('button')].find(x => x.textContent === '确认已解决');
  assert.ok(confirm && !confirm.disabled);
  await act(() => confirm.click());
  await settle();
  assert.match(document.body.textContent, /已保存为确认案例/);
});

test('日志引用仅作文本展示，分析假设不冒充已经解决', () => {
  const html = renderToStaticMarkup(React.createElement(SupportReport, { session: ready }));
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /原因假设/);
  assert.doesNotMatch(html, /已保存为确认案例/);
});

test('React 严格模式清理首次请求后仍能恢复会话', async t => {
  mockApi(t, async () => json(initial));
  await mount(true);
  assert.match(document.body.textContent, /队列任务持续排队/);
  assert.ok(document.querySelector('form[aria-label="补充排查材料"]'));
});

test('迟到的状态查询不能覆盖已经返回的分析结果', async t => {
  let resolveRead;
  let gets = 0;
  mockApi(t, async (url, options) => {
    if (options.method === 'POST') return json(ready);
    if (++gets === 1) return json(initial);
    return new Promise(resolve => { resolveRead = resolve; });
  });
  await mount();
  const refresh = [...document.querySelectorAll('button')].find(item => item.textContent.includes('检查最新状态'));
  await act(() => refresh.click());
  const form = document.querySelector('form[aria-label="补充排查材料"]');
  form.elements.product_version.value = 'v1';
  await act(() => form.dispatchEvent(new browser.Event('submit', { bubbles: true, cancelable: true })));
  await settle();
  assert.match(document.body.textContent, /连接池不足/);
  await act(async () => { resolveRead(json(initial)); });
  await settle();
  assert.match(document.body.textContent, /连接池不足/);
  assert.ok([...document.querySelectorAll('button')].find(item => item.textContent === '确认已解决'));
});

test('切换会话后忽略旧会话迟到的写响应，回退能恢复原会话', async t => {
  let resolvePost;
  const second = { ...initial, thread_id: 't2', symptom: '第二个项目启动后没有声音' };
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url === '/api/support/projects') return json({ projects: [], model_configured: true, analysis_timeout_ms: 255000, log_limit: 64000 });
    if (url === '/api/support/sessions') return json([initial, second]);
    if (url === '/api/support/cases') return json([]);
    if (options.method === 'POST') return new Promise(resolve => { resolvePost = resolve; });
    return json(url.endsWith('/t2') ? second : initial);
  });
  await mount();
  const form = document.querySelector('form[aria-label="补充排查材料"]');
  form.elements.product_version.value = 'v1';
  await act(() => form.dispatchEvent(new browser.Event('submit', { bubbles: true, cancelable: true })));
  const choose = [...document.querySelectorAll('.support-history-list button')].find(item => item.textContent.includes(second.symptom));
  await act(() => choose.click());
  await settle();
  assert.equal(document.querySelector('.support-ticket-heading h1').textContent, second.symptom);
  await act(async () => { resolvePost(json(ready)); });
  await settle();
  assert.equal(document.querySelector('.support-ticket-heading h1').textContent, second.symptom);
  assert.doesNotMatch(document.querySelector('.support-content').textContent, /连接池不足/);
  await act(() => { browser.history.back(); });
  await settle();
  assert.equal(document.querySelector('.support-ticket-heading h1').textContent, initial.symptom);
});

test('日常咨询直接发送，保留来源，并能带着问题进入排查', async t => {
  browser.history.replaceState({}, '', '?view=support');
  let saved;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url === '/api/support/projects') return json({ projects: [], model_configured: true, analysis_timeout_ms: 255000 });
    if (['/api/support/sessions', '/api/support/cases', '/api/support/conversations'].includes(url)) return json([]);
    if (options.method === 'POST') {
      const payload = JSON.parse(options.body);
      assert.equal(payload.message, '页面一直转圈，先给我解释一下可能是什么情况');
      assert.equal(payload.search_query, '');
      saved = { id: url.split('/')[4], status: 'ready', project_id: null, turns: [{ ...payload, status: 'completed', response: {
        reply: '转圈通常表示还在等待结果。可以先确认是否每次都这样。', needs_diagnosis: true,
        sources: [], search: { status: 'not_requested' }, warnings: [],
      } }] };
    }
    return json(saved);
  });
  await mount();
  const form = document.querySelector('form[aria-label="发送咨询"]');
  assert.ok(form);
  assert.equal(document.querySelector('[name="product_version"]'), null);
  form.elements.message.value = '页面一直转圈，先给我解释一下可能是什么情况';
  await act(() => form.dispatchEvent(new browser.Event('submit', { bubbles: true, cancelable: true })));
  await settle();
  assert.match(document.body.textContent, /转圈通常表示还在等待结果/);
  assert.ok(browser.location.search.includes('conversation='));
  const diagnose = [...document.querySelectorAll('button')].find(item => item.textContent.includes('带着问题继续排查'));
  await act(() => diagnose.click());
  await settle();
  assert.equal(document.querySelector('[name="symptom"]').value, '页面一直转圈，先给我解释一下可能是什么情况');
});

test('咨询写入丢失响应后查询同一对话，不自动重发问题', async t => {
  browser.history.replaceState({}, '', '?view=support');
  let posts = 0;
  let saved;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url === '/api/support/projects') return json({ projects: [], model_configured: true, analysis_timeout_ms: 255000 });
    if (['/api/support/sessions', '/api/support/cases', '/api/support/conversations'].includes(url)) return json([]);
    if (options.method === 'POST') {
      posts++;
      const payload = JSON.parse(options.body);
      saved = { id: url.split('/')[4], status: 'ready', project_id: null, turns: [{ ...payload, status: 'completed', response: {
        reply: '连接池会重复利用已经建立的连接。', needs_diagnosis: false, sources: [], search: { status: 'not_requested' }, warnings: [],
      } }] };
      throw new TypeError('connection lost');
    }
    return json(saved);
  });
  await mount();
  const form = document.querySelector('form[aria-label="发送咨询"]');
  assert.ok(form);
  form.elements.message.value = '连接池是什么？';
  await act(() => form.dispatchEvent(new browser.Event('submit', { bubbles: true, cancelable: true })));
  await settle();
  const refresh = [...document.querySelectorAll('button')].find(item => item.textContent.includes('检查最新状态'));
  await act(() => refresh.click());
  await settle();
  assert.match(document.body.textContent, /连接池会重复利用/);
  assert.equal(posts, 1);
  assert.equal(document.querySelector('[name="message"]').value, '');
});

test('联网查证默认开启，由客服自主检索，搜索提示词可留空', async t => {
  browser.history.replaceState({}, '', '?view=support');
  let sent;
  let saved;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url === '/api/support/projects') return json({ projects: [], model_configured: true, analysis_timeout_ms: 255000 });
    if (['/api/support/sessions', '/api/support/cases', '/api/support/conversations'].includes(url)) return json([]);
    if (options.method === 'POST') {
      sent = JSON.parse(options.body);
      saved = { id: url.split('/')[4], status: 'ready', project_id: null, turns: [{ ...sent, status: 'completed', response: {
        reply: 'Python 当前最新稳定版是 3.15.0。', needs_diagnosis: false,
        sources: [{ id: 'W1', kind: 'web', title: 'Python.org', url: 'https://python.org', content: '3.15.0', scope: 'snippet' }],
        search: { status: 'found', query: 'Python latest stable version', notice: '' }, warnings: [],
      } }] };
    }
    return json(saved);
  });
  await mount();
  const form = document.querySelector('form[aria-label="发送咨询"]');
  assert.equal(document.querySelector('.chat-web-toggle input').checked, true);
  form.elements.message.value = 'Python 最新稳定版是多少？';
  // 提示词留空也应能发送
  await act(() => form.dispatchEvent(new browser.Event('submit', { bubbles: true, cancelable: true })));
  await settle();
  assert.equal(sent.allow_web, true);
  assert.equal(sent.search_query, '');
  assert.match(document.body.textContent, /3\.15\.0/);
  assert.match(document.body.textContent, /已检索/);
});

test('客服导航指向知识库，并说明关联项目后回答会引用项目知识', async t => {
  browser.history.replaceState({}, '', '?view=support');
  mockChat(t);
  await mount();
  assert.match(document.querySelector('.support-topnav a[href="/"]').textContent, /知识库/);
  assert.match(document.body.textContent, /引用你的项目知识库/);
});

test('日常答疑与故障排查各有用途说明', async t => {
  browser.history.replaceState({}, '', '?view=support');
  mockChat(t);
  await mount();
  assert.match(document.body.textContent, /白话问用法/);
  const diagnose = [...document.querySelectorAll('.support-mode-switch button')].find(item => item.textContent.includes('故障排查'));
  await act(() => diagnose.click());
  await settle();
  assert.match(document.body.textContent, /带引用定位问题/);
});

test('联网查证默认开启并提示后端可能不稳定，不编造结果', async t => {
  browser.history.replaceState({}, '', '?view=support');
  mockChat(t);
  await mount();
  assert.match(document.body.textContent, /可能不稳定/);
});

test('点击依据编号会展开并滚动定位到对应证据', async () => {
  const proto = browser.Element.prototype;
  const original = proto.scrollIntoView;
  const scrolled = [];
  proto.scrollIntoView = function () { scrolled.push(this.id); };
  try {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root'));
    await act(() => root.render(React.createElement(SupportReport, { session: ready })));
    await settle();
    const link = document.querySelector('a[href="#support-evidence-L1"]');
    assert.ok(link);
    await act(() => link.click());
    await settle();
    assert.equal(document.getElementById('support-evidence-L1').open, true);
    assert.deepEqual(scrolled, ['support-evidence-L1']);
  } finally {
    if (original) proto.scrollIntoView = original; else delete proto.scrollIntoView;
  }
});

test('分析处理过程中显示已等待时长', async t => {
  let resolvePost;
  mockApi(t, (url, options) => { if (options.method === 'POST') return new Promise(resolve => { resolvePost = resolve; }); return json(initial); });
  await mount();
  const form = document.querySelector('form[aria-label="补充排查材料"]');
  form.elements.product_version.value = 'v1';
  await act(() => form.dispatchEvent(new browser.Event('submit', { bubbles: true, cancelable: true })));
  assert.match(document.body.textContent, /已等待/);
  await act(async () => { resolvePost(json(initial)); });
  await settle();
});
