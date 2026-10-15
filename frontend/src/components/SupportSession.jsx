import React, { useRef, useState } from 'react';
import { useSupportSession } from '../support-hooks.js';
import { SUPPORT_STATUS, supplementError, supportTime } from '../lib/support.js';
import { Elapsed, Icon, Notice, Reveal, Spinner } from './Ui.jsx';
import SupportReport from './SupportReport.jsx';
import SupportProgress from './SupportProgress.jsx';

function SupplementForm({ session, settings, disabled, busy, onSubmit }) {
  const [error, setError] = useState('');
  const [logBytes, setLogBytes] = useState(0);
  const submit = async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const payload = Object.fromEntries(['product_version', 'reproduction', 'log_text'].map(name => [name, String(data.get(name) || '').trim()]));
    const invalid = supplementError(payload, settings.log_limit);
    setError(invalid);
    if (!invalid && await onSubmit('supplement', payload)) { form.reset(); setLogBytes(0); }
  };
  const missing = session.pending?.missing || [];
  return <section className="support-card support-materials">
    <div className="support-section-heading"><div className="support-report-title"><span className="support-report-mark"><Icon name="terminal" size={25} /></span><div><span className="eyebrow">排查材料</span><h2>{session.report ? '继续补充线索' : '补充环境与复现步骤'}</h2></div></div></div>
    <p className="support-muted">先提供版本和可重复的操作。日志有助于缩小问题范围，可稍后补充。</p>
    <form aria-label="补充排查材料" onSubmit={submit}>
      <div className="support-material-grid"><div>
      <div className="support-field"><label htmlFor="support-version">软件版本与运行环境 {missing.includes('product_version') && <span>待补充</span>}</label><input id="support-version" name="product_version" maxLength={200} defaultValue={session.product_version || ''} placeholder="例如 v1.2.0，Windows 11，升级后出现" /></div>
      <div className="support-field"><label htmlFor="support-reproduction">复现步骤 {missing.includes('reproduction') && <span>待补充</span>}</label><textarea id="support-reproduction" name="reproduction" maxLength={4000} rows={3} defaultValue={session.reproduction || ''} placeholder="做了什么、预期结果、实际发生了什么？" /></div>
      </div>
      <div className="support-field"><label htmlFor="support-log">故障附近日志 <span>可选</span></label><textarea id="support-log" name="log_text" rows={5} className="support-log-input" onInput={event => setLogBytes(new TextEncoder().encode(event.currentTarget.value).length)} placeholder="粘贴时间、错误码和调用栈；提交前先去除密码、令牌与私人资料。" /><div className="support-log-hint"><span>本次提交会替换之前的日志片段。</span><span className={logBytes > settings.log_limit ? 'support-over-limit' : ''}>{logBytes} / {settings.log_limit} 字节</span></div></div>
      </div>
      {session.pending?.log_guidance && <details className="support-guidance"><summary>日志采集指引</summary><p>{session.pending.log_guidance}</p></details>}
      <Notice>{error}</Notice>
      <button className="button primary" type="submit" disabled={disabled || !settings.model_configured}>{busy === 'supplement' ? <Spinner label="正在提交与分析" /> : <><Icon name="arrow" size={16} />提交排查材料</>}</button>
    </form>
  </section>;
}

function Confirmation({ disabled, busy, onConfirm }) {
  const form = useRef(null);
  const confirm = resolved => onConfirm('confirm', { resolved, resolution_note: String(new FormData(form.current).get('resolution_note') || '').trim() });
  return <section className="support-card support-confirmation" aria-label="确认排查结果"><h2>建议验证后，记录结果。</h2><p className="support-muted">确认解决后，这次处理会保存为可复用的案例。</p><form ref={form} onSubmit={event => event.preventDefault()}><div className="support-field"><label htmlFor="support-resolution">实际操作与复测结果</label><textarea id="support-resolution" name="resolution_note" maxLength={2000} rows={2} placeholder="例如调整连接池后重复三次任务均成功；或记录仍然出现的问题。" /></div><div className="support-actions"><button className="button primary" type="button" disabled={disabled} onClick={() => confirm(true)}>{busy === 'confirm' ? <Spinner label="保存中" /> : '确认已解决'}</button><button className="button secondary" type="button" disabled={disabled} onClick={() => confirm(false)}>仍有问题</button></div></form></section>;
}

