# Reel Captions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add WebVTT captions to generated reels — toggled on/off by a remembered CC button in the library player, plus a "Download with captions" burn-in export.

**Architecture:** Caption text is derived from the selected transcript lines and re-timed onto the reel's `[title 5s][clip]…` timeline by one shared Python function (`captions.build_webvtt`), called in both server generation and the cloud `/plan` path. The VTT is stored beside the reel (local sidecar / R2 object) and served as `text/vtt` via a new endpoint; the library `<video>` loads it as a `<track>`. Burn-in re-encodes on demand — server ffmpeg locally, browser mediabunny in cloud.

**Tech Stack:** Python/Flask, pytest + unittest.mock, vanilla JS, mediabunny (browser encode), ffmpeg.

**Spec:** `docs/superpowers/specs/2026-07-13-reel-captions-design.md`

**Test command (bash tool):** `./venv/Scripts/python.exe -m pytest tests/ -q` (ffmpeg-dependent code is mocked in unit tests; run the app from PowerShell for live checks).

**Conventions in this repo:**
- Generate-flow tests MUST patch `generator_app._library_add` to avoid writing the real `sizzle_library.json`.
- Test suite runs `testing=True`, which executes generation synchronously.
- No JS test framework exists — JS/HTML/CSS tasks are verified live in the running app (Browser pane), matching how prior UI work was checked.
- `TITLE_CARD_DURATION` is an existing module constant in `generator_app.py` (5.0). `reel-encoder.js` has its own `TITLE_CARD_DURATION_SEC = 5.0`.

---

## Phase 1 — Soft captions (independently shippable)

### Task 1: `captions.py` — the shared WebVTT generator

**Files:**
- Create: `captions.py`
- Test: `tests/test_captions.py`

Pure functions, no Flask/ffmpeg imports — the timing logic in isolation.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_captions.py`:

```python
from captions import build_webvtt, collect_caption_lines, WEBVTT_MIME


def _seg(start, end, lines):
    return {"start_sec": start, "end_sec": end, "caption_lines": lines}


def test_mime_constant():
    assert WEBVTT_MIME == "text/vtt"


def test_single_segment_single_line_offsets_after_title_card():
    # Clip 0:10–0:16 (6s). Title card = 5s. A line at source 0:10 (clip start)
    # lands at reel t = 5.0; its cue runs to the clip end (5.0 + 6.0 = 11.0).
    vtt = build_webvtt([_seg(10.0, 16.0, [{"text": "Hello there", "seconds": 10.0}])],
                       title_card_duration=5.0)
    assert vtt.startswith("WEBVTT\n\n")
    assert "00:00:05.000 --> 00:00:11.000\nHello there" in vtt


def test_line_offset_within_clip():
    # Line at source 12.0 in a clip starting at 10.0 -> 2s into the clip ->
    # reel t = 5.0 + 2.0 = 7.0.
    vtt = build_webvtt([_seg(10.0, 16.0, [{"text": "Later line", "seconds": 12.0}])])
    assert "00:00:07.000 --> 00:00:11.000\nLater line" in vtt


def test_consecutive_lines_end_at_next_line_start():
    seg = _seg(10.0, 16.0, [
        {"text": "First", "seconds": 10.0},
        {"text": "Second", "seconds": 13.0},
    ])
    vtt = build_webvtt([seg])
    assert "00:00:05.000 --> 00:00:08.000\nFirst" in vtt        # ends at next line
    assert "00:00:08.000 --> 00:00:11.000\nSecond" in vtt       # ends at clip end


def test_second_segment_starts_after_first_segments_title_and_clip():
    # Seg1: title 5 + clip 6 = ends at reel 11. Seg2 title starts at 11,
    # clip starts at 16. A line at seg2 clip start -> reel 16.
    segs = [
        _seg(10.0, 16.0, [{"text": "one", "seconds": 10.0}]),
        _seg(30.0, 34.0, [{"text": "two", "seconds": 30.0}]),
    ]
    vtt = build_webvtt(segs)
    assert "00:00:16.000 --> 00:00:20.000\ntwo" in vtt


def test_cue_clamped_to_clip_end():
    # A line whose source time sits past end_sec still clamps into the clip.
    seg = _seg(10.0, 12.0, [{"text": "edge", "seconds": 11.9}])
    vtt = build_webvtt([seg])
    # clip 2s -> ends at reel 7.0; start 5.0 + 1.9 = 6.9, end clamps to 7.0
    assert "00:00:06.900 --> 00:00:07.000\nedge" in vtt


def test_no_lines_returns_none():
    assert build_webvtt([_seg(10.0, 16.0, [])]) is None
    assert build_webvtt([]) is None


def test_blank_text_lines_skipped():
    seg = _seg(10.0, 16.0, [{"text": "   ", "seconds": 10.0}])
    assert build_webvtt([seg]) is None


