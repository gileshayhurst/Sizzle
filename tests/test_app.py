import os
import threading
from unittest.mock import patch, MagicMock
from pathlib import Path

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


