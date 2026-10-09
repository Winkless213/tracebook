import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

const server = await createServer({ server: { middlewareMode: true }, optimizeDeps: { noDiscovery: true, include: [] }, appType: 'custom', logLevel: 'error' });
after(() => server.close());
const { KnowledgeOverview } = await server.ssrLoadModule('/src/components/ProjectKnowledge.jsx');

test('默认展示真实项目的知识状态与文档，不要求用户提问或配置模型', () => {
  const data = { ready: 1, projects: [
    { name: 'project-a', branch: 'main', status: 'ready', input_files: 18, documents: ['CODEBASE.md'] },
    { name: 'project-b', branch: 'main', status: 'pending', input_files: 0, documents: [] },
  ] };
  const html = renderToStaticMarkup(React.createElement(KnowledgeOverview, { data }));
  assert.match(html, /project-a/);
  assert.match(html, /知识可用/);
  assert.match(html, /待归纳/);
  assert.match(html, /\/api\/developer-knowledge\/project-a\/documents\/CODEBASE.md/);
  assert.doesNotMatch(html, /<textarea|question-input|max_tokens|回答模型/);
});
