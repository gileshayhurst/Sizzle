import pytest


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("SIZZLE_SECRET_KEY", "k")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage, auth, generator_app
    importlib.reload(storage); importlib.reload(auth); importlib.reload(generator_app)
    return generator_app, auth


def _client(generator_app, auth, user):
    app = generator_app.create_app(testing=True)
    c = app.test_client()
    c.environ_base["HTTP_AUTHORIZATION"] = "Bearer " + auth.make_token(user)
    return c


def test_library_is_per_user(cloud, monkeypatch):
    generator_app, auth = cloud
    stores = {"clientA": [{"id": "a1", "filename": "a.mp4"}],
              "clientB": [{"id": "b1", "filename": "b.mp4"}]}
    monkeypatch.setattr(generator_app, "_load_library",
                        lambda user_id=None: stores.get(user_id, []))
    a = _client(generator_app, auth, "clientA")
    ids = [e["id"] for e in a.get("/library").get_json()]
    assert ids == ["a1"]


def test_cross_tenant_delete_404(cloud, monkeypatch):
    generator_app, auth = cloud
    monkeypatch.setattr(generator_app, "_load_library",
                        lambda user_id=None: [] if user_id == "clientB" else [{"id": "a1"}])
    saved = {}
    monkeypatch.setattr(generator_app, "_save_library",
                        lambda entries, user_id=None: saved.__setitem__(user_id, entries))
    b = _client(generator_app, auth, "clientB")
    assert b.delete("/library/a1").status_code == 404
