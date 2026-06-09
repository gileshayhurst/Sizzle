# Performance, Library & Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add parallel clip extraction, WebSocket generation updates, library delete/edit, and selection persistence to the Sizzle Reel web app.

**Architecture:** Parallel extraction restructures `_run_generation` into three phases (plan / execute / assemble) using a `ThreadPoolExecutor`. WebSocket replaces the 2-second polling loop for generation progress using `flask-sock`. Library delete gains a file-deletion option and a confirmation UI; library edit adds `title`/`notes` fields. Selection persistence saves/restores `state.checked` and `state.highlighted` to `localStorage`.

**Tech Stack:** Python 3.11, Flask, flask-sock (new), concurrent.futures (stdlib), pytest, vanilla JS, localStorage

---

## File Map

| File | Changes |
|------|---------|
| `generator_app.py` | Parallel extraction, WebSocket route, library delete file option, library PATCH endpoint |
| `requirements.txt` | Add `flask-sock` |
| `static/app.js` | `watchGeneration`, library delete confirmation + edit UI, `_saveSelections` |
| `static/style.css` | Delete confirmation styles, edit form styles |
| `tests/test_generator_app.py` | Tests for parallel extraction, WebSocket, library delete/edit |

---

## Task 1: Parallel Clip Extraction

**Files:**
- Modify: `generator_app.py`
- Test: `tests/test_generator_app.py`

### Context

`_run_generation` in `generator_app.py` currently extracts clips one at a time inside a `for vp, segs in video_segments:` loop. The new approach splits the body of `with tempfile.TemporaryDirectory() as tmp_dir:` into three phases: **Plan** (build a list of work items, generate title cards serially), **Execute** (submit all `extract_clip` calls to a ThreadPoolExecutor in parallel), **Assemble** (walk the plan in order, build `clip_paths` from items that succeeded).

The first loop that builds `video_segments` (and increments `job["done"]`) is **not changed**. Only the code inside `with tempfile.TemporaryDirectory() as tmp_dir:` up to `_append_log(job_id, "· Stitching reel...")` is replaced.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_generator_app.py`:

```python
# ─── Parallel clip extraction ─────────────────────────────────────────────────

def test_parallel_extraction_all_succeed(client, tmp_path):
    """All clips extracted; stitch receives title+clip pairs in order."""
    (tmp_path / "v1.mp4").touch()
    (tmp_path / "v2.mp4").touch()
    (tmp_path / "v1.txt").write_text("[0:01] Speaker: hello\n[0:10] Speaker: done\n", encoding="utf-8")
    (tmp_path / "v2.txt").write_text("[0:01] Speaker: world\n[0:10] Speaker: end\n", encoding="utf-8")

    stitched = []

    def fake_extract(video_path, start, end, output_path):
        from pathlib import Path
        Path(output_path).write_bytes(b"clip")

    def fake_title(lines, w, h, out, duration=5.0):
        from pathlib import Path
        Path(out).write_bytes(b"title")

    def fake_stitch(paths, out):
        stitched.extend(paths)
        from pathlib import Path
        Path(out).write_bytes(b"reel")

    with patch("generator_app.extract_clip", side_effect=fake_extract), \
         patch("generator_app.make_title_card", side_effect=fake_title), \
         patch("generator_app.stitch_clips", side_effect=fake_stitch), \
         patch("generator_app._library_add"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {
                "v1.mp4": ["[0:01] Speaker: hello"],
                "v2.mp4": ["[0:01] Speaker: world"],
            },
            "output_filename": "out.mp4",
            "prompt": "test",
        })
    assert resp.status_code == 200
    job_id = resp.get_json()["job_id"]
    from generator_app import _jobs
    job = _jobs[job_id]
    assert job["status"] == "done"
    # Two segments → two title+clip pairs → 4 paths
    assert len(stitched) == 4


def test_parallel_extraction_failed_clip_skipped(client, tmp_path):
    """A failed clip extraction skips that segment; other segments still appear."""
    (tmp_path / "v1.mp4").touch()
    (tmp_path / "v2.mp4").touch()
    (tmp_path / "v1.txt").write_text("[0:01] Speaker: bad\n[0:10] Speaker: end\n", encoding="utf-8")
    (tmp_path / "v2.txt").write_text("[0:01] Speaker: good\n[0:10] Speaker: end\n", encoding="utf-8")

    stitched = []

    def fake_extract(video_path, start, end, output_path):
        from pathlib import Path
        if "v1" in video_path:
            raise RuntimeError("extraction failed")
        Path(output_path).write_bytes(b"clip")

    def fake_title(lines, w, h, out, duration=5.0):
        from pathlib import Path
        Path(out).write_bytes(b"title")

    def fake_stitch(paths, out):
        stitched.extend(paths)
        from pathlib import Path
        Path(out).write_bytes(b"reel")

    with patch("generator_app.extract_clip", side_effect=fake_extract), \
         patch("generator_app.make_title_card", side_effect=fake_title), \
         patch("generator_app.stitch_clips", side_effect=fake_stitch), \
         patch("generator_app._library_add"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {
                "v1.mp4": ["[0:01] Speaker: bad"],
                "v2.mp4": ["[0:01] Speaker: good"],
            },
            "output_filename": "out.mp4",
            "prompt": "test",
        })
    assert resp.status_code == 200
    job_id = resp.get_json()["job_id"]
    from generator_app import _jobs
    job = _jobs[job_id]
    assert job["status"] == "done"
    # v1 failed, v2 succeeded → 1 title + 1 clip = 2 paths
    assert len(stitched) == 2


