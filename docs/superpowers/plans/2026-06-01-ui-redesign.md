# UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cosmetic redesign — Pro Blue colour palette, underline nav tabs, system-ui typography, wider sidebar, and a split-screen folder picker with recent folders history.

**Architecture:** Four independent tasks in dependency order: (1) recent-folders backend + tests, (2) folder picker HTML restructure, (3) full CSS rewrite, (4) recent-folders JS. No functionality changes — every existing test must still pass after each task.

**Tech Stack:** Flask, vanilla JS, CSS. No new dependencies.

---

## Files Changed

| File | Change |
|------|--------|
| `app.py` | Add `RECENT_FOLDERS_PATH`, `_recent_folders_lock`, `_load_recent_folders()`, `_save_recent_folder()`, `GET /recent-folders`; update `POST /load-folder` to call `_save_recent_folder` |
| `tests/test_app.py` | Add 4 tests for recent-folders endpoints |
| `templates/index.html` | Replace `.center-card` in folder picker with `.picker-split` layout; add `#recent-folders-section` |
| `static/style.css` | Full rewrite — new colours, underline tabs, wider sidebar, split-picker classes, recent-folders classes |
| `static/app.js` | Add `relativeTime()`, `loadRecentFolders()`, `renderRecentFolders()`; call on page load |
| `.gitignore` | Add `recent_folders.json` |

---

## Task 1: Recent Folders Backend

**Files:**
- Modify: `app.py`
- Modify: `tests/test_app.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing tests**

Add to the end of `tests/test_app.py`:

```python
def test_recent_folders_starts_empty(client, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "RECENT_FOLDERS_PATH", tmp_path / "recent.json")
    resp = client.get("/recent-folders")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_load_folder_saves_to_recent(client, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "RECENT_FOLDERS_PATH", tmp_path / "recent.json")
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text("[0:05] Speaker: Hi.", encoding="utf-8")
    client.post("/load-folder", json={"folder": str(tmp_path)})
    recent = client.get("/recent-folders").get_json()
    assert len(recent) == 1
    assert recent[0]["path"] == str(tmp_path)
    assert recent[0]["video_count"] == 1
    assert "last_opened" in recent[0]


def test_recent_folders_deduplicates_on_reopen(client, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "RECENT_FOLDERS_PATH", tmp_path / "recent.json")
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text("[0:05] Speaker: Hi.", encoding="utf-8")
    client.post("/load-folder", json={"folder": str(tmp_path)})
    client.post("/load-folder", json={"folder": str(tmp_path)})
    recent = client.get("/recent-folders").get_json()
    assert len(recent) == 1


def test_recent_folders_capped_at_five(client, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "RECENT_FOLDERS_PATH", tmp_path / "recent.json")
    for i in range(6):
        d = tmp_path / f"f{i}"
        d.mkdir()
        (d / "vid.mp4").touch()
        (d / "vid.txt").write_text("[0:05] Speaker: Hi.", encoding="utf-8")
        client.post("/load-folder", json={"folder": str(d)})
    recent = client.get("/recent-folders").get_json()
    assert len(recent) == 5
```

- [ ] **Step 2: Run tests to confirm they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_recent_folders_starts_empty tests/test_app.py::test_load_folder_saves_to_recent tests/test_app.py::test_recent_folders_deduplicates_on_reopen tests/test_app.py::test_recent_folders_capped_at_five -v
```

Expected: 4 FAILED (no attribute `RECENT_FOLDERS_PATH`, no route `/recent-folders`)

- [ ] **Step 3: Add constants and lock to `app.py`**

After the existing `LIBRARY_PATH` line (line 37), add:

```python
LIBRARY_PATH = Path(__file__).parent / "sizzle_library.json"
RECENT_FOLDERS_PATH = Path(__file__).parent / "recent_folders.json"
```

After the existing `_library_lock = threading.Lock()` line, add:

```python
_recent_folders_lock = threading.Lock()
```

- [ ] **Step 4: Add `_load_recent_folders` and `_save_recent_folder` to `app.py`**

After the `_library_add` function (around line 198), add:

```python
def _load_recent_folders() -> list:
    if not RECENT_FOLDERS_PATH.exists():
        return []
    try:
        with RECENT_FOLDERS_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_recent_folder(folder: str, video_count: int) -> None:
    """Prepend folder to recent_folders.json, deduplicate by path, keep max 5."""
    with _recent_folders_lock:
        entries = [e for e in _load_recent_folders() if e.get("path") != folder]
        entries.insert(0, {
            "path": folder,
            "video_count": video_count,
            "last_opened": datetime.now().isoformat(timespec="seconds"),
        })
        entries = entries[:5]
        with RECENT_FOLDERS_PATH.open("w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
```

- [ ] **Step 5: Add `GET /recent-folders` route inside `create_app`**

After the `@app.post("/browse")` route, add:

```python
    @app.get("/recent-folders")
    def recent_folders():
        return jsonify(_load_recent_folders())
```

- [ ] **Step 6: Call `_save_recent_folder` from `load_folder`**

In the `load_folder` route, after the `_filter_generated_reels` block and the `if not video_paths` guard, add one line:

```python
        video_paths = _filter_generated_reels(video_paths)
        if not video_paths:
            return jsonify({"error": "No source video files found (folder contains only previously generated reels)"}), 422

        _save_recent_folder(folder, len(video_paths))   # ← add this line

        filenames = [p.name for p in video_paths]
```

- [ ] **Step 7: Run the 4 new tests — expect PASS**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_recent_folders_starts_empty tests/test_app.py::test_load_folder_saves_to_recent tests/test_app.py::test_recent_folders_deduplicates_on_reopen tests/test_app.py::test_recent_folders_capped_at_five -v
```

Expected: 4 PASSED

- [ ] **Step 8: Run full suite — expect all pass**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: 98 passed

- [ ] **Step 9: Add `recent_folders.json` to `.gitignore`**

Append one line to `.gitignore`:

```
recent_folders.json
```

- [ ] **Step 10: Commit**

```bash
git add app.py tests/test_app.py .gitignore
git commit -m "feat: add recent-folders backend (GET /recent-folders, auto-save on load)"
```

---

## Task 2: Folder Picker HTML Restructure

**Files:**
- Modify: `templates/index.html`

No new tests — visual change verified by running the app.

- [ ] **Step 1: Replace the folder picker screen in `templates/index.html`**

Find and replace the entire `<div id="screen-folder-picker" ...>` block.

**Old** (lines ~33–46):
```html
    <!-- FOLDER PICKER SCREEN -->
    <div id="screen-folder-picker" class="screen">
      <div class="center-card">
        <h2>Open a folder</h2>
        <p class="subtitle">Select the folder containing your video files.</p>
        <div class="folder-input-row">
          <input id="folder-path-input" type="text" placeholder="Paste a folder path..." class="folder-path-input">
          <button id="btn-browse" class="btn-primary">Browse...</button>
        </div>
        <div id="folder-error" class="error-msg hidden"></div>
        <button id="btn-load-folder" class="btn-primary" style="margin-top:12px">Open Folder</button>
      </div>
    </div>
```

**New:**
```html
    <!-- FOLDER PICKER SCREEN -->
    <div id="screen-folder-picker" class="screen">
      <div class="picker-split">
        <!-- Left: branding panel -->
        <div class="picker-brand">
          <div class="picker-brand-icon">🎬</div>
          <div class="picker-brand-title">SIZZLE REEL</div>
          <div class="picker-brand-tagline">AI-powered video<br>highlight extraction</div>
          <div class="picker-steps">
            <div class="picker-step">
              <span class="picker-step-num">1</span>Open a folder of videos
            </div>
            <div class="picker-step">
              <span class="picker-step-num">2</span>Describe what you want
            </div>
            <div class="picker-step">
              <span class="picker-step-num">3</span>Generate your reel
            </div>
          </div>
        </div>
        <!-- Right: form + recent folders -->
        <div class="picker-form">
          <h2>Open a folder</h2>
          <p class="subtitle">Select the folder containing your video files.</p>
          <div class="folder-input-row">
            <input id="folder-path-input" type="text" placeholder="Paste a folder path..." class="folder-path-input">
            <button id="btn-browse" class="btn-primary">Browse…</button>
          </div>
          <div id="folder-error" class="error-msg hidden"></div>
          <button id="btn-load-folder" class="btn-primary" style="margin-top:4px">Open Folder</button>
          <div id="recent-folders-section" class="recent-folders-section hidden">
            <div class="recent-folders-label">Recent folders</div>
            <ul id="recent-folders-list" class="recent-folders-list"></ul>
          </div>
        </div>
      </div>
    </div>
