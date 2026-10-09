import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useHistoryResource } from '../history-hooks.js';
import { historyApi, HISTORY_PAGE_SIZE, historyTime, mergeHistoryJob } from '../lib/history.js';
import HistoryJobs from './HistoryJobs.jsx';
import HistoryRecordPanel, { HistoryRecordContent } from './HistoryRecord.jsx';
import { Icon, Notice, Spinner } from './Ui.jsx';

function SessionList({ sessions, selectedId, state, onSelect, online }) {
  return <aside className="conversation-sessions card" aria-label="项目会话">
    <div className="section-topline"><h3>项目会话 <span>{sessions.length}</span></h3><button className="icon-button" aria-label="刷新会话" title="刷新会话" onClick={state.refresh} disabled={state.refreshing || !online}><Icon name="refresh" size={16} /></button></div>
    <Notice>{state.error}</Notice>
    {state.loading && <div className="conversation-empty" role="status"><Spinner label="读取会话" /></div>}
    {!state.loading && !state.error && !sessions.length && <div className="conversation-empty"><Icon name="clock" size={24} /><h3>{online ? '尚未同步会话' : '网络已断开'}</h3><p>{online ? '本机连接器接入当前项目后，会话会显示在这里。' : '连接恢复后会自动读取会话。'}</p></div>}
    {sessions.length > 0 && <div className="conversation-session-list">{sessions.map((session) => <button key={session.id} className={`conversation-session ${selectedId === session.id ? 'selected' : ''}`} aria-label={`打开会话：${session.title || session.native_id}`} aria-pressed={selectedId === session.id} onClick={() => onSelect(session.id)}><span className="conversation-session-title">{session.title || session.native_id}</span><span className="conversation-session-meta"><span>{session.harness === 'codex' ? 'Codex' : session.harness === 'claude' ? 'Claude' : session.harness}</span><span>{session.record_count} 段记录</span></span><span className="conversation-session-meta"><span>{session.pending_count} 段待提炼</span><time dateTime={session.updated_at}>{historyTime(session.updated_at)}</time></span></button>)}</div>}
  </aside>;
}

function SessionRecords({ detail, onPage, onEvidence, loading }) {
  const { records, total, offset } = detail;
  return <section aria-label="会话脱敏原文">
    <div className="conversation-pagination"><span>{total ? `${records.length ? offset + 1 : 0}–${Math.min(offset + records.length, total)} / ${total} 段` : '0 段记录'}</span><div><button className="button secondary" onClick={() => onPage(Math.max(0, offset - HISTORY_PAGE_SIZE))} disabled={loading || offset === 0}>上一页</button><button className="button secondary" onClick={() => onPage(offset + HISTORY_PAGE_SIZE)} disabled={loading || offset + HISTORY_PAGE_SIZE >= total}>下一页</button></div></div>
    {records.length ? <div className="conversation-records">{records.map((record) => <HistoryRecordContent key={record.id} record={record} onEvidence={onEvidence} />)}</div> : <div className="conversation-empty"><h3>这一页没有记录</h3><p>同步完成后可刷新查看。</p></div>}
  </section>;
}

function useHistoryAction({ sessionId, state, onUnauthorized, onChanged, onJobs }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState('');
  const request = useRef(null);
  useEffect(() => () => request.current?.abort(), []);
  async function run(action, jobId) {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy({ action, id: jobId });
    setError('');
    try {
      const job = await historyApi[action](action === 'distill' ? sessionId : jobId, { signal: controller.signal });
      if (controller.signal.aborted) return;
      state.update((detail) => mergeHistoryJob(detail, job));
      onJobs();
      onChanged();
    } catch (failure) {
      if (controller.signal.aborted || failure.name === 'AbortError') return;
      if (failure.status === 401) onUnauthorized?.();
      setError(failure.message);
      state.refresh();
    } finally {
      if (!controller.signal.aborted) { request.current = null; setBusy(null); }
    }
  }
  return { busy, error, run };
}