def test_parallel_extraction_all_fail_returns_error(client, tmp_path):
    """When every clip fails, job status is 'error'."""
    (tmp_path / "v1.mp4").touch()
    (tmp_path / "v1.txt").write_text("[0:01] Speaker: hi\n[0:10] Speaker: bye\n", encoding="utf-8")

    def fake_extract(video_path, start, end, output_path):
        raise RuntimeError("always fails")

    def fake_title(lines, w, h, out, duration=5.0):
        from pathlib import Path
        Path(out).write_bytes(b"title")

    with patch("generator_app.extract_clip", side_effect=fake_extract), \
         patch("generator_app.make_title_card", side_effect=fake_title), \
         patch("generator_app._library_add"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {"v1.mp4": ["[0:01] Speaker: hi"]},
            "output_filename": "out.mp4",
            "prompt": "test",
        })
    assert resp.status_code == 200
    job_id = resp.get_json()["job_id"]
    from generator_app import _jobs
    assert _jobs[job_id]["status"] == "error"
```

- [ ] **Step 2: Run tests to confirm they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py::test_parallel_extraction_all_succeed tests/test_generator_app.py::test_parallel_extraction_failed_clip_skipped tests/test_generator_app.py::test_parallel_extraction_all_fail_returns_error -v
```

Expected: FAIL (function is still serial; behavior differences may not be caught yet — that's OK, the tests define the contract).

- [ ] **Step 3: Add `import concurrent.futures` to `generator_app.py`**

Add after the existing stdlib imports (after `import uuid`):

```python
import concurrent.futures
```

- [ ] **Step 4: Replace the extraction loop inside `_run_generation`**

Inside `with tempfile.TemporaryDirectory() as tmp_dir:`, replace everything from `clip_paths = []` up to (but not including) `if not clip_paths:` with this three-phase implementation:

```python
        # ── Phase 1: Plan ────────────────────────────────────────────────
        plan = []      # ordered list of {"type": "title"|"clip", ...}
        item_idx = 0
        seg_num = 0

        for vp, segs in video_segments:
            try:
                width, height = get_video_dimensions(str(vp))
            except Exception:
                width, height = 1920, 1080
            for start_sec, end_sec in segs:
                seg_num += 1
                card_path = os.path.join(tmp_dir, f"clip_{item_idx:04d}.mp4")
                item_idx += 1
                clip_path = os.path.join(tmp_dir, f"clip_{item_idx:04d}.mp4")
                item_idx += 1
                plan.append({
                    "type": "title",
                    "path": card_path,
                    "lines": [
                        vp.stem,
                        f"from {_format_seconds(start_sec)}",
                        f"Segment {seg_num} / {total_segs}",
                    ],
                    "width": width,
                    "height": height,
                    "ok": False,
                    "error": None,
                })
                plan.append({
                    "type": "clip",
                    "path": clip_path,
                    "video_path": str(vp),
                    "start_sec": start_sec,
                    "end_sec": end_sec,
                    "ok": False,
                    "error": None,
                })

        # ── Phase 2: Execute ─────────────────────────────────────────────
        # Title cards: serial (fast, ~0.1s each)
        for item in plan:
            if item["type"] != "title":
                continue
            try:
                make_title_card(
                    item["lines"], item["width"], item["height"], item["path"]
                )
                item["ok"] = True
            except Exception as exc:
                item["error"] = str(exc)
                _append_log(job_id, f"· Could not create title card: {exc}")

        # Clips: parallel
        max_workers = min(4, os.cpu_count() or 4)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for idx, item in enumerate(plan):
                if item["type"] != "clip":
                    continue
                # Skip if the paired title card failed
                title_item = plan[idx - 1]
                if not title_item["ok"]:
                    item["error"] = "title card failed"
                    continue
                if job["cancel"].is_set():
                    item["error"] = "cancelled"
                    continue
                item["future"] = executor.submit(
                    extract_clip,
                    item["video_path"],
                    item["start_sec"],
                    item["end_sec"],
                    item["path"],
                )

            for item in plan:
                if item["type"] != "clip" or "future" not in item:
                    continue
                if job["cancel"].is_set():
                    item["future"].cancel()
                    item["error"] = "cancelled"
                    continue
                try:
                    item["future"].result()
                    item["ok"] = True
                except Exception as exc:
                    item["error"] = str(exc)
                    _append_log(
                        job_id,
                        f"✗ [{item['start_sec']:.1f}-{item['end_sec']:.1f}]"
                        f" extraction failed: {exc}",
                    )

        if job["cancel"].is_set():
            with _jobs_lock:
                job["status"] = "cancelled"
            return

        # ── Phase 3: Assemble ────────────────────────────────────────────
        clip_paths = []
        clip_durations = []
        segment_starts = []
        cumulative_time = 0.0
        title_card_count = 0

        i = 0
        while i < len(plan):
            title_item = plan[i]
            clip_item = plan[i + 1]
            i += 2

            if not title_item["ok"] or not clip_item["ok"]:
                continue   # errors already logged in Phase 2

            segment_starts.append(cumulative_time)
            clip_paths.append(title_item["path"])
            cumulative_time += TITLE_CARD_DURATION
            title_card_count += 1

            clip_paths.append(clip_item["path"])
            clip_durations.append(clip_item["end_sec"] - clip_item["start_sec"])
            cumulative_time += clip_item["end_sec"] - clip_item["start_sec"]
```

The `if not clip_paths:` error check and everything after it remain **unchanged**.

- [ ] **Step 5: Run tests to confirm they pass**

```
.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py::test_parallel_extraction_all_succeed tests/test_generator_app.py::test_parallel_extraction_failed_clip_skipped tests/test_generator_app.py::test_parallel_extraction_all_fail_returns_error -v
```

Expected: 3 PASS

- [ ] **Step 6: Run full test suite to confirm no regressions**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add generator_app.py tests/test_generator_app.py
git commit -m "perf: parallel clip extraction using ThreadPoolExecutor"
```

---

## Task 2: WebSocket Generation Progress — Backend

**Files:**
- Modify: `generator_app.py`
- Modify: `requirements.txt`
- Test: `tests/test_generator_app.py`

### Context

Add `flask-sock` to the generator service. Inside `create_app()`, initialize `Sock(app)` and register a `@sock.route('/ws/job/<job_id>')` handler that loops at 200ms intervals, pushing log/progress/done JSON messages. The existing `GET /status/<job_id>` endpoint is **not removed**.

- [ ] **Step 1: Add flask-sock to requirements.txt**

Add a new line to `requirements.txt`:

```
flask-sock
```

Install it:

```
.\venv\Scripts\python.exe -m pip install flask-sock
```

- [ ] **Step 2: Write failing WebSocket tests**

Add to `tests/test_generator_app.py`:

```python
import json as _json

# ─── WebSocket ────────────────────────────────────────────────────────────────

def test_ws_done_job_sends_log_progress_done(client):
    """A job already in 'done' state delivers log, progress, and done messages."""
    import threading
    from generator_app import _jobs, _jobs_lock
    job_id = "ws-test-done"
    with _jobs_lock:
        _jobs[job_id] = {
            "type": "generation",
            "status": "done",
            "total": 1,
            "done": 1,
            "log": ["✓ Done"],
            "result": {
                "filename": "test.mp4",
                "clip_count": 2,
                "duration_seconds": 30,
                "segment_starts": [],
                "path": "/tmp/test.mp4",
            },
            "error": None,
            "cancel": threading.Event(),
        }

    messages = []
    with client.websocket(f"/ws/job/{job_id}") as ws:
        while True:
            raw = ws.receive()
            if raw is None:
                break
            msg = _json.loads(raw)
            messages.append(msg)
            if msg["type"] == "done":
                break

    types = [m["type"] for m in messages]
    assert "log" in types
    assert "progress" in types
    assert "done" in types

    done_msg = next(m for m in messages if m["type"] == "done")
    assert done_msg["status"] == "done"
    assert done_msg["result"]["filename"] == "test.mp4"


def test_ws_unknown_job_sends_error_done(client):
    """An unknown job_id causes the WS to send a done/error message and close."""
    messages = []
    with client.websocket("/ws/job/nonexistent-job-xyz") as ws:
        while True:
            raw = ws.receive()
            if raw is None:
                break
            msg = _json.loads(raw)
            messages.append(msg)
            if msg["type"] == "done":
                break

    assert len(messages) == 1
    assert messages[0]["type"] == "done"
    assert messages[0]["status"] == "error"
    assert "not found" in messages[0]["error"]
```

- [ ] **Step 3: Run tests to confirm they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py::test_ws_done_job_sends_log_progress_done tests/test_generator_app.py::test_ws_unknown_job_sends_error_done -v
```

Expected: FAIL (route doesn't exist yet)

- [ ] **Step 4: Add WebSocket route to `generator_app.py`**

Add this import at the top of `generator_app.py` (after `from flask_cors import CORS`):

```python
from flask_sock import Sock
```

Inside `create_app()`, after `CORS(app)` and `app.config["TESTING"] = testing`, add:

```python
    import time as _time

    sock = Sock(app)

    @sock.route("/ws/job/<job_id>")
    def job_ws(ws, job_id):
        last_log_len = 0
        while True:
            with _jobs_lock:
                job = _jobs.get(job_id)
            if job is None:
                ws.send(json.dumps({
                    "type": "done",
                    "status": "error",
                    "error": "job not found",
                    "result": None,
                }))
                return
            # Push new log lines
            log_snapshot = list(job["log"])
            for msg in log_snapshot[last_log_len:]:
                ws.send(json.dumps({"type": "log", "message": msg}))
            last_log_len = len(log_snapshot)
            # Push progress
            ws.send(json.dumps({
                "type": "progress",
                "done": job["done"],
                "total": job["total"],
            }))
            status = job["status"]
            if status in ("done", "error", "cancelled"):
                ws.send(json.dumps({
                    "type": "done",
                    "status": status,
                    "result": job.get("result"),
                    "error": job.get("error"),
                }))
                return
            _time.sleep(0.2)
```

- [ ] **Step 5: Run tests to confirm they pass**

```
.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py::test_ws_done_job_sends_log_progress_done tests/test_generator_app.py::test_ws_unknown_job_sends_error_done -v
```

Expected: 2 PASS

- [ ] **Step 6: Run full test suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add generator_app.py requirements.txt tests/test_generator_app.py
git commit -m "feat: WebSocket endpoint for generation job progress"
```

---

## Task 3: WebSocket Generation Progress — Frontend

**Files:**
- Modify: `static/app.js`

### Context

Replace `pollGeneration(jobId)` with `watchGeneration(jobId)` in `app.js`. The `submitGenerate` function calls `pollGeneration(job_id)` — change that call too. The cancel button handler moves into `watchGeneration`. No automated tests for this task (browser-only).

- [ ] **Step 1: Add module-level WebSocket reference**

Near the top of `static/app.js`, after the `state` object declaration, add:

```js
let _genWs = null;  // active generation WebSocket
```

- [ ] **Step 2: Replace `pollGeneration` with `watchGeneration`**

Find and delete the entire `pollGeneration` function (lines starting with `function pollGeneration(jobId) {` through its closing `}`).

Replace it with:

```js
function watchGeneration(jobId) {
  const wsUrl = GENERATOR_URL.replace(/^http/, 'ws') + `/ws/job/${jobId}`;
  _genWs = new WebSocket(wsUrl);

  _genWs.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === 'log') {
      appendLog('gen-log', msg.message);
    } else if (msg.type === 'progress') {
      const pct = msg.total > 0 ? Math.round((msg.done / msg.total) * 100) : 0;
      $('gen-bar').style.width = Math.max(pct, 5) + '%';
    } else if (msg.type === 'done') {
      _genWs = null;
      if (msg.status === 'done') {
        $('gen-bar').style.width = '100%';
        state.resultJobId = jobId;
        showResult(msg.result);
      } else if (msg.status === 'error') {
        appendLog('gen-log', `✗ Error: ${msg.error}`);
        $('topbar-controls').classList.remove('hidden');
      } else if (msg.status === 'cancelled') {
        showScreen('screen-workspace');
        $('topbar-controls').classList.remove('hidden');
      }
    }
  };

  _genWs.onerror = () => {
    _genWs = null;
    appendLog('gen-log', '✗ Connection error — generation may still be running');
    $('topbar-controls').classList.remove('hidden');
  };

  $('btn-cancel-gen').onclick = async () => {
    await fetch(`${GENERATOR_URL}/jobs/${jobId}`, { method: 'DELETE' });
    if (_genWs) {
      _genWs.close();
      _genWs = null;
    }
    showScreen('screen-workspace');
    $('topbar-controls').classList.remove('hidden');
  };
}
```

- [ ] **Step 3: Update the `submitGenerate` call site**

In `submitGenerate`, find:

```js
  pollGeneration(job_id);
