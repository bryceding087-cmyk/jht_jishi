import './vendor/aimarket.js?v=sdk-1';

const CONFIG_URL = '/api/integrations/aimarket/config';
const SESSION_URL = '/api/integrations/aimarket/session';
const LOGIN_URL = '/api/integrations/aimarket/login';
const SAFE_MESSAGES = Object.freeze({
  INVALID_OPTIONS: '集市接入配置无效，请联系管理员。',
  NOT_EMBEDDED: '请从集市打开建绘通，或在独立窗口登录。',
  ABORTED: '集市连接已取消。',
  TIMEOUT: '集市连接超时，请重试。',
  INVALID_CONTEXT: '集市接入信息无效，请重新打开应用。',
  CONNECT_REJECTED: '集市暂未授权此次连接，请重新打开应用。',
  POSTMESSAGE_FAILED: '无法连接集市，请重试。',
  CRYPTO_UNAVAILABLE: '当前浏览器不支持安全接入，请使用新版浏览器。',
  DISABLED: '集市接入尚未启用，可在独立窗口登录。',
  AGENT_MISMATCH: '当前应用与集市配置不一致，请联系管理员。',
  LOGIN_REQUIRED: '请先登录建绘通，再重新连接集市。',
  PARENT_NOT_READY: '集市连接尚未就绪，请重新连接后打开画布。',
  SESSION_REJECTED: '无法绑定集市会话，请重新登录或联系管理员。',
  NETWORK_ERROR: '暂时无法连接接入服务，请检查网络后重试。',
});

let controller = null;
let activeContext = null;
let pending = null;
let sequence = 0;
let bindingId = '';
let mediaToken = '';
let sdkLogin = false;
let ownsBinding = false;
let bannerHideTimer = null;
let bannerAnimationTimer = null;

function clearBannerTimers() {
  clearTimeout(bannerHideTimer);
  clearTimeout(bannerAnimationTimer);
  bannerHideTimer = bannerAnimationTimer = null;
}
let options = { authenticated: false, onStatus: null };
let lastStatus = Object.freeze({ state: 'idle', connected: false, authenticated: false, message: '' });

export function isMarketEntry() {
  return location.pathname === '/market' || new URLSearchParams(location.search).get('market') === '1';
}

function isInternalCanvas() {
  const query = new URLSearchParams(location.search);
  if (window.parent === window || query.get('embedded') !== '1' || query.get('view') !== 'canvas') return false;
  try { return window.parent.location.origin === location.origin; } catch { return false; }
}

function hasMarketParent() {
  if (!isInternalCanvas()) return false;
  try {
    return window.parent.location.pathname === '/market'
      || new URLSearchParams(window.parent.location.search).get('market') === '1';
  } catch { return false; }
}

export function isMarketSessionRequired() {
  return isInternalCanvas() ? hasMarketParent() : isMarketEntry();
}

/** Only a verified same-origin market parent may be navigated by its canvas. */
export function navigateToMarketEntry() {
  if (hasMarketParent()) { window.parent.location.replace('/market'); return true; }
  if (!isInternalCanvas() && isMarketEntry()) { window.location.replace('/market'); return true; }
  return false;
}

function validBinding(value) {
  return typeof value === 'string' && /^[A-Za-z0-9_-]{16,200}$/.test(value);
}

