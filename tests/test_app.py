import os
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
    with _jobs_lock:
        assert _jobs[job_id]["status"] == "cancelled"


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
