import os
import threading
from unittest.mock import patch, MagicMock
from pathlib import Path

import pytest
from app import create_app


@pytest.mark.parametrize("cpu_count,num_videos", [
    (1, 1), (1, 3), (2, 1), (2, 3), (4, 1), (4, 2), (4, 3),
    (8, 1), (8, 5), (8, 20), (16, 3), (3, 3), (6, 4),
])
def test_compute_transcription_parallelism_invariants(cpu_count, num_videos):
    from app import _compute_transcription_parallelism
    workers, cpu_threads = _compute_transcription_parallelism(cpu_count, num_videos)
    assert workers >= 1
    assert cpu_threads >= 1
    assert workers <= num_videos
    assert workers * cpu_threads <= cpu_count


def test_compute_transcription_parallelism_single_core():
    from app import _compute_transcription_parallelism
    assert _compute_transcription_parallelism(1, 5) == (1, 1)


def test_compute_transcription_parallelism_uses_half_cores_as_worker_ceiling():
    from app import _compute_transcription_parallelism
    # 8 cores, plenty of videos -> ceiling of 4 workers, 2 threads each
    assert _compute_transcription_parallelism(8, 10) == (4, 2)


def test_compute_transcription_parallelism_caps_workers_at_video_count():
    from app import _compute_transcription_parallelism
    # 8 cores but only 2 videos -> 2 workers, 4 threads each
    assert _compute_transcription_parallelism(8, 2) == (2, 4)


def test_compute_transcription_parallelism_zero_videos_is_safe():
    from app import _compute_transcription_parallelism
    workers, cpu_threads = _compute_transcription_parallelism(4, 0)
    assert workers >= 1
    assert cpu_threads >= 1
    assert workers * cpu_threads <= 4


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


def test_load_folder_returns_video_list(client, tmp_path):
    (tmp_path / "video1.mp4").touch()
    (tmp_path / "video2.mp4").touch()
    (tmp_path / "notes.txt").write_text("[0:01] Speaker: hi", encoding="utf-8")
    with patch("app._get_whisper_model", return_value=None), \
         patch("app.transcribe_video", return_value="[0:00] Speaker: hi"):
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


def test_cancel_does_not_overwrite_done_status(client):
    """Cancelling a completed transcription job must leave status='done'."""
    from app import _jobs, _jobs_lock
    job_id = "cancel-race-done-app-test"
    with _jobs_lock:
        _jobs[job_id] = {
            "type": "transcription", "status": "done",
            "total": 1, "done": 1, "log": [], "result": {},
            "error": None, "cancel": threading.Event(),
        }
    resp = client.delete(f"/jobs/{job_id}")
    assert resp.status_code == 200
    with _jobs_lock:
        assert _jobs[job_id]["status"] == "done"


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


def test_load_folder_excludes_generated_reels(client, tmp_path, monkeypatch):
    """Videos that appear in the library are treated as generated output, not source."""
    from unittest.mock import patch

    source = tmp_path / "source.mp4"
    reel = tmp_path / "NOBU_sizzle.mp4"
    source.touch()
    reel.touch()
    (tmp_path / "source.txt").write_text("[0:05] Speaker: Hi.", encoding="utf-8")

    library = [{
        "id": "abc", "filename": "NOBU_sizzle.mp4", "path": str(reel),
        "source_folder": "tmp/", "prompt": "", "duration_seconds": 10,
        "clip_count": 1, "created_at": "2026-01-01T00:00:00",
    }]
    with patch("storage.load_library", return_value=library):
        resp = client.post("/load-folder", json={"folder": str(tmp_path)})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "NOBU_sizzle.mp4" not in data["files"]
    assert "source.mp4" in data["files"]


