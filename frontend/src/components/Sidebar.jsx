import React from 'react';
import { formatDate, shortCommit } from '../lib/domain.js';
import { Brand, Icon, Spinner, StatusBadge } from './Ui.jsx';

export default function Sidebar({ projects, project, snapshots, snapshotId, history, questionId, loading, disabled, onProject, onSnapshot, onQuestion, onImport, onRefresh, onNew, mobileOpen, setMobileOpen }) {
  const names = [...new Set(projects.map((item) => item.name))];
  const branches = projects.filter((item) => item.name === project?.name);
  return <aside className={`sidebar ${mobileOpen ? 'sidebar-open' : ''}`}>
    <div className="sidebar-brand"><Brand /><button className="icon-button sidebar-toggle" aria-label={mobileOpen ? '收起项目和历史' : '展开项目和历史'} aria-expanded={mobileOpen} aria-controls="sidebar-content" onClick={() => setMobileOpen(!mobileOpen)}><Icon name={mobileOpen ? 'close' : 'layers'} /></button></div>
    <div id="sidebar-content" className="sidebar-content">
      <div className="scope-controls">
        <div className="rail-section-title"><span>工作空间</span><span className="rail-counter">{names.length}</span></div>
        <label htmlFor="project-select">项目</label>
        <select id="project-select" value={project?.name || ''} onChange={(event) => onProject(projects.find((item) => item.name === event.target.value)?.id)} disabled={disabled || !projects.length}>
          {!projects.length && <option value="">尚未导入项目</option>}
          {names.map((name) => <option key={name} value={name}>{name}</option>)}
        </select>
        <div className="field-heading"><Icon name="branch" size={14} /><label htmlFor="branch-select">分支</label></div>
        <select id="branch-select" value={project?.id || ''} onChange={(event) => onProject(event.target.value)} disabled={disabled || !branches.length}>
          {!branches.length && <option value="">暂无分支</option>}
          {branches.map((item) => <option key={item.id} value={item.id}>{item.branch}</option>)}
        </select>
        <label htmlFor="snapshot-select">快照版本</label>
        <select id="snapshot-select" value={snapshotId || ''} onChange={(event) => onSnapshot(event.target.value)} disabled={disabled || loading || !snapshots.length}>
          {!snapshots.length && <option value="">{loading ? '加载版本…' : '暂无快照'}</option>}
          {snapshots.map((item) => <option key={item.id} value={item.id}>{shortCommit(item.commit)}{item.active ? ' · 最新快照' : ` · ${formatDate(item.created_at, true)}`}</option>)}
        </select>
        <button className="button import-sidebar" onClick={onImport}><Icon name="upload" size={16} />导入项目快照</button>
      </div>
      <section className="history-section" aria-label="问答历史">
        <div className="rail-section-title"><span>问答历史 <span className="history-count">{history.length}</span></span><div className="history-tools"><button className="icon-button" aria-label="刷新问答历史" onClick={onRefresh} disabled={loading || !project}><Icon name="refresh" size={14} /></button><button className="icon-button" aria-label="新建提问" onClick={onNew}><Icon name="plus" size={17} /></button></div></div>
        {loading ? <div className="rail-empty"><Spinner label="读取历史" /></div> : history.length ? <div className="history-list">{history.map((item) => <button key={item.id} className={`history-item ${item.id === questionId ? 'selected' : ''}`} onClick={() => { onQuestion(item.id); setMobileOpen(false); }} aria-current={item.id === questionId ? 'true' : undefined}><span className="history-question">{item.question}</span><span className="history-meta"><StatusBadge status={item.status} /><time dateTime={item.created_at}>{formatDate(item.created_at, true)}</time></span></button>)}</div> : <div className="rail-empty"><Icon name="clock" size={20} /><p>还没有问答记录</p><span>每次提问会自动保存到这里。</span></div>}
      </section>
      <div className="sidebar-bottom"><span className="workspace-dot" /><span>个人工作空间</span><Icon name="lock" size={13} /></div>
    </div>
  </aside>;
}
