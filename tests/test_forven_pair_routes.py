"""Tests for setting up organisation pairs from the browser.

A fresh deployment starts with no pairs at all. If the only way to create the
first one is a psql prompt, the deployment is not usable by the people it is
for, so these routes are the bootstrap path.
"""

import pytest

import forven_api
import sz_store
from app import create_app


class _FakeClient:
    def __init__(self, base_url, api_key):
        pass


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FORVEN_STAGING_API_KEY", "fvk_stg")
    monkeypatch.setenv("FORVEN_PROD_API_KEY", "fvk_prod")
    monkeypatch.setattr(forven_api, "ForvenClient", _FakeClient)
    monkeypatch.setattr(sz_store, "is_configured", lambda: True)
    return create_app(testing=True).test_client()


def test_a_pair_is_saved(client, monkeypatch):
    captured = {}

    def fake_upsert(**kwargs):
        captured.update(kwargs)
        return {"id": 7, "name": kwargs["name"]}

    monkeypatch.setattr(sz_store, "upsert_tenant_pair", fake_upsert)

    body = client.post("/forven/pairs", json={
        "name": "Zoe Enterprises",
        "source_tenant_id": "prod-1",
        "dest_tenant_id": "stg-1",
        "dest_tenant_name": "Zoe Enterprises",
        "is_default": True,
    }).get_json()

    assert body["id"] == 7
    assert captured["source_env"] == "production"
    assert captured["dest_env"] == "staging"
    assert captured["is_default"] is True
    # Blank optional fields become None rather than empty strings, so the echo
    # guard treats them as "not checked" instead of "expected to be nothing".
    assert captured["source_tenant_name"] is None


def test_the_tenant_ids_are_required(client):
    response = client.post("/forven/pairs", json={"name": "Half a pair"})

    assert response.status_code == 400
    assert "source_tenant_id" in response.get_json()["error"]


def test_an_unknown_environment_is_rejected(client):
    """Only production and staging have a base URL and a key."""
    response = client.post("/forven/pairs", json={
        "name": "Typo", "source_tenant_id": "a", "dest_tenant_id": "b",
        "source_env": "prod",
    })

    assert response.status_code == 400


def test_without_a_database_it_says_so_rather_than_failing_obscurely(client, monkeypatch):
    monkeypatch.setattr(sz_store, "is_configured", lambda: False)

    response = client.post("/forven/pairs", json={
        "name": "X", "source_tenant_id": "a", "dest_tenant_id": "b",
    })

    assert response.status_code == 503
    assert "DATABASE_URL" in response.get_json()["error"]


def test_a_pair_can_be_removed(client, monkeypatch):
    monkeypatch.setattr(sz_store, "delete_tenant_pair", lambda pair_id: 1)

    assert client.delete("/forven/pairs/7").get_json() == {"deleted": 1}


def test_a_database_that_is_down_does_not_stop_the_app_booting(monkeypatch):
    """Schema creation runs at startup, so a database that is unreachable could
    take the whole app down with it. It is logged and stepped over instead, and
    the pages still serve - /forven falls back to environment-only config."""
    monkeypatch.setattr(sz_store, "is_configured", lambda: True)
    monkeypatch.setattr(sz_store, "init_schema",
                        lambda: (_ for _ in ()).throw(RuntimeError("no route to host")))

    app = create_app(testing=False)

    assert app.test_client().get("/forven").status_code == 200
