# Analyze Zone Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the workspace screen so the analyze bar occupies a prominent top zone and the transcript lives in the scrollable zone below it, filling the previously empty space to the right of the video file list.

**Architecture:** Pure HTML + CSS change to `templates/index.html` and `static/style.css`. The `#main-panel` gains two child wrappers (`.analyze-zone` and `.transcript-zone`); the analyze prompt input becomes a `<textarea>` to fill the taller area naturally. No Python, no JS logic changes.

**Tech Stack:** HTML, CSS (flexbox). Flask Jinja2 template (no template logic changes).

---

## Files

- Modify: `templates/index.html` (lines 90–112 — workspace `#main-panel`)
- Modify: `static/style.css` (lines 204–219 — ANALYZE BAR section, plus new rules)

---

### Task 1: Restructure `#main-panel` HTML

**Files:**
- Modify: `templates/index.html:90-112`

Current structure of `#main-panel` (lines 90–112):

```html
<section id="main-panel">
  <div id="transcript-header">
    <span id="transcript-filename" class="transcript-filename"></span>
    <button id="btn-select-all" class="select-all-btn"></button>
  </div>
  <div id="analyze-bar">
    <input id="analyze-input" type="text" class="footer-input analyze-input"
           placeholder="Describe what you're looking for…">
    <button id="btn-analyze" class="btn-analyze">Analyze</button>
  </div>
  <div id="analyze-error" class="error-msg hidden" style="padding:3px 14px;font-size:10px;"></div>
  <div id="transcript-scroll" class="transcript-scroll"></div>
  <footer id="workspace-footer">
    <div class="footer-field">
      <label class="footer-label">Output filename</label>
      <input id="output-filename" type="text" class="footer-input filename-input" value="sizzle_reel.mp4">
    </div>
    <div class="footer-field footer-field-btn">
      <label class="footer-label">&nbsp;</label>
      <button id="btn-generate" class="btn-generate" disabled>▶ Generate Reel</button>
    </div>
  </footer>
</section>
```

- [ ] **Step 1: Confirm all tests pass before touching anything**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all 99 tests pass.

- [ ] **Step 2: Replace the contents of `#main-panel` with the two-zone structure**

Replace lines 90–112 with:

```html
      <section id="main-panel">
        <div class="analyze-zone">
          <div class="analyze-label">Analyze</div>
          <div id="analyze-bar">
            <textarea id="analyze-input" class="footer-input analyze-input"
                      placeholder="Describe what you're looking for…"></textarea>
            <button id="btn-analyze" class="btn-analyze">Analyze</button>
          </div>
          <div id="analyze-error" class="error-msg hidden" style="padding:3px 14px;font-size:10px;"></div>
        </div>
        <div class="transcript-zone">
          <div id="transcript-header">
            <span id="transcript-filename" class="transcript-filename"></span>
            <button id="btn-select-all" class="select-all-btn"></button>
          </div>
          <div id="transcript-scroll" class="transcript-scroll"></div>
          <footer id="workspace-footer">
            <div class="footer-field">
              <label class="footer-label">Output filename</label>
              <input id="output-filename" type="text" class="footer-input filename-input" value="sizzle_reel.mp4">
            </div>
            <div class="footer-field footer-field-btn">
              <label class="footer-label">&nbsp;</label>
              <button id="btn-generate" class="btn-generate" disabled>▶ Generate Reel</button>
            </div>
          </footer>
        </div>
      </section>
```

Key changes:
- `#transcript-header` moved inside `.transcript-zone` (was first child of `#main-panel`)
- `#analyze-bar` + `#analyze-error` wrapped in `.analyze-zone` (new)
- `#transcript-header`, `#transcript-scroll`, `#workspace-footer` wrapped in `.transcript-zone` (new)
- `<input id="analyze-input" type="text">` → `<textarea id="analyze-input">` (no `type` attribute on textarea)

- [ ] **Step 3: Run tests — confirm they still all pass**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all 99 tests pass. (Tests cover Flask endpoints, not HTML structure — they should be unaffected.)

- [ ] **Step 4: Commit**

