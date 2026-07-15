"""Tests for auth.py — stateless signed Bearer tokens."""
import time
import pytest


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("SIZZLE_SECRET_KEY", "test-secret-key-do-not-use-in-prod")
    import importlib, auth
    importlib.reload(auth)
    return auth


def test_token_roundtrip(_secret):
    token = _secret.make_token("clientA")
    assert _secret.verify_token(token) == "clientA"


def test_tampered_token_rejected(_secret):
    token = _secret.make_token("clientA")
    assert _secret.verify_token(token + "x") is None


def test_expired_token_rejected(_secret, monkeypatch):
    token = _secret.make_token("clientA")
    monkeypatch.setattr(_secret, "TOKEN_MAX_AGE_SECONDS", 0)
    time.sleep(1)
    assert _secret.verify_token(token) is None


def test_verify_none_and_garbage(_secret):
    assert _secret.verify_token("") is None
    assert _secret.verify_token("not.a.token") is None


from flask import Flask, g


def _guarded_app(auth):
    app = Flask(__name__)
    app.before_request(auth.require_auth)

    @app.get("/login")
    def login():
        return "login-ok"

    @app.get("/secret")
    def secret():
        return getattr(g, "user_id", "local")

    return app


def test_guard_allows_local_mode(monkeypatch):
    monkeypatch.delenv("APP_MODE", raising=False)
    import importlib, storage, auth
    importlib.reload(storage); importlib.reload(auth)
    app = _guarded_app(auth)
    with app.test_client() as c:
        assert c.get("/secret").status_code == 200


def test_guard_blocks_missing_token_in_cloud(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("SIZZLE_SECRET_KEY", "k")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage, auth
    importlib.reload(storage); importlib.reload(auth)
    app = _guarded_app(auth)
    with app.test_client() as c:
        assert c.get("/secret").status_code == 401
        assert c.get("/login").status_code == 200


def test_guard_accepts_valid_token_in_cloud(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("SIZZLE_SECRET_KEY", "k")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage, auth
    importlib.reload(storage); importlib.reload(auth)
    app = _guarded_app(auth)
    token = auth.make_token("clientA")
    with app.test_client() as c:
        r = c.get("/secret", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.data.decode() == "clientA"
