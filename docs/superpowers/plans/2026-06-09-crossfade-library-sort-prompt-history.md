# Crossfade, Library Sort, Prompt History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fade-through-black transitions between clips and title cards, a sort dropdown to the library, and a prompt history/template dropdown to the analyze bar.

**Architecture:** Three independent features touching `video_editor.py`, `generator_app.py`, `app.py`, `templates/index.html`, `static/app.js`, and `static/style.css`. No new modules needed. No schema changes to existing JSON files.

**Tech Stack:** Python/Flask, ffmpeg (fade/afade filters), vanilla JS, CSS.

---

## Task 1: Fade-through-black transitions

**Spec:** `docs/superpowers/specs/2026-06-09-crossfade-library-sort-prompt-history-design.md` — Feature 1

**Files:**
- Modify: `video_editor.py` — add `fade_out_secs` param to `extract_clip`
- Modify: `generator_app.py` — add `fade_in_secs` param to `make_title_card`; update call sites in `_run_generation`
- Test: `tests/test_video_editor.py` (new file)

- [ ] **Step 1: Write failing test for extract_clip fade_out_secs**

Create `tests/test_video_editor.py`:

```python
import subprocess
from unittest.mock import patch, call
from video_editor import extract_clip


def _captured_cmd(mock_run):
    """Return the ffmpeg argv list from the first subprocess.run call."""
    return mock_run.call_args[0][0]


def test_extract_clip_no_fade_has_no_vf():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = type("R", (), {"returncode": 0})()
        extract_clip("input.mp4", 0.0, 10.0, "out.mp4")
    cmd = _captured_cmd(mock_run)
    assert "-vf" not in cmd


def test_extract_clip_fade_out_adds_vf_and_af():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = type("R", (), {"returncode": 0})()
        extract_clip("input.mp4", 0.0, 10.0, "out.mp4", fade_out_secs=2.0)
    cmd = _captured_cmd(mock_run)
    assert "-vf" in cmd
    vf_val = cmd[cmd.index("-vf") + 1]
    assert "fade=t=out" in vf_val
    assert "st=8.0" in vf_val   # 10.0 - 2.0
    assert "d=2.0" in vf_val
    assert "-af" in cmd
    af_val = cmd[cmd.index("-af") + 1]
    assert "afade=t=out" in af_val
    assert "st=8.0" in af_val
    assert "d=2.0" in af_val


def test_extract_clip_fade_clamped_for_short_clip():
    """Clip shorter than fade duration: fade_start clamped to 0."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = type("R", (), {"returncode": 0})()
        extract_clip("input.mp4", 5.0, 6.0, "out.mp4", fade_out_secs=2.0)
    cmd = _captured_cmd(mock_run)
    vf_val = cmd[cmd.index("-vf") + 1]
    # duration = 1.0, fade_start = max(0, 1.0-2.0) = 0.0
    assert "st=0.0" in vf_val
```

- [ ] **Step 2: Run tests to verify they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_video_editor.py -v
```

Expected: 3 failures — `fade_out_secs` param doesn't exist yet.

- [ ] **Step 3: Implement fade_out_secs in extract_clip**

Replace the `extract_clip` function in `video_editor.py`:

```python
def extract_clip(video_path: str, start_sec: float, end_sec: float, output_path: str, fade_out_secs: float = 0.0) -> None:
    # Re-encode (never stream-copy) so every clip starts on an I-frame.
    # -ss before -i: fast input seek. -t duration (not -to) is relative to the
    # seek point. -avoid_negative_ts make_zero zeroes each clip's timestamps so
    # the concat demuxer sees clean zero-based PTS on every clip — prevents AV drift.
    duration = end_sec - start_sec
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-i", video_path,
        "-t", str(duration),
        "-avoid_negative_ts", "make_zero",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-r", "30",
        "-c:a", "aac",
        "-ar", "48000",
        "-ac", "2",
    ]
    if fade_out_secs > 0.0:
        fade_start = max(0.0, duration - fade_out_secs)
        cmd += [
            "-vf", f"fade=t=out:st={fade_start}:d={fade_out_secs}",
            "-af", f"afade=t=out:st={fade_start}:d={fade_out_secs}",
        ]
    cmd.append(output_path)
    subprocess.run(cmd, check=True, capture_output=True)
