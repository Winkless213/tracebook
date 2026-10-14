import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../lib/api.js';
import { Brand, Icon, Notice, Reveal, Spinner } from './Ui.jsx';

const documentNames = { 'CODEBASE.md': '开发指南', 'MODULES.md': '模块详解',
  'SESSION-TOPICS.md': '会话主题', 'SESSIONS.md': '开发经验' };

function historyDescription(project) {
  if (project.history_status === 'ready') return `已归纳 ${project.history_sessions} 份会话，提取 ${project.history_items} 条开发经验。`;
  if (project.history_status === 'reviewed') return `已分析 ${project.history_sessions} 份会话，暂无可复用的开发经验。`;
  if (project.history_status === 'no_sources') return '尚无已关联的会话来源。';
  if (project.topics_status === 'ready') return `已分类 ${project.history_sessions} 份会话，开发经验待归纳。`;
  if (project.history_sessions) return `已关联 ${project.history_sessions} 份会话，等待分类与归纳。`;
  return '会话来源正在核对。';
}

export function KnowledgeOverview({ data }) {
  const projects = data.projects || [];
  return <>
    <div className="knowledge-totals" aria-label="知识覆盖情况">
      <div><Icon name="layers" size={26} /><span>已接入项目</span><strong>{projects.length}</strong></div>
      <div><Icon name="books" size={26} /><span>代码归纳完成</span><strong>{data.ready || 0}</strong></div>
      <div><Icon name="clock" size={26} /><span>会话归纳完成</span><strong>{data.history_ready || 0}</strong></div>
    </div>
    {!projects.length ? <section className="knowledge-empty"><h2>尚未接入项目</h2><p>接入后，项目和生成的知识会显示在这里。采集与整理由开发助手执行。</p></section> :
      <section className="knowledge-projects" aria-label="项目列表">{projects.map((project, index) => <Reveal as="article" className="knowledge-project" key={project.name} delay={Math.min(index * .04, .2)}>
        <div className="knowledge-project-heading"><span className="knowledge-project-icon"><Icon name="branch" size={24} /></span><h2>{project.name}</h2><span className={`knowledge-state ${project.status === 'ready' ? 'ready' : ''}`}>{project.status === 'ready' ? '代码知识可用' : project.status === 'unavailable' ? '需要处理' : '代码待归纳'}</span></div>
        <p className="knowledge-branch"><Icon name="branch" size={14} />{project.branch}</p>
        <p className="knowledge-project-description">{project.status === 'ready' ? `已整理 ${project.input_files} 个代码与文档文件。` : project.status === 'unavailable' ? '代码读取失败，需由助手检查来源。' : '代码已拉取，知识尚未生成完成。'}</p>
        <p className="knowledge-project-description knowledge-history">{historyDescription(project)}</p>
        {project.sync_status && project.sync_status !== 'ok' && <p className="knowledge-project-description knowledge-history">最近一次同步遇到问题，当前显示上次保留的资料。</p>}
        {!!project.documents?.length && <details className="knowledge-details"><summary>查看知识文件</summary><div>{project.documents.map(name => <a key={name} href={`/api/developer-knowledge/${encodeURIComponent(project.name)}/documents/${encodeURIComponent(name)}`}>{documentNames[name] || name}<span>Markdown ↓</span></a>)}</div></details>}
        <a className="knowledge-support-link" href={`?view=support&project=${encodeURIComponent(project.name)}`}><Icon name="support" size={18} /><span>遇到问题，进入技术客服</span><Icon name="arrow" size={17} /></a>
      </Reveal>)}</section>}
  </>;
}

export default function ProjectKnowledge({ onUnauthorized, onLogout }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async signal => {
    try {
      const result = await api('/developer-knowledge', { signal });
      setData(result);
      setError('');
    } catch (failure) {
      if (failure.status === 401) onUnauthorized();
      if (failure.name !== 'AbortError') setError(failure.message);
    }
  }, [onUnauthorized]);
  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    const timer = setInterval(() => refresh(controller.signal), 30000);
    return () => { clearInterval(timer); controller.abort(); };
  }, [refresh]);
  const logout = async () => {
    setBusy(true);
    try { await onLogout(); } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  };
  return <div className="knowledge-shell">
    <header className="knowledge-topbar"><Brand /><nav className="support-topnav" aria-label="工作台导航"><a className="button subtle" href="/" aria-current="page"><Icon name="books" />项目知识</a><a className="button subtle" href="?view=support"><Icon name="support" />技术客服</a><button className="button subtle" disabled={busy} onClick={logout}><Icon name="logout" />退出</button></nav></header>
    <main className="knowledge-main">
      <header className="knowledge-intro"><div><h1>项目知识</h1><p>代码结构、历史决定与开发经验，按项目整理。</p></div><a className="button secondary" href="?view=support"><Icon name="support" size={19} />开始一次排查<Icon name="arrow" size={17} /></a></header>
      {error && <div className="knowledge-error"><Notice>{error}</Notice><button className="button" onClick={() => refresh()}>重新连接</button></div>}
      {!data && !error ? <Spinner label="读取项目知识状态" /> : data && <KnowledgeOverview data={data} />}
      <footer className="knowledge-footer">采集与归纳由助手完成。这里查看项目覆盖和知识结果。</footer>
    </main>
  </div>;
}
