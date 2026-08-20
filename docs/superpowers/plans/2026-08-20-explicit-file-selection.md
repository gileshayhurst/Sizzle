# Explicit File Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit per-file include/exclude switch to the workspace sidebar, so a user decides which videos a sizzle reel draws on instead of that being an accident of what is in the folder.

**Architecture:** A client-side `state.included` Set of filenames, enforced at exactly three boundaries — the `/analyze` allow-list (so excluded files never reach Claude), the `state.poolOrdered` derivation (so the reel-length slider ranks and re-times over only the included files), and the generate payload (so excluded files ship no clips). `state.pool` continues to hold every candidate ever scored, so re-including a previously scored file restores its candidates for free.

**Tech Stack:** Flask (Python 3, pytest) for the backend; vanilla ES5-style JS in a single classic script (`static/app.js`) with a dependency-free node test runner (`tests/js/run.mjs`) for the frontend. No new dependencies.

---

## Design Reference

Read `docs/superpowers/specs/2026-08-20-explicit-file-selection-design.md` before starting. Key decisions this plan implements:

| Decision | Choice |
|---|---|
| Scope of exclusion | Everything — Analyze skips it, it leaves the priority order, it is dropped at generation |
| Re-include after Analyze | A notice telling the user to re-run Analyze; no automatic per-file Claude call |
| Excluded file's transcript | Readable, dimmed, read-only; existing selections preserved untouched |
| Persistence | `localStorage`, per folder, storing **exclusions** so new files default to on |
| Exclusions vs. generation | Exclusions **survive** generation; `_clearSelections()` must not touch them |

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `app.py` | `_run_analyze` gains an optional `files` allow-list; `/analyze` route passes it through | Modify |
| `tests/test_app.py` | Backend allow-list tests | Modify |
| `static/app.js` | `state.included`, persistence, `_reorderPool`, sidebar UI, read-only guards, payload filters | Modify |
| `templates/index.html` | Sidebar header gains a count and an All/None button | Modify |
| `static/style.css` | Excluded-row dimming, checkbox layout, read-only affordance | Modify |
| `tests/js/inclusion.test.mjs` | Static assertions that the three boundaries stay wired | Create |
| `CLAUDE.md` | Key Behaviours entry for the three boundaries | Modify |

## Task Order Rationale

Tasks 1–4 are **inert**: they add the machinery and the enforcement while `state.included` always contains every file, so behaviour is unchanged and every commit is safe to ship. Task 5 adds the sidebar switch that finally lets the set become non-full, landing into a codebase that already honours it. Do not reorder.

## Commands

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_app.py -v
```

```bash
node tests/js/run.mjs
```

---

## Task 1: Server-side `files` allow-list for `/analyze`

**Files:**
- Modify: `app.py:261` (`_run_analyze` signature and filter), `app.py:970-985` (route)
- Test: `tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:

```python
def test_analyze_files_allowlist_scores_only_listed_videos(client, tmp_path):
    """An excluded file must never reach Claude — that saving is the whole
    point of the allow-list, so assert on the call count, not just the output."""
    for name in ("a", "b"):
        (tmp_path / f"{name}.mp4").touch()
        (tmp_path / f"{name}.txt").write_text(
            f"[0:05] Speaker: This is {name}.", encoding="utf-8"
        )

    with patch("app.query_claude", return_value="0:05-0:10|9") as qc:
        resp = client.post(
            "/analyze",
            json={"folder": str(tmp_path), "prompt": "food", "files": ["a.mp4"]},
        )

    assert resp.status_code == 200
    assert qc.call_count == 1
    segments = resp.get_json()["segments"]
    assert "a.mp4" in segments
    assert "b.mp4" not in segments


def test_analyze_without_files_key_scores_every_video(client, tmp_path):
    """Regression guard: `files` is optional and every existing caller omits it."""
    for name in ("a", "b"):
        (tmp_path / f"{name}.mp4").touch()
        (tmp_path / f"{name}.txt").write_text(
            f"[0:05] Speaker: This is {name}.", encoding="utf-8"
        )

    with patch("app.query_claude", return_value="0:05-0:10|9") as qc:
        resp = client.post("/analyze", json={"folder": str(tmp_path), "prompt": "food"})

    assert resp.status_code == 200
    assert qc.call_count == 2
    assert set(resp.get_json()["segments"]) == {"a.mp4", "b.mp4"}


def test_analyze_files_allowlist_cannot_defeat_generated_reel_filter(client, tmp_path):
    """The allow-list is applied AFTER _filter_generated_reels, so naming a
    generated reel in it must not get the reel analyzed."""
    (tmp_path / "source.mp4").touch()
    (tmp_path / "source.txt").write_text("[0:05] Speaker: Source.", encoding="utf-8")
    reel = tmp_path / "NOBU_sizzle.mp4"
    reel.touch()
    (tmp_path / "NOBU_sizzle.txt").write_text("[0:05] Speaker: Reel.", encoding="utf-8")

    library = [{
        "id": "abc", "filename": "NOBU_sizzle.mp4", "path": str(reel),
        "source_folder": "tmp/", "prompt": "", "duration_seconds": 10,
        "clip_count": 1, "created_at": "2026-01-01T00:00:00",
    }]

    with patch("storage.load_library", return_value=library):
        with patch("app.query_claude", return_value="0:05-0:10|9") as qc:
            resp = client.post(
                "/analyze",
                json={
                    "folder": str(tmp_path),
                    "prompt": "food",
                    "files": ["NOBU_sizzle.mp4", "source.mp4"],
                },
            )

    assert resp.status_code == 200
    assert qc.call_count == 1
    assert "NOBU_sizzle.mp4" not in resp.get_json()["segments"]


def test_analyze_files_allowlist_ignores_unknown_names(client, tmp_path):
    """A stale client naming a deleted video must not fail the whole run."""
    (tmp_path / "a.mp4").touch()
    (tmp_path / "a.txt").write_text("[0:05] Speaker: This is a.", encoding="utf-8")

    with patch("app.query_claude", return_value="0:05-0:10|9"):
        resp = client.post(
            "/analyze",
            json={"folder": str(tmp_path), "prompt": "food",
                  "files": ["a.mp4", "ghost.mp4"]},
        )

    assert resp.status_code == 200
    assert set(resp.get_json()["segments"]) == {"a.mp4"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
.\venv\Scripts\python.exe -m pytest tests/test_app.py -k allowlist -v
```