```

- [ ] **Step 2: Run existing tests — expect all still pass**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: 98 passed (HTML change doesn't affect backend tests)

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "feat: restructure folder picker to split-screen layout with recent folders slot"
```

---

## Task 3: CSS Full Rewrite

**Files:**
- Modify: `static/style.css` (full replacement)

No automated tests — verify visually by running the app.

- [ ] **Step 1: Replace the entire contents of `static/style.css`**

```css
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: #080c14;
  color: #b0c0d8;
  font-family: system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  font-size: 13px;
  height: 100vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

#app { display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

/* TOP BAR */
#topbar {
  background: #0c1524;
  border-bottom: 1px solid #1a2840;
  padding: 0 18px;
  display: flex;
  align-items: center;
  gap: 16px;
  flex-shrink: 0;
  height: 46px;
}

.logo { font-weight: 700; color: #4d9fff; font-size: 14px; letter-spacing: 1.5px; }

/* Underline nav tabs */
.nav-tabs {
  display: flex;
  gap: 20px;
  align-self: stretch;
  align-items: center;
  background: transparent;
  border-radius: 0;
  padding: 0;
}
.nav-tab {
  padding: 0 2px 4px;
  border-radius: 0;
  font-size: 11px;
  cursor: pointer;
  color: #334;
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  font-family: inherit;
  font-weight: 500;
  align-self: stretch;
  display: flex;
  align-items: center;
  transition: color 0.15s;
}
.nav-tab:hover { color: #556; }
.nav-tab.active { color: #4d9fff; border-bottom-color: #4d9fff; }

#topbar-controls { display: flex; align-items: center; gap: 10px; margin-left: 4px; }

.folder-badge {
  background: #0d1e38; border: 1px solid #1a3260; border-radius: 4px;
  padding: 3px 10px; font-size: 11px; color: #5090d0;
}

.mode-toggle { display: flex; background: #060a12; border: 1px solid #1a2840; border-radius: 16px; padding: 2px; }
.mode-btn {
  padding: 4px 14px; border-radius: 14px; font-size: 10px; cursor: pointer;
  color: #334; background: transparent; border: none; font-family: inherit;
  transition: background 0.15s, color 0.15s;
}
.mode-btn.active { background: #1a5fb4; color: white; }

/* TAB PANELS */
.tab-panel { flex: 1; overflow: hidden; display: flex; flex-direction: column; }
.tab-panel.hidden { display: none; }

/* SCREENS */
.screen { flex: 1; overflow: auto; display: flex; flex-direction: column; }
.screen.hidden { display: none !important; }

/* FOLDER PICKER — split layout */
.picker-split {
  display: flex;
  flex: 1;
  overflow: hidden;
  min-height: 0;
}

.picker-brand {
  width: 42%;
  background: linear-gradient(160deg, #0d1e38, #080c14);
  border-right: 1px solid #1a2840;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 40px;
  flex-shrink: 0;
}
.picker-brand-icon { font-size: 44px; line-height: 1; }
.picker-brand-title { color: #4d9fff; font-size: 20px; font-weight: 700; letter-spacing: 2px; }
.picker-brand-tagline { color: #2a4060; font-size: 12px; text-align: center; line-height: 1.8; }

.picker-steps { display: flex; flex-direction: column; gap: 10px; margin-top: 12px; width: 100%; max-width: 210px; }
.picker-step { display: flex; align-items: center; gap: 12px; color: #2a4060; font-size: 11px; }
.picker-step-num {
  width: 22px; height: 22px;
  background: #0d1e38; border: 1px solid #1a3260; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 10px; color: #4d9fff; flex-shrink: 0;
}

.picker-form {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 40px;
  gap: 12px;
  overflow-y: auto;
}
.picker-form h2 { color: #b8d8ff; font-size: 20px; font-weight: 600; }

/* TRANSCRIBING / GENERATING centered cards */
.center-card {
  margin: auto; padding: 40px; max-width: 500px; width: 100%;
  display: flex; flex-direction: column; gap: 12px; align-items: flex-start;
}
.center-card h2 { color: #b8d8ff; font-size: 18px; font-weight: 600; }

.subtitle { color: #3a5070; font-size: 12px; }
.folder-input-row { display: flex; gap: 8px; width: 100%; }
.folder-path-input {
  flex: 1; background: #060a12; border: 1px solid #1a2840;
  border-radius: 4px; padding: 7px 10px; color: #b0c0d8; font-family: inherit; font-size: 12px;
}
.error-msg { color: #e94560; font-size: 11px; }

/* RECENT FOLDERS */
.recent-folders-section { margin-top: 8px; padding-top: 16px; border-top: 1px solid #1a2840; }
.recent-folders-label {
  font-size: 9px; color: #2a3a50; text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 8px;
}
.recent-folders-list { list-style: none; display: flex; flex-direction: column; gap: 4px; }
.recent-folder-item {
  display: flex; align-items: center; justify-content: space-between;
  padding: 7px 10px; border-radius: 4px; cursor: pointer;
  border: 1px solid transparent; transition: background 0.1s, border-color 0.1s;
}
.recent-folder-item:hover { background: #0c1524; border-color: #1a2840; }
.recent-folder-item:first-child { background: #0c1524; border-color: #1a2840; }
.recent-folder-name { color: #5090d0; font-size: 11px; }
.recent-folder-meta { color: #2a3a50; font-size: 10px; }

.progress-bar-wrap { width: 100%; background: #060a12; border-radius: 4px; height: 6px; overflow: hidden; }
.progress-bar { height: 100%; background: linear-gradient(90deg, #1a5fb4, #4d9fff); border-radius: 4px; transition: width 0.3s; }

.log-box {
  width: 100%; background: #060a12; border: 1px solid #1a2840; border-radius: 6px;
  padding: 10px 12px; font-size: 10px; color: #2a3a50; line-height: 1.9;
  max-height: 180px; overflow-y: auto; font-family: monospace;
}
.log-done { color: #2ecc71; }
.log-active { color: #4d9fff; }
.log-error { color: #e94560; }
.log-info { color: #5090d0; }

/* WORKSPACE */
.workspace-layout { flex: 1; display: flex; overflow: hidden; min-height: 0; }

#sidebar {
  width: 210px; background: #0a0e1a; border-right: 1px solid #1a2840;
  display: flex; flex-direction: column; flex-shrink: 0; overflow: hidden;
}
.sidebar-header {
  padding: 8px 14px; font-size: 9px; color: #2a3a50; text-transform: uppercase;
  letter-spacing: 1.5px; border-bottom: 1px solid #1a2840; flex-shrink: 0;
}
#sidebar-list { flex: 1; overflow-y: auto; list-style: none; }
.sidebar-item {
  padding: 9px 12px; cursor: pointer; border-left: 3px solid transparent;
  font-size: 11px; color: #3a5070; display: flex; flex-direction: column; gap: 3px;
}
.sidebar-item:hover { background: #0d1830; }
.sidebar-item.active { border-left-color: #4d9fff; color: #b8d8ff; background: #0d1830; }
.sidebar-item .item-name { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sidebar-item .item-badge { font-size: 10px; color: #2a3a50; }
.badge-checked { color: #2ecc71; }
.badge-highlighted { color: #f39c12; }

#main-panel { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }

#transcript-header {
  padding: 8px 16px; background: #0c1524; border-bottom: 1px solid #1a2840;
  display: flex; align-items: center; justify-content: space-between; flex-shrink: 0;
}
.transcript-filename { color: #5090d0; font-weight: 600; font-size: 11px; }
.select-all-btn { background: transparent; border: none; cursor: pointer; font-size: 10px; font-family: inherit; }
.select-all-btn.checkbox-mode { color: #2ecc71; text-decoration: underline; }
.select-all-btn.checkbox-mode:hover { color: #27ae60; }
.select-all-btn.highlight-mode { color: #f39c12; text-decoration: underline; }
.select-all-btn.highlight-mode:hover { color: #e67e22; }

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

.transcript-scroll { flex: 1; overflow-y: auto; padding: 10px 16px; }

/* CHECKBOX MODE */
.minute-group { border: 1px solid #1a2840; border-radius: 6px; margin-bottom: 8px; overflow: hidden; }
.minute-label {
  background: #0e1828; padding: 5px 12px; font-size: 9px; color: #2a3a50;
  text-transform: uppercase; letter-spacing: 0.5px; display: flex;
  align-items: center; gap: 8px; border-bottom: 1px solid #1a2840;
}
.minute-label-cb {
  background: #0e1828; padding: 5px 12px; font-size: 9px; color: #5090d0;
  text-transform: uppercase; letter-spacing: 0.5px; display: flex;
  align-items: center; gap: 8px; border-bottom: 1px solid #1a2840;
  cursor: pointer; user-select: none;
}
.minute-label-cb:hover { background: #111e30; }

.transcript-line-cb {
  display: flex; align-items: flex-start; gap: 10px; padding: 6px 12px;
  border-bottom: 1px solid #0e1828; cursor: pointer; user-select: none;
}
.transcript-line-cb:hover { background: #0a1020; }
.transcript-line-cb:last-child { border-bottom: none; }

.cb-box {
  width: 14px; height: 14px; border: 1.5px solid #2a3a50; border-radius: 3px;
  flex-shrink: 0; margin-top: 2px; display: flex; align-items: center;
  justify-content: center; font-size: 9px; transition: background 0.1s;
}
.cb-box.checked { background: #2ecc71; border-color: #2ecc71; color: white; }
.cb-box.indeterminate { background: #0d2a1a; border-color: #2ecc71; color: #2ecc71; }

.ts-cb { color: #4d9fff; width: 38px; font-size: 10px; flex-shrink: 0; font-family: monospace; }
.line-text-cb { color: #3a5070; font-size: 11px; line-height: 1.5; }
.transcript-line-cb:has(.cb-box.checked) .line-text-cb { color: #b8d0f0; }

/* HIGHLIGHT MODE */
.transcript-line-hl {
  display: flex; align-items: flex-start; gap: 8px; padding: 6px 8px;
  border-radius: 5px; margin-bottom: 3px; cursor: pointer;
  border: 1px solid transparent; transition: background 0.1s; user-select: none;
}
.transcript-line-hl:hover { background: #0c1830; }
.transcript-line-hl.highlighted { background: #f39c1218; border-color: #f39c1240; }

.hl-bar {
  width: 4px; border-radius: 2px; background: transparent;
  flex-shrink: 0; align-self: stretch; min-height: 16px;
}
.transcript-line-hl.highlighted .hl-bar { background: #f39c12; }

.ts-hl { color: #4d9fff; width: 38px; font-size: 10px; flex-shrink: 0; padding-top: 1px; font-family: monospace; }
.line-text-hl { color: #3a5070; font-size: 11px; line-height: 1.5; }
.transcript-line-hl.highlighted .line-text-hl { color: #b8d0f0; }

/* WORKSPACE FOOTER */
#workspace-footer {
  background: #0c1524; border-top: 1px solid #1a2840;
  padding: 10px 16px; display: flex; gap: 10px; align-items: flex-end; flex-shrink: 0;
}
.footer-field { display: flex; flex-direction: column; gap: 4px; }
.footer-field-grow { flex: 1; }
.footer-label { font-size: 9px; color: #2a3a50; text-transform: uppercase; letter-spacing: 0.5px; }
.footer-input {
  background: #060a12; border: 1px solid #1a2840; border-radius: 4px;
  padding: 5px 8px; color: #b0c0d8; font-family: inherit; font-size: 11px;
}
.filename-input { width: 160px; font-family: monospace; }
.prompt-input { width: 100%; }
.btn-generate {
  background: #e94560; color: white; border: none; border-radius: 4px;
  padding: 7px 18px; font-size: 11px; font-weight: 600; cursor: pointer;
  font-family: inherit; white-space: nowrap;
}
.btn-generate:hover { background: #c73652; }

/* RESULT SCREEN */
.result-layout { display: flex; flex-direction: column; height: 100%; }
.video-wrap { flex: 1; background: #000; min-height: 0; }
.video-wrap video { width: 100%; height: 100%; object-fit: contain; display: block; }
.result-controls {
  background: #0c1524; border-top: 1px solid #1a2840;
  padding: 10px 16px; display: flex; justify-content: space-between; align-items: center;
  flex-shrink: 0;
}
.result-filename { color: #5090d0; font-weight: 600; font-size: 12px; }
.result-info { color: #2a3a50; font-size: 10px; margin-top: 2px; }
.result-actions { display: flex; gap: 8px; }

/* LIBRARY */
.library-toolbar {
  padding: 12px 18px; border-bottom: 1px solid #1a2840;
  background: #0c1524; flex-shrink: 0;
}
.library-count { font-size: 10px; color: #2a3a50; text-transform: uppercase; letter-spacing: 1px; }
#library-grid {
  flex: 1; overflow-y: auto; padding: 16px;
  display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px;
}
.reel-card {
  background: #0a0e1a; border: 1px solid #1a2840; border-radius: 8px;
  overflow: hidden; display: flex; flex-direction: column;
}
.reel-card:hover { border-color: #4d9fff44; }
.reel-thumb {
  background: #060a12; height: 90px; display: flex; align-items: center;
  justify-content: center; position: relative; cursor: pointer;
}
.reel-thumb:hover .reel-play-icon { opacity: 1; }
.reel-play-icon { font-size: 28px; color: #4d9fff; opacity: 0.7; transition: opacity 0.15s; }
.reel-duration {
  position: absolute; bottom: 5px; right: 7px;
  background: #000a; color: #b0c0d8; font-size: 8px; padding: 1px 5px; border-radius: 2px;
}
.reel-body { padding: 9px 11px; flex: 1; display: flex; flex-direction: column; gap: 3px; }
.reel-name { font-size: 11px; color: #b8d8ff; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.reel-meta { font-size: 9px; color: #2a3a50; }
.reel-prompt { font-size: 9px; color: #5090d0; font-style: italic; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px; }
.reel-actions { display: flex; gap: 4px; margin-top: 6px; }
.reel-btn {
  font-size: 9px; padding: 3px 7px; border-radius: 3px;
  border: 1px solid #1a2840; background: transparent; color: #3a5070;
  cursor: pointer; font-family: inherit;
}
.reel-btn:hover { background: #0c1524; color: #b0c0d8; }
.reel-btn.play { border-color: #1a5fb4; color: #4d9fff; }
.reel-btn.play:hover { background: #1a5fb414; }
.library-empty { grid-column: 1/-1; text-align: center; color: #2a3a50; padding: 60px; font-size: 14px; }

/* OVERLAY */
.overlay {
  position: fixed; inset: 0; background: #000b; display: flex;
  align-items: center; justify-content: center; z-index: 100;
}
.overlay.hidden { display: none; }
.overlay-card {
  background: #0c1524; border: 1px solid #1a2840; border-radius: 10px;
  padding: 16px; max-width: 800px; width: 90%; position: relative;
}
.overlay-close {
  position: absolute; top: 10px; right: 12px;
  background: transparent; border: none; color: #3a5070; font-size: 16px;
  cursor: pointer; font-family: inherit;
}
.overlay-close:hover { color: #b8d8ff; }

/* SHARED BUTTONS */
.btn-primary {
  background: #1a5fb4; color: white; border: none; border-radius: 4px;
  padding: 7px 16px; font-size: 11px; cursor: pointer; font-family: inherit; font-weight: 500;
}
.btn-primary:hover { background: #1e6fc8; }
.btn-secondary {
  background: transparent; border: 1px solid #1a2840; color: #3a5070;
  border-radius: 4px; padding: 7px 16px; font-size: 11px;
  cursor: pointer; font-family: inherit;
}
.btn-secondary:hover { background: #0c1524; color: #b0c0d8; }

.hidden { display: none !important; }
```

