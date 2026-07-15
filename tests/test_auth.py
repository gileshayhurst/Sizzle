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
