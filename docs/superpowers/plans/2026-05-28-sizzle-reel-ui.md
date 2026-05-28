# Sizzle Reel Web UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local Flask web UI that wraps the existing sizzle reel pipeline, letting users pick a folder, select transcript content via checkboxes or highlighting, submit a prompt, and watch the generated reel play back in the browser — with a persistent library of all past reels.

**Architecture:** A Flask app (`app.py`) exposes REST endpoints; a single HTML page (`templates/index.html`) + vanilla JS (`static/app.js`) drives the full UI. Long-running operations (transcription, generation) run in background threads; the frontend polls `/status/<job_id>` every 2 seconds for progress. The existing pipeline modules (`claude_client.py`, `transcriber.py`, `video_editor.py`, `loader.py`, `timestamp_parser.py`) are called from `app.py` unchanged.

**Tech Stack:** Python 3.11+, Flask 2.x, threading (stdlib), uuid (stdlib), tkinter (stdlib), vanilla JS (no framework), pytest + Flask test client

---

## File Structure

```
Sizzle Reel/
├── app.py                        # NEW: Flask app factory, all routes, job system, library I/O
├── sizzle_library.json           # NEW: auto-created on first reel generation
├── requirements.txt              # MODIFY: add flask>=2.0
├── templates/
│   └── index.html                # NEW: single-page HTML
├── static/
│   ├── style.css                 # NEW: all styles
│   └── app.js                    # NEW: all frontend JS
├── tests/
│   ├── test_app.py               # NEW: Flask route + job + library tests
│   └── (existing tests unchanged)
└── (all existing .py files unchanged)
```

---

## Task 1: Flask skeleton + requirements

**Files:**
- Modify: `requirements.txt`
- Create: `app.py`
- Create: `templates/index.html`
- Create: `static/style.css`
- Create: `static/app.js`
- Create: `tests/test_app.py`

- [ ] **Step 1: Add Flask to requirements.txt**

Replace contents of `requirements.txt`:
```
anthropic
openai-whisper
pytest
flask>=2.0
```

- [ ] **Step 2: Install Flask**

Run:
```powershell
.\venv\Scripts\python.exe -m pip install flask>=2.0
```

Expected: Flask installs without error.

- [ ] **Step 3: Write the failing test**

Create `tests/test_app.py`:
```python
import pytest
from app import create_app


@pytest.fixture
def client():
    app = create_app(testing=True)
    with app.test_client() as c:
        yield c


def test_index_returns_200(client):
    resp = client.get("/")
    assert resp.status_code == 200


def test_index_returns_html(client):
    resp = client.get("/")
    assert b"<!DOCTYPE html>" in resp.data or b"<html" in resp.data
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -v`

Expected: `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 5: Create app.py skeleton**

Create `app.py`:
```python
import json
import os
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from claude_client import query_claude
from loader import scan_videos
from timestamp_parser import parse_timestamps
from transcriber import transcribe_video
from video_editor import check_ffmpeg, extract_clip, parse_timestamp_to_seconds, stitch_clips

LIBRARY_PATH = Path(__file__).parent / "sizzle_library.json"

_jobs: dict = {}
_jobs_lock = threading.Lock()
_library_lock = threading.Lock()
_whisper_model = None
_model_lock = threading.Lock()


def create_app(testing: bool = False) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = testing
    return app
```

- [ ] **Step 6: Create minimal index.html**

Create `templates/index.html`:
```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sizzle Reel</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <div id="app">Loading...</div>
  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 7: Create empty static files**

Create `static/style.css` (empty for now — content added in Task 7).
Create `static/app.js` (empty for now — content added in Tasks 8–12).

- [ ] **Step 8: Register the GET / route in create_app**

Update `app.py` — replace the `create_app` function body:
```python
def create_app(testing: bool = False) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = testing

    @app.get("/")
    def index():
        return render_template("index.html")

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5000)
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -v`

Expected: 2 tests PASSED.

- [ ] **Step 10: Commit**

```
git add app.py templates/index.html static/style.css static/app.js requirements.txt tests/test_app.py
git commit -m "feat: add Flask skeleton and static file structure"
```

---

## Task 2: Folder picker + load-folder endpoints

**Files:**
- Modify: `app.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:
```python
import os
from unittest.mock import patch, MagicMock
from pathlib import Path


def test_load_folder_returns_video_list(client, tmp_path):
    (tmp_path / "video1.mp4").touch()
    (tmp_path / "video2.mp4").touch()
    (tmp_path / "notes.txt").write_text("[0:01] Speaker: hi", encoding="utf-8")
    resp = client.post("/load-folder", json={"folder": str(tmp_path)})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "job_id" in data
    assert set(data["files"]) == {"video1.mp4", "video2.mp4"}


def test_load_folder_missing_folder_returns_404(client):
    resp = client.post("/load-folder", json={"folder": "/nonexistent/folder/xyz"})
    assert resp.status_code == 404


def test_load_folder_no_videos_returns_422(client, tmp_path):
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    resp = client.post("/load-folder", json={"folder": str(tmp_path)})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_load_folder_returns_video_list -v`

Expected: FAIL — route not found.

- [ ] **Step 3: Add helper functions and /browse + /load-folder routes to app.py**

Add after the `_model_lock` line and before `create_app`, insert these helpers:

```python
def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        with _model_lock:
            if _whisper_model is None:
                import whisper as _whisper
                _whisper_model = _whisper.load_model("base")
    return _whisper_model


def _new_job(job_type: str, total: int) -> str:
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {
            "type": job_type,
            "status": "running",
            "total": total,
            "done": 0,
            "log": [],
            "result": None,
            "error": None,
            "cancel": threading.Event(),
        }
    return job_id


def _append_log(job_id: str, message: str) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["log"].append(message)


def _pick_directory() -> str | None:
    """Open a native OS folder dialog. Returns the selected path or None."""
    import tkinter as tk
    from tkinter import filedialog
    result: dict = {"path": None}

    def run() -> None:
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", True)
        result["path"] = filedialog.askdirectory(parent=root) or None
        root.destroy()

    t = threading.Thread(target=run)
    t.start()
    t.join()
    return result["path"]
