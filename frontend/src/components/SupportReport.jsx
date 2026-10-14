import React from 'react';
import { Icon, Reveal } from './Ui.jsx';
import { supportPath } from '../lib/support.js';

const kinds = { user: '用户材料', log: '日志片段', knowledge: '项目知识', case: '已确认案例' };
const kindIcons = { user: 'chat', log: 'terminal', knowledge: 'books', case: 'verified' };

function EvidenceItem({ item }) {
  return <details className="support-evidence" id={`support-evidence-${item.id}`}>
    <summary><Icon name={kindIcons[item.kind] || 'file'} size={20} /><span>{kinds[item.kind] || '引用材料'}<small>{item.path || item.case_id || '问题描述与环境'}{item.start_line ? ` · 第 ${item.start_line}–${item.end_line} 行` : ''}</small></span><span className="support-reference">{item.id}</span><Icon name="chevron" size={15} /></summary>
    <div className="support-evidence-body"><pre>{item.content}</pre>
      {item.truncated && <p className="support-muted">此处为限量片段，未展示的部分尚未作为依据。</p>}
      {item.kind === 'knowledge' && <dl className="support-provenance">
        {item.published_project && <div><dt>知识项目</dt><dd>{item.published_project}</dd></div>}
        <div><dt>提交版本</dt><dd><code>{item.commit || (item.origin === 'published' ? '目录未提供提交号' : '会话归档')}</code></dd></div>
        <div><dt>知识快照</dt><dd><code>{item.snapshot_id}</code></dd></div>
        <div><dt>文件校验</dt><dd><code>{item.sha256}</code></dd></div>
      </dl>}
    </div>
  </details>;
}

export default function SupportReport({ session }) {
  const report = session.report;
  if (!report) return null;
  const confirmed = session.status === 'resolved' && report.confirmation?.resolved;
  const showEvidence = event => { const item = document.getElementById(event.currentTarget.hash.slice(1)); if (item) item.open = true; };
  return <Reveal as="section" className="support-report support-card" aria-label="排查报告">
    <div className="support-section-heading support-report-heading"><div className="support-report-title"><span className="support-report-mark"><Icon name="file" size={23} /></span><div><span className="eyebrow">排查结果</span><h2>问题分析报告</h2></div></div><a className="button secondary" href={`/api${supportPath(session.thread_id, 'export')}`} download><Icon name="download" size={16} />导出报告</a></div>
    {confirmed && <p className="support-confirmed"><Icon name="check" size={17} />已保存为确认案例</p>}
    <div className="support-report-grid"><div className="support-analysis">
    <h3>原因假设</h3>
    <p className="support-muted">引用用于回查材料，原因仍需通过复测确认。</p>
    {report.hypotheses?.length ? <ul className="support-hypotheses">{report.hypotheses.map((hypothesis, index) => <li key={index}><p>{hypothesis.text}</p><div className="support-references">{hypothesis.basis.map(item => <a key={item.id} className="support-reference" href={`#support-evidence-${item.id}`} onClick={showEvidence}>依据 {item.id}</a>)}</div></li>)}</ul> : <p className="support-insufficient">{report.verification?.reason || '现有材料不足以确定原因，请继续补充。'}</p>}
    {!!report.suggestions?.length && <><h3>建议与验证步骤</h3><ol className="support-suggestions">{report.suggestions.map((suggestion, index) => <li key={index}>{suggestion}</li>)}</ol></>}
    </div><aside className="support-report-sources" aria-label="本次引用">
    <h3>引用材料 <span className="support-count">{report.evidence?.length || 0}</span></h3>
    <p className="support-muted">展开片段，核对建议的依据。</p>
    <div className="support-evidence-list">{report.evidence?.map(item => <EvidenceItem key={item.id} item={item} />)}</div>
    </aside></div>
    {!!report.confirmation?.resolution_note && <div className="support-feedback"><h3>用户复测记录</h3><p>{report.confirmation.resolution_note}</p></div>}
    {!!report.verification?.limitations?.length && <details className="support-limitations"><summary>查看分析边界</summary><ul>{report.verification.limitations.map((text, index) => <li key={index}>{text}</li>)}</ul></details>}
  </Reveal>;
}
