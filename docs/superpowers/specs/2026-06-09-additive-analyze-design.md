# Additive Multi-Prompt Analysis — Design Spec

**Date:** 2026-06-09

---

## Overview

After running an initial analysis, users can run additional prompts that **add** matched lines to the existing selection rather than replacing it. This lets users build a reel from multiple search angles (e.g. "funny moments" + "product announcements") without losing their previous selections.

---

## User Flow

1. User enters a prompt in the existing analyze input → clicks **Analyze** (existing behaviour, replaces selections).
2. Once the first analysis completes successfully, a second row appears below the main prompt bar labelled **"Analyze again"**.
3. User types a second prompt in that row → clicks **"+ Add to selection"** → new matched lines are unioned into the existing selection (no lines are removed).
4. The secondary row **stays visible** after the additive run. The user can clear its input, type a third prompt, and click again. This is repeatable.
5. If the user clicks the original **Analyze** button again (with a new or edited prompt), it behaves as today — **full replace** — and the secondary row resets/clears.

---

## What Changes

### `/analyze` endpoint (backend)
**No changes.** The endpoint already returns matched lines for all videos in a folder. The additive vs. replace distinction is handled entirely in the frontend.

### `static/app.js`

**`runAnalyze()` — minor change:**
After a successful analyze, reveal the hidden additive row:
```js
$('analyze-add-row').classList.remove('hidden');
```

**New function `runAddAnalyze()`:**
Same fetch to `/analyze` as `runAnalyze`, but instead of replacing Sets, it unions:
```js
state.files.forEach(f => {
  const lines = data.highlights[f.name] || [];
  lines.forEach(l => state.checked[f.name].add(l));
  lines.forEach(l => state.highlighted[f.name].add(l));
});
```
Then re-renders transcript, refreshes badges, saves selections — same as today.

Button/input disable pattern during the request is identical to `runAnalyze`.

**`_clearSelections()` — minor change:**
When clearing (after generation), also hide the additive row and clear its input so the workspace starts fresh:
```js
$('analyze-add-row').classList.add('hidden');
$('analyze-add-input').value = '';
```

### `templates/index.html`

Add a new `#analyze-add-row` div immediately after `#analyze-error`, initially hidden:
```html
<div id="analyze-add-row" class="analyze-add-row hidden">
  <textarea id="analyze-add-input" class="footer-input analyze-input"
            placeholder="Add another angle…"></textarea>
  <button id="btn-analyze-add" type="button" class="btn-analyze-add">+ Add to selection</button>
</div>
```

### `static/style.css`

The additive row uses the same flex layout as `#analyze-bar` but with a visual distinction (dimmer button, top border separator) so it reads as secondary:
```css
.analyze-add-row {
  display: flex; gap: 10px; align-items: flex-start;
  margin-top: 8px; padding-top: 8px;
  border-top: 1px solid #1a2840;
}
.btn-analyze-add {
  background: #1a3a6a; color: #aac4e8; border: none;
  border-radius: 4px; padding: 5px 14px; font-size: 11px; cursor: pointer;
  font-family: inherit; white-space: nowrap; font-weight: 500;
  align-self: flex-end;
}
.btn-analyze-add:hover { background: #1a4a8a; color: #fff; }
.btn-analyze-add:disabled { opacity: 0.5; cursor: default; }
```

---

## Edge Cases

| Scenario | Behaviour |
|----------|-----------|
| Second prompt returns lines already in selection | `Set.add()` is idempotent — no duplicates |
| Second prompt returns no matches | Selection unchanged, no error shown |
| User clicks original Analyze with a new prompt | Full replace as today; additive row input is cleared but row stays visible |
| Generation succeeds | `_clearSelections()` hides the additive row and clears its input |
| Error on additive fetch | Show error in `#analyze-error`, leave selection unchanged |

---

## Files Changed

| File | Change |
|------|--------|
| `static/app.js` | Add `runAddAnalyze()`; show additive row after first analyze; clear+hide row in `_clearSelections()` |
| `templates/index.html` | Add `#analyze-add-row` with textarea + button, hidden by default |
| `static/style.css` | Add `.analyze-add-row`, `.btn-analyze-add`, `.btn-analyze-add:hover`, `.btn-analyze-add:disabled` |

No backend changes. No new Python tests needed (pure frontend).