```

- [ ] **Step 4: Run tests to verify they pass**

```
.\venv\Scripts\python.exe -m pytest tests/test_video_editor.py -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Write failing test for make_title_card fade_in_secs**

Add to `tests/test_video_editor.py` (append at the bottom — keep existing tests):

```python
import os
import tempfile
from unittest.mock import patch, MagicMock
from generator_app import make_title_card


def _title_card_cmd(mock_run):
    return mock_run.call_args[0][0]


def test_make_title_card_no_fade_no_afade():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        with tempfile.TemporaryDirectory() as tmp:
            make_title_card(["Test"], 1920, 1080, os.path.join(tmp, "card.mp4"))
    cmd = _title_card_cmd(mock_run)
    vf_val = cmd[cmd.index("-vf") + 1]
    assert "fade" not in vf_val
    assert "-af" not in cmd


def test_make_title_card_fade_in_appends_filter_and_afade():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        with tempfile.TemporaryDirectory() as tmp:
            make_title_card(["Test"], 1920, 1080, os.path.join(tmp, "card.mp4"), fade_in_secs=2.0)
    cmd = _title_card_cmd(mock_run)
    vf_val = cmd[cmd.index("-vf") + 1]
    assert "fade=t=in:st=0:d=2.0" in vf_val
    assert "-af" in cmd
    af_val = cmd[cmd.index("-af") + 1]
    assert "afade=t=in:st=0:d=2.0" in af_val
```

- [ ] **Step 6: Run new tests to verify they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_video_editor.py::test_make_title_card_no_fade_no_afade tests/test_video_editor.py::test_make_title_card_fade_in_appends_filter_and_afade -v
```

Expected: 2 failures — `fade_in_secs` param doesn't exist yet.

- [ ] **Step 7: Implement fade_in_secs in make_title_card**

In `generator_app.py`, change the function signature (line 188):

```python
def make_title_card(
    lines: list, width: int, height: int, output_path: str, duration: float = 5.0, fade_in_secs: float = 0.0
) -> None:
```

Then after building `filters` (after the for-loop that populates `filters`, before the `subprocess.run` call), add:

```python
    if fade_in_secs > 0.0:
        filters.append(f"fade=t=in:st=0:d={fade_in_secs}")
```

And in the `subprocess.run` call, after the `-t` arg and before the output path, add the `-af` flag when needed. The cleanest way: build the cmd list dynamically:

Replace the `result = subprocess.run(...)` block (lines 242–260) with:

```python
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=black:size={width}x{height}:rate=30",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-vf", ",".join(filters),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac",
        "-t", str(duration),
    ]
    if fade_in_secs > 0.0:
        cmd += ["-af", f"afade=t=in:st=0:d={fade_in_secs}"]
    cmd.append(Path(output_path).name)

    result = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        cwd=str(tmp_dir),
    )
    if result.returncode != 0:
        print(result.stderr.decode(errors="replace"), file=__import__("sys").stderr)
        result.check_returncode()
```

- [ ] **Step 8: Run all tests to verify they pass**

```
.\venv\Scripts\python.exe -m pytest tests/test_video_editor.py -v
```

Expected: all 5 PASSED.

- [ ] **Step 9: Update _run_generation call sites to pass fade values**

In `generator_app.py`, in `_run_generation`, find the title-card loop (around line 369):

```python
                make_title_card(
                    item["lines"], item["width"], item["height"], item["path"]
                )
```

Change to:

```python
                make_title_card(
                    item["lines"], item["width"], item["height"], item["path"],
                    fade_in_secs=2.0,
                )
```

Find the clip extraction executor.submit call (around line 392):

```python
                item["future"] = executor.submit(
                    extract_clip,
                    item["video_path"],
                    item["start_sec"],
                    item["end_sec"],
                    item["path"],
                )
```

Change to:

```python
                item["future"] = executor.submit(
                    extract_clip,
                    item["video_path"],
                    item["start_sec"],
                    item["end_sec"],
                    item["path"],
                    2.0,  # fade_out_secs
                )
