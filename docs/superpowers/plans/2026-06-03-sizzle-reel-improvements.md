# Sizzle Reel Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add enhanced title cards (video name + start time + segment X/Y), fix audio desync, add a folder-switcher dropdown in the workspace topbar, and add prev/next segment skip controls to both video players.

**Architecture:** Four mostly independent changes sharing one backend generation pass: `video_editor.py` gets a seek-flag fix; `app.py` gets an updated `make_title_card` signature, restructured generation loop, and `segment_starts` metadata in results and library entries; `index.html`/`app.js`/`style.css` get the new UI controls.

**Tech Stack:** Python/Flask (`app.py`, `video_editor.py`), vanilla JS (`static/app.js`), CSS (`static/style.css`), Jinja2 HTML (`templates/index.html`), pytest, ffmpeg subprocess.

---

## File Map

| File | Changes |
|------|---------|
| `video_editor.py` | `extract_clip` — move `-ss` before `-i`, use `-t duration`, add `-avoid_negative_ts make_zero` |
| `app.py` | `_format_seconds` helper; `make_title_card(lines, ...)` signature; restructured generation loop (unified cards, `seg_num`, `total_segs`, `segment_starts`); add `segment_starts` to result dict and library entry |
| `templates/index.html` | Add `#btn-prev-seg` / `#btn-next-seg` to result player; add `#btn-lib-prev-seg` / `#btn-lib-next-seg` to library player overlay |
| `static/style.css` | Styles for `.seg-skip-btn`, `.folder-dropdown` |
| `static/app.js` | `skipToSegment()` helper; wire result-player skip buttons; update `showResult(result)` to store segment_starts on state; update `openLibraryPlayer(entry)` to use `entry.segment_starts`; folder-badge dropdown |
| `tests/test_video_editor.py` | Update `test_extract_clip_calls_correct_ffmpeg_args` to expect new args |
| `tests/test_app.py` | Add tests: `_format_seconds`, `make_title_card` lines list, `segment_starts` in generation result |

---

## Task 1: Fix audio desync in `extract_clip`

**Files:**
- Modify: `tests/test_video_editor.py:35-48`
- Modify: `video_editor.py:23-41`

- [ ] **Step 1: Update the existing args test to expect the new seek order**

Replace `test_extract_clip_calls_correct_ffmpeg_args` in `tests/test_video_editor.py`:

```python
def test_extract_clip_calls_correct_ffmpeg_args():
    with patch("video_editor.subprocess.run") as mock_run:
        extract_clip("input.mp4", 5.0, 30.0, "clip.mp4")
    args = mock_run.call_args[0][0]
    assert args == [
        "ffmpeg", "-y",
        "-ss", "5.0",
        "-i", "input.mp4",
        "-t", "25.0",
        "-avoid_negative_ts", "make_zero",
        "-c:v", "libx264",
        "-preset", "fast",
        "-c:a", "aac",
        "clip.mp4",
    ]
```

- [ ] **Step 2: Run test to confirm it fails**

```
.\venv\Scripts\python.exe -m pytest tests/test_video_editor.py::test_extract_clip_calls_correct_ffmpeg_args -v
```

Expected: FAIL — `assert args == [...]` mismatch (current code has `-i` before `-ss`)

- [ ] **Step 3: Update `extract_clip` in `video_editor.py`**

Replace the `subprocess.run` call in `extract_clip` (lines 28–41):

```python
def extract_clip(video_path: str, start_sec: float, end_sec: float, output_path: str) -> None:
    # Re-encode (never stream-copy) so every clip starts on an I-frame.
    # -ss before -i: fast input seek. -t duration (not -to) is relative to the
    # seek point. -avoid_negative_ts make_zero zeroes each clip's timestamps so
    # the concat demuxer sees clean zero-based PTS on every clip — prevents AV drift.
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", str(start_sec),
            "-i", video_path,
            "-t", str(end_sec - start_sec),
            "-avoid_negative_ts", "make_zero",
            "-c:v", "libx264",
            "-preset", "fast",
            "-c:a", "aac",
            output_path,
        ],
        check=True,
        capture_output=True,
    )
```

- [ ] **Step 4: Run all video_editor tests**