def test_transcription_cancel_mid_video_stops_within_one_second(tmp_path, client):
    """Cancelling during transcription must exit within ~1s, not wait for full video."""
    import time
    import app as app_module
    from unittest.mock import patch

    # A transcription that would block for 2 seconds without the fix
    def slow_transcribe(path, model=None):
        time.sleep(2)
        return "[0:00] Speaker: hi"

    (tmp_path / "a.mp4").touch()
    (tmp_path / "b.mp4").touch()

    with patch("app.transcribe_video", side_effect=slow_transcribe), \
         patch("app._get_whisper_model", return_value=None):
        resp = client.post("/load-folder", json={"folder": str(tmp_path)})
        data = resp.get_json()
        job_id = data.get("job_id")

    assert job_id is not None

    # Give the thread a moment to start transcribing
    time.sleep(0.2)

    # Cancel
    client.delete(f"/jobs/{job_id}")

    # Should stop well before the 2s video finishes
    deadline = time.time() + 1.5
    while time.time() < deadline:
        status_resp = client.get(f"/status/{job_id}")
        if status_resp.get_json()["status"] in ("cancelled", "done", "error"):
            break
        time.sleep(0.1)

    final_status = client.get(f"/status/{job_id}").get_json()["status"]
    assert final_status == "cancelled"


def test_ensure_cloud_session_caches_and_downloads(tmp_path):
    """_ensure_cloud_session creates a temp dir, downloads files, and caches the result."""
    import app as app_module
    from unittest.mock import patch

    app_module._cloud_session_dirs.clear()
    app_module._cloud_session_ready.clear()

    downloaded = []

    def fake_download(key, local_path):
        downloaded.append((key, local_path))

    with patch("storage.is_cloud", return_value=True), \
         patch("storage.list_keys", return_value=["sessions/x/video.txt"]), \
         patch("storage.download_file", side_effect=fake_download), \
         patch("tempfile.mkdtemp", return_value=str(tmp_path)):
        result = app_module._ensure_cloud_session("sessions/x")

    assert result == str(tmp_path)
    assert len(downloaded) == 1
    assert downloaded[0][0] == "sessions/x/video.txt"

    # Second call must return cached path without re-downloading
    with patch("storage.list_keys") as mock_list, \
         patch("storage.download_file") as mock_dl:
        result2 = app_module._ensure_cloud_session("sessions/x")
    assert result2 == str(tmp_path)
    mock_list.assert_not_called()
    mock_dl.assert_not_called()

    app_module._cloud_session_dirs.clear()
    app_module._cloud_session_ready.clear()


def test_ensure_cloud_session_downloads_only_transcripts(tmp_path):
    """_ensure_cloud_session must NOT download video bytes — the main app only ever
    reads .txt sidecars, and pulling every video into Render's /tmp blows the 2GB
    ephemeral disk limit. Videos get 0-byte placeholders so scan_videos still lists
    them; only .txt files are actually downloaded."""
    import app as app_module
    from unittest.mock import patch

    app_module._cloud_session_dirs.clear()
    app_module._cloud_session_ready.clear()

    downloaded = []

    def fake_download(key, local_path):
        downloaded.append(key)
        Path(local_path).write_text("transcript body", encoding="utf-8")

    with patch("storage.is_cloud", return_value=True), \
         patch("storage.list_keys", return_value=[
             "sessions/y/clip.mp4",
             "sessions/y/clip.txt",
             "sessions/y/other.mov",
             "sessions/y/other.txt",
         ]), \
         patch("storage.download_file", side_effect=fake_download), \
         patch("tempfile.mkdtemp", return_value=str(tmp_path)):
        result = app_module._ensure_cloud_session("sessions/y")

    assert result == str(tmp_path)

    # Only the transcripts are downloaded — never the video bytes.
    assert set(downloaded) == {"sessions/y/clip.txt", "sessions/y/other.txt"}

    # Videos still exist locally (as 0-byte placeholders) so scan_videos lists them.
    assert (tmp_path / "clip.mp4").exists()
    assert (tmp_path / "other.mov").exists()
    assert (tmp_path / "clip.mp4").stat().st_size == 0
    assert (tmp_path / "other.mov").stat().st_size == 0

    # Transcripts have real content.
    assert (tmp_path / "clip.txt").read_text(encoding="utf-8") == "transcript body"

    app_module._cloud_session_dirs.clear()
    app_module._cloud_session_ready.clear()


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


