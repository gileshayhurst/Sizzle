import pytest


@pytest.fixture
def cloud_client(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("SIZZLE_SECRET_KEY", "k")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage, auth, generator_app
    importlib.reload(storage); importlib.reload(auth); importlib.reload(generator_app)
    app = generator_app.create_app(testing=True)
    with app.test_client() as c:
        yield c, auth


def test_library_requires_token(cloud_client):
    c, _ = cloud_client
    assert c.get("/library").status_code == 401


def test_library_ok_with_token(cloud_client, monkeypatch):
    c, auth = cloud_client
    import generator_app
    monkeypatch.setattr(generator_app, "_load_library", lambda user_id=None: [])
    token = auth.make_token("clientA")
    r = c.get("/library", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
