import React, { useEffect, useRef, useState } from 'react';
import { useSupportSession } from '../support-hooks.js';
import { Icon, Notice, Spinner } from './Ui.jsx';

function AnswerSources({ sources }) {
  if (!sources.length) return null;
  return <details className="chat-sources"><summary><Icon name="link" size={14} />参考依据 · {sources.length}</summary><div>{sources.map(source => <details className="chat-source" key={source.id}><summary><span>{source.kind === 'web' ? source.title : source.path}</span><small>{source.kind === 'web' ? source.scope === 'page_excerpt' ? '网页片段' : '搜索摘要' : '项目知识'}</small></summary><p>{source.content}</p>{source.kind === 'web' ? <a href={source.url} target="_blank" rel="noreferrer">打开来源<Icon name="arrow" size={13} /></a> : <small>快照 {source.snapshot_id} · 第 {source.start_line} 行<br />{source.sha256 && `SHA-256 ${source.sha256}`}</small>}</details>)}</div></details>;
}

function ChatTurn({ turn, projectId, onDiagnose }) {
  const answer = turn.response;
  return <article className="chat-turn"><div className="chat-question"><span>你</span><p>{turn.message}</p></div>{answer && <div className="chat-answer"><span className="chat-avatar"><Icon name="support" size={19} /></span><div className="chat-answer-body"><span className="chat-speaker">技术客服</span><p className="chat-reply">{answer.reply}</p>{answer.search.status !== 'not_requested' && <div className="chat-search-record"><Icon name="search" size={13} /><span>{answer.search.status === 'found' ? '已检索' : '尚未查证'} · {answer.search.query}</span></div>}
    <Notice tone="warning">{answer.search.notice}</Notice><AnswerSources sources={answer.sources} />
    {answer.warnings.length > 0 && <details className="chat-knowledge-note"><summary>资料时效说明</summary>{answer.warnings.map((warning, index) => <p key={index}>{warning}</p>)}</details>}
    {answer.needs_diagnosis && <button className="button subtle chat-diagnose" onClick={() => onDiagnose({ symptom: turn.message, projectId })}><Icon name="bug" size={16} />带着问题继续排查<Icon name="arrow" size={14} /></button>}
  </div></div>}</article>;
}