- [ ] **Step 2: Run existing tests — expect all pass**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: 98 passed (CSS has no backend tests)

- [ ] **Step 3: Spot-check visually**

Run `.\venv\Scripts\python.exe app.py` and open `http://localhost:5000`. Verify:
- Topbar is navy (`#0c1524`), logo is blue
- "✦ CREATE" tab is underlined in blue; "📼 LIBRARY" tab is dark and flat
- Folder picker shows split layout (gradient left panel + form right panel)
- Workspace sidebar is slightly wider, transcript text is sans-serif
- Timestamps are still monospace

- [ ] **Step 4: Commit**

```bash
git add static/style.css
git commit -m "feat: full CSS redesign — Pro Blue palette, underline tabs, system-ui font, wider sidebar"
```

---

## Task 4: Recent Folders Frontend

**Files:**
- Modify: `static/app.js`

No automated tests — verify visually.

- [ ] **Step 1: Add helper functions to `static/app.js`**

Add the following three functions immediately before the `// ─── Generate ───` comment block (around line 509):

```javascript
// ─── Recent folders ───────────────────────────────────────────────────────────
function relativeTime(iso) {
  const diffDays = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  if (diffDays === 0) return 'today';
  if (diffDays === 1) return 'yesterday';
  if (diffDays < 7) return `${diffDays} days ago`;
  const weeks = Math.floor(diffDays / 7);
  return weeks === 1 ? '1 week ago' : `${weeks} weeks ago`;
}

function renderRecentFolders(entries) {
  const section = $('recent-folders-section');
  const list = $('recent-folders-list');
  if (!entries || entries.length === 0) {
    section.classList.add('hidden');
    return;
  }
  list.innerHTML = '';
  entries.forEach(entry => {
    const li = document.createElement('li');
    li.className = 'recent-folder-item';
    const name = entry.path.replace(/\\/g, '/').split('/').filter(Boolean).pop() || entry.path;
    const count = entry.video_count;
    li.innerHTML =
      `<span class="recent-folder-name">📁 ${name}/</span>` +
      `<span class="recent-folder-meta">${count} video${count !== 1 ? 's' : ''} · ${relativeTime(entry.last_opened)}</span>`;
    li.addEventListener('click', () => {
      $('folder-path-input').value = entry.path;
      openFolder(entry.path);
    });
    list.appendChild(li);
  });
  section.classList.remove('hidden');
}

async function loadRecentFolders() {
  try {
    const resp = await fetch('/recent-folders');
    if (resp.ok) renderRecentFolders(await resp.json());
  } catch (_) {
    // recent folders is a convenience feature — fail silently
  }
}
```