def test_collect_caption_lines_selected_respondent_in_range():
    all_lines = [
        {"raw": "a", "text": "picked", "seconds": 10.0, "is_interviewer": False},
        {"raw": "b", "text": "not picked", "seconds": 11.0, "is_interviewer": False},
        {"raw": "c", "text": "interviewer", "seconds": 12.0, "is_interviewer": True},
        {"raw": "d", "text": "out of range", "seconds": 99.0, "is_interviewer": False},
    ]
    selected = {"a", "c", "d"}
    got = collect_caption_lines(all_lines, selected, 10.0, 16.0)
    assert got == [{"text": "picked", "seconds": 10.0}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_captions.py -q`
Expected: collection error / all fail — `captions` module does not exist yet.

- [ ] **Step 3: Implement `captions.py`**

Create `captions.py`:

```python
"""WebVTT caption generation for reels.

Pure, framework-free: turns the selected transcript lines of a reel's segments
into a WebVTT string, re-timed onto the reel's [title 5s][clip]... timeline.
"""

WEBVTT_MIME = "text/vtt"

# Kept in sync with generator_app.TITLE_CARD_DURATION; passed explicitly so this
# module never imports the Flask app.
_DEFAULT_TITLE_CARD_DURATION = 5.0


def collect_caption_lines(all_lines, selected_raws, seg_start, seg_end):
    """Selected respondent lines whose source time falls in [seg_start, seg_end).

    Interviewer lines are excluded (captions show the respondent only, per spec),
    even if a user manually selected one.
    """
    return [
        {"text": line["text"], "seconds": line["seconds"]}
        for line in all_lines
        if line["raw"] in selected_raws
        and not line.get("is_interviewer")
        and seg_start <= line["seconds"] < seg_end
    ]


def _fmt_ts(sec: float) -> str:
    """Seconds -> WebVTT 'HH:MM:SS.mmm'."""
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def build_webvtt(segments, title_card_duration: float = _DEFAULT_TITLE_CARD_DURATION):
    """Build a WebVTT string from ordered reel segments, or None if no cues.

    Each segment dict needs: start_sec, end_sec (source clip range) and
    caption_lines (list of {"text", "seconds"} from collect_caption_lines).

    ponytail: assumes every segment is encoded (naive cumulative timeline). If a
    segment's extraction fails and assembly drops the pair, cues after it drift.
    Failure-aware re-timing is deliberately not built — dropped segments are rare
    and already produce a degraded reel. Upgrade path: pass the assembler's
    surviving segment_starts + clip_durations instead of recomputing here.
    """
    cues = []
    reel_t = 0.0
    for seg in segments:
        clip_dur = seg["end_sec"] - seg["start_sec"]
        clip_start = reel_t + title_card_duration
        clip_end = clip_start + clip_dur
        lines = seg.get("caption_lines", [])
        for i, line in enumerate(lines):
            cue_start = clip_start + (line["seconds"] - seg["start_sec"])
            if i + 1 < len(lines):
                cue_end = clip_start + (lines[i + 1]["seconds"] - seg["start_sec"])
            else:
                cue_end = clip_end
            cue_start = max(clip_start, min(cue_start, clip_end))
            cue_end = max(cue_start, min(cue_end, clip_end))
            text = (line["text"] or "").strip()
            if not text or cue_end <= cue_start:
                continue
            cues.append((cue_start, cue_end, text))
        reel_t = clip_end

    if not cues:
        return None
    body = "\n\n".join(f"{_fmt_ts(a)} --> {_fmt_ts(b)}\n{t}" for a, b, t in cues)
    return "WEBVTT\n\n" + body + "\n"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_captions.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add captions.py tests/test_captions.py
git commit -m "feat: captions.build_webvtt — derive reel WebVTT from transcript lines

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Attach caption lines to segments + write VTT in server generation

**Files:**
- Modify: `generator_app.py` — `_build_segment_list` (~317-364), `_run_generation_impl` (VTT write + library entry, around the result/library block ~701-730)
- Test: `tests/test_generator_app.py`

`_build_segment_list` currently discards the parsed `all_lines`. Carry them so each result segment gets `caption_lines`; then in server generation build the VTT and store it (local sidecar / cloud-fallback R2 object) and record the caption reference on the library entry.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_generator_app.py`:

```python
def test_build_segment_list_attaches_caption_lines(tmp_path):
    import generator_app
    from pathlib import Path

    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"")
    (tmp_path / "clip.txt").write_text(
        "[0:10] Guest: first selected line\n"
        "[0:12] Guest: second selected line\n"
        "[0:20] Guest: unselected\n",
        encoding="utf-8",
    )
    selections = {"clip.mp4": [
        "[0:10] Guest: first selected line",
        "[0:12] Guest: second selected line",
    ]}

    # get_video_duration is called per video; stub it so no ffmpeg runs.
    import unittest.mock as m
    with m.patch("generator_app.get_video_duration", return_value=60.0):
        segs = generator_app._build_segment_list([Path(vid)], selections)

    assert len(segs) == 1
    cl = segs[0]["caption_lines"]
    assert [c["text"] for c in cl] == ["first selected line", "second selected line"]
    assert [c["seconds"] for c in cl] == [10.0, 12.0]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./venv/Scripts/python.exe -m pytest tests/test_generator_app.py::test_build_segment_list_attaches_caption_lines -q`
Expected: FAIL with `KeyError: 'caption_lines'`.

- [ ] **Step 3: Carry `all_lines` through `_build_segment_list` and attach `caption_lines`**

In `generator_app.py`, add the import near the other local imports (top of file, after `from shared import ...`):

```python
from captions import build_webvtt, collect_caption_lines, WEBVTT_MIME
```

Replace the body of `_build_segment_list` (currently lines ~331-364) from `grouped = []` through the final `return result` with:

```python
    grouped = []
    for vp in video_paths:
        selected_raws = selections.get(vp.name, [])
        if not selected_raws:
            continue
        txt_path = vp.with_suffix(".txt")
        if not txt_path.exists():
            continue
        all_lines = _parse_transcript_lines(txt_path.read_text(encoding="utf-8"))
        ffmpeg_input = video_urls.get(vp.name, str(vp)) if video_urls else str(vp)
        duration = get_video_duration(ffmpeg_input)
        selected_set = set(selected_raws)
        segs = _group_lines_into_segments(all_lines, selected_set, video_duration=duration)
        if segs:
            grouped.append((vp, segs, ffmpeg_input, all_lines, selected_set))

    total_segs = sum(len(segs) for _, segs, _, _, _ in grouped)
    result = []
    seg_num = 0
    for vp, segs, ffmpeg_input, all_lines, selected_set in grouped:
        for start_sec, end_sec in segs:
            seg_num += 1
            result.append({
                "video_name": vp.name,
                "video_stem": vp.stem,
                "ffmpeg_input": ffmpeg_input,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "caption_lines": collect_caption_lines(
                    all_lines, selected_set, start_sec, end_sec),
                "title_lines": [
                    vp.stem,
                    f"from {_format_seconds(start_sec)}",
                    f"Segment {seg_num} / {total_segs}",
                ],
            })
    return result