```

- [ ] **Step 10: Run full test suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 11: Commit**

```
git add video_editor.py generator_app.py tests/test_video_editor.py
git commit -m "feat: add fade-through-black transitions between clips and title cards"
```

---

## Task 2: Library sort dropdown

**Spec:** `docs/superpowers/specs/2026-06-09-crossfade-library-sort-prompt-history-design.md` — Feature 2

**Files:**
- Modify: `templates/index.html` — add sort `<select>` inside `.library-toolbar`
- Modify: `static/app.js` — add `state.librarySort`, `state.libraryEntries`; refactor `loadLibrary` → `fetchLibrary` + `renderLibrary`; wire sort change event
- Modify: `static/style.css` — style the sort select

- [ ] **Step 1: Add sort dropdown HTML**

In `templates/index.html`, find the library toolbar (around line 176):

```html
    <div class="library-toolbar">
      <span id="library-count" class="library-count"></span>
    </div>
```

Replace with:

```html
    <div class="library-toolbar">
      <span id="library-count" class="library-count"></span>
      <select id="library-sort" class="library-sort-select">
        <option value="newest">Date: Newest</option>
        <option value="oldest">Date: Oldest</option>
        <option value="most-clips">Most clips</option>
        <option value="fewest-clips">Fewest clips</option>
      </select>
    </div>
```

- [ ] **Step 2: Add state fields and refactor library functions in app.js**

In `static/app.js`, find the `state` object (top of file). Add two fields after `librarySegmentStarts: []`:

```js
  librarySort: 'newest',
  libraryEntries: [],
```

Find the existing `loadLibrary` function:

```js
async function loadLibrary() {
  const resp = await fetch(GENERATOR_URL + '/library');
  const entries = await resp.json();
  renderLibrary(entries);
}
```

Replace it with:

```js
async function fetchLibrary() {
  const resp = await fetch(GENERATOR_URL + '/library');
  state.libraryEntries = await resp.json();
  renderLibrary();
}

function renderLibrary() {
  const entries = [...state.libraryEntries];
  const sort = state.librarySort;
  if (sort === 'newest') {
    entries.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
  } else if (sort === 'oldest') {
    entries.sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''));
  } else if (sort === 'most-clips') {
    entries.sort((a, b) => (b.clip_count || 0) - (a.clip_count || 0));
  } else if (sort === 'fewest-clips') {
    entries.sort((a, b) => (a.clip_count || 0) - (b.clip_count || 0));
  }

  const grid = $('library-grid');
  grid.innerHTML = '';
  $('library-count').textContent = `Generated Reels (${entries.length})`;

  if (entries.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'library-empty';
    empty.textContent = 'No reels generated yet.';
    grid.appendChild(empty);
    return;
  }

  entries.forEach(entry => {
    const card = document.createElement('div');
    card.className = 'reel-card';
    card.dataset.id = entry.id;

    const mins = Math.floor((entry.duration_seconds || 0) / 60);
    const secs = (entry.duration_seconds || 0) % 60;
    const durStr = `${mins}:${String(secs).padStart(2, '0')}`;
    const dateStr = entry.created_at ? entry.created_at.split('T')[0] : '';

    const thumb = document.createElement('div');
    thumb.className = 'reel-thumb';
    thumb.dataset.id = entry.id;
    thumb.innerHTML = `<div class="reel-play-icon">▶</div><div class="reel-duration">${durStr}</div>`;
    thumb.addEventListener('click', () => openLibraryPlayer(entry));

    const body = document.createElement('div');
    body.className = 'reel-body';
    _renderCardBody(body, card, entry, dateStr);

    card.appendChild(thumb);
    card.appendChild(body);
    grid.appendChild(card);
  });
}
```

- [ ] **Step 3: Replace loadLibrary call sites with fetchLibrary**

Search `app.js` for all calls to `loadLibrary()` and replace each with `fetchLibrary()`.

There will be at least two: one in the tab-switch handler (`if (tab === 'library') loadLibrary()`) and one in the delete cancel button (`cancelBtn.addEventListener('click', () => loadLibrary())`).

Search and replace globally:
- `loadLibrary()` → `fetchLibrary()`

Confirm no `loadLibrary` references remain.

- [ ] **Step 4: Wire sort change event**

In `app.js`, find where the library event listeners are (near `$('tab-library')` references, around line 62). Add after those:

```js
$('library-sort').addEventListener('change', e => {
  state.librarySort = e.target.value;
  renderLibrary();
});
```

- [ ] **Step 5: Fix the delete handler to use state.libraryEntries**

The existing delete handler (in `doDelete`) currently calls `fetchLibrary()` only on cancel. After a successful delete it manually removes the card and counts `.reel-card` elements. That's fine — but also remove the entry from `state.libraryEntries` so re-sorts stay consistent.

Find the `doDelete` function's success path:

```js
    card.classList.add('fading');
    setTimeout(() => {
      card.remove();
      const remaining = document.querySelectorAll('.reel-card').length;
      $('library-count').textContent = `Generated Reels (${remaining})`;
      if (remaining === 0) {
        const grid = $('library-grid');
        const empty = document.createElement('div');
        empty.className = 'library-empty';
        empty.textContent = 'No reels generated yet.';
        grid.appendChild(empty);
      }
    }, 300);
