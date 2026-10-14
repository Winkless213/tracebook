import React, { useState } from 'react';
import { api } from '../lib/api.js';
import { Brand, Icon, Notice, Reveal, Spinner } from './Ui.jsx';

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
    <div className="login-brand"><Brand /><span className="login-private"><Icon name="lock" size={16} />私人工作空间</span></div>
    <div className="login-layout">
      <Reveal as="section" className="login-intro">
        <h1>开发者知识工作台</h1>
        <p>查看项目资料，记录问题与排查过程。<br />代码结构、开发经验和已验证的案例，集中在这里。</p>
        <div className="login-proof"><span><Icon name="branch" size={18} />项目代码</span><span><Icon name="books" size={18} />开发知识</span><span><Icon name="verified" size={18} />排查案例</span></div>
      </Reveal>
      <Reveal as="section" className="login-card" delay={.08}>
        <div className="lock-mark"><Icon name="lock" size={23} /></div>
        <h2>登录工作空间</h2>
        <p>输入密码，继续查看项目与排查记录。</p>
        <form onSubmit={submit}>
          <label htmlFor="password">访问密码</label>
          <input id="password" name="password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required autoFocus placeholder="输入访问密码" disabled={busy} />
          <Notice>{error}</Notice>
          <button className="button primary login-submit" disabled={busy || !password}>{busy ? <Spinner label="验证中" /> : <>进入工作台<Icon name="arrow" /></>}</button>
        </form>
        <div className="login-note"><Icon name="lock" size={14} /><span>私人项目资料仅在登录后可访问。</span></div>
      </Reveal>
    </div>
    <footer className="login-footer"><span>Tracebook · 开发者项目知识助手</span><span>{info?.release ? `版本 ${info.release}` : '独立项目工作空间'}</span></footer>
  </main>;
}
