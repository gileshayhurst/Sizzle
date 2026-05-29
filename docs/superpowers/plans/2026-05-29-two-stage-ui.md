# Two-Stage UI: Analyze + Generate Separation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate Claude analysis (auto-highlight relevant transcript lines) from video generation (stitch highlighted lines directly into a reel), add segment title cards between non-contiguous clips, fix the highlight-mode stacked-listener bug, and restore per-line checkboxes in checkbox mode.

**Architecture:** A new `/analyze` endpoint calls Claude and returns per-video sets of matching raw line strings. The modified `/generate` skips Claude entirely; `_group_lines_into_segments()` converts selected lines to `(start_sec, end_sec)` clip ranges. A new "Analyze bar" above the transcript scroll holds the prompt input. Segment title cards ("Segment 1", …) are inserted between non-contiguous selected clusters within a single video using the existing `make_title_card` machinery.

**Tech Stack:** Python 3.11 / Flask 2.x, ffmpeg via existing helpers, vanilla JS (ES2022), pytest. Run tests: `.\venv\Scripts\python.exe -m pytest tests/ -v`

---

## File Map

| File | Changes |
|---|---|
| `app.py` | Add `_group_lines_into_segments()`, `_run_analyze()`, `/analyze` route; rewrite `_run_generation` (no Claude, use segments, segment cards); relax prompt requirement in `/generate` route |
| `templates/index.html` | Add analyze bar + error div; remove footer prompt field; remove topbar Analyze Everything button; add `disabled` to Generate button |
| `static/style.css` | Add analyze bar styles; add `.btn-analyze`; add `.btn-generate:disabled`; add `.transcript-line-cb` hover + cursor for per-line clicks |
| `static/app.js` | Add `runAnalyze()`, `updateGenerateBtn()`, `_updateHeaderCbState()`; rewrite `renderCheckboxMode` (per-line checkboxes, in-place DOM mutation); fix `renderHighlightMode` with AbortController; remove `btn-analyze-all` listener; update `submitGenerate` |
| `tests/test_app.py` | Add tests for `_group_lines_into_segments` and `/analyze`; update `test_title_card_inserted_between_videos` (mode→highlight, add selections); rename `test_generate_missing_prompt_returns_400` (prompt now optional) |

---

### Task 1: `_group_lines_into_segments` helper

**Files:**
- Modify: `app.py` (add function after `_group_by_minute`, around line 133)
- Test: `tests/test_app.py`

- [ ] **Step 1: Write four failing tests**

Add to `tests/test_app.py`:

```python
def test_group_lines_into_segments_single_contiguous_block():
    from app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 5.0},
        {"raw": "b", "seconds": 10.0},
        {"raw": "c", "seconds": 15.0},
        {"raw": "d", "seconds": 20.0},  # unselected
    ]
    result = _group_lines_into_segments(lines, {"a", "b", "c"})
    # end = first unselected line (d) at 20.0
    assert result == [(5.0, 20.0)]


def test_group_lines_into_segments_two_clusters():
    from app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 5.0},
        {"raw": "b", "seconds": 10.0},  # unselected — splits the groups
        {"raw": "c", "seconds": 15.0},
        {"raw": "d", "seconds": 20.0},
    ]
    result = _group_lines_into_segments(lines, {"a", "c", "d"})
    # segment 1: a(5.0) ends at b(10.0)
    # segment 2: c,d — last line + 10 = 30.0
    assert result == [(5.0, 10.0), (15.0, 30.0)]


def test_group_lines_into_segments_all_selected():
    from app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 5.0},
        {"raw": "b", "seconds": 10.0},
    ]
    result = _group_lines_into_segments(lines, {"a", "b"})
    # No line after the group — end = last.seconds + 10
    assert result == [(5.0, 20.0)]


def test_group_lines_into_segments_none_selected():
    from app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 5.0},
        {"raw": "b", "seconds": 10.0},
    ]
    result = _group_lines_into_segments(lines, set())
    assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_group_lines_into_segments_single_contiguous_block tests/test_app.py::test_group_lines_into_segments_two_clusters tests/test_app.py::test_group_lines_into_segments_all_selected tests/test_app.py::test_group_lines_into_segments_none_selected -v
```

Expected: FAIL with `ImportError: cannot import name '_group_lines_into_segments'`

- [ ] **Step 3: Add `_group_lines_into_segments` to `app.py`**

Insert after the `_group_by_minute` function (around line 133):

