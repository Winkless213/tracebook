import assert from 'node:assert/strict';
import test from 'node:test';
import { questionIntent, validateSnapshot, isPending, sourceLines, questionError, mergeQuestion } from './domain.js';

const snapshot = () => ({ schema: 1, project: 'ledger', branch: 'main', commit: 'a'.repeat(40), exported_at: '2026-10-09T00:00:00Z', excluded: [], files: [{ path: 'README.md', content: '# 说明', sha256: 'b'.repeat(64) }] });

test('同一提交网络重试复用幂等键，改变快照必须创建新键', () => {
  const payload = { project_id: 'p1', snapshot_id: 's1', question: '失败任务如何恢复执行？' };
  const first = questionIntent(null, payload, () => 'first');
  assert.equal(questionIntent(first, { ...payload }, () => 'second').key, 'first');
  assert.equal(questionIntent(first, { ...payload, snapshot_id: 's2' }, () => 'second').key, 'second');
});

test('快照只接受契约格式，限制按 UTF-8 字节执行', () => {
  assert.equal(validateSnapshot(snapshot()), null);
  assert.match(validateSnapshot({ ...snapshot(), schema: 2 }), /schema/);
  const huge = snapshot();
  huge.files[0].content = '中'.repeat(700000);
  assert.match(validateSnapshot(huge), /2 MB/);
});

test('快照文件列表上限为 200，拒绝不完整文件', () => {
  const tooMany = snapshot();
  tooMany.files = Array.from({ length: 201 }, () => snapshot().files[0]);
  assert.match(validateSnapshot(tooMany), /200/);
  const missing = snapshot();
  delete missing.files[0].sha256;
  assert.match(validateSnapshot(missing), /sha256/);
});

test('导入限制与后端的十进制 2 MB 保持一致', () => {
  const over = snapshot();
  over.files[0].content = 'a'.repeat(2000001);
  assert.match(validateSnapshot(over), /2 MB/);
});

test('行号从证据的 start_line 开始，原文中的 HTML 保持字符串', () => {
  assert.deepEqual(sourceLines('<script>alert(1)</script>\r\n下一行', 18), [{ number: 18, text: '<script>alert(1)</script>' }, { number: 19, text: '下一行' }]);
});

test('只对非终态继续轮询', () => {
  assert.equal(isPending('queued'), true);
  assert.equal(isPending('retrieving'), true);
  assert.equal(isPending('generating'), true);
  for (const status of ['completed', 'failed', 'cancelled']) assert.equal(isPending(status), false);
});

test('问题必须达到契约长度，保留中文字符计数', () => {
  assert.match(questionError('太短'), /8/);
  assert.equal(questionError('这个项目的重试机制是什么？'), null);
  assert.match(questionError('中'.repeat(2001)), /2000/);
});

test('轮询更新历史而不重复插入，按创建时间保留顺序', () => {
  const older = { id: 'old', created_at: '2026-10-08T00:00:00Z', status: 'queued' };
  const newer = { id: 'new', created_at: '2026-10-09T00:00:00Z', status: 'completed' };
  const result = mergeQuestion([newer, older], { ...older, status: 'failed' });
  assert.equal(result.length, 2);
  assert.equal(result[0].id, 'new');
  assert.equal(result[1].status, 'failed');
});