```

Then inside `create_app`, after the `@app.get("/")` route, add:

```python
    @app.post("/browse")
    def browse():
        path = _pick_directory()
        if path is None:
            return jsonify({"path": None})
        return jsonify({"path": path})

    @app.post("/load-folder")
    def load_folder():
        folder = (request.get_json() or {}).get("folder", "").strip()
        if not folder or not Path(folder).exists():
            return jsonify({"error": "Folder not found"}), 404
        try:
            video_paths = scan_videos(folder)
        except ValueError as e:
            return jsonify({"error": str(e)}), 422

        filenames = [p.name for p in video_paths]
        needs_transcription = [p for p in video_paths
                                if not p.with_suffix(".txt").exists()
                                or p.with_suffix(".txt").stat().st_size == 0]

        if not needs_transcription:
            return jsonify({"job_id": None, "files": filenames, "folder": folder})

        job_id = _new_job("transcription", len(needs_transcription))

        def _transcribe():
            model = _get_whisper_model()
            for i, vp in enumerate(needs_transcription):
                if _jobs[job_id]["cancel"].is_set():
                    _jobs[job_id]["status"] = "cancelled"
                    return
                _append_log(job_id, f"⟳ {vp.name} — transcribing...")
                try:
                    transcript = transcribe_video(str(vp), model=model)
                    vp.with_suffix(".txt").write_text(transcript, encoding="utf-8")
                    _append_log(job_id, f"✓ {vp.name} — done")
                except Exception as exc:
                    _append_log(job_id, f"✗ {vp.name} — failed: {exc}")
                with _jobs_lock:
                    _jobs[job_id]["done"] = i + 1
            with _jobs_lock:
                _jobs[job_id]["status"] = "done"
                _jobs[job_id]["result"] = {"folder": folder, "files": filenames}

        threading.Thread(target=_transcribe, daemon=True).start()
        return jsonify({"job_id": job_id, "files": filenames, "folder": folder})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -v`

Expected: all tests PASSED (the transcription thread won't actually fire in the test because the files already have `.txt` from the `touch()` not matching — the test creates `.mp4` files with no `.txt`, so a job is started but since Whisper is mocked via no actual call in the test, we need to ensure the test doesn't hang).

Note: The `test_load_folder_returns_video_list` test creates `.mp4` files but no `.txt` files, so `_transcribe` starts in a background thread. The route returns immediately with the job_id. The test only checks the response — it won't hang. The background thread will fail silently trying to transcribe empty files, which is fine for testing.

- [ ] **Step 5: Commit**

```
git add app.py tests/test_app.py
git commit -m "feat: add folder picker and load-folder endpoint"
```

---

## Task 3: Status endpoint + job management

**Files:**
- Modify: `app.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:
```python
def test_status_returns_job_state(client):
    # Manually inject a job
    from app import _jobs, _jobs_lock
    import threading
    job_id = "test-job-123"
    with _jobs_lock:
        _jobs[job_id] = {
            "type": "transcription",
            "status": "running",
            "total": 3,
            "done": 1,
            "log": ["✓ video1.mp4 — done"],
            "result": None,
            "error": None,
            "cancel": threading.Event(),
        }
    resp = client.get(f"/status/{job_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "running"
    assert data["done"] == 1
    assert data["total"] == 3
    assert "✓ video1.mp4 — done" in data["log"]


def test_status_unknown_job_returns_404(client):
    resp = client.get("/status/nonexistent-id")
    assert resp.status_code == 404


def test_cancel_job(client):
    from app import _jobs, _jobs_lock
    import threading
    job_id = "cancel-test-456"
    cancel_event = threading.Event()
    with _jobs_lock:
        _jobs[job_id] = {
            "type": "generation",
            "status": "running",
            "total": 2,
            "done": 0,
            "log": [],
            "result": None,
            "error": None,
            "cancel": cancel_event,
        }
    resp = client.delete(f"/jobs/{job_id}")
    assert resp.status_code == 200
    assert cancel_event.is_set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_status_returns_job_state -v`

Expected: FAIL — route not found.

- [ ] **Step 3: Add /status and /jobs routes inside create_app**

Add after the `/load-folder` route inside `create_app`:

```python
    @app.get("/status/<job_id>")
    def job_status(job_id):
        with _jobs_lock:
            job = _jobs.get(job_id)
        if job is None:
            return jsonify({"error": "not found"}), 404
        return jsonify({
            "type": job["type"],
            "status": job["status"],
            "total": job["total"],
            "done": job["done"],
            "log": list(job["log"]),
            "result": job["result"],
            "error": job["error"],
        })

    @app.delete("/jobs/<job_id>")
    def cancel_job(job_id):
        with _jobs_lock:
            job = _jobs.get(job_id)
        if job:
            job["cancel"].set()
            with _jobs_lock:
                _jobs[job_id]["status"] = "cancelled"
        return jsonify({"ok": True})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -v`

Expected: all tests PASSED.

- [ ] **Step 5: Commit**

```
git add app.py tests/test_app.py
git commit -m "feat: add job status and cancel endpoints"
```

---

## Task 4: Transcript data endpoint

**Files:**
- Modify: `app.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:
```python
def test_group_by_minute_buckets_lines():
    from app import _group_by_minute
    lines = [
        {"timestamp": "0:05", "seconds": 5.0,  "minute_bucket": 0, "raw": "a", "text": "a"},
        {"timestamp": "0:50", "seconds": 50.0, "minute_bucket": 0, "raw": "b", "text": "b"},
        {"timestamp": "1:10", "seconds": 70.0, "minute_bucket": 1, "raw": "c", "text": "c"},
    ]
    groups = _group_by_minute(lines)
    assert len(groups) == 2
    assert groups[0]["label"] == "0:00 – 1:00"
    assert len(groups[0]["lines"]) == 2
    assert groups[1]["label"] == "1:00 – 2:00"
    assert len(groups[1]["lines"]) == 1


