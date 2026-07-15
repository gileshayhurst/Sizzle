import pytest


@pytest.fixture
def cloud_client(monkeypatch):
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


def test_oversize_body_rejected(cloud_client):
    # Body larger than MAX_CONTENT_LENGTH -> 413 before the handler runs.
    big = b"x" * (60 * 1024 * 1024)   # 60 MB
    r = cloud_client.post("/upload/prepare", data=big,
                          content_type="application/json")
    assert r.status_code == 413
