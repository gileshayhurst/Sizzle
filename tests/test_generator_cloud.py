"""Tests for generator_app.py cloud mode: S3 download/upload flow."""
import os
import io
from unittest.mock import patch, MagicMock, call
from pathlib import Path
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


def test_load_library_uses_storage_in_cloud_mode(monkeypatch, tmp_path):
    """_load_library in generator_app reads from storage.read_json in cloud mode."""
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY", "key")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    import importlib, storage, generator_app
    importlib.reload(storage)
    importlib.reload(generator_app)

    fake_entries = [{"id": "1", "filename": "reel.mp4"}]
    with patch("generator_app.storage.read_json", return_value=fake_entries) as mock_rj:
        result = generator_app._load_library()
    mock_rj.assert_called_once_with(storage.library_key())
    assert result == fake_entries


def test_save_library_uses_storage_in_cloud_mode(monkeypatch):
    """_save_library in generator_app writes via storage.write_json in cloud mode."""
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY", "key")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    import importlib, storage, generator_app
    importlib.reload(storage)
    importlib.reload(generator_app)

    data = [{"id": "2", "filename": "reel2.mp4"}]
    with patch("generator_app.storage.write_json") as mock_wj:
        generator_app._save_library(data)
    mock_wj.assert_called_once_with(storage.library_key(), data)


def test_generate_endpoint_accepts_session_key_in_cloud_mode(cloud_client, tmp_path):
    """POST /generate in cloud mode accepts session_key and downloads files from S3."""
    session_key = "sessions/test123"
    mp4_bytes = b"fake mp4"
    txt_content = "[0:00] Speaker: Hello world."

    def fake_list_keys(prefix):
        return [f"{session_key}/video.mp4", f"{session_key}/video.txt"]

    def fake_download(key, local_path):
        if key.endswith(".mp4"):
            Path(local_path).write_bytes(mp4_bytes)
        else:
            Path(local_path).write_text(txt_content, encoding="utf-8")

    selections = {"video.mp4": ["[0:00] Speaker: Hello world."]}

    with patch("generator_app.storage.list_keys", side_effect=fake_list_keys), \
         patch("generator_app.storage.download_file", side_effect=fake_download), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.make_title_card"), \
         patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.storage.upload_file"), \
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
