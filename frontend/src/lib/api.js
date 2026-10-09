export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

const FIELDS = { password: '密码', question: '问题', project_id: '项目', snapshot_id: '快照', files: '文件', path: '路径', content: '正文', sha256: '校验值', project: '项目', branch: '分支', commit: '提交版本' };

export function errorMessage(payload) {
  if (typeof payload?.detail === 'string') return payload.detail;
  if (Array.isArray(payload?.detail)) return payload.detail.map((item) => {
    const field = (item.loc || []).filter((part) => part !== 'body').map((part) => typeof part === 'number' ? part + 1 : FIELDS[part] || part).join(' ');
    return `${field || '输入内容'}：${item.type?.includes('missing') ? '请填写此项' : item.msg || '内容格式不正确'}`;
  }).join('；');
  return '请求未完成，请稍后重试。';
}

export async function api(path, options = {}) {
  const { method = 'GET', body, signal, idempotencyKey, timeoutMs = 20000, fetcher = fetch } = options;
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) abort();
  else signal?.addEventListener('abort', abort, { once: true });
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  const headers = { Accept: 'application/json' };
  if (method !== 'GET') Object.assign(headers, { 'Content-Type': 'application/json', 'X-Requested-With': 'Tracebook' });
  if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;
  try {
    const response = await fetcher(`/api${path}`, { method, credentials: 'same-origin', headers, signal: controller.signal, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    let payload = null;
    if (response.headers.get('content-type')?.includes('application/json')) {
      try { payload = await response.json(); }
      catch { throw new ApiError('服务返回了无法读取的内容，请稍后重试。', response.status); }
    }
    if (!response.ok) throw new ApiError(payload ? errorMessage(payload) : response.status === 401 ? '登录已失效，请重新登录。' : `服务暂时无法完成请求（${response.status}），请稍后重试。`, response.status);
    if (payload === null && response.status !== 204) throw new ApiError('服务返回了无法读取的内容，请稍后重试。', response.status);
    return payload;
  } catch (error) {
    if (timedOut) throw new ApiError('连接超时，当前内容已保留。请检查网络后重试。', 0);
    if (error.name === 'AbortError' || error instanceof ApiError) throw error;
    throw new ApiError('暂时无法连接服务，当前内容已保留。网络恢复后可重试。', 0);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
  }
}