def test_transcripts_excludes_generated_reels(client, tmp_path, monkeypatch):
    """GET /transcripts filters out library entries so generated reels don't appear
    in the sidebar."""
    from unittest.mock import patch

    source = tmp_path / "source.mp4"
    reel = tmp_path / "NOBU_sizzle.mp4"
    source.touch()
    reel.touch()
    (tmp_path / "source.txt").write_text("[0:05] Speaker: Hi.", encoding="utf-8")

    library = [{
        "id": "abc", "filename": "NOBU_sizzle.mp4", "path": str(reel),
        "source_folder": "tmp/", "prompt": "", "duration_seconds": 10,
        "clip_count": 1, "created_at": "2026-01-01T00:00:00",
    }]
    with patch("storage.load_library", return_value=library):
        resp = client.get(f"/transcripts?folder={tmp_path}")
    assert resp.status_code == 200
    data = resp.get_json()
    names = [f["name"] for f in data["files"]]
    assert "NOBU_sizzle.mp4" not in names
    assert "source.mp4" in names


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


def test_analyze_returns_segments_with_scores(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:05] Speaker: Hello world.\n[0:15] Speaker: Black cod is amazing.",
        encoding="utf-8",
    )
    with patch("app.query_claude", return_value="0:05-0:20|9"):
        resp = client.post("/analyze", json={"folder": str(tmp_path), "prompt": "food"})
    assert resp.status_code == 200
    data = resp.get_json()
    segs = data["segments"]["vid.mp4"]
    assert len(segs) == 1
    seg = segs[0]
    assert seg["score"] == 9
    assert seg["start"] == "0:05" and seg["end"] == "0:20"
    # duration_seconds is the clip the generator will actually cut, not Claude's
    # raw range. Plain tier, both lines selected, nothing after the last one, so
    # the run reaches the MAX_CLIP_SECONDS ceiling from 0:05.
    assert seg["duration_seconds"] == 40.0
    assert seg["start_seconds"] == 5.0 and seg["end_seconds"] == 45.0
    assert len(seg["lines"]) == 2  # both lines fall within 0:05-0:20


def test_analyze_estimate_matches_generator_when_candidates_merge(client, tmp_path):
    """Adjacent candidates merge into one run in the generator and hit the
    MAX_CLIP_SECONDS ceiling, so the summed estimate must not over-promise.

    Two Claude ranges cover six contiguous lines with no unselected line
    between them. Grouped separately they total more than the generator will
    cut, because the generator groups the union into a single run that
    MAX_CLIP_SECONDS truncates to 40s. The estimate has to report 40.
    """
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:00] Speaker: Black cod is amazing.\n"
        "[0:10] Speaker: Black cod is amazing.\n"
        "[0:20] Speaker: Black cod is amazing.\n"
        "[0:30] Speaker: Black cod is amazing.\n"
        "[0:40] Speaker: Black cod is amazing.\n"
        "[0:50] Speaker: Black cod is amazing.",
        encoding="utf-8",
    )
    with patch("app.query_claude", return_value="0:00-0:20|9\n0:30-0:50|9"):
        resp = client.post("/analyze", json={"folder": str(tmp_path), "prompt": "food"})
    assert resp.status_code == 200
    segs = resp.get_json()["segments"]["vid.mp4"]
    assert len(segs) == 2

    from shared import MAX_CLIP_SECONDS
    total = sum(s["duration_seconds"] for s in segs)
    assert total == pytest.approx(MAX_CLIP_SECONDS), (
        f"estimate {total}s over-promises; generator cuts {MAX_CLIP_SECONDS}s"
    )
    # Scaled proportionally from 30s and 40s; the split is uneven, so assert the
    # relationship rather than a per-segment constant.
    assert segs[0]["duration_seconds"] < segs[1]["duration_seconds"]
    for s in segs:
        assert s["end_seconds"] == pytest.approx(s["start_seconds"] + s["duration_seconds"])