def test_transcripts_endpoint_returns_structured_data(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:05] Speaker: Hello world.\n[1:10] Speaker: Second line.",
        encoding="utf-8"
    )
    resp = client.get(f"/transcripts?folder={tmp_path}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["files"]) == 1
    f = data["files"][0]
    assert f["name"] == "vid.mp4"
    assert len(f["lines"]) == 2
    assert f["lines"][0]["timestamp"] == "0:05"
    assert f["lines"][0]["minute_bucket"] == 0
    assert f["lines"][1]["minute_bucket"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_group_by_minute_buckets_lines -v`

Expected: `ImportError: cannot import name '_group_by_minute'`

- [ ] **Step 3: Add helpers and /transcripts route to app.py**

Add these functions after `_pick_directory` (before `create_app`):

```python
import re as _re
_LINE_RE = _re.compile(r'^\[(\d+:\d{2})\]\s+\w+:\s+(.*)')


def _parse_transcript_lines(raw_text: str) -> list[dict]:
    lines = []
    for raw in raw_text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        m = _LINE_RE.match(raw)
        if not m:
            continue
        ts, text = m.group(1), m.group(2)
        seconds = parse_timestamp_to_seconds(ts)
        lines.append({
            "raw": raw,
            "timestamp": ts,
            "text": text,
            "seconds": seconds,
            "minute_bucket": int(seconds) // 60,
        })
    return lines


def _group_by_minute(lines: list[dict]) -> list[dict]:
    buckets: dict[int, list] = {}
    for line in lines:
        b = line["minute_bucket"]
        buckets.setdefault(b, []).append(line)
    result = []
    for b in sorted(buckets):
        result.append({
            "bucket": b,
            "label": f"{b}:00 – {b + 1}:00",
            "lines": buckets[b],
        })
    return result
```

Then add inside `create_app` after the `/jobs/<job_id>` route:

```python
    @app.get("/transcripts")
    def get_transcripts():
        folder = request.args.get("folder", "").strip()
        if not folder or not Path(folder).exists():
            return jsonify({"error": "Folder not found"}), 404
        try:
            video_paths = scan_videos(folder)
        except ValueError as e:
            return jsonify({"error": str(e)}), 422
        files = []
        for vp in video_paths:
            txt_path = vp.with_suffix(".txt")
            if not txt_path.exists():
                lines = []
            else:
                lines = _parse_transcript_lines(txt_path.read_text(encoding="utf-8"))
            files.append({"name": vp.name, "lines": lines})
        return jsonify({"files": files})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -v`

Expected: all tests PASSED.

- [ ] **Step 5: Commit**

```
git add app.py tests/test_app.py
git commit -m "feat: add transcript data endpoint with minute-grouping"
```

---

## Task 5: Generate endpoint + generation job

**Files:**
- Modify: `app.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:
```python
def test_generate_returns_job_id(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")

    with patch("app.query_claude", return_value="0:05-0:10"), \
         patch("app.extract_clip"), \
         patch("app.stitch_clips"), \
         patch("app.check_ffmpeg"):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {"vid.mp4": ["[0:05] Speaker: Hello."]},
            "prompt": "greetings",
            "output_filename": "out.mp4",
        })
    assert resp.status_code == 200
    data = resp.get_json()
    assert "job_id" in data


def test_generate_missing_prompt_returns_400(client, tmp_path):
    resp = client.post("/generate", json={
        "folder": str(tmp_path),
        "mode": "highlight",
        "selections": {},
        "output_filename": "out.mp4",
    })
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_generate_returns_job_id -v`

Expected: FAIL — route not found.

- [ ] **Step 3: Add generation logic to app.py**

Add this function before `create_app`:

```python
def _load_library() -> list:
    if not LIBRARY_PATH.exists():
        return []
    try:
        with LIBRARY_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_library(entries: list) -> None:
    with LIBRARY_PATH.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def _library_add(entry: dict) -> None:
    with _library_lock:
        entries = _load_library()
        entries.insert(0, entry)
        _save_library(entries)


def _run_generation(job_id: str, folder: str, mode: str,
                    selections: dict, prompt: str, output_filename: str) -> None:
    job = _jobs[job_id]
    try:
        video_paths = scan_videos(folder)
    except Exception as exc:
        with _jobs_lock:
            job["status"] = "error"
            job["error"] = str(exc)
        return

    video_segments: list[tuple] = []

    for i, vp in enumerate(video_paths):
        if job["cancel"].is_set():
            with _jobs_lock:
                job["status"] = "cancelled"
            return

        if mode == "all":
            txt = vp.with_suffix(".txt")
            if not txt.exists():
                continue
            transcript = txt.read_text(encoding="utf-8")
        else:
            lines = selections.get(vp.name, [])
            if not lines:
                continue
            transcript = "\n".join(lines)

        _append_log(job_id, f"⟳ {vp.name} — analyzing...")
        try:
            response = query_claude(transcript, prompt)
            segments = parse_timestamps(response)
        except Exception as exc:
            _append_log(job_id, f"✗ {vp.name} — API error: {exc}")
            with _jobs_lock:
                job["done"] = i + 1
            continue

        if segments:
            _append_log(job_id, f"✓ {vp.name} — found: {', '.join(segments)}")
            video_segments.append((vp, segments))
        else:
            _append_log(job_id, f"· {vp.name} — no relevant segments")

        with _jobs_lock:
            job["done"] = i + 1

    if not video_segments:
        with _jobs_lock:
            job["status"] = "error"
            job["error"] = "No relevant segments found in any video"
        return

    _append_log(job_id, "· Extracting clips...")
    output_path = str(Path(folder) / output_filename)

    with tempfile.TemporaryDirectory() as tmp_dir:
        clip_paths: list[str] = []
        clip_index = 0
        for vp, segments in video_segments:
            for seg in segments:
                start_str, end_str = seg.split("-")
                start_sec = parse_timestamp_to_seconds(start_str)
                end_sec = parse_timestamp_to_seconds(end_str)
                clip_path = os.path.join(tmp_dir, f"clip_{clip_index:04d}{vp.suffix}")
                try:
                    extract_clip(str(vp), start_sec, end_sec, clip_path)
                    clip_paths.append(clip_path)
                    clip_index += 1
                except Exception as exc:
                    _append_log(job_id, f"✗ {vp.name} [{seg}] — extraction failed: {exc}")

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

    duration = int(sum(
        parse_timestamp_to_seconds(s.split("-")[1]) - parse_timestamp_to_seconds(s.split("-")[0])
        for _, segs in video_segments for s in segs
    ))

    result = {
        "path": output_path,
        "filename": output_filename,
        "clip_count": len(clip_paths),
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
        "clip_count": len(clip_paths),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })
```

Then add inside `create_app`:

```python
    @app.post("/generate")
    def generate():
        body = request.get_json() or {}
        folder = body.get("folder", "").strip()
        prompt = body.get("prompt", "").strip()
        mode = body.get("mode", "highlight")
        selections = body.get("selections", {})
        output_filename = body.get("output_filename", "sizzle_reel.mp4").strip()

        if not prompt:
            return jsonify({"error": "prompt is required"}), 400
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

        job_id = _new_job("generation", len(video_paths))
        threading.Thread(
            target=_run_generation,
            args=(job_id, folder, mode, selections, prompt, output_filename),
            daemon=True,
        ).start()
        return jsonify({"job_id": job_id})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -v`

Expected: all tests PASSED.

- [ ] **Step 5: Commit**

```
git add app.py tests/test_app.py
git commit -m "feat: add generate endpoint and background generation job"
```

---

## Task 6: Video serving + library endpoints

**Files:**
- Modify: `app.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:
```python
def test_library_starts_empty(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.LIBRARY_PATH", tmp_path / "lib.json")
    resp = client.get("/library")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_library_delete_removes_entry(client, tmp_path, monkeypatch):
    import app as app_module
    lib_path = tmp_path / "lib.json"
    monkeypatch.setattr(app_module, "LIBRARY_PATH", lib_path)
    # Seed one entry
    lib_path.write_text(json.dumps([{"id": "abc123", "filename": "x.mp4"}]), encoding="utf-8")
    resp = client.delete("/library/abc123")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    remaining = json.loads(lib_path.read_text(encoding="utf-8"))
    assert remaining == []


def test_video_endpoint_not_found(client):
    resp = client.get("/video/nonexistent-job-id")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_library_starts_empty -v`

Expected: FAIL — route not found.

- [ ] **Step 3: Add video serving and library routes inside create_app**

Add inside `create_app` after the `/generate` route:

```python
    @app.get("/video/<job_id>")
    def serve_video(job_id):
        with _jobs_lock:
            job = _jobs.get(job_id)
        if not job or not job.get("result"):
            return jsonify({"error": "not found"}), 404
        path = Path(job["result"]["path"])
        if not path.is_file():
            return jsonify({"error": "file not found on disk"}), 404
        return send_file(str(path), conditional=True)

    @app.get("/library-video/<entry_id>")
    def serve_library_video(entry_id):
        entries = _load_library()
        entry = next((e for e in entries if e["id"] == entry_id), None)
        if not entry:
            return jsonify({"error": "not found"}), 404
        path = Path(entry["path"])
        if not path.is_file():
            return jsonify({"error": "file not found on disk"}), 404
        return send_file(str(path), conditional=True)

    @app.get("/library")
    def get_library():
        return jsonify(_load_library())

    @app.delete("/library/<entry_id>")
    def delete_library_entry(entry_id):
        with _library_lock:
            entries = _load_library()
            entries = [e for e in entries if e["id"] != entry_id]
            _save_library(entries)
        return jsonify({"ok": True})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -v`

Expected: all tests PASSED.

- [ ] **Step 5: Run the full test suite**

Run: `.\venv\Scripts\python.exe -m pytest tests/ -v`

Expected: all existing tests still pass, all new tests pass.

- [ ] **Step 6: Commit**

```
git add app.py tests/test_app.py
git commit -m "feat: add video serving and library CRUD endpoints"
```

---

## Task 7: HTML structure and CSS

**Files:**
- Modify: `templates/index.html`
- Modify: `static/style.css`

No automated tests — verify manually by running the server and opening the browser.

- [ ] **Step 1: Write index.html**

Replace `templates/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sizzle Reel</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<div id="app">

  <!-- TOP NAV -->
  <header id="topbar">
    <span class="logo">🎬 SIZZLE REEL</span>
    <nav class="nav-tabs">
      <button class="nav-tab active" data-tab="create">✦ Create</button>
      <button class="nav-tab" data-tab="library">📼 Library</button>
    </nav>
    <!-- Shown only in Create tab, after folder loaded -->
    <div id="topbar-controls" class="hidden">
      <span id="folder-badge" class="folder-badge"></span>
      <button id="btn-analyze-all" class="analyze-all-btn">✓ Analyze Everything</button>
      <div class="mode-toggle">
        <button class="mode-btn active" data-mode="checkbox">☑ Checkbox</button>
        <button class="mode-btn" data-mode="highlight">✦ Highlight</button>
      </div>
    </div>
  </header>

  <!-- CREATE TAB -->
  <main id="tab-create" class="tab-panel">

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

    <!-- TRANSCRIPTION PROGRESS SCREEN -->
    <div id="screen-transcribing" class="screen hidden">
      <div class="center-card">
        <h2>Preparing transcripts...</h2>
        <p id="transcribe-subtitle" class="subtitle"></p>
        <div class="progress-bar-wrap">
          <div id="transcribe-bar" class="progress-bar" style="width:0%"></div>
        </div>
        <div id="transcribe-log" class="log-box"></div>
      </div>
    </div>

    <!-- WORKSPACE SCREEN -->
    <div id="screen-workspace" class="screen hidden workspace-layout">

      <aside id="sidebar">
        <div class="sidebar-header">Video Files</div>
        <ul id="sidebar-list"></ul>
      </aside>

      <section id="main-panel">
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
          <div class="footer-field footer-field-grow">
            <label class="footer-label">Prompt</label>
            <input id="prompt-input" type="text" class="footer-input prompt-input" placeholder="e.g. best bites of black cod...">
          </div>
          <div class="footer-field footer-field-btn">
            <label class="footer-label">&nbsp;</label>
            <button id="btn-generate" class="btn-generate">▶ Generate Reel</button>
          </div>
        </footer>
      </section>

    </div>

    <!-- GENERATION PROGRESS SCREEN -->
    <div id="screen-generating" class="screen hidden">
      <div class="center-card">
        <h2>⏳ Generating your sizzle reel...</h2>
        <p class="subtitle">Analyzing transcripts with Claude, then extracting clips</p>
        <div class="progress-bar-wrap">
          <div id="gen-bar" class="progress-bar" style="width:0%"></div>
        </div>
        <div id="gen-log" class="log-box"></div>
        <button id="btn-cancel-gen" class="btn-secondary" style="margin-top:12px">Cancel</button>
      </div>
    </div>

    <!-- RESULT SCREEN -->
    <div id="screen-result" class="screen hidden">
      <div class="result-layout">
        <div class="video-wrap">
          <video id="result-video" controls>
            <source id="result-source" src="" type="video/mp4">
          </video>
        </div>
        <div class="result-controls">
          <div class="result-meta">
            <div id="result-filename" class="result-filename"></div>
            <div id="result-info" class="result-info"></div>
          </div>
          <div class="result-actions">
            <button id="btn-new-reel" class="btn-primary">+ New Reel</button>
            <button id="btn-open-folder" class="btn-secondary">📂 Open Folder</button>
          </div>
        </div>
      </div>
    </div>

  </main>

  <!-- LIBRARY TAB -->
  <main id="tab-library" class="tab-panel hidden">
    <div class="library-toolbar">
      <span id="library-count" class="library-count"></span>
    </div>
    <div id="library-grid" class="library-grid"></div>

    <!-- Library video player overlay -->
    <div id="library-player-overlay" class="overlay hidden">
      <div class="overlay-card">
        <button id="btn-close-player" class="overlay-close">✕</button>
        <video id="library-video" controls style="width:100%;max-height:500px">
          <source id="library-source" src="" type="video/mp4">
        </video>
        <div id="library-player-meta" style="padding:8px 0;font-size:12px;color:#888"></div>
      </div>
    </div>
  </main>

</div>
<script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write style.css**

Replace `static/style.css`:

```css
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: #111;
  color: #e0e0e0;
  font-family: 'Courier New', monospace;
  font-size: 13px;
  height: 100vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

#app { display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

/* TOP BAR */
#topbar {
  background: #16213e;
  border-bottom: 1px solid #2a2a4a;
  padding: 8px 16px;
  display: flex;
  align-items: center;
  gap: 14px;
  flex-shrink: 0;
  height: 48px;
}

.logo { font-weight: bold; color: #e94560; font-size: 14px; letter-spacing: 1px; }

.nav-tabs { display: flex; gap: 2px; background: #0f1729; border-radius: 6px; padding: 2px; }
.nav-tab {
  padding: 4px 14px; border-radius: 4px; font-size: 11px; cursor: pointer;
  color: #888; background: transparent; border: none; font-family: inherit;
}
.nav-tab.active { background: #e94560; color: white; }

#topbar-controls { display: flex; align-items: center; gap: 10px; margin-left: 4px; }

.folder-badge {
  background: #0f3460; border: 1px solid #2a4a8a; border-radius: 4px;
  padding: 3px 10px; font-size: 11px; color: #8ab4f8;
}

.analyze-all-btn {
  background: transparent; border: 1px solid #2ecc71; color: #2ecc71;
  border-radius: 4px; padding: 4px 10px; font-size: 10px; cursor: pointer;
  font-family: inherit;
}
.analyze-all-btn:hover { background: #2ecc7122; }

.mode-toggle { display: flex; background: #0f1729; border: 1px solid #2a2a4a; border-radius: 16px; padding: 2px; }
.mode-btn {
  padding: 4px 14px; border-radius: 14px; font-size: 10px; cursor: pointer;
  color: #888; background: transparent; border: none; font-family: inherit;
  transition: background 0.15s, color 0.15s;
}
.mode-btn.active { background: #e94560; color: white; }

/* TAB PANELS */
.tab-panel { flex: 1; overflow: hidden; display: flex; flex-direction: column; }
.tab-panel.hidden { display: none; }

/* SCREENS */
.screen { flex: 1; overflow: auto; display: flex; flex-direction: column; }
.screen.hidden { display: none !important; }

/* FOLDER PICKER + TRANSCRIBING SCREENS */
.center-card {
  margin: auto; padding: 40px; max-width: 500px; width: 100%;
  display: flex; flex-direction: column; gap: 12px; align-items: flex-start;
}
.center-card h2 { color: #fff; font-size: 18px; }
.subtitle { color: #888; font-size: 12px; }
.folder-input-row { display: flex; gap: 8px; width: 100%; }
.folder-path-input {
  flex: 1; background: #0f1729; border: 1px solid #2a2a4a;
  border-radius: 4px; padding: 6px 10px; color: #ccc; font-family: inherit;
  font-size: 12px;
}
.error-msg { color: #e94560; font-size: 11px; }

.progress-bar-wrap { width: 100%; background: #0f1729; border-radius: 4px; height: 6px; overflow: hidden; }
.progress-bar { height: 100%; background: linear-gradient(90deg, #e94560, #f39c12); border-radius: 4px; transition: width 0.3s; }

.log-box {
  width: 100%; background: #0a0a18; border: 1px solid #2a2a4a; border-radius: 6px;
  padding: 10px 12px; font-size: 10px; color: #556; line-height: 1.9;
  max-height: 180px; overflow-y: auto; font-family: monospace;
}
.log-done { color: #2ecc71; }
.log-active { color: #f39c12; }
.log-error { color: #e94560; }
.log-info { color: #8ab4f8; }

/* WORKSPACE */
.workspace-layout { flex: 1; display: flex; overflow: hidden; min-height: 0; }

#sidebar {
  width: 180px; background: #141428; border-right: 1px solid #2a2a4a;
  display: flex; flex-direction: column; flex-shrink: 0; overflow: hidden;
}
.sidebar-header {
  padding: 8px 12px; font-size: 9px; color: #555; text-transform: uppercase;
  letter-spacing: 1px; border-bottom: 1px solid #2a2a4a; flex-shrink: 0;
}
#sidebar-list { flex: 1; overflow-y: auto; list-style: none; }
.sidebar-item {
  padding: 8px 12px; cursor: pointer; border-left: 3px solid transparent;
  font-size: 11px; color: #888; display: flex; flex-direction: column; gap: 3px;
}
.sidebar-item:hover { background: #1a1a40; }
.sidebar-item.active { border-left-color: #e94560; color: #fff; background: #0f1a3a; }
.sidebar-item .item-name { font-weight: bold; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sidebar-item .item-badge { font-size: 9px; color: #556; }
.badge-checked { color: #2ecc71; }
.badge-highlighted { color: #f39c12; }

#main-panel { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }

#transcript-header {
  padding: 8px 14px; background: #16213e; border-bottom: 1px solid #2a2a4a;
  display: flex; align-items: center; justify-content: space-between; flex-shrink: 0;
}
.transcript-filename { color: #8ab4f8; font-weight: bold; font-size: 11px; }
.select-all-btn {
  background: transparent; border: none; cursor: pointer; font-size: 10px;
  font-family: inherit;
}
.select-all-btn.checkbox-mode { color: #2ecc71; text-decoration: underline; }
.select-all-btn.checkbox-mode:hover { color: #27ae60; }
.select-all-btn.highlight-mode { color: #f39c12; text-decoration: underline; }
.select-all-btn.highlight-mode:hover { color: #e67e22; }

.transcript-scroll { flex: 1; overflow-y: auto; padding: 10px 14px; }

/* CHECKBOX MODE */
.minute-group { border: 1px solid #2a2a4a; border-radius: 6px; margin-bottom: 8px; overflow: hidden; }
.minute-label {
  background: #1a1a3a; padding: 5px 10px; font-size: 9px; color: #556;
  text-transform: uppercase; letter-spacing: 0.5px; display: flex;
  align-items: center; gap: 8px; border-bottom: 1px solid #2a2a4a;
}
.check-all-group {
  margin-left: auto; color: #2ecc71; text-decoration: underline;
  background: none; border: none; cursor: pointer; font-size: 9px; font-family: inherit;
}

.transcript-line-cb {
  display: flex; align-items: flex-start; gap: 8px; padding: 5px 10px;
  border-bottom: 1px solid #1f1f38; cursor: pointer; user-select: none;
}
.transcript-line-cb:last-child { border-bottom: none; }
.transcript-line-cb:hover { background: #1a1a30; }

.cb-box {
  width: 14px; height: 14px; border: 1.5px solid #444; border-radius: 3px;
  flex-shrink: 0; margin-top: 1px; display: flex; align-items: center;
  justify-content: center; font-size: 10px; transition: background 0.1s;
}
.cb-box.checked { background: #2ecc71; border-color: #2ecc71; color: white; }

.ts-cb { color: #e94560; width: 36px; font-size: 10px; flex-shrink: 0; }
.line-text-cb { color: #ccc; font-size: 10px; line-height: 1.5; }

/* HIGHLIGHT MODE */
.transcript-line-hl {
  display: flex; align-items: flex-start; gap: 8px; padding: 5px 8px;
  border-radius: 5px; margin-bottom: 3px; cursor: pointer;
  border: 1px solid transparent; transition: background 0.1s; user-select: none;
}
.transcript-line-hl:hover { background: #1f1f3a; }
.transcript-line-hl.highlighted { background: #f39c1222; border-color: #f39c1255; }

.hl-bar {
  width: 4px; border-radius: 2px; background: transparent;
  flex-shrink: 0; align-self: stretch; min-height: 16px;
}
.transcript-line-hl.highlighted .hl-bar { background: #f39c12; }

.ts-hl { color: #e94560; width: 36px; font-size: 10px; flex-shrink: 0; padding-top: 1px; }
.line-text-hl { color: #ccc; font-size: 10px; line-height: 1.5; }
.transcript-line-hl.highlighted .line-text-hl { color: #f5d79e; }

/* WORKSPACE FOOTER */
#workspace-footer {
  background: #16213e; border-top: 1px solid #2a2a4a;
  padding: 10px 14px; display: flex; gap: 10px; align-items: flex-end; flex-shrink: 0;
}
.footer-field { display: flex; flex-direction: column; gap: 3px; }
.footer-field-grow { flex: 1; }
.footer-label { font-size: 9px; color: #556; text-transform: uppercase; letter-spacing: 0.5px; }
.footer-input {
  background: #0f1729; border: 1px solid #2a2a4a; border-radius: 4px;
  padding: 5px 8px; color: #ccc; font-family: monospace; font-size: 11px;
}
.filename-input { width: 160px; }
.prompt-input { width: 100%; }
.btn-generate {
  background: #e94560; color: white; border: none; border-radius: 4px;
  padding: 6px 16px; font-size: 11px; font-weight: bold; cursor: pointer;
  font-family: inherit; white-space: nowrap;
}
.btn-generate:hover { background: #c73652; }

/* RESULT SCREEN */
.result-layout { display: flex; flex-direction: column; height: 100%; }
.video-wrap { flex: 1; background: #000; min-height: 0; }
.video-wrap video { width: 100%; height: 100%; object-fit: contain; display: block; }
.result-controls {
  background: #16213e; border-top: 1px solid #2a2a4a;
  padding: 10px 14px; display: flex; justify-content: space-between; align-items: center;
  flex-shrink: 0;
}
.result-filename { color: #8ab4f8; font-weight: bold; font-size: 12px; }
.result-info { color: #556; font-size: 10px; margin-top: 2px; }
.result-actions { display: flex; gap: 8px; }

/* LIBRARY */
.library-toolbar {
  padding: 12px 16px; border-bottom: 1px solid #2a2a4a;
  background: #16213e; flex-shrink: 0;
}
.library-count { font-size: 10px; color: #556; text-transform: uppercase; letter-spacing: 1px; }
#library-grid {
  flex: 1; overflow-y: auto; padding: 14px;
  display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px;
}
.reel-card {
  background: #141428; border: 1px solid #2a2a4a; border-radius: 8px;
  overflow: hidden; display: flex; flex-direction: column;
}
.reel-card:hover { border-color: #e9456077; }
.reel-thumb {
  background: #0a0a1a; height: 90px; display: flex; align-items: center;
  justify-content: center; position: relative; cursor: pointer;
}
.reel-thumb:hover .reel-play-icon { opacity: 1; }
.reel-play-icon { font-size: 28px; color: #e94560; opacity: 0.7; transition: opacity 0.15s; }
.reel-duration {
  position: absolute; bottom: 5px; right: 7px;
  background: #000a; color: #ccc; font-size: 8px; padding: 1px 5px; border-radius: 2px;
}
.reel-body { padding: 8px 10px; flex: 1; display: flex; flex-direction: column; gap: 3px; }
.reel-name { font-size: 11px; color: #fff; font-weight: bold; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.reel-meta { font-size: 9px; color: #556; }
.reel-prompt { font-size: 9px; color: #8ab4f8; font-style: italic; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px; }
.reel-actions { display: flex; gap: 4px; margin-top: 6px; }
.reel-btn {
  font-size: 9px; padding: 3px 7px; border-radius: 3px;
  border: 1px solid #333; background: transparent; color: #888;
  cursor: pointer; font-family: inherit;
}
.reel-btn:hover { background: #1a1a30; color: #ccc; }
.reel-btn.play { border-color: #e94560; color: #e94560; }
.reel-btn.play:hover { background: #e9456022; }
.library-empty { grid-column: 1/-1; text-align: center; color: #444; padding: 60px; font-size: 14px; }

/* OVERLAY */
.overlay {
  position: fixed; inset: 0; background: #000b; display: flex;
  align-items: center; justify-content: center; z-index: 100;
}
.overlay.hidden { display: none; }
.overlay-card {
  background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 10px;
  padding: 16px; max-width: 800px; width: 90%; position: relative;
}
.overlay-close {
  position: absolute; top: 10px; right: 12px;
  background: transparent; border: none; color: #888; font-size: 16px;
  cursor: pointer; font-family: inherit;
}
.overlay-close:hover { color: #fff; }

/* SHARED BUTTONS */
.btn-primary {
  background: #e94560; color: white; border: none; border-radius: 4px;
  padding: 6px 14px; font-size: 11px; cursor: pointer; font-family: inherit;
}
.btn-primary:hover { background: #c73652; }
.btn-secondary {
  background: transparent; border: 1px solid #444; color: #888;
  border-radius: 4px; padding: 6px 14px; font-size: 11px;
  cursor: pointer; font-family: inherit;
}
.btn-secondary:hover { background: #1a1a30; color: #ccc; }

.hidden { display: none !important; }
```

- [ ] **Step 3: Start the server and verify visually**

Run:
```powershell
.\venv\Scripts\python.exe app.py
```

Open `http://localhost:5000` in a browser.

Expected: The folder picker screen appears with dark theme, no JS errors in the browser console.

- [ ] **Step 4: Commit**

```
git add templates/index.html static/style.css
git commit -m "feat: add full HTML structure and CSS styles"
```

---

## Task 8: Frontend JS — app shell, folder flow, transcription polling

**Files:**
- Modify: `static/app.js`

No automated tests — verify manually.

- [ ] **Step 1: Write the app shell and folder flow in app.js**

Replace `static/app.js`:

```javascript
// ─── State ────────────────────────────────────────────────────────────────────
const state = {
  folder: null,
  files: [],          // [{name, lines:[{raw, timestamp, text, seconds, minute_bucket}]}]
  activeFile: null,   // filename string
  mode: 'checkbox',   // 'checkbox' | 'highlight'
  checked: {},        // {filename: Set<raw_line_string>}
  highlighted: {},    // {filename: Set<raw_line_string>}
  currentJobId: null,
  resultJobId: null,
};

// ─── DOM refs ─────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

// ─── Navigation ───────────────────────────────────────────────────────────────
document.querySelectorAll('.nav-tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.nav-tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const tab = btn.dataset.tab;
    $('tab-create').classList.toggle('hidden', tab !== 'create');
    $('tab-library').classList.toggle('hidden', tab !== 'library');
    if (tab === 'library') loadLibrary();
  });
});

// ─── Screen helpers ───────────────────────────────────────────────────────────
function showScreen(id) {
  ['screen-folder-picker','screen-transcribing','screen-workspace',
   'screen-generating','screen-result'].forEach(s => {
    $(s).classList.toggle('hidden', s !== id);
  });
}

// ─── Folder picker ────────────────────────────────────────────────────────────
$('btn-browse').addEventListener('click', async () => {
  const resp = await fetch('/browse', { method: 'POST' });
  const { path } = await resp.json();
  if (path) $('folder-path-input').value = path;
});

$('btn-load-folder').addEventListener('click', () => {
  const folder = $('folder-path-input').value.trim();
  if (!folder) return;
  openFolder(folder);
});

$('folder-path-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    const folder = e.target.value.trim();
    if (folder) openFolder(folder);
  }
});

async function openFolder(folder) {
  $('folder-error').classList.add('hidden');
  const resp = await fetch('/load-folder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folder }),
  });
  const data = await resp.json();
  if (!resp.ok) {
    $('folder-error').textContent = data.error || 'Failed to open folder';
    $('folder-error').classList.remove('hidden');
    return;
  }
  state.folder = folder;
  state.files = [];
  state.checked = {};
  state.highlighted = {};

  $('folder-badge').textContent = '📁 ' + folder.split(/[\\/]/).pop() + '/';
  $('output-filename').value = folder.split(/[\\/]/).pop() + '_sizzle.mp4';

  if (data.job_id) {
    // Needs transcription
    showScreen('screen-transcribing');
    $('topbar-controls').classList.add('hidden');
    pollTranscription(data.job_id, data.files, folder);
  } else {
    await loadTranscripts(folder);
    showWorkspace();
  }
}

// ─── Transcription polling ────────────────────────────────────────────────────
function pollTranscription(jobId, files, folder) {
  const total = files.length;
  let lastLogLen = 0;

  const interval = setInterval(async () => {
    const resp = await fetch(`/status/${jobId}`);
    const job = await resp.json();

    const pct = total > 0 ? Math.round((job.done / total) * 100) : 0;
    $('transcribe-bar').style.width = pct + '%';
    $('transcribe-subtitle').textContent = `Transcribing ${job.done} / ${total} videos...`;

    const newLines = job.log.slice(lastLogLen);
    newLines.forEach(msg => appendLog('transcribe-log', msg));
    lastLogLen = job.log.length;

    if (job.status === 'done') {
      clearInterval(interval);
      await loadTranscripts(folder);
      showWorkspace();
    } else if (job.status === 'error' || job.status === 'cancelled') {
      clearInterval(interval);
      appendLog('transcribe-log', `✗ ${job.error || 'Cancelled'}`);
    }
  }, 2000);
}

async function loadTranscripts(folder) {
  const resp = await fetch(`/transcripts?folder=${encodeURIComponent(folder)}`);
  const data = await resp.json();
  state.files = data.files;
  state.files.forEach(f => {
    if (!state.checked[f.name]) state.checked[f.name] = new Set();
    if (!state.highlighted[f.name]) state.highlighted[f.name] = new Set();
  });
}

function showWorkspace() {
  showScreen('screen-workspace');
  $('topbar-controls').classList.remove('hidden');
  renderSidebar();
  if (state.files.length > 0) selectFile(state.files[0].name);
}

// ─── Log helper ───────────────────────────────────────────────────────────────
function appendLog(boxId, msg) {
  const box = $(boxId);
  const div = document.createElement('div');
  if (msg.startsWith('✓')) div.className = 'log-done';
  else if (msg.startsWith('⟳')) div.className = 'log-active';
  else if (msg.startsWith('✗')) div.className = 'log-error';
  else div.className = 'log-info';
  div.textContent = msg;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

// ─── Mode toggle ──────────────────────────────────────────────────────────────
document.querySelectorAll('.mode-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.mode = btn.dataset.mode;
    if (state.activeFile) renderTranscript(state.activeFile);
    updateSelectAllBtn();
  });
});

// ─── Analyze Everything ───────────────────────────────────────────────────────
$('btn-analyze-all').addEventListener('click', () => {
  submitGenerate('all', {});
});
```

- [ ] **Step 2: Start the server and test folder loading**

Run: `.\venv\Scripts\python.exe app.py`

Open `http://localhost:5000`. Click "Browse..." — a native folder dialog should appear. Select a folder with video files. Expected: transcription screen appears if transcripts are missing; workspace appears if already transcribed.

- [ ] **Step 3: Commit**

```
git add static/app.js
git commit -m "feat: add folder flow and transcription progress JS"
```

---

## Task 9: Frontend JS — sidebar and checkbox mode

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Append sidebar and checkbox rendering to app.js**

Append to `static/app.js`:

```javascript
// ─── Sidebar ──────────────────────────────────────────────────────────────────
function renderSidebar() {
  const list = $('sidebar-list');
  list.innerHTML = '';
  state.files.forEach(f => {
    const li = document.createElement('li');
    li.className = 'sidebar-item' + (f.name === state.activeFile ? ' active' : '');
    li.dataset.name = f.name;

    const nameDiv = document.createElement('div');
    nameDiv.className = 'item-name';
    nameDiv.textContent = f.name;

    const badgeDiv = document.createElement('div');
    badgeDiv.className = 'item-badge';
    badgeDiv.id = `badge-${CSS.escape(f.name)}`;
    updateBadgeEl(badgeDiv, f.name);

    li.appendChild(nameDiv);
    li.appendChild(badgeDiv);
    li.addEventListener('click', () => selectFile(f.name));
    list.appendChild(li);
  });
}

function updateBadgeEl(el, filename) {
  const cb = state.checked[filename]?.size || 0;
  const hl = state.highlighted[filename]?.size || 0;
  if (state.mode === 'checkbox') {
    el.innerHTML = cb > 0 ? `<span class="badge-checked">${cb} checked</span>` : '0 checked';
  } else {
    el.innerHTML = hl > 0 ? `<span class="badge-highlighted">${hl} highlighted</span>` : 'none highlighted';
  }
}

function refreshBadge(filename) {
  const el = document.getElementById(`badge-${CSS.escape(filename)}`);
  if (el) updateBadgeEl(el, filename);
}

function selectFile(filename) {
  state.activeFile = filename;
  $('transcript-filename').textContent = filename.replace(/\.[^.]+$/, '.txt');
  document.querySelectorAll('.sidebar-item').forEach(li => {
    li.classList.toggle('active', li.dataset.name === filename);
  });
  renderTranscript(filename);
  updateSelectAllBtn();
}

function updateSelectAllBtn() {
  const btn = $('btn-select-all');
  if (state.mode === 'checkbox') {
    btn.textContent = 'check all';
    btn.className = 'select-all-btn checkbox-mode';
    btn.onclick = () => checkAllInFile(state.activeFile);
  } else {
    btn.textContent = 'highlight all';
    btn.className = 'select-all-btn highlight-mode';
    btn.onclick = () => highlightAllInFile(state.activeFile);
  }
}

// ─── Checkbox mode ────────────────────────────────────────────────────────────
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

  Object.values(groups).forEach(group => {
    const groupEl = document.createElement('div');
    groupEl.className = 'minute-group';

    const labelEl = document.createElement('div');
    labelEl.className = 'minute-label';
    labelEl.textContent = group.label;

    const checkAllBtn = document.createElement('button');
    checkAllBtn.className = 'check-all-group';
    checkAllBtn.textContent = 'check all';
    checkAllBtn.addEventListener('click', e => {
      e.stopPropagation();
      group.lines.forEach(l => state.checked[fileObj.name].add(l.raw));
      renderCheckboxMode(fileObj);
      refreshBadge(fileObj.name);
    });
    labelEl.appendChild(checkAllBtn);
    groupEl.appendChild(labelEl);

    group.lines.forEach(line => {
      const lineEl = document.createElement('div');
      lineEl.className = 'transcript-line-cb';

      const cbBox = document.createElement('div');
      cbBox.className = 'cb-box' + (state.checked[fileObj.name].has(line.raw) ? ' checked' : '');
      cbBox.textContent = state.checked[fileObj.name].has(line.raw) ? '✓' : '';

      const ts = document.createElement('div');
      ts.className = 'ts-cb';
      ts.textContent = line.timestamp;

      const text = document.createElement('div');
      text.className = 'line-text-cb';
      text.textContent = line.text;

      lineEl.appendChild(cbBox);
      lineEl.appendChild(ts);
      lineEl.appendChild(text);

      lineEl.addEventListener('click', () => {
        const s = state.checked[fileObj.name];
        if (s.has(line.raw)) { s.delete(line.raw); cbBox.classList.remove('checked'); cbBox.textContent = ''; }
        else { s.add(line.raw); cbBox.classList.add('checked'); cbBox.textContent = '✓'; }
        refreshBadge(fileObj.name);
      });

      groupEl.appendChild(lineEl);
    });
    scroll.appendChild(groupEl);
  });
}

function checkAllInFile(filename) {
  const fileObj = state.files.find(f => f.name === filename);
  if (!fileObj) return;
  fileObj.lines.forEach(l => state.checked[filename].add(l.raw));
  renderTranscript(filename);
  refreshBadge(filename);
}

function renderTranscript(filename) {
  const fileObj = state.files.find(f => f.name === filename);
  if (state.mode === 'checkbox') renderCheckboxMode(fileObj);
  else renderHighlightMode(fileObj);
}
```

- [ ] **Step 2: Verify checkbox mode manually**

Run the server, open a folder with a transcript. Expected: transcript lines appear grouped by minute with green checkboxes. Clicking a line toggles the checkbox. "Check all" per group and per file works. Sidebar badge updates.

- [ ] **Step 3: Commit**

```
git add static/app.js
git commit -m "feat: add sidebar and checkbox mode rendering"
```

---

## Task 10: Frontend JS — highlight mode with drag-to-brush

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Append highlight mode to app.js**

Append to `static/app.js`:

```javascript
// ─── Highlight mode ───────────────────────────────────────────────────────────
let _dragActive = false;
let _dragSetTo = null;   // true = highlighting, false = un-highlighting

function renderHighlightMode(fileObj) {
  const scroll = $('transcript-scroll');
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
    // Determine whether this drag is a highlight or un-highlight pass
    _dragSetTo = !hl.has(raw);
    _applyHighlight(fileObj.name, lineEl, _dragSetTo);
    refreshBadge(fileObj.name);
  });

  scroll.addEventListener('mousemove', e => {
    if (!_dragActive) return;
    const lineEl = e.target.closest('.transcript-line-hl');
    if (!lineEl) {
      // Auto-scroll when near edges
      const rect = scroll.getBoundingClientRect();
      const threshold = 40;
      if (e.clientY < rect.top + threshold) scroll.scrollTop -= 8;
      else if (e.clientY > rect.bottom - threshold) scroll.scrollTop += 8;
      return;
    }
    _applyHighlight(fileObj.name, lineEl, _dragSetTo);
    refreshBadge(fileObj.name);
  });

  document.addEventListener('mouseup', () => { _dragActive = false; }, { once: false });
}

function _applyHighlight(filename, lineEl, setTo) {
  const raw = lineEl.dataset.raw;
  const hl = state.highlighted[filename];
  if (setTo) {
    hl.add(raw);
    lineEl.classList.add('highlighted');
  } else {
    hl.delete(raw);
    lineEl.classList.remove('highlighted');
  }
}

function highlightAllInFile(filename) {
  const fileObj = state.files.find(f => f.name === filename);
  if (!fileObj) return;
  fileObj.lines.forEach(l => state.highlighted[filename].add(l.raw));
  renderTranscript(filename);
  refreshBadge(filename);
}
```

- [ ] **Step 2: Verify highlight mode manually**

Switch to Highlight mode. Expected:
- Clicking a line toggles amber highlight.
- Click and drag across multiple lines highlights each one as cursor passes over it.
- Dragging to bottom edge auto-scrolls the transcript.
- "Highlight all" highlights every line.
- Sidebar badge updates to show count.

- [ ] **Step 3: Commit**

```
git add static/app.js
git commit -m "feat: add highlight mode with drag-to-brush and auto-scroll"
```

---

## Task 11: Frontend JS — generate flow, progress polling, result player

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Append generate flow to app.js**

Append to `static/app.js`:

```javascript
// ─── Generate ─────────────────────────────────────────────────────────────────
$('btn-generate').addEventListener('click', () => {
  const mode = state.mode;
  const selections = {};
  state.files.forEach(f => {
    const lines = mode === 'checkbox'
      ? [...(state.checked[f.name] || [])]
      : [...(state.highlighted[f.name] || [])];
    if (lines.length > 0) selections[f.name] = lines;
  });
  submitGenerate(mode, selections);
});

async function submitGenerate(mode, selections) {
  const prompt = $('prompt-input').value.trim();
  if (!prompt) { alert('Please enter a prompt before generating.'); return; }

  const outputFilename = $('output-filename').value.trim() || 'sizzle_reel.mp4';

  showScreen('screen-generating');
  $('gen-log').innerHTML = '';
  $('gen-bar').style.width = '0%';
  $('topbar-controls').classList.add('hidden');

  const resp = await fetch('/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      folder: state.folder,
      mode,
      selections,
      prompt,
      output_filename: outputFilename,
    }),
  });
  const { job_id, error } = await resp.json();
  if (!resp.ok) {
    appendLog('gen-log', `✗ ${error || 'Failed to start generation'}`);
    return;
  }

  state.currentJobId = job_id;
  pollGeneration(job_id);
}

function pollGeneration(jobId) {
  let lastLogLen = 0;

  const interval = setInterval(async () => {
    const resp = await fetch(`/status/${jobId}`);
    const job = await resp.json();

    const pct = job.total > 0 ? Math.round((job.done / job.total) * 100) : 0;
    $('gen-bar').style.width = Math.max(pct, 5) + '%';

    const newLines = job.log.slice(lastLogLen);
    newLines.forEach(msg => appendLog('gen-log', msg));
    lastLogLen = job.log.length;

    if (job.status === 'done') {
      clearInterval(interval);
      $('gen-bar').style.width = '100%';
      state.resultJobId = jobId;
      showResult(job.result);
    } else if (job.status === 'error') {
      clearInterval(interval);
      appendLog('gen-log', `✗ Error: ${job.error}`);
      $('topbar-controls').classList.remove('hidden');
    } else if (job.status === 'cancelled') {
      clearInterval(interval);
      showScreen('screen-workspace');
      $('topbar-controls').classList.remove('hidden');
    }
  }, 2000);

  $('btn-cancel-gen').onclick = async () => {
    await fetch(`/jobs/${jobId}`, { method: 'DELETE' });
    clearInterval(interval);
  };
}

function showResult(result) {
  showScreen('screen-result');
  $('topbar-controls').classList.remove('hidden');

  const src = `/video/${state.resultJobId}`;
  $('result-source').src = src;
  $('result-video').load();

  $('result-filename').textContent = result.filename;
  const mins = Math.floor(result.duration_seconds / 60);
  const secs = result.duration_seconds % 60;
  $('result-info').textContent =
    `${mins}:${String(secs).padStart(2,'0')} · ${result.clip_count} clips · saved to folder`;
}

$('btn-new-reel').addEventListener('click', () => {
  $('result-video').pause();
  $('result-source').src = '';
  showScreen('screen-workspace');
  $('topbar-controls').classList.remove('hidden');
});

$('btn-open-folder').addEventListener('click', async () => {
  await fetch('/open-folder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folder: state.folder }),
  });
});
```

- [ ] **Step 2: Add /open-folder endpoint to app.py**

Add inside `create_app` in `app.py`:

```python
    @app.post("/open-folder")
    def open_folder_in_explorer():
        folder = (request.get_json() or {}).get("folder", "").strip()
        if folder and Path(folder).exists():
            import subprocess
            subprocess.Popen(f'explorer "{folder}"')
        return jsonify({"ok": True})
```

- [ ] **Step 3: Verify generate flow manually**

Open a folder with videos + transcripts. Select some lines in checkbox or highlight mode. Enter a prompt. Click "Generate Reel". Expected:
- Progress screen appears with live log.
- Each video shows `⟳ analyzing...` then `✓ found segments: ...` 
- Progress bar advances.
- When done, result screen appears with embedded video player.
- "New Reel" returns to the workspace with the same folder loaded.
- "Open Folder" opens Windows Explorer at the folder.

- [ ] **Step 4: Run full test suite**

Run: `.\venv\Scripts\python.exe -m pytest tests/ -v`

Expected: all tests PASSED.

- [ ] **Step 5: Commit**

```
git add app.py static/app.js
git commit -m "feat: add generate flow, progress polling, and result video player"
```

---

## Task 12: Frontend JS — library tab

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Append library rendering to app.js**

Append to `static/app.js`:

```javascript
// ─── Library ──────────────────────────────────────────────────────────────────
async function loadLibrary() {
  const resp = await fetch('/library');
  const entries = await resp.json();
  renderLibrary(entries);
}

function renderLibrary(entries) {
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

    const mins = Math.floor((entry.duration_seconds || 0) / 60);
    const secs = (entry.duration_seconds || 0) % 60;
    const durStr = `${mins}:${String(secs).padStart(2,'0')}`;
    const dateStr = entry.created_at ? entry.created_at.split('T')[0] : '';

    card.innerHTML = `
      <div class="reel-thumb" data-id="${entry.id}">
        <div class="reel-play-icon">▶</div>
        <div class="reel-duration">${durStr}</div>
      </div>
      <div class="reel-body">
        <div class="reel-name" title="${entry.filename}">${entry.filename}</div>
        <div class="reel-meta">${dateStr} · ${entry.clip_count || 0} clips · ${entry.source_folder || ''}</div>
        <div class="reel-prompt" title="${entry.prompt}">"${entry.prompt}"</div>
        <div class="reel-actions">
          <button class="reel-btn play" data-id="${entry.id}">▶ Play</button>
          <button class="reel-btn show" data-id="${entry.id}" data-path="${entry.path}">📂 Show</button>
          <button class="reel-btn delete" data-id="${entry.id}">🗑</button>
        </div>
      </div>`;

    // Thumb click = play
    card.querySelector('.reel-thumb').addEventListener('click', () => openLibraryPlayer(entry));
    card.querySelector('.reel-btn.play').addEventListener('click', () => openLibraryPlayer(entry));

    // Show in explorer
    card.querySelector('.reel-btn.show').addEventListener('click', async () => {
      const folder = entry.path.replace(/[\\/][^\\/]+$/, '');
      await fetch('/open-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder }),
      });
    });

    // Delete
    card.querySelector('.reel-btn.delete').addEventListener('click', async () => {
      await fetch(`/library/${entry.id}`, { method: 'DELETE' });
      loadLibrary();
    });

    grid.appendChild(card);
  });
}

function openLibraryPlayer(entry) {
  $('library-source').src = `/library-video/${entry.id}`;
  $('library-video').load();
  $('library-player-meta').textContent =
    `"${entry.prompt}" — ${entry.source_folder}`;
  $('library-player-overlay').classList.remove('hidden');
}

$('btn-close-player').addEventListener('click', () => {
  $('library-video').pause();
  $('library-source').src = '';
  $('library-player-overlay').classList.add('hidden');
});
```

- [ ] **Step 2: Verify library tab manually**

Generate a reel, then click the 📼 Library tab. Expected:
- The generated reel appears as a card with filename, date, clip count, folder, and prompt.
- Clicking the card thumbnail or ▶ Play opens the overlay video player.
- Closing the overlay stops playback.
- 🗑 Delete removes the card (does not delete the file on disk).
- 📂 Show opens the folder in Explorer.

- [ ] **Step 3: Run full test suite one final time**

Run: `.\venv\Scripts\python.exe -m pytest tests/ -v`

Expected: all tests PASSED.

- [ ] **Step 4: Final manual end-to-end smoke test**

1. Run `.\venv\Scripts\python.exe app.py`
2. Open `http://localhost:5000`
3. Browse to a real video folder, load it, wait for transcription
4. Switch between Checkbox and Highlight modes — verify both work
5. Select lines, enter a prompt, generate — verify the reel plays
6. Open the Library tab — verify the reel appears and plays from there
7. Click Analyze Everything — verify generation runs without selections

- [ ] **Step 5: Commit**

```
git add static/app.js
git commit -m "feat: add library tab with grid view and video player overlay"
```
