import pytest
from unittest.mock import patch


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


def test_login_success_returns_token(cloud_client):
    with patch("app.manage_users.verify_user", return_value=True):
        r = cloud_client.post("/login", json={"user_id": "clientA", "password": "pw"})
    assert r.status_code == 200
    assert r.get_json()["token"]


def test_login_bad_password_401(cloud_client):
    with patch("app.manage_users.verify_user", return_value=False):
        r = cloud_client.post("/login", json={"user_id": "clientA", "password": "x"})
    assert r.status_code == 401


def test_protected_route_requires_token(cloud_client):
    assert cloud_client.get("/recent-folders").status_code == 401