```
.\venv\Scripts\python.exe -m pytest tests/test_video_editor.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```
git add video_editor.py tests/test_video_editor.py
git commit -m "fix: move -ss before -i in extract_clip to fix AV desync in generated reels"
```

---

## Task 2: Add `_format_seconds` helper and update `make_title_card` to accept `lines: list[str]`

**Files:**
- Modify: `app.py` (add `_format_seconds` near top of module-level helpers; update `make_title_card` signature)
- Modify: `tests/test_app.py` (add two tests)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_app.py` (after the existing imports, before any test functions):

```python
def test_format_seconds_zero():
    from app import _format_seconds
    assert _format_seconds(0.0) == "0:00"


def test_format_seconds_minutes_and_seconds():
    from app import _format_seconds
    assert _format_seconds(75.0) == "1:15"


def test_format_seconds_exact_minute():
    from app import _format_seconds
    assert _format_seconds(120.0) == "2:00"


def test_make_title_card_generates_one_drawtext_per_line():
    """make_title_card(lines, ...) must produce one drawtext filter per line."""
    from unittest.mock import patch, MagicMock
    from app import make_title_card
    with patch("app.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card(["NOBU", "from 1:23", "Segment 2 / 5"], 1920, 1080, "/tmp/card.mp4")
    args = mock_run.call_args[0][0]
    vf_idx = args.index("-vf")
    vf_value = args[vf_idx + 1]
    # Three lines → three drawtext filters joined by comma
    assert vf_value.count("drawtext=") == 3
    assert "NOBU" in vf_value
    assert "from 1\\:23" in vf_value
    assert "Segment 2 / 5" in vf_value
```

- [ ] **Step 2: Run tests to confirm they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_format_seconds_zero tests/test_app.py::test_make_title_card_generates_one_drawtext_per_line -v
```

Expected: FAIL — `ImportError: cannot import name '_format_seconds'` and `TypeError` on `make_title_card`

- [ ] **Step 3: Add `_format_seconds` to `app.py`**

Add this function after the `_find_system_font` function (around line 270, before `make_title_card`):

```python
def _format_seconds(sec: float) -> str:
    """Format seconds as M:SS for display on title cards."""
    m = int(sec) // 60
    s = int(sec) % 60
    return f"{m}:{s:02d}"
