import { disconnectMarket, isMarketSessionRequired, navigateToMarketEntry } from './market-session.js?v=market-sdk-3';

let redirecting = false;
let sessionUserId = '';
const sessionChangeKey = 'jianhuitong.session-change';

export const userStorageKey = key => `${key}:user:${sessionUserId}`;

function notifySessionChange() {
  try { localStorage.setItem(sessionChangeKey, crypto.randomUUID()); } catch {}
}

window.addEventListener('storage', event => {
  if (event.key === sessionChangeKey && sessionUserId) {
    void disconnectMarket({ keepalive: true });
    if (!navigateToMarketEntry()) window.location.replace('/');
  }
});

export function redirectToLogin() {
  if (redirecting) return;
  redirecting = true;
  const next = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  void disconnectMarket({ keepalive: true });
  if (!navigateToMarketEntry()) window.location.replace(`/login?next=${encodeURIComponent(next)}`);
}

export function checkSessionResponse(response) {
  const authenticatedUser = response.headers.get('X-Authenticated-User');
  if (sessionUserId && authenticatedUser && authenticatedUser !== sessionUserId) {
    void disconnectMarket({ keepalive: true });
    if (!navigateToMarketEntry()) window.location.replace('/');
    throw new Error('登录账号已变化，正在刷新页面');
  }
  if (response.status === 428 && isMarketSessionRequired()) {
    void disconnectMarket({ keepalive: true });
    navigateToMarketEntry();
    const error = new Error('集市连接已过期，正在重新连接');
    error.status = 428;
    throw error;
  }
  if (response.status !== 401) return;
  redirectToLogin();
  const error = new Error('登录已过期，请重新登录');
  error.status = 401;
  throw error;
}

export function createSessionControls({ api, beforeLogout, onError }) {
  const button = document.getElementById('logoutButton');
  const status = document.getElementById('sessionStatus');
  let busy = false;
  let marketUser = false;

  window.addEventListener('pageshow', async event => {
    if (!event.persisted) return;
    try { await api('/api/auth/me'); }
    catch (error) { if (error.status !== 401) onError('暂时无法确认登录状态，请检查网络后刷新页面。'); }
  });

  // Inert prevents editing while saving; the capture handler also blocks canvas shortcuts.
  window.addEventListener('keydown', event => {
    if (!busy) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);

  button.addEventListener('click', async () => {
    if (busy) return;
    busy = true;
    button.disabled = true;
    const sections = [...document.querySelectorAll('body > aside, body > main, #modalBackdrop, #imageGenerationOverlay')];
    const previousInert = sections.map(element => element.inert);
    sections.forEach(element => { element.inert = true; });
    status.textContent = marketUser ? '正在保存并重新连接集市…' : '正在保存并退出…';
    status.hidden = false;
    try {
      await beforeLogout();
      status.textContent = marketUser ? '正在重新连接集市…' : '正在退出登录…';
      await api('/api/auth/logout', { method: 'POST' });
      await disconnectMarket({ revoke: false });
      if (!marketUser) notifySessionChange();
      if (!navigateToMarketEntry()) window.location.replace('/login');
    } catch (error) {
      onError(error.status === 401 ? error.message : `退出未完成，当前页面已保留：${error.message}`);
      button.disabled = false;
      sections.forEach((element, index) => { element.inert = previousInert[index]; });
      status.hidden = true;
      busy = false;
      button.focus();
    }
  });

  return {
    async load() {
      const { user } = await api('/api/auth/me');
      if (!user?.username) throw new Error('无法读取当前登录账号');
      sessionUserId = String(user.id);
      marketUser = Boolean(user.market_user);
      if (marketUser) {
        button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5M6.1 6.1a8 8 0 0 1 13.2 3.2M17.9 17.9A8 8 0 0 1 4.7 14.7"/></svg>';
        button.ariaLabel = '重新连接集市';
        button.title = '保存当前内容并重新获取集市用户信息';
      }
      const name = user.display_name || user.username;
      const role = user.market_user
        ? user.market_user.organization?.name || '集市成员 · 个人数据'
        : user.role === 'admin' ? '管理员 · 全部用户数据' : '创作成员 · 个人数据';
      document.getElementById('sessionName').textContent = name;
      document.getElementById('sessionRole').textContent = role;
      document.getElementById('sessionAvatar').textContent = [...name][0].toUpperCase();
      document.getElementById('sessionUser').title = `${name} · ${user.market_user?.workNumber || (user.market_user ? '集市成员' : user.username)} · ${role}`;
      button.disabled = false;
      return user;
    }
  };
}
