// Match the reference's short fades and positional easing without delaying drag.
const surfaces = new WeakMap();
const ease = 'cubic-bezier(0.4, 0, 0.2, 1)';

export function surfaceIsOpen(element) {
  return !!element && !element.hidden && element.dataset.open !== 'false';
}

export function setSurfaceVisible(element, visible, options = {}) {
  if (!element) return;
  const previous = surfaces.get(element);
  if (previous?.visible === visible && previous.key === options.key &&
      (visible ? !element.hidden : element.hidden || previous.animation)) return previous.promise || Promise.resolve();
  previous?.animation?.cancel();
  const entry = { visible, key: options.key, animation: null };
  surfaces.set(element, entry);
  element.dataset.open = String(visible);
  element.inert = !visible;
  element.setAttribute('aria-hidden', String(!visible));
  if (visible) element.hidden = false;
  if (matchMedia('(prefers-reduced-motion: reduce)').matches || !element.animate || (!visible && element.hidden)) {
    element.hidden = !visible;
    return Promise.resolve();
  }
  const keyframes = visible
    ? [{ opacity: 0, translate: '0 6px', scale: '.98' }, { opacity: 1, translate: '0 0', scale: '1' }]
    : [{ opacity: 1, translate: '0 0', scale: '1' }, { opacity: 0, translate: '0 4px', scale: '.98' }];
  entry.animation = element.animate(keyframes, { duration: visible ? 180 : 120, easing: ease, fill: 'both' });
  entry.promise = entry.animation.finished.then(() => {
    if (surfaces.get(element) !== entry) return;
    if (!visible) element.hidden = true;
    entry.animation.cancel();
    entry.animation = null;
  }).catch(() => {});
  return entry.promise;
}
