# Cloud Generation Speed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate two serial cloud-mode bottlenecks: downloading all session videos before generation starts, and uploading the finished reel only after stitching completes.

**Architecture:** Feature 1 passes presigned R2 URLs directly to ffmpeg instead of downloading video files — ffmpeg uses HTTP range requests to pull only the bytes it needs. Feature 2 pipes ffmpeg's stitch output simultaneously to the local file and to an S3 multipart upload, so upload finishes at the same time as the stitch.

**Tech Stack:** Python, Flask, boto3, ffmpeg/ffprobe (HTTP input support), `subprocess.Popen` with stdout pipe, `io.RawIOBase` tee stream

---

## File Map

| File | Change |
|------|--------|
| `generator_app.py` | Add `video_paths`/`video_urls` params to `_run_generation`; rewrite `/generate` cloud branch; replace Phase 3 stitch+upload with streaming version; add `import io` |
| `video_editor.py` | Add `stitch_clips_to_pipe()` function |
| `storage.py` | Add `upload_stream()` function |
| `tests/test_generator_cloud.py` | Add Feature 1 assertions; update existing test for Feature 2 |
| `tests/test_video_editor.py` | Add `stitch_clips_to_pipe` tests |
| `tests/test_storage.py` | Add `upload_stream` test |

---

## Task 1: Extend `_run_generation` to accept pre-computed paths and URLs

**Files:**
- Modify: `generator_app.py` (signature + scanning loop + plan phase)
- Test: `tests/test_generator_cloud.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_generator_cloud.py`:

```python
def test_run_generation_skips_scan_videos_when_paths_provided(tmp_path):
    """When video_paths is provided, _run_generation must not call scan_videos."""
    import importlib, generator_app
    importlib.reload(generator_app)

    (tmp_path / "video.txt").write_text("[0:00] Speaker: Hello world.", encoding="utf-8")
    vp = tmp_path / "video.mp4"
    job_id = generator_app._new_job("generation", 1)

    with patch("generator_app.scan_videos") as mock_scan, \
         patch("generator_app.get_video_duration", return_value=None), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.make_title_card"), \
         patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app._library_add"):
        generator_app._run_generation(
            job_id, str(tmp_path),
            {"video.mp4": ["[0:00] Speaker: Hello world."]},
            "test prompt", "out.mp4",
            video_paths=[vp],
            video_urls={"video.mp4": "https://r2.example.com/presigned/video.mp4"},
        )

    mock_scan.assert_not_called()


def test_run_generation_passes_presigned_url_to_extract_clip(tmp_path):
    """When video_urls is provided, extract_clip must receive the presigned URL."""
    import importlib, generator_app
    importlib.reload(generator_app)

    (tmp_path / "video.txt").write_text("[0:00] Speaker: Hello world.", encoding="utf-8")
    vp = tmp_path / "video.mp4"
    presigned = "https://r2.example.com/presigned/video.mp4?token=abc"
    captured = []

    job_id = generator_app._new_job("generation", 1)

    with patch("generator_app.get_video_duration", return_value=None), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.make_title_card"), \
         patch("generator_app.extract_clip", side_effect=lambda vp, *a, **kw: captured.append(vp)), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app._library_add"):
        generator_app._run_generation(
            job_id, str(tmp_path),
            {"video.mp4": ["[0:00] Speaker: Hello world."]},
            "test prompt", "out.mp4",
            video_paths=[vp],
            video_urls={"video.mp4": presigned},
        )

    assert captured == [presigned]
```

- [ ] **Step 2: Run tests to confirm they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_generator_cloud.py::test_run_generation_skips_scan_videos_when_paths_provided tests/test_generator_cloud.py::test_run_generation_passes_presigned_url_to_extract_clip -v
```

Expected: FAIL — `_run_generation` does not accept `video_paths` or `video_urls` yet.

- [ ] **Step 3: Implement the changes in `generator_app.py`**

Change the `_run_generation` signature and body. Replace the entire function signature and the opening scan block:

**Signature** (find and replace):
```python
# OLD:
def _run_generation(job_id: str, folder: str,
                    selections: dict, prompt: str, output_filename: str,
                    session_key: str = None) -> None:

