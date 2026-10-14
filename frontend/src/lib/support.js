export const SUPPORT_STATUS = {
  awaiting_info: '等待补充', processing: '正在分析', awaiting_confirmation: '等待验证',
  unresolved: '继续排查', resolved: '已确认解决', failed: '分析未完成',
};

export const SUPPORT_DEFAULTS = { projects: [], model_configured: false, log_limit: 64000, analysis_timeout_ms: 255000 };

export function supportPath(threadId, action = '', resource = 'sessions') {
  return `/support/${resource}/${encodeURIComponent(threadId)}${action ? `/${action}` : ''}`;
}

export function supplementError(payload, limit) {
  if (!Object.values(payload).some(value => value.trim())) return '请至少补充版本、复现步骤或日志中的一项。';
  if (new TextEncoder().encode(payload.log_text || '').length > limit) return `日志最多 ${limit} UTF-8 字节，请只粘贴故障前后的片段。`;
  return '';
}

export function supportTime(value) {
  if (!value || Number.isNaN(Date.parse(value))) return '';
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value));
}

export function needsStatusCheck(failure) {
  return ![401, 404, 422].includes(failure.status);
}
