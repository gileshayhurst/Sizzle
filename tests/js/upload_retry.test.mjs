import { readFileSync } from 'node:fs';
import assert from 'node:assert';

import { putWithRetry } from '../../static/upload-filters.js';

const src = readFileSync('static/app.js', 'utf8');

// No real delays in tests -- the backoff is exercised for its call count, not
// its wall clock.
const noSleep = () => Promise.resolve();

function fakeFetch(outcomes) {
  const calls = [];
  const impl = async (url, opts) => {
    calls.push({ url, opts });
    const next = outcomes.shift();
    if (next instanceof Error) throw next;
    return { ok: next < 400, status: next };
  };
  return { impl, calls };
}

export async function test_a_clean_put_is_sent_once() {
  const { impl, calls } = fakeFetch([200]);
  const resp = await putWithRetry('https://r2/put', 'BYTES', { fetchImpl: impl, sleep: noSleep });
  assert.strictEqual(resp.status, 200);
  assert.strictEqual(calls.length, 1);
  assert.strictEqual(calls[0].opts.method, 'PUT');
  assert.strictEqual(calls[0].opts.body, 'BYTES');
}

export async function test_a_dropped_connection_is_retried_and_can_succeed() {
  // The case this exists for: one blip mid-transfer used to lose the whole
  // upload run and strand a half-populated session.
  const { impl, calls } = fakeFetch([new Error('network error'), 200]);
  const resp = await putWithRetry('https://r2/put', 'BYTES', { fetchImpl: impl, sleep: noSleep });
  assert.strictEqual(resp.status, 200);
  assert.strictEqual(calls.length, 2);
}

export async function test_server_errors_are_retried() {
  const { impl, calls } = fakeFetch([500, 503, 200]);
  await putWithRetry('https://r2/put', 'BYTES', { fetchImpl: impl, sleep: noSleep });
  assert.strictEqual(calls.length, 3);
}

export async function test_throttling_is_retried() {
  const { impl, calls } = fakeFetch([429, 200]);
  await putWithRetry('https://r2/put', 'BYTES', { fetchImpl: impl, sleep: noSleep });
  assert.strictEqual(calls.length, 2);
}

export async function test_an_expired_signature_is_not_retried() {
  // Re-sending 1.4 GB to be refused again helps nobody, and would triple the
  // time spent discovering the URL is dead.
  const { impl, calls } = fakeFetch([403, 200]);
  await assert.rejects(
    () => putWithRetry('https://r2/put', 'BYTES', { fetchImpl: impl, sleep: noSleep }),
    /403/,
  );
  assert.strictEqual(calls.length, 1, '403 must be terminal');
}

export async function test_it_gives_up_after_the_attempt_budget() {
  const { impl, calls } = fakeFetch([500, 500, 500, 200]);
  await assert.rejects(
    () => putWithRetry('https://r2/put', 'BYTES', { fetchImpl: impl, sleep: noSleep }),
    /500/,
  );
  assert.strictEqual(calls.length, 3);
}

export async function test_backoff_grows_between_attempts() {
  const waits = [];
  const { impl } = fakeFetch([500, 500, 200]);
  await putWithRetry('https://r2/put', 'BYTES', {
    fetchImpl: impl,
    sleep: ms => { waits.push(ms); return Promise.resolve(); },
  });
  assert.deepStrictEqual(waits, [1000, 2000]);
}

export function test_the_upload_loop_actually_uses_the_retry() {
  // A bare fetch(putUrl, {method:'PUT'}) in doUpload would silently undo this.
  assert.ok(/putWithRetry\(putUrl, file\)/.test(src),
    'doUpload must PUT via putWithRetry');
  assert.ok(!/fetch\(putUrl,/.test(src),
    'doUpload must not PUT to the presigned URL directly');
}