/** A target URL is required: never forward the binding to model gateways. */
export function withMarketSession(requestOptions = {}, requestUrl = '') {
  const headers = new Headers(requestOptions.headers || {});
  headers.delete('X-JHT-Market-Session');
  let businessRequest = false;
  try {
    const target = new URL(requestUrl, location.origin);
    businessRequest = Boolean(requestUrl && target.origin === location.origin && !target.username && !target.password
      && target.pathname.startsWith('/api/') && (!target.pathname.startsWith('/api/auth/')
        || sdkLogin && ['/api/auth/me', '/api/auth/logout'].includes(target.pathname))
      && !target.pathname.startsWith('/api/integrations/aimarket/'));
  } catch { /* Invalid or external targets never receive the session header. */ }
  if (businessRequest && !bindingId && isMarketSessionRequired()) throw issue(hasMarketParent() ? 'PARENT_NOT_READY' : 'LOGIN_REQUIRED');
  if (businessRequest && bindingId) headers.set('X-JHT-Market-Session', bindingId);
  let body = requestOptions.body;
  // Media capabilities are presentation-only and must never enter saved records.
  if (typeof body === 'string' && headers.get('Content-Type')?.includes('application/json')) {
    try { body = JSON.stringify(mapMediaData(JSON.parse(body), false)); } catch { /* Server validates malformed JSON. */ }
  }
  return { ...requestOptions, ...(body === undefined ? {} : { body }), headers };
}

export function marketMediaUrl(value, authorize = true) {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')) return value;
  try {
    const url = new URL(value, location.origin);
    if (url.origin !== location.origin || !(url.pathname.startsWith('/uploads/') || url.pathname === '/api/scripts/template')) return value;
    url.searchParams.delete('jht_media');
    if (authorize && mediaToken) url.searchParams.set('jht_media', mediaToken);
    return url.pathname + url.search + url.hash;
  } catch { return value; }
}

function mapMediaData(value, authorize) {
  if (typeof value === 'string') return marketMediaUrl(value, authorize);
  if (Array.isArray(value)) return value.map(item => mapMediaData(item, authorize));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, mapMediaData(item, authorize)]));
  return value;
}

export function marketResponseData(value) { return mapMediaData(value, true); }

function inheritBinding(signal) {
  return new Promise(resolve => {
    const requestId = crypto.randomUUID();
    let timer;
    function finish(value = '') {
      clearTimeout(timer);
      window.removeEventListener('message', receive);
      signal.removeEventListener('abort', aborted);
      resolve(value);
    }
    function aborted() { finish(); }
    function receive(event) {
      if (event.origin !== location.origin || event.source !== window.parent
        || event.data?.type !== 'jht-market-session:response' || event.data.requestId !== requestId) return;
      mediaToken = validBinding(event.data.media_token) ? event.data.media_token : '';
      sdkLogin = event.data.sdk_login === true;
      finish(validBinding(event.data.binding_id) ? event.data.binding_id : '');
    }
    window.addEventListener('message', receive);
    signal.addEventListener('abort', aborted, { once: true });
    // Old standalone parent pages may not implement this optional protocol.
    timer = setTimeout(() => finish(), 1500);
    window.parent.postMessage({ type: 'jht-market-session:request', requestId }, location.origin);
  });
}

window.addEventListener('message', event => {
  if (event.origin !== location.origin || event.data?.type !== 'jht-market-session:request'
    || typeof event.data.requestId !== 'string' || event.data.requestId.length > 200
    || ![...document.querySelectorAll('iframe')].some(frame => frame.contentWindow === event.source)) return;
  event.source.postMessage({ type: 'jht-market-session:response', requestId: event.data.requestId,
    binding_id: bindingId, media_token: mediaToken, sdk_login: sdkLogin }, location.origin);
});

function issue(code) {
  const error = new Error(SAFE_MESSAGES[code] || SAFE_MESSAGES.NETWORK_ERROR);
  error.code = code;
  return error;
}

