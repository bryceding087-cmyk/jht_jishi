// Silent card previews: load in view, play on hover/focus, stop off screen.
export function createVideoHoverPreview() {
  let active = null, unbind = [];
  const reset = video => { video.pause(); try { video.currentTime = .05; } catch {} };
  function stop() { const previous = active; active = null; if (previous) reset(previous.video); }
  function load(video) {
    if (!video.dataset.mediaSrc) return;
    video.preload = 'metadata';
    video.src = video.dataset.mediaSrc;
    video.removeAttribute('data-media-src');
  }
  async function play(card, video) {
    if (document.hidden || !card.isConnected || !card.getClientRects().length || active?.video === video) return;
    stop(); load(video);
    const pending = { card, video }; active = pending;
    video.muted = true; video.defaultMuted = true; video.loop = true;
    try {
      await video.play();
      // A late play() must not restart an abandoned preview or stop a newer one.
      if (active !== pending && active?.video !== video) reset(video);
    } catch {
      if (active === pending) stop();
    }
  }
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (entry.isIntersecting && entry.target.isConnected && entry.target.getClientRects().length) {
        const video = entry.target.querySelector('video'); if (video) load(video);
      } else if (active?.card === entry.target) stop();
    }
  }, { rootMargin: '0px', threshold: 0 });
  function listen(target, event, handler) {
    target.addEventListener(event, handler);
    unbind.push(() => target.removeEventListener(event, handler));
  }
  function bind(cards) {
    stop(); unbind.forEach(remove => remove()); unbind = [];
    observer.disconnect(); observer.takeRecords();
    for (const card of cards) {
      const video = card.querySelector('video'); if (!video) continue;
      observer.observe(card);
      listen(card, 'pointerenter', event => { if (event.pointerType !== 'touch') play(card, video); });
      listen(card, 'pointerleave', () => { if (active?.card === card) stop(); });
      listen(card, 'focus', () => play(card, video));
      listen(card, 'blur', () => { if (active?.card === card) stop(); });
      listen(video, 'error', () => { if (active?.video === video) stop(); });
    }
  }
  document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); });
  window.addEventListener('blur', stop);
  window.addEventListener('pagehide', stop);
  return { bind, stop };
}
