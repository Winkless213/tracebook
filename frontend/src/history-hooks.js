import { useCallback, useEffect, useState } from 'react';

export function useHistoryResource(load, { onUnauthorized, pollMs = 0, online = true } = {}) {
  const [state, setState] = useState({ load, data: null, loading: true, refreshing: false, error: '' });
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const update = useCallback((updater) => {
    setState((current) => current.load === load ? { ...current, data: updater(current.data) } : current);
    refresh();
  }, [load, refresh]);
  useEffect(() => {
    const controller = new AbortController();
    let timer;
    setState((current) => ({ load, data: current.load === load ? current.data : null, loading: online && !(current.load === load && current.data), refreshing: online, error: '' }));
    const read = async () => {
      let retry = true;
      try {
        const data = await load({ signal: controller.signal });
        if (!controller.signal.aborted) setState({ load, data, loading: false, refreshing: false, error: '' });
      } catch (failure) {
        if (controller.signal.aborted || failure.name === 'AbortError') return;
        if (failure.status === 401) onUnauthorized?.();
        retry = failure.status === 0 || failure.status >= 500;
        setState((current) => ({ ...current, loading: false, refreshing: false, error: failure.message }));
      }
      if (!controller.signal.aborted && pollMs && retry) timer = setTimeout(read, pollMs);
    };
    if (online) read();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [load, revision, pollMs, online, onUnauthorized]);
  const current = state.load === load ? state : { data: null, loading: online, refreshing: online, error: '' };
  return { ...current, refresh, update };
}