```

- [ ] **Step 4: Run it to verify it passes**

Run: `./venv/Scripts/python.exe -m pytest tests/test_generator_app.py::test_build_segment_list_attaches_caption_lines -q`
Expected: PASS.

- [ ] **Step 5: Write the failing test for the local sidecar**

Add to `tests/test_generator_app.py` (uses the existing synchronous `testing=True` generate flow — match the arrangement of the current end-to-end generate test in this file; if a helper/fixture builds the app + selections, reuse it):

```python
def test_local_generation_writes_vtt_sidecar(tmp_path, monkeypatch):
    """Local generate writes {stem}.vtt beside the reel and records captions_filename."""
    import generator_app, unittest.mock as m

    # Stub the heavy media steps so no ffmpeg runs; make title cards + clips + stitch
    # all "succeed" by producing empty files at the paths the pipeline expects.
    # (Reuse whatever stubbing the existing generate test in this file already does;
    #  the assertion below is the new part.)
    captured = {}

    def fake_add(entry):
        captured["entry"] = entry

    monkeypatch.setattr(generator_app, "_library_add", fake_add)

    # ... arrange + run the synchronous generate exactly as the existing
    # end-to-end generate test does, with output_filename="reel.mp4",
    # selections that yield >=1 caption line, and folder=tmp_path ...

    sidecar = tmp_path / "reel.vtt"
    assert sidecar.exists()
    assert sidecar.read_text(encoding="utf-8").startswith("WEBVTT")
    assert captured["entry"]["captions_filename"] == "reel.vtt"
```

> **Implementer note:** model the arrange/run half on the existing end-to-end generate tests in `tests/test_generator_app.py` — `test_generation_result_includes_segment_starts` (line ~684) and `test_generation_result_includes_entry_id` (line ~1278). Both use the `client` fixture, patch `_library_add`, and run the synchronous (`testing=True`) generate with stubbed media steps. Reuse that exact stubbing; the only *new* assertions are the three lines above (sidecar exists, starts with WEBVTT, entry has `captions_filename`). Capture the entry by patching `_library_add` with `fake_add` as shown.

- [ ] **Step 6: Run it to verify it fails**

Run: `./venv/Scripts/python.exe -m pytest tests/test_generator_app.py::test_local_generation_writes_vtt_sidecar -q`
Expected: FAIL — no sidecar written, `captions_filename` missing.

- [ ] **Step 7: Build + store the VTT in `_run_generation_impl`**

In `generator_app.py`, `_run_generation_impl` builds `segments` at line ~419 (`segments = _build_segment_list(...)`) and later assembles the reel and builds `library_entry` (~701-730). Add VTT handling. Immediately **before** `_library_add(library_entry)` (line ~730), insert:

```python
        # ── Captions: derive a WebVTT track from the same segments ───────────
        vtt = build_webvtt(segments)
        if vtt:
            stem = Path(output_filename).stem
            if storage.is_cloud() and session_key:
                captions_key = f"{session_key}/{stem}.vtt"
                try:
                    tmp_vtt = Path(folder) / f"{stem}.vtt"
                    tmp_vtt.write_text(vtt, encoding="utf-8")
                    storage.upload_file(str(tmp_vtt), captions_key)
                    library_entry["captions_key"] = captions_key
                except Exception as exc:
                    _append_log(job_id, f"· Captions upload skipped: {exc}")
            else:
                try:
                    sidecar = Path(output_path).with_suffix(".vtt")
                    sidecar.write_text(vtt, encoding="utf-8")
                    library_entry["captions_filename"] = sidecar.name
                except Exception as exc:
                    _append_log(job_id, f"· Captions sidecar skipped: {exc}")
```

> `output_path` and `output_filename` are already local variables in this function (used to build `library_entry["path"]`/`["filename"]`). `Path(output_path).with_suffix(".vtt")` yields the sidecar beside the reel (`.../reel.mp4` → `.../reel.vtt`).

- [ ] **Step 8: Run the new tests + full generator suite**

Run: `./venv/Scripts/python.exe -m pytest tests/test_generator_app.py -q`
Expected: PASS (new tests green, existing generate tests unaffected — captions are additive and best-effort).

- [ ] **Step 9: Commit**

```bash
git add generator_app.py tests/test_generator_app.py
git commit -m "feat: server generation writes reel captions (local sidecar / cloud R2)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Cloud browser path — `/plan` returns VTT, encoder uploads it, `/library` records it