```python
def _group_lines_into_segments(
    all_lines: list[dict], selected_raws: set[str]
) -> list[tuple[float, float]]:
    """Convert selected transcript lines into (start_sec, end_sec) clip ranges.

    Lines are grouped into segments: any unselected line between two selected
    lines ends the current segment and starts a new one.

    End time = seconds of the first line AFTER the segment (the next line in the
    full transcript, whether selected or not). If the segment runs to the end of
    the transcript, end = last_line.seconds + 10.
    """
    segments: list[tuple[float, float]] = []
    current: list[dict] = []

    for line in all_lines:
        if line["raw"] in selected_raws:
            current.append(line)
        else:
            if current:
                segments.append((current[0]["seconds"], line["seconds"]))
                current = []

    if current:
        segments.append((current[0]["seconds"], current[-1]["seconds"] + 10.0))

    return segments
```

- [ ] **Step 4: Run tests to verify they pass**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_group_lines_into_segments_single_contiguous_block tests/test_app.py::test_group_lines_into_segments_two_clusters tests/test_app.py::test_group_lines_into_segments_all_selected tests/test_app.py::test_group_lines_into_segments_none_selected -v
```

Expected: 4 PASSED

- [ ] **Step 5: Run full suite to check for regressions**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all existing tests still PASS

- [ ] **Step 6: Commit**

```
git add app.py tests/test_app.py
git commit -m "feat: add _group_lines_into_segments helper"
```

---

### Task 2: `/analyze` endpoint

**Files:**
- Modify: `app.py` (add `_run_analyze()` function and `/analyze` route)
- Test: `tests/test_app.py`

- [ ] **Step 1: Write four failing tests**

Add to `tests/test_app.py`:

```python
def test_analyze_returns_highlights(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:05] Speaker: Hello world.\n[0:15] Speaker: Black cod is amazing.",
        encoding="utf-8",
    )
    with patch("app.query_claude", return_value="0:05-0:20"):
        resp = client.post("/analyze", json={"folder": str(tmp_path), "prompt": "food"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "highlights" in data
    assert "vid.mp4" in data["highlights"]
    # both lines fall within 0:05-0:20
    assert len(data["highlights"]["vid.mp4"]) == 2


def test_analyze_missing_prompt_returns_400(client, tmp_path):
    resp = client.post("/analyze", json={"folder": str(tmp_path)})
    assert resp.status_code == 400


def test_analyze_missing_folder_returns_404(client):
    resp = client.post("/analyze", json={"folder": "/nonexistent/xyz", "prompt": "food"})
    assert resp.status_code == 404


def test_analyze_no_matches_returns_empty_list(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")
    with patch("app.query_claude", return_value="none"):
        resp = client.post("/analyze", json={"folder": str(tmp_path), "prompt": "food"})
    assert resp.status_code == 200
    assert resp.get_json()["highlights"]["vid.mp4"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_analyze_returns_highlights tests/test_app.py::test_analyze_missing_prompt_returns_400 tests/test_app.py::test_analyze_missing_folder_returns_404 tests/test_app.py::test_analyze_no_matches_returns_empty_list -v
```

Expected: FAIL with 404 (route not defined yet)

- [ ] **Step 3: Add `_run_analyze` and the `/analyze` route to `app.py`**

Add `_run_analyze` after `_run_generation` (before `create_app`):

```python
def _run_analyze(folder: str, prompt: str) -> dict:
    """Call Claude on every transcript in folder. Returns per-video matched raw lines."""
    try:
        video_paths = scan_videos(folder)
    except Exception as exc:
        return {"error": str(exc)}

    highlights: dict[str, list[str]] = {}
    errors: list[str] = []

    for vp in video_paths:
        txt_path = vp.with_suffix(".txt")
        if not txt_path.exists() or txt_path.stat().st_size == 0:
            highlights[vp.name] = []
            continue

        transcript = txt_path.read_text(encoding="utf-8")
        all_lines = _parse_transcript_lines(transcript)

        try:
            response = query_claude(transcript, prompt)
            ranges = parse_timestamps(response)
        except Exception as exc:
            errors.append(f"{vp.name}: {exc}")
            highlights[vp.name] = []
            continue

        matched: list[str] = []
        for seg in ranges:
            start_str, end_str = seg.split("-", 1)
            start_sec = parse_timestamp_to_seconds(start_str)
            end_sec = parse_timestamp_to_seconds(end_str)
            for line in all_lines:
                if start_sec - 0.5 <= line["seconds"] <= end_sec + 0.5:
                    if line["raw"] not in matched:
                        matched.append(line["raw"])

        highlights[vp.name] = matched

    if errors and not any(highlights.values()):
        return {"error": "; ".join(errors)}

    return {"highlights": highlights}
```

Add the route inside `create_app`, after the `/transcripts` route:

```python
    @app.post("/analyze")
    def analyze():
        body = request.get_json() or {}
        folder = body.get("folder", "").strip()
        prompt = body.get("prompt", "").strip()
        if not prompt:
            return jsonify({"error": "prompt is required"}), 400
        if not folder or not Path(folder).exists():
            return jsonify({"error": "Folder not found"}), 404
        result = _run_analyze(folder, prompt)
        if "error" in result:
            return jsonify(result), 500
        return jsonify(result)
```

- [ ] **Step 4: Run the four new tests**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_analyze_returns_highlights tests/test_app.py::test_analyze_missing_prompt_returns_400 tests/test_app.py::test_analyze_missing_folder_returns_404 tests/test_app.py::test_analyze_no_matches_returns_empty_list -v
```

Expected: 4 PASSED

- [ ] **Step 5: Run full suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 6: Commit**

```
git add app.py tests/test_app.py
git commit -m "feat: add /analyze endpoint (Claude highlight extraction)"
```

---

### Task 3: Rewrite `_run_generation` — direct clip extraction + segment title cards

**Files:**
- Modify: `app.py` (`_run_generation` function and `/generate` route)
- Test: `tests/test_app.py` (update two existing tests that will break)

**Context:** The current `_run_generation` sends selections to Claude to get `M:SS–M:SS` ranges, then extracts clips. The new version skips Claude, calls `_group_lines_into_segments` to derive ranges from selected lines, and inserts "Segment N" title cards between non-contiguous segments within a video. The `/generate` route currently returns 400 when `prompt` is absent — prompt is now optional (stored for library only).

- [ ] **Step 1: Update the two tests that will break**

In `tests/test_app.py`, make these changes:

**1. Rename `test_generate_missing_prompt_returns_400` → verify prompt is now optional:**

```python
def test_generate_accepts_empty_prompt(client, tmp_path):
    """prompt is now optional at generate time (stored for library only)."""
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")
    with patch("app.extract_clip"), \
         patch("app.stitch_clips"), \
         patch("app.check_ffmpeg"):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {"vid.mp4": ["[0:05] Speaker: Hello."]},
            "output_filename": "out.mp4",
        })
    assert resp.status_code == 200
    assert "job_id" in resp.get_json()
```

**2. Update `test_title_card_inserted_between_videos` to use selections instead of mode="all":**

```python
def test_title_card_inserted_between_videos(client, tmp_path):
    """make_title_card is called once between two source videos."""
    import time

    (tmp_path / "alpha.mp4").touch()
    (tmp_path / "alpha.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")
    (tmp_path / "beta.mp4").touch()
    (tmp_path / "beta.txt").write_text("[0:10] Speaker: World.", encoding="utf-8")

    with patch("app.extract_clip"), \
         patch("app.stitch_clips"), \
         patch("app.check_ffmpeg"), \
         patch("app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("app.make_title_card") as mock_card:

        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {
                "alpha.mp4": ["[0:05] Speaker: Hello."],
                "beta.mp4": ["[0:10] Speaker: World."],
            },
            "prompt": "",
            "output_filename": "out.mp4",
        })
        assert resp.status_code == 200
        job_id = resp.get_json()["job_id"]

        for _ in range(25):
            status = client.get(f"/status/{job_id}").get_json()["status"]
            if status in ("done", "error", "cancelled"):
                break
            time.sleep(0.2)

        assert status == "done", f"Job ended in unexpected state: {status}"

    # One video-name title card between alpha and beta
    assert mock_card.call_count == 1
    assert mock_card.call_args[0][0] == "beta"
```

- [ ] **Step 2: Run the full suite — expect exactly these two tests to fail**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: `test_generate_missing_prompt_returns_400` FAIL and `test_title_card_inserted_between_videos` FAIL (both call Claude which is no longer called in generate). All other tests PASS.

- [ ] **Step 3: Replace `_run_generation` in `app.py`**

Replace the entire `_run_generation` function with:

```python
def _run_generation(job_id: str, folder: str, mode: str,
                    selections: dict, prompt: str, output_filename: str) -> None:
    """Extract and stitch clips from selected transcript lines.

    No Claude call — clip ranges come from _group_lines_into_segments().
    Segment title cards ("Segment N") are inserted between non-contiguous
    selected clusters within a single video. Video-name title cards are
    inserted between different source videos (existing behaviour).
    """
    job = _jobs[job_id]
    try:
        video_paths = scan_videos(folder)
    except Exception as exc:
        with _jobs_lock:
            job["status"] = "error"
            job["error"] = str(exc)
        return

    video_segments: list[tuple] = []

    for vp in video_paths:
        if job["cancel"].is_set():
            with _jobs_lock:
                job["status"] = "cancelled"
            return

        selected_raws = selections.get(vp.name, [])
        if not selected_raws:
            continue

        txt_path = vp.with_suffix(".txt")
        if not txt_path.exists():
            _append_log(job_id, f"· {vp.name} — no transcript, skipping")
            continue

        all_lines = _parse_transcript_lines(txt_path.read_text(encoding="utf-8"))
        segs = _group_lines_into_segments(all_lines, set(selected_raws))

        if segs:
            _append_log(job_id, f"✓ {vp.name} — {len(segs)} segment(s)")
            video_segments.append((vp, segs))
        else:
            _append_log(job_id, f"· {vp.name} — selections produced no segments")

        with _jobs_lock:
            job["done"] += 1

    if not video_segments:
        with _jobs_lock:
            job["status"] = "error"
            job["error"] = "No segments found in selections"
        return

    _append_log(job_id, "· Extracting clips...")
    output_path = str(Path(folder) / output_filename)

    with tempfile.TemporaryDirectory() as tmp_dir:
        clip_paths: list[str] = []
        clip_durations: list[float] = []
        clip_index = 0
        seg_num = 1
        prev_vp = None

        for vp, segs in video_segments:
            # Video-name title card between different source videos
            if prev_vp is not None:
                card_path = os.path.join(tmp_dir, f"clip_{clip_index:04d}.mp4")
                try:
                    width, height = get_video_dimensions(str(vp))
                    make_title_card(vp.stem, width, height, card_path)
                    clip_paths.append(card_path)
                    clip_index += 1
                except Exception as exc:
                    _append_log(job_id, f"· Could not create title card for {vp.name}: {exc}")
            prev_vp = vp

            for seg_idx, (start_sec, end_sec) in enumerate(segs):
                # Segment title card between non-contiguous segments in same video
                if seg_idx > 0:
                    card_path = os.path.join(tmp_dir, f"clip_{clip_index:04d}.mp4")
                    try:
                        width, height = get_video_dimensions(str(vp))
                        make_title_card(f"Segment {seg_num}", width, height, card_path)
                        clip_paths.append(card_path)
                        clip_index += 1
                        seg_num += 1
                    except Exception as exc:
                        _append_log(job_id, f"· Could not create segment {seg_num} card: {exc}")

                clip_path = os.path.join(tmp_dir, f"clip_{clip_index:04d}{vp.suffix}")
                try:
                    extract_clip(str(vp), start_sec, end_sec, clip_path)
                    clip_paths.append(clip_path)
                    clip_durations.append(end_sec - start_sec)
                    clip_index += 1
                except Exception as exc:
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

    duration = int(sum(clip_durations))

    result = {
        "path": output_path,
        "filename": output_filename,
        "clip_count": len(clip_durations),
        "duration_seconds": duration,
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
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })
```

- [ ] **Step 4: Update the `/generate` route — remove prompt requirement, update total count**

In the `generate()` route inside `create_app`, replace the existing function with:

```python
    @app.post("/generate")
    def generate():
        body = request.get_json() or {}
        folder = body.get("folder", "").strip()
        prompt = body.get("prompt", "").strip()   # optional — stored for library only
        mode = body.get("mode", "highlight")
        selections = body.get("selections", {})
        output_filename = body.get("output_filename", "sizzle_reel.mp4").strip()
        output_filename = Path(output_filename).name

        if not folder or not Path(folder).exists():
            return jsonify({"error": "Folder not found"}), 404

        try:
            check_ffmpeg()
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 500

        try:
            video_paths = scan_videos(folder)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 422

        selected_count = sum(1 for p in video_paths if selections.get(p.name))
        job_id = _new_job("generation", max(selected_count, 1))
        threading.Thread(
            target=_run_generation,
            args=(job_id, folder, mode, selections, prompt, output_filename),
            daemon=True,
        ).start()
        return jsonify({"job_id": job_id})
```

- [ ] **Step 5: Run full suite — all tests should now pass**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 6: Add one test for segment title cards within a video**

Add to `tests/test_app.py`:

```python
def test_segment_title_cards_inserted_within_video(client, tmp_path):
    """Segment title cards appear between non-contiguous clusters in the same video."""
    import time

    (tmp_path / "vid.mp4").touch()
    # Two lines with a gap line between them — will produce 2 segments
    (tmp_path / "vid.txt").write_text(
        "[0:05] Speaker: First line.\n"
        "[0:15] Speaker: Gap line.\n"
        "[0:25] Speaker: Second cluster.",
        encoding="utf-8",
    )

    with patch("app.extract_clip"), \
         patch("app.stitch_clips"), \
         patch("app.check_ffmpeg"), \
         patch("app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("app.make_title_card") as mock_card:

        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {
                "vid.mp4": [
                    "[0:05] Speaker: First line.",
                    "[0:25] Speaker: Second cluster.",
                ],
            },
            "prompt": "",
            "output_filename": "out.mp4",
        })
        assert resp.status_code == 200
        job_id = resp.get_json()["job_id"]

        for _ in range(25):
            status = client.get(f"/status/{job_id}").get_json()["status"]
            if status in ("done", "error", "cancelled"):
                break
            time.sleep(0.2)

        assert status == "done", f"Job ended in unexpected state: {status}"

    # One segment card between the two clusters (no cross-video card, only one video)
    assert mock_card.call_count == 1
    assert mock_card.call_args[0][0] == "Segment 1"
```

- [ ] **Step 7: Run the new test**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_segment_title_cards_inserted_within_video -v
```

Expected: PASS

- [ ] **Step 8: Run full suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 9: Commit**

```
git add app.py tests/test_app.py
git commit -m "feat: rewrite _run_generation with direct clip extraction and segment title cards"
```

---

### Task 4: HTML + CSS — analyze bar, remove footer prompt, per-line checkbox styles

**Files:**
- Modify: `templates/index.html`
- Modify: `static/style.css`

- [ ] **Step 1: Update `templates/index.html`**

**a) Remove the "Analyze Everything" button from the topbar** (find and delete this line):
```html
      <button id="btn-analyze-all" class="analyze-all-btn">✓ Analyze Everything</button>
```

**b) Add the analyze bar inside `#main-panel`, between `#transcript-header` and `#transcript-scroll`:**

Replace:
```html
        <div id="transcript-header">
          <span id="transcript-filename" class="transcript-filename"></span>
          <button id="btn-select-all" class="select-all-btn"></button>
        </div>
        <div id="transcript-scroll" class="transcript-scroll"></div>
```

With:
```html
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
```

**c) Simplify the footer — remove the prompt field:**

Replace the entire `<footer id="workspace-footer">` block with:
```html
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
```

- [ ] **Step 2: Update `static/style.css`**

**a) Remove the `.analyze-all-btn` rule** (find and delete these lines):
```css
.analyze-all-btn {
  background: transparent; border: 1px solid #2ecc71; color: #2ecc71;
  border-radius: 4px; padding: 4px 10px; font-size: 10px; cursor: pointer;
  font-family: inherit;
}
.analyze-all-btn:hover { background: #2ecc7122; }
```

**b) Add after the `#topbar-controls` rule:**
```css
/* ANALYZE BAR */
#analyze-bar {
  display: flex; gap: 8px; padding: 8px 14px;
  background: #13132a; border-bottom: 1px solid #2a2a4a;
  flex-shrink: 0;
}
.analyze-input { flex: 1; }
.btn-analyze {
  background: #1a4a8a; color: #8ab4f8; border: 1px solid #2a6abf;
  border-radius: 4px; padding: 5px 14px; font-size: 11px; cursor: pointer;
  font-family: inherit; white-space: nowrap;
}
.btn-analyze:hover { background: #1e5aa0; }
.btn-analyze:disabled { opacity: 0.5; cursor: default; }
.btn-generate:disabled { opacity: 0.4; cursor: default; }
```

**c) Update `.transcript-line-cb` to allow clicking individual lines:**