export default function SupportChat({ conversationId, settings, onUpdate, onOpen, onDiagnose, onUnauthorized }) {
  const [draftId] = useState(() => crypto.randomUUID());
  const id = conversationId || draftId;
  const state = useSupportSession(id, settings, onUpdate, onUnauthorized, { resource: 'conversations', draft: !conversationId });
  const form = useRef(null);
  const request = useRef(null);
  const [web, setWeb] = useState(false);
  const [validation, setValidation] = useState('');
  const turns = state.session?.turns || [];
  const latest = turns.at(-1);
  const chosenName = new URLSearchParams(window.location.search).get('project');
  const initialProject = settings.projects.find(item => item.name === chosenName)?.id || '';
  const awaiting = state.busy || state.session?.status === 'processing';
  const retryable = ['failed', 'interrupted'].includes(state.session?.status);
  const disabled = state.loading || awaiting || state.needsCheck || retryable || !settings.model_configured;
  useEffect(() => {
    if (latest?.status !== 'completed' || latest.request_id !== request.current?.request_id) return;
    form.current?.reset();
    setWeb(false);
    request.current = null;
    if (!conversationId) onOpen(id);
  }, [latest, conversationId, id, onOpen]);
  const submit = async event => {
    event.preventDefault();
    if (disabled) return;
    const data = new FormData(event.currentTarget);
    const message = String(data.get('message') || '').trim();
    const searchQuery = web ? String(data.get('search_query') || '').trim() : '';
    if (!message) { setValidation('先写下想咨询的问题。'); return; }
    setValidation('');
    const payload = { message, allow_web: web, search_query: searchQuery, project_id: state.session?.project_id || data.get('project_id') || null };
    if (!request.current || request.current.message !== message || request.current.allow_web !== web || request.current.search_query !== searchQuery) request.current = { ...payload, request_id: crypto.randomUUID() };
    // 请求前保存不含正文的地址，刷新后仍能找回已被服务接收的对话。
    const query = new URLSearchParams(window.location.search);
    query.set('conversation', id);
    window.history.replaceState({}, '', `?${query}`);
    await state.write('messages', request.current);
  };
  return <section className="support-card support-chat"><header className="chat-heading"><div><h1>把问题说清楚，一起理顺</h1><p>聊用法、聊原理，也聊遇到的难题。</p></div>{(turns.length > 0 || state.needsCheck || state.error) && <button className="button subtle" disabled={Boolean(awaiting || state.loading)} onClick={state.refresh}><Icon name="refresh" size={14} />检查最新状态</button>}</header>
    <Notice>{state.error || validation}</Notice>
    {state.loading && <div className="chat-loading"><Spinner label="读取对话" /></div>}
    <div className="chat-transcript" aria-label="咨询记录">{turns.map(turn => <ChatTurn key={turn.request_id} turn={turn} projectId={state.session.project_id} onDiagnose={onDiagnose} />)}</div>
    {awaiting && <div className="chat-loading" role="status"><Spinner label={web || latest?.search_query ? '正在查阅资料并整理回答' : '正在整理回答'} /></div>}
    {retryable && <div className="chat-retry"><Notice tone="warning">{state.session.status === 'interrupted' ? '上次回复中断了，问题已保留。' : state.session.error}</Notice><button className="button secondary" disabled={Boolean(state.busy || state.needsCheck)} onClick={() => state.write('retry')}>重试这条回复</button></div>}
    {!state.loading && <form ref={form} aria-label="发送咨询" className="chat-composer" onSubmit={submit}>
      {!turns.length ? <><div className="support-field chat-project"><label htmlFor="chat-project">关联项目 <span>可选</span></label><select key={initialProject} id="chat-project" name="project_id" defaultValue={initialProject} disabled={Boolean(disabled)}><option value="">通用咨询</option>{settings.projects.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div><div className="chat-starters"><span>也可以从这里聊起</span>{['这个项目能帮我做什么？', '用白话解释一下连接池', '软件一直转圈，该先看哪里？'].map(text => <button type="button" key={text} disabled={Boolean(disabled)} onClick={() => { form.current.elements.message.value = text; form.current.elements.message.focus(); }}>{text}<Icon name="arrow" size={13} /></button>)}</div></> : <p className="chat-project-label"><Icon name="books" size={13} />{settings.projects.find(item => item.id === state.session.project_id)?.name || '通用咨询'}<span>本次对话</span></p>}
      <div className="support-field chat-message-field"><label htmlFor="chat-message">{turns.length ? '继续聊聊' : '想了解什么？'}</label><textarea id="chat-message" name="message" maxLength={4000} rows={3} required disabled={Boolean(disabled)} placeholder="不用整理成报告，像平时聊天一样描述就好。" /></div>
      <div className="chat-composer-actions"><label className="chat-web-toggle"><input type="checkbox" checked={web} disabled={Boolean(disabled)} onChange={event => setWeb(event.target.checked)} /><Icon name="search" size={15} />联网查证</label><button className="button primary" disabled={Boolean(disabled)}>发送<Icon name="arrow" size={16} /></button></div>
      {web && <div className="support-field chat-web-query"><label htmlFor="chat-search">搜索提示词 <span>可选</span></label><input id="chat-search" name="search_query" maxLength={160} disabled={Boolean(disabled)} placeholder="留空则由客服自行判断要查什么" /><p className="support-form-note">开启后客服会在需要时效性或最新公开资料时自行联网检索并附来源；这里可填公开技术名词作提示，留空也可以。</p></div>}
    </form>}
  </section>;
}
