"""In cloud mode a user may only reference session keys under their own prefix.
Passing an arbitrary/real server path or another user's key must 403 (regression
for audit finding #5)."""
import pytest


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("SIZZLE_SECRET_KEY", "k")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage, auth, app as app_mod
    importlib.reload(storage); importlib.reload(auth); importlib.reload(app_mod)
    application = app_mod.create_app(testing=True)
    c = application.test_client()
    c.environ_base["HTTP_AUTHORIZATION"] = "Bearer " + auth.make_token("clientA")
    return c


def test_analyze_rejects_foreign_prefix(cloud):
    r = cloud.post("/analyze", json={"folder": "users/clientB/sessions/x",
                                     "prompt": "hi"})
    assert r.status_code == 403


def test_analyze_rejects_real_server_path(cloud):
    r = cloud.post("/analyze", json={"folder": "/etc", "prompt": "hi"})
    assert r.status_code == 403


def test_analyze_allows_own_prefix(cloud, monkeypatch):
    # Own-prefix folder passes the guard; stub the session download so we don't
    # hit real S3, and assert the response is anything but 403 (here, 404).
    import app as app_mod
    monkeypatch.setattr(app_mod, "_ensure_cloud_session",
                        lambda *a, **k: "/nonexistent_session_dir")
    r = cloud.post("/analyze", json={"folder": "users/clientA/sessions/x",
                                     "prompt": "hi"})
    assert r.status_code != 403
    assert r.status_code == 404