def test_analyze_highlights_is_union_of_segment_lines(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:05] Speaker: Hello world.\n[0:15] Speaker: Black cod is amazing.",
        encoding="utf-8",
    )
    with patch("app.query_claude", return_value="0:05-0:20|9"):
        resp = client.post("/analyze", json={"folder": str(tmp_path), "prompt": "food"})
    data = resp.get_json()
    seg_lines = [l for s in data["segments"]["vid.mp4"] for l in s["lines"]]
    assert set(data["highlights"]["vid.mp4"]) == set(seg_lines)


def test_analyze_drops_interviewer_only_segment(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:05] Interviewer: What did you think of the cod?\n"
        "[0:15] Speaker: The cod was superb.",
        encoding="utf-8",
    )
    # First range is only the interviewer line -> dropped; second maps to respondent.
    with patch("app.query_claude", return_value="0:05-0:09|8\n0:15-0:20|9"):
        resp = client.post("/analyze", json={"folder": str(tmp_path), "prompt": "cod"})
    segs = resp.get_json()["segments"]["vid.mp4"]
    assert len(segs) == 1
    assert segs[0]["start"] == "0:15"


def test_parse_scored_timestamps_with_scores():
    from timestamp_parser import parse_scored_timestamps
    assert parse_scored_timestamps("0:05-0:20|9\n1:00-1:10|7") == [
        ("0:05-0:20", 9), ("1:00-1:10", 7)
    ]


def test_parse_scored_timestamps_missing_score_defaults_to_5():
    from timestamp_parser import parse_scored_timestamps
    assert parse_scored_timestamps("0:05-0:20") == [("0:05-0:20", 5)]


def test_parse_scored_timestamps_garbled_score_defaults_and_clamps():
    from timestamp_parser import parse_scored_timestamps
    # non-integer -> default 5; out-of-range -> clamp to 1..10
    assert parse_scored_timestamps("0:05-0:20|foo") == [("0:05-0:20", 5)]
    assert parse_scored_timestamps("0:05-0:20|99") == [("0:05-0:20", 10)]
    assert parse_scored_timestamps("0:05-0:20|0") == [("0:05-0:20", 1)]


def test_parse_scored_timestamps_none():
    from timestamp_parser import parse_scored_timestamps
    assert parse_scored_timestamps("none") is None


def test_parse_scored_timestamps_commas_and_whitespace():
    from timestamp_parser import parse_scored_timestamps
    assert parse_scored_timestamps("0:05-0:20|8, 1:00-1:10|6") == [
        ("0:05-0:20", 8), ("1:00-1:10", 6)
    ]


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


def test_analyze_runs_claude_calls_concurrently(client, tmp_path):
    """Per-video Claude calls must run in parallel, or a folder of many long
    videos serialises into a request long enough for the hosting proxy to time
    out (returning HTML that the frontend can't parse as JSON)."""
    import time

    n = 6
    for i in range(n):
        (tmp_path / f"vid{i}.mp4").touch()
        (tmp_path / f"vid{i}.txt").write_text(
            f"[0:05] Speaker: Segment {i}.", encoding="utf-8"
        )

    per_call = 0.3

    def slow_claude(transcript, prompt):
        time.sleep(per_call)
        return "0:05-0:10"

    with patch("app.query_claude", side_effect=slow_claude):
        start = time.perf_counter()
        resp = client.post("/analyze", json={"folder": str(tmp_path), "prompt": "food"})
        elapsed = time.perf_counter() - start

    assert resp.status_code == 200
    highlights = resp.get_json()["highlights"]
    # correctness preserved: every video mapped to its matched line
    assert len(highlights) == n
    assert all(len(v) == 1 for v in highlights.values())
    # concurrency: wall time must be far below the sequential sum (n * per_call)
    assert elapsed < (n * per_call) / 2, (
        f"analyze took {elapsed:.2f}s for {n} videos; expected concurrent execution"
    )


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