# NEW:
def _run_generation(job_id: str, folder: str,
                    selections: dict, prompt: str, output_filename: str,
                    session_key: str = None,
                    video_paths: list = None,
                    video_urls: dict = None) -> None:
```

**Scan block** (find and replace at the top of the function body):
```python
# OLD:
    job = _jobs[job_id]
    try:
        video_paths = scan_videos(folder)
    except Exception as exc:
        with _jobs_lock:
            job["status"] = "error"
            job["error"] = str(exc)
        return
    video_paths = _filter_generated_reels(video_paths)

# NEW:
    job = _jobs[job_id]
    if video_paths is None:
        try:
            video_paths = scan_videos(folder)
        except Exception as exc:
            with _jobs_lock:
                job["status"] = "error"
                job["error"] = str(exc)
            return
        video_paths = _filter_generated_reels(video_paths)
```

**Scanning loop** — add `ffmpeg_input` variable and store it in `video_segments`. Find this block inside the `for vp in video_paths:` loop:
```python
# OLD:
        all_lines = _parse_transcript_lines(txt_path.read_text(encoding="utf-8"))
        duration = get_video_duration(str(vp))
        segs = _group_lines_into_segments(all_lines, set(selected_raws), video_duration=duration)

        if segs:
            _append_log(job_id, f"✓ {vp.name} — {len(segs)} segment(s)")
            video_segments.append((vp, segs))

# NEW:
        all_lines = _parse_transcript_lines(txt_path.read_text(encoding="utf-8"))
        ffmpeg_input = video_urls[vp.name] if video_urls else str(vp)
        duration = get_video_duration(ffmpeg_input)
        segs = _group_lines_into_segments(all_lines, set(selected_raws), video_duration=duration)

        if segs:
            _append_log(job_id, f"✓ {vp.name} — {len(segs)} segment(s)")
            video_segments.append((vp, segs, ffmpeg_input))
```

**Plan phase** — update the `for vp, segs in video_segments:` loop to unpack the third element:
```python
# OLD:
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

# NEW:
        for vp, segs, ffmpeg_input in video_segments:
            try:
                width, height = get_video_dimensions(ffmpeg_input)
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
                    "video_path": ffmpeg_input,
                    "start_sec": start_sec,
                    "end_sec": end_sec,
                    "ok": False,
                    "error": None,
                })
```

- [ ] **Step 4: Run tests to confirm they pass**

```
.\venv\Scripts\python.exe -m pytest tests/test_generator_cloud.py::test_run_generation_skips_scan_videos_when_paths_provided tests/test_generator_cloud.py::test_run_generation_passes_presigned_url_to_extract_clip -v
```

Expected: PASS

- [ ] **Step 5: Run the full test suite to confirm nothing is broken**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```
git add generator_app.py tests/test_generator_cloud.py
git commit -m "feat: extend _run_generation to accept pre-computed video_paths and video_urls"
```

---

## Task 2: Update `/generate` cloud branch to use presigned URLs

**Files:**
- Modify: `generator_app.py` (the `/generate` route, cloud branch only)
- Test: `tests/test_generator_cloud.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_generator_cloud.py`:

```python
def test_generate_cloud_does_not_download_video_files(cloud_client, tmp_path):
    """In cloud mode, /generate must NOT call download_file for video files."""
    session_key = "sessions/test456"
    txt_content = "[0:00] Speaker: Hello world."

    def fake_list_keys(prefix):
        return [f"{session_key}/video.mp4", f"{session_key}/video.txt"]

    downloaded_keys = []

    def fake_download(key, local_path):
        downloaded_keys.append(key)
        if key.endswith(".txt"):
            Path(local_path).write_text(txt_content, encoding="utf-8")

    selections = {"video.mp4": ["[0:00] Speaker: Hello world."]}

    with patch("generator_app.storage.list_keys", side_effect=fake_list_keys), \
         patch("generator_app.storage.download_file", side_effect=fake_download), \
         patch("generator_app.storage.presigned_url", return_value="https://r2.example.com/video.mp4"), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.get_video_duration", return_value=None), \
         patch("generator_app.make_title_card"), \
         patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.storage.upload_file"), \
         patch("generator_app._library_add"):
        resp = cloud_client.post("/generate", json={
            "session_key": session_key,
            "mode": "checkbox",
            "selections": selections,
            "prompt": "test",
            "output_filename": "out.mp4",
        })

    assert resp.status_code == 200
    # No video file should have been downloaded
    assert not any(k.endswith(".mp4") for k in downloaded_keys), \
        f"Expected no .mp4 downloads, but got: {downloaded_keys}"
    # The txt file for the selected video should have been downloaded
    assert any(k.endswith(".txt") for k in downloaded_keys)


