# Loading-Folder Modal with Cancellable Cloud Download — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the small "Loading folder…" text with a cancel-able modal popup, and make the cloud-mode session download run as a cancellable background job with real progress.

**Architecture:** Cloud-mode `/load-folder` stops blocking inside `_ensure_cloud_session`; for uncached sessions it returns a `session_download` job immediately and downloads in a daemon thread using the existing job system (`_new_job` / `GET /status/<job_id>` / `DELETE /jobs/<job_id>`). The frontend shows an overlay modal (existing `.overlay` pattern) for every `openFolder()` call, polls the job for progress, and its ✕ aborts the fetch and/or cancels the job.

**Tech Stack:** Flask (app.py), vanilla JS (static/app.js), plain CSS (static/style.css), pytest.

**Spec:** `docs/superpowers/specs/2026-07-08-loading-folder-modal-design.md`

---

## File Structure

| File | Change |
|---|---|
| `app.py` | Extract `_scan_load_folder()` helper from the `/load-folder` route; add `SessionDownloadCancelled`; extend `_ensure_cloud_session()` with `job_id`/`cancel_event` params + cancel cleanup; rewrite `/load-folder` to spawn a `session_download` job for uncached cloud sessions |
| `tests/test_app.py` | New tests for cancellable download, waiter behaviour, progress reporting, async job contract, cancel+retry, cached-session sync path |
| `templates/index.html` | New `#loading-folder-modal` overlay |
| `static/style.css` | `.progress-bar.indeterminate` animation; remove now-dead `.error-msg.folder-loading` rule |
| `static/app.js` | Rewrite `openFolder()`; add `_showLoadingModal` / `_closeLoadingModal` / cancel handler / `_pollSessionDownload` / `_enterFolder` |

Key existing code (line numbers as of this plan):
- `app.py:276-314` — `_ensure_cloud_session`
- `app.py:471-588` — `/load-folder` route (incl. nested `_transcribe`)
- `app.py:590-614` — `/status/<job_id>` and `DELETE /jobs/<job_id>`
- `static/app.js:296-351` — `openFolder`
- `static/app.js:354-389` — `pollTranscription` (pattern to mirror; do not modify)
- `templates/index.html:247-260` — `#not-downloaded-modal` (pattern to mirror; insert new modal after it)

Run tests with: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -v` (repo root as cwd).

---

### Task 1: Extract `_scan_load_folder` helper (pure refactor)

The scan/filter/transcript-check logic currently lives inline in the `/load-folder` route. The cloud download job thread will need the same logic, so extract it. No behaviour change — existing tests must stay green.

**Files:**
- Modify: `app.py:471-521` (route body up to and including the `needs_transcription` computation)
- Add: module-level helper `_scan_load_folder` (place it directly after `_ensure_cloud_session`, before `create_app`)

- [ ] **Step 1: Run the existing suite to establish a green baseline**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -v`
Expected: all tests PASS. If anything fails, stop — fix or report before refactoring.

- [ ] **Step 2: Add the helper**

Insert after `_ensure_cloud_session` (module level, before `create_app`). This code is moved verbatim from the route body — the only changes are `return jsonify(...)` → `return None, "<message>"` and the final packaging:

```python
def _scan_load_folder(folder: str) -> tuple[dict | None, str | None]:
    """Scan `folder` and apply every load-folder filter.

    Shared by the synchronous /load-folder path and the cloud session_download
    job thread. Returns (result, error): exactly one is non-None. result is
    {"folder", "files", "needs_transcription"} where needs_transcription is a
    list of video Paths lacking a non-empty .txt transcript.
    """
    try:
        video_paths = scan_videos(folder)
    except ValueError as e:
        return None, str(e)

    video_paths = _filter_generated_reels(video_paths)
    if not video_paths:
        return None, "No source video files found (folder contains only previously generated reels)"

    # Check the sidecar for reels generated into this specific folder.
    # In cloud mode this catches reels that were generated locally and then
    # re-uploaded; in local mode it catches reels not yet in the library
    # (e.g. library cleared) or downloaded from a different session.
    locally_generated: set[str] = set()
    sidecar = Path(folder) / "sizzle_generated_reels.txt"
    if sidecar.exists():
        try:
            locally_generated = set(sidecar.read_text(encoding="utf-8").splitlines())
        except Exception:
            pass
    if locally_generated:
        video_paths = [p for p in video_paths if p.name not in locally_generated]
        if not video_paths:
            return None, "No source video files found (folder contains only previously generated reels)"

    # In cloud mode Whisper is not available — only videos with pre-supplied
    # .txt transcripts can be used.
    if storage.is_cloud():
        video_paths = [p for p in video_paths
                       if p.with_suffix(".txt").exists()
                       and p.with_suffix(".txt").stat().st_size > 0]
        if not video_paths:
            return None, "No transcripts found. In cloud mode, upload a .txt transcript alongside each video."

    _save_recent_folder(folder, len(video_paths))
    filenames = [p.name for p in video_paths]
    needs_transcription = [p for p in video_paths
                           if not p.with_suffix(".txt").exists()
                           or p.with_suffix(".txt").stat().st_size == 0]
    return {"folder": folder, "files": filenames,
            "needs_transcription": needs_transcription}, None
```

- [ ] **Step 3: Rewrite the route to call it**

Replace the `/load-folder` route body from `folder = (request.get_json() or ...)` down to the `needs_transcription = [...]` computation (app.py:472-516) with:

```python
    @app.post("/load-folder")
    def load_folder():
        folder = (request.get_json() or {}).get("folder", "").strip()
        if storage.is_cloud() and folder and not Path(folder).exists():
            folder = _ensure_cloud_session(folder)
        if not folder or not Path(folder).exists():
            return jsonify({"error": "Folder not found"}), 404

        result, error = _scan_load_folder(folder)
        if error:
            return jsonify({"error": error}), 422

        filenames = result["files"]
        needs_transcription = result["needs_transcription"]
```

Everything below (`if not needs_transcription: ...` through the end of the route) stays byte-for-byte unchanged — it already refers to `filenames`, `needs_transcription`, and `folder`.

- [ ] **Step 4: Run the suite**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -v`
Expected: all PASS (notably `test_load_folder_returns_video_list`, `test_load_folder_no_videos_returns_422`, `test_load_folder_excludes_generated_reels`).

- [ ] **Step 5: Commit**

```powershell
git add app.py; git commit -m "refactor: extract _scan_load_folder from the load-folder route"
```

---

### Task 2: Cancellable, progress-reporting `_ensure_cloud_session`

**Files:**
- Modify: `app.py:276-314` (`_ensure_cloud_session`), plus a new `SessionDownloadCancelled` exception class directly above it
- Test: `tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py` (the file already imports `threading`, `patch`, `Path`, `pytest` at the top):

```python
def test_ensure_cloud_session_cancel_cleans_cache_and_raises(tmp_path):
    """A cancel event set mid-download aborts the download, removes the session
    cache entries, and deletes the temp dir so a retry re-downloads cleanly."""
    import app as app_module

    app_module._cloud_session_dirs.clear()
    app_module._cloud_session_ready.clear()

    cancel = threading.Event()
    job_id = "dl-cancel-unit-job"
    with app_module._jobs_lock:
        app_module._jobs[job_id] = {
            "type": "session_download", "status": "running", "total": 0,
            "done": 0, "log": [], "result": None, "error": None, "cancel": cancel,
        }

    session_tmp = tmp_path / "sess"
    session_tmp.mkdir()

    def fake_download(key, local_path):
        Path(local_path).write_text("t", encoding="utf-8")
        cancel.set()  # cancellation arrives right after the first file

    with patch("storage.list_keys", return_value=["sessions/c/a.txt", "sessions/c/b.txt"]), \
         patch("storage.download_file", side_effect=fake_download), \
         patch("tempfile.mkdtemp", return_value=str(session_tmp)):
        with pytest.raises(app_module.SessionDownloadCancelled):
            app_module._ensure_cloud_session("sessions/c", job_id=job_id, cancel_event=cancel)

    assert "sessions/c" not in app_module._cloud_session_dirs
    assert "sessions/c" not in app_module._cloud_session_ready
    assert not session_tmp.exists()

    with app_module._jobs_lock:
        del app_module._jobs[job_id]