def test_recent_folder_updates_video_count_on_reopen(client, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "RECENT_FOLDERS_PATH", tmp_path / "recent.json")
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text("[0:05] Speaker: Hi.", encoding="utf-8")
    client.post("/load-folder", json={"folder": str(tmp_path)})
    # Add a second video before re-opening
    (tmp_path / "vid2.mp4").touch()
    (tmp_path / "vid2.txt").write_text("[0:10] Speaker: Bye.", encoding="utf-8")
    client.post("/load-folder", json={"folder": str(tmp_path)})
    recent = client.get("/recent-folders").get_json()
    assert len(recent) == 1
    assert recent[0]["video_count"] == 2


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


def test_run_analyze_excludes_interviewer_lines(tmp_path):
    from app import _run_analyze
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    txt = tmp_path / "v.txt"
    txt.write_text(
        "[0:10] Interviewer: Have you heard of Freshpet?\n"
        "[0:14] Participant: Yes I love Freshpet.\n",
        encoding="utf-8",
    )
    with patch("app.scan_videos", return_value=[video]), \
         patch("app._filter_generated_reels", side_effect=lambda paths: paths), \
         patch("app.query_claude", return_value="0:10-0:14"):
        result = _run_analyze(str(tmp_path), "Freshpet")

    matched = result["highlights"]["v.mp4"]
    assert "[0:14] Participant: Yes I love Freshpet." in matched
    assert all("Interviewer" not in line for line in matched)


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




def test_analyze_estimate_is_bounded_by_the_next_line(client, tmp_path):
    """A candidate's estimate must be bounded by the next unselected line.

    Grouping a candidate in isolation leaves no next line, so every estimate
    would run to the MAX_CLIP_SECONDS ceiling and the length slider would show
    every candidate as 40s.
    """
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:00] Speaker: First point here.\n"
        "[0:10] Speaker: Second point here.\n"
        "[0:20] Speaker: Third point here.\n"
        "[0:30] Speaker: Fourth point here.",
        encoding="utf-8",
    )
    with patch("app.query_claude", return_value="0:00-0:00|9"):
        resp = client.post("/analyze", json={"folder": str(tmp_path), "prompt": "x"})
    seg = resp.get_json()["segments"]["vid.mp4"][0]
    from shared import MAX_CLIP_SECONDS
    assert seg["duration_seconds"] == 10.0, (
        f"expected the clip to end at the next line (0:10); got "
        f"{seg['duration_seconds']}s"
    )
    assert seg["duration_seconds"] < MAX_CLIP_SECONDS


def test_analyze_uses_lines_in_range_not_inline_predicate(tmp_path):
    """_run_analyze must not use the old inline start-only predicate; it must
    call lines_in_range from shared so plain-tier results are identical to before."""
    import app as app_module
    # A plain-tier transcript: one line at 0:10
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text("[0:10] Participant: The food was amazing.", encoding="utf-8")

    with (
        patch("app.scan_videos", return_value=[tmp_path / "vid.mp4"]),
        patch("app.query_claude", return_value="0:10-0:15|8"),
        patch("app._filter_generated_reels", side_effect=lambda v: v),
        patch("storage.load_library", return_value=[]),
    ):
        result = app_module._run_analyze(str(tmp_path), "food quality")

    assert "vid.mp4" in result.get("segments", {})
    segs = result["segments"]["vid.mp4"]
    assert len(segs) == 1
    assert "[0:10] Participant: The food was amazing." in segs[0]["lines"]