```
git add templates/index.html
git commit -m "refactor: restructure main-panel into analyze-zone + transcript-zone"
```

---

### Task 2: Update CSS for the two zones

**Files:**
- Modify: `static/style.css:204-219`

Current ANALYZE BAR section (lines 204–219):

```css
/* ANALYZE BAR */
#analyze-bar {
  display: flex; gap: 8px; padding: 8px 16px;
  background: #090d18; border-bottom: 1px solid #1a2840; flex-shrink: 0;
}
.analyze-input { flex: 1; }
.btn-analyze {
  background: #1a5fb4; color: #fff; border: none;
  border-radius: 4px; padding: 5px 14px; font-size: 11px; cursor: pointer;
  font-family: inherit; white-space: nowrap; font-weight: 500;
}
.btn-analyze:hover { background: #1e6fc8; }
.btn-analyze:disabled { opacity: 0.5; cursor: default; }
.btn-generate:disabled { opacity: 0.4; cursor: default; }
```

- [ ] **Step 1: Replace the ANALYZE BAR block and add zone rules**

Replace lines 204–219 (from `/* ANALYZE BAR */` through `.btn-generate:disabled` line) with:

```css
/* ANALYZE ZONE */
.analyze-zone {
  background: #090d18;
  border-bottom: 2px solid #1a2840;
  padding: 14px 16px 10px;
  flex-shrink: 0;
}
.analyze-label {
  font-size: 9px; color: #2a3a50; text-transform: uppercase;
  letter-spacing: 1.5px; margin-bottom: 8px;
}
#analyze-bar {
  display: flex; gap: 10px; align-items: flex-start; padding: 0;
}
.analyze-input { flex: 1; height: 54px; resize: none; }
.btn-analyze {
  background: #1a5fb4; color: #fff; border: none;
  border-radius: 4px; padding: 5px 14px; font-size: 11px; cursor: pointer;
  font-family: inherit; white-space: nowrap; font-weight: 500;
  align-self: flex-end;
}
.btn-analyze:hover { background: #1e6fc8; }
.btn-analyze:disabled { opacity: 0.5; cursor: default; }
.btn-generate:disabled { opacity: 0.4; cursor: default; }

/* TRANSCRIPT ZONE */
.transcript-zone {
  flex: 1; display: flex; flex-direction: column; overflow: hidden; min-height: 0;
}
```

What changed vs current:
- `#analyze-bar`: removed `background`, `border-bottom`, `flex-shrink: 0` (moved to `.analyze-zone`); changed `gap: 8px` → `gap: 10px`; changed `padding: 8px 16px` → `padding: 0`; added `align-items: flex-start`
- `.analyze-input`: added `height: 54px; resize: none;` (textarea sizing)
- `.btn-analyze`: added `align-self: flex-end` (pins button to bottom of textarea)
- Added `.analyze-zone` rule (wraps the bar)
- Added `.analyze-label` rule (small uppercase label above input)
- Added `.transcript-zone` rule (the flex-1 lower zone)

Note: `.analyze-input` still carries class `footer-input` from the HTML, which provides `background`, `border`, `border-radius`, `padding`, `color`, and `font-family: inherit`. No change needed there — the textarea inherits those styles correctly.

- [ ] **Step 2: Run tests — confirm all still pass**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all 99 tests pass.

- [ ] **Step 3: Visual check — run the app and open a folder**

```powershell
.\venv\Scripts\python.exe sizzle.py <any_folder_with_videos> --prompt test
```

Or just start the Flask server if you have one. Open the workspace screen with a video selected. Verify:
- Analyze section appears as a distinct top band (~110px tall) with "ANALYZE" label, textarea, and Analyze button pinned to the textarea bottom
- Transcript (filename header + lines + footer) fills the remaining height below
- No visual overlap or layout collapse
- The Analyze button and Generate Reel button still respond to clicks normally
- The textarea accepts text input (Enter key inserts newline, which is expected — use the Analyze button to submit)

- [ ] **Step 4: Commit**

```
git add static/style.css
git commit -m "style: add analyze-zone and transcript-zone CSS for two-zone workspace layout"
```
