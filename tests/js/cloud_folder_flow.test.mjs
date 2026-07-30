import { readFileSync } from 'node:fs';
import assert from 'node:assert';

import {
  GENERATED_REELS_MARKER,
  excludeGeneratedReels,
  parseGeneratedReels,
} from '../../static/upload-filters.js';

const src = readFileSync('static/app.js', 'utf8');

// ── Bug 1: every cloud session-open must encode first ─────────────────────────
//
// _encodeSession was wired only into doUpload, so clicking a folder in the
// recent list went straight to openFolder and no encode was ever dispatched --
// silently, because nothing was attempted to fail. The fix makes openFolder the
// chokepoint, so paths added later are covered without anyone remembering to.

function openFolderBody() {
  const start = src.indexOf('async function openFolder(');
  assert.ok(start !== -1, 'openFolder not found in static/app.js');
  let depth = 0, i = src.indexOf('{', start);
  const open = i;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && --depth === 0) return src.slice(open, i + 1);
  }
  throw new Error('unbalanced braces in openFolder');
}

export function test_open_folder_encodes_before_loading_the_folder() {
  const body = openFolderBody();
  const encodeAt = body.indexOf('_encodeSession(');
  const loadAt = body.indexOf("fetch('/load-folder'");
  assert.ok(encodeAt !== -1, 'openFolder must encode the session');
  assert.ok(loadAt !== -1, 'openFolder must load the folder');
  // Ordering matters: selections are keyed by raw line text, so a plain->rich
  // swap under an already-open folder invalidates every restored selection.
  assert.ok(encodeAt < loadAt, 'openFolder must encode BEFORE loading the folder');
}

export function test_encoding_is_wired_once_not_per_call_site() {
  // A chokepoint, not a patch applied to whichever call sites we remembered.
  const calls = (src.match(/await _encodeSession\(/g) || []).length;
  assert.strictEqual(calls, 1, `expected exactly one call site, found ${calls}`);
}

export function test_encode_session_is_top_level_so_every_caller_can_reach_it() {
  // It originally lived inside the initCloudMode IIFE, which is why the
  // recent-list handler (declared outside that IIFE) could not have called it.
  assert.ok(/^async function _encodeSession\(/m.test(src),
    '_encodeSession must be declared at top level, not inside an IIFE');
}

export function test_local_mode_is_not_dragged_through_the_encoder() {
  const body = openFolderBody();
  assert.ok(/APP_MODE === 'cloud'/.test(body),
    'the encode must be gated on cloud mode');
  assert.ok(/startsWith\('sessions\/'\)/.test(body),
    'the encode must only run for an upload session key');
}

// ── Bug 2: never upload a previously generated reel ───────────────────────────
//
// The marker was only read server-side, after the bytes had already been sent.

export function test_folder_picker_consults_the_generated_reels_marker() {
  const start = src.indexOf("folderPicker.addEventListener('change'");
  assert.ok(start !== -1, 'folder picker handler not found');
  const handler = src.slice(start, start + 1200);
  assert.ok(handler.includes(GENERATED_REELS_MARKER),
    'the folder picker must exclude reels listed in the marker BEFORE uploading');
}

export function test_marker_parsing_matches_the_server() {
  // Python reads it as set(text.splitlines()); mirror that, tolerating CRLF and
  // stray whitespace so a hand-edited marker still filters.
  const parsed = parseGeneratedReels('Angle.mp4\r\nConvenience.mp4\n\n  Hook.mp4  \n');
  assert.deepStrictEqual([...parsed].sort(), ['Angle.mp4', 'Convenience.mp4', 'Hook.mp4']);
}

export function test_marker_parsing_handles_absent_or_empty_marker() {
  assert.strictEqual(parseGeneratedReels('').size, 0);
  assert.strictEqual(parseGeneratedReels(undefined).size, 0);
}

export function test_exclude_drops_listed_reels_and_keeps_sources() {
  const files = [
    { name: 'Angle.mp4' },
    { name: 'forven-interview-9ff33531.webm' },
    { name: 'forven-interview-9ff33531.txt' },
    { name: 'Convenience.mp4' },
  ];
  const kept = excludeGeneratedReels(files, 'Angle.mp4\nConvenience.mp4\n');
  assert.deepStrictEqual(kept.map(f => f.name),
    ['forven-interview-9ff33531.webm', 'forven-interview-9ff33531.txt']);
}

export function test_exclude_is_a_noop_without_a_marker() {
  const files = [{ name: 'Angle.mp4' }, { name: 'a.webm' }];
  assert.strictEqual(excludeGeneratedReels(files, '').length, 2);
}

export function test_exclude_matches_on_exact_filename_only() {
  // "Angle.mp4" must not knock out "Angle1.mp4" or "MyAngle.mp4".
  const files = [{ name: 'Angle1.mp4' }, { name: 'MyAngle.mp4' }, { name: 'Angle.mp4' }];
  const kept = excludeGeneratedReels(files, 'Angle.mp4');
  assert.deepStrictEqual(kept.map(f => f.name), ['Angle1.mp4', 'MyAngle.mp4']);
}