Expected: FAIL. `test_analyze_files_allowlist_scores_only_listed_videos` fails on `assert qc.call_count == 1` (actual `2`) because `files` is currently ignored.

- [ ] **Step 3: Add the allow-list to `_run_analyze`**

In `app.py`, change the signature at line 261 and the filter at line 268.

From:

```python
def _run_analyze(folder: str, prompt: str) -> dict:
    """Call Claude on every transcript in folder. Returns per-video scored
    segments plus a legacy `highlights` union of the matched lines."""
    try:
        video_paths = scan_videos(folder)
    except Exception as exc:
        return {"error": str(exc)}
    video_paths = _filter_generated_reels(video_paths)
```

To:

```python
def _run_analyze(folder: str, prompt: str, files: list[str] | None = None) -> dict:
    """Call Claude on every transcript in folder. Returns per-video scored
    segments plus a legacy `highlights` union of the matched lines.

    `files` is an optional allow-list of filenames. When given, only those
    videos are analyzed — an excluded file never reaches query_claude, which is
    the cost saving the feature exists for. When None, every video is analyzed,
    so every existing caller is unaffected.

    Applied AFTER _filter_generated_reels so the generated-reel guard stays
    authoritative: naming a reel here cannot get it analyzed. The allow-list is
    only ever a filter over scanned paths, never a source of paths, so it cannot
    be used to reach a file scan_videos did not return.
    """
    try:
        video_paths = scan_videos(folder)
    except Exception as exc:
        return {"error": str(exc)}
    video_paths = _filter_generated_reels(video_paths)
    if files is not None:
        allowed = set(files)
        video_paths = [p for p in video_paths if p.name in allowed]
```

- [ ] **Step 4: Pass `files` through from the route**

In `app.py`, in the `analyze()` route (around line 970), change:

```python
        result = _run_analyze(folder, prompt)
```

To:

```python
        files = body.get("files")
        if files is not None and not isinstance(files, list):
            return jsonify({"error": "files must be a list"}), 400
        result = _run_analyze(folder, prompt, files)
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run:

```bash
.\venv\Scripts\python.exe -m pytest tests/test_app.py -k allowlist -v
```

Expected: 4 passed.

- [ ] **Step 6: Run the whole backend suite to check nothing regressed**

Run:

```bash
.\venv\Scripts\python.exe -m pytest tests/test_app.py -v
```

Expected: all pass, including the pre-existing `test_analyze_*` tests, which omit `files` and so must be untouched.

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat(analyze): accept an optional files allow-list"
```

---

## Task 2: `state.included`, persistence, and `_reorderPool`

Adds the state and the single filter point. `state.included` is populated with every file on load, so behaviour is unchanged.

**Files:**
- Modify: `static/app.js:5-23` (state), `static/app.js:374-403` (persistence helpers), `static/app.js:864-889` (folder load)

- [ ] **Step 1: Add the state field**

In `static/app.js`, in the `state` object, after the `highlighted` line:

```js
  highlighted: {},    // {filename: Set<raw_line_string>}
  included: new Set(),// filenames included in the reel; every file by default
```

- [ ] **Step 2: Add the persistence helpers**

In `static/app.js`, immediately after the `_savePool` function (which ends around line 383), add:

