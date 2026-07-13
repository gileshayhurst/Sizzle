# Download / Show / Title Card Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three bugs: auto-save generated reels to a user-chosen local folder (cloud mode), redesign the library Show button with a "not downloaded" modal, and fix title card text being clipped when filenames are long.

**Architecture:** Task 1 fixes a pure Python bug in `generator_app.py`. Tasks 2–3 add a server endpoint and a new result field in Python. Task 4 adds HTML markup. Tasks 5–6 wire the JS. Each task builds on the previous — run them in order.

**Tech Stack:** Python/Flask (generator_app.py), vanilla JS (static/app.js), IndexedDB (File System Access API persistence), HTML/CSS.

---

## Task order and dependencies

```
Task 1 (title card)  ─┐
Task 2 (find-folder) ─┤─ independent, run first
Task 3 (entry_id)    ─┤
Task 4 (HTML)        ─┘
        │
Task 5 (JS auto-save) ← needs Tasks 3 + 4
        │
Task 6 (JS Show btn)  ← needs Tasks 2 + 5
```

---

## Task 1: Fix title card text cutoff

**Files:**
- Modify: `generator_app.py` — `make_title_card` function (~line 198)
- Test: `tests/test_generator_app.py`

The bug: `drawtext` uses `x=(w-text_w)/2`. When a filename is wider than the frame, `text_w > w`, x goes negative, and the left edge of the text is clipped off-screen. Fix: reduce font size proportionally before building the filter, then add a floor clamp to the x expression.

- [ ] **Step 1: Write failing test for font size reduction**

Add to `tests/test_generator_app.py`:

```python
def test_make_title_card_reduces_fontsize_for_long_line(tmp_path):
    """Font size must be reduced when a line would overflow the frame width."""
    from generator_app import make_title_card
    import re
    out = str(tmp_path / "card.mp4")
    # 50 chars at default fontsize (72 for 1080p) → 50*72*0.55=1980 > 1920-80=1840
    long_line = "A" * 50
    with patch("generator_app.subprocess.run") as mock_run, \
         patch("generator_app._find_system_font", return_value=None):
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card([long_line], 1920, 1080, out)
    vf_value = mock_run.call_args[0][0][mock_run.call_args[0][0].index("-vf") + 1]
    match = re.search(r"fontsize=(\d+)", vf_value)
    assert match, "fontsize not found in drawtext filter"
    assert int(match.group(1)) < 72, "Expected font size to be reduced below default 72"


def test_make_title_card_x_expression_uses_max_clamp(tmp_path):
    """The drawtext x expression must clamp to prevent text starting off-screen."""
    from generator_app import make_title_card
    out = str(tmp_path / "card.mp4")
    with patch("generator_app.subprocess.run") as mock_run, \
         patch("generator_app._find_system_font", return_value=None):
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card(["Hello"], 1920, 1080, out)
    vf_value = mock_run.call_args[0][0][mock_run.call_args[0][0].index("-vf") + 1]
    assert "max(20,(w-text_w)/2)" in vf_value, \
        f"Expected max(20,...) clamp in x expression, got: {vf_value}"
```

