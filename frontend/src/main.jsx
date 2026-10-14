import React from 'react';
import { createRoot } from 'react-dom/client';
import { LazyMotion, domAnimation, MotionConfig } from 'motion/react';
import App from './App.jsx';
import './styles.css';
import './workspace.css';

class RenderBoundary extends React.Component {
  state = { error: false };
  static getDerivedStateFromError() { return { error: true }; }
  render() {
    if (this.state.error) return <main className="boot-page"><div className="card boot-card"><h1>页面暂时无法显示</h1><p>服务端已保存的问答不受影响。请重新加载页面。</p><button className="button primary" onClick={() => window.location.reload()}>重新加载</button></div></main>;
    return this.props.children;
  }
}

createRoot(document.getElementById('root')).render(<React.StrictMode><RenderBoundary><MotionConfig reducedMotion="user"><LazyMotion features={domAnimation} strict><App /></LazyMotion></MotionConfig></RenderBoundary></React.StrictMode>);