```

Replace with:

```js
  watchGeneration(job_id);
```

- [ ] **Step 4: Manual smoke test**

Start both services:
```
.\venv\Scripts\python.exe -c "from app import create_app; create_app().run(debug=True)"
.\venv\Scripts\python.exe -c "from generator_app import create_app; create_app().run(debug=True, port=5001)"
```

Open http://localhost:5000, load a folder, run a generation job, and confirm:
- The progress log updates appear immediately (not in 2-second batches)
- The progress bar advances
- The result screen appears when done

- [ ] **Step 5: Commit**

```bash
git add static/app.js
git commit -m "feat: replace generation polling with WebSocket live updates"
```

---

## Task 4: Library Delete — File Deletion Option (Backend)

**Files:**
- Modify: `generator_app.py`
- Test: `tests/test_generator_app.py`

### Context

The existing `DELETE /library/<entry_id>` endpoint removes the entry from JSON but doesn't delete the file and doesn't return 404 for missing entries. Update it to: return 404 if the entry isn't found, and when `?delete_file=true` is passed, also delete the `.mp4` file from disk.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_generator_app.py`:

```python
# ─── Library delete ───────────────────────────────────────────────────────────

def test_delete_library_entry_removes_from_json(client, tmp_path):
    """DELETE /library/<id> removes the entry; file is not deleted."""
    reel_file = tmp_path / "reel.mp4"
    reel_file.write_bytes(b"fake reel")
    entry = {
        "id": "del-test-1",
        "filename": "reel.mp4",
        "path": str(reel_file),
        "source_folder": "test/",
        "prompt": "test",
        "duration_seconds": 10,
        "clip_count": 1,
        "segment_starts": [],
        "created_at": "2026-06-08T00:00:00",
    }
    with patch("generator_app._load_library", return_value=[entry]), \
         patch("generator_app._save_library") as mock_save:
        resp = client.delete(f"/library/del-test-1")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    saved = mock_save.call_args[0][0]
    assert not any(e["id"] == "del-test-1" for e in saved)
    assert reel_file.exists()   # file NOT deleted


def test_delete_library_entry_with_delete_file_removes_file(client, tmp_path):
    """DELETE /library/<id>?delete_file=true also deletes the .mp4 file."""
    reel_file = tmp_path / "reel.mp4"
    reel_file.write_bytes(b"fake reel")
    entry = {
        "id": "del-test-2",
        "filename": "reel.mp4",
        "path": str(reel_file),
        "source_folder": "test/",
        "prompt": "test",
        "duration_seconds": 10,
        "clip_count": 1,
        "segment_starts": [],
        "created_at": "2026-06-08T00:00:00",
    }
    with patch("generator_app._load_library", return_value=[entry]), \
         patch("generator_app._save_library"):
        resp = client.delete(f"/library/del-test-2?delete_file=true")
    assert resp.status_code == 200
    assert not reel_file.exists()   # file IS deleted


def test_delete_library_entry_not_found_returns_404(client):
    """DELETE /library/<id> returns 404 when the id doesn't exist."""
    with patch("generator_app._load_library", return_value=[]):
        resp = client.delete("/library/no-such-id")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to confirm they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py::test_delete_library_entry_removes_from_json tests/test_generator_app.py::test_delete_library_entry_with_delete_file_removes_file tests/test_generator_app.py::test_delete_library_entry_not_found_returns_404 -v
```