def test_generate_cloud_calls_presigned_url_for_selected_video(cloud_client, tmp_path):
    """In cloud mode, /generate must call storage.presigned_url for the selected video key."""
    session_key = "sessions/test789"
    txt_content = "[0:00] Speaker: Hello world."

    def fake_list_keys(prefix):
        return [f"{session_key}/video.mp4", f"{session_key}/video.txt"]

    def fake_download(key, local_path):
        if key.endswith(".txt"):
            Path(local_path).write_text(txt_content, encoding="utf-8")

    presigned_calls = []

    def fake_presigned(key, expires=3600):
        presigned_calls.append((key, expires))
        return f"https://r2.example.com/{key}"

    selections = {"video.mp4": ["[0:00] Speaker: Hello world."]}

    with patch("generator_app.storage.list_keys", side_effect=fake_list_keys), \
         patch("generator_app.storage.download_file", side_effect=fake_download), \
         patch("generator_app.storage.presigned_url", side_effect=fake_presigned), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.get_video_duration", return_value=None), \
         patch("generator_app.make_title_card"), \
         patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.storage.upload_file"), \
         patch("generator_app._library_add"):
        resp = cloud_client.post("/generate", json={
            "session_key": session_key,
            "mode": "checkbox",
            "selections": selections,
            "prompt": "test",
            "output_filename": "out.mp4",
        })

    assert resp.status_code == 200
    # presigned_url must have been called for the video key with a 2hr TTL
    video_key_calls = [c for c in presigned_calls if c[0].endswith(".mp4") and "out.mp4" not in c[0]]
    assert len(video_key_calls) >= 1
    assert video_key_calls[0][1] == 7200, "Video input presigned URL must use 2-hour TTL"
```

- [ ] **Step 2: Run tests to confirm they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_generator_cloud.py::test_generate_cloud_does_not_download_video_files tests/test_generator_cloud.py::test_generate_cloud_calls_presigned_url_for_selected_video -v
```

Expected: FAIL

- [ ] **Step 3: Implement the new cloud branch in `/generate`**

In `generator_app.py`, replace the `/generate` function's cloud setup block and the subsequent scan. The full rewrite of the relevant section (from the `if storage.is_cloud():` block through to the `job_id = _new_job(...)` line):

