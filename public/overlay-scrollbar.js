// A shared overlay scrollbar never reserves or changes content width.
export function installOverlayScrollbar(viewport, { floating = false, enabled = () => true } = {}) {
  if (!viewport) return;
  const region = floating ? viewport : viewport.parentElement;
  const track = document.createElement('div');
  track.className = 'recent-scroll-track' + (floating ? ' home-scroll-track' : '');
  track.setAttribute('aria-hidden', 'true');
  const thumb = document.createElement('div');
  thumb.className = 'recent-scroll-thumb';
  track.append(thumb);
  (floating ? document.body : region).append(track);
  let timer;
  let inside = false;
  let drag = null;
  let travel = 0;
  let range = 0;

  function hide() {
    clearTimeout(timer);
    if (!drag) track.classList.remove('is-scrolling');
  }
  function update() {
    range = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
    const height = region.clientHeight;
    if (floating) {
      const bounds = viewport.getBoundingClientRect();
      Object.assign(track.style, { position: 'fixed', left: `${bounds.right - 8}px`, top: `${bounds.top}px`, bottom: 'auto', height: `${height}px`, zIndex: '30' });
    }
    const size = Math.min(height, Math.max(28, height * viewport.clientHeight / Math.max(1, viewport.scrollHeight)));
    travel = height - size;
    thumb.style.height = `${size}px`;
    thumb.style.transform = `translateY(${range ? viewport.scrollTop / range * travel : 0}px)`;
    track.hidden = !enabled() || !viewport.getClientRects().length || !range || !height;
    if (track.hidden) hide();
  }
  function reveal() {
    update();
    if (track.hidden) return;
    clearTimeout(timer);
    track.classList.add('is-scrolling');
    if (!drag) timer = setTimeout(hide, 850);
  }
  region.addEventListener('pointerenter', () => { inside = true; });
  region.addEventListener('pointerleave', () => { inside = false; hide(); });
  viewport.addEventListener('wheel', event => { if (event.deltaY) reveal(); }, { passive: true });
  viewport.addEventListener('scroll', () => {
    update();
    if (inside || drag || viewport.contains(document.activeElement)) reveal();
  }, { passive: true });
  thumb.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    event.preventDefault();
    drag = { y: event.clientY, scroll: viewport.scrollTop };
    thumb.setPointerCapture(event.pointerId);
    track.classList.add('is-dragging');
    reveal();
  });
  thumb.addEventListener('pointermove', event => {
    if (drag && travel > 0) viewport.scrollTop = drag.scroll + (event.clientY - drag.y) / travel * range;
  });
  thumb.addEventListener('lostpointercapture', () => {
    drag = null;
    track.classList.remove('is-dragging');
    if (inside) reveal(); else hide();
  });
  thumb.addEventListener('pointerup', event => { thumb.releasePointerCapture(event.pointerId); });
  const resize = new ResizeObserver(update);
  resize.observe(viewport);
  resize.observe(region);
  new MutationObserver(update).observe(viewport, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['class', 'hidden', 'data-home-stage'] });
  window.addEventListener('resize', update);
  update();
}