Expected: 3 FAIL

- [ ] **Step 3: Update `DELETE /library/<entry_id>` in `generator_app.py`**

Inside `create_app()`, find the existing endpoint:

```python
    @app.delete("/library/<entry_id>")
    def delete_library_entry(entry_id):
        with _library_lock:
            entries = _load_library()
            entries = [e for e in entries if e["id"] != entry_id]
            _save_library(entries)
        return jsonify({"ok": True})
```

Replace with:

```python
    @app.delete("/library/<entry_id>")
    def delete_library_entry(entry_id):
        delete_file = request.args.get("delete_file") == "true"
        file_path_to_delete = None
        with _library_lock:
            entries = _load_library()
            entry = next((e for e in entries if e["id"] == entry_id), None)
            if entry is None:
                return jsonify({"error": "not found"}), 404
            if delete_file:
                file_path_to_delete = entry.get("path")
            entries = [e for e in entries if e["id"] != entry_id]
            _save_library(entries)
        if file_path_to_delete:
            try:
                Path(file_path_to_delete).unlink(missing_ok=True)
            except Exception:
                pass  # best-effort; never fail a delete over a missing file
        return jsonify({"ok": True})
```

- [ ] **Step 4: Run tests to confirm they pass**

```
.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py::test_delete_library_entry_removes_from_json tests/test_generator_app.py::test_delete_library_entry_with_delete_file_removes_file tests/test_generator_app.py::test_delete_library_entry_not_found_returns_404 -v
```