Find the existing `.transcript-line-cb` rule and replace it with:
```css
.transcript-line-cb {
  display: flex; align-items: flex-start; gap: 8px; padding: 5px 10px;
  border-bottom: 1px solid #1f1f38; cursor: pointer; user-select: none;
}
.transcript-line-cb:hover { background: #141430; }
.transcript-line-cb:last-child { border-bottom: none; }
```

- [ ] **Step 3: Verify the server starts without errors**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_index_returns_200 tests/test_app.py::test_index_returns_html -v
```

Expected: 2 PASSED

- [ ] **Step 4: Commit**

```
git add templates/index.html static/style.css
git commit -m "feat: add analyze bar, remove footer prompt, update checkbox line styles"
```

---

### Task 5: JS — analyze flow, `state.lastPrompt`, generate button enabled state

**Files:**
- Modify: `static/app.js`

**Context:** The current JS has:
- `$('btn-analyze-all').addEventListener(...)` — remove this (button is gone)
- `submitGenerate` reads `$('prompt-input')` — update to use `state.lastPrompt` and `$('analyze-input')`
- Generate button is always enabled — add `updateGenerateBtn()` to enable/disable it

- [ ] **Step 1: Add `lastPrompt` to the state object**

Find:
```javascript
const state = {
  folder: null,
  files: [],
  activeFile: null,
  mode: 'checkbox',
  checked: {},
  highlighted: {},
  currentJobId: null,
  resultJobId: null,
};
```

Replace with:
```javascript
const state = {
  folder: null,
  files: [],          // [{name, lines:[{raw, timestamp, text, seconds, minute_bucket}]}]
  activeFile: null,   // filename string
  mode: 'checkbox',   // 'checkbox' | 'highlight'
  checked: {},        // {filename: Set<raw_line_string>}
  highlighted: {},    // {filename: Set<raw_line_string>}
  currentJobId: null,
  resultJobId: null,
  lastPrompt: '',     // prompt used for the most recent Analyze call
};
```

- [ ] **Step 2: Remove the `btn-analyze-all` listener and add the analyze bar listeners**

Find and remove:
```javascript
$('btn-analyze-all').addEventListener('click', () => {
  submitGenerate('all', {});
});
```

Replace with:
```javascript
// ─── Analyze bar ──────────────────────────────────────────────────────────────
$('btn-analyze').addEventListener('click', runAnalyze);
$('analyze-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') runAnalyze();
});

