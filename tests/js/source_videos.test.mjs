// The browser encoder records which interviews ended up in the cut, and sends
// them with the library entry. Without that, every reel made this way is
// undeliverable: Forven's reel-register call needs exactly the interviews whose
// footage is in the reel, and the app refuses to guess.
//
// generate() needs WebCodecs, so this asserts on the source the way
// upload_retry.test.mjs does, rather than running the encoder.
import { readFileSync } from 'node:fs';
import assert from 'node:assert';

const src = readFileSync('static/reel-encoder.js', 'utf8');

export async function test_the_library_entry_carries_the_source_videos() {
  // The bug this exists for: the server accepted source_videos and the browser
  // never sent it, so freshly generated reels could not be delivered.
  const body = src.slice(src.indexOf('/library'), src.indexOf('/library') + 600);
  assert.ok(body.includes('source_videos:'),
    'the POST to /library must include source_videos');
}

export async function test_only_clips_that_survived_are_counted() {
  // A segment whose range lies past the end of its source writes nothing. It
  // contributes no footage, so claiming its interview would wrongly restrict
  // who may see the reel.
  const survived = src.indexOf('segmentStarts.push(ts);');
  const skipped = src.indexOf('skipped —');
  assert.ok(survived > 0 && skipped > survived, 'expected both branches present');

  const record = src.indexOf('sourceVideos.add(');
  assert.ok(record > survived && record < skipped,
    'sourceVideos.add must sit in the branch where the clip was written, '
    + 'not alongside the skip');
}

export async function test_the_set_is_sorted_so_entries_are_stable() {
  assert.ok(/\[\.\.\.sourceVideos\]\.sort\(\)/.test(src),
    'source_videos should be sorted, matching the server-side path');
}
