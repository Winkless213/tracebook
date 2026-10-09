export const STATUS = { queued: '等待处理', retrieving: '检索资料', generating: '生成回答', completed: '已完成', failed: '执行失败', cancelled: '已取消' };
export const KINDS = { code: '代码', document: '文档', config: '配置', test: '测试' };
export const isPending = (status) => ['queued', 'retrieving', 'generating'].includes(status);
export const sourceLines = (content, start = 1) => String(content ?? '').split(/\r\n|\n|\r/).map((text, index) => ({ number: start + index, text }));
export const shortCommit = (commit) => commit ? commit.slice(0, 8) : '未提供版本';
export const questionError = (text) => Array.from(text.trim()).length < 8 ? '请至少输入 8 个字符，让问题更具体。' : Array.from(text.trim()).length > 2000 ? '问题最多 2000 个字符。' : null;

export function questionIntent(previous, payload, createKey = () => crypto.randomUUID()) {
  const signature = JSON.stringify([payload.project_id, payload.snapshot_id || null, payload.question]);
  return previous?.signature === signature ? previous : { signature, key: createKey() };
}

export function validateSnapshot(value) {
  if (!value || value.schema !== 1) return '快照 schema 必须为 1，请使用项目的快照导出工具。';
  for (const field of ['project', 'branch', 'commit', 'exported_at']) if (typeof value[field] !== 'string' || !value[field].trim()) return `快照缺少 ${field} 字段。`;
  if (!Array.isArray(value.excluded)) return '快照缺少 excluded 列表。';
  if (!Array.isArray(value.files) || value.files.length < 1 || value.files.length > 200) return '快照必须包含 1–200 份文本文件。';
  let bytes = 0;
  for (const file of value.files) {
    if (!file || typeof file.path !== 'string' || !file.path || typeof file.content !== 'string' || !/^[a-f0-9]{64}$/i.test(file.sha256 || '')) return '每份文件必须包含路径、文本 content 和 sha256 校验值。';
    bytes += new TextEncoder().encode(file.content).length;
    if (bytes > 2_000_000) return '快照中的文本总量不能超过 2 MB。';
  }
  return null;
}

export function mergeQuestion(history, question) {
  return [question, ...history.filter((item) => item.id !== question.id)].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 100);
}

export function formatDate(value, compact = false) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '时间未提供';
  return new Intl.DateTimeFormat('zh-CN', compact ? { month: 'numeric', day: 'numeric' } : { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
}

export function readPreference(key, fallback) {
  try { return JSON.parse(localStorage.getItem(`tracebook:${key}`)) ?? fallback; }
  catch { return fallback; }
}

export function savePreference(key, value) {
  try { localStorage.setItem(`tracebook:${key}`, JSON.stringify(value)); }
  catch { /* 浏览器限制存储时，继续保留当前会话。 */ }
}