async function runAnalyze() {
  const prompt = $('analyze-input').value.trim();
  if (!prompt) return;

  $('btn-analyze').textContent = 'Analyzing…';
  $('btn-analyze').disabled = true;
  $('analyze-input').disabled = true;
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

    state.lastPrompt = prompt;

    // Apply returned highlights to every file, replacing prior selections
    state.files.forEach(f => {
      const lines = data.highlights[f.name] || [];
      if (state.mode === 'checkbox') {
        state.checked[f.name] = new Set(lines);
      } else {
        state.highlighted[f.name] = new Set(lines);
      }
    });

    if (state.activeFile) renderTranscript(state.activeFile);
    state.files.forEach(f => refreshBadge(f.name));
    updateGenerateBtn();

  } catch (err) {
    $('analyze-error').textContent = 'Network error: ' + err.message;
    $('analyze-error').classList.remove('hidden');
  } finally {
    $('btn-analyze').textContent = 'Analyze';
    $('btn-analyze').disabled = false;
    $('analyze-input').disabled = false;
  }
}
```

- [ ] **Step 3: Add `updateGenerateBtn` and wire it up**

Add after the `runAnalyze` function:

```javascript
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

Then add `updateGenerateBtn()` calls in these existing functions (find each and add the call at the end):

In `checkAllInFile`:
```javascript
function checkAllInFile(filename) {
  const fileObj = state.files.find(f => f.name === filename);
  if (!fileObj) return;
  fileObj.lines.forEach(l => state.checked[filename].add(l.raw));
  renderTranscript(filename);
  refreshBadge(filename);
  updateGenerateBtn();
}
```

