// Assign media URLs only when their card reaches the clipped viewport.
// Native loading="lazy" may fetch several screens ahead of the user.
export function createViewportObserver(onVisible) {
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting || !entry.target.isConnected || !entry.target.getClientRects().length) continue;
      observer.unobserve(entry.target);
      onVisible(entry.target);
    }
  }, { rootMargin: '0px', threshold: 0 });
  return {
    observe(elements) {
      observer.disconnect();
      observer.takeRecords();
      for (const element of elements) observer.observe(element);
    }
  };
}

export function revealMedia(root) {
  const media = root.matches('[data-media-src]') ? [root] : root.querySelectorAll('[data-media-src]');
  for (const element of media) {
    const source = element.dataset.mediaSrc;
    element.removeAttribute('data-media-src');
    if (!source) continue;
    if (element.tagName === 'VIDEO' || element.tagName === 'AUDIO') element.preload = 'metadata';
    element.src = source;
  }
}
