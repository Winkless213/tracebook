import React, { useCallback } from 'react';
import { useHistoryResource } from '../history-hooks.js';
import { historyApi, HISTORY_ROLES, historyTime } from '../lib/history.js';
import { Icon, Notice, Spinner } from './Ui.jsx';

export function HistoryRecordContent({ record, onEvidence }) {
  const provenance = record.provenance || {};
  return <article className="conversation-record">
    <header className="conversation-record-heading"><strong>{HISTORY_ROLES[record.role] || record.role}</strong><time dateTime={record.time || undefined}>{historyTime(record.time)}</time><span className="kind-tag">脱敏记录</span></header>
    <pre className="conversation-text" tabIndex={0} aria-label="脱敏记录正文">{record.text || '（空记录）'}</pre>
    <div className="conversation-record-source"><span>{provenance.file || '来源文件未提供'}{provenance.line != null && ` · L${provenance.line}`}{provenance.part != null && ` · 分段 ${provenance.part}`}</span>{onEvidence && <button className="inline-button" onClick={() => onEvidence(record.id)}>回查来源</button>}</div>
    <details className="conversation-provenance"><summary>来源与校验值</summary><dl>
      <div><dt>记录 ID</dt><dd><code>{record.id}</code></dd></div>
      <div><dt>会话 ID</dt><dd><code>{record.session_id}</code></dd></div>
      <div><dt>来源文件</dt><dd><code>{provenance.file || '未提供'}</code></dd></div>
      <div><dt>原始行 / 分段</dt><dd>{provenance.line ?? '未提供'} / {provenance.part ?? '未提供'}</dd></div>
      <div><dt>记录 SHA-256</dt><dd><code>{provenance.record_sha256 || '未提供'}</code></dd></div>
      <div><dt>来源 SHA-256</dt><dd><code>{provenance.source_sha256 || '未提供'}</code></dd></div>
    </dl></details>
  </article>;
}

export function HistoryRecordLoader({ recordId, onUnauthorized, online = true }) {
  const load = useCallback((options) => historyApi.record(recordId, options), [recordId]);
  const state = useHistoryResource(load, { onUnauthorized, online });
  return <div aria-busy={state.loading}>
    {state.loading && <div className="source-loading" role="status"><Spinner label="读取脱敏原文" /></div>}
    <Notice action={<button className="inline-button" onClick={state.refresh} disabled={!online || state.refreshing}>重试读取原文</button>}>{state.error}</Notice>
    {!online && !state.data && <Notice tone="warning">网络已断开，连接恢复后读取原文。</Notice>}
    {state.data && <HistoryRecordContent record={state.data} />}
  </div>;
}

export default function HistoryRecordPanel({ recordId, onClose, onUnauthorized, online }) {
  return <aside id="history-evidence" className="conversation-evidence card" tabIndex={-1} aria-labelledby="history-evidence-title">
    <div className="section-topline"><div className="section-heading"><Icon name="link" size={17} /><h3 id="history-evidence-title">依据原文</h3></div><button className="icon-button" aria-label="关闭依据原文" onClick={onClose}><Icon name="close" size={17} /></button></div>
    <p className="conversation-note">回查已同步的脱敏记录，核对当时的表述与来源。</p>
    <HistoryRecordLoader key={recordId} recordId={recordId} onUnauthorized={onUnauthorized} online={online} />
  </aside>;
}