**Files:**
- Modify: `generator_app.py` — `plan()` (~905-980), `library_add_endpoint()` (~981-1003)
- Modify: `static/reel-encoder.js` — `generate()` (~167-274)
- Test: `tests/test_browser_endpoints.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_browser_endpoints.py`. This file's cloud tests use the `cloud_client` fixture; model the arrange half of the plan test on the existing `test_plan_returns_segment_list_with_correct_shape` (line ~63), which seeds a session with a `.txt` transcript + video and patches `storage` list/download/presigned. The only new part is the three caption asserts:

```python
def test_plan_returns_captions_fields(cloud_client, tmp_path, monkeypatch):
    # ... arrange exactly as test_plan_returns_segment_list_with_correct_shape ...
    resp = cloud_client.post("/plan", json={...})   # same body that test uses
    data = resp.get_json()
    assert data["captions_key"].endswith(".vtt")
    assert data["captions_vtt"].startswith("WEBVTT")
    assert "captions_put_url" in data


def test_library_records_captions_key(cloud_client, monkeypatch):
    import generator_app
    captured = {}
    monkeypatch.setattr(generator_app, "_library_add", lambda e: captured.setdefault("e", e))
    resp = cloud_client.post("/library", json={
        "session_key": "sessions/abc",
        "output_filename": "reel.mp4",
        "captions_key": "sessions/abc/reel.vtt",
    })
    assert resp.status_code == 200
    assert captured["e"]["captions_key"] == "sessions/abc/reel.vtt"
```

> **Implementer note:** fill the `...` in `test_plan_returns_captions_fields` by copying the arrange half of the existing `/plan` cloud test in this file. Only the three asserts are new.

- [ ] **Step 2: Run them to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_browser_endpoints.py -q -k "captions"`
Expected: FAIL — `/plan` returns no caption fields, `/library` ignores `captions_key`.

- [ ] **Step 3: Add caption fields to `/plan`**

In `generator_app.py` `plan()`, after `segments = _build_segment_list(...)` and the `if not segments:` guard (~946-948), and before building the JSON response, add:

```python
            captions_vtt = build_webvtt(segments)
            captions_key = None
            captions_put_url = None
            if captions_vtt:
                stem = Path(output_filename).stem
                captions_key = f"{session_key}/{stem}.vtt"
                captions_put_url = storage.presigned_put_url(captions_key, expires=7200)
```

Then add these three keys to the returned `jsonify({...})` dict (alongside `reel_key`/`presigned_put_url`):

```python
                "captions_vtt": captions_vtt,
                "captions_key": captions_key,
                "captions_put_url": captions_put_url,
```

- [ ] **Step 4: Record `captions_key` in `/library`**

In `library_add_endpoint()`, after the `entry = {...}` dict is built (~990-1001) and before `_library_add(entry)`, add:

```python
        captions_key = (body.get("captions_key") or "").strip()
        if captions_key:
            entry["captions_key"] = captions_key
```

- [ ] **Step 5: Run the Python tests**

Run: `./venv/Scripts/python.exe -m pytest tests/test_browser_endpoints.py -q`
Expected: PASS.

- [ ] **Step 6: Upload the VTT from the browser encoder**

In `static/reel-encoder.js` `generate()`, after the reel PUT succeeds (~247, right after `log('✓ Reel uploaded to cloud storage');`) insert the VTT upload:

```javascript
    // ── Upload the caption track (if the plan produced one) ─────────────────
    if (plan.captions_vtt && plan.captions_put_url) {
      const capResp = await fetch(plan.captions_put_url, {
        method: 'PUT',
        headers: { 'Content-Type': 'text/vtt' },
        body: plan.captions_vtt,
        signal,
      });
      if (capResp.ok) {
        log('✓ Captions uploaded');
      } else {
        log(`· Captions upload skipped (${capResp.status})`);
      }
    }
```

Then include `captions_key` in the `POST /library` body (in the `body: JSON.stringify({...})` block ~253-260) — but only when the VTT actually uploaded. Track it with a flag: replace the block above's `log('✓ Captions uploaded');` branch to also set a local `let captionsKey = null;` (declare it just before the upload block) to `plan.captions_key`, and add `captions_key: captionsKey,` to the library POST body.

Concretely, declare before the upload block:

```javascript
    let captionsKey = null;
```

set it inside the `if (capResp.ok)` branch:

```javascript
        captionsKey = plan.captions_key;
```

and add to the library POST body object:

```javascript
        captions_key: captionsKey,
```

- [ ] **Step 7: Syntax-check the encoder**

Run: `node --check static/reel-encoder.js`
Expected: no output (valid). (It's an ES module using imports — `node --check` parses it without executing.)

- [ ] **Step 8: Commit**

```bash
git add generator_app.py static/reel-encoder.js tests/test_browser_endpoints.py
git commit -m "feat: cloud browser path uploads + records reel captions

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `GET /library-captions/<id>` — serve the VTT as text/vtt

**Files:**
- Modify: `generator_app.py` — new route near `serve_library_video` (~1049)
- Test: `tests/test_generator_app.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_generator_app.py`:

