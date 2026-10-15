import React, { useEffect, useRef, useState } from 'react';
import { useReducedMotion } from 'motion/react';
import * as m from 'motion/react-m';
import { PlusIcon } from '@phosphor-icons/react/dist/csr/Plus';
import { ArrowRightIcon } from '@phosphor-icons/react/dist/csr/ArrowRight';
import { MagnifyingGlassIcon } from '@phosphor-icons/react/dist/csr/MagnifyingGlass';
import { UploadSimpleIcon } from '@phosphor-icons/react/dist/csr/UploadSimple';
import { FileTextIcon } from '@phosphor-icons/react/dist/csr/FileText';
import { GitBranchIcon } from '@phosphor-icons/react/dist/csr/GitBranch';
import { ClockCounterClockwiseIcon } from '@phosphor-icons/react/dist/csr/ClockCounterClockwise';
import { XIcon } from '@phosphor-icons/react/dist/csr/X';
import { DownloadSimpleIcon } from '@phosphor-icons/react/dist/csr/DownloadSimple';
import { CaretRightIcon } from '@phosphor-icons/react/dist/csr/CaretRight';
import { CheckIcon } from '@phosphor-icons/react/dist/csr/Check';
import { ArrowsClockwiseIcon } from '@phosphor-icons/react/dist/csr/ArrowsClockwise';
import { LinkIcon } from '@phosphor-icons/react/dist/csr/Link';
import { ShieldCheckIcon } from '@phosphor-icons/react/dist/csr/ShieldCheck';
import { SignOutIcon } from '@phosphor-icons/react/dist/csr/SignOut';
import { StackIcon } from '@phosphor-icons/react/dist/csr/Stack';
import { HeadsetIcon } from '@phosphor-icons/react/dist/csr/Headset';
import { TerminalWindowIcon } from '@phosphor-icons/react/dist/csr/TerminalWindow';
import { BooksIcon } from '@phosphor-icons/react/dist/csr/Books';
import { BugIcon } from '@phosphor-icons/react/dist/csr/Bug';
import { ChatTeardropTextIcon } from '@phosphor-icons/react/dist/csr/ChatTeardropText';
import { CheckCircleIcon } from '@phosphor-icons/react/dist/csr/CheckCircle';
import { STATUS } from '../lib/domain.js';

const icons = {
  plus: PlusIcon, arrow: ArrowRightIcon, search: MagnifyingGlassIcon, upload: UploadSimpleIcon,
  file: FileTextIcon, branch: GitBranchIcon, clock: ClockCounterClockwiseIcon, close: XIcon,
  download: DownloadSimpleIcon, chevron: CaretRightIcon, check: CheckIcon, refresh: ArrowsClockwiseIcon,
  link: LinkIcon, lock: ShieldCheckIcon, logout: SignOutIcon, layers: StackIcon,
  support: HeadsetIcon, terminal: TerminalWindowIcon, books: BooksIcon, bug: BugIcon,
  chat: ChatTeardropTextIcon, verified: CheckCircleIcon,
};

export function Icon({ name, size = 18, ...props }) {
  const Symbol = icons[name] || icons.file;
  return <Symbol size={size} weight="duotone" aria-hidden="true" {...props} />;
}

export function Reveal({ as = 'div', delay = 0, children, ...props }) {
  const reduce = useReducedMotion();
  const Surface = m[as];
  return <Surface initial={{ opacity: 0, y: reduce ? 0 : 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: reduce ? 0 : .32, delay: reduce ? 0 : delay, ease: [.22, 1, .36, 1] }} {...props}>{children}</Surface>;
}

export function Brand() {
  return <div className="brand"><img src="/favicon.svg" alt="" width="34" height="34" /><div><strong>Tracebook<span className="brand-dot">.</span></strong><span>项目知识工作台</span></div></div>;
}

export function Spinner({ label = '加载中' }) {
  return <span className="loading-inline"><span className="spinner" aria-hidden="true" /><span>{label}</span></span>;
}

export function Elapsed() {
  const start = useRef(Date.now());
  const [, tick] = useState(0);
  useEffect(() => { const id = setInterval(() => tick(count => count + 1), 1000); return () => clearInterval(id); }, []);
  return <span className="support-elapsed">已等待 {Math.max(0, Math.floor((Date.now() - start.current) / 1000))} 秒</span>;
}

export function Notice({ children, tone = 'error', onDismiss, action }) {
  if (!children) return null;
  return <div className={`notice ${tone}`} role={tone === 'error' ? 'alert' : 'status'}><span>{children}</span>{action}{onDismiss && <button className="icon-button" aria-label="关闭提示" onClick={onDismiss}><Icon name="close" size={16} /></button>}</div>;
}

export function StatusBadge({ status }) {
  return <span className={`status-badge status-${status}`}><span className="status-dot" />{STATUS[status] || status}</span>;
}
