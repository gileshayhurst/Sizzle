import json
import pytest
from app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "PROMPT_HISTORY_PATH", tmp_path / "prompt_history.json")
    flask_app = create_app(testing=True)
    with flask_app.test_client() as c:
        yield c


def test_get_prompt_history_empty(client):
    resp = client.get("/prompt-history")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data == {"recent": [], "templates": []}


def test_post_use_adds_to_recent(client):
    client.post("/prompt-history", json={"action": "use", "text": "best reactions"})
    resp = client.get("/prompt-history")
    data = resp.get_json()
    assert data["recent"] == ["best reactions"]


def test_post_use_deduplicates_and_moves_to_front(client):
    client.post("/prompt-history", json={"action": "use", "text": "first"})
    client.post("/prompt-history", json={"action": "use", "text": "second"})
    client.post("/prompt-history", json={"action": "use", "text": "first"})
    resp = client.get("/prompt-history")
    data = resp.get_json()
    assert data["recent"] == ["first", "second"]


def test_post_use_caps_at_ten(client):
    for i in range(12):
        client.post("/prompt-history", json={"action": "use", "text": f"prompt {i}"})
    resp = client.get("/prompt-history")
    data = resp.get_json()
    assert len(data["recent"]) == 10
    assert data["recent"][0] == "prompt 11"


def test_save_and_delete_template(client):
    client.post("/prompt-history", json={"action": "save_template", "name": "Reactions", "text": "best reactions"})
    resp = client.get("/prompt-history")
    data = resp.get_json()
    assert data["templates"] == [{"name": "Reactions", "text": "best reactions"}]

    client.post("/prompt-history", json={"action": "delete_template", "name": "Reactions"})
    resp = client.get("/prompt-history")
    data = resp.get_json()
    assert data["templates"] == []


def test_save_template_updates_existing_name(client):
    client.post("/prompt-history", json={"action": "save_template", "name": "Reactions", "text": "v1"})
    client.post("/prompt-history", json={"action": "save_template", "name": "Reactions", "text": "v2"})
    resp = client.get("/prompt-history")
    data = resp.get_json()
    assert len(data["templates"]) == 1
    assert data["templates"][0]["text"] == "v2"