```

Replace with:

```js
    card.classList.add('fading');
    setTimeout(() => {
      state.libraryEntries = state.libraryEntries.filter(e => e.id !== entry.id);
      renderLibrary();
    }, 300);
```

- [ ] **Step 6: Add CSS for sort select**

In `static/style.css`, find `.library-toolbar` (around line 329) and append after its existing rules:

```css
.library-sort-select {
  background: #1e1e1e;
  color: #ccc;
  border: 1px solid #444;
  border-radius: 4px;
  padding: 3px 8px;
  font-size: 12px;
  cursor: pointer;
}
.library-sort-select:focus {
  outline: none;
  border-color: #888;
}
```

Also update `.library-toolbar` to lay out children in a row with space-between if it doesn't already. Find the existing rule and ensure it has:

```css
.library-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  gap: 12px;
}
```

(Check the existing rule first — only add what's missing.)

- [ ] **Step 7: Run full test suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all pass (no backend changes, tests unaffected).

- [ ] **Step 8: Commit**

```
git add templates/index.html static/app.js static/style.css
git commit -m "feat: add sort dropdown to library (newest/oldest/most-clips/fewest-clips)"
```

---

## Task 3: Prompt history + named templates

**Spec:** `docs/superpowers/specs/2026-06-09-crossfade-library-sort-prompt-history-design.md` — Feature 3

**Files:**
- Modify: `app.py` — add `_prompt_history_lock`, `PROMPT_HISTORY_PATH`, `_load_prompt_history`, `_save_prompt_history`, and two routes: `GET /prompt-history`, `POST /prompt-history`
- Modify: `templates/index.html` — wrap analyze-bar in `.prompt-wrap`; add history toggle button, history panel, save-template inline input
- Modify: `static/app.js` — load history on panel open, populate input on select, save on analyze, template CRUD
- Modify: `static/style.css` — styles for panel and inputs

### Backend

- [ ] **Step 1: Write failing backend tests**

Create `tests/test_prompt_history.py`:

```python
import json
import pytest
from app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "PROMPT_HISTORY_PATH", tmp_path / "prompt_history.json")
    flask_app = create_app(testing=True)
    with flask_app.test_client() as c:
        yield c


def test_get_prompt_history_empty(client):
    resp = client.get("/prompt-history")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data == {"recent": [], "templates": []}


def test_post_use_adds_to_recent(client):
    client.post("/prompt-history", json={"action": "use", "text": "best reactions"})
    resp = client.get("/prompt-history")
    data = resp.get_json()
    assert data["recent"] == ["best reactions"]


def test_post_use_deduplicates_and_moves_to_front(client):
    client.post("/prompt-history", json={"action": "use", "text": "first"})
    client.post("/prompt-history", json={"action": "use", "text": "second"})
    client.post("/prompt-history", json={"action": "use", "text": "first"})
    resp = client.get("/prompt-history")
    data = resp.get_json()
    assert data["recent"] == ["first", "second"]


def test_post_use_caps_at_ten(client):
    for i in range(12):
        client.post("/prompt-history", json={"action": "use", "text": f"prompt {i}"})
    resp = client.get("/prompt-history")
    data = resp.get_json()
    assert len(data["recent"]) == 10
    assert data["recent"][0] == "prompt 11"


def test_save_and_delete_template(client):
    client.post("/prompt-history", json={"action": "save_template", "name": "Reactions", "text": "best reactions"})
    resp = client.get("/prompt-history")
    data = resp.get_json()
    assert data["templates"] == [{"name": "Reactions", "text": "best reactions"}]

    client.post("/prompt-history", json={"action": "delete_template", "name": "Reactions"})
    resp = client.get("/prompt-history")
    data = resp.get_json()
    assert data["templates"] == []


