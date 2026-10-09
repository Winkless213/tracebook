import React, { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api.js';
import { shortCommit, validateSnapshot } from '../lib/domain.js';
import { Icon, Notice, Spinner } from './Ui.jsx';

export default function ImportDialog({ onClose, onImported, onUnauthorized }) {
  const dialog = useRef(null);
  const [payload, setPayload] = useState(null);
  const [fileName, setFileName] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const readId = useRef(0);
  useEffect(() => { dialog.current.showModal(); }, []);
  async function selectFile(event) {
    const file = event.target.files?.[0];
    const current = ++readId.current;
    setPayload(null);
    setError('');
    setFileName(file?.name || '');
    if (!file) return;
    if (file.size > 3_000_000) { setError('JSON 文件不能超过 3 MB。'); return; }
    setReading(true);
    try {
      const text = await file.text();
      if (current !== readId.current) return;
      let parsed;
      try { parsed = JSON.parse(text.replace(/^\uFEFF/, '')); }
      catch { throw new Error('无法读取 JSON，请选择 UTF-8 编码的项目快照文件。'); }
      const invalid = validateSnapshot(parsed);
      if (invalid) throw new Error(invalid);
      setPayload(parsed);
    } catch (failure) { if (current === readId.current) setError(failure.message); }
    finally { if (current === readId.current) setReading(false); }
  }
  async function submit(event) {
    event.preventDefault();
    if (!payload) return;
    setBusy(true);
    setError('');
    try {
      const result = await api('/import', { method: 'POST', body: payload, timeoutMs: 60000 });
      await onImported(result, payload);
      onClose();
    } catch (failure) { if (failure.status === 401) onUnauthorized(); setError(failure.message); }
    finally { setBusy(false); }
  }
  return <dialog ref={dialog} className="import-dialog" aria-labelledby="import-title" onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}>
    <div className="dialog-header"><span className="square-icon"><Icon name="upload" size={19} /></span><button className="icon-button" aria-label="关闭导入窗口" onClick={onClose} disabled={busy}><Icon name="close" /></button></div>
    <h2 id="import-title">导入项目快照</h2><p className="dialog-description">让代码和文档共享同一个版本上下文。</p>
    <form onSubmit={submit}>
      <label className="upload-zone" htmlFor="snapshot-file"><Icon name="upload" size={25} /><strong>{fileName || '选择 JSON 快照文件'}</strong><span>最多 200 份文件 · 文本总量不超过 2 MB</span><input id="snapshot-file" type="file" accept=".json,application/json" onChange={selectFile} disabled={busy} /></label>
      <p className="import-format-note">使用项目快照导出工具生成的 schema 1 文件；服务不会克隆仓库或执行导入的代码。</p>
      {reading && <Spinner label="读取并校验快照" />}
      {payload && <dl className="import-preview"><div><dt>项目</dt><dd>{payload.project}</dd></div><div><dt>分支</dt><dd>{payload.branch}</dd></div><div><dt>提交版本</dt><dd><code>{shortCommit(payload.commit)}</code></dd></div><div><dt>导入文件</dt><dd>{payload.files.length} 份</dd></div>{payload.excluded.length > 0 && <div><dt>导出时已排除</dt><dd>{payload.excluded.length} 份</dd></div>}</dl>}
      <Notice>{error}</Notice>
      <div className="import-privacy"><Icon name="lock" size={17} /><p>提问时，相关资料片段会发送给已配置的回答模型。导入快照本身不会调用模型。</p></div>
      <div className="dialog-actions"><button type="button" className="button secondary" onClick={onClose} disabled={busy}>取消</button><button className="button primary" disabled={!payload || busy || reading}>{busy ? <Spinner label="导入中" /> : <><Icon name="upload" size={16} />导入快照</>}</button></div>
    </form>
  </dialog>;
}