def test_ensure_cloud_session_waiter_raises_after_cancel():
    """A concurrent waiter that wakes to a removed cache entry must raise
    SessionDownloadCancelled instead of returning a broken path."""
    import app as app_module

    app_module._cloud_session_dirs["sessions/w"] = "/fake-half-populated"
    ev = threading.Event()
    app_module._cloud_session_ready["sessions/w"] = ev

    outcome = {}

    def waiter():
        try:
            app_module._ensure_cloud_session("sessions/w")
            outcome["result"] = "returned"
        except app_module.SessionDownloadCancelled:
            outcome["result"] = "cancelled"

    t = threading.Thread(target=waiter)
    t.start()
    # Simulate the downloading caller being cancelled: entries removed, then
    # waiters released.
    with app_module._cloud_session_lock:
        app_module._cloud_session_dirs.pop("sessions/w")
        app_module._cloud_session_ready.pop("sessions/w")
    ev.set()
    t.join(timeout=2)
    assert not t.is_alive()
    assert outcome["result"] == "cancelled"


def test_ensure_cloud_session_reports_progress_to_job(tmp_path):
    """total = number of .txt keys (videos are 0-byte placeholders and don't
    count); done increments per downloaded transcript."""
    import app as app_module

    app_module._cloud_session_dirs.clear()
    app_module._cloud_session_ready.clear()

    job_id = "dl-progress-unit-job"
    with app_module._jobs_lock:
        app_module._jobs[job_id] = {
            "type": "session_download", "status": "running", "total": 0,
            "done": 0, "log": [], "result": None, "error": None,
            "cancel": threading.Event(),
        }

    with patch("storage.list_keys", return_value=[
             "sessions/p/v.mp4", "sessions/p/v.txt", "sessions/p/w.txt"]), \
         patch("storage.download_file",
               side_effect=lambda k, d: Path(d).write_text("t", encoding="utf-8")), \
         patch("tempfile.mkdtemp", return_value=str(tmp_path)):
        result = app_module._ensure_cloud_session(
            "sessions/p", job_id=job_id,
            cancel_event=app_module._jobs[job_id]["cancel"])

    assert result == str(tmp_path)
    with app_module._jobs_lock:
        assert app_module._jobs[job_id]["total"] == 2
        assert app_module._jobs[job_id]["done"] == 2
        del app_module._jobs[job_id]

    app_module._cloud_session_dirs.clear()
    app_module._cloud_session_ready.clear()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -k ensure_cloud_session -v`
Expected: the three NEW tests FAIL (`AttributeError: module 'app' has no attribute 'SessionDownloadCancelled'` / `TypeError: _ensure_cloud_session() got an unexpected keyword argument 'job_id'`). The two pre-existing `ensure_cloud_session` tests still PASS.

- [ ] **Step 3: Implement**

Replace `_ensure_cloud_session` (app.py:276-314) entirely with:

```python
class SessionDownloadCancelled(Exception):
    """A cloud session download was cancelled via its job's cancel event."""


