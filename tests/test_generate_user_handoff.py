"""The generation worker runs on a thread with no request context, so /generate
must capture g.user_id and pass it explicitly; the reel must land in that user's
library."""
import pytest
from unittest.mock import patch


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("SIZZLE_SECRET_KEY", "k")
    monkeypatch.setenv("S3_BUCKET", "b"); monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    import importlib, storage, auth, generator_app
    importlib.reload(storage); importlib.reload(auth); importlib.reload(generator_app)
    return generator_app, auth


def test_generate_passes_user_id_to_worker(cloud, monkeypatch):
    generator_app, auth = cloud
    captured = {}

    def fake_run(job_id, folder, selections, prompt, output_filename, **kw):
        captured["user_id"] = kw.get("user_id")

    monkeypatch.setattr(generator_app, "_run_generation", fake_run)
    monkeypatch.setattr(generator_app.storage, "list_keys", lambda p: [])
    monkeypatch.setattr(generator_app, "check_ffmpeg", lambda: None)
    app = generator_app.create_app(testing=True)
    c = app.test_client()
    c.environ_base["HTTP_AUTHORIZATION"] = "Bearer " + auth.make_token("clientA")
    c.post("/generate", json={"session_key": "users/clientA/sessions/x",
                              "selections": {}, "prompt": "p"})
    assert captured["user_id"] == "clientA"
