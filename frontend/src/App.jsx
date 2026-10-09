import React, { useCallback, useRef, useState } from 'react';
import { api } from './lib/api.js';
import { questionError, questionIntent, readPreference, savePreference, shortCommit } from './lib/domain.js';
import { useCatalog, useOnline, useProjectData, useQuestion, useSession } from './hooks.js';
import { Brand, Icon, Notice, Spinner } from './components/Ui.jsx';
import Login from './components/Login.jsx';
import Sidebar from './components/Sidebar.jsx';
import Composer from './components/Composer.jsx';
import Answer from './components/Answer.jsx';
import Evidence from './components/Evidence.jsx';
import ImportDialog from './components/ImportDialog.jsx';
import KnowledgeMap from './components/KnowledgeMap.jsx';
import HistoryKnowledge from './components/HistoryKnowledge.jsx';

function WorkspaceHeader({ project, snapshot, info, onLogout, logoutBusy, historyOpen }) {
  const conversationOnly = (snapshot?.origin || project?.origin) === 'conversation';
  return <>
    <header className="workspace-header"><div><div className="breadcrumb"><span>WORKSPACE</span><Icon name="chevron" size={11} /><span>{project?.name || '开始使用'}</span></div><h1>{project?.name || '项目知识库'}</h1><p>从代码与文档中找答案，回到原文核对。</p></div><div className="header-actions"><span className={`model-status ${info?.provider_configured ? '' : 'unconfigured'}`} title={info?.provider_configured ? [info.primary_model, info.fallback_model].filter(Boolean).join(' / ') : '回答模型尚未配置'}><span />{info?.provider_configured ? '回答模型已配置' : '仅检索可用'}</span><button className="icon-button logout-button" aria-label="退出登录" title="退出登录" onClick={onLogout} disabled={logoutBusy}><Icon name="logout" size={17} /></button></div></header>
    {project && !historyOpen && <div className="snapshot-strip"><div className="snapshot-identity"><Icon name="branch" size={16} /><strong>{project.branch}</strong><code>{conversationOnly ? '无 Git 快照' : shortCommit(snapshot?.commit || project.commit)}</code><span className={`snapshot-badge ${snapshot?.active === false ? 'historical' : ''}`}>{conversationOnly ? '历史会话资料' : snapshot?.active === false ? '历史快照' : '最新快照'}</span></div>{!conversationOnly && <div className="snapshot-counts"><span><b>{snapshot?.file_count ?? project.file_count}</b> 份文件</span><span className="strip-divider" /><span><b>{snapshot?.chunk_count ?? project.chunk_count}</b> 个片段</span></div>}</div>}
  </>;
}

function useSelections(project, data) {
  const [versions, setVersions] = useState(() => readPreference('versions', {}));
  const [questions, setQuestions] = useState(() => readPreference('questions', {}));
  const snapshot = data.snapshots.find((item) => item.id === versions[project?.id]) || data.snapshots.find((item) => item.active) || data.snapshots[0] || null;
  const savedQuestion = questions[project?.id];
  const questionId = savedQuestion === null ? null : data.history.find((item) => item.id === savedQuestion)?.id || data.history[0]?.id || null;
  const chooseSnapshot = (id, target = project?.id) => setVersions((previous) => { const next = { ...previous, [target]: id }; savePreference('versions', next); return next; });
  const chooseQuestion = (id, target = project?.id) => setQuestions((previous) => { const next = { ...previous, [target]: id }; savePreference('questions', next); return next; });
  return { snapshot, questionId, chooseSnapshot, chooseQuestion };
}

