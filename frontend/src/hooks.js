import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './lib/api.js';
import { isPending, mergeQuestion } from './lib/domain.js';

export function useSession() {
  const [authenticated, setAuthenticated] = useState(null);
  const [info, setInfo] = useState(null);
  const [error, setError] = useState('');
  const [checking, setChecking] = useState(false);
  const check = useCallback(async (signal) => {
    setChecking(true);
    setError('');
    try {
      const [session, details] = await Promise.all([api('/session', { signal }), api('/info', { signal })]);
      setAuthenticated(session.authenticated);
      setInfo(details);
    } catch (failure) {
      if (failure.name !== 'AbortError') setError(failure.message);
    } finally { if (!signal?.aborted) setChecking(false); }
  }, []);
  useEffect(() => { const controller = new AbortController(); check(controller.signal); return () => controller.abort(); }, [check]);
  const expire = useCallback(() => setAuthenticated(false), []);
  return { authenticated, setAuthenticated, info, error, checking, check, expire };
}

export function useCatalog(onUnauthorized) {
  const [projects, setProjects] = useState([]);
  const [samples, setSamples] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const refresh = useCallback(async (signal) => {
    setLoading(true);
    setError('');
    try {
      const [nextProjects, nextSamples] = await Promise.all([api('/projects', { signal }), api('/samples', { signal })]);
      setProjects(nextProjects);
      setSamples(nextSamples);
      return nextProjects;
    } catch (failure) {
      if (failure.status === 401) onUnauthorized();
      if (failure.name !== 'AbortError') setError(failure.message);
      return null;
    } finally { if (!signal?.aborted) setLoading(false); }
  }, [onUnauthorized]);
  useEffect(() => { const controller = new AbortController(); refresh(controller.signal); return () => controller.abort(); }, [refresh]);
  return { projects, samples, loading, error, refresh };
}

export function useProjectData(projectId, onUnauthorized) {
  const [snapshots, setSnapshots] = useState([]);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [loadedProject, setLoadedProject] = useState(null);
  const currentProject = useRef(projectId);
  currentProject.current = projectId;
  useEffect(() => {
    const controller = new AbortController();
    setSnapshots([]);
    setHistory([]);
    setLoadedProject(projectId);
    setError('');
    if (!projectId) { setLoading(false); return () => controller.abort(); }
    setLoading(true);
    Promise.all([
      api(`/projects/${encodeURIComponent(projectId)}/snapshots`, { signal: controller.signal }),
      api(`/questions?project_id=${encodeURIComponent(projectId)}`, { signal: controller.signal }),
    ]).then(([versions, questions]) => { setSnapshots(versions); setHistory(questions); }).catch((failure) => {
      if (failure.status === 401) onUnauthorized();
      if (failure.name !== 'AbortError') setError(failure.message);
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, revision, onUnauthorized]);
  const updateQuestion = useCallback((question) => {
    if (question.project_id === currentProject.current) setHistory((items) => mergeQuestion(items, question));
  }, []);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const inScope = loadedProject === projectId;
  return { snapshots: inScope ? snapshots : [], history: inScope ? history : [], loading: loading || Boolean(projectId && !inScope), error: inScope ? error : '', updateQuestion, refresh };
}

export function useQuestion(questionId, onUpdate, onUnauthorized) {
  const [question, setQuestion] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    let timer;
    setQuestion(null);
    setError('');
    setLoading(Boolean(questionId));
    if (!questionId) return () => controller.abort();
    const poll = async () => {
      try {
        const next = await api(`/questions/${encodeURIComponent(questionId)}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setQuestion(next);
        setError('');
        onUpdate(next);
        if (isPending(next.status)) timer = setTimeout(poll, 1500);
      } catch (failure) {
        if (failure.name === 'AbortError') return;
        if (failure.status === 401) onUnauthorized();
        setError(failure.message);
        if (failure.status === 0 || failure.status >= 500) timer = setTimeout(poll, 1500);
      } finally { if (!controller.signal.aborted) setLoading(false); }
    };
    poll();
    const reconnect = () => { clearTimeout(timer); refresh(); };
    window.addEventListener('online', reconnect);
    return () => { controller.abort(); clearTimeout(timer); window.removeEventListener('online', reconnect); };
  }, [questionId, revision, onUpdate, onUnauthorized, refresh]);
  return { question, loading, error, refresh };
}

export function useOnline() {
  const [online, setOnline] = useState(navigator.onLine);
  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    window.addEventListener('online', update);
    window.addEventListener('offline', update);
    return () => { window.removeEventListener('online', update); window.removeEventListener('offline', update); };
  }, []);
  return online;
}