- [ ] **Step 2: Run tests — confirm they fail**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py::test_make_title_card_reduces_fontsize_for_long_line tests/test_generator_app.py::test_make_title_card_x_expression_uses_max_clamp -v
```

Expected: both FAIL.

- [ ] **Step 3: Implement the fix in `generator_app.py`**

In `make_title_card`, find the line `fontsize = max(24, height // 15)` (~line 210) and replace the entire block down through the `filters.append(...)` loop with:

```python
    fontsize = max(24, height // 15)

    # Reduce font size if the longest line would overflow the frame.
    # Rough estimate: Arial glyph width ≈ 0.55× fontsize.
    max_chars = max(len(line) for line in lines)
    usable_width = width - 80
    while fontsize > 16 and max_chars * fontsize * 0.55 > usable_width:
        fontsize = int(fontsize * 0.9)

    tmp_dir = Path(output_path).parent
    prefix = Path(output_path).stem  # unique per clip, e.g. "clip_0000"

    # ── Font: copy into tmp_dir so we can reference it by filename only ──────
    font_src = _find_system_font()
    if font_src:
        font_name = Path(font_src).name          # e.g. "arial.ttf"
        font_dest = tmp_dir / font_name
        if not font_dest.exists():
            shutil.copy(font_src, font_dest)
        fontfile_arg = f"fontfile={font_name}:"  # relative — no colon in path
    else:
        fontfile_arg = ""

    # ── Text files: write each line to its own file so the filter string ─────
    # ── contains no user content at all (avoids all escaping issues).     ─────
    # drawtext still expands % format specifiers even from textfile, so double
    # any literal percent signs in the text.
    text_filenames = []
    for i, line in enumerate(lines):
        tf = tmp_dir / f"{prefix}_t{i}.txt"
        tf.write_text(line.replace("%", "%%"), encoding="utf-8")
        text_filenames.append(tf.name)  # relative filename only

    # ── Build filter ──────────────────────────────────────────────────────────
    line_height = int(fontsize * 1.2)
    spacing = 8
    n = len(lines)
    total_h = n * line_height + (n - 1) * spacing

    filters = []
    for i, tf_name in enumerate(text_filenames):
        if n == 1:
            y_expr = "(h-text_h)/2"
        else:
            y_off = i * (line_height + spacing)
            y_expr = f"(h-{total_h})/2+{y_off}"
        filters.append(
            f"drawtext={fontfile_arg}textfile={tf_name}"
            f":fontcolor=white:fontsize={fontsize}:x=max(20,(w-text_w)/2):y={y_expr}"
        )
```

Note: the only changes from the original are (a) the font-size reduction while-loop after line 210, and (b) `x=(w-text_w)/2` → `x=max(20,(w-text_w)/2)` in the `filters.append(...)` call.

- [ ] **Step 4: Run tests — confirm they pass**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py::test_make_title_card_reduces_fontsize_for_long_line tests/test_generator_app.py::test_make_title_card_x_expression_uses_max_clamp -v
```

Expected: both PASS.

- [ ] **Step 5: Run full test suite to check for regressions**

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```powershell
git add generator_app.py tests/test_generator_app.py
git commit -m "fix: clamp title card x position and reduce fontsize for long lines"
```

---

## Task 2: Add `POST /find-local-folder` endpoint to generator service

**Files:**
- Modify: `generator_app.py` — add endpoint just before `return app` (~line 812)
- Test: `tests/test_generator_app.py`

This endpoint scans common Windows user directories for a probe file the browser writes. The browser can't expose a directory handle's OS path, but Python can find the file by name+content. The generator service runs on the same machine as the browser, so the scan works.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_generator_app.py`:

```python
def test_find_local_folder_locates_probe_file(tmp_path, client):
    """Endpoint finds the probe file and returns the directory path."""
    folder = tmp_path / "Downloads" / "MyVideos"
    folder.mkdir(parents=True)
    probe = folder / "sizzle_probe_abc123.tmp"
    probe.write_text("abc123", encoding="utf-8")

    with patch("generator_app.Path.home", return_value=tmp_path):
        resp = client.post("/find-local-folder", json={
            "probe_name": "sizzle_probe_abc123.tmp",
            "probe_content": "abc123",
        })

    assert resp.status_code == 200
    assert resp.get_json()["path"] == str(folder)


def test_find_local_folder_returns_null_when_not_found(tmp_path, client):
    """Returns {"path": null} when no matching probe file exists."""
    with patch("generator_app.Path.home", return_value=tmp_path):
        resp = client.post("/find-local-folder", json={
            "probe_name": "sizzle_probe_missing.tmp",
            "probe_content": "nothing",
        })

    assert resp.status_code == 200
    assert resp.get_json()["path"] is None


def test_find_local_folder_returns_null_on_empty_params(client):
    """Missing probe params → {"path": null}, no crash."""
    resp = client.post("/find-local-folder", json={})
    assert resp.status_code == 200
    assert resp.get_json()["path"] is None
```

- [ ] **Step 2: Run tests — confirm they fail**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py::test_find_local_folder_locates_probe_file tests/test_generator_app.py::test_find_local_folder_returns_null_when_not_found tests/test_generator_app.py::test_find_local_folder_returns_null_on_empty_params -v
```

Expected: all three FAIL (endpoint doesn't exist yet).

- [ ] **Step 3: Add the endpoint to `generator_app.py`**

Insert immediately before the `return app` line at the bottom of `create_app()` (currently ~line 833):

```python
    @app.post("/find-local-folder")
    def find_local_folder():
        body = request.get_json() or {}
        probe_name = body.get("probe_name", "").strip()
        probe_content = body.get("probe_content", "").strip()
        if not probe_name or not probe_content:
            return jsonify({"path": None})

        home = Path.home()
        search_roots = ["Downloads", "Videos", "Documents", "Desktop", "Pictures"]

        for root_name in search_roots:
            root = home / root_name
            if not root.exists():
                continue
            # Collect folders up to depth 2 under this root
            dirs_to_check = [root]
            try:
                for item in root.iterdir():
                    if item.is_dir():
                        dirs_to_check.append(item)
                        try:
                            for subitem in item.iterdir():
                                if subitem.is_dir():
                                    dirs_to_check.append(subitem)
                        except (PermissionError, OSError):
                            pass
            except (PermissionError, OSError):
                pass

            for folder_path in dirs_to_check:
                probe_path = folder_path / probe_name
                try:
                    if probe_path.is_file() and probe_path.read_text(encoding="utf-8").strip() == probe_content:
                        return jsonify({"path": str(folder_path)})
                except (PermissionError, OSError):
                    continue

        return jsonify({"path": None})

```

- [ ] **Step 4: Run tests — confirm they pass**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py::test_find_local_folder_locates_probe_file tests/test_generator_app.py::test_find_local_folder_returns_null_when_not_found tests/test_generator_app.py::test_find_local_folder_returns_null_on_empty_params -v
```

Expected: all three PASS.

- [ ] **Step 5: Run full suite**

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```powershell
git add generator_app.py tests/test_generator_app.py
git commit -m "feat: add /find-local-folder endpoint for OS path detection via probe file"
```

---

## Task 3: Include `entry_id` in the generation result

**Files:**
- Modify: `generator_app.py` — `_run_generation` function (~line 565)
- Test: `tests/test_generator_app.py`

The WS `done` message sends `job["result"]` to the browser. The browser needs `entry_id` to key the `sizzle_downloads` localStorage record and link result-screen downloads to library entries.

- [ ] **Step 1: Write failing test**

Add to `tests/test_generator_app.py`:

```python
def test_generation_result_includes_entry_id(tmp_path, client):
    """The job result sent over WS must include entry_id matching the library entry."""
    video = tmp_path / "clip.mp4"
    video.touch()
    txt = tmp_path / "clip.txt"
    txt.write_text("[0:00] Speaker: Hello world\n", encoding="utf-8")

    captured_entry = {}

    def fake_add(entry):
        captured_entry.update(entry)

    with patch("generator_app._library_add", side_effect=fake_add), \
         patch("generator_app.make_title_card"), \
         patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "prompt": "test",
            "output_filename": "out.mp4",
            "selections": {"clip.mp4": ["[0:00] Speaker: Hello world"]},
        })
        assert resp.status_code == 200
        job_id = resp.get_json()["job_id"]

    from generator_app import _jobs
    result = _jobs[job_id]["result"]
    assert "entry_id" in result, "result must contain entry_id"
    assert result["entry_id"] == captured_entry["id"]
```

- [ ] **Step 2: Run test — confirm it fails**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py::test_generation_result_includes_entry_id -v
```

Expected: FAIL (`KeyError: 'entry_id'` or `AssertionError`).

- [ ] **Step 3: Patch `_run_generation` in `generator_app.py`**

Find these lines near the end of `_run_generation` (~line 565):

```python
    _library_add(library_entry)

    with _jobs_lock:
        job["status"] = "done"
```

Replace with:

```python
    _library_add(library_entry)

    with _jobs_lock:
        job["result"]["entry_id"] = library_entry["id"]
        job["status"] = "done"
```

- [ ] **Step 4: Run test — confirm it passes**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py::test_generation_result_includes_entry_id -v
```

Expected: PASS.

- [ ] **Step 5: Run full suite**

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```powershell
git add generator_app.py tests/test_generator_app.py
git commit -m "feat: include entry_id in generation result for client-side download tracking"
```

---

## Task 4: HTML markup for output folder buttons and "not downloaded" modal

**Files:**
- Modify: `templates/index.html`

Add three pieces of markup. No JS yet — just the DOM nodes the next two tasks wire up.

- [ ] **Step 1: Add "Set output folder" button to the result screen**

In `templates/index.html`, find the result-actions div (around line 185). It currently ends with:

```html
            <button id="btn-open-folder" class="btn-secondary">📂 Open Folder</button>
          </div>
```

Add one line after `btn-open-folder`:

```html
            <button id="btn-open-folder" class="btn-secondary">📂 Open Folder</button>
            <button id="btn-set-output-folder" class="btn-secondary hidden">📁 Set output folder</button>
          </div>
```

- [ ] **Step 2: Add "Set output folder" button to the library toolbar**

Find the library-toolbar div (~line 201):

```html
    <div class="library-toolbar">
      <span id="library-count" class="library-count"></span>
      <select id="library-sort" class="library-sort-select">
```

Insert a button between the count and the sort select:

```html
    <div class="library-toolbar">
      <span id="library-count" class="library-count"></span>
      <button id="btn-lib-set-output-folder" class="btn-secondary hidden">📁 Set output folder</button>
      <select id="library-sort" class="library-sort-select">
```

- [ ] **Step 3: Add the "not downloaded" modal overlay**

Find the closing `</main>` of the library tab (~line 226):

```html
  </main>

</div>
<script src="/static/app.js"></script>
```

Insert the modal before `</div>`:

```html
  </main>

  <!-- "Not downloaded" modal — shown by Show button in cloud mode -->
  <div id="not-downloaded-modal" class="overlay hidden">
    <div class="overlay-card" style="max-width:440px">
      <button id="btn-not-downloaded-close" class="overlay-close">✕</button>
      <div style="font-size:14px;font-weight:600;color:#b8d8ff;margin-bottom:10px">
        You have not downloaded this file
      </div>
      <div id="not-downloaded-body" style="color:#7090a8;margin-bottom:18px;font-size:12px"></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button id="btn-not-downloaded-save" class="btn-primary"></button>
        <button id="btn-not-downloaded-view" class="btn-secondary">View in Browser</button>
      </div>
    </div>
  </div>

</div>
<script src="/static/app.js"></script>
```

- [ ] **Step 4: Verify HTML is valid — start the app and load the page**

```powershell
.\venv\Scripts\python.exe -c "from app import create_app; create_app().run(debug=True)"
```

Open `http://localhost:5000` in a browser. Check:
- No console errors on load
- The page renders (result screen, library tab accessible)
- The new buttons are not visible (they're `hidden` by default)
- The modal is not visible

- [ ] **Step 5: Commit**

```powershell
git add templates/index.html
git commit -m "feat: add output folder picker buttons and not-downloaded modal markup"
```

---

## Task 5: JS — IndexedDB helpers, output folder picker, and auto-save on generation complete

**Files:**
- Modify: `static/app.js`

This task adds all JS infrastructure for the cloud-mode auto-save feature:
1. IndexedDB helpers (save/load a `FileSystemDirectoryHandle`)
2. localStorage helpers for the `sizzle_downloads` record
3. Output folder picker + UI update
4. `_saveToOutputFolder` — shared function that writes a blob and probes for OS path
5. `_autoSaveReelResult` — called after generation completes
6. Wiring the `btn-set-output-folder` / `btn-lib-set-output-folder` buttons
7. Updating `showResult()` and the `btn-open-folder` click handler for cloud mode

- [ ] **Step 1: Add IndexedDB + localStorage helpers at the top of `static/app.js`**

Insert the following block immediately after the `const state = { ... }` declaration (after the closing `};` of state, before the first function definition):

```js
// ─── IndexedDB helpers (persist FileSystemDirectoryHandle across reloads) ────
function _idbOpen() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('sizzle', 1);
    req.onupgradeneeded = () => req.result.createObjectStore('kv');
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
async function _idbSave(key, value) {
  const db = await _idbOpen();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('kv', 'readwrite');
    tx.objectStore('kv').put(value, key);
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}
async function _idbLoad(key) {
  const db = await _idbOpen();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('kv', 'readonly');
    const req = tx.objectStore('kv').get(key);
    req.onsuccess = () => resolve(req.result ?? null);
    req.onerror = () => reject(req.error);
  });
}

// ─── Downloads record in localStorage ────────────────────────────────────────
// sizzle_downloads: { [entryId]: { folderName, filename, localFolderPath|null } }
function _getDownloads() {
  try { return JSON.parse(localStorage.getItem('sizzle_downloads') || '{}'); }
  catch { return {}; }
}
function _getDownload(entryId) { return _getDownloads()[entryId] || null; }
function _setDownload(entryId, info) {
  const d = _getDownloads();
  d[entryId] = info;
  localStorage.setItem('sizzle_downloads', JSON.stringify(d));
}

// ─── Output folder name display ───────────────────────────────────────────────
function _updateOutputFolderUI() {
  const name = localStorage.getItem('sizzle_output_folder_name');
  const label = name ? `📁 ${name}` : '📁 Set output folder';
  const r = $('btn-set-output-folder');
  const l = $('btn-lib-set-output-folder');
  if (r) r.textContent = label;
  if (l) l.textContent = label;
}

// ─── Output folder picker ─────────────────────────────────────────────────────
async function _pickOutputFolder() {
  let handle;
  try {
    handle = await window.showDirectoryPicker({ mode: 'readwrite' });
  } catch (e) {
    if (e.name === 'AbortError') return null; // user cancelled
    throw e;
  }
  await _idbSave('sizzle_output_dir_handle', handle);
  localStorage.setItem('sizzle_output_folder_name', handle.name);
  _updateOutputFolderUI();
  return handle;
}

// ─── Core save: write blob to dir handle, probe for OS path ──────────────────
async function _saveToOutputFolder(handle, filename, blob, entryId) {
  // Write video file
  const fh = await handle.getFileHandle(filename, { create: true });
  const writable = await fh.createWritable();
  await writable.write(blob);
  await writable.close();

  // Write probe file, ask server to locate it on disk
  const probeUuid = crypto.randomUUID();
  const probeName = `sizzle_probe_${probeUuid}.tmp`;
  let localFolderPath = null;
  try {
    const probeFh = await handle.getFileHandle(probeName, { create: true });
    const probeW = await probeFh.createWritable();
    await probeW.write(probeUuid);
    await probeW.close();

    const scanResp = await fetch(`${GENERATOR_URL}/find-local-folder`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ probe_name: probeName, probe_content: probeUuid }),
    });
    if (scanResp.ok) localFolderPath = (await scanResp.json()).path || null;
  } catch { /* probe scan is best-effort */ }

  try { await handle.removeEntry(probeName); } catch { /* best-effort */ }

  _setDownload(entryId, { folderName: handle.name, filename, localFolderPath });
  return { folderName: handle.name, localFolderPath };
}

// ─── Auto-save the just-generated reel to the output folder ──────────────────
async function _autoSaveReelResult(jobId, filename, entryId) {
  const handle = await _idbLoad('sizzle_output_dir_handle');
  if (!handle) return null;

  const perm = await handle.queryPermission({ mode: 'readwrite' });
  if (perm !== 'granted') await handle.requestPermission({ mode: 'readwrite' });

  const resp = await fetch(`${GENERATOR_URL}/video/${jobId}`);
  if (!resp.ok) throw new Error(`Video fetch failed: ${resp.status}`);
  const blob = await resp.blob();

  return _saveToOutputFolder(handle, filename, blob, entryId);
}
```

- [ ] **Step 2: Wire output folder picker buttons at bottom of `initCloudMode()`**

In `static/app.js`, find the `initCloudMode()` IIFE (the `(function initCloudMode() { ... })()` block). At the very end, just before the closing `})()`, add:

```js
  // Show output-folder buttons (cloud-only) and init their labels
  const setFolderBtns = [$('btn-set-output-folder'), $('btn-lib-set-output-folder')];
  setFolderBtns.forEach(btn => {
    if (!btn) return;
    btn.classList.remove('hidden');
    btn.addEventListener('click', _pickOutputFolder);
  });
  _updateOutputFolderUI();
```

- [ ] **Step 3: Trigger auto-save after generation completes**

In `static/app.js`, find the WebSocket `done` handler (~line 835):

```js
      } else if (msg.type === 'done') {
        _genWs = null;
        if (msg.status === 'done') {
          $('gen-bar').style.width = '100%';
          state.resultJobId = jobId;
          _clearSelections();
          showResult(msg.result);
```

After `showResult(msg.result);`, add:

```js
          if (APP_MODE === 'cloud' && msg.result.entry_id) {
            const openBtn = $('btn-open-folder');
            openBtn.textContent = '⬇ Saving…';
            openBtn.disabled = true;
            _autoSaveReelResult(jobId, msg.result.filename, msg.result.entry_id)
              .then(saved => {
                openBtn.disabled = false;
                if (saved) {
                  openBtn.textContent = `✓ Saved to ${saved.folderName}`;
                  openBtn.dataset.savedPath = saved.localFolderPath || '';
                  openBtn.dataset.savedFilename = msg.result.filename;
                } else {
                  // No output folder set — revert to original cloud label
                  openBtn.textContent = '⬇ Download';
                }
              })
              .catch(() => {
                openBtn.disabled = false;
                openBtn.textContent = '⬇ Download';
              });
          }
```

- [ ] **Step 4: Update `btn-open-folder` click handler for cloud mode**

Find the `$('btn-open-folder').addEventListener('click', async () => {` block (~line 954). Replace the entire cloud branch:

```js
$('btn-open-folder').addEventListener('click', async () => {
  if (APP_MODE === 'cloud') {
    const localPath = $('btn-open-folder').dataset.savedPath;
    const filename = $('btn-open-folder').dataset.savedFilename;
    if (localPath && filename) {
      await fetch(GENERATOR_URL + '/open-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder: localPath, file_path: localPath + '\\' + filename }),
      });
      return;
    }
    // No OS path — try reading from dir handle as blob URL
    if (filename) {
      const handle = await _idbLoad('sizzle_output_dir_handle').catch(() => null);
      if (handle) {
        try {
          const fh = await handle.getFileHandle(filename);
          window.open(URL.createObjectURL(await fh.getFile()), '_blank');
          return;
        } catch { /* fall through */ }
      }
    }
    // Final fallback: presigned URL
    if (state.resultDownloadUrl) window.open(state.resultDownloadUrl, '_blank');
    return;
  }
  // Local mode (unchanged)
  await fetch(GENERATOR_URL + '/open-folder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folder: state.folder, file_path: state.resultPath }),
  });
});
```

- [ ] **Step 5: Manual verification**

Start both services:
```powershell
# Terminal 1
.\venv\Scripts\python.exe -c "from app import create_app; create_app().run(debug=True)"
# Terminal 2
.\venv\Scripts\python.exe -c "from generator_app import create_app; create_app().run(debug=True, port=5001)"
```

In a browser (with `APP_MODE=cloud` if testing cloud path, otherwise skip):
1. Click "📁 Set output folder" — a native folder picker should appear
2. Pick a folder — button label should update to `📁 [chosen folder name]`
3. The label persists after page reload (IndexedDB)

- [ ] **Step 6: Commit**

```powershell
git add static/app.js
git commit -m "feat: IndexedDB dir handle, output folder picker, and auto-save on generation complete"
```

---

## Task 6: JS — Library Show button redesign and "not downloaded" modal

**Files:**
- Modify: `static/app.js`

Redesign the Show button in `_renderLibraryCard` for cloud mode:
- If entry is in `sizzle_downloads` with `localFolderPath` → open Explorer
- If entry is in `sizzle_downloads` without path → open via blob URL
- If not in `sizzle_downloads` → show "not downloaded" modal

- [ ] **Step 1: Replace the Show button creation and click handler**

In `static/app.js`, find the Show button block inside `_renderLibraryCard` (around line 1098). The block to replace starts at `const showBtn = document.createElement('button');` and ends after the closing `});` of `showBtn.addEventListener('click', ...)`. Replace the entire block with:

```js
  const showBtn = document.createElement('button');
  showBtn.className = 'reel-btn show';
  showBtn.dataset.id = entry.id;
  showBtn.dataset.path = entry.path || '';

  if (APP_MODE === 'cloud') {
    const dlInfo = _getDownload(entry.id);
    if (dlInfo && dlInfo.localFolderPath) {
      showBtn.textContent = '📂 Show';
    } else if (dlInfo) {
      showBtn.textContent = '🌐 View';
    } else {
      showBtn.textContent = '📂 Show';
    }
  } else {
    showBtn.textContent = '📂 Show';
  }

  showBtn.addEventListener('click', async () => {
    if (APP_MODE === 'cloud') {
      const info = _getDownload(entry.id);
      if (info) {
        if (info.localFolderPath) {
          // Open Windows Explorer with the file highlighted
          await fetch(GENERATOR_URL + '/open-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              folder: info.localFolderPath,
              file_path: info.localFolderPath + '\\' + info.filename,
            }),
          });
        } else {
          // OS path unknown — serve from dir handle or proxy
          const handle = await _idbLoad('sizzle_output_dir_handle').catch(() => null);
          if (handle) {
            try {
              const fh = await handle.getFileHandle(info.filename);
              window.open(URL.createObjectURL(await fh.getFile()), '_blank');
              return;
            } catch { /* fall through to proxy */ }
          }
          window.open(`${GENERATOR_URL}/library-video/${entry.id}`, '_blank');
        }
      } else {
        _showNotDownloadedModal(entry);
      }
      return;
    }
    // Local mode: open the containing folder with the file highlighted
    const folder = (entry.path || '').replace(/[\\/][^\\/]+$/, '');
    await fetch(GENERATOR_URL + '/open-folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder, file_path: entry.path }),
    });
  });
```

- [ ] **Step 2: Add `_showNotDownloadedModal` function and modal button wiring**

Add the following after the `_idbLoad`/`_idbSave` helper block from Task 5 (or at the bottom of the non-IIFE JS, before the `initCloudMode` IIFE):

```js
// ─── "Not downloaded" modal ───────────────────────────────────────────────────
let _modalEntry = null;

function _showNotDownloadedModal(entry) {
  _modalEntry = entry;
  const folderName = localStorage.getItem('sizzle_output_folder_name') || 'output folder';
  $('not-downloaded-body').textContent =
    `"${entry.filename}" has not been saved to your local machine.`;
  $('btn-not-downloaded-save').textContent = `Save to ${folderName}`;
  $('not-downloaded-modal').classList.remove('hidden');
}

$('btn-not-downloaded-close').addEventListener('click', () => {
  $('not-downloaded-modal').classList.add('hidden');
  _modalEntry = null;
});

$('btn-not-downloaded-view').addEventListener('click', () => {
  if (_modalEntry) {
    window.open(`${GENERATOR_URL}/library-video/${_modalEntry.id}`, '_blank');
  }
  $('not-downloaded-modal').classList.add('hidden');
  _modalEntry = null;
});

$('btn-not-downloaded-save').addEventListener('click', async () => {
  if (!_modalEntry) return;
  const entry = _modalEntry;
  $('not-downloaded-modal').classList.add('hidden');
  _modalEntry = null;

  let handle = await _idbLoad('sizzle_output_dir_handle').catch(() => null);
  if (!handle) {
    handle = await _pickOutputFolder();
    if (!handle) return; // user cancelled picker
  }

  const saveBtn = document.querySelector(`.reel-btn.show[data-id="${entry.id}"]`);
  if (saveBtn) { saveBtn.textContent = '⬇ Saving…'; saveBtn.disabled = true; }

  try {
    const resp = await fetch(`${GENERATOR_URL}/library-video/${entry.id}`);
    if (!resp.ok) throw new Error(`Fetch failed: ${resp.status}`);
    const blob = await resp.blob();
    const saved = await _saveToOutputFolder(handle, entry.filename, blob, entry.id);

    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = saved.localFolderPath ? '📂 Show' : '🌐 View';
    }
  } catch (err) {
    if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = '📂 Show'; }
    alert(`Save failed: ${err.message}`);
  }
});
```

- [ ] **Step 3: Manual verification**

Start both services (same commands as Task 5 Step 5).

**Scenario A — Not downloaded:**
1. Go to Library tab
2. Click "📂 Show" on any entry → the "You have not downloaded this file" modal should appear
3. Click "View in Browser" → video opens in new tab; modal closes
4. Repeat; click "Save to [folder]" → if no output folder set, picker appears; after picking, file saves; button updates to "📂 Show" or "🌐 View"

**Scenario B — Downloaded with known OS path:**
1. After a successful "Save to [folder]" (probe scan found the path)
2. Click "📂 Show" → Windows Explorer opens with the file highlighted

**Scenario C — Downloaded without OS path:**
1. Set output folder to an unusual location (not under Downloads/Videos/etc.)
2. Save a reel
3. Click "🌐 View" → file plays in browser tab from the dir handle

- [ ] **Step 4: Run full test suite to confirm no regressions**

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all previously passing tests still pass (no server-side changes in this task).

- [ ] **Step 5: Commit**

```powershell
git add static/app.js
git commit -m "feat: redesign library Show button with not-downloaded modal and Explorer reveal"
```

---

## Self-review

**Spec coverage:**
- §1a (directory picker) → Task 5 Step 1 `_pickOutputFolder`, Step 2 button wiring ✓
- §1b (auto-write on generation complete) → Task 5 Steps 3–4 ✓
- §1c (probe scan / OS path) → Task 2 server endpoint + Task 5 `_saveToOutputFolder` ✓
- §2 "not downloaded" modal → Task 4 HTML + Task 6 Steps 1–2 ✓
- §2 "downloaded + path known" → Task 6 Step 1 (Explorer reveal branch) ✓
- §2 "downloaded + no path" → Task 6 Step 1 (blob URL branch) ✓
- §3a font size reduction → Task 1 Step 3 ✓
- §3b x clamp → Task 1 Step 3 ✓

**Placeholder scan:** No TBDs, TODOs, or vague steps found.

**Type consistency:**
- `_getDownload(entryId)` returns `{folderName, filename, localFolderPath}` or `null` — used consistently in Tasks 5 and 6 ✓
- `_saveToOutputFolder(handle, filename, blob, entryId)` → returns `{folderName, localFolderPath}` — return value used correctly in Task 5 auto-save and Task 6 modal ✓
- `_showNotDownloadedModal(entry)` takes a full library entry object — called with `entry` in Task 6 ✓
- `entry_id` field in the result object (Task 3) consumed in Task 5 as `msg.result.entry_id` ✓