```python
    VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    video_paths_for_gen = None
    video_urls_for_gen = None

    if storage.is_cloud():
        if not session_key:
            return jsonify({"error": "session_key required in cloud mode"}), 400
        tmp_session_dir = tempfile.mkdtemp(prefix="sizzle_gen_")
        _tmp_dir_to_cleanup = None  # kept alive for /video/<job_id> serving

        all_keys = storage.list_keys(session_key + "/")
        selected_filenames = set(selections.keys())

        # Download only transcript files for selected videos
        for key in all_keys:
            p = Path(key)
            if p.suffix.lower() == ".txt":
                stem = p.stem
                if any(Path(fn).stem == stem for fn in selected_filenames):
                    storage.download_file(key, os.path.join(tmp_session_dir, p.name))

        # Generate presigned URLs (2hr TTL) for selected video files only
        video_urls_for_gen = {}
        for key in all_keys:
            p = Path(key)
            if p.suffix.lower() in VIDEO_EXTS and p.name in selected_filenames:
                video_urls_for_gen[p.name] = storage.presigned_url(key, expires=7200)

        # Synthetic Path objects — only .name and .with_suffix(".txt") are used
        video_paths_for_gen = sorted(
            [Path(tmp_session_dir) / fn for fn in video_urls_for_gen],
            key=lambda p: p.name,
        )
        selected_count = len(video_paths_for_gen)
        folder = tmp_session_dir
    else:
        folder = body.get("folder", "").strip()
        if not folder or not Path(folder).exists():
            return jsonify({"error": "Folder not found"}), 404
        _tmp_dir_to_cleanup = None
```

Then replace the scan block (immediately after) with a local-only scan:

```python
    try:
        check_ffmpeg()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500

    if not storage.is_cloud():
        try:
            video_paths = scan_videos(folder)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 422
        video_paths = _filter_generated_reels(video_paths)
        selected_count = sum(1 for p in video_paths if selections.get(p.name))

    job_id = _new_job("generation", max(selected_count, 1))
```

Finally, update both `_run_generation` call sites (testing mode and threaded mode) to pass the new params:

```python
        # testing mode:
        _run_generation(
            job_id, folder, selections, prompt, output_filename,
            session_key=session_key,
            video_paths=video_paths_for_gen,
            video_urls=video_urls_for_gen,
        )
```

```python
        # threaded mode:
        def _run_with_cleanup():
            try:
                _run_generation(
                    job_id, folder, selections, prompt, output_filename,
                    session_key=session_key,
                    video_paths=video_paths_for_gen,
                    video_urls=video_urls_for_gen,
                )
            finally:
                if _tmp_dir_to_cleanup:
                    shutil.rmtree(_tmp_dir_to_cleanup, ignore_errors=True)
```

- [ ] **Step 4: Run new tests**

```
.\venv\Scripts\python.exe -m pytest tests/test_generator_cloud.py::test_generate_cloud_does_not_download_video_files tests/test_generator_cloud.py::test_generate_cloud_calls_presigned_url_for_selected_video -v
```

Expected: PASS

- [ ] **Step 5: Run the full suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```
git add generator_app.py tests/test_generator_cloud.py
git commit -m "feat: cloud /generate downloads only txts and uses presigned URLs for video inputs"
```

---

## Task 3: Add `stitch_clips_to_pipe` to `video_editor.py`

**Files:**
- Modify: `video_editor.py`
- Test: `tests/test_video_editor.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_video_editor.py`:

```python
def test_stitch_clips_to_pipe_returns_popen_with_pipe_stdout():
    """stitch_clips_to_pipe must return a Popen with stdout=PIPE and the right ffmpeg flags."""
    from video_editor import stitch_clips_to_pipe
    import subprocess

    with patch("video_editor.subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        result = stitch_clips_to_pipe(["/tmp/a.mp4", "/tmp/b.mp4"])

    assert result is mock_proc
    call_args = mock_popen.call_args
    cmd = call_args[0][0]
    kwargs = call_args[1]

    assert kwargs.get("stdout") == subprocess.PIPE
    assert kwargs.get("stderr") == subprocess.PIPE
    assert "pipe:1" in cmd
    assert "-movflags" in cmd
    movflags_val = cmd[cmd.index("-movflags") + 1]
    assert "frag_keyframe" in movflags_val
    assert "empty_moov" in movflags_val
    # Must be a concat command
    assert "concat" in cmd
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"


def test_stitch_clips_to_pipe_concat_list_contains_paths():
    """stitch_clips_to_pipe must write clip paths into the concat list file."""
    from video_editor import stitch_clips_to_pipe
    import subprocess

    captured_cmd = []

    def fake_popen(cmd, **kwargs):
        captured_cmd.extend(cmd)
        m = MagicMock()
        m._concat_list_path = cmd[cmd.index("-i") + 1]
        return m

    with patch("video_editor.subprocess.Popen", side_effect=fake_popen):
        proc = stitch_clips_to_pipe(["/tmp/clip_0.mp4", "/tmp/clip_1.mp4"])

    list_path = proc._concat_list_path
    content = Path(list_path).read_text()
    assert "/tmp/clip_0.mp4" in content
    assert "/tmp/clip_1.mp4" in content
    # Cleanup
    Path(list_path).unlink(missing_ok=True)
```

