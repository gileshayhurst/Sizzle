import pytest


@pytest.fixture
def cloud_client(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("SIZZLE_SECRET_KEY", "k")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://sizzle-app-q1p9.onrender.com")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage, auth, generator_app
    importlib.reload(storage); importlib.reload(auth); importlib.reload(generator_app)
    app = generator_app.create_app(testing=True)
    with app.test_client() as c:
        yield c


def test_allowed_origin_echoed(cloud_client):
    r = cloud_client.get("/library",
                         headers={"Origin": "https://sizzle-app-q1p9.onrender.com"})
    assert r.headers.get("Access-Control-Allow-Origin") == \
        "https://sizzle-app-q1p9.onrender.com"


def test_foreign_origin_not_allowed(cloud_client):
    r = cloud_client.get("/library", headers={"Origin": "https://evil.example.com"})
    assert r.headers.get("Access-Control-Allow-Origin") != "https://evil.example.com"