function SessionDetail({ sessionId, onUnauthorized, onChanged, online }) {
  const [offset, setOffset] = useState(0);
  const [tab, setTab] = useState('records');
  const [evidenceId, setEvidenceId] = useState(null);
  const evidenceTrigger = useRef(null);
  const load = useCallback((options) => historyApi.session(sessionId, offset, options), [sessionId, offset]);
  const state = useHistoryResource(load, { onUnauthorized, online, pollMs: 3000 });
  const action = useHistoryAction({ sessionId, state, onUnauthorized, onChanged, onJobs: () => setTab('jobs') });
  const detail = state.data;
  const activeJob = detail?.jobs.some((job) => ['queued', 'generating'].includes(job.status));
  function showEvidence(id) {
    evidenceTrigger.current = document.activeElement;
    setEvidenceId(id);
    requestAnimationFrame(() => { const panel = document.getElementById('history-evidence'); panel?.focus({ preventScroll: true }); panel?.scrollIntoView({ behavior: 'smooth', block: 'start' }); });
  }
  function closeEvidence() { setEvidenceId(null); evidenceTrigger.current?.focus(); }
  return <section className="conversation-detail card" aria-label="会话详情" aria-busy={state.loading}>
    <Notice action={<button className="inline-button" onClick={state.refresh} disabled={!online || state.refreshing}>重新读取会话</button>}>{state.error}</Notice>
    {state.loading && <div className="conversation-empty" role="status"><Spinner label="读取会话原文与任务" /></div>}
    {!online && !detail && <div className="conversation-empty">连接恢复后可读取会话原文与任务。</div>}
    {detail && <>
      <header className="conversation-detail-heading"><div><span className="eyebrow">会话知识</span><h3>{detail.session.title || detail.session.native_id}</h3><p>{detail.session.record_count} 段记录 · <strong>{detail.session.pending_count} 段待提炼</strong></p></div><button className="icon-button" aria-label="刷新会话详情" title="刷新会话详情" onClick={state.refresh} disabled={state.refreshing || !online}><Icon name="refresh" size={16} /></button></header>
      <div className="conversation-distill"><p>每次提炼最近最多 20 段待处理记录，结果保留依据，需人工核对。</p><button className="button primary" disabled={Boolean(action.busy) || !online || activeJob || !(detail.session.pending_count > 0)} onClick={() => action.run('distill')}>{action.busy?.action === 'distill' ? <Spinner label="提交中" /> : <><Icon name="layers" size={15} />提炼候选经验</>}</button></div>
      <Notice>{action.error}</Notice>
      <div className="conversation-tabs" role="group" aria-label="会话内容"><button aria-pressed={tab === 'records'} onClick={() => setTab('records')}>脱敏原文</button><button aria-pressed={tab === 'jobs'} onClick={() => setTab('jobs')}>候选经验与任务</button>{activeJob && <span role="status">任务处理中</span>}</div>
      {evidenceId && <HistoryRecordPanel recordId={evidenceId} onClose={closeEvidence} onUnauthorized={onUnauthorized} online={online} />}
      {tab === 'records' ? <SessionRecords detail={detail} onPage={setOffset} onEvidence={showEvidence} loading={state.refreshing} /> : <HistoryJobs jobs={detail.jobs} onAction={action.run} onEvidence={showEvidence} busy={action.busy} online={online} />}
    </>}
  </section>;
}

function ProjectHistory({ project, onUnauthorized, online }) {
  const [selectedId, setSelectedId] = useState(null);
  const load = useCallback((options) => historyApi.sessions(project.id, options), [project.id]);
  const state = useHistoryResource(load, { onUnauthorized, online, pollMs: 10000 });
  const sessions = state.data || [];
  const selected = sessions.find((session) => session.id === selectedId) || sessions[0];
  useEffect(() => { if (selected) setSelectedId(selected.id); }, [selected?.id]);
  return <section id="history-knowledge" tabIndex={-1} className="conversation-workspace" aria-labelledby="history-knowledge-title">
    <header className="conversation-workspace-heading"><div><h2 id="history-knowledge-title">会话知识</h2><p>在 {project.name} 中回看工程讨论，提炼有依据的候选经验。</p></div><span className="conversation-privacy"><Icon name="lock" size={13} />仅展示脱敏记录</span></header>
    <p className="conversation-connector">通过本机连接器选择 Codex / Claude 会话并归入当前项目后，记录会增量同步到这里。</p>
    <div className="conversation-layout"><SessionList sessions={sessions} selectedId={selected?.id} state={state} onSelect={setSelectedId} online={online} />{selected ? <SessionDetail key={selected.id} sessionId={selected.id} onUnauthorized={onUnauthorized} onChanged={state.refresh} online={online} /> : <div className="conversation-placeholder card"><Icon name="file" size={28} /><h3>选择一条会话</h3><p>查看脱敏原文、提炼进度与候选经验的依据。</p></div>}</div>
  </section>;
}

export default function HistoryKnowledge({ project, onUnauthorized, online = true }) {
  if (!project) return <section id="history-knowledge" className="card conversation-empty"><h2>先选择项目</h2><p>导入项目快照后，可将本机会话同步到该项目。</p></section>;
  return <ProjectHistory key={project.id} project={project} onUnauthorized={onUnauthorized} online={online} />;
}
