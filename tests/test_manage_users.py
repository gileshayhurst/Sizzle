"""Tests for the operator user-provisioning helpers."""
import pytest


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage, manage_users
    importlib.reload(storage); importlib.reload(manage_users)
    return manage_users


def test_add_and_verify(cloud, monkeypatch):
    store = {}
    monkeypatch.setattr(cloud.storage, "read_json", lambda k: store.get(k, {}))
    monkeypatch.setattr(cloud.storage, "write_json",
                        lambda k, d: store.__setitem__(k, d))
    cloud.add_user("clientA", "s3cret")
    assert cloud.verify_user("clientA", "s3cret") is True
    assert cloud.verify_user("clientA", "wrong") is False
    assert cloud.verify_user("ghost", "x") is False


def test_hash_is_not_plaintext(cloud, monkeypatch):
    store = {}
    monkeypatch.setattr(cloud.storage, "read_json", lambda k: store.get(k, {}))
    monkeypatch.setattr(cloud.storage, "write_json",
                        lambda k, d: store.__setitem__(k, d))
    cloud.add_user("clientA", "s3cret")
    assert "s3cret" not in str(store)
