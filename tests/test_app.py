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