- [ ] **Step 2: Call `loadRecentFolders()` at the bottom of `app.js`**

At the very end of `app.js` (after the `$('btn-close-player')` listener block), add:

```javascript
// Load recent folders on startup
loadRecentFolders();
```

- [ ] **Step 3: Run existing tests — expect all pass**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: 98 passed

- [ ] **Step 4: Verify recent folders UI end-to-end**

1. Run `.\venv\Scripts\python.exe app.py`
2. Open `http://localhost:5000` — folder picker shows, "Recent folders" section is hidden (no history yet)
3. Open a folder with videos
4. Click "New Reel" or refresh to return to the folder picker
5. "Recent folders" section now shows the folder with "1 video(s) · today"
6. Click the recent folder row — it should open the folder without typing

- [ ] **Step 5: Commit**

```bash
git add static/app.js
git commit -m "feat: recent folders UI — fetch, render, relative timestamps, click-to-open"
```

---

## Self-Review

**Spec coverage check:**

| Requirement | Task |
|-------------|------|
| Pro Blue colours | Task 3 |
| Underline nav tabs | Task 3 |
| system-ui body font | Task 3 |
| Monospace timestamps only | Task 3 |
| 11px transcript lines | Task 3 |
| Sidebar 210px | Task 3 |
| Split folder picker (left brand, right form) | Tasks 2 + 3 |
| Recent folders section in picker | Tasks 1 + 2 + 3 + 4 |
| GET /recent-folders endpoint | Task 1 |
| Save on load-folder | Task 1 |
| Dedup + max 5 | Task 1 |
| Relative time display | Task 4 |
| Click recent row opens folder | Task 4 |
| All other screens colour/font update | Task 3 |
| No functionality changes | All tasks (CSS/HTML/JS only except recent-folders) |

**Placeholder scan:** None found.

**Type consistency:** `_save_recent_folder(folder: str, video_count: int)` matches call site in `load_folder`. `renderRecentFolders(entries)` matches `loadRecentFolders` call. `relativeTime(iso)` matches usage in `renderRecentFolders`.
