import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { normalizeContentError, describeTaskError, CONTENT_REJECTION_MESSAGE } from '../public/task-errors.js';

const cases = JSON.parse(readFileSync(new URL('./content_rejection_cases.json', import.meta.url), 'utf8'));
for (const {error, rejected} of cases) {
  assert.deepEqual(normalizeContentError(error), rejected ? CONTENT_REJECTION_MESSAGE : error);
  if (rejected) {
    assert.equal(describeTaskError({error}).message, CONTENT_REJECTION_MESSAGE);
    const saved = '模型接口返回 HTTP 400: ' + JSON.stringify({error});
    assert.equal(normalizeContentError(saved), CONTENT_REJECTION_MESSAGE);
    assert.equal(describeTaskError({error:saved}).message, CONTENT_REJECTION_MESSAGE);
  }
}
assert.equal(describeTaskError({error:{code:'SetLimitExceeded'}}).quotaLimited, true);
assert.equal(describeTaskError({error:'invalid image size'}).message, 'invalid image size');
console.log(`PASS: ${cases.length} moderation and unrelated error cases, including saved HTTP failures`);