def _ensure_cloud_session(session_key: str, job_id: str | None = None,
                          cancel_event: threading.Event | None = None) -> str:
    """Download session files from S3 into a local temp dir if not already cached.

    Thread-safe: concurrent callers for the same session_key block until the
    first caller finishes downloading (rather than getting a half-populated dir).

    When job_id/cancel_event are supplied (the async /load-folder path), progress
    is reported to the job between files, and the download aborts with
    SessionDownloadCancelled when the event is set. On cancel the cache entries
    are removed BEFORE waiters are released, so a retry re-downloads cleanly and
    any waiter wakes to a missing entry and raises SessionDownloadCancelled too.
    """
    with _cloud_session_lock:
        if session_key in _cloud_session_dirs:
            event = _cloud_session_ready[session_key]
            is_new = False
        else:
            tmp = tempfile.mkdtemp(prefix="sizzle_session_")
            _cloud_session_dirs[session_key] = tmp
            event = threading.Event()
            _cloud_session_ready[session_key] = event
            is_new = True

    if not is_new:
        event.wait()          # block until the first caller finishes
        with _cloud_session_lock:
            cached = _cloud_session_dirs.get(session_key)
        if cached is None:    # first caller was cancelled and cleaned up
            raise SessionDownloadCancelled(session_key)
        return cached

    tmp = _cloud_session_dirs[session_key]
    try:
        # The main app only ever reads .txt sidecars (scan_videos merely enumerates
        # filenames; analyze/transcripts read transcripts). Downloading the video
        # bytes would pile hundreds of MB per session into Render's /tmp and blow the
        # 2GB ephemeral-disk limit. So download only transcripts; give each video a
        # 0-byte placeholder so scan_videos still lists it.
        keys = storage.list_keys(session_key + "/")
        if job_id is not None:
            txt_total = sum(1 for k in keys if Path(k).suffix.lower() == ".txt")
            with _jobs_lock:
                if job_id in _jobs:
                    _jobs[job_id]["total"] = txt_total
        done = 0
        for key in keys:
            if cancel_event is not None and cancel_event.is_set():
                raise SessionDownloadCancelled(session_key)
            filename = Path(key).name
            dest = os.path.join(tmp, filename)
            if Path(filename).suffix.lower() == ".txt":
                storage.download_file(key, dest)
                done += 1
                if job_id is not None:
                    with _jobs_lock:
                        if job_id in _jobs:
                            _jobs[job_id]["done"] = done
            else:
                Path(dest).touch()
    except SessionDownloadCancelled:
        # Remove the cache entries BEFORE the finally releases waiters, so
        # waiters see the missing entry (= cancelled) rather than a
        # half-populated dir, and a retry re-downloads.
        with _cloud_session_lock:
            _cloud_session_dirs.pop(session_key, None)
            _cloud_session_ready.pop(session_key, None)
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    finally:
        event.set()           # release waiters even if download failed
    return tmp
```

(`shutil`, `threading`, `tempfile`, `os` are already imported at the top of app.py.)

- [ ] **Step 4: Run the tests**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -k ensure_cloud_session -v`
Expected: all five (2 old + 3 new) PASS.

- [ ] **Step 5: Run the whole file, then commit**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -v` — all PASS.

```powershell
git add app.py tests/test_app.py; git commit -m "feat: cancellable, progress-reporting cloud session download"
```

---

### Task 3: Async cloud `/load-folder` with `session_download` job

**Files:**
- Modify: `app.py` — the `/load-folder` route (as rewritten in Task 1)
- Test: `tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:

```python
def _poll_job(client, job_id, timeout=5.0):
    """Poll /status/<job_id> until it leaves 'running' or timeout expires."""
    import time
    deadline = time.time() + timeout
    status = None
    while time.time() < deadline:
        status = client.get(f"/status/{job_id}").get_json()
        if status["status"] in ("done", "error", "cancelled"):
            return status
        time.sleep(0.05)
    return status


def test_load_folder_uncached_cloud_session_returns_download_job(client, tmp_path):
    """Cloud mode + uncached session: /load-folder returns a session_download
    job immediately; the job finishes with the folder/files payload in result."""
    import app as app_module

    app_module._cloud_session_dirs.clear()
    app_module._cloud_session_ready.clear()

    def fake_download(key, local_path):
        Path(local_path).write_text("[0:01] Speaker: hi", encoding="utf-8")

    with patch("storage.is_cloud", return_value=True), \
         patch("storage.list_keys", return_value=[
             "sessions/dl1/vid.mp4", "sessions/dl1/vid.txt"]), \
         patch("storage.download_file", side_effect=fake_download), \
         patch("tempfile.mkdtemp", return_value=str(tmp_path)):
        resp = client.post("/load-folder", json={"folder": "sessions/dl1"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["job_type"] == "session_download"
        assert data["job_id"]

        status = _poll_job(client, data["job_id"])

    assert status["status"] == "done"
    assert status["result"]["files"] == ["vid.mp4"]
    assert status["result"]["folder"] == str(tmp_path)

    app_module._cloud_session_dirs.clear()
    app_module._cloud_session_ready.clear()


def test_session_download_cancel_cleans_cache_and_retry_succeeds(client, tmp_path):
    """DELETE /jobs/<id> mid-download cancels the job, the session cache is
    cleaned, and a retried /load-folder re-downloads and completes."""
    import time
    import app as app_module

    app_module._cloud_session_dirs.clear()
    app_module._cloud_session_ready.clear()

    release = threading.Event()

    def slow_download(key, local_path):
        release.wait(timeout=5)  # hold the first download open until cancelled
        Path(local_path).write_text("[0:01] Speaker: hi", encoding="utf-8")

    dir1 = tmp_path / "first"; dir1.mkdir()
    dir2 = tmp_path / "second"; dir2.mkdir()
    tmp_dirs = iter([str(dir1), str(dir2)])

    with patch("storage.is_cloud", return_value=True), \
         patch("storage.list_keys", return_value=[
             "sessions/dl2/a.mp4", "sessions/dl2/a.txt",
             "sessions/dl2/b.mp4", "sessions/dl2/b.txt"]), \
         patch("storage.download_file", side_effect=slow_download), \
         patch("tempfile.mkdtemp", side_effect=lambda **kw: next(tmp_dirs)):
        resp = client.post("/load-folder", json={"folder": "sessions/dl2"})
        job_id = resp.get_json()["job_id"]
        time.sleep(0.2)                  # let the thread reach the blocking download
        client.delete(f"/jobs/{job_id}")
        release.set()                    # unblock so the loop can observe the cancel

        status = _poll_job(client, job_id)
        assert status["status"] == "cancelled"

        # The thread cleans the cache after cancelling — wait for it.
        deadline = time.time() + 5
        while time.time() < deadline:
            if "sessions/dl2" not in app_module._cloud_session_dirs:
                break
            time.sleep(0.05)
        assert "sessions/dl2" not in app_module._cloud_session_dirs
        assert "sessions/dl2" not in app_module._cloud_session_ready

        # Retry: fresh job, downloads run instantly now, completes.
        resp2 = client.post("/load-folder", json={"folder": "sessions/dl2"})
        job_id2 = resp2.get_json()["job_id"]
        assert job_id2 != job_id
        status2 = _poll_job(client, job_id2)
        assert status2["status"] == "done"
        assert status2["result"]["files"] == ["a.mp4", "b.mp4"]

    app_module._cloud_session_dirs.clear()
    app_module._cloud_session_ready.clear()


def test_load_folder_cached_cloud_session_stays_synchronous(client, tmp_path):
    """An already-downloaded session must not spawn a job — /load-folder answers
    synchronously with the file list, exactly as before."""
    import app as app_module

    app_module._cloud_session_dirs.clear()
    app_module._cloud_session_ready.clear()

    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text("[0:01] Speaker: hi", encoding="utf-8")
    ev = threading.Event()
    ev.set()
    app_module._cloud_session_dirs["sessions/cached"] = str(tmp_path)
    app_module._cloud_session_ready["sessions/cached"] = ev

    with patch("storage.is_cloud", return_value=True):
        resp = client.post("/load-folder", json={"folder": "sessions/cached"})

    assert resp.status_code == 200
    data = resp.get_json()
    assert "job_type" not in data
    assert data["job_id"] is None
    assert data["files"] == ["vid.mp4"]

    app_module._cloud_session_dirs.clear()
    app_module._cloud_session_ready.clear()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -k "session_download or cached_cloud or uncached_cloud" -v`
Expected: `uncached...returns_download_job` and `...cancel_cleans_cache_and_retry` FAIL (no `job_type` key — the route still blocks synchronously). `cached_cloud_session_stays_synchronous` may already PASS (it exercises current behaviour); that's fine.

- [ ] **Step 3: Implement the async route**

In the `/load-folder` route (as left by Task 1), replace these three lines:

```python
        folder = (request.get_json() or {}).get("folder", "").strip()
        if storage.is_cloud() and folder and not Path(folder).exists():
            folder = _ensure_cloud_session(folder)
```

with:

```python
        folder = (request.get_json() or {}).get("folder", "").strip()
        if storage.is_cloud() and folder and not Path(folder).exists():
            session_key = folder
            with _cloud_session_lock:
                ready = _cloud_session_ready.get(session_key)
                cached = (ready is not None and ready.is_set()
                          and session_key in _cloud_session_dirs)
            if not cached:
                # Download runs as a cancellable background job; the frontend
                # polls /status/<job_id> and cancels via DELETE /jobs/<job_id>.
                job_id = _new_job("session_download", 0)

                def _download():
                    cancel_event = _jobs[job_id]["cancel"]
                    try:
                        local_dir = _ensure_cloud_session(
                            session_key, job_id=job_id, cancel_event=cancel_event)
                    except SessionDownloadCancelled:
                        with _jobs_lock:
                            if job_id in _jobs and _jobs[job_id]["status"] == "running":
                                _jobs[job_id]["status"] = "cancelled"
                        return
                    except Exception as exc:
                        with _jobs_lock:
                            if job_id in _jobs:
                                _jobs[job_id]["status"] = "error"
                                _jobs[job_id]["error"] = str(exc)
                        return
                    result, error = _scan_load_folder(local_dir)
                    with _jobs_lock:
                        if job_id not in _jobs:
                            return
                        if error:
                            _jobs[job_id]["status"] = "error"
                            _jobs[job_id]["error"] = error
                        else:
                            _jobs[job_id]["status"] = "done"
                            _jobs[job_id]["result"] = {
                                "folder": result["folder"],
                                "files": result["files"],
                            }

                threading.Thread(target=_download, daemon=True).start()
                return jsonify({"job_id": job_id, "job_type": "session_download"})
            folder = _ensure_cloud_session(session_key)
```

The rest of the route (404 check, `_scan_load_folder` call, transcription branch) is unchanged.

- [ ] **Step 4: Run the tests**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -v`
Expected: all PASS, including the three new ones and the pre-existing cloud/transcription tests.

- [ ] **Step 5: Commit**

```powershell
git add app.py tests/test_app.py; git commit -m "feat: cloud session download runs as a cancellable session_download job"
```

---

### Task 4: Loading modal HTML + CSS

**Files:**
- Modify: `templates/index.html` (insert after `#not-downloaded-modal`, i.e. after line 260, before the closing `</div>` of `#app`)
- Modify: `static/style.css` (add after the `.progress-bar` rule at line 308; delete the `.error-msg.folder-loading` rule at lines 279-280)

- [ ] **Step 1: Add the modal markup**

Insert into `templates/index.html` directly after the closing `</div>` of `#not-downloaded-modal` (line 260), following that modal's inline-style pattern:

```html
  <!-- Loading-folder modal — shown while /load-folder (and any cloud session
       download) is in flight; ✕ cancels the load -->
  <div id="loading-folder-modal" class="overlay hidden">
    <div class="overlay-card" style="max-width:420px">
      <button id="btn-loading-folder-cancel" class="overlay-close" aria-label="Cancel loading folder">✕</button>
      <div style="font-size:15px;font-weight:600;color:var(--ink);margin-bottom:4px">
        Loading folder
      </div>
      <div id="loading-folder-name" style="color:var(--body);font-size:13px;margin-bottom:14px"></div>
      <div class="progress-bar-wrap">
        <div id="loading-folder-bar" class="progress-bar" style="width:0%"></div>
      </div>
      <div id="loading-folder-status" style="color:var(--muted);font-size:12px;margin-top:8px">Opening folder…</div>
    </div>
  </div>
```

- [ ] **Step 2: Add the indeterminate-progress CSS and remove the dead rule**

In `static/style.css`, directly after the `.progress-bar` rule (line 308), add:

