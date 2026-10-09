import React from 'react';
import { formatDate, isPending, shortCommit } from '../lib/domain.js';
import { Icon, Notice, Spinner, StatusBadge } from './Ui.jsx';

function ExecutionDetails({ question }) {
  const usage = question.usage;
  return <details className="execution-details"><summary><Icon name="clock" size={14} />执行记录{question.latency_ms != null && <span>{(question.latency_ms / 1000).toFixed(1)} 秒</span>}</summary><div className="execution-body">
    <dl className="run-facts"><div><dt>检索方式</dt><dd>{question.method || '等待检索'}</dd></div><div><dt>重启恢复</dt><dd>{question.recovery_count ?? 0} 次</dd></div><div><dt>模型用量</dt><dd>{usage?.total_tokens != null ? `${usage.total_tokens.toLocaleString()} tokens` : '暂无用量记录'}</dd></div>{usage?.unreported_calls > 0 && <div><dt>未报告用量</dt><dd>{usage.unreported_calls} 次调用</dd></div>}</dl>
    {question.attempts?.length > 0 && <div className="attempt-list">{question.attempts.map((attempt, index) => <div className="attempt" key={index}><div><code>{attempt.model}</code><span>{attempt.status} {attempt.latency_ms != null ? `· ${(attempt.latency_ms / 1000).toFixed(1)} 秒` : ''}</span></div>{attempt.error && <p>{attempt.error}</p>}</div>)}</div>}
    {question.events?.length > 0 && <ol className="event-list">{question.events.map((event) => <li key={event.id}><time dateTime={event.created_at}>{formatDate(event.created_at)}</time><span>{event.message}</span></li>)}</ol>}
  </div></details>;
}