```

- [ ] **Step 4: Update `make_title_card` signature from `name: str` to `lines: list[str]`**

Replace the `make_title_card` function signature and the `lines = textwrap.wrap(name, chars_per_line) or [name]` block. The full updated function:

```python
def make_title_card(
    lines: list[str], width: int, height: int, output_path: str, duration: float = 5.0
) -> None:
    """Generate a black title card with white centred text, encoded H.264/AAC.

    lines: list of text strings, one per visual line on the card.
    """
    fontsize = max(24, height // 15)

    def _escape(s: str) -> str:
        return (
            s.replace("\\", "\\\\")
             .replace("'", "\\'")
             .replace(":", "\\:")
             .replace("%", "%%")
        )

    font = _find_system_font()
    if font:
        escaped_font = font.replace("\\", "/").replace(":", "\\:")
        fontfile_arg = f"fontfile='{escaped_font}':"
    else:
        fontfile_arg = ""

    line_height = int(fontsize * 1.2)
    spacing = 8
    n = len(lines)
    total_h = n * line_height + (n - 1) * spacing

    filters = []
    for i, line in enumerate(lines):
        if n == 1:
            y_expr = "(h-text_h)/2"
        else:
            y_off = i * (line_height + spacing)
            y_expr = f"(h-{total_h})/2+{y_off}"
        filters.append(
            f"drawtext={fontfile_arg}text='{_escape(line)}':fontcolor=white"
            f":fontsize={fontsize}:x=(w-text_w)/2:y={y_expr}"
        )

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=black:size={width}x{height}:rate=30",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-vf", ",".join(filters),
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac",
            "-t", str(duration),
            output_path,
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        print(result.stderr.decode(errors="replace"), file=__import__("sys").stderr)
        result.check_returncode()
```

- [ ] **Step 5: Run the new tests**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_format_seconds_zero tests/test_app.py::test_format_seconds_minutes_and_seconds tests/test_app.py::test_format_seconds_exact_minute tests/test_app.py::test_make_title_card_generates_one_drawtext_per_line -v
```

Expected: all PASS

- [ ] **Step 6: Run full test suite to catch regressions**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all PASS (the two old `make_title_card` call sites in `_run_generation` will now break at runtime — that's expected and will be fixed in Task 3)

- [ ] **Step 7: Commit**

```
git add app.py tests/test_app.py
git commit -m "feat: add _format_seconds helper and update make_title_card to accept lines list"
```

---

## Task 3: Restructure generation loop — unified title cards + `segment_starts`

**Files:**
- Modify: `app.py` — `_run_generation` function and `_library_add` call
- Modify: `tests/test_app.py` — add `segment_starts` test

The goal: replace the two separate title card types (video-name card, between-segment card) with a single card per content clip showing `[vp.stem, "from M:SS", "Segment N / Total"]`. Track `segment_starts: list[float]` (cumulative output time at which each content clip begins) and include it in the result dict and library entry.

- [ ] **Step 1: Write failing test for `segment_starts` in generation result**

Add to `tests/test_app.py`:

```python
def test_generation_result_includes_segment_starts(client, tmp_path):
    """After a successful generation, job result must include segment_starts list."""
    import threading, time
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:05] Speaker: Hello.\n[1:10] Speaker: World.", encoding="utf-8"
    )

    with patch("app.extract_clip"), \
         patch("app.stitch_clips"), \
         patch("app.check_ffmpeg"), \
         patch("app.make_title_card"), \
         patch("app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("app._library_add"):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "checkbox",
            "selections": {"vid.mp4": ["[0:05] Speaker: Hello.", "[1:10] Speaker: World."]},
            "prompt": "greetings",
            "output_filename": "out.mp4",
        })
    job_id = resp.get_json()["job_id"]

    # Poll until done (generation runs in a thread)
    from app import _jobs
    for _ in range(50):
        time.sleep(0.1)
        if _jobs.get(job_id, {}).get("status") in ("done", "error"):
            break

    result = _jobs[job_id]["result"]
    assert result is not None
    assert "segment_starts" in result
    assert isinstance(result["segment_starts"], list)
    assert len(result["segment_starts"]) >= 1
```

- [ ] **Step 2: Run test to confirm it fails**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_generation_result_includes_segment_starts -v
```

Expected: FAIL — `KeyError: 'segment_starts'`

- [ ] **Step 3: Rewrite the generation loop in `_run_generation`**

Replace the entire `with tempfile.TemporaryDirectory() as tmp_dir:` block (currently lines ~395–450) with:

```python
    TITLE_CARD_DURATION = 5.0
    total_segs = sum(len(segs) for _, segs in video_segments)

    _append_log(job_id, "· Extracting clips...")
    output_path = str(Path(folder) / output_filename)

    with tempfile.TemporaryDirectory() as tmp_dir:
        clip_paths: list[str] = []
        clip_durations: list[float] = []
        segment_starts: list[float] = []
        cumulative_time: float = 0.0
        clip_index = 0
        seg_num = 0

        for vp, segs in video_segments:
            if job["cancel"].is_set():
                with _jobs_lock:
                    job["status"] = "cancelled"
                return

            try:
                width, height = get_video_dimensions(str(vp))
            except Exception:
                width, height = 1920, 1080

            for start_sec, end_sec in segs:
                seg_num += 1

                # Title card before this clip
                card_path = os.path.join(tmp_dir, f"clip_{clip_index:04d}.mp4")
                card_lines = [
                    vp.stem,
                    f"from {_format_seconds(start_sec)}",
                    f"Segment {seg_num} / {total_segs}",
                ]
                try:
                    make_title_card(card_lines, width, height, card_path)
                    clip_paths.append(card_path)
                    clip_index += 1
                    cumulative_time += TITLE_CARD_DURATION
                except Exception as exc:
                    _append_log(job_id, f"· Could not create title card for {vp.name}: {exc}")

                # Record where this content clip starts in the output
                segment_starts.append(cumulative_time)

                # Content clip
                clip_path = os.path.join(tmp_dir, f"clip_{clip_index:04d}{vp.suffix}")
                try:
                    extract_clip(str(vp), start_sec, end_sec, clip_path)
                    clip_paths.append(clip_path)
                    clip_durations.append(end_sec - start_sec)
                    cumulative_time += end_sec - start_sec
                    clip_index += 1
                except Exception as exc:
                    segment_starts.pop()  # clip failed, remove the start marker
                    _append_log(
                        job_id,
                        f"✗ {vp.name} [{start_sec:.1f}-{end_sec:.1f}] — extraction failed: {exc}",
                    )

        if not clip_paths:
            with _jobs_lock:
                job["status"] = "error"
                job["error"] = "No clips could be extracted"
            return

        _append_log(job_id, "· Stitching reel...")
        try:
            stitch_clips(clip_paths, output_path)
        except Exception as exc:
            with _jobs_lock:
                job["status"] = "error"
                job["error"] = f"Stitch failed: {exc}"
            return
```

- [ ] **Step 4: Add `segment_starts` to the result dict and library entry**

Replace the `duration = int(sum(clip_durations))` block and subsequent `result = {...}` and `_library_add({...})` block (after the `with tempfile.TemporaryDirectory()` block):

```python
    duration = int(sum(clip_durations))

    result = {
        "path": output_path,
        "filename": output_filename,
        "clip_count": len(clip_durations),
        "duration_seconds": duration,
        "segment_starts": segment_starts,
    }

    _append_log(job_id, f"✓ Done — saved to {output_filename}")
    with _jobs_lock:
        job["status"] = "done"
        job["result"] = result

    _library_add({
        "id": str(uuid.uuid4()),
        "filename": output_filename,
        "path": output_path,
        "source_folder": Path(folder).name + "/",
        "prompt": prompt,
        "duration_seconds": duration,
        "clip_count": len(clip_durations),
        "segment_starts": segment_starts,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })
```

- [ ] **Step 5: Run the new test**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_generation_result_includes_segment_starts -v
```

Expected: PASS

- [ ] **Step 6: Run full test suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```
git add app.py tests/test_app.py
git commit -m "feat: unified title cards (video/time/segment) and segment_starts in generation result"
```

---

## Task 4: Add segment skip button HTML and CSS

**Files:**
- Modify: `templates/index.html`
- Modify: `static/style.css`

- [ ] **Step 1: Add skip buttons to the result player in `templates/index.html`**

Find the `<div class="result-actions">` block (lines ~147–151) and replace it with:

```html
          <div class="result-actions">
            <div class="seg-skip-controls">
              <button id="btn-prev-seg" class="seg-skip-btn" title="Previous segment">⏮ Prev</button>
              <button id="btn-next-seg" class="seg-skip-btn" title="Next segment">Next ⏭</button>
            </div>
            <button id="btn-new-reel" class="btn-primary">+ New Reel</button>
            <button id="btn-open-folder" class="btn-secondary">📂 Open Folder</button>
          </div>
```

- [ ] **Step 2: Add skip buttons to the library player overlay in `templates/index.html`**

Find the `<div id="library-player-overlay" ...>` block. Replace the inner `<div class="overlay-card">` contents with:

```html
    <div id="library-player-overlay" class="overlay hidden">
      <div class="overlay-card">
        <button id="btn-close-player" class="overlay-close">✕</button>
        <video id="library-video" controls style="width:100%;max-height:500px">
          <source id="library-source" src="" type="video/mp4">
        </video>
        <div class="seg-skip-controls" style="margin-top:8px">
          <button id="btn-lib-prev-seg" class="seg-skip-btn" title="Previous segment">⏮ Prev</button>
          <button id="btn-lib-next-seg" class="seg-skip-btn" title="Next segment">Next ⏭</button>
        </div>
        <div id="library-player-meta" style="padding:8px 0;font-size:12px;color:#5090d0"></div>
      </div>
    </div>
```

- [ ] **Step 3: Add CSS for skip buttons and folder dropdown to `static/style.css`**

Append to the end of `static/style.css`:

```css
/* ── Segment skip controls ──────────────────────────────────────────────── */
.seg-skip-controls {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.seg-skip-btn {
  background: #1a2840;
  border: 1px solid #2a4060;
  color: #7ab0e0;
  font-size: 11px;
  padding: 5px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-family: inherit;
}

.seg-skip-btn:hover {
  background: #223050;
  color: #9dd0ff;
}

/* ── Folder switcher dropdown ───────────────────────────────────────────── */
.folder-badge {
  cursor: pointer;
  user-select: none;
}

.folder-badge:hover {
  color: #9dd0ff;
}

.folder-dropdown {
  position: fixed;
  background: #0c1524;
  border: 1px solid #2a4060;
  border-radius: 6px;
  min-width: 260px;
  z-index: 1000;
  box-shadow: 0 4px 16px rgba(0,0,0,0.5);
  overflow: hidden;
}

.folder-dropdown button {
  display: block;
  width: 100%;
  text-align: left;
  padding: 9px 14px;
  background: transparent;
  border: none;
  border-bottom: 1px solid #1a2840;
  color: #b0c0d8;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.folder-dropdown button:last-child {
  border-bottom: none;
}

.folder-dropdown button:hover {
  background: #1a2840;
  color: #9dd0ff;
}

.folder-dropdown .dropdown-new-folder {
  color: #5090d0;
  font-style: italic;
}
```

- [ ] **Step 4: Start dev server and verify HTML loads without errors**

```
.\venv\Scripts\python.exe -c "from app import create_app; create_app().run(debug=True)"
```

Open `http://localhost:5000` in a browser. Confirm no JS errors in the console. The buttons won't work yet (no JS handlers).

- [ ] **Step 5: Commit**

```
git add templates/index.html static/style.css
git commit -m "feat: add segment skip button HTML and CSS + folder dropdown CSS"
```

---

## Task 5: Wire up segment skip controls in JavaScript

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Add `skipToSegment` helper and wire result player buttons**

Add the following after the `showResult` function (around line 662 in `app.js`):

```js
// ─── Segment skip ─────────────────────────────────────────────────────────────
function skipToSegment(video, segmentStarts, direction) {
  const t = video.currentTime;
  if (direction === 'next') {
    const target = segmentStarts.find(s => s > t + 0.5);
    if (target !== undefined) video.currentTime = target;
  } else {
    const targets = segmentStarts.filter(s => s < t - 0.5);
    if (targets.length) video.currentTime = targets[targets.length - 1];
  }
}

$('btn-prev-seg').addEventListener('click', () => {
  skipToSegment($('result-video'), state.resultSegmentStarts || [], 'prev');
});
$('btn-next-seg').addEventListener('click', () => {
  skipToSegment($('result-video'), state.resultSegmentStarts || [], 'next');
});

$('btn-lib-prev-seg').addEventListener('click', () => {
  skipToSegment($('library-video'), state.librarySegmentStarts || [], 'prev');
});
$('btn-lib-next-seg').addEventListener('click', () => {
  skipToSegment($('library-video'), state.librarySegmentStarts || [], 'next');
});
```

- [ ] **Step 2: Add `resultSegmentStarts` and `librarySegmentStarts` to state and update `showResult`**

At the top of `app.js`, in the `state` object, add two new fields:

```js
const state = {
  folder: null,
  files: [],
  activeFile: null,
  mode: 'checkbox',
  checked: {},
  highlighted: {},
  currentJobId: null,
  resultJobId: null,
  lastPrompt: '',
  resultSegmentStarts: [],
  librarySegmentStarts: [],
};
```

Then update the `showResult` function to capture `segment_starts`:

```js
function showResult(result) {
  showScreen('screen-result');
  $('topbar-controls').classList.remove('hidden');

  state.resultSegmentStarts = result.segment_starts || [];

  const src = `/video/${state.resultJobId}`;
  $('result-source').src = src;
  $('result-video').load();

  $('result-filename').textContent = result.filename;
  const mins = Math.floor(result.duration_seconds / 60);
  const secs = result.duration_seconds % 60;
  $('result-info').textContent =
    `${mins}:${String(secs).padStart(2,'0')} · ${result.clip_count} clips · saved to folder`;
}
```

- [ ] **Step 3: Update `openLibraryPlayer` to store library segment starts**

Replace the `openLibraryPlayer` function:

```js
function openLibraryPlayer(entry) {
  state.librarySegmentStarts = entry.segment_starts || [];
  $('library-source').src = `/library-video/${entry.id}`;
  $('library-video').load();
  $('library-player-meta').textContent =
    `"${entry.prompt}" — ${entry.source_folder}`;
  $('library-player-overlay').classList.remove('hidden');
}
```

- [ ] **Step 4: Test segment skip in the browser**

Start the dev server and generate a short test reel (or open a reel from the library). Confirm:
- "Next ⏭" skips forward to the next segment
- "⏮ Prev" skips back to the previous segment
- At the last segment, Next does nothing
- At the first segment, Prev does nothing

- [ ] **Step 5: Commit**

```
git add static/app.js
git commit -m "feat: add segment skip controls to result and library players"
```

---

## Task 6: Folder switcher dropdown in workspace topbar

**Files:**
- Modify: `static/app.js`
- Modify: `templates/index.html`

- [ ] **Step 1: Add the `▾` chevron to the folder badge in `templates/index.html`**

The badge is currently set by JS (`$('folder-badge').textContent = ...`). No HTML change needed — the chevron will be set in JS. Skip this step.

- [ ] **Step 2: Add folder dropdown logic to `app.js`**

Find the line in `openFolder` that sets the folder badge text (line ~75):
```js
$('folder-badge').textContent = '📁 ' + folder.split(/[\\/]/).pop() + '/';
```

Replace it with:
```js
$('folder-badge').textContent = '📁 ' + folder.split(/[\\/]/).pop() + '/ ▾';
```

Then add the following dropdown logic after the `loadRecentFolders()` call at the bottom of `app.js`:

```js
// ─── Folder badge dropdown ────────────────────────────────────────────────────
let _folderDropdown = null;

function _closeFolderDropdown() {
  if (_folderDropdown) {
    _folderDropdown.remove();
    _folderDropdown = null;
  }
}

$('folder-badge').addEventListener('click', async (e) => {
  e.stopPropagation();
  if (_folderDropdown) {
    _closeFolderDropdown();
    return;
  }

  const rect = $('folder-badge').getBoundingClientRect();
  const dropdown = document.createElement('div');
  dropdown.className = 'folder-dropdown';
  dropdown.style.top = (rect.bottom + 4) + 'px';
  dropdown.style.left = rect.left + 'px';
  _folderDropdown = dropdown;

  // Fetch recent folders
  let recents = [];
  try {
    const resp = await fetch('/recent-folders');
    recents = await resp.json();
  } catch (_) {}

  recents.forEach(entry => {
    const btn = document.createElement('button');
    btn.textContent = '📁 ' + entry.path.split(/[\\/]/).pop() + '/';
    btn.title = entry.path;
    btn.addEventListener('click', () => {
      _closeFolderDropdown();
      openFolder(entry.path);
    });
    dropdown.appendChild(btn);
  });

  const newBtn = document.createElement('button');
  newBtn.className = 'dropdown-new-folder';
  newBtn.textContent = '📂 Select new folder...';
  newBtn.addEventListener('click', async () => {
    _closeFolderDropdown();
    const resp = await fetch('/browse', { method: 'POST' });
    const { path } = await resp.json();
    if (path) openFolder(path);
  });
  dropdown.appendChild(newBtn);

  document.body.appendChild(dropdown);

  // Dismiss on outside click or Escape
  const onOutside = (ev) => {
    if (!dropdown.contains(ev.target)) {
      _closeFolderDropdown();
      document.removeEventListener('mousedown', onOutside);
    }
  };
  const onEscape = (ev) => {
    if (ev.key === 'Escape') {
      _closeFolderDropdown();
      document.removeEventListener('keydown', onEscape);
    }
  };
  setTimeout(() => {
    document.addEventListener('mousedown', onOutside);
    document.addEventListener('keydown', onEscape);
  }, 0);
});
```

- [ ] **Step 3: Test the folder dropdown in the browser**

Load a folder, then click the `📁 foldername/ ▾` badge. Confirm:
- Dropdown appears with recent folders and "Select new folder..." item
- Clicking a recent folder calls `openFolder` with that path
- Clicking "Select new folder..." opens the system browse dialog
- Clicking outside the dropdown closes it
- Pressing Escape closes it
- Clicking the badge again when open closes it

- [ ] **Step 4: Commit**

```
git add static/app.js
git commit -m "feat: folder switcher dropdown on workspace topbar badge"
```

---

## Task 7: Final verification

- [ ] **Step 1: Run full test suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all PASS

- [ ] **Step 2: Manual end-to-end smoke test**

Start the dev server:
```
.\venv\Scripts\python.exe -c "from app import create_app; create_app().run(debug=True)"
```

1. Open a folder of real videos
2. Analyze with a prompt and select some clips
3. Generate a reel — observe the generation log shows expected title card creation
4. On the result screen: check title cards in the video show 3 lines (video name / from M:SS / Segment N / Total)
5. Use ⏮ Prev and Next ⏭ to jump between segments
6. Open the Library tab, play the reel — confirm skip buttons work there too
7. In the workspace, click the folder badge — confirm the dropdown appears with recent folders
8. Listen to the reel for audio/video sync (should be noticeably better than before)

- [ ] **Step 3: Commit if any tweaks were made, then done**
