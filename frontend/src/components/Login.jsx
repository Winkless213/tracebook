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
        <h1>理解项目，<br />从有据可查开始。</h1>
        <p>把分散的代码与工程文档连起来。<br />每个结论，都能回到一个具体的文件和版本。</p>
        <div className="login-proof"><Icon name="branch" /><span>项目快照</span><span className="proof-divider" /><Icon name="link" /><span>原文引用</span><span className="proof-divider" /><Icon name="clock" /><span>问答留存</span></div>
        <div className="reference-demo" aria-hidden="true"><div><span className="reference-tag">SOURCE</span><span>路径 · 行号 · 提交版本</span></div><div className="demo-line long" /><div className="demo-line" /><div className="demo-line short" /><div className="demo-footer"><span className="tiny-square" />上下文，始终跟随答案。</div></div>
      </section>
      <section className="login-card">
        <div className="lock-mark"><Icon name="lock" size={23} /></div>
        <h2>进入你的知识工作台</h2>
        <p>使用工作台访问密码登录。</p>
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
