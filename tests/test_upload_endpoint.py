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
