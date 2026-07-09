"""Tests for POST /plan and POST /library browser-pipeline endpoints."""
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture
def cloud_client(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY", "key")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:9000")
    import importlib, storage, generator_app
    importlib.reload(storage)
    importlib.reload(generator_app)
    app = generator_app.create_app(testing=True)
    with app.test_client() as c:
        yield c
    # Restore module state for other test files
    monkeypatch.delenv("APP_MODE", raising=False)
    importlib.reload(storage)
    importlib.reload(generator_app)


@pytest.fixture
def local_client():
    import importlib, generator_app
    # Ensure APP_MODE is not set to cloud
    os.environ.pop("APP_MODE", None)
    importlib.reload(generator_app)
    app = generator_app.create_app(testing=True)
    with app.test_client() as c:
        yield c


# ─── POST /plan ───────────────────────────────────────────────────────────────

def test_plan_returns_400_in_local_mode(local_client):
    resp = local_client.post("/plan", json={"session_key": "sessions/abc"})
    assert resp.status_code == 400
    assert "cloud" in resp.get_json()["error"].lower()


def test_plan_returns_400_without_session_key(cloud_client):
    resp = cloud_client.post("/plan", json={"selections": {}})
    assert resp.status_code == 400


def test_plan_returns_422_when_no_segments_found(cloud_client, tmp_path):
    session_key = "sessions/test123"
    # list_keys returns nothing
    with patch("generator_app.storage.list_keys", return_value=[]):
        resp = cloud_client.post("/plan", json={
            "session_key": session_key,
            "selections": {"video.webm": ["[0:10] Speaker: Hi."]},
        })
    assert resp.status_code == 422


def test_plan_returns_segment_list_with_correct_shape(cloud_client, tmp_path):
    import importlib, generator_app
    session_key = "sessions/test456"
    transcript = "[0:10] Speaker: This is great content.\n[0:25] Speaker: End."
    raw_line = "[0:10] Speaker: This is great content."

    # Write transcript to a temp file so _build_segment_list can read it
    txt_path = tmp_path / "interview.txt"
    txt_path.write_text(transcript, encoding="utf-8")

    def fake_download(key, local_path):
        if key.endswith(".txt"):
            Path(local_path).write_text(transcript, encoding="utf-8")

    presigned_get = "https://r2.example.com/interview.webm?sig=xyz"

    with patch("generator_app.storage.list_keys",
               return_value=[f"{session_key}/interview.webm", f"{session_key}/interview.txt"]), \
         patch("generator_app.storage.download_file", side_effect=fake_download), \
         patch("generator_app.storage.presigned_url", return_value=presigned_get), \
         patch("generator_app.storage.presigned_put_url", return_value="https://r2.example.com/put"), \
         patch("generator_app.get_video_duration", return_value=60.0), \
         patch("generator_app.get_video_dimensions", return_value=(1280, 720)):

        resp = cloud_client.post("/plan", json={
            "session_key": session_key,
            "selections": {"interview.webm": [raw_line]},
            "output_filename": "reel.mp4",
        })

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["session_key"] == session_key
    assert data["output_filename"] == "reel.mp4"
    assert data["width"] == 1280
    assert data["height"] == 720
    assert "presigned_put_url" in data
    assert "reel_key" in data
    assert len(data["segments"]) == 1
    seg = data["segments"][0]
    assert seg["video"] == "interview.webm"
    assert seg["presigned_get_url"] == presigned_get
    assert seg["start_sec"] == 10.0
    assert seg["title_lines"][0] == "interview"
    assert seg["title_lines"][1] == "from 0:10"
    assert seg["title_lines"][2] == "Segment 1 / 1"


# ─── POST /library ────────────────────────────────────────────────────────────

def test_library_post_returns_400_without_required_fields(cloud_client):
    resp = cloud_client.post("/library", json={})
    assert resp.status_code == 400

    resp = cloud_client.post("/library", json={"session_key": "sessions/abc"})
    assert resp.status_code == 400  # missing output_filename


def test_library_post_creates_entry_with_correct_reel_s3_key(cloud_client):
    with patch("generator_app._library_add") as mock_add:
        resp = cloud_client.post("/library", json={
            "session_key": "sessions/abc123",
            "output_filename": "my_reel.mp4",
            "prompt": "exciting moments",
            "duration_seconds": 95,
            "clip_count": 4,
            "segment_starts": [0, 10.5, 30.2, 55.0],
        })
    assert resp.status_code == 200
    data = resp.get_json()
    assert "id" in data

    mock_add.assert_called_once()
    entry = mock_add.call_args[0][0]
    assert entry["reel_s3_key"] == "sessions/abc123/my_reel.mp4"
    assert entry["filename"] == "my_reel.mp4"
    assert entry["prompt"] == "exciting moments"
    assert entry["duration_seconds"] == 95
    assert entry["clip_count"] == 4
    assert entry["segment_starts"] == [0, 10.5, 30.2, 55.0]
    assert entry["source_folder"] == "abc123/"
    assert "created_at" in entry
    assert "id" in entry


def test_library_post_returns_same_id_as_entry(cloud_client):
    with patch("generator_app._library_add"):
        resp = cloud_client.post("/library", json={
            "session_key": "sessions/xyz",
            "output_filename": "reel.mp4",
        })
    assert resp.status_code == 200
    # The returned id should be a UUID string
    import re
    assert re.match(r"[0-9a-f-]{36}", resp.get_json()["id"])
