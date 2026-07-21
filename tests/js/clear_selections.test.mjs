import { readFileSync } from 'node:fs';
import assert from 'node:assert';

const src = readFileSync('static/app.js', 'utf8');

// Pull just the _clearSelections body so these assertions can't be satisfied by
// some unrelated call elsewhere in the file.
function clearSelectionsBody() {
  const start = src.indexOf('function _clearSelections()');
  assert.ok(start !== -1, '_clearSelections not found in static/app.js');
  let depth = 0, i = src.indexOf('{', start);
  const open = i;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && --depth === 0) return src.slice(open, i + 1);
  }
  throw new Error('unbalanced braces in _clearSelections');
}

// _clearSelections empties state.checked / state.highlighted. Anything rendered
// FROM those Sets has to be redrawn in the same breath, or the UI disagrees
// with the state: after generating a reel the sidebar kept its stale
// "N checked" badges while the transcript rendered nothing selected.
export function test_clear_selections_refreshes_sidebar_badges() {
  assert.ok(/refreshBadge\(/.test(clearSelectionsBody()),
    '_clearSelections must refresh the sidebar badges it just invalidated');
}

// Same root cause: Generate is enabled from those Sets, so clearing them
// without re-evaluating left the button live with an empty selection.
export function test_clear_selections_updates_generate_button() {
  assert.ok(/updateGenerateBtn\(/.test(clearSelectionsBody()),
    '_clearSelections must re-evaluate the Generate button');
}

// The refresh belongs inside _clearSelections rather than in the New Reel
// handler, because both generation paths (cloud and server) call it
// independently — fixing only the button would leave one path broken.
export function test_both_generation_paths_go_through_clear_selections() {
  const calls = (src.match(/_clearSelections\(\)/g) || []).length;
  assert.ok(calls >= 3,
    `expected the definition plus both generation call sites, found ${calls}`);
}