- [ ] **Step 2: Run to confirm they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_video_editor.py::test_stitch_clips_to_pipe_returns_popen_with_pipe_stdout tests/test_video_editor.py::test_stitch_clips_to_pipe_concat_list_contains_paths -v
```

Expected: FAIL — `stitch_clips_to_pipe` not defined.

- [ ] **Step 3: Implement `stitch_clips_to_pipe` in `video_editor.py`**

Add after the existing `stitch_clips` function:

```python
def stitch_clips_to_pipe(clip_paths: list[str]) -> subprocess.Popen:
    """Like stitch_clips but streams fragmented MP4 to stdout instead of writing a file.

    Returns a Popen object. Caller must:
    - Read proc.stdout (to consume the stream and avoid pipe buffer deadlock)
    - Drain proc.stderr in a separate thread (to prevent ffmpeg blocking on a full pipe)
    - Call proc.wait() after stdout is exhausted
    - Delete proc._concat_list_path (the temp concat list file) after proc.wait()
    """
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    concat_list_path = f.name
    for path in clip_paths:
        f.write(f"file '{Path(path).as_posix()}'\n")
    f.close()

    proc = subprocess.Popen(
        [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list_path,
            "-c", "copy",
            "-movflags", "frag_keyframe+empty_moov",
            "-f", "mp4",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    proc._concat_list_path = concat_list_path
    return proc
```

- [ ] **Step 4: Run tests**

```
.\venv\Scripts\python.exe -m pytest tests/test_video_editor.py::test_stitch_clips_to_pipe_returns_popen_with_pipe_stdout tests/test_video_editor.py::test_stitch_clips_to_pipe_concat_list_contains_paths -v
```

Expected: PASS

- [ ] **Step 5: Run full suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all passing.

- [ ] **Step 6: Commit**

```
git add video_editor.py tests/test_video_editor.py
git commit -m "feat: add stitch_clips_to_pipe for streaming fragmented MP4 output"
```

---

## Task 4: Add `upload_stream` to `storage.py`

**Files:**
- Modify: `storage.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_storage.py` (look at existing tests in that file for the fixture pattern; add below them):

```python
def test_upload_stream_calls_upload_fileobj_in_cloud_mode(monkeypatch):
    """upload_stream must call boto3 upload_fileobj with the stream and correct key."""
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY", "key")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:9000")

    import importlib, storage
    importlib.reload(storage)

    mock_s3 = MagicMock()
    fake_stream = io.BytesIO(b"fake video bytes")

    with patch("storage._s3", return_value=mock_s3), \
         patch("storage._bucket", return_value="test-bucket"):
        storage.upload_stream("sessions/abc/reel.mp4", fake_stream)

    mock_s3.upload_fileobj.assert_called_once()
    call_args = mock_s3.upload_fileobj.call_args
    assert call_args[0][0] is fake_stream          # stream
    assert call_args[0][1] == "test-bucket"        # bucket
    assert call_args[0][2] == "sessions/abc/reel.mp4"  # key
```

Note: `io` and `MagicMock` are already imported in `test_storage.py`. If not, add `import io` and `from unittest.mock import patch, MagicMock` at the top.

- [ ] **Step 2: Run to confirm it fails**

```
.\venv\Scripts\python.exe -m pytest tests/test_storage.py::test_upload_stream_calls_upload_fileobj_in_cloud_mode -v
```

Expected: FAIL — `upload_stream` not defined.

- [ ] **Step 3: Implement `upload_stream` in `storage.py`**

Add after the `presigned_put_url` function:

```python
def upload_stream(key: str, readable) -> None:
    """Upload a readable byte stream to S3/R2 using boto3's multipart transfer.

    boto3 upload_fileobj handles multipart chunking automatically (default 8MB parts).
    The stream must implement read(n) -> bytes; empty bytes signals EOF.
    """
    _s3().upload_fileobj(
        readable,
        _bucket(),
        key,
        ExtraArgs={"ContentType": "video/mp4"},
    )
```

- [ ] **Step 4: Run test**

```
.\venv\Scripts\python.exe -m pytest tests/test_storage.py::test_upload_stream_calls_upload_fileobj_in_cloud_mode -v
```

Expected: PASS

- [ ] **Step 5: Run full suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all passing.

- [ ] **Step 6: Commit**

```
git add storage.py tests/test_storage.py
git commit -m "feat: add upload_stream to storage.py for streaming S3 multipart upload"
```

---

## Task 5: Streaming stitch+upload in `_run_generation` Phase 3

**Files:**
- Modify: `generator_app.py` (Phase 3 + add `import io`)
- Modify: `tests/test_generator_cloud.py` (update existing test + add streaming test)

- [ ] **Step 1: Write failing test and update the existing cloud test**

**Update** `test_generate_endpoint_accepts_session_key_in_cloud_mode` in `tests/test_generator_cloud.py` — replace `stitch_clips` and `upload_file` patches with `stitch_clips_to_pipe` and `upload_stream`:

```python
def test_generate_endpoint_accepts_session_key_in_cloud_mode(cloud_client, tmp_path):
    """POST /generate in cloud mode accepts session_key and uses presigned URLs + streaming upload."""
    session_key = "sessions/test123"
    txt_content = "[0:00] Speaker: Hello world."

    def fake_list_keys(prefix):
        return [f"{session_key}/video.mp4", f"{session_key}/video.txt"]

    def fake_download(key, local_path):
        if key.endswith(".txt"):
            Path(local_path).write_text(txt_content, encoding="utf-8")

    selections = {"video.mp4": ["[0:00] Speaker: Hello world."]}

    # stitch_clips_to_pipe returns a Popen-like object; its stdout is read by the tee loop
    mock_proc = MagicMock()
    mock_proc.stdout = io.BytesIO(b"fake mp4 data")
    mock_proc.stderr = io.BytesIO(b"")
    mock_proc.returncode = 0
    mock_proc._concat_list_path = str(tmp_path / "_concat.txt")
    Path(mock_proc._concat_list_path).touch()
    mock_proc.wait.return_value = None

    with patch("generator_app.storage.list_keys", side_effect=fake_list_keys), \
         patch("generator_app.storage.download_file", side_effect=fake_download), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.get_video_duration", return_value=None), \
         patch("generator_app.make_title_card"), \
         patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips_to_pipe", return_value=mock_proc), \
         patch("generator_app.storage.upload_stream"), \
         patch("generator_app.storage.presigned_url", return_value="https://s3/reel.mp4"), \
         patch("generator_app._library_add"):
        resp = cloud_client.post("/generate", json={
            "session_key": session_key,
            "mode": "checkbox",
            "selections": selections,
            "prompt": "test",
            "output_filename": "out.mp4",
        })

    assert resp.status_code == 200
    body = resp.get_json()
    assert "job_id" in body
