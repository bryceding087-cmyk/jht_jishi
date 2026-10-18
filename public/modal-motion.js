// Global default for centered modals, including future components.
const selector = '.modal, [role="dialog"][aria-modal="true"], dialog[open], [data-centered-modal]';
const backdropSelector = '.modal-backdrop, .ig-overlay, .ag-overlay, [data-modal-backdrop]';
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const entries = new Map();
const duration = 380;
const easing = 'cubic-bezier(.22, 1, .36, 1)';
let origin = null;

// Capture before handlers remove menus, await requests or move focus.
document.addEventListener('click', event => {
  if (!(event.target instanceof Element)) return;
  if (event.detail && (event.clientX || event.clientY)) {
    origin = { x: event.clientX, y: event.clientY, time: performance.now() };
  } else rememberElement(event.target);
}, true);
document.addEventListener('keydown', event => {
  if (event.key === 'Enter' || event.key === ' ') rememberElement(event.target);
}, true);

function rememberElement(element) {
  if (!(element instanceof Element)) return;
  const rect = element.getBoundingClientRect();
  if (rect.width && rect.height) origin = { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, time: performance.now() };
}

function visible(element) {
  if (!element.isConnected || element.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
  if (element instanceof HTMLDialogElement && !element.open) return false;
  const style = getComputedStyle(element);
  return style.visibility !== 'hidden' && style.display !== 'none' && element.getClientRects().length > 0;
}

function stop(entry) {
  entry.animation?.cancel();
  entry.backdropAnimation?.cancel();
}

function enter(element, host) {
  if (reducedMotion.matches || !element.animate) return { element };
  const rect = element.getBoundingClientRect();
  const source = origin && performance.now() - origin.time < 10000 ? origin : null;
  const dx = source ? Math.max(0, Math.min(innerWidth, source.x)) - (rect.x + rect.width / 2) : 0;
  const dy = source ? Math.max(0, Math.min(innerHeight, source.y)) - (rect.y + rect.height / 2) : 18;
  const style = getComputedStyle(element);
  const frames = [
    { opacity: 0, translate: `${dx}px ${dy}px`, scale: source ? .24 : .88, transformOrigin: '50% 50%' },
    { opacity: style.opacity, translate: style.translate === 'none' ? '0px 0px' : style.translate, scale: style.scale === 'none' ? 1 : style.scale, transformOrigin: '50% 50%' },
  ];
  const entry = { element, frames, animation: element.animate(frames, { duration, easing, fill: 'both' }) };
  releaseAfterFinish(entry.animation);
  if (host !== element) {
    entry.backdropAnimation = host.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 220, easing, fill: 'both' });
    releaseAfterFinish(entry.backdropAnimation);
  }
  return entry;
}

function releaseAfterFinish(animation) {
  // Leave no permanent transform affecting fixed children, scrolling or media.
  animation.finished.then(() => animation.cancel()).catch(() => {});
}

function sync() {
  const open = new Map();
  document.querySelectorAll(selector).forEach(element => {
    if (!visible(element)) return;
    const host = element.closest(backdropSelector) || element;
    if (!open.has(host)) open.set(host, element);
  });
  for (const [host, entry] of entries) {
    if (!open.has(host)) { stop(entry); entries.delete(host); }
  }
  for (const [host, element] of open) {
    const previous = entries.get(host);
    const screen = `${element.className}|${element.querySelector('h2')?.textContent || ''}`;
    if (!previous || previous.screen !== screen) {
      // Shared hosts also open distinct dialogs without hiding the backdrop,
      // for example asset details -> delete confirmation.
      if (previous) stop(previous);
      entries.set(host, { ...enter(element, host), screen });
    }
    else if (previous.element !== element) {
      // Generators replace the dialog DOM on updates. Continue the original
      // timeline, then leave subsequent progress updates stationary.
      const elapsed = previous.animation?.currentTime;
      previous.animation?.cancel();
      previous.element = element;
      if (!reducedMotion.matches && typeof elapsed === 'number' && elapsed < duration) {
        previous.animation = element.animate(previous.frames, { duration, easing, fill: 'both' });
        previous.animation.currentTime = elapsed;
        releaseAfterFinish(previous.animation);
      }
    }
  }
}

// Also observe ancestor visibility changes. Mutation batches run before paint.
new MutationObserver(sync).observe(document.documentElement, {
  subtree: true, childList: true, attributes: true,
  attributeFilter: ['hidden', 'open', 'class', 'style', 'role', 'aria-modal', 'aria-hidden', 'inert', 'data-centered-modal'],
});
reducedMotion.addEventListener('change', () => {
  if (reducedMotion.matches) entries.forEach(stop);
});
sync();
