import React, { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api.js';
import { needsStatusCheck, SUPPORT_STATUS, supportTime } from '../lib/support.js';
import { useSupportCatalog } from '../support-hooks.js';
import { Brand, Icon, Notice, Reveal, Spinner } from './Ui.jsx';
import SupportSession from './SupportSession.jsx';
import SupportProgress from './SupportProgress.jsx';
import SupportChat from './SupportChat.jsx';

function IntakeNote() {
  return <aside className="support-intake-note" aria-label="排查提示"><h2>排查提示</h2><p>先描述现象，再补充发生环境与日志。</p><ul>
    <li><Icon name="books" size={19} /><div><strong>关联项目知识</strong><span>从已发布的资料里寻找依据。</span></div></li>
    <li><Icon name="link" size={19} /><div><strong>回到引用核对</strong><span>逐条展开日志与知识片段。</span></div></li>
    <li><Icon name="verified" size={19} /><div><strong>验证后保存案例</strong><span>留下实际操作与复测结果。</span></div></li>
  </ul><div className="support-note-foot"><Icon name="lock" size={13} /><span>提交前检查私人信息。常见凭据会在保存前脱敏。</span></div></aside>;
}

function NewIssue({ settings, onCreated, onCheck, onUnauthorized, seed }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const scope = useRef(null);
  useEffect(() => { scope.current = new AbortController(); return () => scope.current.abort(); }, []);
  const initialProject = new URLSearchParams(window.location.search).get('project');
  const project = settings.projects.find(item => item.id === seed?.projectId || item.name === initialProject);
  const submit = async event => {
    event.preventDefault();
    if (busy || uncertain) return;
    const data = new FormData(event.currentTarget);
    const symptom = String(data.get('symptom') || '').trim();
    if (Array.from(symptom).length < 8) { setError('请用至少 8 个字符描述实际遇到的问题。'); return; }
    const signal = scope.current.signal;
    setBusy(true);
    setError('');
    try {
      const next = await api('/support/sessions', { method: 'POST', body: { symptom, project_id: data.get('project_id') || null }, signal });
      if (!signal.aborted) onCreated(next);
    } catch (failure) {
      if (signal.aborted) return;
      if (failure.status === 401) onUnauthorized();
      setUncertain(needsStatusCheck(failure));
      setError(failure.status === 0 ? '创建结果尚未确认。请先检查最近问题，避免重复创建。' : failure.message);
    } finally { if (!signal.aborted) setBusy(false); }
  };
  return <Reveal as="section" className="support-card support-new"><div className="support-new-heading"><div><h1>新建问题</h1><p className="support-muted">记录遇到的现象，结合项目知识与日志排查。</p></div></div><SupportProgress /><div className="support-start-layout"><form aria-label="创建客服问题" onSubmit={submit}>
    <div className="support-field"><label htmlFor="support-project">关联项目 <span>可选</span></label><select key={project?.id || 'none'} id="support-project" name="project_id" defaultValue={project?.id || ''}><option value="">通用问题／暂不关联项目</option>{settings.projects.map(item => <option key={item.id} value={item.id}>{item.name}{item.knowledge_available === false ? '（知识待归纳）' : ''}</option>)}</select></div>
    <div className="support-field"><label htmlFor="support-symptom">遇到了什么问题？</label><textarea id="support-symptom" name="symptom" required minLength={8} maxLength={4000} rows={5} defaultValue={seed?.symptom || ''} placeholder="例如：升级后任务一直排队。重启后仍未恢复，之前同样操作可以完成。" /></div>
    <Notice>{error}</Notice>
    {uncertain && <div className="support-actions"><button className="button secondary" type="button" onClick={onCheck}>检查最近问题</button><button className="button subtle" type="button" onClick={() => setUncertain(false)}>确认未创建，重新提交</button></div>}
    <button className="button primary" disabled={busy || uncertain}>{busy ? <Spinner label="创建会话中" /> : <>开始排查<Icon name="arrow" size={18} /></>}</button>
    <p className="support-form-note">下一步补充版本、复现与日志。确认解决后再保存为案例。</p>
  </form><IntakeNote /></div></Reveal>;
}

function SupportHistory({ catalog, threadId, onSelect, open, setOpen }) {
  const [tab, setTab] = useState('sessions');
  const items = tab === 'cases' ? catalog.cases : catalog.sessions;
  return <details className="support-history support-card" open={open} onToggle={event => setOpen(event.currentTarget.open)}><summary><Icon name="clock" size={16} />问题与案例<span>{catalog.sessions.length}</span><Icon name="chevron" size={14} /></summary><div className="support-history-body"><div className="support-history-tabs" role="group" aria-label="客服历史视图"><button aria-pressed={tab === 'sessions'} onClick={() => setTab('sessions')}>最近问题</button><button aria-pressed={tab === 'cases'} onClick={() => setTab('cases')}>确认案例</button><button aria-label="刷新最近问题" className="icon-button" disabled={catalog.loading} onClick={catalog.refresh}><Icon name="refresh" size={15} /></button></div>
    {catalog.loading && !items.length ? <Spinner label="读取历史" /> : !items.length ? <div className="support-history-empty"><span className="support-empty-mark"><Icon name={tab === 'cases' ? 'check' : 'search'} size={20} /></span><strong>{catalog.error ? '暂时无法读取' : tab === 'cases' ? '等待第一个确认案例' : '还没有排查记录'}</strong><p>{catalog.error ? '检查连接后，重新读取列表。' : tab === 'cases' ? '问题验证解决后，案例会出现在这里。' : '新建一个问题，排查过程会保存在这里。'}</p></div> : <ul className="support-history-list">{items.map(item => <li key={item.thread_id}><button aria-current={threadId === item.thread_id ? 'page' : undefined} onClick={() => onSelect(item.thread_id)}><strong>{item.symptom}</strong><span><span>{SUPPORT_STATUS[item.status] || (tab === 'cases' ? '已确认解决' : item.status)}</span><time>{supportTime(item.created_at)}</time></span></button></li>)}</ul>}
    <p className="support-history-note">{tab === 'cases' ? '保存用户确认的处理结果；原因假设仍需核实。' : '最近 100 条会话，刷新页面后可继续。'}</p>
  </div></details>;
}

export default function Support({ onUnauthorized, onLogout }) {
  const [threadId, setThreadId] = useState(() => new URLSearchParams(window.location.search).get('thread'));
  const [conversationId, setConversationId] = useState(() => new URLSearchParams(window.location.search).get('conversation'));
  const [mode, setMode] = useState(() => threadId || new URLSearchParams(window.location.search).get('mode') === 'diagnose' ? 'diagnose' : 'chat');
  const catalog = useSupportCatalog(onUnauthorized, mode === 'chat');
  const [issueSeed, setIssueSeed] = useState(null);
  const [historyOpen, setHistoryOpen] = useState(() => window.innerWidth > 720);
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { const sync = () => { const query = new URLSearchParams(window.location.search); setThreadId(query.get('thread')); setConversationId(query.get('conversation')); setMode(query.get('thread') || query.get('mode') === 'diagnose' ? 'diagnose' : 'chat'); }; window.addEventListener('popstate', sync); return () => window.removeEventListener('popstate', sync); }, []);
  const select = (id, target = 'diagnose') => {
    const query = new URLSearchParams({ view: 'support' });
    const project = new URLSearchParams(window.location.search).get('project');
    if (project) query.set('project', project);
    if (target === 'diagnose') query.set('mode', 'diagnose');
    if (id) query.set(target === 'diagnose' ? 'thread' : 'conversation', id);
    window.history.pushState({}, '', `?${query}`);
    setThreadId(target === 'diagnose' ? id : null);
    setConversationId(target === 'chat' ? id : null);
    setMode(target);
    if (window.innerWidth <= 720) setHistoryOpen(false);
  };
  const logout = async () => { setLogoutBusy(true); try { await onLogout(); } catch (failure) { setError(failure.message); } finally { setLogoutBusy(false); } };
  return <div className="knowledge-shell support-shell"><a className="skip-link" href="#support-main">跳到排查内容</a><header className="knowledge-topbar"><Brand /><nav className="support-topnav" aria-label="工作台导航"><a className="button subtle" href="/"><Icon name="books" />知识库</a><a className="button subtle" href="?view=support" aria-current="page"><Icon name="support" />技术客服</a><button className="button subtle" disabled={logoutBusy} onClick={logout}><Icon name="logout" />退出</button></nav></header><main className="support-main"><div className="support-page-intro"><div><Icon name="support" size={18} /><span>工作空间</span><Icon name="chevron" size={12} /><strong>技术客服</strong></div><span className={`support-availability ${catalog.settings.model_configured ? 'ready' : ''}`}><span />{catalog.loading ? '正在读取服务状态' : catalog.settings.model_configured ? '客服分析可用' : '客服分析尚未就绪'}</span></div>
    <p className="support-grounding">关联项目后，答疑与排查都会引用你的项目知识库作为依据；没有依据时如实说明。</p>
    <Notice>{error}</Notice><Notice action={<button className="inline-button" disabled={catalog.loading} onClick={catalog.refresh}>重新连接</button>}>{catalog.error}</Notice>
    {!catalog.loading && !catalog.settings.model_configured && <Notice tone="warning">客服分析尚未就绪，可以先创建问题。模型配置完成后再提交排查材料。</Notice>}
    <div className="support-mode-switch" role="group" aria-label="客服方式"><button aria-pressed={mode === 'chat'} onClick={() => select(null, 'chat')}><Icon name="chat" size={16} />日常答疑</button><button aria-pressed={mode === 'diagnose'} onClick={() => { setIssueSeed(null); select(null); }}><Icon name="bug" size={16} />故障排查</button></div>
    <p className="support-mode-hint">{mode === 'chat' ? '白话问用法与原理；需要时联网查公开资料，回答可引用项目知识。' : '补充版本与日志，带引用定位问题；确认解决后存为可复用案例。'}</p>
    <div className="support-layout"><aside className="support-sidebar"><button className="button secondary support-new-button" onClick={() => { setIssueSeed(null); select(null, mode); }}><Icon name="plus" size={19} />{mode === 'chat' ? '新对话' : '新问题'}<Icon name="chat" size={17} /></button>{mode === 'chat' ? <details className="support-history support-card" open={historyOpen} onToggle={event => setHistoryOpen(event.currentTarget.open)}><summary><Icon name="clock" size={16} />最近咨询<span>{catalog.conversations.length}</span><Icon name="chevron" size={14} /></summary><div className="support-history-body">{catalog.conversations.length ? <ul className="support-history-list">{catalog.conversations.map(item => <li key={item.id}><button aria-current={conversationId === item.id ? 'page' : undefined} onClick={() => select(item.id, 'chat')}><strong>{item.title}</strong><span><span>咨询</span><time>{supportTime(item.updated_at)}</time></span></button></li>)}</ul> : <div className="support-history-empty"><span className="support-empty-mark"><Icon name="chat" size={20} /></span><strong>{catalog.loading ? '正在读取' : '从一个小问题开始'}</strong><p>聊过的内容会保存在这里。</p></div>}<button className="button subtle" disabled={catalog.loading} onClick={catalog.refresh}><Icon name="refresh" size={14} />刷新记录</button></div></details> : <SupportHistory catalog={catalog} threadId={threadId} onSelect={select} open={historyOpen} setOpen={setHistoryOpen} />}<div className="support-sidebar-foot"><Icon name="lock" size={19} /><div><strong>你的技术问答空间</strong><p>材料与结果随会话保存，随时回来继续。</p></div></div></aside><div id="support-main" className="support-content" tabIndex={-1}>{mode === 'chat' ? <SupportChat key={conversationId || 'new-chat'} conversationId={conversationId} settings={catalog.settings} onUpdate={catalog.updateConversation} onOpen={id => select(id, 'chat')} onDiagnose={seed => { setIssueSeed(seed); select(null); }} onUnauthorized={onUnauthorized} /> : threadId ? <SupportSession key={threadId} threadId={threadId} settings={catalog.settings} onUpdate={catalog.updateSession} onUnauthorized={onUnauthorized} /> : <NewIssue seed={issueSeed} settings={catalog.settings} onCreated={next => { catalog.updateSession(next); select(next.thread_id); }} onCheck={catalog.refresh} onUnauthorized={onUnauthorized} />}</div></div>
    <footer className="knowledge-footer">分析建议需结合实际环境验证。提交前，请检查材料中的私人信息。</footer>
  </main></div>;
}