function Workspace({ info, onUnauthorized, onLogout, draft, setDraft }) {
  const catalog = useCatalog(onUnauthorized);
  const [projectKey, setProjectKey] = useState(() => readPreference('project', null));
  const currentProject = catalog.projects.find((item) => item.id === projectKey) || catalog.projects[0] || null;
  const data = useProjectData(currentProject?.id, onUnauthorized);
  const selection = useSelections(currentProject, data);
  const { snapshot, questionId } = selection;
  const project = currentProject;
  const questionState = useQuestion(questionId, data.updateQuestion, onUnauthorized);
  const question = questionState.question?.project_id === project?.id && questionState.question?.id === questionId ? questionState.question : null;
  const [search, setSearch] = useState(null);
  const [busy, setBusy] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [composerError, setComposerError] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [importOpen, setImportOpen] = useState(false);
  const [mapOpen, setMapOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [activeSource, setActiveSource] = useState(null);
  const [logoutBusy, setLogoutBusy] = useState(false);
  const intent = useRef(null);
  const online = useOnline();
  const activeSearch = search?.projectId === project?.id && search?.snapshotId === snapshot?.id ? search : null;
  const sources = activeSearch?.sources || question?.sources || [];
  const evidenceSnapshot = activeSearch ? activeSearch.snapshotId : question?.snapshot_id;
  const reportError = (failure) => { if (failure.status === 401) onUnauthorized(); setError(failure.message); };
  const selectProject = (id) => { setProjectKey(id); savePreference('project', id); setSearch(null); setComposerError(''); };
  const newQuestion = () => { setHistoryOpen(false); selection.chooseQuestion(null); setSearch(null); setActiveSource(null); requestAnimationFrame(() => document.getElementById('question-input')?.focus()); };
  const chooseSource = (id) => {
    setActiveSource(id);
    if (window.innerWidth < 1200) requestAnimationFrame(() => { const panel = document.getElementById('evidence-panel'); panel?.focus({ preventScroll: true }); panel?.scrollIntoView({ behavior: 'smooth', block: 'start' }); });
  };
  async function run(mode) {
    const invalid = questionError(draft);
    if (invalid) { setComposerError(invalid); return; }
    if (!project || !snapshot) return;
    setBusy(mode);
    setComposerError('');
    setError('');
    const payload = { project_id: project.id, question: draft.trim(), snapshot_id: snapshot.id };
    try {
      if (mode === 'search') {
        const result = await api('/search', { method: 'POST', body: { ...payload, top_k: 6 } });
        setSearch({ ...result, projectId: project.id, snapshotId: snapshot.id });
        selection.chooseQuestion(null);
        setActiveSource(result.sources[0]?.id || null);
      } else {
        intent.current = questionIntent(intent.current, payload);
        const result = await api('/questions', { method: 'POST', body: payload, idempotencyKey: intent.current.key });
        intent.current = null;
        data.updateQuestion(result);
        selection.chooseQuestion(result.id);
        setSearch(null);
        setActiveSource(null);
      }
    } catch (failure) { if (failure.status === 401) onUnauthorized(); setComposerError(failure.message); }
    finally { setBusy(false); }
  }
  async function questionAction(action) {
    if (!question) return;
    setActionBusy(action);
    setError('');
    try {
      const next = await api(`/questions/${encodeURIComponent(question.id)}/${action}`, { method: 'POST', body: {} });
      data.updateQuestion(next);
      questionState.refresh();
    } catch (failure) { reportError(failure); }
    finally { setActionBusy(false); }
  }
  async function imported(result, payload) {
    const projects = await catalog.refresh();
    const importedId = typeof result.project === 'string' ? result.project : result.project?.id;
    const target = projects?.find((item) => item.id === importedId) || projects?.find((item) => item.name === payload.project && item.branch === payload.branch);
    if (target) {
      selectProject(target.id);
      selection.chooseSnapshot(typeof result.snapshot === 'string' ? result.snapshot : result.snapshot?.id, target.id);
      selection.chooseQuestion(null, target.id);
      data.refresh();
    }
    setNotice(result.unchanged ? '快照内容未变化，已保留现有版本。' : `已导入 ${payload.files.length} 份文件，项目快照已就绪。`);
  }
  async function logout() {
    setLogoutBusy(true);
    try { await onLogout(); } catch (failure) { reportError(failure); }
    finally { setLogoutBusy(false); }
  }
  return <div className="app-shell">
    <a className="skip-link" href={historyOpen ? '#history-knowledge' : '#question-input'}>{historyOpen ? '跳到会话知识' : '跳到提问'}</a>
    <Sidebar projects={catalog.projects} project={project} snapshots={data.snapshots} snapshotId={snapshot?.id} history={data.history} questionId={questionId} loading={data.loading || catalog.loading} disabled={Boolean(busy)} onProject={selectProject} onSnapshot={(id) => { setHistoryOpen(false); selection.chooseSnapshot(id); setSearch(null); }} onQuestion={(id) => { setHistoryOpen(false); selection.chooseQuestion(id); setSearch(null); setActiveSource(null); }} onImport={() => setImportOpen(true)} onRefresh={data.refresh} onNew={newQuestion} mobileOpen={mobileOpen} setMobileOpen={setMobileOpen} historyOpen={historyOpen} onHistory={() => { setHistoryOpen(true); setMobileOpen(false); }} />
    <main className="workspace-main">
      <WorkspaceHeader project={project} snapshot={snapshot} info={info} onLogout={logout} logoutBusy={logoutBusy} historyOpen={historyOpen} />
      {project && <div className="workspace-tabs" role="group" aria-label="项目视图"><button aria-pressed={!historyOpen} onClick={() => setHistoryOpen(false)}>项目问答</button><button aria-pressed={historyOpen} onClick={() => setHistoryOpen(true)}><Icon name="clock" size={15} />会话知识</button></div>}
      <div className="workspace-notices">
        {project && snapshot && !historyOpen && <button className="button secondary knowledge-open" onClick={() => setMapOpen(true)}><Icon name="file" size={15} />项目资料导航</button>}
        {!online && <Notice tone="warning">当前网络已断开。输入和已加载内容仍保留，连接恢复后继续同步状态。</Notice>}
        <Notice action={<button className="inline-button" onClick={() => catalog.refresh()}>重新连接</button>}>{catalog.error}</Notice>
        <Notice action={<button className="inline-button" onClick={data.refresh}>重新读取</button>}>{data.error}</Notice>
        <Notice onDismiss={() => setError('')}>{error}</Notice>
        <Notice tone="success" onDismiss={() => setNotice('')}>{notice}</Notice>
      </div>
      {historyOpen ? <HistoryKnowledge project={project} onUnauthorized={onUnauthorized} online={online} /> : <div className="workspace-grid"><div className="question-column">
        {!project && !catalog.loading && <div className="first-import"><div><span className="eyebrow">YOUR FIRST SNAPSHOT</span><h2>把项目带进来。</h2><p>一份快照，连接代码、文档和工程决策。</p></div><button className="button primary" onClick={() => setImportOpen(true)}><Icon name="plus" size={17} />导入快照</button></div>}
        <Composer draft={draft} setDraft={(value) => { setDraft(value); if (composerError) setComposerError(''); }} samples={catalog.samples} busy={busy} disabled={!project || !snapshot || data.loading || !online} providerConfigured={info?.provider_configured} onAsk={() => run('ask')} onSearch={() => run('search')} error={composerError} onDismissError={() => setComposerError('')} hasProject={Boolean(project)} />
        <Answer question={question} search={activeSearch} loading={questionState.loading} connectionError={questionState.error} currentSnapshotId={snapshot?.id} onSource={chooseSource} onRetry={() => questionAction('retry')} onCancel={() => questionAction('cancel')} onRefresh={questionState.refresh} onNew={newQuestion} actionBusy={actionBusy} hasProject={Boolean(project)} />
        <p className="workspace-footnote"><Icon name="link" size={13} />回答可能存在遗漏。做出改动前，请核对引用和所选版本。</p>
      </div><Evidence sources={sources} activeId={activeSource} onSelect={chooseSource} snapshotId={evidenceSnapshot} onUnauthorized={onUnauthorized} /></div>}
      <footer className="workspace-footer"><span>TRACEBOOK / PROJECT INTELLIGENCE</span><span>{info?.release || '独立工作空间'}</span></footer>
    </main>
    {importOpen && <ImportDialog onClose={() => setImportOpen(false)} onImported={imported} onUnauthorized={onUnauthorized} />}
    {mapOpen && project && snapshot && <KnowledgeMap project={project} snapshot={snapshot} onClose={() => setMapOpen(false)} onUnauthorized={onUnauthorized} />}
  </div>;
}

export default function App() {
  const session = useSession();
  const [draft, setDraft] = useState('');
  const logout = useCallback(async () => { await api('/logout', { method: 'POST', body: {} }); setDraft(''); session.setAuthenticated(false); }, [session.setAuthenticated]);
  if (session.authenticated === null) return <main className="boot-page"><Brand /><div className="card boot-card">{session.error ? <><Notice>{session.error}</Notice><button className="button primary" disabled={session.checking} onClick={() => session.check()}>{session.checking ? <Spinner label="连接中" /> : '重新连接工作台'}</button></> : <Spinner label="连接你的工作空间" />}</div></main>;
  if (!session.authenticated) return <Login info={session.info} onSuccess={() => { session.setAuthenticated(true); session.check(); }} />;
  return <Workspace info={session.info} onUnauthorized={session.expire} onLogout={logout} draft={draft} setDraft={setDraft} />;
}