Expected: 3 PASS

- [ ] **Step 5: Run full test suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add generator_app.py tests/test_generator_app.py
git commit -m "feat: library delete supports ?delete_file=true and returns 404 for missing entries"
```

---

## Task 5: Library Edit Endpoint (Backend)

**Files:**
- Modify: `generator_app.py`
- Test: `tests/test_generator_app.py`

### Context

Add `PATCH /library/<entry_id>` that accepts `{"title": "...", "notes": "..."}` and updates those fields on the entry. The `title` and `notes` fields are new optional fields on library entries; existing entries without them behave correctly (frontend defaults to `entry.filename` for title and `""` for notes).

- [ ] **Step 1: Write failing tests**

Add to `tests/test_generator_app.py`:

```python
# ─── Library edit ─────────────────────────────────────────────────────────────

def test_patch_library_entry_updates_title_and_notes(client):
    """PATCH /library/<id> updates title and notes fields and returns updated entry."""
    entry = {
        "id": "edit-test-1",
        "filename": "reel.mp4",
        "path": "/tmp/reel.mp4",
        "source_folder": "test/",
        "prompt": "test",
        "duration_seconds": 10,
        "clip_count": 1,
        "segment_starts": [],
        "created_at": "2026-06-08T00:00:00",
    }
    with patch("generator_app._load_library", return_value=[entry]), \
         patch("generator_app._save_library") as mock_save:
        resp = client.patch(
            "/library/edit-test-1",
            json={"title": "My Reel", "notes": "Great footage"},
            content_type="application/json",
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["title"] == "My Reel"
    assert data["notes"] == "Great footage"
    saved = mock_save.call_args[0][0]
    updated = next(e for e in saved if e["id"] == "edit-test-1")
    assert updated["title"] == "My Reel"
    assert updated["notes"] == "Great footage"


def test_patch_library_entry_not_found_returns_404(client):
    """PATCH /library/<id> returns 404 when the id doesn't exist."""
    with patch("generator_app._load_library", return_value=[]):
        resp = client.patch(
            "/library/no-such-id",
            json={"title": "X"},
            content_type="application/json",
        )
    assert resp.status_code == 404


def test_patch_library_entry_ignores_unknown_keys(client):
    """PATCH /library/<id> silently ignores fields other than title and notes."""
    entry = {
        "id": "edit-test-2",
        "filename": "reel.mp4",
        "path": "/tmp/reel.mp4",
        "source_folder": "test/",
        "prompt": "original",
        "duration_seconds": 10,
        "clip_count": 1,
        "segment_starts": [],
        "created_at": "2026-06-08T00:00:00",
    }
    with patch("generator_app._load_library", return_value=[entry]), \
         patch("generator_app._save_library") as mock_save:
        resp = client.patch(
            "/library/edit-test-2",
            json={"title": "New", "prompt": "hacked", "id": "spoofed"},
            content_type="application/json",
        )
    assert resp.status_code == 200
    saved = mock_save.call_args[0][0]
    updated = next(e for e in saved if e["id"] == "edit-test-2")
    assert updated["prompt"] == "original"   # not overwritten
    assert updated["id"] == "edit-test-2"    # not overwritten
```

- [ ] **Step 2: Run tests to confirm they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py::test_patch_library_entry_updates_title_and_notes tests/test_generator_app.py::test_patch_library_entry_not_found_returns_404 tests/test_generator_app.py::test_patch_library_entry_ignores_unknown_keys -v
```

Expected: 3 FAIL

- [ ] **Step 3: Add `PATCH /library/<entry_id>` to `generator_app.py`**

Inside `create_app()`, add this route after the `DELETE /library/<entry_id>` route:

```python
    @app.patch("/library/<entry_id>")
    def edit_library_entry(entry_id):
        body = request.get_json() or {}
        with _library_lock:
            entries = _load_library()
            entry = next((e for e in entries if e["id"] == entry_id), None)
            if entry is None:
                return jsonify({"error": "not found"}), 404
            if "title" in body:
                entry["title"] = str(body["title"])
            if "notes" in body:
                entry["notes"] = str(body["notes"])
            _save_library(entries)
        return jsonify(entry)
```

- [ ] **Step 4: Run tests to confirm they pass**

```
.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py::test_patch_library_entry_updates_title_and_notes tests/test_generator_app.py::test_patch_library_entry_not_found_returns_404 tests/test_generator_app.py::test_patch_library_entry_ignores_unknown_keys -v
```

Expected: 3 PASS

- [ ] **Step 5: Run full test suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add generator_app.py tests/test_generator_app.py
git commit -m "feat: PATCH /library/<id> endpoint for title and notes editing"
```

---

## Task 6: Library UI — Delete Confirmation + Edit Form

**Files:**
- Modify: `static/app.js`
- Modify: `static/style.css`

### Context

The library card currently uses `innerHTML` to build the card and a simple delete handler. This task rewrites `renderLibrary` to use DOM construction (matching the rest of the app's pattern), adds an edit button (✏) alongside the delete button (🗑), wires up the delete confirmation flow, and wires up the inline edit form.

**Delete confirmation flow:** clicking 🗑 replaces `.reel-actions` content with three buttons: "Library only", "Also delete file", "Cancel". Confirming removes the card with a fade. Cancelling calls `loadLibrary()` to restore.

**Edit flow:** clicking ✏ replaces `.reel-body` content with an inline form (name input + notes textarea + Save/Cancel). Saving PATCHes the entry and calls `loadLibrary()`. Cancelling calls `loadLibrary()`.

No automated tests for this task.

- [ ] **Step 1: Add CSS for new UI elements to `static/style.css`**

Append to `static/style.css`:

```css
/* ─── Library card edit/delete UI ────────────────────────────────────────── */
.reel-card-actions-row {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 6px;
}
.reel-btn-icon {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 13px;
  padding: 2px 5px;
  border-radius: 4px;
  color: #8898aa;
  line-height: 1;
  transition: background 0.15s, color 0.15s;
}
.reel-btn-icon:hover { background: #232d3a; color: #c8daf0; }

.reel-delete-confirm {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 6px;
}
.reel-delete-confirm-label {
  font-size: 11px;
  color: #c87070;
}
.reel-btn.confirm-lib,
.reel-btn.confirm-file {
  background: #3a1a1a;
  border-color: #7a3535;
  color: #e08080;
}
.reel-btn.confirm-lib:hover,
.reel-btn.confirm-file:hover {
  background: #5a2525;
}
.reel-btn.cancel-del {
  color: #8898aa;
  background: none;
  border-color: #2a3a4a;
}

.reel-edit-form {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 4px 0;
}
.reel-edit-name {
  background: #151f29;
  border: 1px solid #2a3a4a;
  border-radius: 4px;
  color: #c8daf0;
  font-size: 12px;
  padding: 4px 8px;
  width: 100%;
  box-sizing: border-box;
}
.reel-edit-notes {
  background: #151f29;
  border: 1px solid #2a3a4a;
  border-radius: 4px;
  color: #8898aa;
  font-size: 11px;
  padding: 4px 8px;
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  min-height: 48px;
}
.reel-edit-btns {
  display: flex;
  gap: 6px;
}

.reel-notes {
  font-size: 11px;
  color: #6a7f93;
  margin-top: 3px;
  font-style: italic;
  white-space: pre-wrap;
  word-break: break-word;
}

@keyframes fadeOutCard {
  to { opacity: 0; transform: scale(0.97); }
}
.reel-card.fading {
  animation: fadeOutCard 0.3s ease forwards;
}
```

- [ ] **Step 2: Replace `renderLibrary` in `static/app.js`**

Find the entire `renderLibrary(entries)` function. Replace it with:

```js
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
    card.dataset.id = entry.id;

    const mins = Math.floor((entry.duration_seconds || 0) / 60);
    const secs = (entry.duration_seconds || 0) % 60;
    const durStr = `${mins}:${String(secs).padStart(2, '0')}`;
    const dateStr = entry.created_at ? entry.created_at.split('T')[0] : '';

    // Thumbnail
    const thumb = document.createElement('div');
    thumb.className = 'reel-thumb';
    thumb.dataset.id = entry.id;
    thumb.innerHTML = `<div class="reel-play-icon">▶</div><div class="reel-duration">${durStr}</div>`;
    thumb.addEventListener('click', () => openLibraryPlayer(entry));

    // Body
    const body = document.createElement('div');
    body.className = 'reel-body';
    _renderCardBody(body, card, entry, dateStr);

    card.appendChild(thumb);
    card.appendChild(body);
    grid.appendChild(card);
  });
}

