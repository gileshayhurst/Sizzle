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

// The pool key stores state.pool[].lines — the SAME raw transcript strings the
// selection key stores. _restorePool() runs on every folder load and feeds them
// back into state.checked via _applySliderSelection -> _applyCandidatesToSelection,
// so leaving it unversioned silently defeats the selection-key bump: selections
// restore empty (correct), then the slider repopulates them with dead strings.
export function test_pool_key_is_versioned_too() {
  assert.ok(src.includes('sizzle_pool_v3_'),
    'pool key must be versioned: it persists raw line strings like the selection key');
  assert.ok(!/sizzle_pool_['"]\s*\+/.test(src),
    'an unversioned sizzle_pool_ key would reintroduce stale raw strings');
}

export function test_pool_and_selection_keys_share_a_version() {
  const sel = [...src.matchAll(/sizzle_sel_(v\d+)_/g)].map(m => m[1]);
  const pool = [...src.matchAll(/sizzle_pool_(v\d+)_/g)].map(m => m[1]);
  assert.ok(pool.length > 0, 'pool key must carry a version');
  assert.strictEqual(new Set([...sel, ...pool]).size, 1,
    `pool and selection keys must be bumped together, found: sel=${sel} pool=${pool}`);
}
