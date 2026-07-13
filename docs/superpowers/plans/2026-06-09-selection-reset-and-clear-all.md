# Selection Reset & Clear-All Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clear persisted transcript selections when a reel is successfully generated, and add a per-file "uncheck all" / "clear all" button to the left of the existing "check all" button.

**Architecture:** Both features are frontend-only changes across three files: `static/app.js` (logic), `templates/index.html` (markup), `static/style.css` (styling). No backend changes. No new dependencies.

**Tech Stack:** Vanilla JS, HTML, CSS. Python/pytest for the existing test suite (no new Python tests needed — these are pure frontend changes with no testable Python surface).

---

## Codebase orientation

Read these before touching anything:

- **`static/app.js`** — single-file SPA. Relevant sections:
  - `_saveSelections()` at line ~21 — writes `state.checked` / `state.highlighted` to `localStorage` under key `sizzle_sel_<folder>`.
  - `updateSelectAllBtn()` at line ~327 — sets label/handler on `#btn-select-all` based on `state.mode`.
  - `checkAllInFile(filename)` at line ~455 — adds all lines to `state.checked[filename]`, re-renders, saves.
  - `highlightAllInFile(filename)` at line ~572 — same for highlight mode.
  - `watchGeneration(jobId)` at line ~676 — receives WebSocket messages; `msg.status === 'done'` branch calls `showResult(msg.result)`.
  - `state.folder`, `state.files`, `state.checked`, `state.highlighted` — the key state fields.

- **`templates/index.html`** line ~113:
  ```html
  <div id="transcript-header">
    <span id="transcript-filename" class="transcript-filename"></span>
    <button id="btn-select-all" class="select-all-btn"></button>
  </div>
  ```

- **`static/style.css`** lines ~198-202 — `.select-all-btn` and mode variants.

- **`tests/test_app.py`** — existing Python tests. Run with `.\venv\Scripts\python.exe -m pytest tests/ -v` on Windows / `./venv/Scripts/python.exe -m pytest tests/ -v` on Linux. All 164 tests must still pass after your changes.

---

## Task 1: Add `_clearSelections()` and call it on successful generation

**Files:**
- Modify: `static/app.js`

### What to do

Add a `_clearSelections()` function directly below `_saveSelections()` (around line 40). Then call it in `watchGeneration` when `msg.status === 'done'`.

- [ ] **Step 1: Add `_clearSelections()` in `static/app.js`**

  Insert this block immediately after the closing `}` of `_saveSelections()` (after line 39):

  ```js
  function _clearSelections() {
    // Remove the persisted payload so a page reload starts empty.
    if (state.folder) {
      try { localStorage.removeItem('sizzle_sel_' + state.folder); } catch (_) {}
    }
    // Reset in-memory Sets so the workspace renders with nothing selected
    // if the user navigates back without reloading.
    for (const filename of Object.keys(state.checked))     state.checked[filename]     = new Set();
    for (const filename of Object.keys(state.highlighted)) state.highlighted[filename] = new Set();
  }
  ```

- [ ] **Step 2: Call `_clearSelections()` on successful generation**

  In `watchGeneration`, find the `msg.status === 'done'` branch (around line 694). It currently reads:

  ```js
      if (msg.status === 'done') {
        $('gen-bar').style.width = '100%';
        state.resultJobId = jobId;
        showResult(msg.result);
  ```

  Change it to:

  ```js
      if (msg.status === 'done') {
        $('gen-bar').style.width = '100%';
        state.resultJobId = jobId;
        _clearSelections();
        showResult(msg.result);
  ```

  Do NOT add `_clearSelections()` to the `error` or `cancelled` branches — the user needs their selection intact to retry.

- [ ] **Step 3: Verify the existing test suite still passes**

  ```
  .\venv\Scripts\python.exe -m pytest tests/ -q
  ```

  Expected: `164 passed` (or however many pass currently). These are Python tests; the JS change has no Python surface so the count should not change.

- [ ] **Step 4: Manual smoke-test**

  Start the app:
  ```
  .\venv\Scripts\python.exe -c "from app import create_app; create_app().run(debug=True)"
  ```
  1. Open a folder with transcripts.
  2. Select some lines.
  3. Reload the page — selections should still be there (existing behaviour preserved).
  4. Generate a reel.
  5. When the result screen appears, navigate back to the workspace (or open the same folder again).
  6. Confirm all lines are deselected and localStorage key `sizzle_sel_<folder>` is gone (check in DevTools → Application → Local Storage).

- [ ] **Step 5: Commit**

  ```
  git add static/app.js
  git commit -m "feat: clear selections from localStorage and state after successful generation"
  ```

---

## Task 2: Add `updateClearAllBtn()`, `uncheckAllInFile()`, `clearHighlightsInFile()`

