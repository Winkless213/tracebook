import React, { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../lib/api.js';
import { KINDS, shortCommit, sourceLines } from '../lib/domain.js';
import { Icon, Notice, Spinner } from './Ui.jsx';

export default function Evidence({ sources, activeId, onSelect, snapshotId, onUnauthorized }) {
  const source = sources.find((item) => item.id === activeId) || sources[0];
  const [view, setView] = useState('snippet');
  const [document, setDocument] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const linesContainer = useRef(null);
  const currentDocument = document?.path === source?.path && document?.snapshot_id === snapshotId ? document : null;
  useEffect(() => { setView('snippet'); setDocument(null); setError(''); }, [source?.id, source?.path, snapshotId]);
  useEffect(() => {
    if (view !== 'document' || !source || !snapshotId) { setLoading(false); setError(''); return; }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    api(`/sources/${encodeURIComponent(snapshotId)}?path=${encodeURIComponent(source.path)}`, { signal: controller.signal }).then((next) => { if (!controller.signal.aborted) setDocument(next); }).catch((failure) => {
      if (failure.status === 401) onUnauthorized();
      if (failure.name !== 'AbortError') setError(failure.message);
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [view, source?.path, snapshotId, revision, onUnauthorized]);
  const lines = useMemo(() => sourceLines(view === 'document' ? currentDocument?.content || '' : source?.content || '', view === 'document' ? 1 : source?.start_line || 1), [view, currentDocument, source]);
  useEffect(() => {
    if (view === 'document' && currentDocument && !loading && linesContainer.current) {
      const first = linesContainer.current.querySelector('.line-highlight');
      if (first) linesContainer.current.scrollTop = Math.max(0, first.offsetTop - 72);
    }
  }, [view, currentDocument, loading]);
  return <aside className="evidence-panel card" id="evidence-panel" tabIndex={-1} aria-labelledby="evidence-title">
    <div className="section-topline"><div className="section-heading"><span className="square-icon"><Icon name="link" size={17} /></span><h2 id="evidence-title">证据上下文</h2></div><span className="evidence-count">{sources.length.toString().padStart(2, '0')}</span></div>
    {!source ? <div className="evidence-empty"><div className="evidence-placeholder"><span /><span /><span /><span /></div><h3>回到原文，确认每个结论</h3><p>检索或提问后，相关文件会显示在这里。点击回答中的引用，查看对应片段和原始行号。</p><div className="evidence-empty-footer"><Icon name="file" size={15} />文件 · 行号 · 提交版本</div></div> : <>
      <label className="source-select-label" htmlFor="source-select">选择证据</label>
      <select id="source-select" className="source-select" value={source.id} onChange={(event) => onSelect(event.target.value)}>{sources.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.path} · L{item.start_line}–{item.end_line}</option>)}</select>
      <div className="source-heading"><div><span className="source-id">{source.id}</span><span className="kind-tag">{KINDS[source.kind] || source.kind}</span></div><h3>{source.path}</h3><div className="source-meta"><code>{shortCommit(source.commit)}</code><span>·</span><span>L{source.start_line}–{source.end_line}</span></div>{source.symbol && <div className="source-symbol">{source.symbol}</div>}</div>
      <div className="source-tabs" role="group" aria-label="证据显示方式"><button id="snippet-tab" aria-pressed={view === 'snippet'} aria-controls="source-content" onClick={() => setView('snippet')}>命中片段</button><button id="document-tab" aria-pressed={view === 'document'} aria-controls="source-content" onClick={() => setView('document')}>原文件</button></div>
      <div id="source-content" aria-labelledby={view === 'document' ? 'document-tab' : 'snippet-tab'}>
        {loading || (view === 'document' && !currentDocument && !error) ? <div className="source-loading"><Spinner label="读取原文件" /></div> : error ? <Notice action={<button className="inline-button" onClick={() => setRevision((value) => value + 1)}>重试</button>}>{error}</Notice> : <div className="code-lines" ref={linesContainer} tabIndex={0} aria-label={view === 'document' ? `${source.path} 完整原文` : `${source.path} 命中片段`}><pre>{lines.map((line) => <div key={line.number} className={`code-line ${view === 'document' && line.number >= source.start_line && line.number <= source.end_line ? 'line-highlight' : ''}`}><span className="line-number" aria-hidden="true">{line.number}</span><code>{line.text || ' '}</code></div>)}</pre></div>}
      </div>
      <div className="source-footer"><Icon name="lock" size={13} /><span>原文按文本展示，代码不会执行。</span></div>
      <details className="source-fingerprint"><summary>文件校验值</summary><code>{currentDocument?.sha256 || source.sha256}</code></details>
    </>}
  </aside>;
}