def test_save_template_updates_existing_name(client):
    client.post("/prompt-history", json={"action": "save_template", "name": "Reactions", "text": "v1"})
    client.post("/prompt-history", json={"action": "save_template", "name": "Reactions", "text": "v2"})
    resp = client.get("/prompt-history")
    data = resp.get_json()
    assert len(data["templates"]) == 1
    assert data["templates"][0]["text"] == "v2"
```

- [ ] **Step 2: Run tests to verify they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_prompt_history.py -v
```

Expected: all fail — routes don't exist yet.

- [ ] **Step 3: Implement prompt history backend**

In `app.py`, after `RECENT_FOLDERS_PATH = ...` (line 38), add:

```python
PROMPT_HISTORY_PATH = Path(__file__).parent / "prompt_history.json"
```

After `_recent_folders_lock = threading.Lock()` (line 46), add:

```python
_prompt_history_lock = threading.Lock()
```

After the `_save_recent_folder` function (after line 180), add these three functions:

```python
def _load_prompt_history() -> dict:
    if not PROMPT_HISTORY_PATH.exists():
        return {"recent": [], "templates": []}
    try:
        with PROMPT_HISTORY_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"recent": [], "templates": []}


def _save_prompt_history(data: dict) -> None:
    try:
        with PROMPT_HISTORY_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def _prompt_history_use(text: str) -> None:
    with _prompt_history_lock:
        data = _load_prompt_history()
        recent = [t for t in data.get("recent", []) if t != text]
        recent.insert(0, text)
        data["recent"] = recent[:10]
        _save_prompt_history(data)
```

Inside `create_app()`, before `return app` (line 571), add two new routes:

```python
    @app.get("/prompt-history")
    def get_prompt_history():
        return jsonify(_load_prompt_history())

    @app.post("/prompt-history")
    def post_prompt_history():
        body = request.get_json() or {}
        action = body.get("action", "")
        text = body.get("text", "").strip()
        name = body.get("name", "").strip()
        if action == "use":
            if text:
                _prompt_history_use(text)
        elif action == "save_template":
            if name and text:
                with _prompt_history_lock:
                    data = _load_prompt_history()
                    templates = data.get("templates", [])
                    templates = [t for t in templates if t["name"] != name]
                    templates.append({"name": name, "text": text})
                    data["templates"] = templates
                    _save_prompt_history(data)
        elif action == "delete_template":
            if name:
                with _prompt_history_lock:
                    data = _load_prompt_history()
                    data["templates"] = [t for t in data.get("templates", []) if t["name"] != name]
                    _save_prompt_history(data)
        else:
            return jsonify({"error": "unknown action"}), 400
        return jsonify({"ok": True})
```

- [ ] **Step 4: Run tests to verify they pass**

```
.\venv\Scripts\python.exe -m pytest tests/test_prompt_history.py -v
```

Expected: all 6 PASSED.

- [ ] **Step 5: Auto-save prompt on analyze**

In `static/app.js`, in `runAnalyze()`, after `const prompt = $('analyze-input').value.trim();` (and the early return if empty), add a fire-and-forget save before the fetch:

```js
  fetch('/prompt-history', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'use', text: prompt }),
  });
```

This goes right before the `$('btn-analyze').textContent = 'Analyzing…'` line.

- [ ] **Step 6: Add HTML structure for history panel**

In `templates/index.html`, find the analyze-bar (around line 103):

```html
          <div id="analyze-bar">
            <textarea id="analyze-input" class="footer-input analyze-input"
                      placeholder="Describe what you're looking for…"></textarea>
            <button id="btn-analyze" class="btn-analyze">Analyze</button>
          </div>
```

Replace with:

```html
          <div id="analyze-bar">
            <div class="prompt-wrap">
              <textarea id="analyze-input" class="footer-input analyze-input"
                        placeholder="Describe what you're looking for…"></textarea>
              <button id="btn-history-toggle" class="btn-history-toggle" type="button" title="Prompt history">▾</button>
              <button id="btn-save-template" class="btn-save-template hidden" type="button" title="Save as template">★</button>
              <div id="prompt-history-panel" class="prompt-history-panel hidden">
                <div class="prompt-history-section" id="ph-recent-section">
                  <div class="prompt-history-heading">Recent</div>
                  <div id="ph-recent-list"></div>
                </div>
                <div class="prompt-history-section" id="ph-templates-section">
                  <div class="prompt-history-heading">
                    Templates
                    <span id="ph-template-save-area" class="ph-template-save-area hidden">
                      <input id="ph-template-name" class="ph-template-name-input" type="text" placeholder="Template name…" maxlength="60" />
                      <button id="ph-template-save-btn" class="ph-template-save-btn" type="button">Save</button>
                    </span>
                  </div>
                  <div id="ph-templates-list"></div>
                </div>
              </div>
            </div>
            <button id="btn-analyze" class="btn-analyze">Analyze</button>
          </div>
```