export default function SupportSession({ threadId, settings, onUpdate, onUnauthorized }) {
  const state = useSupportSession(threadId, settings, onUpdate, onUnauthorized);
  const { session, loading, busy, error, needsCheck, refresh, write } = state;
  const disabled = Boolean(busy || needsCheck);
  const project = settings.projects.find(item => item.id === session?.project_id);
  return <Reveal className="support-detail" aria-busy={Boolean(busy)}>
    {error && <Notice action={<button className="inline-button" disabled={Boolean(busy || loading)} onClick={refresh}>检查最新状态</button>}>{error}</Notice>}
    {!session && (loading ? <div className="support-card support-placeholder"><Spinner label="恢复客服会话" /></div> : <div className="support-card support-placeholder"><h2>会话暂时无法读取</h2><p>检查连接后重新读取，或从历史选择其他问题。</p><button className="button secondary" onClick={refresh}>检查最新状态</button></div>)}
    {session && <>
      <header className="support-ticket-heading"><div className="support-ticket-meta"><span>{project?.name || (session.project_id ? '原项目暂不在列表' : '未关联项目')}</span><time>{supportTime(session.created_at)}</time><span className={`support-status ${session.status}`}>{SUPPORT_STATUS[session.status] || session.status}</span></div><h1>{session.symptom}</h1><div className="support-ticket-tools"><span className="support-muted">{session.product_version || '版本待补充'}</span><button className="button subtle" disabled={Boolean(busy || loading)} onClick={refresh}><Icon name="refresh" size={14} />检查最新状态</button></div></header>
      <SupportProgress status={session.status} />
      {busy && <div className="support-processing" role="status"><span className="support-processing-bars" aria-hidden="true"><i /><i /><i /><i /></span><div><strong>正在处理这次排查</strong><p>请求已提交，请保持页面打开；分析可能需要几分钟。<Elapsed /></p></div></div>}
      {session.status === 'processing' && !busy && <Notice tone="info" action={<button className="inline-button" disabled={disabled || !settings.model_configured} onClick={() => write('retry')}>恢复处理</button>}>服务正在处理，页面会自动查询状态。长时间没有变化时可恢复处理。</Notice>}
      {session.status === 'failed' && <Notice action={<button className="inline-button" disabled={disabled || !settings.model_configured} onClick={() => write('retry')}>重试分析</button>}>{session.error || '分析未完成，材料已保存在会话中。'}</Notice>}
      {session.status === 'unresolved' && <Notice tone="warning">本次问题仍未解决。补充新的复现结果或日志，继续排查。</Notice>}
      <SupportReport session={session} />
      {session.status === 'awaiting_confirmation' && <Confirmation disabled={disabled} busy={busy} onConfirm={write} />}
      {session.status === 'awaiting_confirmation' ? <details className="support-additional"><summary><Icon name="plus" size={16} /><span>需要更多线索？继续补充材料</span><Icon name="chevron" size={15} /></summary><SupplementForm key={session.updated_at || 'materials'} session={session} settings={settings} disabled={disabled} busy={busy} onSubmit={write} /></details> : ['awaiting_info', 'unresolved', 'failed'].includes(session.status) && <SupplementForm key={session.updated_at || 'materials'} session={session} settings={settings} disabled={disabled} busy={busy} onSubmit={write} />}
      {(session.reproduction || session.masked_log) && <details className="support-card support-saved"><summary>查看已保存的排查材料</summary>{session.reproduction && <><h3>复现步骤</h3><p>{session.reproduction}</p></>}{session.masked_log && <><h3>日志文本</h3><pre>{session.masked_log}</pre><p className="support-muted">已按常见规则处理凭据；脱敏仍可能遗漏私人信息。</p></>}</details>}
    </>}
  </Reveal>;
}
