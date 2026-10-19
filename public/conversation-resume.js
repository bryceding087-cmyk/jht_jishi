// Store only a conversation ID, scoped to the authenticated user and this tab.
// The server remains the source of truth for tasks, results and permissions.
export function createConversationResume({storage = () => sessionStorage} = {}) {
  const key = user => `jht.conversation-return.v1:${user.id}`;
  function remember(user, id) {
    if (!user?.id || !id) return;
    try { storage().setItem(key(user), id); } catch {}
  }
  function forget(user) {
    if (!user?.id) return;
    try { storage().removeItem(key(user)); } catch {}
  }
  function target(user, conversations, entry = new URLSearchParams()) {
    if (!user?.id || ['conversation', 'project', 'view', 'mode', 'embedded'].some(name => entry.has(name))) return null;
    const own = conversations.filter(item => String(item.owner_id) === String(user.id));
    let saved;
    try { saved = storage().getItem(key(user)); } catch {}
    const previous = own.find(item => item.id === saved);
    if (saved && !previous) forget(user);
    const active = own.filter(item => item.active_task || ['queued', 'running', 'waiting_tool'].includes(item.status))
      .sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
    return active.find(item => item.id === saved)?.id || active[0]?.id || previous?.id || null;
  }
  return {remember, forget, target};
}
