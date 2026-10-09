import React, { useState } from 'react';
import { api } from '../lib/api.js';
import { Brand, Icon, Notice, Spinner } from './Ui.jsx';

export default function Login({ onSuccess, info }) {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try { await api('/login', { method: 'POST', body: { password } }); onSuccess(); }
    catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }
  return <main className="login-page">
    <div className="login-brand"><Brand /></div>
    <div className="login-layout">
      <section className="login-intro">
        <span className="eyebrow">KNOWLEDGE, WITH CONTEXT</span>
        <h1>项目积累，<br />供开发助手接着用。</h1>
        <p>代码与开发经验整理成可追溯的知识，<br />让 Codex 和 Claude 按任务读取。</p>
        <div className="login-proof"><Icon name="branch" /><span>项目代码</span><span className="proof-divider" /><Icon name="link" /><span>开发知识</span><span className="proof-divider" /><Icon name="clock" /><span>持续积累</span></div>
      </section>
      <section className="login-card">
        <div className="lock-mark"><Icon name="lock" size={23} /></div>
        <h2>查看项目知识</h2>
        <p>查看接入情况、整理结果与需要处理的问题。</p>
        <form onSubmit={submit}>
          <label htmlFor="password">访问密码</label>
          <input id="password" name="password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required autoFocus placeholder="输入访问密码" disabled={busy} />
          <Notice>{error}</Notice>
          <button className="button primary login-submit" disabled={busy || !password}>{busy ? <Spinner label="验证中" /> : <>进入工作台<Icon name="arrow" /></>}</button>
        </form>
        <div className="login-note"><Icon name="lock" size={14} /><span>私人项目资料仅在登录后可访问。</span></div>
      </section>
    </div>
    <footer className="login-footer"><span>Tracebook · 开发者项目知识助手</span><span>{info?.release ? `版本 ${info.release}` : '独立项目工作空间'}</span></footer>
  </main>;
}
