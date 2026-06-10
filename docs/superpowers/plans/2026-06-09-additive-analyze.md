# Additive Multi-Prompt Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After running an initial analysis, expose a secondary prompt row that adds matched lines to the existing selection instead of replacing them.

**Architecture:** Frontend-only change across three files. The existing `/analyze` endpoint is reused unchanged. A new `runAddAnalyze()` function mirrors `runAnalyze()` but unions results into existing Sets instead of overwriting them. The secondary row (`#analyze-add-row`) is hidden until the first analysis succeeds, and is cleared/hidden again when `_clearSelections()` is called after generation.

**Tech Stack:** Vanilla JS, HTML, CSS. Python/pytest for existing test suite (164 tests; no new Python tests needed — this is a pure frontend change).

---

## Codebase orientation

Read these sections before touching anything:

**`static/app.js`:**
- `runAnalyze()` at line ~239 — fetches `/analyze`, then does a **full replace**:
  ```js
  state.checked[f.name]     = new Set(lines);   // ← replaces
  state.highlighted[f.name] = new Set(lines);   // ← replaces
  ```
  After a successful run it re-renders transcript, refreshes badges, saves selections.
- `_clearSelections()` at line ~41 — removes localStorage key and resets all Sets to empty; called when generation succeeds.
- `state.folder`, `state.files`, `state.checked`, `state.highlighted` — the key state.

**`templates/index.html` around line 100–129:**
```html
<div class="analyze-zone">
  <div class="analyze-label">Analyze</div>
  <div id="analyze-bar"> ... textarea + Analyze button ... </div>
  <div id="analyze-error" class="error-msg hidden" ...></div>
</div>
```
The new row goes after `#analyze-error`, still inside `.analyze-zone`.

**`static/style.css` around line 208–229:**
```css
#analyze-bar { display: flex; gap: 10px; align-items: flex-start; }
.analyze-input { flex: 1; height: 54px; resize: none; }
.btn-analyze { background: #1a5fb4; ... align-self: flex-end; }
```

**Tests:** `.\venv\Scripts\python.exe -m pytest tests/ -q` — all 164 must still pass.

---

## Task 1: Add `#analyze-add-row` to HTML and style it

**Files:**
- Modify: `templates/index.html`
- Modify: `static/style.css`

- [ ] **Step 1: Add the hidden row to `templates/index.html`**

  Find this block (around line 128):
  ```html
          <div id="analyze-error" class="error-msg hidden" style="padding:3px 14px;font-size:10px;"></div>
        </div>
  ```

  Replace with:
  ```html
          <div id="analyze-error" class="error-msg hidden" style="padding:3px 14px;font-size:10px;"></div>
          <div id="analyze-add-row" class="analyze-add-row hidden">
            <textarea id="analyze-add-input" class="footer-input analyze-input"
                      placeholder="Add another angle…"></textarea>
            <button id="btn-analyze-add" type="button" class="btn-analyze-add">+ Add to selection</button>
          </div>
        </div>
  ```

  The row is inside `.analyze-zone`, after `#analyze-error`, before the closing `</div>` that ends `.analyze-zone`.

- [ ] **Step 2: Add CSS to `static/style.css`**

  Find the line `.btn-analyze:disabled { opacity: 0.5; cursor: default; }` (around line 229). Append these rules immediately after it:

  ```css
  .analyze-add-row { display: flex; gap: 10px; align-items: flex-start; margin-top: 8px; padding-top: 8px; border-top: 1px solid #1a2840; }
  .btn-analyze-add { background: #1a3a6a; color: #aac4e8; border: none; border-radius: 4px; padding: 5px 14px; font-size: 11px; cursor: pointer; font-family: inherit; white-space: nowrap; font-weight: 500; align-self: flex-end; }
  .btn-analyze-add:hover { background: #1a4a8a; color: #fff; }
  .btn-analyze-add:disabled { opacity: 0.5; cursor: default; }
  ```

- [ ] **Step 3: Run tests**

  ```
  .\venv\Scripts\python.exe -m pytest tests/ -q
  ```
  Expected: 164 passed, no failures.

- [ ] **Step 4: Commit**

  ```
  git add templates/index.html static/style.css
  git commit -m "feat: add hidden additive analyze row to HTML and CSS"
  ```

---

