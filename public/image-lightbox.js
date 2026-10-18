// A standalone image viewer; no asset details, editing, or generation actions.
export function createImageLightbox({ onError = () => {} } = {}) {
  let dialog, image, closing = null, revision = 0;

  function finishClose() {
    if (dialog?.open) dialog.close();
  }

  function close() {
    if (!dialog?.open || closing) return;
    if (matchMedia('(prefers-reduced-motion: reduce)').matches || !dialog.animate) {
      finishClose();
      return;
    }
    const current = ++revision;
    dialog.getAnimations().forEach(animation => animation.cancel());
    dialog.dataset.closing = 'true';
    closing = dialog.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 180, easing: 'ease-out', fill: 'forwards' });
    closing.finished.then(() => { if (current === revision) finishClose(); }).catch(() => {});
  }

  function initialize() {
    if (dialog) return;
    dialog = document.createElement('dialog');
    dialog.className = 'image-lightbox';
    dialog.setAttribute('aria-label', '图片预览');
    image = document.createElement('img');
    image.draggable = false;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'image-lightbox-close';
    button.setAttribute('aria-label', '关闭图片预览');
    button.textContent = '×';
    button.onclick = close;
    dialog.append(image, button);
    document.body.append(dialog);
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    dialog.addEventListener('click', event => { if (event.target === dialog) close(); });
    dialog.addEventListener('close', () => {
      ++revision;
      closing?.cancel();
      closing = null;
      delete dialog.dataset.closing;
      image.removeAttribute('src');
    });
    image.addEventListener('error', () => {
      if (!dialog.open) return;
      finishClose();
      onError('图片暂时无法加载，请稍后重试');
    });
  }

  function open(asset) {
    if (!asset?.url || !['image', 'prop'].includes(asset.kind || 'image')) return;
    initialize();
    ++revision;
    closing?.cancel();
    closing = null;
    delete dialog.dataset.closing;
    image.alt = asset.name || '参考图片';
    image.src = asset.url;
    if (!dialog.open) dialog.showModal();
  }

  return { open, close };
}
