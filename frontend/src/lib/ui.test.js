import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

const server = await createServer({ server: { middlewareMode: true }, optimizeDeps: { noDiscovery: true, include: [] }, appType: 'custom', logLevel: 'error' });
after(() => server.close());
const { default: Answer } = await server.ssrLoadModule('/src/components/Answer.jsx');
const { default: Evidence } = await server.ssrLoadModule('/src/components/Evidence.jsx');
const source = { id: 'S1', path: 'src/queue.py', start_line: 18, end_line: 19, content: '<script>alert("data")</script>\nreturn state', kind: 'code', commit: 'a'.repeat(40), sha256: 'b'.repeat(64), symbol: 'resume' };
const question = { id: 'q1', question: '任务失败后应当如何恢复？', project_id: 'p1', snapshot_id: 'old', commit: source.commit, created_at: '2026-10-09T00:00:00Z', status: 'completed', sources: [source], result: { status: 'answered', summary: '结论来自项目证据', claims: [{ text: '恢复执行受次数限制', evidence: [{ source_id: 'S1', quote: source.content }] }], gaps: [] } };

test('导入的 HTML 与答案引用按文本转义，不生成脚本节点', () => {
  const answer = renderToStaticMarkup(React.createElement(Answer, { question, currentSnapshotId: 'current' }));
  const evidence = renderToStaticMarkup(React.createElement(Evidence, { sources: [source], activeId: 'S1', snapshotId: 'old' }));
  for (const html of [answer, evidence]) {
    assert.match(html, /&lt;script&gt;/);
    assert.doesNotMatch(html, /<script>/);
  }
  assert.match(evidence, />18<\/span>/);
  assert.match(evidence, />19<\/span>/);
  assert.match(answer, /历史快照/);
});

test('冲突与证据不足具有明确文案，不混为已回答', () => {
  const conflict = renderToStaticMarkup(React.createElement(Answer, { question: { ...question, result: { ...question.result, status: 'conflict' } } }));
  const insufficient = renderToStaticMarkup(React.createElement(Answer, { question: { ...question, result: { ...question.result, status: 'insufficient', gaps: ['缺少部署文档'] } } }));
  assert.match(conflict, /项目资料存在冲突/);
  assert.match(insufficient, /现有证据不足/);
  assert.match(insufficient, /缺少部署文档/);
});

test('失败可续跑，但只有完成的回答才可导出', () => {
  const failed = renderToStaticMarkup(React.createElement(Answer, { question: { ...question, status: 'failed', result: null, error: '模型调用超时' } }));
  const complete = renderToStaticMarkup(React.createElement(Answer, { question }));
  assert.match(failed, /续跑本次提问/);
  assert.doesNotMatch(failed, /\/export/);
  assert.match(complete, /\/api\/questions\/q1\/export/);
});

test('检索与回答标注会话来源和历史时间，不把经验候选包装成代码版本', () => {
  const historySource = { ...source, origin: 'experience', record_ids: ['r1', 'r2'], commit: '', time: '2025-01-02T03:04:00Z', path: '经验候选/提炼任务/1', provenance: { line: 29 } };
  const search = renderToStaticMarkup(React.createElement(Answer, { search: { query: question.question, sources: [historySource] } }));
  const answer = renderToStaticMarkup(React.createElement(Answer, { question: { ...question, sources: [historySource] } }));
  const evidence = renderToStaticMarkup(React.createElement(Evidence, { sources: [historySource] }));
  for (const html of [search, answer, evidence]) {
    assert.match(html, /候选经验 · 待核实/);
    assert.match(html, /2025/);
    assert.doesNotMatch(html, /未提供版本/);
  }
  assert.match(evidence, /脱敏原文/);
  assert.doesNotMatch(evidence, /原文件/);
});