```python
def test_library_captions_serves_local_sidecar(tmp_path, monkeypatch):
    import generator_app
    reel = tmp_path / "reel.mp4"
    reel.write_bytes(b"x")
    (tmp_path / "reel.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhi\n",
                                       encoding="utf-8")
    app = generator_app.create_app(testing=True)
    monkeypatch.setattr(generator_app, "_load_library", lambda: [
        {"id": "e1", "path": str(reel), "filename": "reel.mp4",
         "captions_filename": "reel.vtt"},
    ])
    c = app.test_client()
    resp = c.get("/library-captions/e1")
    assert resp.status_code == 200
    assert resp.mimetype == "text/vtt"
    assert b"WEBVTT" in resp.data


def test_library_captions_404_when_no_captions(monkeypatch):
    import generator_app
    app = generator_app.create_app(testing=True)
    monkeypatch.setattr(generator_app, "_load_library", lambda: [
        {"id": "e2", "path": "", "filename": "reel.mp4"},  # no caption fields
    ])
    resp = app.test_client().get("/library-captions/e2")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run them to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_generator_app.py -q -k "library_captions"`
Expected: FAIL — route does not exist (404 for both, wrong reason).

- [ ] **Step 3: Add the route**

In `generator_app.py`, immediately after the `serve_library_video` function (after its closing, ~1090, before `@app.get("/library")`), add:

```python
    @app.get("/library-captions/<entry_id>")
    def serve_library_captions(entry_id):
        entries = _load_library()
        entry = next((e for e in entries if e["id"] == entry_id), None)
        if not entry:
            return jsonify({"error": "not found"}), 404
        # Local sidecar first (works until the container restarts).
        fname = entry.get("captions_filename")
        if fname:
            sidecar = Path(entry["path"]).with_name(fname)
            if sidecar.is_file():
                return app.response_class(
                    sidecar.read_text(encoding="utf-8"), mimetype=WEBVTT_MIME)
        # Cloud: the VTT is tiny text — proxy it directly (unlike metered video).
        key = entry.get("captions_key")
        if key and storage.is_cloud():
            try:
                data = storage.read_file_bytes(key)
                return app.response_class(data, mimetype=WEBVTT_MIME)
            except Exception:
                return jsonify({"error": "captions not found"}), 404
        return jsonify({"error": "no captions"}), 404
```

> `Path(entry["path"]).with_name(fname)` resolves the sidecar beside the reel using the stored filename — robust to the reel living in any folder.
> This revives a real caller for `storage.read_file_bytes` (the ponytail audit had flagged it as caller-less — it now earns its keep for captions).

- [ ] **Step 4: Run the tests + full generator suite**

Run: `./venv/Scripts/python.exe -m pytest tests/test_generator_app.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add generator_app.py tests/test_generator_app.py
git commit -m "feat: GET /library-captions/<id> serves reel VTT (local + cloud)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Library player CC toggle (soft captions, browser-verified)

**Files:**
- Modify: `templates/index.html` — library player overlay (~246-258)
- Modify: `static/app.js` — `openLibraryPlayer` (~2249-2264), close handler (~2266)
- Modify: `static/style.css` — CC button style

- [ ] **Step 1: Add the CC button + track wiring to the overlay markup**

In `templates/index.html`, the library player overlay currently is:

```html
        <video id="library-video" controls style="width:100%;max-height:500px">
          <source id="library-source" src="" type="video/mp4">
        </video>
        <div class="seg-skip-controls" style="margin-top:8px">
          <button id="btn-lib-prev-seg" class="seg-skip-btn" title="Previous segment">← Prev</button>
          <button id="btn-lib-next-seg" class="seg-skip-btn" title="Next segment">Next →</button>
        </div>
```

Replace with (adds `crossorigin`, an empty `<track>` slot the JS fills, and a CC button in the controls row):

```html
        <video id="library-video" controls crossorigin="anonymous" style="width:100%;max-height:500px">
          <source id="library-source" src="" type="video/mp4">
          <track id="library-track" kind="captions" label="Captions" srclang="en">
        </video>
        <div class="seg-skip-controls" style="margin-top:8px">
          <button id="btn-lib-prev-seg" class="seg-skip-btn" title="Previous segment">← Prev</button>
          <button id="btn-lib-next-seg" class="seg-skip-btn" title="Next segment">Next →</button>
          <button id="btn-lib-cc" class="cc-btn hidden" type="button" aria-pressed="false" title="Toggle captions">CC</button>
        </div>
```

- [ ] **Step 2: Wire the toggle + remembered preference in `app.js`**

In `static/app.js`, replace `openLibraryPlayer` (~2249-2264) with:

```javascript
function _captionsOn() {
  return localStorage.getItem('sizzle_captions_on') === '1';
}

function _applyCcState(on) {
  const track = $('library-video').textTracks[0];
  if (track) track.mode = on ? 'showing' : 'hidden';
  const btn = $('btn-lib-cc');
  btn.classList.toggle('active', on);
  btn.setAttribute('aria-pressed', on ? 'true' : 'false');
}

function openLibraryPlayer(entry) {
  state.librarySegmentStarts = entry.segment_starts || [];
  const src = `${GENERATOR_URL}/library-video/${entry.id}`;
  const displayName = entry.title || entry.filename;
  $('library-player-meta').textContent = `${displayName} — "${entry.prompt}"`;

  // Captions: only wire the track + CC button when this reel has a caption file.
  const hasCaptions = !!(entry.captions_key || entry.captions_filename);
  const trackEl = $('library-track');
  const ccBtn = $('btn-lib-cc');
  if (hasCaptions) {
    trackEl.src = `${GENERATOR_URL}/library-captions/${entry.id}`;
    ccBtn.classList.remove('hidden');
  } else {
    trackEl.removeAttribute('src');
    ccBtn.classList.add('hidden');
  }

  _openModal('library-player-overlay', 'btn-close-player');
  $('library-video').src = src;
  $('library-video').load();

  // The text track exists after load(); apply the remembered on/off state.
  if (hasCaptions) {
    // textTracks[0] is available synchronously once the <track> has a src.
    _applyCcState(_captionsOn());
  }
}

