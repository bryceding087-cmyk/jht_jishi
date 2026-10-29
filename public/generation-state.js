const PENDING_STATUSES = new Set(['queued', 'running', 'waiting_tool']);
const TERMINAL_STATUSES = new Set(['done', 'succeeded', 'failed', 'blocked', 'cancelled', 'canceled', 'interrupted']);

// Existing conversations share a gate by ID. Unsaved drafts get independent
// local scopes, then retain that gate when their conversation is created.
export function createGenerationGuard({ api, onChange = () => {} }) {
  const entries = new Map();
  function entry(scope) {
    if (!entries.has(scope)) {
      const conversationId = scope && !scope.startsWith('draft:') ? scope : null;
      entries.set(scope, { conversationId, known: !conversationId, busy: false, queueable: false, submitting: false,
        stopping: false, stopPromise: null, request: null, stoppedRequests: new Set(), task: null, revision: 0, refreshSequence: 0 });
    }
    return entries.get(scope);
  }
  const snapshot = scope => {
    const { known, busy, queueable, submitting, stopping, task, request, conversationId } = entry(scope);
    return { known, busy, queueable, submitting, stopping, task, request, conversationId,
      active: submitting || busy || !!request && !known };
  };
  const notify = scope => onChange(snapshot(scope));
  const isBlocked = scope => {
    const value = entry(scope);
    return !value.known || value.busy && !value.queueable || value.submitting || value.stopping;
  };

  return {
    snapshot,
    isBlocked,
    isSubmitting: scope => entry(scope).submitting,
    isActive: scope => snapshot(scope).active,
    wasStopped: (scope, requestId) => !!requestId && (entry(scope).stoppedRequests.has(requestId)
      || entry(scope).stopping && entry(scope).stoppingRequestId === requestId),
    reason(scope) {
      const value = entry(scope);
      if (value.stopping) return '正在停止，请稍候';
      if (value.submitting) return '正在提交，请稍候';
      if (!value.known) return '正在确认当前对话的生成状态，请稍候';
      return value.busy && !value.queueable ? '当前对话正在生成，请完成后再提交' : '';
    },
    requireRefresh(scope) {
      const value = entry(scope);
      value.revision++;
      if (value.conversationId) value.known = false;
      notify(scope);
    },
    beginSubmission(scope, { requestId, kind } = {}) {
      if (isBlocked(scope)) return false;
      const value = entry(scope);
      value.revision++;
      value.submitting = true;
      value.request = requestId ? { request_id: requestId, kind } : null;
      notify(scope);
      return true;
    },
    bindConversation(scope, conversationId, requestId) {
      const value = entry(scope);
      if (requestId && (value.stoppedRequests.has(requestId) || value.request?.request_id !== requestId)) return false;
      value.revision++;
      value.conversationId = conversationId;
      entries.set(conversationId, value);
      notify(scope);
      return true;
    },
    finishSubmission(scope, result, kind, conversationId, requestId) {
      if (requestId && (entry(scope).stoppedRequests.has(requestId) || entry(scope).request?.request_id !== requestId)) return false;
      if (conversationId) this.bindConversation(scope, conversationId, requestId);
      const value = entry(scope);
      value.revision++;
      value.submitting = false;
      if (result?.id && result.status) {
        value.queueable = !!result.queue_state && (kind === 'image' || kind === 'video');
        value.known = PENDING_STATUSES.has(result.status) || TERMINAL_STATUSES.has(result.status);
        value.busy = PENDING_STATUSES.has(result.status);
        const resultRequestId = result.request_id || result.params?.request_id || requestId || value.request?.request_id;
        value.task = value.busy ? { id: result.id, kind, status: result.status, request_id: resultRequestId, conversation_id: value.conversationId } : null;
        value.request = value.busy && resultRequestId ? { request_id: resultRequestId, kind } : null;
      } else {
        // A lost task response needs a scoped server check. If conversation
        // creation itself failed, the draft can retry with the same request ID.
        value.known = !value.conversationId;
      }
      notify(scope);
      return true;
    },
    stop(scope) {
      const value = entry(scope);
      if (value.stopPromise) return value.stopPromise;
      if (!snapshot(scope).active) return Promise.resolve(null);
      const target = value.submitting || !value.task ? value.request : {
        request_id: value.task.request_id || value.task.params?.request_id,
        task_id: value.task.id, kind: value.task.kind || value.request?.kind,
      };
      if (!target?.kind || (!target.request_id && !target.task_id)) return Promise.reject(new Error('暂未取得停止请求所需的任务标识，请稍后重试'));
      const requestId = target.request_id;
      const payload = { ...target, ...(value.conversationId ? { conversation_id: value.conversationId } : {}) };
      value.revision++;
      value.stopping = true;
      value.stoppingRequestId = requestId;
      const promise = Promise.resolve().then(() => api('/api/generation/stop', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      })).then(result => {
        if (requestId) value.stoppedRequests.add(requestId);
        value.revision++;
        value.submitting = false;
        value.known = true;
        value.busy = false;
        value.task = null;
        value.request = null;
        return result;
      }).finally(() => {
        value.stopping = false;
        value.stoppingRequestId = null;
        value.stopPromise = null;
        notify(scope);
      });
      value.stopPromise = promise;
      notify(scope);
      return promise;
    },
    async refresh(scope) {
      const value = entry(scope);
      if (value.submitting || value.stopping || !value.conversationId) return false;
      const revision = value.revision, sequence = ++value.refreshSequence;
      const data = await api(`/api/conversations/generation-state?conversation_id=${encodeURIComponent(value.conversationId)}`, { background: true });
      if (entry(scope) !== value || revision !== value.revision || sequence !== value.refreshSequence || value.submitting || value.stopping) return false;
      if (typeof data?.is_busy !== 'boolean') throw new Error('无法确认当前对话的生成状态');
      value.known = true;
      value.queueable = data.allow_queue === true;
      value.busy = data.is_busy;
      value.task = value.busy ? data.task || null : null;
      if (value.task) {
        const requestId = value.task.request_id || value.task.params?.request_id;
        value.request = requestId ? { request_id: requestId, kind: value.task.kind } : null;
      } else if (!value.busy) value.request = null;
      notify(scope);
      return true;
    },
  };
}