- [ ] **Step 7: Implement prompt history JS**

In `static/app.js`, add the following block after the library section (after the `closeLibraryPlayer` function, or at the end of the file before the last closing lines):

```js
// ─── Prompt History ───────────────────────────────────────────────────────────

let _phOutsideClickHandler = null;

function _closePHPanel() {
  $('prompt-history-panel').classList.add('hidden');
  $('ph-template-save-area').classList.add('hidden');
  if (_phOutsideClickHandler) {
    document.removeEventListener('click', _phOutsideClickHandler);
    _phOutsideClickHandler = null;
  }
}

async function _openPHPanel() {
  const panel = $('prompt-history-panel');
  panel.classList.remove('hidden');

  const data = await fetch('/prompt-history').then(r => r.json()).catch(() => ({ recent: [], templates: [] }));
  _renderPHRecent(data.recent);
  _renderPHTemplates(data.templates);

  // Close on outside click
  setTimeout(() => {
    _phOutsideClickHandler = (e) => {
      if (!panel.contains(e.target) && e.target !== $('btn-history-toggle')) {
        _closePHPanel();
      }
    };
    document.addEventListener('click', _phOutsideClickHandler);
  }, 0);
}

function _renderPHRecent(items) {
  const list = $('ph-recent-list');
  list.innerHTML = '';
  if (!items.length) {
    list.innerHTML = '<div class="ph-empty">No history yet</div>';
    return;
  }
  items.forEach(text => {
    const item = document.createElement('div');
    item.className = 'ph-item';
    item.textContent = text;
    item.addEventListener('click', () => {
      $('analyze-input').value = text;
      _closePHPanel();
      _updateSaveTemplateBtn();
    });
    list.appendChild(item);
  });
}

function _renderPHTemplates(templates) {
  const list = $('ph-templates-list');
  list.innerHTML = '';
  if (!templates.length) {
    list.innerHTML = '<div class="ph-empty">No templates saved</div>';
    return;
  }
  templates.forEach(tpl => {
    const row = document.createElement('div');
    row.className = 'ph-item ph-template-row';

    const label = document.createElement('span');
    label.className = 'ph-template-label';
    label.textContent = tpl.name;
    label.addEventListener('click', () => {
      $('analyze-input').value = tpl.text;
      _closePHPanel();
      _updateSaveTemplateBtn();
    });

    const del = document.createElement('button');
    del.className = 'ph-delete-btn';
    del.type = 'button';
    del.textContent = '×';
    del.title = 'Delete template';
    del.addEventListener('click', async (e) => {
      e.stopPropagation();
      await fetch('/prompt-history', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'delete_template', name: tpl.name }),
      });
      row.remove();
      if (!$('ph-templates-list').children.length) {
        $('ph-templates-list').innerHTML = '<div class="ph-empty">No templates saved</div>';
      }
    });

    row.appendChild(label);
    row.appendChild(del);
    list.appendChild(row);
  });
}

function _updateSaveTemplateBtn() {
  const val = $('analyze-input').value.trim();
  $('btn-save-template').classList.toggle('hidden', !val);
}

$('btn-history-toggle').addEventListener('click', (e) => {
  e.stopPropagation();
  const panel = $('prompt-history-panel');
  if (panel.classList.contains('hidden')) {
    _openPHPanel();
  } else {
    _closePHPanel();
  }
});

$('analyze-input').addEventListener('input', _updateSaveTemplateBtn);

$('btn-save-template').addEventListener('click', (e) => {
  e.stopPropagation();
  const saveArea = $('ph-template-save-area');
  if (saveArea.classList.contains('hidden')) {
    // Make sure panel is open
    if ($('prompt-history-panel').classList.contains('hidden')) {
      _openPHPanel();
    }
    saveArea.classList.remove('hidden');
    $('ph-template-name').value = '';
    $('ph-template-name').focus();
  }
});

async function _doSaveTemplate() {
  const name = $('ph-template-name').value.trim();
  const text = $('analyze-input').value.trim();
  if (!name || !text) return;
  await fetch('/prompt-history', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'save_template', name, text }),
  });
  $('ph-template-save-area').classList.add('hidden');
  // Refresh templates list
  const data = await fetch('/prompt-history').then(r => r.json()).catch(() => ({ recent: [], templates: [] }));
  _renderPHTemplates(data.templates);
}

$('ph-template-save-btn').addEventListener('click', _doSaveTemplate);
$('ph-template-name').addEventListener('keydown', e => {
  if (e.key === 'Enter') _doSaveTemplate();
  if (e.key === 'Escape') $('ph-template-save-area').classList.add('hidden');
});
```