function _renderCardBody(body, card, entry, dateStr) {
  body.innerHTML = '';

  const displayName = entry.title || entry.filename;

  // Name row (name + edit + delete icons)
  const nameRow = document.createElement('div');
  nameRow.style.cssText = 'display:flex;align-items:flex-start;justify-content:space-between;gap:4px';

  const nameEl = document.createElement('div');
  nameEl.className = 'reel-name';
  nameEl.title = entry.filename;
  nameEl.textContent = displayName;

  const iconRow = document.createElement('div');
  iconRow.style.cssText = 'display:flex;gap:2px;flex-shrink:0';

  const editBtn = document.createElement('button');
  editBtn.className = 'reel-btn-icon';
  editBtn.title = 'Edit';
  editBtn.textContent = '✏';

  const deleteBtn = document.createElement('button');
  deleteBtn.className = 'reel-btn-icon';
  deleteBtn.title = 'Delete';
  deleteBtn.textContent = '🗑';

  iconRow.appendChild(editBtn);
  iconRow.appendChild(deleteBtn);
  nameRow.appendChild(nameEl);
  nameRow.appendChild(iconRow);
  body.appendChild(nameRow);

  // Meta
  const meta = document.createElement('div');
  meta.className = 'reel-meta';
  meta.textContent = `${escAttr(dateStr)} · ${entry.clip_count || 0} clips · ${escAttr(entry.source_folder || '')}`;
  body.appendChild(meta);

  // Prompt
  const prompt = document.createElement('div');
  prompt.className = 'reel-prompt';
  prompt.title = entry.prompt || '';
  prompt.textContent = `"${entry.prompt || ''}"`;
  body.appendChild(prompt);

  // Notes (shown only if present)
  if (entry.notes) {
    const notes = document.createElement('div');
    notes.className = 'reel-notes';
    notes.textContent = entry.notes;
    body.appendChild(notes);
  }

  // Action buttons
  const actions = document.createElement('div');
  actions.className = 'reel-actions';

  const playBtn = document.createElement('button');
  playBtn.className = 'reel-btn play';
  playBtn.dataset.id = entry.id;
  playBtn.textContent = '▶ Play';

  const showBtn = document.createElement('button');
  showBtn.className = 'reel-btn show';
  showBtn.dataset.id = entry.id;
  showBtn.dataset.path = entry.path || '';
  showBtn.textContent = '📂 Show';

  actions.appendChild(playBtn);
  actions.appendChild(showBtn);
  body.appendChild(actions);

  // Event listeners
  playBtn.addEventListener('click', () => openLibraryPlayer(entry));

  showBtn.addEventListener('click', async () => {
    const folder = (entry.path || '').replace(/[\\/][^\\/]+$/, '');
    await fetch(GENERATOR_URL + '/open-folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder }),
    });
  });

  deleteBtn.addEventListener('click', () => _showDeleteConfirm(body, card, entry, dateStr, actions));

  editBtn.addEventListener('click', () => _showEditForm(body, card, entry, dateStr));
}

