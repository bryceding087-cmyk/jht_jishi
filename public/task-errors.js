export const ARK_MODEL_MANAGEMENT_URL = 'https://console.volcengine.com/ark/region:cn-beijing/openManagement';

export const CONTENT_REJECTION_MESSAGE = '您生成的内容不合规，请修改后重试。';

// Also translate older saved failures without resubmitting generation requests.
export function normalizeContentError(value) {
  const text = typeof value === 'string' ? value : JSON.stringify(value ?? '');
  const rejected = /sensitive[\s_-]*content[\s_-]*detected|(?:input|output)[\w-]*content[\s_-]*(?:filter|blocked)|content[\s_-]*(?:policy[\s_-]*violation|filter(?:ed)?|moderation|blocked)|responsible[\s_-]*ai[\s_-]*policy[\s_-]*violation|data[\s_-]*inspection[\s_-]*failed|(?:copyright|trademark|likeness)[\s_-]*(?:violation|infringement)|(?:safety[\s_-]*(?:violation|filter)|prohibited[\s_-]*content)|(?:may contain|contains?)\s+(?:sensitive|inappropriate|unsafe)\s+(?:information|content)|(?:violat\w*|blocked by|rejected by).{0,60}(?:content policy|safety policy|safety system|moderation)|(?:内容|提示词|输入|输出|图片|图像|视频|音频|文本).{0,12}(?:不合规|违规|违反.{0,8}(?:政策|规定)|未通过.{0,6}审核|审核不通过|审核失败|涉敏|包含敏感|含有敏感)|(?:内容|安全)审核.{0,8}(?:拦截|拒绝|不通过|失败)/i.test(text);
  return rejected ? CONTENT_REJECTION_MESSAGE : value;
}

// Handle both saved provider error objects and HTTP exceptions from the adapter.
export function describeTaskError(task = {}) {
  const original = task.error ?? task.provider_response?.error;
  if (normalizeContentError(original) === CONTENT_REJECTION_MESSAGE) {
    return {raw:CONTENT_REJECTION_MESSAGE,code:'',requestId:'',quotaLimited:false,title:'生成失败',message:CONTENT_REJECTION_MESSAGE,billingNote:''};
  }
  let error = original;
  if (typeof error === 'string') {
    const jsonStart = error.indexOf('{');
    if (jsonStart >= 0) {
      try { error = JSON.parse(error.slice(jsonStart)); } catch { /* Keep plain-text errors. */ }
    }
  }
  if (error && typeof error === 'object' && error.error) error = error.error;
  const raw = typeof error === 'string' ? error : error?.message || (error ? JSON.stringify(error) : '模型服务未返回错误详情');
  const code = typeof error === 'object' ? String(error?.code || '') : '';
  const requestId = String(error?.request_id || task.provider_response?.request_id || '') || raw.match(/Request\s+id\s*:\s*([\w-]+)/i)?.[1] || '';
  const quotaLimited = code === 'SetLimitExceeded' || /SetLimitExceeded|has reached the set (?:usage|inference) limit/i.test(raw);
  return {
    raw, code, requestId, quotaLimited,
    title: quotaLimited ? '模型用量达到上限，服务已暂停' : '生成失败',
    message: quotaLimited ? '火山方舟暂停了此账号的模型调用。请在「开通管理」中检查该模型的安心体验（Safe Experience Mode）及用量设置，恢复服务后再提交。' : raw,
    billingNote: quotaLimited ? '调整用量限制或关闭安心体验后，调用可能按量计费，请先确认控制台的费用说明。' : '',
  };
}