- [ ] **Step 8: Add CSS for prompt history**

In `static/style.css`, append at the end of the file:

```css
/* ── Prompt history ─────────────────────────────────────────────────────────── */
.prompt-wrap {
  position: relative;
  flex: 1;
  display: flex;
  align-items: flex-start;
  gap: 4px;
}

.btn-history-toggle {
  background: #2a2a2a;
  color: #aaa;
  border: 1px solid #444;
  border-radius: 4px;
  padding: 4px 7px;
  cursor: pointer;
  font-size: 13px;
  line-height: 1;
  flex-shrink: 0;
  margin-top: 2px;
}
.btn-history-toggle:hover { background: #333; color: #fff; }

.btn-save-template {
  background: #2a2a2a;
  color: #f0b429;
  border: 1px solid #444;
  border-radius: 4px;
  padding: 4px 7px;
  cursor: pointer;
  font-size: 13px;
  line-height: 1;
  flex-shrink: 0;
  margin-top: 2px;
}
.btn-save-template:hover { background: #333; }

.prompt-history-panel {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  background: #1e1e1e;
  border: 1px solid #444;
  border-radius: 6px;
  z-index: 100;
  max-height: 320px;
  overflow-y: auto;
  box-shadow: 0 4px 16px rgba(0,0,0,0.5);
  margin-top: 4px;
}

.prompt-history-section { padding: 6px 0; }
.prompt-history-section + .prompt-history-section { border-top: 1px solid #333; }

.prompt-history-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 12px;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #666;
}

.ph-item {
  padding: 6px 12px;
  cursor: pointer;
  font-size: 12px;
  color: #ccc;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ph-item:hover { background: #2a2a2a; color: #fff; }

.ph-template-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
  padding: 4px 12px;
}
.ph-template-label {
  flex: 1;
  cursor: pointer;
  font-size: 12px;
  color: #ccc;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ph-template-label:hover { color: #fff; }

.ph-delete-btn {
  background: none;
  border: none;
  color: #666;
  cursor: pointer;
  font-size: 14px;
  padding: 0 2px;
  line-height: 1;
  flex-shrink: 0;
}
.ph-delete-btn:hover { color: #e55; }

.ph-empty {
  padding: 4px 12px;
  font-size: 11px;
  color: #555;
  font-style: italic;
}

.ph-template-save-area {
  display: flex;
  align-items: center;
  gap: 4px;
}

.ph-template-name-input {
  background: #2a2a2a;
  color: #ccc;
  border: 1px solid #555;
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 11px;
  width: 140px;
}
.ph-template-name-input:focus { outline: none; border-color: #888; }

.ph-template-save-btn {
  background: #2a6a3a;
  color: #fff;
  border: none;
  border-radius: 4px;
  padding: 2px 8px;
  font-size: 11px;
  cursor: pointer;
}
.ph-template-save-btn:hover { background: #2e7d45; }
```

- [ ] **Step 9: Run full test suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 10: Commit**

```
git add app.py templates/index.html static/app.js static/style.css tests/test_prompt_history.py
git commit -m "feat: add prompt history and named templates dropdown to analyze bar"
```

---

## Final verification

- [ ] Start the app and open it in the browser:

```
.\venv\Scripts\python.exe -c "from app import create_app; create_app().run(debug=True)"
```

- [ ] Open a folder with videos, run Analyze — verify the prompt is saved to history
- [ ] Open the `▾` dropdown — verify recent prompt appears; click it to repopulate input
- [ ] Save a template via `★`, verify it appears in Templates section; delete it
- [ ] Switch to Library tab — verify sort dropdown appears; change to "Oldest" and "Fewest clips" and verify order changes
- [ ] Generate a reel and verify the fade-through-black transition appears between each clip and the following title card
