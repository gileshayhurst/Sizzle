import pytest


def _cloud(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage
    importlib.reload(storage)
    return storage


def test_session_key_scoped_by_user(monkeypatch):
    s = _cloud(monkeypatch)
    key = s.new_session_key("clientA")
    assert key.startswith("users/clientA/sessions/")


def test_session_key_unscoped_without_user(monkeypatch):
    s = _cloud(monkeypatch)
    assert s.new_session_key().startswith("sessions/")


def test_library_key_scoped_by_user(monkeypatch):
    s = _cloud(monkeypatch)
    assert s.library_key("clientA") == "users/clientA/library.json"


def test_library_key_legacy_without_user(monkeypatch):
    s = _cloud(monkeypatch)
    assert s.library_key() == "library/sizzle_library.json"