function banner() {
  let element = document.getElementById('marketConnection');
  if (element) return element;
  if (!document.getElementById('marketStylesheet')) {
    const stylesheet = document.createElement('link');
    stylesheet.id = 'marketStylesheet';
    stylesheet.rel = 'stylesheet';
    stylesheet.href = '/market.css?v=notice-2';
    document.head.append(stylesheet);
  }
  element = document.createElement('div');
  element.id = 'marketConnection';
  element.className = 'market-banner';
  element.setAttribute('role', 'status');
  element.setAttribute('aria-live', 'polite');
  const indicator = document.createElement('span');
  indicator.className = 'market-indicator';
  indicator.setAttribute('aria-hidden', 'true');
  const label = document.createElement('span');
  label.id = 'marketConnectionMessage';
  label.className = 'market-message';
  const retry = document.createElement('button');
  retry.type = 'button';
  retry.id = 'marketConnectionRetry';
  retry.className = 'market-retry';
  retry.textContent = '重新连接';
  retry.addEventListener('click', () => {
    if (hasMarketParent() && navigateToMarketEntry()) return;
    void initializeMarket(options);
  });
  const standalone = document.createElement('a');
  standalone.className = 'market-standalone';
  standalone.href = '/login';
  standalone.target = '_blank';
  standalone.rel = 'noopener noreferrer';
  standalone.textContent = '独立窗口登录';
  element.append(indicator, label, retry, standalone);
  document.body.append(element);
  return element;
}

function publish(state, message, connected = false, visible = true) {
  clearBannerTimers();
  lastStatus = Object.freeze({ state, message, connected, authenticated: options.authenticated });
  if (visible) {
    const element = banner();
    element.dataset.state = state;
    element.dataset.dismissing = 'false';
    element.hidden = false;
    document.getElementById('marketConnectionMessage').textContent = message;
    const retry = document.getElementById('marketConnectionRetry');
    retry.hidden = state !== 'error';
    retry.disabled = state === 'connecting';
    if (state === 'connected') {
      bannerHideTimer = setTimeout(() => {
        element.dataset.dismissing = 'true';
        bannerAnimationTimer = setTimeout(() => { element.hidden = true; }, 300);
      }, 3000);
    }
  }
  try { options.onStatus?.(lastStatus); } catch { /* Observers cannot interrupt credential cleanup. */ }
  return lastStatus;
}

function forgetContext(value = activeContext) {
  if (value) value.apiKey = '';
  if (value === activeContext) activeContext = null;
}

function validateConfig(config) {
  if (!config?.enabled) throw issue('DISABLED');
  try {
    const url = new URL(config.market_origin);
    if (!['http:', 'https:'].includes(url.protocol) || url.origin !== config.market_origin
      || config.uses_session_key !== true || typeof config.agent_id !== 'string') throw issue('INVALID_OPTIONS');
  } catch { throw issue('INVALID_OPTIONS'); }
  return config;
}

function sessionPayload(context) {
  const { user, agent } = context;
  // Send only the SDK contract. A parent message cannot smuggle model URLs,
  // local roles, or arbitrary properties into the application session.
  return {
    user: {
      id: user.id, name: user.name, accountType: user.accountType, workNumber: user.workNumber,
      organization: user.organization === null ? null : {
        id: user.organization.id, name: user.organization.name, type: user.organization.type,
      },
    },
    agent: { id: agent.id, name: agent.name },
    apiKey: context.apiKey,
  };
}