```js
// Inclusion is persisted as the set of EXCLUDED names, not included ones, so a
// file added to the folder after this was written defaults to on. A new file
// appearing silently switched off would be invisible to the user.
function _saveIncluded() {
  if (!state.folder) return;
  try {
    const excluded = state.files
      .map(f => f.name)
      .filter(n => !state.included.has(n));
    localStorage.setItem('sizzle_excl_v1_' + state.folder, JSON.stringify(excluded));
  } catch (_) {}
}

// Rebuild state.included from state.files minus the persisted exclusions.
// Persisted names that no longer exist in the folder simply have no effect.
function _restoreIncluded() {
  state.included = new Set(state.files.map(f => f.name));
  if (!state.folder) return;
  try {
    const raw = localStorage.getItem('sizzle_excl_v1_' + state.folder);
    if (!raw) return;
    for (const name of JSON.parse(raw) || []) state.included.delete(name);
  } catch (_) {
    // Malformed or unavailable localStorage — every file stays included
  }
}
```

- [ ] **Step 3: Add `_reorderPool` as the single filter point**

In `static/app.js`, immediately after `_restoreIncluded`, add:

```js
// The ONE place the inclusion filter is applied to the candidate pool.
//
// state.pool keeps every candidate Claude has ever scored, including ones from
// currently-excluded files; only this derived ordering filters. That is what
// makes the length slider cohere for free — its range, optimal marker, selected
// prefix and "N of M segments" label are all pure functions of poolOrdered —
// and it means re-including a file that WAS scored restores its candidates at
// no cost, so the re-analyze notice only fires for files that truly have none.
function _reorderPool() {
  const includedOrder = state.files
    .map(f => f.name)
    .filter(n => state.included.has(n));
  state.poolOrdered = sortByPriority(
    state.pool.filter(c => state.included.has(c.file)),
    includedOrder
  );
}
```

- [ ] **Step 4: Route `_restorePool` through `_reorderPool`**

In `static/app.js`, in `_restorePool` (line 384), replace:

```js
    state.pool = (saved.pool || []).filter(c => fileNames.has(c.file));
    state.poolOrdered = sortByPriority(state.pool, state.files.map(f => f.name));
```

With:

```js
    state.pool = (saved.pool || []).filter(c => fileNames.has(c.file));
    _reorderPool();
```

- [ ] **Step 5: Restore inclusion on folder load**

In `static/app.js`, in the folder-load path, add the `_restoreIncluded()` call so it runs before `_restorePool()`. Replace:

```js
  _restorePool();
}
```

With:

```js
  // Must run before _restorePool — _reorderPool reads state.included.
  _restoreIncluded();
  _restorePool();
}
```

- [ ] **Step 6: Route both analyze paths through `_reorderPool`**

In `runAnalyze`, replace:

```js
    state.pool = buildCandidatePool(data.segments || {}, state.files.map(f => f.name));
    state.poolOrdered = sortByPriority(state.pool, state.files.map(f => f.name));
```

With:

```js
    state.pool = buildCandidatePool(data.segments || {}, state.files.map(f => f.name));
    _reorderPool();
```

In `runAddAnalyze`, replace:

```js
    state.pool = mergeIntoPool(state.pool, data.segments || {}, fileOrder);
    state.poolOrdered = sortByPriority(state.pool, fileOrder);
```

With:

```js
    state.pool = mergeIntoPool(state.pool, data.segments || {}, fileOrder);
    _reorderPool();
```

- [ ] **Step 7: Verify nothing changed**

Run:

```bash
node tests/js/run.mjs
```

Expected: 27 passed, 0 failed — the same as before this task. `state.included` holds every file, so `_reorderPool` is currently a no-op rewrite of the previous sort.

- [ ] **Step 8: Commit**

```bash
git add static/app.js
git commit -m "feat(selection): add state.included and route the pool through _reorderPool"
```

---

## Task 3: Enforce inclusion at the analyze and generate boundaries

Still inert — the set is full — but the enforcement lands before the UI that can empty it.

**Files:**
- Modify: `static/app.js` (`_postAnalyze`, `_clearSelections`, `updateGenerateBtn`, the `#btn-generate` handler)
- Test: `tests/js/inclusion.test.mjs` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/js/inclusion.test.mjs`:

```js
import { readFileSync } from 'node:fs';
import assert from 'node:assert';

const src = readFileSync('static/app.js', 'utf8');

// Slice out a single function body by brace-matching, so an assertion can't be
// satisfied by an unrelated call elsewhere in the file. Same approach as
// tests/js/clear_selections.test.mjs.
function bodyOf(signature) {
  const start = src.indexOf(signature);
  assert.ok(start !== -1, `${signature} not found in static/app.js`);
  let depth = 0, i = src.indexOf('{', start);
  const open = i;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && --depth === 0) return src.slice(open, i + 1);
  }
  throw new Error(`unbalanced braces in ${signature}`);
}