## Task 2: Implement `runAddAnalyze()` and wire it up

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Add `runAddAnalyze()` after `runAnalyze()`**

  Find the closing `}` of `runAnalyze()` (after the `finally` block, around line 290). Insert the new function immediately after it:

  ```js
  async function runAddAnalyze() {
    const prompt = $('analyze-add-input').value.trim();
    if (!prompt) return;

    $('btn-analyze-add').textContent = 'Analyzing…';
    $('btn-analyze-add').disabled = true;
    $('analyze-add-input').disabled = true;
    $('analyze-error').classList.add('hidden');

    try {
      const resp = await fetch('/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder: state.folder, prompt }),
      });
      const data = await resp.json();

      if (!resp.ok) {
        $('analyze-error').textContent = data.error || 'Analyze failed';
        $('analyze-error').classList.remove('hidden');
        return;
      }

      // Union — add new lines without removing existing selections
      state.files.forEach(f => {
        const lines = data.highlights[f.name] || [];
        lines.forEach(l => state.checked[f.name].add(l));
        lines.forEach(l => state.highlighted[f.name].add(l));
      });

      if (state.activeFile) renderTranscript(state.activeFile);
      state.files.forEach(f => refreshBadge(f.name));
      updateGenerateBtn();
      _saveSelections();

    } catch (err) {
      $('analyze-error').textContent = 'Network error: ' + err.message;
      $('analyze-error').classList.remove('hidden');
    } finally {
      $('btn-analyze-add').textContent = '+ Add to selection';
      $('btn-analyze-add').disabled = false;
      $('analyze-add-input').disabled = false;
    }
  }
  ```

- [ ] **Step 2: Wire the button and Enter key**

  Find the lines that wire `runAnalyze` to the button and textarea (around line 234):
  ```js
  $('btn-analyze').addEventListener('click', runAnalyze);
  $('analyze-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') runAnalyze();
  });
  ```

  Immediately after those lines, add:
  ```js
  $('btn-analyze-add').addEventListener('click', runAddAnalyze);
  $('analyze-add-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) runAddAnalyze();
  });
  ```

  (Shift+Enter still inserts a newline; plain Enter submits — consistent with the primary textarea behaviour.)

- [ ] **Step 3: Reveal the row after a successful first analyze**

  Inside `runAnalyze()`, find the line where `state.lastPrompt = prompt;` is set (around line 268). Immediately after `_saveSelections();` (the last line in the success block, around line 280), add:

  ```js
  $('analyze-add-row').classList.remove('hidden');
  ```

  The success block should look like:
  ```js
  state.lastPrompt = prompt;

  // Apply returned highlights to BOTH sets so mode-switching preserves analysis
  state.files.forEach(f => {
    const lines = data.highlights[f.name] || [];
    state.checked[f.name] = new Set(lines);
    state.highlighted[f.name] = new Set(lines);
  });

  if (state.activeFile) renderTranscript(state.activeFile);
  state.files.forEach(f => refreshBadge(f.name));
  updateGenerateBtn();
  _saveSelections();
  $('analyze-add-row').classList.remove('hidden');
  ```

- [ ] **Step 4: Hide and reset the row in `_clearSelections()`**

  Find `_clearSelections()` (around line 41). It currently ends with:
  ```js
  for (const filename of Object.keys(state.checked))     state.checked[filename]     = new Set();
  for (const filename of Object.keys(state.highlighted)) state.highlighted[filename] = new Set();
  ```

  Add two lines after those:
  ```js
  $('analyze-add-row').classList.add('hidden');
  $('analyze-add-input').value = '';
  ```

  Final `_clearSelections()`:
  ```js
  function _clearSelections() {
    if (state.folder) {
      try { localStorage.removeItem('sizzle_sel_' + state.folder); } catch (_) {}
    }
    for (const filename of Object.keys(state.checked))     state.checked[filename]     = new Set();
    for (const filename of Object.keys(state.highlighted)) state.highlighted[filename] = new Set();
    $('analyze-add-row').classList.add('hidden');
    $('analyze-add-input').value = '';
  }
  ```

- [ ] **Step 5: Run tests**

  ```
  .\venv\Scripts\python.exe -m pytest tests/ -q
  ```
  Expected: 164 passed, no failures.

- [ ] **Step 6: Commit**

  ```
  git add static/app.js
  git commit -m "feat: implement additive analyze — runAddAnalyze unions results into existing selection"
  ```

---

## Task 3: Push and verify

- [ ] **Step 1: Run full test suite one final time**

  ```
  .\venv\Scripts\python.exe -m pytest tests/ -q
  ```
  Expected: all tests pass.

- [ ] **Step 2: Push to remote**

  ```
  git push
  ```

- [ ] **Step 3: Manual smoke-test checklist** (do this locally, not on the deployed app)

  Start the app:
  ```
  .\venv\Scripts\python.exe -c "from app import create_app; create_app().run(debug=True)"
  ```

  1. Open a folder with transcripts.
  2. Enter a prompt → click **Analyze** → confirm lines highlight.
  3. Confirm the **"+ Add to selection"** row appears below.
  4. Note which lines are currently selected.
  5. Enter a different prompt in the add row → click **"+ Add to selection"**.
  6. Confirm newly matched lines are added; previously selected lines remain.
  7. Click the original **Analyze** button with a new prompt → confirm all selections reset (full replace behaviour preserved).
  8. Confirm the add row is still visible after a full re-analyze.
  9. Generate a reel → on the result screen confirm the add row is hidden when navigating back.
