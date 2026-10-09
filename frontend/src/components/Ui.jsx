import React from 'react';
import { STATUS } from '../lib/domain.js';

const paths = {
  plus: <path d="M12 5v14M5 12h14" />,
  arrow: <path d="M5 12h14m-6-6 6 6-6 6" />,
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4.5 4.5" /></>,
  upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5" /></>,
  file: <><path d="M14 3H5v18h14V8zM14 3v5h5M9 12h6M9 16h6" /></>,
  branch: <><circle cx="7" cy="5" r="2" /><circle cx="17" cy="5" r="2" /><circle cx="7" cy="19" r="2" /><path d="M7 7v10m10-10v3c0 3-10 1-10 5" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  download: <><path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4" /></>,
  chevron: <path d="m9 5 7 7-7 7" />,
  check: <path d="m5 12 4 4L19 6" />,
  refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6 6a8 8 0 0 1 13 2M18 18A8 8 0 0 1 5 16" /></>,
  link: <><path d="m10 13 4-4M8 16l-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0M14 8l2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(1 0) scale(.92)" /></>,
  lock: <><rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3" /></>,
  logout: <><path d="M9 4H4v16h5M9 12h12m-4-4 4 4-4 4" /></>,
  layers: <><path d="m12 3 10 5-10 5L2 8zM2 13l10 5 10-5M2 18l10 5 10-5" /></>,
};

export function Icon({ name, size = 18, ...props }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name] || paths.file}</svg>;
}

export function Brand() {
  return <div className="brand"><img src="/favicon.svg" alt="" width="34" height="34" /><div><strong>Tracebook<span className="brand-dot">.</span></strong><span>项目知识工作台</span></div></div>;
}

export function Spinner({ label = '加载中' }) {
  return <span className="loading-inline"><span className="spinner" aria-hidden="true" /><span>{label}</span></span>;
}

export function Notice({ children, tone = 'error', onDismiss, action }) {
  if (!children) return null;
  return <div className={`notice ${tone}`} role={tone === 'error' ? 'alert' : 'status'}><span>{children}</span>{action}{onDismiss && <button className="icon-button" aria-label="关闭提示" onClick={onDismiss}><Icon name="close" size={16} /></button>}</div>;
}

export function StatusBadge({ status }) {
  return <span className={`status-badge status-${status}`}><span className="status-dot" />{STATUS[status] || status}</span>;
}