/** The returned status never contains the SDK context or credential. */
export function initializeMarket({ authenticated = false, onStatus = null } = {}) {
  const nextOptions = { authenticated: authenticated === true, onStatus };
  if (pending && options.authenticated === nextOptions.authenticated) return pending;
  options = nextOptions;
  if (isInternalCanvas()) {
    controller?.abort();
    controller = new AbortController();
    const attempt = ++sequence;
    pending = inheritBinding(controller.signal).then(value => {
      if (attempt !== sequence) return lastStatus;
      bindingId = value;
      ownsBinding = false;
      pending = null;
      if (!bindingId && hasMarketParent()) return publish('error', SAFE_MESSAGES.PARENT_NOT_READY);
      return publish('inherited', '', Boolean(bindingId), false);
    });
    return pending;
  }
  if (!isMarketEntry()) return Promise.resolve(publish('standalone', '', false, false));
  if (lastStatus.connected && lastStatus.authenticated === options.authenticated) return Promise.resolve(lastStatus);
  if (window.parent === window) return Promise.resolve(publish('error', SAFE_MESSAGES.NOT_EMBEDDED));

  controller?.abort();
  forgetContext();
  const attempt = ++sequence;
  const attemptController = new AbortController();
  controller = attemptController;
  publish('connecting', '正在连接集市…');
  pending = (async () => {
    let context = null;
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; attemptController.abort(); }, 25000);
    try {
      const response = await fetch(CONFIG_URL, { credentials: 'same-origin', cache: 'no-store', signal: attemptController.signal });
      if (!response.ok) throw issue('NETWORK_ERROR');
      const config = validateConfig(await response.json());
      context = await globalThis.aimarket.connect({ marketOrigin: config.market_origin, signal: attemptController.signal });
      if (attempt !== sequence || attemptController.signal.aborted) throw issue('ABORTED');
      activeContext = context;
      if (config.agent_id && context.agent.id !== config.agent_id) throw issue('AGENT_MISMATCH');
      if (!options.authenticated && !config.sdk_login) return publish('login-required', '已连接集市，请使用建绘通账号登录。', true);

      const binding = await fetch(config.sdk_login ? LOGIN_URL : SESSION_URL, {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sessionPayload(context)), signal: attemptController.signal,
      });
      if (attempt !== sequence || attemptController.signal.aborted) throw issue('ABORTED');
      if (binding.status === 401) throw issue('LOGIN_REQUIRED');
      if (!binding.ok) throw issue('SESSION_REJECTED');
      const result = await binding.json();
      if (attempt !== sequence || attemptController.signal.aborted) throw issue('ABORTED');
      if (result.connected !== true || !validBinding(result.binding_id)
        || config.sdk_login === true && !validBinding(result.media_token)) throw issue('SESSION_REJECTED');
      bindingId = result.binding_id;
      sdkLogin = config.sdk_login === true;
      mediaToken = validBinding(result.media_token) ? result.media_token : '';
      ownsBinding = true;
      // The opaque reference stays in memory, separate from the SDK context.
      return publish('connected', sdkLogin ? '已连接集市 · 个人工作区' : '已连接集市 · 使用当前建绘通账号', true);
    } catch (error) {
      if (attempt !== sequence) return lastStatus;
      const code = timedOut ? 'TIMEOUT' : error?.name === 'AbortError' ? 'ABORTED' : error?.code;
      return publish('error', SAFE_MESSAGES[code] || SAFE_MESSAGES.NETWORK_ERROR);
    } finally {
      clearTimeout(timeout);
      forgetContext(context);
      if (attempt === sequence) pending = null;
    }
  })();
  return pending;
}

/** Revoke only a binding attempted by this page; nested canvases never revoke it. */
export async function disconnectMarket({ revoke = true, keepalive = false } = {}) {
  clearBannerTimers();
  ++sequence;
  controller?.abort();
  controller = null;
  pending = null;
  forgetContext();
  const revokedBinding = revoke && ownsBinding ? bindingId : '';
  bindingId = '';
  mediaToken = '';
  sdkLogin = false;
  ownsBinding = false;
  lastStatus = Object.freeze({ state: 'disconnected', message: '', connected: false, authenticated: options.authenticated });
  const element = document.getElementById('marketConnection');
  if (element) element.hidden = true;
  if (revokedBinding) {
    try {
      await fetch(SESSION_URL, {
        method: 'DELETE', credentials: 'same-origin', cache: 'no-store', keepalive,
        headers: { 'X-JHT-Market-Session': revokedBinding },
      });
    } catch { /* Page exit delivery is best effort; the server also expires keys. */ }
  }
  return lastStatus;
}

window.addEventListener('pagehide', () => { void disconnectMarket({ keepalive: true }); });
window.addEventListener('pageshow', event => { if (event.persisted && isMarketEntry()) location.reload(); });