```css
/* Indeterminate variant — loading-folder modal before real progress exists
   (local scans, cloud pre-download). JS clears the inline width first. */
.progress-bar.indeterminate {
  width: 40%;
  animation: progress-indeterminate 1.2s ease-in-out infinite;
}
@keyframes progress-indeterminate {
  from { margin-left: -40%; }
  to   { margin-left: 100%; }
}
```

Then delete lines 279-280 (the rule is dead once Task 5 lands — `folder-loading` is only referenced by the old `openFolder`):

```css
/* Loading state overrides the red error colour — higher specificity wins */
.error-msg.folder-loading { color: var(--muted); font-style: italic; }
```

- [ ] **Step 3: Commit**

```powershell
git add templates/index.html static/style.css; git commit -m "feat: loading-folder modal markup and indeterminate progress style"
```

---

### Task 5: Frontend — `openFolder` rewrite with modal, cancel, and job polling

**Files:**
- Modify: `static/app.js:296-351` — replace the whole `openFolder` function with the block below (which also adds the modal helpers, the ✕ handler, `_pollSessionDownload`, and `_enterFolder`)

Existing helpers used (all already defined in app.js): `$`, `showScreen`, `showWorkspace`, `loadTranscripts`, `pollTranscription`, `state`.

- [ ] **Step 1: Replace `openFolder`**

Delete the current `openFolder` (app.js:296-351, from `async function openFolder` to its closing `}`) and put this in its place:

```js
// ─── Loading-folder modal ─────────────────────────────────────────────────────
// One load is in flight at a time. Each openFolder call captures its own ctx,
// so late responses from a cancelled load are ignored.
let _loadingCtx = null;

function _showLoadingModal(name) {
  _loadingCtx = { abort: null, jobId: null, pollTimer: null, cancelled: false };
  $('loading-folder-name').textContent = name;
  const bar = $('loading-folder-bar');
  bar.style.width = '';               // let the .indeterminate width apply
  bar.classList.add('indeterminate');
  $('loading-folder-status').textContent = 'Opening folder…';
  $('loading-folder-modal').classList.remove('hidden');
  return _loadingCtx;
}

function _closeLoadingModal() {
  $('loading-folder-modal').classList.add('hidden');
  $('loading-folder-bar').classList.remove('indeterminate');
  _loadingCtx = null;
}

$('btn-loading-folder-cancel').addEventListener('click', () => {
  const ctx = _loadingCtx;
  if (!ctx) return;
  ctx.cancelled = true;
  if (ctx.abort) ctx.abort.abort();
  if (ctx.pollTimer) clearInterval(ctx.pollTimer);
  if (ctx.jobId) fetch(`/jobs/${ctx.jobId}`, { method: 'DELETE' }).catch(() => {});
  _closeLoadingModal();
  const btnLoad = $('btn-load-folder');
  if (btnLoad) btnLoad.disabled = false;
  // No-op when already on the picker; returns there from the upload flow.
  showScreen('screen-folder-picker');
});

async function openFolder(folder, displayName) {
  const folderErr = $('folder-error');
  const btnLoad   = $('btn-load-folder');
  const name = displayName || folder.split(/[\\/]/).pop() || folder;
  folderErr.classList.add('hidden');
  if (btnLoad) btnLoad.disabled = true;

  const ctx = _showLoadingModal(name + '/');
  ctx.abort = new AbortController();

  const fail = (msg) => {
    _closeLoadingModal();
    if (btnLoad) btnLoad.disabled = false;
    showScreen('screen-folder-picker');
    folderErr.textContent = msg;
    folderErr.classList.remove('hidden');
  };

  let resp, data;
  try {
    resp = await fetch('/load-folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder }),
      signal: ctx.abort.signal,
    });
    data = await resp.json();
  } catch (err) {
    if (ctx.cancelled) return;   // user hit ✕ — handler already cleaned up
    fail('Could not open folder — try uploading your files again.');
    return;
  }
  if (ctx.cancelled) return;

  if (!resp.ok) {
    fail(data.error || 'Failed to open folder');
    return;
  }

  if (data.job_type === 'session_download') {
    // Cloud: transcripts are downloading server-side — poll for progress.
    ctx.jobId = data.job_id;
    const bar = $('loading-folder-bar');
    bar.classList.remove('indeterminate');
    bar.style.width = '0%';
    _pollSessionDownload(ctx, folder, name, fail);
    return;
  }

  _closeLoadingModal();
  if (btnLoad) btnLoad.disabled = false;
  await _enterFolder(folder, name, data);
}

function _pollSessionDownload(ctx, folder, displayName, fail) {
  ctx.pollTimer = setInterval(async () => {
    let job;
    try {
      const resp = await fetch(`/status/${ctx.jobId}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      job = await resp.json();
    } catch (err) {
      clearInterval(ctx.pollTimer);
      if (ctx.cancelled) return;
      fail('Lost contact with server while loading the folder.');
      return;
    }
    if (ctx.cancelled) return;

    if (job.total > 0) {
      const pct = Math.round((job.done / job.total) * 100);
      $('loading-folder-bar').style.width = pct + '%';
      $('loading-folder-status').textContent =
        `Downloading transcripts… ${job.done} of ${job.total}`;
    }

    if (job.status === 'done') {
      clearInterval(ctx.pollTimer);
      _closeLoadingModal();
      const btnLoad = $('btn-load-folder');
      if (btnLoad) btnLoad.disabled = false;
      await _enterFolder(folder, displayName, job.result || {});
    } else if (job.status === 'error') {
      clearInterval(ctx.pollTimer);
      fail(job.error || 'Failed to load folder');
    } else if (job.status === 'cancelled') {
      // Cancelled from another tab/path — mirror the ✕ cleanup.
      clearInterval(ctx.pollTimer);
      _closeLoadingModal();
      const btnLoad = $('btn-load-folder');
      if (btnLoad) btnLoad.disabled = false;
    }
  }, 500);
}

