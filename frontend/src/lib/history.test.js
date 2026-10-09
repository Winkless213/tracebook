import assert from 'node:assert/strict';
import test from 'node:test';
import { historyApi, historyJobActions, historyRecordIds, historyTime, mergeHistoryJob } from './history.js';

const json = (value) => new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } });

test('会话请求限定项目，原文分页保留服务端 pending_count', async () => {
  const requests = [];
  const detail = { session: { id: 's/1', pending_count: 67 }, records: [], total: 117, offset: 50, jobs: [] };
  const options = { fetcher: async (url) => { requests.push(url); return json(url.includes('offset=') ? detail : []); } };
  await historyApi.sessions('p&1', options);
  const result = await historyApi.session('s/1', 50, options);
  assert.deepEqual(requests, ['/api/history/sessions?project_id=p%261', '/api/history/sessions/s%2F1?offset=50&limit=50']);
  assert.equal(result.session.pending_count, 67);
  assert.equal(result.offset, 50);
});

test('提炼、续跑和取消直接消费 Job，不假设 job 包装字段', async () => {
  const job = { id: 'j/1', session_id: 's/1', status: 'failed', error: '调用超时', record_ids: ['r1'] };
  const requests = [];
  const options = { fetcher: async (url, request) => {
    requests.push(url);
    assert.equal(request.method, 'POST');
    assert.equal(request.credentials, 'same-origin');
    assert.equal(request.headers['X-Requested-With'], 'Tracebook');
    assert.deepEqual(JSON.parse(request.body), {});
    return json(job);
  } };
  assert.deepEqual(await historyApi.distill('s/1', options), job);
  assert.deepEqual(await historyApi.retry('j/1', options), job);
  assert.deepEqual(await historyApi.cancel('j/1', options), job);
  assert.deepEqual(requests, ['/api/history/sessions/s%2F1/distill', '/api/history/jobs/j%2F1/retry', '/api/history/jobs/j%2F1/cancel']);
});

test('会话与多依据经验保留真实记录 ID，引用编号不能冒充记录 ID', async () => {
  assert.deepEqual(historyRecordIds({ id: 'S1', origin: 'conversation', record_id: 'r/1' }), ['r/1']);
  assert.deepEqual(historyRecordIds({ id: 'S2', origin: 'experience', record_id: 'r1', record_ids: ['r1', 'r2', 'r1'] }), ['r1', 'r2']);
  assert.deepEqual(historyRecordIds({ id: 'S3', origin: 'experience' }), []);
  assert.deepEqual(historyRecordIds({ id: 'S4', origin: 'code', record_id: 'not-a-record' }), []);
  const record = { id: 'r/1', role: 'assistant', text: '[已脱敏]', time: null, session_id: 's1', provenance: { file: 'session.jsonl', line: 29, part: 0, record_sha256: 'a', source_sha256: 'b' } };
  const result = await historyApi.record('r/1', { fetcher: async (url) => {
    assert.equal(url, '/api/history/records/r%2F1');
    return json(record);
  } });
  assert.deepEqual(result, record);
});

test('任务更新不能串会话，不能猜测剩余量或重复加入任务', () => {
  const detail = { session: { id: 's1', pending_count: 48 }, records: [{ id: 'r1' }], total: 50, offset: 0, jobs: [{ id: 'j1', session_id: 's1', status: 'generating' }] };
  const job = { id: 'j1', session_id: 's1', status: 'completed', result: { lessons: [] } };
  const next = mergeHistoryJob(detail, job);
  assert.deepEqual(next.jobs, [job]);
  assert.equal(next.session.pending_count, 48);
  assert.equal(next.records, detail.records);
  assert.equal(detail.jobs[0].status, 'generating');
  assert.equal(mergeHistoryJob(detail, { ...job, session_id: 'another-session' }), detail);
});

test('仅进行中任务允许取消，只有失败任务允许续跑', () => {
  for (const status of ['queued', 'generating']) assert.deepEqual(historyJobActions(status), ['cancel']);
  assert.deepEqual(historyJobActions('failed'), ['retry']);
  for (const status of ['completed', 'cancelled', 'unknown', undefined]) assert.deepEqual(historyJobActions(status), []);
});

test('历史时间保留年份，缺失时间不能显示成 1970 年', () => {
  assert.match(historyTime('2025-01-02T03:04:00Z'), /2025/);
  assert.equal(historyTime(null), '时间未提供');
  assert.equal(historyTime('invalid'), '时间未提供');
});