```

**Add** a new test that asserts `upload_stream` is called instead of `upload_file`:

```python
def test_generate_cloud_uses_streaming_upload_not_upload_file(cloud_client, tmp_path):
    """In cloud mode, generation must use upload_stream for the reel, not upload_file."""
    session_key = "sessions/streaming_test"
    txt_content = "[0:00] Speaker: Hello world."

    def fake_list_keys(prefix):
        return [f"{session_key}/video.mp4", f"{session_key}/video.txt"]

    def fake_download(key, local_path):
        if key.endswith(".txt"):
            Path(local_path).write_text(txt_content, encoding="utf-8")

    mock_proc = MagicMock()
    mock_proc.stdout = io.BytesIO(b"fake mp4 data")
    mock_proc.stderr = io.BytesIO(b"")
    mock_proc.returncode = 0
    mock_proc._concat_list_path = str(tmp_path / "_concat2.txt")
    Path(mock_proc._concat_list_path).touch()
    mock_proc.wait.return_value = None

    mock_upload_stream = MagicMock()
    mock_upload_file = MagicMock()

    with patch("generator_app.storage.list_keys", side_effect=fake_list_keys), \
         patch("generator_app.storage.download_file", side_effect=fake_download), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.get_video_duration", return_value=None), \
         patch("generator_app.make_title_card"), \
         patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips_to_pipe", return_value=mock_proc), \
         patch("generator_app.storage.upload_stream", mock_upload_stream), \
         patch("generator_app.storage.upload_file", mock_upload_file), \
         patch("generator_app.storage.presigned_url", return_value="https://s3/reel.mp4"), \
         patch("generator_app._library_add"):
        cloud_client.post("/generate", json={
            "session_key": session_key,
            "mode": "checkbox",
            "selections": {"video.mp4": ["[0:00] Speaker: Hello world."]},
            "prompt": "test",
            "output_filename": "out.mp4",
        })

    mock_upload_stream.assert_called_once()
    mock_upload_file.assert_not_called()
```

- [ ] **Step 2: Run to confirm new test fails and updated test also fails (stitch_clips_to_pipe not imported yet in generator_app)**

```
.\venv\Scripts\python.exe -m pytest tests/test_generator_cloud.py::test_generate_endpoint_accepts_session_key_in_cloud_mode tests/test_generator_cloud.py::test_generate_cloud_uses_streaming_upload_not_upload_file -v
```

Expected: FAIL

- [ ] **Step 3: Add `import io` to `generator_app.py`**

In `generator_app.py`, add `io` to the stdlib imports block:

```python
# OLD top of file imports:
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import concurrent.futures

# NEW:
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import concurrent.futures
```

Also add `stitch_clips_to_pipe` to the import from `video_editor`:

```python
# OLD:
from video_editor import check_ffmpeg, extract_clip, parse_timestamp_to_seconds, stitch_clips

# NEW:
from video_editor import check_ffmpeg, extract_clip, parse_timestamp_to_seconds, stitch_clips, stitch_clips_to_pipe
```

- [ ] **Step 4: Replace Phase 3 stitch+upload in `_run_generation`**

Find this block in `_run_generation` (around line 508):

```python
        _append_log(job_id, "· Stitching reel...")
        try:
            stitch_clips(clip_paths, output_path)
        except Exception as exc:
            with _jobs_lock:
                job["status"] = "error"
                job["error"] = f"Stitch failed: {exc}"
            return
```

And separately, later in the function:

```python
    # In cloud mode: upload the finished reel to S3 and add a presigned download URL.
    reel_download_url = None
    if storage.is_cloud() and session_key:
        reel_s3_key = f"{session_key}/{output_filename}"
        _append_log(job_id, f"⟳ Uploading reel to cloud storage…")
        try:
            storage.upload_file(output_path, reel_s3_key)
            reel_download_url = storage.presigned_url(reel_s3_key)
            _append_log(job_id, f"✓ Reel uploaded to cloud storage")
        except Exception as exc:
            _append_log(job_id, f"✗ Could not upload reel to cloud storage: {exc}")
