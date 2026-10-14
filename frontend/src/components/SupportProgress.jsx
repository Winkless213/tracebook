import React from 'react';
import { Icon } from './Ui.jsx';

export default function SupportProgress({ status }) {
  const step = !status ? 0 : ['awaiting_info', 'processing', 'failed', 'unresolved'].includes(status) ? 1 : 2;
  const resolved = status === 'resolved';
  return <ol className="support-progress" aria-label="排查流程">
    {['描述问题', '补充材料', '验证结果'].map((label, index) => <li key={label} className={resolved || index < step ? 'done' : index === step ? 'current' : ''} aria-current={!resolved && index === step ? 'step' : undefined}><span><Icon name={resolved || index < step ? 'check' : ['chat', 'terminal', 'verified'][index]} size={18} /></span><strong><small>0{index + 1}</small>{label}</strong></li>)}
  </ol>;
}
