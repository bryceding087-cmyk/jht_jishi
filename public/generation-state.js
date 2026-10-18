const PENDING_STATUSES = new Set(['queued', 'running', 'waiting_tool']);
const TERMINAL_STATUSES = new Set(['done', 'succeeded', 'failed', 'blocked', 'cancelled', 'canceled', 'interrupted']);

// Existing conversations share a gate by ID. Unsaved drafts get independent
// local scopes, then retain that gate when their conversation is created.
export function createGenerationGuard({ api, onChange = () => {} }) {
  const entries = new Map();
  function entry(scope) {
    if (!entries.has(scope)) {
      const conversationId = scope && !scope.startsWith('draft:') ? scope : null;
      entries.set(scope, { conversationId, known: !conversationId, busy: false, submitting: false, task: null, revision: 0, refreshSequence: 0 });
    }
    return entries.get(scope);
  }
  const snapshot = scope => {
    const { known, busy, submitting, task } = entry(scope);
    return { known, busy, submitting, task };
  };
  const notify = scope => onChange(snapshot(scope));
  const isBlocked = scope => {
    const value = entry(scope);
    return !value.known || value.busy || value.submitting;
  };

  return {
    snapshot,
    isBlocked,
    isSubmitting: scope => entry(scope).submitting,
    reason(scope) {
      const value = entry(scope);
      if (value.submitting) return '正在提交，请稍候';
      if (!value.known) return '正在确认当前对话的生成状态，请稍候';
      return value.busy ? '当前对话正在生成，请完成后再提交' : '';
    },
    requireRefresh(scope) {
      const value = entry(scope);
      value.revision++;
      if (value.conversationId) value.known = false;
      notify(scope);
    },
    beginSubmission(scope) {
      if (isBlocked(scope)) return false;
      const value = entry(scope);
      value.revision++;
      value.submitting = true;
      notify(scope);
      return true;
    },
    bindConversation(scope, conversationId) {
      const value = entry(scope);
      value.revision++;
      value.conversationId = conversationId;
      entries.set(conversationId, value);
      notify(scope);
    },
    finishSubmission(scope, result, kind, conversationId) {
      if (conversationId) this.bindConversation(scope, conversationId);
      const value = entry(scope);
      value.revision++;
      value.submitting = false;
      if (result?.id && result.status) {
        value.known = PENDING_STATUSES.has(result.status) || TERMINAL_STATUSES.has(result.status);
        value.busy = PENDING_STATUSES.has(result.status);
        value.task = value.busy ? { id: result.id, kind, status: result.status, conversation_id: value.conversationId } : null;
      } else {
        // A lost task response needs a scoped server check. If conversation
        // creation itself failed, the draft can retry with the same request ID.
        value.known = !value.conversationId;
      }
      notify(scope);
    },
    async refresh(scope) {
      const value = entry(scope);
      if (value.submitting || !value.conversationId) return false;
      const revision = value.revision, sequence = ++value.refreshSequence;
      const data = await api(`/api/conversations/generation-state?conversation_id=${encodeURIComponent(value.conversationId)}`, { background: true });
      if (entry(scope) !== value || revision !== value.revision || sequence !== value.refreshSequence || value.submitting) return false;
      if (typeof data?.is_busy !== 'boolean') throw new Error('无法确认当前对话的生成状态');
      value.known = true;
      value.busy = data.is_busy;
      value.task = value.busy ? data.task || null : null;
      notify(scope);
      return true;
    },
  };
}
