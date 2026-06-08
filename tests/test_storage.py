"""Tests for storage.py — exercises the local backend only (no real S3)."""
import importlib
import json
import os
import pytest
from pathlib import Path


# ── helpers ────────────────────────────────────────────────────────────────────

def reload_storage(monkeypatch, tmp_path, mode="local"):
    """Reload storage module with fresh env so module-level checks re-run."""
    monkeypatch.setenv("APP_MODE", mode)
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    import storage
    importlib.reload(storage)
    return storage


# ── is_cloud / data_root ───────────────────────────────────────────────────────

def test_is_cloud_false_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_MODE", raising=False)
    s = reload_storage(monkeypatch, tmp_path)
    assert s.is_cloud() is False


def test_is_cloud_true_when_env_set(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path, mode="cloud")
    assert s.is_cloud() is True


# ── new_session_key ────────────────────────────────────────────────────────────

def test_new_session_key_format(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    key = s.new_session_key()
    assert key.startswith("sessions/")
    assert len(key) > len("sessions/") + 8  # has a uuid hex


def test_new_session_key_unique(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    assert s.new_session_key() != s.new_session_key()


# ── library_key ────────────────────────────────────────────────────────────────

def test_library_key(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    assert s.library_key() == "library/sizzle_library.json"


# ── upload_file / download_file (local backend) ───────────────────────────────

def test_upload_creates_file_under_data_root(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    src = tmp_path / "video.mp4"
    src.write_bytes(b"fake video data")

    s.upload_file(str(src), "sessions/abc/video.mp4")

    dest = tmp_path / "sessions" / "abc" / "video.mp4"
    assert dest.exists()
    assert dest.read_bytes() == b"fake video data"


def test_download_retrieves_file(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    # Pre-plant a file in the data root
    (tmp_path / "sessions" / "abc").mkdir(parents=True)
    (tmp_path / "sessions" / "abc" / "clip.mp4").write_bytes(b"clip bytes")

    out = tmp_path / "downloaded.mp4"
    s.download_file("sessions/abc/clip.mp4", str(out))
    assert out.read_bytes() == b"clip bytes"


# ── read_json / write_json (local backend) ────────────────────────────────────

def test_write_then_read_json(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    data = [{"id": "1", "name": "test reel"}]
    s.write_json("library/sizzle_library.json", data)
    assert s.read_json("library/sizzle_library.json") == data


def test_read_json_missing_returns_empty_list(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    assert s.read_json("nonexistent/file.json") == []


def test_read_json_corrupt_returns_empty_list(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    bad_file = tmp_path / "library" / "sizzle_library.json"
    bad_file.parent.mkdir(parents=True)
    bad_file.write_text("not json", encoding="utf-8")
    assert s.read_json("library/sizzle_library.json") == []


# ── list_keys (local backend) ─────────────────────────────────────────────────

def test_list_keys_returns_files_in_prefix(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    prefix_dir = tmp_path / "sessions" / "abc"
    prefix_dir.mkdir(parents=True)
    (prefix_dir / "video.mp4").write_bytes(b"")
    (prefix_dir / "video.txt").write_text("transcript", encoding="utf-8")

    keys = s.list_keys("sessions/abc")
    assert "sessions/abc/video.mp4" in keys
    assert "sessions/abc/video.txt" in keys


def test_list_keys_empty_prefix_returns_empty(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    assert s.list_keys("sessions/nonexistent") == []


def test_list_keys_finds_nested_files(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    nested = tmp_path / "sessions" / "abc" / "clips"
    nested.mkdir(parents=True)
    (nested / "clip_0001.mp4").write_bytes(b"clip")

    keys = s.list_keys("sessions/abc")
    assert "sessions/abc/clips/clip_0001.mp4" in keys


# ── presigned_url (local backend) ──────────────────────────────────────────────

def test_presigned_url_raises_in_local_mode(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="cloud mode"):
        s.presigned_url("sessions/abc/video.mp4")


# ── presigned_put_url (cloud backend) ──────────────────────────────────────────

def test_presigned_put_url_raises_in_local_mode(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="only available in cloud mode"):
        s.presigned_put_url("sessions/abc/video.mp4")


def test_presigned_put_url_calls_s3_in_cloud_mode(monkeypatch, tmp_path):
    from unittest.mock import MagicMock, patch
    s = reload_storage(monkeypatch, tmp_path, mode="cloud")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY", "key")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    mock_client = MagicMock()
    mock_client.generate_presigned_url.return_value = "https://r2.example.com/put-url"
    with patch("storage._s3", return_value=mock_client):
        url = s.presigned_put_url("sessions/abc/video.mp4", expires=300)
    mock_client.generate_presigned_url.assert_called_once_with(
        "put_object",
        Params={"Bucket": "test-bucket", "Key": "sessions/abc/video.mp4"},
        ExpiresIn=300,
    )
    assert url == "https://r2.example.com/put-url"
