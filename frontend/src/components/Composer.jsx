import React, { useRef } from 'react';
import { Icon, Notice, Spinner } from './Ui.jsx';

export default function Composer({ draft, setDraft, samples, busy, disabled, providerConfigured, onAsk, onSearch, error, onDismissError, hasProject }) {
  const input = useRef(null);
  const length = Array.from(draft).length;
  const fill = (question) => { setDraft(question); input.current?.focus(); };
  return <section className="composer card" aria-labelledby="composer-title">
    <div className="section-topline"><div className="section-heading"><span className="square-icon"><Icon name="search" size={17} /></span><h2 id="composer-title">向项目提问</h2></div><span className="subtle-label">基于所选快照</span></div>
    <form onSubmit={(event) => { event.preventDefault(); onAsk(); }}>
      <label htmlFor="question-input">关于这个项目，你想了解什么？</label>
      <textarea ref={input} id="question-input" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="例如：任务失败后如何恢复？重试次数在哪里限制？" rows={4} aria-describedby="question-help" aria-invalid={Boolean(error)} disabled={busy} />
      <div className="composer-bottom"><span id="question-help" className={`character-count ${length > 2000 ? 'over-limit' : ''}`}>{length} / 2000<span> · 至少 8 字</span></span><div className="composer-actions"><button type="button" className="button secondary" onClick={onSearch} disabled={busy || disabled || !draft.trim()}>{busy === 'search' ? <Spinner label="检索中" /> : <><Icon name="search" size={16} />仅检索</>}</button><button type="submit" className="button primary" disabled={busy || disabled || !providerConfigured || !draft.trim()}>{busy === 'ask' ? <Spinner label="提交中" /> : <>提问<Icon name="arrow" size={17} /></>}</button></div></div>
    </form>
    <Notice onDismiss={onDismissError}>{error}</Notice>
    {!hasProject && <p className="composer-hint">先导入一个项目快照，再用问题定位代码和文档。</p>}
    {hasProject && !providerConfigured && <p className="composer-hint">回答模型尚未配置，可先使用「仅检索」查看项目证据。</p>}
    {samples.length > 0 && <div className="sample-section"><div className="sample-heading"><span>从这些问题开始</span><span>点击填入，不会自动运行</span></div><div className="sample-list">{samples.slice(0, 3).map((sample) => <button type="button" className="sample-button" key={sample.id} title={sample.description} onClick={() => fill(sample.question)} disabled={busy}><span>{sample.title}</span><Icon name="arrow" size={14} /></button>)}</div>{samples.length > 3 && <details className="more-samples"><summary>更多示例问题（{samples.length - 3}）</summary><div className="sample-list">{samples.slice(3).map((sample) => <button type="button" className="sample-button" key={sample.id} title={sample.description} onClick={() => fill(sample.question)} disabled={busy}>{sample.title}<Icon name="arrow" size={14} /></button>)}</div></details>}</div>}
  </section>;
}
