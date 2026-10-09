import React, { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api.js';
import { KINDS, shortCommit, sourceLines } from '../lib/domain.js';
import { Icon, Notice, Spinner } from './Ui.jsx';

export default function KnowledgeMap({ project, snapshot, onClose, onUnauthorized }) {
  const dialog = useRef(null);
  const [catalog, setCatalog] = useState(null);
  const [path, setPath] = useState(null);
  const [source, setSource] = useState(null);
  const [error, setError] = useState('');
  const [reading, setReading] = useState(false);
  useEffect(() => { dialog.current.showModal(); }, []);
  useEffect(() => {
    const controller = new AbortController();
    api(`/projects/${encodeURIComponent(project.id)}/knowledge-map?snapshot_id=${encodeURIComponent(snapshot.id)}`, { signal: controller.signal }).then(setCatalog).catch((failure) => {
      if (failure.status === 401) onUnauthorized();
      if (failure.name !== 'AbortError') setError(failure.message);
    });
    return () => controller.abort();
  }, [project.id, snapshot.id, onUnauthorized]);
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    setSource(null); setReading(true); setError('');
    api(`/sources/${encodeURIComponent(snapshot.id)}?path=${encodeURIComponent(path)}`, { signal: controller.signal }).then(setSource).catch((failure) => {
      if (failure.status === 401) onUnauthorized();
      if (failure.name !== 'AbortError') setError(failure.message);
    }).finally(() => { if (!controller.signal.aborted) setReading(false); });
    return () => controller.abort();
  }, [path, snapshot.id, onUnauthorized]);
  return <dialog ref={dialog} className="import-dialog knowledge-dialog" aria-labelledby="knowledge-title" onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <div className="dialog-header"><span className="square-icon"><Icon name="file" size={19} /></span><button className="icon-button" aria-label="关闭项目资料导航" onClick={onClose}><Icon name="close" /></button></div>
    <h2 id="knowledge-title">项目资料导航</h2><p className="dialog-description">{project.name} · {shortCommit(snapshot.commit)}<br />从原文提取文件、章节和函数，帮助确认资料范围。</p>
    <Notice>{error}</Notice>
    {!catalog && !error ? <Spinner label="读取项目资料" /> : catalog && <>
      <div className="knowledge-counts">{Object.entries(catalog.counts).map(([kind, count]) => <span key={kind}>{KINDS[kind]} <b>{count}</b></span>)}</div>
      <p className="knowledge-scope">{catalog.scope ? `仓库共 ${catalog.scope.tracked_file_count} 份已跟踪文件；本快照导入 ${catalog.files.length} 份，${catalog.scope.out_of_scope_count} 份不在选定范围。` : '此快照未记录全仓库文件数量，无法判断覆盖范围。'}</p>
      {catalog.scope?.selected_roots?.length > 0 && <details className="knowledge-roots"><summary>查看选定路径</summary>{catalog.scope.selected_roots.map((item) => <code key={item}>{item}</code>)}</details>}
      <div className="knowledge-gaps">{catalog.gaps.map((gap) => <p key={gap}>{gap}</p>)}</div>
      <div className="knowledge-files">{catalog.files.map((file) => <details key={file.path}><summary><span>{file.path}</span><small>{KINDS[file.kind]} · {file.line_count} 行</small></summary><div className="knowledge-anchors">{file.anchors.slice(0, 24).map((anchor) => <span key={`${anchor.label}:${anchor.start_line}`}>{anchor.label} · L{anchor.start_line}–{anchor.end_line}</span>)}{file.anchors.length > 24 && <span>其余 {file.anchors.length - 24} 个片段见原文</span>}<button className="button secondary" onClick={() => setPath(file.path)}>查看 {file.path} 原文</button></div></details>)}</div>
      {reading && <Spinner label="读取文件原文" />}
      {source && <section className="knowledge-source"><h3>{source.path}</h3><div className="code-lines" tabIndex={0} aria-label="导航文件原文"><pre>{sourceLines(source.content).map((line) => <div className="code-line" key={line.number}><span className="line-number">{line.number}</span><code>{line.text || ' '}</code></div>)}</pre></div></section>}
    </>}
  </dialog>;
}
