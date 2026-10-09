import { api } from './api.js';

export const HISTORY_PAGE_SIZE = 50;
export const HISTORY_STATUS = { queued: '排队中', generating: '提炼中', completed: '已完成', failed: '提炼失败', cancelled: '已取消' };
export const HISTORY_ROLES = { user: '用户', assistant: '助手', 'tool-output': '工具输出', system: '系统', developer: '开发者', tool: '工具' };
export const isHistorySource = (source) => ['conversation', 'experience'].includes(source?.origin);
export const historyJobActions = (status) => ['queued', 'generating'].includes(status) ? ['cancel'] : status === 'failed' ? ['retry'] : [];

export const historyApi = {
  sessions: (projectId, options) => api(`/history/sessions?project_id=${encodeURIComponent(projectId)}`, options),
  session: (id, offset = 0, options) => api(`/history/sessions/${encodeURIComponent(id)}?offset=${offset}&limit=${HISTORY_PAGE_SIZE}`, options),
  record: (id, options) => api(`/history/records/${encodeURIComponent(id)}`, options),
  distill: (id, options) => api(`/history/sessions/${encodeURIComponent(id)}/distill`, { ...options, method: 'POST', body: {} }),
  retry: (id, options) => api(`/history/jobs/${encodeURIComponent(id)}/retry`, { ...options, method: 'POST', body: {} }),
  cancel: (id, options) => api(`/history/jobs/${encodeURIComponent(id)}/cancel`, { ...options, method: 'POST', body: {} }),
};

export function historyRecordIds(source) {
  if (!isHistorySource(source)) return [];
  return [...new Set([...(source.record_ids || []), source.record_id].filter((id) => typeof id === 'string' && id))];
}

export function mergeHistoryJob(detail, job) {
  if (!detail || detail.session.id !== job.session_id) return detail;
  const jobs = [job, ...detail.jobs.filter((item) => item.id !== job.id)].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  return { ...detail, jobs };
}

export function historyTime(value) {
  if (!value) return '时间未提供';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '时间未提供';
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
}
