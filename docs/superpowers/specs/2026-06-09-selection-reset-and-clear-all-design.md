# Selection Reset on Generation & "Clear All" Button — Design Spec

**Date:** 2026-06-09

---

## Overview

Two small UX improvements to selection management in the transcript workspace:

1. **Reset selections after a successful reel generation** — localStorage-persisted selections should survive page reloads, but be wiped when the user successfully generates a reel so they start fresh on the next session with that folder.
2. **"Uncheck all" / "Clear all" button** — a second button to the left of the existing "check all" / "highlight all" button that clears all selections for the currently-viewed file.

---

## Feature 1: Clear Selections on Successful Generation

### Trigger
Selections are cleared when the WebSocket receives `{ type: "done", status: "done" }` — i.e., the reel was generated successfully. Errored (`status: "error"`) and cancelled (`status: "cancelled"`) jobs leave selections intact so the user can adjust and retry.

### What "clear" means
- Remove `sizzle_sel_<folder>` from `localStorage` so a subsequent page reload starts with an empty selection.
- Reset `state.checked[filename]` and `state.highlighted[filename]` to empty `Set`s for every filename in `state.files`. The in-memory state must match localStorage to avoid them diverging.

### Implementation point
`watchGeneration(jobId)` in `static/app.js`, inside the `msg.status === 'done'` branch, before calling `showResult(msg.result)`. A new helper `_clearSelections()` encapsulates both the localStorage removal and the in-memory reset.

```js
function _clearSelections() {
  // Wipe localStorage
  if (state.folder) {
    try { localStorage.removeItem('sizzle_sel_' + state.folder); } catch (_) {}
  }
  // Wipe in-memory Sets so that if the user navigates back to the workspace
  // the transcript renders with nothing selected.
  for (const filename of Object.keys(state.checked))   state.checked[filename]   = new Set();
  for (const filename of Object.keys(state.highlighted)) state.highlighted[filename] = new Set();
}
```

### Re-render
No explicit re-render is needed at clear time — the user is moving to the result screen. When they navigate back to the workspace (via the result screen's back button or by opening a new folder), `renderTranscript` is called naturally and will reflect the empty state.

---

## Feature 2: "Uncheck All" / "Clear All" Button

### Placement
A new `<button id="btn-clear-all">` is inserted immediately to the **left** of the existing `#btn-select-all` inside `#transcript-header`.

```html
<div id="transcript-header">
  <span id="transcript-filename" class="transcript-filename"></span>
  <button id="btn-clear-all"  class="clear-all-btn"></button>
  <button id="btn-select-all" class="select-all-btn"></button>
</div>
```

### Mode-aware label and handler
The button mirrors the mode-awareness of `#btn-select-all`. A new `updateClearAllBtn()` function sets label and `onclick` based on `state.mode`:

| Mode | Label | Action |
|------|-------|--------|
| checkbox | "uncheck all" | `uncheckAllInFile(state.activeFile)` |
| highlight | "clear all" | `clearHighlightsInFile(state.activeFile)` |

`updateClearAllBtn()` is called everywhere `updateSelectAllBtn()` is currently called.

### New action functions

```js
function uncheckAllInFile(filename) {
  if (!state.checked[filename]) return;
  state.checked[filename] = new Set();
  renderTranscript(filename);
  refreshBadge(filename);
  updateGenerateBtn();
  _saveSelections();
}

function clearHighlightsInFile(filename) {
  if (!state.highlighted[filename]) return;
  state.highlighted[filename] = new Set();
  renderTranscript(filename);
  refreshBadge(filename);
  updateGenerateBtn();
  _saveSelections();
}
```

### Styling
`.clear-all-btn` uses the same typographic style as `.select-all-btn` (transparent background, no border, `font-size: 10px`, underlined) but in a muted grey so it reads as a secondary/destructive action:

```css
.clear-all-btn { background: transparent; border: none; cursor: pointer; font-size: 10px; font-family: inherit; color: #888; text-decoration: underline; margin-right: 6px; }
.clear-all-btn:hover { color: #555; }
```

---

## Files Changed

| File | Change |
|------|--------|
| `static/app.js` | Add `_clearSelections()`, call it on successful generation; add `updateClearAllBtn()`, `uncheckAllInFile()`, `clearHighlightsInFile()`; wire `updateClearAllBtn()` everywhere `updateSelectAllBtn()` is called |
| `templates/index.html` | Add `<button id="btn-clear-all">` to the left of `#btn-select-all` |
| `static/style.css` | Add `.clear-all-btn` and `.clear-all-btn:hover` rules |

No backend changes. No new dependencies.

---

## Out of Scope
- Clearing selections when the user manually navigates away from a folder (already handled by existing reload-restore behaviour).
- A "clear all files" button that clears selections across all videos at once.
