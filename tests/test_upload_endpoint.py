"""Tests for POST /upload and config injection in app.py."""
import pytest
from unittest.mock import patch


@pytest.fixture
def client():
    from app import create_app
    app = create_app(testing=True)
    with app.test_client() as c:
        yield c


def test_index_injects_generator_url(client, monkeypatch):
    """GET / should include window.__CONFIG__ with the configured generator URL."""
    monkeypatch.setenv("GENERATOR_URL", "https://my-generator.onrender.com")
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "window.__CONFIG__" in html
    assert "https://my-generator.onrender.com" in html


def test_index_injects_default_generator_url_when_env_absent(client, monkeypatch):
    """When GENERATOR_URL is not set, the default localhost:5001 is injected."""
    monkeypatch.delenv("GENERATOR_URL", raising=False)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "localhost:5001" in html


def test_index_injects_app_mode(client, monkeypatch):
    """GET / should inject the APP_MODE into window.__CONFIG__."""
    monkeypatch.setenv("APP_MODE", "cloud")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "cloud" in resp.data.decode()


import io
import os
from unittest.mock import patch, MagicMock


def test_upload_returns_session_info_local_mode(tmp_path, monkeypatch):
    """POST /upload in local mode stores files and returns session metadata."""
    monkeypatch.setenv("APP_MODE", "local")
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    import importlib, storage, app as app_mod
    importlib.reload(storage)
    importlib.reload(app_mod)

    flask_app = app_mod.create_app(testing=True)
    with flask_app.test_client() as c:
        data = {
            "files": (io.BytesIO(b"fake mp4"), "video1.mp4"),
        }
        resp = c.post("/upload", data=data, content_type="multipart/form-data")

    assert resp.status_code == 200
    body = resp.get_json()
    assert "session_key" in body
    assert body["session_key"].startswith("sessions/")
    # File should exist under DATA_ROOT
    session_dir = tmp_path / body["session_key"]
    assert (session_dir / "video1.mp4").exists()


def test_upload_rejects_non_video_files(tmp_path, monkeypatch):
    """POST /upload returns 400 if a non-video file is included."""
    monkeypatch.setenv("APP_MODE", "local")
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    import importlib, storage, app as app_mod
    importlib.reload(storage)
    importlib.reload(app_mod)

    flask_app = app_mod.create_app(testing=True)
    with flask_app.test_client() as c:
        data = {
            "files": (io.BytesIO(b"not a video"), "document.pdf"),
        }
        resp = c.post("/upload", data=data, content_type="multipart/form-data")

    assert resp.status_code == 400


def test_upload_requires_at_least_one_file(tmp_path, monkeypatch):
    """POST /upload with no files returns 400."""
    monkeypatch.setenv("APP_MODE", "local")
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    import importlib, storage, app as app_mod
    importlib.reload(storage)
    importlib.reload(app_mod)

    flask_app = app_mod.create_app(testing=True)
    with flask_app.test_client() as c:
        resp = c.post("/upload", data={}, content_type="multipart/form-data")

    assert resp.status_code == 400


def test_upload_cloud_mode_calls_storage_upload(tmp_path, monkeypatch):
    """In cloud mode, POST /upload saves to S3 via storage.upload_file."""
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY", "key")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    import importlib, storage, app as app_mod
    importlib.reload(storage)
    importlib.reload(app_mod)

    flask_app = app_mod.create_app(testing=True)
    uploaded_keys = []

    def fake_upload(local_path, key):
        uploaded_keys.append(key)

    with flask_app.test_client() as c, \
         patch("app.storage.upload_file", side_effect=fake_upload):
        data = {"files": (io.BytesIO(b"fake mp4"), "video1.mp4")}
        resp = c.post("/upload", data=data, content_type="multipart/form-data")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["session_key"].startswith("sessions/")
    # upload_file must have been called with the correct S3 key
    assert any("video1.mp4" in k for k in uploaded_keys)


# ── /upload/prepare ──────────────────────────────────────────────────────────

def test_upload_prepare_returns_presigned_urls(client, monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    with patch("storage.new_session_key", return_value="sessions/testsession"), \
         patch("storage.presigned_put_url", side_effect=lambda key, expires=3600: f"https://r2.example.com/{key}"):
        resp = client.post("/upload/prepare", json={
            "files": ["clip1.mp4", "clip1.txt", "clip2.mov"]
        })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["session_key"] == "sessions/testsession"
    assert data["folder"] == "sessions/testsession"
    assert len(data["uploads"]) == 3
    assert data["uploads"][0]["filename"] == "clip1.mp4"
    assert "url" in data["uploads"][0]
    assert "key" in data["uploads"][0]


def test_upload_prepare_rejects_unsupported_extension(client, monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    resp = client.post("/upload/prepare", json={"files": ["video.mp4", "readme.pdf"]})
    assert resp.status_code == 400
    assert "Unsupported" in resp.get_json()["error"]


def test_upload_prepare_requires_at_least_one_video(client, monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    resp = client.post("/upload/prepare", json={"files": ["transcript.txt"]})
    assert resp.status_code == 400
    assert "video" in resp.get_json()["error"].lower()


def test_upload_prepare_requires_files_list(client, monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    resp = client.post("/upload/prepare", json={})
    assert resp.status_code == 400


# ── /upload/commit ───────────────────────────────────────────────────────────

def test_upload_commit_returns_folder(client, monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    resp = client.post("/upload/commit", json={
        "session_key": "sessions/testsession",
        "files": ["clip1.mp4", "clip1.txt"]
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["folder"] == "sessions/testsession"
    assert data["session_key"] == "sessions/testsession"


def test_upload_commit_rejects_missing_session_key(client, monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    resp = client.post("/upload/commit", json={"files": ["clip1.mp4"]})
    assert resp.status_code == 400
