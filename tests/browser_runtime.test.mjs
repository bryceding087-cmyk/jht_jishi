import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import vm from 'node:vm';

const source = readFileSync(new URL('../public/browser-runtime.js', import.meta.url), 'utf8');
function harness(crypto) {
  const handlers = new Map();
  const message = { textContent: '正在加载…' }, retry = { hidden: true };
  const document = { documentElement: { dataset: { appBoot: 'loading' } },
    getElementById: id => id === 'appBootMessage' ? message : retry };
  let reloads = 0;
  const context = vm.createContext({ crypto, document,
    window: { addEventListener: (name, fn) => handlers.set(name, fn) },
    location: { reload() { reloads++; } } });
  vm.runInContext(source, context);
  return { context, handlers, document, message, retry, reloads: () => reloads };
}

// Model the HTTP browser: getRandomValues exists, randomUUID does not.
const httpCrypto = { getRandomValues: array => webcrypto.getRandomValues(array) };
const env = harness(httpCrypto);
const values = new Set();
for (let n = 0; n < 1000; n++) {
  const id = httpCrypto.randomUUID();
  assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  values.add(id);
}
assert.equal(values.size, 1000);
assert.equal(env.document.documentElement.dataset.appBoot, 'loading');

const native = () => 'native UUID';
assert.equal(harness({ randomUUID: native }).context.crypto.randomUUID, native);
assert.equal(harness({}).document.documentElement.dataset.appBoot, 'error');
assert.equal(harness(undefined).retry.hidden, false);

env.handlers.get('error')({ target: { tagName: 'IMG' } });
assert.equal(env.document.documentElement.dataset.appBoot, 'loading');
env.handlers.get('error')({ error: new TypeError('fixture startup error') });
assert.equal(env.document.documentElement.dataset.appBoot, 'error');
assert.equal(env.retry.hidden, false);
env.retry.onclick();
assert.equal(env.reloads(), 1);
assert.ok(!env.message.textContent.includes('fixture startup error'));
delete env.document.documentElement.dataset.appBoot;
env.handlers.get('unhandledrejection')({});
assert.equal(env.document.documentElement.dataset.appBoot, undefined);

const index = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
assert.ok(index.indexOf('<script src="browser-runtime.js') < index.indexOf('<script type="module"'));
assert.match(index, /<script src="browser-runtime\.js\?v=http-uuid-1"><\/script>/);
console.log('PASS: HTTP UUID fallback, native preservation, startup error visibility and script order');