function _showDeleteConfirm(body, card, entry, dateStr, actions) {
  actions.innerHTML = '';

  const label = document.createElement('span');
  label.className = 'reel-delete-confirm-label';
  label.textContent = 'Remove?';

  const libOnly = document.createElement('button');
  libOnly.className = 'reel-btn confirm-lib';
  libOnly.textContent = 'Library only';

  const withFile = document.createElement('button');
  withFile.className = 'reel-btn confirm-file';
  withFile.textContent = 'Also delete file';

  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'reel-btn cancel-del';
  cancelBtn.textContent = 'Cancel';

  actions.appendChild(label);
  actions.appendChild(libOnly);
  actions.appendChild(withFile);
  actions.appendChild(cancelBtn);

  async function doDelete(deleteFile) {
    const url = `${GENERATOR_URL}/library/${entry.id}` + (deleteFile ? '?delete_file=true' : '');
    await fetch(url, { method: 'DELETE' });
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
  }

  libOnly.addEventListener('click', () => doDelete(false));
  withFile.addEventListener('click', () => doDelete(true));
  cancelBtn.addEventListener('click', () => loadLibrary());
}

function _showEditForm(body, card, entry, dateStr) {
  body.innerHTML = '';

  const form = document.createElement('div');
  form.className = 'reel-edit-form';

  const nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.className = 'reel-edit-name';
  nameInput.value = entry.title || entry.filename;
  nameInput.placeholder = 'Display name';

  const notesInput = document.createElement('textarea');
  notesInput.className = 'reel-edit-notes';
  notesInput.value = entry.notes || '';
  notesInput.placeholder = 'Notes…';

  const btns = document.createElement('div');
  btns.className = 'reel-edit-btns';

  const saveBtn = document.createElement('button');
  saveBtn.className = 'reel-btn';
  saveBtn.textContent = 'Save';

  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'reel-btn';
  cancelBtn.textContent = 'Cancel';

  btns.appendChild(saveBtn);
  btns.appendChild(cancelBtn);
  form.appendChild(nameInput);
  form.appendChild(notesInput);
  form.appendChild(btns);
  body.appendChild(form);

  nameInput.focus();
  nameInput.select();

  saveBtn.addEventListener('click', async () => {
    const newTitle = nameInput.value.trim() || entry.filename;
    const newNotes = notesInput.value;
    const resp = await fetch(`${GENERATOR_URL}/library/${entry.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: newTitle, notes: newNotes }),
    });
    if (resp.ok) {
      loadLibrary();
    }
  });

  cancelBtn.addEventListener('click', () => loadLibrary());

  nameInput.addEventListener('keydown', e => {
    if (e.key === 'Escape') loadLibrary();
    if (e.key === 'Enter') saveBtn.click();
  });
}
```

- [ ] **Step 3: Update `openLibraryPlayer` to use `entry.title`**

Find `openLibraryPlayer` in `app.js`. Change the line that sets `library-player-meta`:

From:
```js
  $('library-player-meta').textContent =
    `"${entry.prompt}" — ${entry.source_folder}`;
```

To:
```js
  const displayName = entry.title || entry.filename;
  $('library-player-meta').textContent =
    `${displayName} — "${entry.prompt}"`;
```

- [ ] **Step 4: Manual smoke test**

Open http://localhost:5000, go to the Library tab and confirm:
- Each card shows ✏ and 🗑 buttons
- Clicking 🗑 shows the three confirmation options; "Cancel" restores the card; "Library only" removes entry and fades the card; "Also delete file" removes entry and (if file exists) deletes it
- Clicking ✏ shows the inline edit form with name/notes inputs; Cancel restores; Save updates the displayed name and notes

- [ ] **Step 5: Commit**

```bash
git add static/app.js static/style.css
git commit -m "feat: library delete confirmation + inline edit for name and notes"
```

---

## Task 7: Selection Persistence

**Files:**
- Modify: `static/app.js`

### Context

Add `_saveSelections()` that writes `state.checked` and `state.highlighted` to `localStorage` keyed by `sizzle_sel_<folder>`. Call it after every mutation. In `loadTranscripts()`, after populating `state.files`, restore persisted selections for any files still present in the folder. `Set` values are serialised as arrays.

No automated tests (pure `localStorage` front-end feature).

- [ ] **Step 1: Add `_saveSelections` helper to `static/app.js`**

Add this function after the `state` object declaration (near the top of `app.js`, after the `let _genWs = null;` line added in Task 3):

```js
function _saveSelections() {
  if (!state.folder) return;
  try {
    const key = 'sizzle_sel_' + state.folder;
    const payload = {
      checked: {},
      highlighted: {},
    };
    for (const [filename, set] of Object.entries(state.checked)) {
      payload.checked[filename] = [...set];
    }
    for (const [filename, set] of Object.entries(state.highlighted)) {
      payload.highlighted[filename] = [...set];
    }
    localStorage.setItem(key, JSON.stringify(payload));
  } catch (_) {
    // localStorage may be unavailable (private mode quota, etc.) — fail silently
  }
}
```

- [ ] **Step 2: Add restore call in `loadTranscripts`**

Find the `loadTranscripts` function. It ends with:

```js
  state.files.forEach(f => {
    if (!state.checked[f.name]) state.checked[f.name] = new Set();
    if (!state.highlighted[f.name]) state.highlighted[f.name] = new Set();
  });
```

Add this block immediately after those lines (still inside `loadTranscripts`):

```js
  // Restore persisted selections for this folder
  try {
    const key = 'sizzle_sel_' + state.folder;
    const raw = localStorage.getItem(key);
    if (raw) {
      const saved = JSON.parse(raw);
      const fileNames = new Set(state.files.map(f => f.name));
      for (const [filename, arr] of Object.entries(saved.checked || {})) {
        if (fileNames.has(filename)) state.checked[filename] = new Set(arr);
      }
      for (const [filename, arr] of Object.entries(saved.highlighted || {})) {
        if (fileNames.has(filename)) state.highlighted[filename] = new Set(arr);
      }
    }
  } catch (_) {
    // Malformed or unavailable localStorage — silently ignore
  }
```

- [ ] **Step 3: Call `_saveSelections()` after `runAnalyze` applies results**

In `runAnalyze()`, find this block (inside the `try`):

```js
    if (state.activeFile) renderTranscript(state.activeFile);
    state.files.forEach(f => refreshBadge(f.name));
    updateGenerateBtn();
```

Add `_saveSelections()` after `updateGenerateBtn()`:

```js
    if (state.activeFile) renderTranscript(state.activeFile);
    state.files.forEach(f => refreshBadge(f.name));
    updateGenerateBtn();
    _saveSelections();
```

- [ ] **Step 4: Add `_saveSelections()` at all 5 mutation sites**

**Site 1 — Checkbox per-line click** (inside `renderCheckboxMode`, in the `lineEl.addEventListener('click', ...)` handler):

```js
        _updateHeaderCbState(headerCb, group.lines, s);
        refreshBadge(fileObj.name);
        updateGenerateBtn();
        _saveSelections();   // ← add this line
      });
```

**Site 2 — Checkbox minute-header click** (inside `renderCheckboxMode`, in the `labelEl.addEventListener('click', ...)` handler):

```js
      _updateHeaderCbState(headerCb, group.lines, s);
      refreshBadge(fileObj.name);
      updateGenerateBtn();
      _saveSelections();   // ← add this line
    });
```

**Site 3 — Highlight drag end** (the `document.addEventListener('mouseup', ...)` near the top of the highlight-mode section):

From:
```js
document.addEventListener('mouseup', () => { _dragActive = false; });
```

To:
```js
document.addEventListener('mouseup', () => {
  if (_dragActive) _saveSelections();
  _dragActive = false;
});
```

**Site 4 — `checkAllInFile`** (function defined after `renderCheckboxMode`):

```js
function checkAllInFile(filename) {
  const fileObj = state.files.find(f => f.name === filename);
  if (!fileObj) return;
  fileObj.lines.forEach(l => state.checked[filename].add(l.raw));
  renderTranscript(filename);
  refreshBadge(filename);
  updateGenerateBtn();
  _saveSelections();   // ← add this line
}
```

**Site 5 — `highlightAllInFile`** (function defined after `checkAllInFile`):

```js
function highlightAllInFile(filename) {
  const fileObj = state.files.find(f => f.name === filename);
  if (!fileObj) return;
  fileObj.lines.forEach(l => state.highlighted[filename].add(l.raw));
  renderTranscript(filename);
  refreshBadge(filename);
  updateGenerateBtn();
  _saveSelections();   // ← add this line
}

- [ ] **Step 6: Manual smoke test**

1. Load a folder, manually check some lines in checkbox mode
2. Refresh the browser (F5)
3. Reopen the same folder — the checkboxes should be restored
4. Run an Analyze — the analyzed selections should persist across a refresh
5. Load a different folder — it should start with clean selections

- [ ] **Step 7: Commit**

```bash
git add static/app.js
git commit -m "feat: persist checkbox and highlight selections to localStorage"
```

---

## Final: Run Full Test Suite

- [ ] **Run all tests**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests pass with no failures or errors.
