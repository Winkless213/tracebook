import React from 'react';
import { historyJobActions, HISTORY_STATUS, historyTime } from '../lib/history.js';
import { Icon, Notice, Spinner } from './Ui.jsx';

const CATEGORIES = { user_decision: '用户决策', user_proposal: '用户提议', assistant_hypothesis: '助手假设', tool_observation: '工具观测', tool_claim: '工具陈述' };
const CLAIM_STATUS = { supported_as_record: '有记录支持', unverified: '尚未核实', contradicted: '存在矛盾' };

export function HistoryReferences({ ids = [], onEvidence, label = '依据' }) {
  return <div className="conversation-references">{[...new Set(ids)].map((id, index) => <button key={id} className="button secondary" onClick={() => onEvidence(id)} title={id}><Icon name="link" size={13} />{label} {index + 1}<code>{id.slice(0, 8)}</code></button>)}</div>;
}

function DistillationResult({ result, onEvidence }) {
  return <div className="distillation-result">
    <p className="conversation-note">以下为 AI 提炼的候选内容，使用前请核对依据和适用范围。</p>
    {result.lessons?.length ? <div className="conversation-lessons">{result.lessons.map((lesson, index) => <article className="conversation-lesson" key={index}><span className="candidate-label">候选经验 · 待核实</span><h4>{lesson.candidate}</h4><p><strong>适用范围：</strong>{lesson.scope || '未提供'}</p><HistoryReferences ids={lesson.evidence_ids} onEvidence={onEvidence} /></article>)}</div> : <p className="conversation-note">本次未提炼出候选经验。</p>}
    {result.records?.length > 0 && <details className="conversation-findings"><summary>记录提要 · {result.records.length} 条</summary>{result.records.map((record) => <article key={record.id}><div className="conversation-tags"><span>{CATEGORIES[record.category] || record.category}</span><span>记录标注：{CLAIM_STATUS[record.claim_status] || record.claim_status}</span></div><p>{record.finding}</p><HistoryReferences ids={[record.id, ...(record.related_ids || [])]} onEvidence={onEvidence} /></article>)}</details>}
    {result.gaps?.length > 0 && <div className="conversation-gaps"><strong>还缺少的依据</strong><ul>{result.gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul></div>}
  </div>;
}

export function HistoryJobCard({ job, onAction, onEvidence, busy, online = true }) {
  const activeAction = busy?.id === job.id ? busy.action : null;
  return <article className="conversation-job" aria-label={`提炼任务 ${job.id.slice(0, 8)}`}>
    <header className="conversation-job-heading"><div><span className={`status-badge status-${job.status}`}><span className="status-dot" />{HISTORY_STATUS[job.status] || job.status}</span><time dateTime={job.updated_at}>{historyTime(job.updated_at)}</time></div><div className="conversation-job-actions">{historyJobActions(job.status).map((action) => <button className="button secondary" key={action} disabled={Boolean(busy) || !online} onClick={() => onAction(action, job.id)}>{activeAction === action ? <Spinner label={action === 'retry' ? '提交中' : '取消中'} /> : action === 'retry' ? '续跑任务' : '取消任务'}</button>)}</div></header>
    <div className="conversation-job-meta"><span>{job.record_ids.length} 段输入</span><span>{job.attempts?.length ?? 0} 次尝试</span>{job.usage?.total_tokens != null && (!job.usage.unreported_calls || job.usage.total_tokens > 0) && <span>{job.usage.total_tokens.toLocaleString()} {job.usage.unreported_calls ? '已报告 tokens' : 'tokens'}</span>}{job.usage?.unreported_calls > 0 && <span>{job.usage.unreported_calls} 次用量未知</span>}</div>
    <Notice>{job.error}</Notice>
    {['queued', 'generating'].includes(job.status) && <p className="conversation-note" role="status">{job.status === 'queued' ? '任务已入队，等待处理。' : '正在根据本次输入提炼候选经验。'}状态会自动刷新，离开页面后可回来查看。</p>}
    {job.status === 'cancelled' && <p className="conversation-note">任务已取消，输入记录仍保留。可点击“提炼候选经验”创建新任务。</p>}
    {job.status === 'completed' && (job.result ? <DistillationResult result={job.result} onEvidence={onEvidence} /> : <Notice tone="warning">任务已完成，但未返回可展示的提炼结果。</Notice>)}
    <details className="conversation-execution"><summary>输入依据与执行记录</summary><code className="conversation-job-id">{job.id}</code><HistoryReferences ids={job.record_ids} onEvidence={onEvidence} label="输入" />
      {job.attempts?.map((attempt, index) => <div className="conversation-attempt" key={attempt.id || index}><span>{attempt.model} · {HISTORY_STATUS[attempt.status] || attempt.status}</span>{attempt.latency_ms != null && <span>{(attempt.latency_ms / 1000).toFixed(1)} 秒</span>}{attempt.error && <p>{attempt.error}</p>}</div>)}
      {job.usage?.unreported_calls > 0 && <p className="conversation-note">{job.usage.unreported_calls} 次调用未报告用量。</p>}
    </details>
  </article>;
}

export default function HistoryJobs({ jobs, ...props }) {
  return <section aria-label="候选经验与提炼任务">{jobs.length ? <div className="conversation-jobs">{jobs.map((job) => <HistoryJobCard key={job.id} job={job} {...props} />)}</div> : <div className="conversation-empty"><Icon name="layers" size={24} /><h3>还没有提炼任务</h3><p>点击“提炼候选经验”，从尚未提炼的会话记录中整理经验与依据。</p></div>}</section>;
}
