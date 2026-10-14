import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './lib/api.js';
import { needsStatusCheck, SUPPORT_DEFAULTS, supportPath } from './lib/support.js';

export function useSupportCatalog(onUnauthorized, includeChat = false) {
  const [settings, setSettings] = useState(SUPPORT_DEFAULTS);
  const [sessions, setSessions] = useState([]);
  const [cases, setCases] = useState([]);
  const [conversations, setConversations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const scope = useRef(null);
  const generation = useRef(0);
  const refresh = useCallback(async () => {
    const signal = scope.current?.signal;
    const current = ++generation.current;
    setLoading(true);
    const paths = ['/support/projects', '/support/sessions', '/support/cases', ...(includeChat ? ['/support/conversations'] : [])];
    const results = await Promise.allSettled(paths.map(path => api(path, { signal })));
    if (signal?.aborted || current !== generation.current) return;
    [setSettings, setSessions, setCases, ...(includeChat ? [setConversations] : [])].forEach((update, index) => { if (results[index].status === 'fulfilled') update(results[index].value); });
    const failures = results.filter(result => result.status === 'rejected').map(result => result.reason);
    if (failures.some(failure => failure.status === 401)) onUnauthorized();
    setError(failures.filter(failure => failure.name !== 'AbortError').map(failure => failure.message).join('；'));
    setLoading(false);
  }, [onUnauthorized, includeChat]);
  useEffect(() => {
    scope.current = new AbortController();
    refresh();
    return () => { scope.current.abort(); generation.current++; };
  }, [refresh]);
  const updateSession = useCallback(next => {
    setSessions(previous => [next, ...previous.filter(item => item.thread_id !== next.thread_id)].sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || ''))));
    if (next.case_id) refresh();
  }, [refresh]);
  const updateConversation = useCallback(next => {
    setConversations(previous => [next, ...previous.filter(item => item.id !== next.id)].slice(0, 100));
  }, []);
  return { settings, sessions, cases, conversations, loading, error, refresh, updateSession, updateConversation };
}

export function useSupportSession(threadId, settings, onUpdate, onUnauthorized, { resource = 'sessions', draft = false } = {}) {
  const [session, setSession] = useState(() => draft ? { id: threadId, turns: [], status: 'ready' } : null);
  const [loading, setLoading] = useState(!draft);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [needsCheck, setNeedsCheck] = useState(false);
  const scope = useRef(null);
  const sequence = useRef(0);
  const reading = useRef(0);
  const writing = useRef(false);
  const apply = useCallback(next => { setSession(next); setError(''); setNeedsCheck(false); onUpdate(next); }, [onUpdate]);
  const refresh = useCallback(async () => {
    if (reading.current || writing.current) return;
    const signal = scope.current?.signal;
    const current = ++sequence.current;
    reading.current = current;
    setLoading(true);
    try {
      const next = await api(supportPath(threadId, '', resource), { signal });
      if (!signal?.aborted && current === sequence.current) apply(next);
    } catch (failure) {
      if (signal?.aborted || current !== sequence.current) return;
      if (failure.status === 401) onUnauthorized();
      if (failure.name !== 'AbortError') setError(failure.message);
    } finally {
      if (reading.current === current) reading.current = 0;
      if (!signal?.aborted && current === sequence.current) setLoading(false);
    }
  }, [threadId, resource, apply, onUnauthorized]);
  useEffect(() => {
    scope.current = new AbortController();
    if (!draft) refresh();
    return () => { scope.current.abort(); reading.current = 0; sequence.current++; };
  }, [refresh, draft]);
  useEffect(() => {
    if (session?.status !== 'processing' || busy || needsCheck) return;
    const timer = setInterval(refresh, 3000);
    return () => clearInterval(timer);
  }, [session?.status, busy, needsCheck, refresh]);
  const write = async (action, body = {}) => {
    if (writing.current || needsCheck) return null;
    const signal = scope.current?.signal;
    writing.current = true;
    sequence.current++;
    reading.current = 0;
    setBusy(action);
    setError('');
    try {
      const next = await api(supportPath(threadId, action, resource), { method: 'POST', body, signal, timeoutMs: settings.analysis_timeout_ms });
      if (signal?.aborted) return null;
      apply(next);
      return next;
    } catch (failure) {
      if (signal?.aborted) return null;
      if (failure.status === 401) onUnauthorized();
      setNeedsCheck(needsStatusCheck(failure));
      setError(failure.status === 0 ? '尚未收到处理结果，服务可能仍在运行。输入已保留，请先检查最新状态。' : failure.message);
      return null;
    } finally {
      writing.current = false;
      if (!signal?.aborted) { setBusy(''); setLoading(false); }
    }
  };
  return { session, loading, busy, error, needsCheck, refresh, write };
}
