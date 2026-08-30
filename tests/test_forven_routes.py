"""Unit tests for the Forven pull interface routes."""

import pytest

import forven_api
import forven_config
import forven_ingest
import storage
from app import create_app


class _FakePage:
    def __init__(self, tenant_name, rows=None):
        self.tenant_name = tenant_name
        self.tenant_public_id = "t1"
        self.rows = rows or []
        self.next_cursor = None


class _FakeClient:
    """Stands in for ForvenClient. tenant_name drives the echo guard."""

    tenant_name = "Zoe Enterprises"
    rows = [
        {"interview_ref": "W1NHQK0E", "topic": "Dog Food & Care",
         "duration_seconds": 120, "source": "shared", "media_status": "completed",
         "interview_date": "2026-08-01T12:00:00+00:00"},
    ]

    def __init__(self, base_url, api_key):
        self.base_url = base_url

    def list_interviews(self, tenant, **kwargs):
        return _FakePage(self.tenant_name, self.rows)

    def iter_interviews(self, tenant, **kwargs):
        return iter(self.rows)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FORVEN_STAGING_API_KEY", "fvk_stg")
    monkeypatch.setenv("FORVEN_STAGING_TENANT_ID", "stg-tenant")
    monkeypatch.setenv("FORVEN_STAGING_TENANT_NAME", "Zoe Enterprises")
    monkeypatch.setenv("FORVEN_SOURCE_ENV", "staging")
    monkeypatch.setattr(forven_api, "ForvenClient", _FakeClient)
    return create_app(testing=True).test_client()


def test_page_renders(client):
    response = client.get("/forven")

    assert response.status_code == 200
    assert b"Forven" in response.data


def test_listing_returns_interviews_and_connection_echo(client):
    response = client.get("/forven/interviews")
    body = response.get_json()

    assert response.status_code == 200
    assert body["source_env"] == "staging"
    assert body["interviews"][0]["interview_ref"] == "W1NHQK0E"
    assert [c["status"] for c in body["connections"]] == ["ok", "ok"]


def test_listing_flags_a_tenant_mismatch(client, monkeypatch):
    """The worst misconfiguration available: valid id, wrong organisation."""
    monkeypatch.setattr(_FakeClient, "tenant_name", "Portland Tea Shop")

    body = client.get("/forven/interviews").get_json()

    assert body["connections"][0]["status"] == "mismatch"
    assert "Zoe Enterprises" in body["connections"][0]["detail"]


def test_listing_reports_an_api_failure_without_crashing(client, monkeypatch):
    def boom(self, tenant, **kwargs):
        raise forven_api.NotVisibleError("Forven API 404: feature off")

    monkeypatch.setattr(_FakeClient, "iter_interviews", boom)

    response = client.get("/forven/interviews")

    assert response.status_code == 502
    assert "NotVisibleError" in response.get_json()["error"]


def test_pull_requires_at_least_one_ref(client):
    response = client.post("/forven/pull", json={"refs": []})

    assert response.status_code == 400
    assert "Select at least one" in response.get_json()["error"]


def test_pull_ingests_and_reports_what_landed(client, monkeypatch):
    monkeypatch.setattr(storage, "new_session_key", lambda: "sessions/abc")
    monkeypatch.setattr(
        forven_ingest, "ingest",
        lambda c, *, tenant_public_id, refs, session_key, log=print: session_key,
    )
    # One transcript landed even though two were requested - the second had no
    # transcript ready and was skipped.
    monkeypatch.setattr(storage, "list_keys",
                        lambda prefix: ["sessions/abc/W1NHQK0E.txt", "sessions/abc/W1NHQK0E.mp4"])

    body = client.post("/forven/pull", json={"refs": ["W1NHQK0E", "EANCBVAZ"]}).get_json()

    assert body["session_key"] == "sessions/abc"
    assert body["ingested"] == 1
    assert body["requested"] == 2