$('btn-lib-cc').addEventListener('click', () => {
  const next = !_captionsOn();
  localStorage.setItem('sizzle_captions_on', next ? '1' : '0');
  _applyCcState(next);
});
```

- [ ] **Step 3: Style the CC button in `style.css`**

In `static/style.css`, after the `.seg-skip-btn` rules (~649-666), add:

```css
/* Captions toggle — ghost by default, amber-tint pill when on (mode-btn pattern).
   "CC" label keeps state non-color-only (WCAG 1.4.1). */
.cc-btn {
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--body);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.02em;
  padding: 7px 14px;
  border-radius: var(--radius-md);
  cursor: pointer;
  font-family: inherit;
  transition: background var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}
.cc-btn:hover { border-color: var(--border-strong); color: var(--ink); }
.cc-btn.active { background: var(--amber-tint); border-color: var(--amber-border); color: var(--amber-ink); }
.cc-btn:focus-visible { outline: none; box-shadow: 0 0 0 3px rgba(224,123,57,.22); }
```

- [ ] **Step 4: Syntax-check the JS**

Run: `node --check static/app.js`
Expected: no output (valid).

- [ ] **Step 5: Verify live in the running app**

Start the app from **PowerShell** (ffmpeg is on the PowerShell PATH):
`.\venv\Scripts\python.exe -c "from app import create_app; create_app().run(port=5000)"`
and the generator: `.\venv\Scripts\python.exe -c "from generator_app import create_app; create_app().run(port=5001)"`

In the Browser pane: open the Library tab, open a reel that has captions (generate one first from `test_videos` with a prompt that matches speech, e.g. "moments about food"). Verify via `read_page` / `javascript_tool`:
- `document.getElementById('btn-lib-cc')` is visible (not `.hidden`) for a captioned reel.
- Clicking it flips `document.getElementById('library-video').textTracks[0].mode` between `showing`/`hidden` and toggles `.active`.
- Reloading and reopening another reel preserves the choice (`localStorage.sizzle_captions_on`).
- For an old reel with no caption field, the CC button stays hidden.

Capture a screenshot with captions showing as proof.

- [ ] **Step 6: Commit**

```bash
git add templates/index.html static/app.js static/style.css
git commit -m "feat: library player CC toggle for reel captions (remembered)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

**Phase 1 is now shippable and verifiable on its own.** Phase 2 adds burn-in export.

---

## Phase 2 — Burn-in on download

### Task 6: Local burn-in endpoint — `POST /library/<id>/download-captioned`

**Files:**
- Modify: `generator_app.py` — new route after `serve_library_captions`
- Test: `tests/test_generator_app.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_generator_app.py`:

```python
def test_download_captioned_runs_ffmpeg_subtitles(tmp_path, monkeypatch):
    import generator_app, subprocess, unittest.mock as m
    reel = tmp_path / "reel.mp4"; reel.write_bytes(b"x")
    (tmp_path / "reel.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhi\n",
                                       encoding="utf-8")
    app = generator_app.create_app(testing=True)
    monkeypatch.setattr(generator_app, "_load_library", lambda: [
        {"id": "e1", "path": str(reel), "filename": "reel.mp4",
         "captions_filename": "reel.vtt"},
    ])

    calls = {}
    def fake_run(cmd, *a, **k):
        calls["cmd"] = cmd
        # Emulate ffmpeg producing the output file (last arg).
        Path(cmd[-1]).write_bytes(b"captioned")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(generator_app.subprocess, "run", fake_run)
    resp = app.test_client().post("/library/e1/download-captioned")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("video/mp4")
    # ffmpeg invoked with a subtitles filter referencing the VTT
    joined = " ".join(calls["cmd"])
    assert "subtitles" in joined
    assert "-vf" in calls["cmd"]


def test_download_captioned_404_without_captions(monkeypatch):
    import generator_app
    app = generator_app.create_app(testing=True)
    monkeypatch.setattr(generator_app, "_load_library", lambda: [
        {"id": "e2", "path": "/x/reel.mp4", "filename": "reel.mp4"},
    ])
    resp = app.test_client().post("/library/e2/download-captioned")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run them to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_generator_app.py -q -k "download_captioned"`
Expected: FAIL — route missing.

- [ ] **Step 3: Add the burn-in route (local only)**

In `generator_app.py`, after `serve_library_captions`, add:

```python
    @app.post("/library/<entry_id>/download-captioned")
    def download_captioned(entry_id):
        """Burn the reel's VTT into a downloadable MP4 (local mode only).

        Cloud mode burns in the browser via ReelEncoder.burnCaptions — the Render
        free tier deliberately does not re-encode video server-side.
        """
        if storage.is_cloud():
            return jsonify({"error": "cloud burn-in runs in the browser"}), 400
        entries = _load_library()
        entry = next((e for e in entries if e["id"] == entry_id), None)
        if not entry:
            return jsonify({"error": "not found"}), 404
        reel = Path(entry["path"])
        fname = entry.get("captions_filename")
        vtt = Path(reel).with_name(fname) if fname else None
        if not reel.is_file() or not vtt or not vtt.is_file():
            return jsonify({"error": "reel or captions missing"}), 404

        out_dir = Path(tempfile.mkdtemp(prefix="sizzle_cap_"))
        out_path = out_dir / f"{reel.stem}_captioned.mp4"
        # ffmpeg's subtitles filter needs a POSIX-style path with the colon after
        # the Windows drive letter escaped, quoted inside the filter string.
        vtt_arg = str(vtt).replace("\\", "/").replace(":", "\\:")
        style = "FontName=Arial,FontSize=22,PrimaryColour=&H00FFFFFF,BorderStyle=3,Outline=1,Shadow=0,BackColour=&H80000000"
        cmd = [
            "ffmpeg", "-y", "-i", str(reel),
            "-vf", f"subtitles='{vtt_arg}':force_style='{style}'",
            "-c:a", "copy", str(out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0 or not out_path.is_file():
            return jsonify({"error": "burn-in failed"}), 500
        return send_file(
            str(out_path), mimetype="video/mp4",
            as_attachment=True,
            download_name=f"{reel.stem}_captioned.mp4",
        )
```

> `subprocess`, `tempfile`, `send_file`, and `Path` are already imported in `generator_app.py`.

- [ ] **Step 4: Run the tests + full generator suite**

Run: `./venv/Scripts/python.exe -m pytest tests/test_generator_app.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add generator_app.py tests/test_generator_app.py
git commit -m "feat: local burn-in — POST /library/<id>/download-captioned (ffmpeg)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: "Download with captions" card action (local + cloud), browser-verified

**Files:**
- Modify: `static/reel-encoder.js` — new `burnCaptions` export
- Modify: `static/app.js` — `_renderCardBody` download control (~2002-2006 area)

- [ ] **Step 1: Add `burnCaptions` to the encoder**

In `static/reel-encoder.js`, add a helper that parses WebVTT into cues and a public `burnCaptions`. Add near the top helpers (after `_fadeOutGain`, ~53):

```javascript
// Parse a WebVTT string into [{start, end, text}] (seconds). Minimal parser:
// handles 'HH:MM:SS.mmm' and 'MM:SS.mmm' cue timings, ignores styling blocks.
function _parseVtt(vtt) {
  const cues = [];
  const toSec = t => {
    const parts = t.trim().split(':').map(parseFloat);
    return parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2]
                              : parts[0] * 60 + parts[1];
  };
  for (const block of vtt.split(/\n\n+/)) {
    const line = block.split('\n').find(l => l.includes('-->'));
    if (!line) continue;
    const [a, b] = line.split('-->');
    const text = block.split('\n').slice(block.split('\n').indexOf(line) + 1).join('\n').trim();
    if (text) cues.push({ start: toSec(a), end: toSec(b), text });
  }
  return cues;
}
```

Then add to the `window.ReelEncoder = { ... }` object a new method (after `generate`, ~274):

```javascript
  async burnCaptions(reelUrl, vttText, { onLog, onProgress, signal } = {}) {
    const log = onLog || console.log;
    const progress = onProgress || (() => {});
    const cues = _parseVtt(vttText);

    const input = new Input({ formats: ALL_FORMATS, source: new UrlSource(reelUrl) });
    const videoTrack = await input.getPrimaryVideoTrack();
    const audioTrack = await input.getPrimaryAudioTrack();
    const width = videoTrack.displayWidth, height = videoTrack.displayHeight;

    if (!(await canEncodeVideo('avc', { width, height, bitrate: VIDEO_BITRATE }))) {
      throw new Error('Browser cannot encode H.264 at this resolution');
    }

    const target = new BufferTarget();
    const output = new Output({ format: new Mp4OutputFormat({ fastStart: 'in-memory' }), target });
    const canvas = new OffscreenCanvas(width, height);
    const ctx = canvas.getContext('2d', { alpha: false });
    const videoSource = new CanvasSource(canvas, { codec: 'avc', bitrate: VIDEO_BITRATE });
    const audioSource = new AudioBufferSource({
      codec: 'aac', bitrate: AUDIO_BITRATE,
      transform: { numberOfChannels: CHANNELS, sampleRate: SAMPLE_RATE },
    });
    output.addVideoTrack(videoSource, { frameRate: FPS });
    output.addAudioTrack(audioSource);
    await output.start();

    const fontSize = Math.max(20, Math.floor(height / 22));
    try {
      const durationSec = await input.computeDuration();
      const totalFrames = Math.max(1, Math.round(durationSec * FPS));
      const sink = new CanvasSink(videoTrack, { width, height, fit: 'contain', poolSize: 2 });
      let f = 0;
      for await (const { canvas: frame, timestamp } of sink.canvases(0)) {
        _throwIfAborted(signal);
        ctx.drawImage(frame, 0, 0, width, height);
        const cue = cues.find(c => timestamp >= c.start && timestamp < c.end);
        if (cue) _drawCaption(ctx, cue.text, width, height, fontSize);
        await videoSource.add(f / FPS, 1 / FPS);
        if (++f % 15 === 0) progress(f, totalFrames);
        if (f >= totalFrames) break;
      }
      if (audioTrack) {
        const asink = new AudioBufferSink(audioTrack);
        for await (const { buffer } of asink.buffers()) {
          _throwIfAborted(signal);
          await audioSource.add(buffer);
        }
      }
      await output.finalize();
    } catch (err) {
      try { await output.cancel(); } catch { /* torn down */ }
      throw err;
    }
    log('✓ Captions burned in');
    return new Blob([target.buffer], { type: 'video/mp4' });
  },
