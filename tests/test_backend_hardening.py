"""Login-free backend hardening: rate limiting, request-size cap, CORS
restriction, and the sessions/ path-traversal guard. No auth is involved —
these protect the cloud services without changing the site's flow."""
import importlib

import pytest


def _reload_cloud(monkeypatch, **extra):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("S3_BUCKET", "b")
    monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)
    import storage
    importlib.reload(storage)
    return storage


@pytest.fixture
def app_cloud(monkeypatch):
    _reload_cloud(monkeypatch)
    import app as app_mod
    importlib.reload(app_mod)
    application = app_mod.create_app(testing=True)
    with application.test_client() as c:
        yield c


@pytest.fixture
def generator_cloud(monkeypatch):
    _reload_cloud(monkeypatch, ALLOWED_ORIGINS="https://sizzle-app-q1p9.onrender.com")
    import generator_app
    importlib.reload(generator_app)
    application = generator_app.create_app(testing=True)
    with application.test_client() as c:
        yield c


# ── Rate limiting ──────────────────────────────────────────────────────────

def test_analyze_is_rate_limited(app_cloud, monkeypatch):
    # 10/min limit — the 11th call in the window returns 429. Stub the session
    # download so the allowed calls return cleanly (404) instead of hitting S3.
    import app as app_mod
    monkeypatch.setattr(app_mod, "_ensure_cloud_session", lambda *a, **k: "/nonexistent")
    codes = [app_cloud.post("/analyze", json={"folder": "sessions/x", "prompt": "hi"}).status_code
             for _ in range(13)]
    assert 429 in codes


# ── Request-size cap ───────────────────────────────────────────────────────

def test_oversize_body_rejected(app_cloud):
    big = b"x" * (60 * 1024 * 1024)  # 60 MB > 50 MB cap
    r = app_cloud.post("/upload/prepare", data=big, content_type="application/json")
    assert r.status_code == 413


# ── Path-traversal guard (audit finding #5, login-free) ────────────────────

def test_analyze_rejects_real_server_path(app_cloud):
    assert app_cloud.post("/analyze", json={"folder": "/etc", "prompt": "hi"}).status_code == 403


def test_analyze_rejects_non_session_folder(app_cloud):
    assert app_cloud.post("/analyze", json={"folder": "../secrets", "prompt": "hi"}).status_code == 403


def test_analyze_allows_session_folder(app_cloud, monkeypatch):
    # A well-formed session key passes the guard; stub the download so we don't
    # hit real S3. Anything but 403 proves the guard let it through (here 404).
    import app as app_mod
    monkeypatch.setattr(app_mod, "_ensure_cloud_session", lambda *a, **k: "/nonexistent")
    r = app_cloud.post("/analyze", json={"folder": "sessions/abc", "prompt": "hi"})
    assert r.status_code != 403


# ── CORS restriction ───────────────────────────────────────────────────────

def test_allowed_origin_echoed(generator_cloud):
    r = generator_cloud.get("/library",
                            headers={"Origin": "https://sizzle-app-q1p9.onrender.com"})
    assert r.headers.get("Access-Control-Allow-Origin") == \
        "https://sizzle-app-q1p9.onrender.com"


def test_foreign_origin_not_allowed(generator_cloud):
    r = generator_cloud.get("/library", headers={"Origin": "https://evil.example.com"})
    assert r.headers.get("Access-Control-Allow-Origin") != "https://evil.example.com"