// Boundary 1 of 3. An excluded file must never reach Claude — that saving is
// the reason the allow-list exists on the server.
export function test_post_analyze_sends_the_files_allowlist() {
  assert.ok(/files\s*:/.test(bodyOf('async function _postAnalyze(')),
    '_postAnalyze must send a `files` allow-list so excluded files are not scored');
}

// Boundary 2 of 3. Exactly one place may filter the pool by inclusion, or the
// filter drifts out of sync between call sites.
export function test_reorder_pool_is_the_only_pool_filter() {
  assert.ok(/state\.included/.test(bodyOf('function _reorderPool()')),
    '_reorderPool must filter the pool by state.included');
  const sorts = (src.match(/sortByPriority\(/g) || []).length;
  assert.strictEqual(sorts, 2,
    `sortByPriority should appear twice (its definition and _reorderPool), found ${sorts}` +
    ' — a third call site means the inclusion filter has been bypassed');
}

// Boundary 3 of 3. Excluded files must ship no clips.
export function test_generate_handler_filters_by_inclusion() {
  const start = src.indexOf("$('btn-generate').addEventListener('click'");
  assert.ok(start !== -1, 'btn-generate click handler not found');
  const handler = src.slice(start, start + 600);
  assert.ok(/state\.included/.test(handler),
    'the generate handler must skip files that are not included');
}

export function test_generate_button_state_respects_inclusion() {
  assert.ok(/state\.included/.test(bodyOf('function updateGenerateBtn()')),
    'updateGenerateBtn must ignore selections in excluded files');
}

// Which files a folder is about is a property of the FOLDER, not of one reel.
// _clearSelections wipes selections and the pool after a successful generation;
// wiping exclusions too would make the user re-exclude the same files before
// every reel, which is the tedium this feature exists to remove.
export function test_clear_selections_does_not_wipe_exclusions() {
  assert.ok(!/sizzle_excl/.test(bodyOf('function _clearSelections()')),
    'exclusions must survive generation — _clearSelections must not touch them');
}

export function test_exclusion_key_is_versioned() {
  assert.ok(/sizzle_excl_v\d+_/.test(src),
    'the exclusion key must carry a version, like the selection and pool keys');
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
node tests/js/run.mjs
```

Expected: `30 passed, 3 failed`. The three failures are `test_post_analyze_sends_the_files_allowlist`, `test_generate_handler_filters_by_inclusion` and `test_generate_button_state_respects_inclusion`.

The other three new tests pass already: `test_reorder_pool_is_the_only_pool_filter` and `test_exclusion_key_is_versioned` were satisfied by Task 2, and `test_clear_selections_does_not_wipe_exclusions` passes because `_clearSelections` has never mentioned the key. That last one is a *regression guard*, not a red-to-green test — its job is to fail if someone later "tidies up" by adding the exclusion key alongside the other two.

- [ ] **Step 3: Send the allow-list from `_postAnalyze`**

In `static/app.js`, in `_postAnalyze`, replace:

```js
    body: JSON.stringify({ folder: state.folder, prompt }),
```

With:

```js
    body: JSON.stringify({ folder: state.folder, prompt, files: [...state.included] }),
```

Both `runAnalyze` and `runAddAnalyze` call `_postAnalyze`, so both inherit this.

- [ ] **Step 4: Filter the generate payload**

In `static/app.js`, in the `#btn-generate` click handler, replace:

```js
  state.files.forEach(f => {
    const lines = mode === 'checkbox'
      ? [...(state.checked[f.name] || [])]
      : [...(state.highlighted[f.name] || [])];
    if (lines.length > 0) selections[f.name] = lines;
  });
```

With:

```js
  state.files.forEach(f => {
    if (!state.included.has(f.name)) return;
    const lines = mode === 'checkbox'
      ? [...(state.checked[f.name] || [])]
      : [...(state.highlighted[f.name] || [])];
    if (lines.length > 0) selections[f.name] = lines;
  });
```

An excluded file's selections stay in `state.checked` untouched — they are simply not shipped, and return intact when the file is switched back on. The generator needs no change: it already handles a `selections` dict that omits some of the folder's videos.

- [ ] **Step 5: Gate the Generate and Analyze buttons**

In `static/app.js`, replace `updateGenerateBtn`:

```js
function updateGenerateBtn() {
  const hasAny = state.files.some(f => {
    const s = state.mode === 'checkbox'
      ? state.checked[f.name]
      : state.highlighted[f.name];
    return s && s.size > 0;
  });
  $('btn-generate').disabled = !hasAny;
}
```

With:

```js
function updateGenerateBtn() {
  const hasAny = state.files.some(f => {
    if (!state.included.has(f.name)) return false;
    const s = state.mode === 'checkbox'
      ? state.checked[f.name]
      : state.highlighted[f.name];
    return s && s.size > 0;
  });
  $('btn-generate').disabled = !hasAny;
}

// Analyzing with every file switched off would spend a request to score
// nothing, so the button goes dead rather than failing after the round-trip.
function updateAnalyzeBtn() {
  const btn = $('btn-analyze');
  if (btn) btn.disabled = state.included.size === 0;
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run:

```bash
node tests/js/run.mjs
```

Expected: 33 passed, 0 failed.

- [ ] **Step 7: Commit**

```bash
git add static/app.js tests/js/inclusion.test.mjs
git commit -m "feat(selection): enforce inclusion at the analyze and generate boundaries"
```

---

## Task 4: Read-only transcript for an excluded file

**Files:**
- Modify: `static/app.js` (`renderTranscript`, `renderCheckboxMode`, `renderHighlightMode`, the four bulk helpers, `updateSelectAllBtn`, `updateClearAllBtn`)
- Modify: `static/style.css`

> **Do not attempt a CSS-only gate.** Every selectable element carries a `keydown` handler for Enter/Space (`static/app.js:1308`, `1367`, `1461`) and `tabindex="0"`. `pointer-events: none` does not affect the keyboard, so a CSS-only version would let keyboard users check lines in a file that is switched off.

- [ ] **Step 1: Add the editability helper and the container class**

In `static/app.js`, replace `renderTranscript`:

```js
function renderTranscript(filename) {
  const fileObj = state.files.find(f => f.name === filename);
  if (state.mode === 'checkbox') renderCheckboxMode(fileObj);
  else renderHighlightMode(fileObj);
}
```

With:

```js
// A file that is switched off is readable but not editable. Enforced in JS
// (below) because the lines carry keydown handlers that CSS cannot reach.
function _isEditable(filename) { return state.included.has(filename); }

function renderTranscript(filename) {
  const fileObj = state.files.find(f => f.name === filename);
  $('transcript-scroll').classList.toggle('readonly', !_isEditable(filename));
  if (state.mode === 'checkbox') renderCheckboxMode(fileObj);
  else renderHighlightMode(fileObj);
}
```

- [ ] **Step 2: Guard checkbox mode**

In `static/app.js`, in `renderCheckboxMode`, add the flag directly after the empty-transcript guard:

```js
  const editable = _isEditable(fileObj.name);
```

Add as the first line of the `toggleGroup` function body:

```js
      if (!editable) return;
```

Add as the first line of the `toggleLine` function body:

```js
        if (!editable) return;
```

Then make the minute header and the lines unreachable by keyboard when not editable. At `static/app.js:1271`, replace the minute header's tabindex line:

```js
    labelEl.setAttribute('tabindex', '0');
```

With:

```js
    labelEl.setAttribute('tabindex', editable ? '0' : '-1');
    if (!editable) labelEl.setAttribute('aria-disabled', 'true');
```

> **The next replacement is not unique in the file.** `lineEl.setAttribute('tabindex', '0');` appears twice — at `static/app.js:1320` inside `renderCheckboxMode` (six-space indent) and at `static/app.js:1434` inside `renderHighlightMode` (four-space indent). This step edits **only the one at line 1320**, inside `renderCheckboxMode`. Line 1434 is handled in Step 3. Do not use a global find-and-replace.

At `static/app.js:1320`, inside `renderCheckboxMode`, replace:

```js
      lineEl.setAttribute('tabindex', '0');
```

With:

```js
      lineEl.setAttribute('tabindex', editable ? '0' : '-1');
      if (!editable) lineEl.setAttribute('aria-disabled', 'true');
```

- [ ] **Step 3: Guard highlight mode**

In `static/app.js`, in `renderHighlightMode`, add after the empty-transcript guard:

```js
  const editable = _isEditable(fileObj.name);
```

At `static/app.js:1434` — the *second* occurrence, inside `renderHighlightMode`, four-space indent; the one at line 1320 was already handled in Step 2 — replace:

```js
    lineEl.setAttribute('tabindex', '0');
```

With:

```js
    lineEl.setAttribute('tabindex', editable ? '0' : '-1');
    if (!editable) lineEl.setAttribute('aria-disabled', 'true');
```

Add as the first line inside the line `keydown` handler, before the `if (e.key === ...)` check:

```js
      if (!editable) return;
```

Add as the first line inside the `mousedown` drag-to-brush handler, before `const lineEl = ...`:

```js
    if (!editable) return;
```

- [ ] **Step 4: Guard the four bulk helpers**

In `static/app.js`, add as the first line of each of `checkAllInFile`, `uncheckAllInFile`, `highlightAllInFile` and `clearHighlightsInFile`:

```js
  if (!_isEditable(filename)) return;
```

These are already unreachable via the disabled header buttons (next step); the guard is defence in depth so a future caller cannot bypass the rule.

- [ ] **Step 5: Disable the header buttons**

In `static/app.js`, add as the second line of `updateSelectAllBtn` (after `const btn = $('btn-select-all');`):

```js
  btn.disabled = !_isEditable(state.activeFile);
```

And in `updateClearAllBtn`, after the existing `if (!btn) return;`:

```js
  btn.disabled = !_isEditable(state.activeFile);
```

- [ ] **Step 6: Add the read-only affordance CSS**

In `static/style.css`, after the `.minute-label-cb:hover` rule (around line 433), add:

```css
/* An excluded file's transcript stays readable and scrollable, but reads as
   inert. The actual gate is in JS — these lines carry keydown handlers that
   pointer-events cannot reach — so this is affordance only. */
.transcript-scroll.readonly .transcript-line-cb,
.transcript-scroll.readonly .transcript-line-hl,
.transcript-scroll.readonly .minute-label-cb { cursor: default; }
.transcript-scroll.readonly .transcript-line-cb:hover,
.transcript-scroll.readonly .transcript-line-hl:hover,
.transcript-scroll.readonly .minute-label-cb:hover { background: transparent; }
```

- [ ] **Step 7: Verify the JS suite still passes**

Run:

```bash
node tests/js/run.mjs
```

Expected: 33 passed, 0 failed.

- [ ] **Step 8: Commit**

```bash
git add static/app.js static/style.css
git commit -m "feat(selection): make an excluded file's transcript read-only"
```

---

## Task 5: Sidebar include switch

The task that makes the feature reachable.

**Files:**
- Modify: `templates/index.html:~137` (sidebar header)
- Modify: `static/app.js` (`renderSidebar`, `updateBadgeEl`, new `setFileIncluded` / `_afterInclusionChange` / `updateSidebarHeader` / `_maybeNudgeReanalyze`)
- Modify: `static/style.css`

- [ ] **Step 1: Update the sidebar header markup**

In `templates/index.html`, replace:

```html
        <div class="sidebar-header">Video Files</div>
```

With:

```html
        <div class="sidebar-header">
          <span>Video Files</span>
          <span id="sidebar-count" class="sidebar-count"></span>
          <button id="btn-include-all" type="button" class="sidebar-all-btn"></button>
        </div>
```

- [ ] **Step 2: Add the inclusion-change handlers**

In `static/app.js`, immediately before `function renderSidebar()`, add:

```js
// ─── Inclusion ────────────────────────────────────────────────────────────────
// Everything that has to happen after state.included changes, in one place so
// the per-file checkbox and the All/None button cannot drift apart.
function _afterInclusionChange() {
  _saveIncluded();
  _reorderPool();
  if (state.poolOrdered.length >= 2) {
    // Chrome only, plus "custom": re-applying the slider's priority prefix here
    // would silently discard manual line edits, and the current selection is
    // generally no longer a clean prefix of the new ordering anyway. This
    // matches how runAddAnalyze handles a pool change. Dragging the slider
    // resets to a clean prefix of the new set.
    _refreshSliderChromeOnly($('reel-slider').value);
    markSliderCustom();
  } else {
    $('reel-length-row')?.classList.add('hidden');
  }
  renderSidebar();
  if (state.activeFile) renderTranscript(state.activeFile);
  updateSelectAllBtn();
  updateClearAllBtn();
  updateGenerateBtn();
  updateAnalyzeBtn();
  _maybeNudgeReanalyze();
}

function setFileIncluded(filename, included) {
  if (included) state.included.add(filename);
  else state.included.delete(filename);
  _afterInclusionChange();
}

// A file switched on after an Analyze run has no candidates unless it was
// scored earlier, so tell the user rather than silently shipping a reel that
// is missing that file's best moments. Reuses the existing neutral strip; the
// `nudge` marker lets this clear its own message without clobbering a real
// analyze error.
function _maybeNudgeReanalyze() {
  const el = $('analyze-error');
  if (!el) return;
  const clear = () => {
    if (el.classList.contains('nudge')) {
      el.classList.add('hidden');
      el.classList.remove('nudge');
    }
  };
  if (state.pool.length === 0) return clear();
  const scored = new Set(state.pool.map(c => c.file));
  const missing = [...state.included].filter(n => !scored.has(n));
  if (missing.length === 0) return clear();
  const n = missing.length;
  _showAnalyzeMsg(
    n === 1
      ? '1 file has no results yet — re-run Analyze to include it.'
      : `${n} files have no results yet — re-run Analyze to include them.`,
    true
  );
  el.classList.add('nudge');
}

function updateSidebarHeader() {
  const total = state.files.length;
  const included = state.files.filter(f => state.included.has(f.name)).length;
  const countEl = $('sidebar-count');
  if (countEl) countEl.textContent = total ? `${included} of ${total}` : '';
  const btn = $('btn-include-all');
  if (!btn) return;
  const allOn = total > 0 && included === total;
  btn.textContent = allOn ? 'None' : 'All';
  btn.setAttribute('aria-label', allOn ? 'Exclude every file' : 'Include every file');
  btn.onclick = () => {
    state.files.forEach(f => {
      if (allOn) state.included.delete(f.name);
      else state.included.add(f.name);
    });
    _afterInclusionChange();
  };
}
```

- [ ] **Step 3: Add the checkbox to each sidebar row**

In `static/app.js`, replace `renderSidebar`:

```js
function renderSidebar() {
  const list = $('sidebar-list');
  list.innerHTML = '';
  state.files.forEach(f => {
    const li = document.createElement('li');
    li.className = 'sidebar-item' + (f.name === state.activeFile ? ' active' : '');
    li.dataset.name = f.name;

    const nameDiv = document.createElement('div');
    nameDiv.className = 'item-name';
    nameDiv.textContent = f.name;

    const badgeDiv = document.createElement('div');
    badgeDiv.className = 'item-badge';
    badgeDiv.id = `badge-${CSS.escape(f.name)}`;
    updateBadgeEl(badgeDiv, f.name);

    li.appendChild(nameDiv);
    li.appendChild(badgeDiv);
    li.addEventListener('click', () => selectFile(f.name));
    list.appendChild(li);
  });
}
```

With:

```js
function renderSidebar() {
  const list = $('sidebar-list');
  list.innerHTML = '';
  state.files.forEach(f => {
    const included = state.included.has(f.name);
    const li = document.createElement('li');
    li.className = 'sidebar-item'
      + (f.name === state.activeFile ? ' active' : '')
      + (included ? '' : ' excluded');
    li.dataset.name = f.name;

    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'item-include';
    cb.checked = included;
    cb.setAttribute('aria-label', `Include ${f.name} in the reel`);
    // The row itself switches the transcript pane; the checkbox must not.
    cb.addEventListener('click', e => e.stopPropagation());
    cb.addEventListener('change', () => setFileIncluded(f.name, cb.checked));

    const main = document.createElement('div');
    main.className = 'item-main';

    const nameDiv = document.createElement('div');
    nameDiv.className = 'item-name';
    nameDiv.textContent = f.name;

    const badgeDiv = document.createElement('div');
    badgeDiv.className = 'item-badge';
    badgeDiv.id = `badge-${CSS.escape(f.name)}`;
    updateBadgeEl(badgeDiv, f.name);

    main.appendChild(nameDiv);
    main.appendChild(badgeDiv);
    li.appendChild(cb);
    li.appendChild(main);
    li.addEventListener('click', () => selectFile(f.name));
    list.appendChild(li);
  });
  updateSidebarHeader();
}
```

- [ ] **Step 4: Show "excluded" in the badge**

In `static/app.js`, replace `updateBadgeEl`:

```js
function updateBadgeEl(el, filename) {
  const cb = state.checked[filename]?.size || 0;
  const hl = state.highlighted[filename]?.size || 0;
  if (state.mode === 'checkbox') {
```

With:

```js
function updateBadgeEl(el, filename) {
  // Counts are not lost while a file is off — just not shown. They return
  // intact when it is switched back on.
  if (!state.included.has(filename)) { el.textContent = 'excluded'; return; }
  const cb = state.checked[filename]?.size || 0;
  const hl = state.highlighted[filename]?.size || 0;
  if (state.mode === 'checkbox') {
```

- [ ] **Step 5: Initialise the button state on workspace open**

In `static/app.js`, in `showWorkspace`, replace:

```js
  updateGenerateBtn();
}
```

With:

```js
  updateGenerateBtn();
  updateAnalyzeBtn();
}
```

- [ ] **Step 6: Add the sidebar CSS**

In `static/style.css`, replace:

```css
.sidebar-header {
  padding: 12px 14px 10px; font-size: 0.6875rem; color: var(--muted); text-transform: uppercase;
  letter-spacing: 0.08em; font-weight: 600; border-bottom: 1px solid var(--border); flex-shrink: 0;
}
```

With:

```css
.sidebar-header {
  padding: 12px 14px 10px; font-size: 0.6875rem; color: var(--muted); text-transform: uppercase;
  letter-spacing: 0.08em; font-weight: 600; border-bottom: 1px solid var(--border); flex-shrink: 0;
  display: flex; align-items: center; gap: 6px;
}
.sidebar-count { color: var(--muted); font-weight: 500; letter-spacing: 0.04em; }
.sidebar-all-btn {
  margin-left: auto; background: none; border: none; padding: 2px 4px; cursor: pointer;
  font: inherit; letter-spacing: inherit; text-transform: inherit;
  color: var(--amber-ink); border-radius: var(--radius-sm);
}
.sidebar-all-btn:hover { background: var(--amber-tint); }
```

Then replace:

```css
.sidebar-item {
  padding: 9px 11px; cursor: pointer; border-radius: var(--radius-sm);
  font-size: 12px; color: var(--body); display: flex; flex-direction: column; gap: 3px;
  transition: background var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}
```

With:

```css
.sidebar-item {
  padding: 9px 11px; cursor: pointer; border-radius: var(--radius-sm);
  font-size: 12px; color: var(--body); display: flex; flex-direction: row;
  align-items: center; gap: 9px;
  transition: background var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}
/* min-width:0 keeps the .item-name ellipsis working inside a flex child */
.sidebar-item .item-main { display: flex; flex-direction: column; gap: 3px; min-width: 0; flex: 1; }
.sidebar-item .item-include { accent-color: var(--amber); flex: none; cursor: pointer; margin: 0; }
/* Dimming uses the --muted token rather than opacity, so the excluded row keeps
   the AA contrast ratio DESIGN.md has already validated for this background. */
.sidebar-item.excluded .item-name,
.sidebar-item.excluded .item-badge { color: var(--muted); }
.sidebar-item.excluded .item-name { font-weight: 500; }
```

- [ ] **Step 7: Keep the mobile row layout**

In `static/style.css`, in the mobile block that already restyles `.sidebar-item` (around line 1109), add inside the same media query:

```css
  .sidebar-item .item-main { flex-direction: row; align-items: center; gap: 8px; }
```

Note: `.sidebar-header` is `display: none` on mobile, so the count and All/None button are desktop-only. The per-file checkboxes still work there.

- [ ] **Step 8: Verify the JS suite**

Run:

```bash
node tests/js/run.mjs
```

Expected: 33 passed, 0 failed.

- [ ] **Step 9: Verify in the running app**

Start both services:

```powershell
.\venv\Scripts\python.exe -c "from app import create_app; create_app().run(debug=True)"
```

```powershell
.\venv\Scripts\python.exe -c "from generator_app import create_app; create_app().run(debug=True, port=5001)"
```

Open a folder with at least three videos, then check each of:

1. Every file starts checked; the header reads `3 of 3` and the button reads "None".
2. Uncheck one file — its row dims, its badge reads "excluded", the header reads `2 of 3`, the button reads "All".
3. Click the excluded row — its transcript still opens and scrolls, but clicking a line does nothing, tabbing skips its lines, and check-all/clear-all are disabled.
4. Run Analyze — the server log shows two Claude calls, not three.
5. The reel-length slider's max is lower than it would be with all three.
6. Re-check the excluded file — the notice "1 file has no results yet — re-run Analyze to include it." appears under the analyze bar.
7. Uncheck it again — that notice disappears.
8. Re-run Analyze — the notice clears and the slider's max widens.
9. Reload the page — the exclusions are still there.
10. Generate a reel and confirm no clip comes from an excluded file, and that the exclusions survive into the next reel.

- [ ] **Step 10: Commit**

```bash
git add templates/index.html static/app.js static/style.css
git commit -m "feat(sidebar): explicit per-file include switch"
```

---

## Task 6: Document the invariant

**Files:**
- Modify: `CLAUDE.md` (Key Behaviours section)

- [ ] **Step 1: Add the Key Behaviours entry**

In `CLAUDE.md`, in the `## Key Behaviours` list, add after the `_filter_generated_reels` bullet:

```markdown
- **File inclusion is enforced at exactly three boundaries.** `state.included`
  (a Set of filenames in `static/app.js`, persisted per folder as the
  *exclusion* list under `sizzle_excl_v1_<folder>`) is honoured by: the `files`
  allow-list sent to `/analyze` (so an excluded file never reaches
  `query_claude`), `_reorderPool` (the only place the candidate pool is filtered
  — the reel-length slider is a pure function of `state.poolOrdered`, so this
  one filter makes its range, optimal marker and label cohere), and the
  `#btn-generate` payload. A new code path that consumes `state.files` must
  filter by `state.included` or say why it does not. `state.pool` deliberately
  keeps candidates from excluded files, so re-including a scored file restores
  them without another Claude call.
- **Exclusions survive generation.** `_clearSelections()` wipes
  `sizzle_sel_v3_` and `sizzle_pool_v3_` so the next reel starts empty; it must
  **not** touch `sizzle_excl_v1_`. Which files a folder is about is a property
  of the folder, not of one reel — resetting it would force the user to
  re-exclude the same files before every reel. Guarded by
  `tests/js/inclusion.test.mjs`.
```

- [ ] **Step 2: Add the JS test command**

In `CLAUDE.md`, in the `## Commands` PowerShell block, after the pytest lines, add:

```powershell
# Run the frontend test suite (dependency-free node runner)
node tests/js/run.mjs
```

- [ ] **Step 3: Run the full suite one last time**

```bash
.\venv\Scripts\python.exe -m pytest tests/ -v
```

```bash
node tests/js/run.mjs
```

Expected: both green.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: record the three inclusion boundaries"
```
