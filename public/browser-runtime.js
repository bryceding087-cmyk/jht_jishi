// Run as a classic script before application modules, including their startup code.
(() => {
  function showStartupError() {
    if (document.documentElement.dataset.appBoot !== 'loading') return;
    const message = document.getElementById('appBootMessage');
    const retry = document.getElementById('appBootRetry');
    if (!message || !retry) return;
    document.documentElement.dataset.appBoot = 'error';
    message.textContent = '页面启动失败，请重新加载。若仍然失败，请将控制台错误提供给管理员。';
    retry.hidden = false;
    retry.onclick = () => location.reload();
  }

  // Errors before boot() (including module evaluation) must not leave a spinner.
  window.addEventListener('error', event => {
    if (event.error || event.message || event.target?.tagName === 'SCRIPT') showStartupError();
  }, true);
  window.addEventListener('unhandledrejection', showStartupError);

  const browserCrypto = globalThis.crypto;
  if (typeof browserCrypto?.randomUUID === 'function') return;
  // getRandomValues is available on ordinary HTTP pages; randomUUID is restricted
  // to secure contexts. Keep cryptographic randomness and RFC 9562 v4 bit layout.
  if (typeof browserCrypto?.getRandomValues !== 'function') {
    showStartupError();
    return;
  }
  Object.defineProperty(browserCrypto, 'randomUUID', {
    configurable: true,
    writable: true,
    value() {
      const bytes = browserCrypto.getRandomValues(new Uint8Array(16));
      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      bytes[8] = (bytes[8] & 0x3f) | 0x80;
      const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    },
  });
})();