In `highlightAllInFile`:
```javascript
function highlightAllInFile(filename) {
  const fileObj = state.files.find(f => f.name === filename);
  if (!fileObj) return;
  fileObj.lines.forEach(l => state.highlighted[filename].add(l.raw));
  renderTranscript(filename);
  refreshBadge(filename);
  updateGenerateBtn();
}
```

In `showWorkspace`, add `updateGenerateBtn()` at the end:
```javascript
function showWorkspace() {
  showScreen('screen-workspace');
  $('topbar-controls').classList.remove('hidden');
  renderSidebar();
  if (state.files.length > 0) selectFile(state.files[0].name);
  updateGenerateBtn();
}
```

- [ ] **Step 4: Update `submitGenerate` to use `state.lastPrompt`**

Find the `submitGenerate` function and replace the prompt line:

Old:
```javascript
async function submitGenerate(mode, selections) {
  const prompt = $('prompt-input').value.trim();
  if (!prompt) { alert('Please enter a prompt before generating.'); return; }
```

New:
```javascript
async function submitGenerate(mode, selections) {
  const prompt = state.lastPrompt || $('analyze-input').value.trim();
```

(Remove the `alert` guard — prompt is no longer required at generate time.)

- [ ] **Step 5: Update `$('btn-generate')` listener to call `updateGenerateBtn` on mode switch too**

