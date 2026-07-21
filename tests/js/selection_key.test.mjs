import { readFileSync } from 'node:fs';
import assert from 'node:assert';

const src = readFileSync('static/app.js', 'utf8');

export function test_selection_key_is_v3() {
  assert.ok(src.includes('sizzle_sel_v3_'),
    'selection key must be v3: rich transcripts change every raw line string');
}

export function test_no_v2_key_remains() {
  assert.ok(!src.includes('sizzle_sel_v2_'),
    'a leftover v2 key would restore stale selections that match no rendered line');
}

export function test_every_selection_key_site_uses_the_same_version() {
  const versions = new Set([...src.matchAll(/sizzle_sel_(v\d+)_/g)].map(m => m[1]));
  assert.strictEqual(versions.size, 1,
    `all selection key sites must agree, found: ${[...versions].join(', ')}`);
}