async function _enterFolder(folder, displayName, data) {
  state.folder = folder;
  state.folderName = displayName || folder.split(/[\\/]/).pop();
  state.files = [];
  state.checked = {};
  state.highlighted = {};

  $('folder-badge').textContent = state.folderName + '/  ▾';

  if (data.job_id) {
    // Needs transcription (local mode)
    showScreen('screen-transcribing');
    $('topbar-controls').classList.add('hidden');
    pollTranscription(data.job_id, folder);
  } else {
    await loadTranscripts(folder);
    showWorkspace();
  }
}
```

Notes for the implementer:
- `state.folder` stays the **original** argument (session key in cloud mode, path in local mode) — `/transcripts` and `/analyze` map a session key to the cached dir server-side. Do not substitute the job result's local tmp path.
- All existing callers of `openFolder(...)` (recent list, dropdown, buttons, cloud upload flow) need no changes — the modal lives inside `openFolder`.
- Do not touch `pollTranscription`.

- [ ] **Step 2: Sanity-check the file parses**

Run: `node --check static/app.js`
Expected: no output (exit 0). If `node` is unavailable, skip — the browser check in Task 6 covers it.

- [ ] **Step 3: Run the backend suite (guards against template/static route regressions)**

Run: `.\venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```powershell
git add static/app.js; git commit -m "feat: loading-folder modal with cancel replaces inline loading text"
```

---

### Task 6: End-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

Run: `.\venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 2: Browser verification (local mode)**

Start the main app (port 5000) and open it in the preview browser. Verify:

1. The folder picker shows recent folders. Click one.
2. The "Loading folder" modal appears (it may only flash — local loads are fast). No small italic "Loading folder…" text appears in the error area.
3. The workspace loads normally afterwards; no console errors.
4. Reload, click a recent folder again, and immediately click the modal's ✕ if you can catch it — the modal closes, the picker is intact, and no console errors follow. (If the load finishes before you can click, that's acceptable — the local path is near-instant by design.)
5. Enter a bogus path in the folder input and press Enter — the modal closes and the red error text shows.

- [ ] **Step 3: Commit any fixes discovered, then report**

If verification exposed bugs, fix them, re-run the suite, and commit with a `fix:` message. Report results honestly — including anything that could not be verified (e.g. real R2 cloud mode is not reachable locally; it is covered by the Task 3 tests).
