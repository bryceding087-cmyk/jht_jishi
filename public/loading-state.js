const escape = value => String(value).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
export function loadingMarkup(label = '正在加载…', compact = false) {
  return `<div class="app-loading${compact ? ' app-loading-compact' : ''}" role="status" aria-live="polite"><img src="/triangle-flow.svg?v=1" alt="" aria-hidden="true"><span>${escape(label)}</span></div>`;
}

// One delayed badge for foreground requests; short requests never flash it.
const pending = new Map();
let badge, showTimer, hideTimer, shownAt = 0;
function updateBadge() {
  if (!pending.size) return;
  if (document.querySelector('#appBootScreen,.app-loading-overlay')) return;
  if (!badge?.isConnected) {
    badge = document.createElement('div');
    badge.className = 'app-network-loading';
    document.body.append(badge);
    shownAt = performance.now();
  }
  if (!badge.firstChild) badge.innerHTML = loadingMarkup('', true);
  badge.querySelector('span').textContent = [...pending.values()].at(-1);
}
export function beginRequest(url, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  // Polling already has persistent task progress. Keep it out of page loading.
  if (method === 'GET' && /^\/api\/(?:tasks|images\/generations|audio\/generations|agent\/runs)\/[^/?]+(?:\?|$)/.test(url)) return () => {};
  const token = Symbol();
  pending.set(token, method === 'DELETE' ? '正在删除…' : ['PUT', 'PATCH'].includes(method) ? '正在保存…' : method === 'GET' ? '正在加载…' : '正在提交…');
  clearTimeout(hideTimer);
  if (badge?.isConnected) updateBadge();
  else if (!showTimer) showTimer = setTimeout(() => { showTimer = null; updateBadge(); }, 200);
  let finished = false;
  return () => {
    if (finished) return;
    finished = true;
    pending.delete(token);
    if (pending.size) { if (badge?.isConnected) updateBadge(); return; }
    clearTimeout(showTimer); showTimer = null;
    hideTimer = setTimeout(() => { if (!pending.size) badge?.remove(); }, Math.max(0, shownAt + 250 - performance.now()));
  };
}

const regions = new WeakMap();
export async function withLoading(task, label = '正在加载…', target = document.querySelector('main')) {
  if (!target) return task();
  let region = regions.get(target);
  if (!region) {
    const overlay = document.createElement('div');
    overlay.className = 'app-loading-overlay';
    overlay.innerHTML = loadingMarkup(label);
    region = { count: 0, overlay, busy: target.getAttribute('aria-busy'), timer: setTimeout(() => target.append(overlay), 200) };
    regions.set(target, region);
    target.setAttribute('aria-busy', 'true');
  } else region.overlay.innerHTML = loadingMarkup(label);
  region.count++;
  try { return await task(); }
  finally {
    if (--region.count === 0) {
      clearTimeout(region.timer); region.overlay.remove();
      if (region.busy === null) target.removeAttribute('aria-busy'); else target.setAttribute('aria-busy', region.busy);
      regions.delete(target);
    }
  }
}
