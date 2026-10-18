import { initializeMarket, isMarketEntry } from './market-session.js?v=market-sdk-3';

// Keep page content from starting the browser's native drag operation.
document.addEventListener('dragstart', event => event.preventDefault(), true);

const form = document.querySelector('#loginForm');
const username = document.querySelector('#username');
const password = document.querySelector('#password');
const toggle = document.querySelector('#passwordToggle');
const submit = document.querySelector('#loginButton');
const errorBox = document.querySelector('#loginError');
let submitting = false;

document.querySelector('#currentYear').textContent = String(new Date().getFullYear());

toggle.addEventListener('click', () => {
  const visible = password.type === 'password';
  password.type = visible ? 'text' : 'password';
  toggle.setAttribute('aria-pressed', String(visible));
  toggle.setAttribute('aria-label', visible ? '隐藏密码' : '显示密码');
});

const helpButton = document.querySelector('#helpButton');
helpButton.addEventListener('click', () => {
  const helpText = document.querySelector('#helpText');
  helpText.hidden = !helpText.hidden;
  helpButton.setAttribute('aria-expanded', String(!helpText.hidden));
});

for (const event of ['keydown', 'keyup']) {
  password.addEventListener(event, (e) => {
    document.querySelector('#capsLockHint').hidden = !e.getModifierState('CapsLock');
  });
}
password.addEventListener('blur', () => { document.querySelector('#capsLockHint').hidden = true; });

function showError(message, fields = []) {
  errorBox.textContent = message;
  errorBox.hidden = false;
  fields.forEach(field => field.setAttribute('aria-invalid', 'true'));
}

for (const field of [username, password]) {
  field.addEventListener('input', () => {
    username.removeAttribute('aria-invalid');
    password.removeAttribute('aria-invalid');
    errorBox.hidden = true;
  });
}

function setBusy(busy) {
  submitting = busy;
  submit.disabled = busy;
  username.readOnly = busy;
  password.readOnly = busy;
  document.querySelector('#remember').disabled = busy;
  form.setAttribute('aria-busy', String(busy));
  submit.querySelector('.button-label').textContent = busy ? '正在登录' : '登录平台';
  submit.querySelector('.button-arrow').hidden = busy;
  submit.querySelector('.spinner').hidden = !busy;
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (submitting) return;
  errorBox.hidden = true;
  username.removeAttribute('aria-invalid');
  password.removeAttribute('aria-invalid');
  if (!username.value.trim()) {
    showError('请输入账号。', [username]);
    username.focus();
    return;
  }
  if (!password.value) {
    showError('请输入密码。', [password]);
    password.focus();
    return;
  }
  const body = { username: username.value.trim(), password: password.value, remember: document.querySelector('#remember').checked };
  setBusy(true);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin', body: JSON.stringify(body), signal: controller.signal,
    });
    if (!response.ok) {
      if (response.status === 401) {
        showError('账号或密码不正确，请重新输入。', [username, password]);
        password.focus();
        password.select();
      } else if (response.status === 429) {
        const seconds = Number(response.headers.get('Retry-After'));
        showError(seconds > 0 ? `尝试次数较多，请 ${Math.ceil(seconds / 60)} 分钟后重试。` : '尝试次数较多，请稍后再试。');
      } else {
        showError(response.status >= 500 ? '登录服务暂时不可用，请稍后重试。' : '暂时无法登录，请刷新页面后重试。');
      }
      return;
    }
    password.value = '';
    try { localStorage.setItem('jianhuitong.session-change', crypto.randomUUID()); } catch {}
    // Bypass an older, cached pre-authentication workspace document on upgrade.
    window.location.replace(isMarketEntry() ? '/market' : '/?v=market-sdk-3');
  } catch (error) {
    showError(error.name === 'AbortError' ? '登录请求超时，请重试。' : '无法连接到服务器，请检查网络后重试。');
  } finally {
    clearTimeout(timeout);
    body.password = '';
    setBusy(false);
  }
});

// A restored browser tab must recheck its server-side session.
window.addEventListener('pageshow', async (event) => {
  if (!event.persisted) return;
  try {
    const response = await fetch('/api/auth/me', { credentials: 'same-origin', cache: 'no-store' });
    if (response.ok) window.location.replace(isMarketEntry() ? '/market' : '/');
  } catch { /* The form remains available when the connection returns. */ }
});

void initializeMarket({ authenticated: false });