In the mode-toggle listener, add `updateGenerateBtn()` at the end:
```javascript
document.querySelectorAll('.mode-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.mode = btn.dataset.mode;
    if (state.activeFile) renderTranscript(state.activeFile);
    updateSelectAllBtn();
    updateGenerateBtn();
  });
});
```

- [ ] **Step 6: Run tests**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests PASS (JS changes are not unit-tested at this level)

- [ ] **Step 7: Commit**

```
git add static/app.js
git commit -m "feat: analyze flow, lastPrompt state, generate button enabled/disabled"
```

---

### Task 6: JS — Fix stacked event listener bug in highlight mode (AbortController)

**Files:**
- Modify: `static/app.js`

**Context:** Every call to `renderHighlightMode` adds new `mousedown`/`mousemove` listeners on `#transcript-scroll` without removing old ones. After N renders (file-switch, mode-switch), N listeners fire per event. Each one re-reads `hl.has(raw)` from the already-mutated Set, so toggling flip-flops — the line ends up in its original state. Fix: track a module-level `AbortController`; call `.abort()` before each render so the previous listeners auto-remove.

- [ ] **Step 1: Add the AbortController and fix `renderHighlightMode`**

Find the highlight-mode section (look for `let _dragActive = false;`) and replace from there through the end of `renderHighlightMode`:

```javascript
// ─── Highlight mode ───────────────────────────────────────────────────────────
let _dragActive = false;
let _dragSetTo = null;   // true = highlighting, false = un-highlighting
let _hlAbortController = null;  // cancels stale mousedown/mousemove listeners
document.addEventListener('mouseup', () => { _dragActive = false; });

function renderHighlightMode(fileObj) {
  const scroll = $('transcript-scroll');

  // Abort previous listeners before wiping innerHTML
  if (_hlAbortController) _hlAbortController.abort();
  _hlAbortController = new AbortController();
  const { signal } = _hlAbortController;

  scroll.innerHTML = '';
  if (!fileObj || fileObj.lines.length === 0) {
    scroll.textContent = 'No transcript available.';
    return;
  }

  fileObj.lines.forEach(line => {
    const lineEl = document.createElement('div');
    lineEl.className = 'transcript-line-hl' +
      (state.highlighted[fileObj.name].has(line.raw) ? ' highlighted' : '');
    lineEl.dataset.raw = line.raw;

    const bar = document.createElement('div');
    bar.className = 'hl-bar';

    const ts = document.createElement('div');
    ts.className = 'ts-hl';
    ts.textContent = line.timestamp;

    const text = document.createElement('div');
    text.className = 'line-text-hl';
    text.textContent = line.text;

    lineEl.appendChild(bar);
    lineEl.appendChild(ts);
    lineEl.appendChild(text);
    scroll.appendChild(lineEl);
  });

  // ── Drag-to-brush ──────────────────────────────────────────────────────────
  scroll.addEventListener('mousedown', e => {
    const lineEl = e.target.closest('.transcript-line-hl');
    if (!lineEl) return;
    e.preventDefault();
    _dragActive = true;
    const raw = lineEl.dataset.raw;
    const hl = state.highlighted[fileObj.name];
    _dragSetTo = !hl.has(raw);
    _applyHighlight(fileObj.name, lineEl, _dragSetTo);
    refreshBadge(fileObj.name);
    updateGenerateBtn();
  }, { signal });

  scroll.addEventListener('mousemove', e => {
    if (!_dragActive) return;
    const lineEl = e.target.closest('.transcript-line-hl');
    if (!lineEl) {
      const rect = scroll.getBoundingClientRect();
      const threshold = 40;
      if (e.clientY < rect.top + threshold) scroll.scrollTop -= 8;
      else if (e.clientY > rect.bottom - threshold) scroll.scrollTop += 8;
      return;
    }
    _applyHighlight(fileObj.name, lineEl, _dragSetTo);
    refreshBadge(fileObj.name);
    updateGenerateBtn();
  }, { signal });
}
```

- [ ] **Step 2: Run tests**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 3: Commit**

```
git add static/app.js
git commit -m "fix: use AbortController to prevent stacked highlight-mode listeners"
```

---

### Task 7: JS — Restore per-line checkboxes in checkbox mode

**Files:**
- Modify: `static/app.js`

**Context:** The current `renderCheckboxMode` has one checkbox per minute-group header only (per-line checkboxes were removed in a previous session). Restore them: each line gets its own checkbox, the minute-header checkbox remains as "select all in group". Clicking a line toggles only that line; clicking the header toggles all. DOM is mutated in-place on click (no full re-render) to prevent stacked-listener issues.

- [ ] **Step 1: Add `_updateHeaderCbState` helper just before `renderCheckboxMode`**

Find the `// ─── Checkbox mode ────` comment and insert this function before `renderCheckboxMode`:

```javascript
function _updateHeaderCbState(cbEl, lines, s) {
  const count = lines.filter(l => s.has(l.raw)).length;
  const all = count === lines.length;
  const some = count > 0 && !all;
  cbEl.className = 'cb-box' + (all ? ' checked' : some ? ' indeterminate' : '');
  cbEl.textContent = all ? '✓' : some ? '–' : '';
}
```

- [ ] **Step 2: Replace `renderCheckboxMode` entirely**

Find and replace the complete `renderCheckboxMode` function with:

```javascript
function renderCheckboxMode(fileObj) {
  const scroll = $('transcript-scroll');
  scroll.innerHTML = '';
  if (!fileObj || fileObj.lines.length === 0) {
    scroll.textContent = 'No transcript available.';
    return;
  }

  // Group by minute
  const groups = {};
  fileObj.lines.forEach(line => {
    const b = line.minute_bucket;
    if (!groups[b]) groups[b] = { label: `${b}:00 – ${b + 1}:00`, lines: [] };
    groups[b].lines.push(line);
  });

  const s = state.checked[fileObj.name];

  Object.values(groups).forEach(group => {
    const groupEl = document.createElement('div');
    groupEl.className = 'minute-group';

    // ── Minute header with select-all checkbox ─────────────────────────────
    const labelEl = document.createElement('div');
    labelEl.className = 'minute-label-cb';

    const headerCb = document.createElement('div');
    _updateHeaderCbState(headerCb, group.lines, s);

    const labelText = document.createElement('span');
    labelText.textContent = group.label;

    labelEl.appendChild(headerCb);
    labelEl.appendChild(labelText);

    labelEl.addEventListener('click', () => {
      const allChecked = group.lines.every(l => s.has(l.raw));
      if (allChecked) {
        group.lines.forEach(l => s.delete(l.raw));
      } else {
        group.lines.forEach(l => s.add(l.raw));
      }
      // Mutate DOM in place — no re-render
      group.lines.forEach(l => {
        const lineEl = groupEl.querySelector(`[data-line-raw="${CSS.escape(l.raw)}"]`);
        if (lineEl) {
          const cb = lineEl.querySelector('.cb-box-line');
          const checked = s.has(l.raw);
          cb.className = 'cb-box cb-box-line' + (checked ? ' checked' : '');
          cb.textContent = checked ? '✓' : '';
        }
      });
      _updateHeaderCbState(headerCb, group.lines, s);
      refreshBadge(fileObj.name);
      updateGenerateBtn();
    });

    groupEl.appendChild(labelEl);

    // ── Individual lines with per-line checkboxes ──────────────────────────
    group.lines.forEach(line => {
      const lineEl = document.createElement('div');
      lineEl.className = 'transcript-line-cb';
      lineEl.dataset.lineRaw = line.raw;

      const lineCb = document.createElement('div');
      lineCb.className = 'cb-box cb-box-line' + (s.has(line.raw) ? ' checked' : '');
      lineCb.textContent = s.has(line.raw) ? '✓' : '';

      const ts = document.createElement('div');
      ts.className = 'ts-cb';
      ts.textContent = line.timestamp;

      const text = document.createElement('div');
      text.className = 'line-text-cb';
      text.textContent = line.text;

      lineEl.appendChild(lineCb);
      lineEl.appendChild(ts);
      lineEl.appendChild(text);

      lineEl.addEventListener('click', () => {
        const checked = s.has(line.raw);
        if (checked) {
          s.delete(line.raw);
          lineCb.className = 'cb-box cb-box-line';
          lineCb.textContent = '';
        } else {
          s.add(line.raw);
          lineCb.className = 'cb-box cb-box-line checked';
          lineCb.textContent = '✓';
        }
        _updateHeaderCbState(headerCb, group.lines, s);
        refreshBadge(fileObj.name);
        updateGenerateBtn();
      });

      groupEl.appendChild(lineEl);
    });

    scroll.appendChild(groupEl);
  });
}
```

- [ ] **Step 3: Remove the old `checkAllInFile` call from `updateSelectAllBtn`**

The `checkAllInFile` function is still needed (called by `btn-select-all`). No change needed there — it calls `renderTranscript` which calls `renderCheckboxMode`, which rebuilds the DOM cleanly.

- [ ] **Step 4: Run full test suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```
git add static/app.js
git commit -m "feat: restore per-line checkboxes in checkbox mode with in-place DOM updates"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| Move prompt to analyze bar above transcript | Task 4 (HTML), Task 5 (JS) |
| Analyze button calls Claude, highlights lines | Task 2 (/analyze endpoint), Task 5 (runAnalyze) |
| Analyze replaces prior selections on re-run | Task 5 (state.files.forEach replaces Set) |
| Manual highlight/unhighlight after analyze | Task 6 (AbortController fix) |
| Unhighlighting must work | Task 6 (AbortController fix) |
| Generate uses selected lines 1:1, no Claude | Task 3 (_run_generation rewrite) |
| Segment title cards between gaps in same video | Task 3 (seg_idx > 0 → make_title_card) |
| Video-name title cards between videos | Task 3 (prev_vp logic, preserved) |
| Segment numbering is global across reel | Task 3 (seg_num counter, single video_segments loop) |
| Generate button disabled when nothing selected | Task 5 (updateGenerateBtn) |
| Per-line checkboxes in checkbox mode | Task 7 |
| Minute-group header checkbox | Task 7 (_updateHeaderCbState) |
| Prompt optional at generate time | Task 3 (/generate route, remove 400) |
| lastPrompt stored, passed to library | Task 5 (state.lastPrompt) |
| No modification to video_editor.py | ✓ (not touched) |
| No modification to transcriber.py | ✓ (not touched) |

All spec requirements covered. No placeholders found.