**Files:**
- Modify: `static/app.js`

### What to do

Add the two action functions and a `updateClearAllBtn()` function. Wire `updateClearAllBtn()` everywhere `updateSelectAllBtn()` is called.

- [ ] **Step 1: Add `uncheckAllInFile` and `clearHighlightsInFile` in `static/app.js`**

  Insert these two functions immediately after `checkAllInFile` (after line ~463):

  ```js
  function uncheckAllInFile(filename) {
    if (!state.checked[filename]) return;
    state.checked[filename] = new Set();
    renderTranscript(filename);
    refreshBadge(filename);
    updateGenerateBtn();
    _saveSelections();
  }
  ```

  Insert this immediately after `highlightAllInFile` (after line ~580):

  ```js
  function clearHighlightsInFile(filename) {
    if (!state.highlighted[filename]) return;
    state.highlighted[filename] = new Set();
    renderTranscript(filename);
    refreshBadge(filename);
    updateGenerateBtn();
    _saveSelections();
  }
  ```

- [ ] **Step 2: Add `updateClearAllBtn()` in `static/app.js`**

  Insert this function immediately after `updateSelectAllBtn()` (after line ~338):

  ```js
  function updateClearAllBtn() {
    const btn = $('btn-clear-all');
    if (!btn) return;  // guard: button may not exist in all layouts
    if (state.mode === 'checkbox') {
      btn.textContent = 'uncheck all';
      btn.onclick = () => uncheckAllInFile(state.activeFile);
    } else {
      btn.textContent = 'clear all';
      btn.onclick = () => clearHighlightsInFile(state.activeFile);
    }
  }
  ```

- [ ] **Step 3: Call `updateClearAllBtn()` everywhere `updateSelectAllBtn()` is called**

  Search the file for every call to `updateSelectAllBtn()` and add `updateClearAllBtn()` on the next line. Find them with:
  ```
  grep -n "updateSelectAllBtn" static/app.js
  ```
  For each occurrence, the pattern changes from:
  ```js
  updateSelectAllBtn();
  ```
  to:
  ```js
  updateSelectAllBtn();
  updateClearAllBtn();
  ```

- [ ] **Step 4: Run tests**

  ```
  .\venv\Scripts\python.exe -m pytest tests/ -q
  ```
  Expected: same count as before.

- [ ] **Step 5: Commit**

  ```
  git add static/app.js
  git commit -m "feat: add uncheckAllInFile, clearHighlightsInFile, updateClearAllBtn"
  ```

---

## Task 3: Add the "uncheck all" button to the HTML and style it

**Files:**
- Modify: `templates/index.html`
- Modify: `static/style.css`

### What to do

- [ ] **Step 1: Add the button to `templates/index.html`**

  Find this block (around line 111):
  ```html
  <div id="transcript-header">
    <span id="transcript-filename" class="transcript-filename"></span>
    <button id="btn-select-all" class="select-all-btn"></button>
  </div>
  ```

  Replace it with:
  ```html
  <div id="transcript-header">
    <span id="transcript-filename" class="transcript-filename"></span>
    <button id="btn-clear-all"  class="clear-all-btn"></button>
    <button id="btn-select-all" class="select-all-btn"></button>
  </div>
  ```

  `btn-clear-all` sits to the **left** of `btn-select-all` so it appears first visually.

- [ ] **Step 2: Add CSS to `static/style.css`**

  Append these rules after the existing `.select-all-btn` block (after line ~202):

  ```css
  .clear-all-btn { background: transparent; border: none; cursor: pointer; font-size: 10px; font-family: inherit; color: #888; text-decoration: underline; margin-right: 6px; }
  .clear-all-btn:hover { color: #555; }
  ```

- [ ] **Step 3: Run tests**

  ```
  .\venv\Scripts\python.exe -m pytest tests/ -q
  ```
  Expected: same count as before.

- [ ] **Step 4: Manual smoke-test**

  1. Open a folder, select some lines in checkbox mode.
  2. Confirm "uncheck all" button appears to the left of "check all".
  3. Click "uncheck all" — all lines for that file deselect.
  4. Switch to highlight mode — button reads "clear all".
  5. Highlight some lines, click "clear all" — all highlights for that file clear.
  6. Confirm the badge count drops to 0 after clearing.
  7. Confirm `localStorage` is updated (key still present but array empty for that file).

- [ ] **Step 5: Commit**

  ```
  git add templates/index.html static/style.css
  git commit -m "feat: add clear-all button to transcript header"
  ```

---

## Task 4: Push and verify

- [ ] **Step 1: Push to remote**

  ```
  git push
  ```

- [ ] **Step 2: Confirm all tests pass one final time**

  ```
  .\venv\Scripts\python.exe -m pytest tests/ -q
  ```

  Expected: all tests pass, no regressions.