```

And add the caption-drawing helper next to `_parseVtt`:

```javascript
// Draw one caption line, bottom-centre, white text on a translucent box.
function _drawCaption(ctx, text, width, height, fontSize) {
  ctx.font = `bold ${fontSize}px sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'bottom';
  const metrics = ctx.measureText(text);
  const padX = fontSize * 0.5, padY = fontSize * 0.3;
  const boxW = Math.min(width * 0.9, metrics.width + padX * 2);
  const y = height - fontSize;
  ctx.fillStyle = 'rgba(0,0,0,0.55)';
  ctx.fillRect((width - boxW) / 2, y - fontSize - padY, boxW, fontSize + padY * 2);
  ctx.fillStyle = '#fff';
  ctx.fillText(text, width / 2, y);
}
```

> `AudioBufferSink` is already imported at the top of the file (line ~32). `computeDuration`/`canvases`/`buffers` are mediabunny APIs already used by `_encodeClip`.

- [ ] **Step 2: Syntax-check the encoder**

Run: `node --check static/reel-encoder.js`
Expected: no output.

- [ ] **Step 3: Add the "Download with captions" action in `app.js`**

In `static/app.js` `_renderCardBody`, the plain download button is built at ~2002-2006. Immediately after the existing `downloadBtn.addEventListener` wiring for the plain download (find where `downloadBtn` gets its click handler — it triggers `/library-video/<id>?download=1`), add a second button, shown only when the entry has captions:

```javascript
  if (entry.captions_key || entry.captions_filename) {
    const capBtn = document.createElement('button');
    capBtn.className = 'reel-btn-icon';
    capBtn.title = 'Download with captions';
    capBtn.setAttribute('aria-label', 'Download with captions');
    capBtn.textContent = 'CC↓';
    capBtn.style.cssText = 'font-size:11px;font-weight:700';
    capBtn.addEventListener('click', () => _downloadCaptioned(entry, capBtn));
    iconRow.appendChild(capBtn);
  }
```

(Append `capBtn` into the same `iconRow` the download/edit/delete icons use.)

Then add the handler function near `openLibraryPlayer`:

```javascript
async function _downloadCaptioned(entry, btn) {
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = '…';
  try {
    if (APP_MODE === 'cloud' && window.ReelEncoder?.isSupported()) {
      // Fetch the reel URL + VTT, burn in-browser, download the blob.
      const vtt = await fetch(`${GENERATOR_URL}/library-captions/${entry.id}`).then(r => r.text());
      const reelUrl = `${GENERATOR_URL}/library-video/${entry.id}`;
      const blob = await window.ReelEncoder.burnCaptions(reelUrl, vtt, {
        onLog: () => {},
        onProgress: (d, t) => { btn.textContent = `${Math.round((d / t) * 100)}%`; },
      });
      _downloadBlob(blob, `${(entry.title || entry.filename).replace(/\.mp4$/, '')}_captioned.mp4`);
    } else {
      // Local mode: server ffmpeg burn-in, streamed as an attachment.
      const resp = await fetch(`${GENERATOR_URL}/library/${entry.id}/download-captioned`, { method: 'POST' });
      if (!resp.ok) throw new Error(`Burn-in failed (${resp.status})`);
      const blob = await resp.blob();
      _downloadBlob(blob, `${(entry.title || entry.filename).replace(/\.mp4$/, '')}_captioned.mp4`);
    }
  } catch (err) {
    alert(`Could not create captioned download: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

function _downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
```

> If a `_downloadBlob` helper already exists in `app.js`, reuse it instead of redefining — grep first (`grep -n "createObjectURL" static/app.js`).

- [ ] **Step 4: Syntax-check**

Run: `node --check static/app.js`
Expected: no output.

- [ ] **Step 5: Verify live**

With both servers running (PowerShell), in the Browser pane:
- **Local mode:** open the Library, on a captioned reel click the "CC↓" button; confirm a `*_captioned.mp4` downloads. Open it and confirm captions are burned in (or, headless: assert the POST returns 200 + `video/mp4` and a non-empty body via `read_network_requests`).
- Confirm the CC↓ button is absent on reels without captions.
- **Cloud path** (if a cloud env is configured): repeat; the burn runs in-browser (progress % ticks on the button) and downloads the blob. If no cloud env is available, note that the cloud path is covered by `node --check` + the shared `burnCaptions` code review only, and defer the live cloud check.

Capture a screenshot / network log as proof.

- [ ] **Step 6: Commit**

```bash
git add static/reel-encoder.js static/app.js
git commit -m "feat: 'Download with captions' burn-in (local ffmpeg / cloud mediabunny)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Run the full suite: `./venv/Scripts/python.exe -m pytest tests/ -q` — all green.
- [ ] Confirm `git status` is clean and the branch history reads as 7 focused commits.
- [ ] Update `CLAUDE.md`: add `captions.py` to the shared-modules list, note the `captions_filename`/`captions_key` library fields, the `/library-captions/<id>` endpoint, and the `/library/<id>/download-captioned` endpoint. Commit as `docs: document captions feature in CLAUDE.md`.