```

Replace **both** blocks with this single combined block:

```python
        reel_s3_key = f"{session_key}/{output_filename}" if storage.is_cloud() and session_key else None
        reel_download_url = None

        if reel_s3_key:
            # Cloud mode: stream ffmpeg output simultaneously to local file + S3 upload.
            # proc.stdout → _TeeReader → [local_f write + upload_stream read]
            _append_log(job_id, "· Stitching reel and uploading to cloud storage...")
            proc = stitch_clips_to_pipe(clip_paths)

            stderr_buf: list = []

            def _drain_stderr():
                stderr_buf.append(proc.stderr.read())

            stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
            stderr_thread.start()

            upload_exc = None
            try:
                with open(output_path, "wb") as _local_f:
                    class _TeeReader(io.RawIOBase):
                        def readinto(self, b):
                            data = proc.stdout.read(len(b))
                            n = len(data)
                            b[:n] = data
                            if data:
                                _local_f.write(data)
                            return n

                    storage.upload_stream(reel_s3_key, _TeeReader())
            except Exception as exc:
                upload_exc = exc
                _append_log(job_id, f"✗ Streaming upload failed: {exc}")
            finally:
                try:
                    os.unlink(proc._concat_list_path)
                except OSError:
                    pass

            proc.wait()
            stderr_thread.join()

            if proc.returncode != 0:
                stderr_text = (stderr_buf[0] if stderr_buf else b"").decode(errors="replace")
                with _jobs_lock:
                    job["status"] = "error"
                    job["error"] = f"Stitch failed: {stderr_text[:300]}"
                return

            if upload_exc is None:
                reel_download_url = storage.presigned_url(reel_s3_key)
                _append_log(job_id, "✓ Reel stitched and uploaded to cloud storage")
            else:
                _append_log(job_id, "· Reel saved locally (cloud upload failed)")
        else:
            # Local mode: write to disk directly.
            _append_log(job_id, "· Stitching reel...")
            try:
                stitch_clips(clip_paths, output_path)
            except Exception as exc:
                with _jobs_lock:
                    job["status"] = "error"
                    job["error"] = f"Stitch failed: {exc}"
                return
```

- [ ] **Step 5: Run the new and updated tests**

```
.\venv\Scripts\python.exe -m pytest tests/test_generator_cloud.py::test_generate_endpoint_accepts_session_key_in_cloud_mode tests/test_generator_cloud.py::test_generate_cloud_uses_streaming_upload_not_upload_file -v
```

Expected: PASS

- [ ] **Step 6: Run the full suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```
git add generator_app.py tests/test_generator_cloud.py
git commit -m "feat: cloud stitch + S3 upload run concurrently via streaming pipe"
```

---

## Self-Review Checklist (completed before handoff)

- [x] **Spec coverage:** Feature 1 (download only txts, presigned URL inputs) → Tasks 1+2. Feature 2 (streaming upload) → Tasks 3+4+5. Error handling logged in both features. Local mode untouched throughout.
- [x] **No placeholders:** All steps contain complete code.
- [x] **Type consistency:** `video_segments` tuple changes from `(Path, list)` to `(Path, list, str)` in Task 1, and the plan-phase loop in the same task unpacks the third element. `stitch_clips_to_pipe` defined in Task 3 and imported in Task 5. `upload_stream` defined in Task 4 and called in Task 5.
- [x] **Existing test updated:** `test_generate_endpoint_accepts_session_key_in_cloud_mode` updated in Task 5 to patch `stitch_clips_to_pipe` and `upload_stream` instead of `stitch_clips` and `upload_file`.