function AnswerClaims({ result, sources, onSource }) {
  return <div className="answer-content">
    {result.status === 'insufficient' && <Notice tone="warning">现有证据不足以完整回答。以下只展示能够确认的部分。</Notice>}
    {result.status === 'conflict' && <Notice tone="warning">项目资料存在冲突，请结合双方引用核对后再作判断。</Notice>}
    <p className="answer-summary">{result.summary}</p>
    {result.claims?.length > 0 && <ol className="claim-list">{result.claims.map((claim, index) => <li key={index}><div className="claim-number">{String(index + 1).padStart(2, '0')}</div><div className="claim-body"><p>{claim.text}</p>{claim.evidence?.map((evidence, evidenceIndex) => {
      const source = sources.find((item) => item.id === evidence.source_id);
      return <div className="claim-evidence" key={`${evidence.source_id}-${evidenceIndex}`}><button type="button" className="citation-button" disabled={!source} onClick={() => onSource(source.id)}><span className="source-id">{evidence.source_id}</span><span>{source ? source.path : '引用不可用'}</span>{source && <span className="citation-lines">L{source.start_line}–{source.end_line}</span>}<Icon name="chevron" size={13} /></button><blockquote>{evidence.quote}</blockquote></div>;
    })}</div></li>)}</ol>}
    {result.gaps?.length > 0 && <div className="gaps"><h3>还需要哪些信息</h3><ul>{result.gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul></div>}
  </div>;
}

function QuestionBody({ question, onSource, onRetry, onCancel, actionBusy }) {
  if (isPending(question.status)) return <div className="pending-answer" role="status"><div className="pending-orbit"><span className="spinner" /></div><h3>{question.status === 'queued' ? '问题已保存，等待处理' : question.status === 'retrieving' ? '正在定位相关代码与文档' : '正在依据证据整理回答'}</h3><p>可以离开页面，回来后继续查看。</p><ol className="progress-steps">{[['queued', '排队'], ['retrieving', '检索'], ['generating', '回答']].map(([status, label], index) => <li key={status} className={['queued', 'retrieving', 'generating'].indexOf(question.status) >= index ? 'current' : ''}><span>{index + 1}</span>{label}</li>)}</ol><button className="button text-button" onClick={onCancel} disabled={actionBusy}>{actionBusy === 'cancel' ? <Spinner label="取消中" /> : '取消本次提问'}</button></div>;
  if (question.status === 'failed') return <div className="failure-state"><Notice>{question.error || '本次处理未能完成。已保存问题与执行记录。'}</Notice><p>当前问题和已有证据已保留，可手动续跑。</p><button className="button secondary" onClick={onRetry} disabled={actionBusy}>{actionBusy === 'retry' ? <Spinner label="提交中" /> : <><Icon name="refresh" size={16} />续跑本次提问</>}</button></div>;
  if (question.status === 'cancelled') return <div className="cancelled-state"><h3>本次提问已取消</h3><p>记录仍保留在历史中。修改上方问题后可以再次提问。</p></div>;
  if (question.result) return <AnswerClaims result={question.result} sources={question.sources || []} onSource={onSource} />;
  return <Notice tone="warning">暂未获得可展示的回答，请查看执行记录。</Notice>;
}

export default function Answer({ question, search, loading, connectionError, currentSnapshotId, onSource, onRetry, onCancel, onRefresh, onNew, actionBusy, hasProject }) {
  if (loading && !question) return <section className="card answer-card loading-card" aria-live="polite"><Spinner label="读取已保存的问答" /></section>;
  if (search) return <section className="card answer-card"><div className="section-topline"><div className="section-heading"><span className="square-icon"><Icon name="search" size={17} /></span><h2>检索结果</h2></div><span className="result-count">{search.sources.length} 条证据</span></div><p className="search-question">{search.query}</p><div className="search-mode-note"><Icon name="check" size={14} /><span>仅检索项目资料，本次未调用回答模型。</span></div>{search.warnings?.map((warning, index) => <Notice key={index} tone="warning">{warning}</Notice>)}{search.sources.length ? <div className="search-results">{search.sources.map((source) => <button key={source.id} className="search-result" onClick={() => onSource(source.id)}><div><span className="source-id">{source.id}</span><strong>{source.path}</strong><Icon name="chevron" size={15} /></div><p>{source.content.slice(0, 210)}{source.content.length > 210 ? '…' : ''}</p><span className="search-result-meta">L{source.start_line}–{source.end_line}<span>·</span>{source.symbol || '文本片段'}<span>·</span>{shortCommit(source.commit)}</span></button>)}</div> : <div className="empty-answer"><Icon name="search" size={25} /><h3>没有找到相关证据</h3><p>试试文件名、函数名或项目里使用的具体词语。</p></div>}</section>;
  if (!question) return <section className="card answer-card"><Notice action={<button className="inline-button" onClick={onRefresh}>重新读取</button>}>{connectionError}</Notice><div className="empty-answer"><div className="empty-illustration"><Icon name="file" size={28} /><span><Icon name="link" size={15} /></span></div><h3>{hasProject ? '让问题有答案，让答案有出处。' : '你的项目知识，从一份快照开始。'}</h3><p>{hasProject ? '提问后，回答和引用会出现在这里。\n也可以先检索，直接查看最相关的原文。' : '导入已提交的代码与文档，\n在同一份版本上下文里查找答案。'}</p><div className="empty-steps"><span><b>01</b>选择快照</span><Icon name="chevron" size={13} /><span><b>02</b>提出问题</span><Icon name="chevron" size={13} /><span><b>03</b>核对证据</span></div></div></section>;
  return <section className="card answer-card" aria-labelledby="answer-title">
    <div className="section-topline"><div className="section-heading"><span className="square-icon"><Icon name="file" size={17} /></span><h2 id="answer-title">项目回答</h2></div><div className="answer-header-actions"><StatusBadge status={question.status} />{question.status === 'completed' && question.result && <a className="icon-button" href={`/api/questions/${encodeURIComponent(question.id)}/export`} download aria-label="导出 Markdown" title="导出 Markdown"><Icon name="download" size={17} /></a>}</div></div>
    <div className="asked-question"><p>{question.question}</p><div><time dateTime={question.created_at}>{formatDate(question.created_at)}</time><span>·</span><code>{shortCommit(question.commit)}</code><button className="inline-button" onClick={onNew}>新建提问</button></div></div>
    {(question.stale || (currentSnapshotId && question.snapshot_id !== currentSnapshotId)) && <Notice tone="info">这条问答基于历史快照 {shortCommit(question.commit)}，引用仍指向当时的原文件。</Notice>}
    <Notice tone="warning" action={<button className="inline-button" onClick={onRefresh}>立即重连</button>}>{connectionError && `状态连接暂时中断，正在自动重试。${connectionError}`}</Notice>
    <QuestionBody question={question} onSource={onSource} onRetry={onRetry} onCancel={onCancel} actionBusy={actionBusy} />
    <ExecutionDetails question={question} />
  </section>;
}
