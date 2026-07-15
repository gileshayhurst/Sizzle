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
    with application.test_client() as c:
        yield c


def test_login_is_rate_limited(cloud_client):
    from unittest.mock import patch
    with patch("app.manage_users.verify_user", return_value=False):
        codes = [cloud_client.post("/login",
                 json={"user_id": "x", "password": "y"}).status_code
                 for _ in range(12)]
    assert 429 in codes    # the 6th+ attempt within the window is throttled
