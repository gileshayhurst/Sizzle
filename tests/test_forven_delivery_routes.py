"""Tests for listing finished reels and delivering them to Forven.

The behaviour worth pinning down is the refusal: a reel that does not record
which interviews it came from must not be sent. register treats
source_interview_refs as the truth about whose footage is in the cut, so
guessing there would either hide the reel from people entitled to see it or
break participant erasure.
"""

import json

import pytest

import forven_api
import forven_deliver
import storage
import sz_store
from app import create_app

DELIVERABLE = {
    "id": "reel-1",
    "filename": "sizzle_reel.mp4",
    "prompt": "Moments about dog food",
    "duration_seconds": 42,
    "clip_count": 3,
    "created_at": "2026-08-31T10:00:00",
    "reel_s3_key": "sessions/abc/sizzle_reel.mp4",
    "source_videos": ["W1NHQK0E.mp4", "EANCBVAZ.mp4"],
}

LEGACY = {
    "id": "reel-old",
    "filename": "old.mp4",
    "prompt": "Made before we recorded sources",
    "duration_seconds": 30,
    "clip_count": 2,
    "created_at": "2026-08-01T10:00:00",
    "reel_s3_key": "sessions/old/old.mp4",
}


class _FakeClient:
    def __init__(self, base_url, api_key):
        pass


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FORVEN_STAGING_API_KEY", "fvk_stg")
    monkeypatch.setenv("FORVEN_STAGING_TENANT_ID", "stg-tenant")
    monkeypatch.setenv("FORVEN_STAGING_TENANT_NAME", "Zoe Enterprises")
    monkeypatch.setenv("FORVEN_SOURCE_ENV", "staging")
    monkeypatch.setattr(forven_api, "ForvenClient", _FakeClient)
    monkeypatch.setattr(storage, "load_library", lambda: [DELIVERABLE, LEGACY])
    monkeypatch.setattr(storage, "download_file", lambda key, path: None)
    # The store is a recording sidecar here; delivery must not depend on it.
    monkeypatch.setattr(sz_store, "is_configured", lambda: False)
    return create_app(testing=True).test_client()


def test_listing_marks_which_reels_can_be_delivered(client):
    reels = client.get("/forven/reels").get_json()["reels"]

    by_id = {r["id"]: r for r in reels}
    assert by_id["reel-1"]["deliverable"] is True
    assert by_id["reel-1"]["source_refs"] == ["EANCBVAZ", "W1NHQK0E"]
    assert by_id["reel-old"]["deliverable"] is False


def test_delivering_sends_exactly_the_interviews_in_the_cut(client, monkeypatch):
    captured = {}

    def fake_deliver(client_, **kwargs):
        captured.update(kwargs)
        return {"reel_ref": "FR-ABC123", "reel_public_id": "pub-9"}

    monkeypatch.setattr(forven_deliver, "deliver", fake_deliver)

    body = client.post("/forven/deliver", json={"reel_id": "reel-1"}).get_json()

    assert body["reel_ref"] == "FR-ABC123"
    assert captured["source_interview_refs"] == ["EANCBVAZ", "W1NHQK0E"]
    assert captured["duration_seconds"] == 42


def test_a_reel_without_recorded_sources_is_refused(client, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("must not attempt delivery without source refs")

    monkeypatch.setattr(forven_deliver, "deliver", explode)

    response = client.post("/forven/deliver", json={"reel_id": "reel-old"})

    assert response.status_code == 422
    assert "does not record" in response.get_json()["error"]


def test_an_unknown_reel_is_a_404(client):
    response = client.post("/forven/deliver", json={"reel_id": "nope"})

    assert response.status_code == 404


def test_an_api_failure_is_reported_not_swallowed(client, monkeypatch):
    def boom(client_, **kwargs):
        raise forven_api.CapabilityError("403: key lacks allow_upload_reels")

    monkeypatch.setattr(forven_deliver, "deliver", boom)

    response = client.post("/forven/deliver", json={"reel_id": "reel-1"})

    assert response.status_code == 502
    assert "CapabilityError" in response.get_json()["error"]


def test_a_failed_recording_does_not_report_a_failed_delivery(client, monkeypatch):
    """The reel is in Forven. Telling the customer it failed invites a duplicate."""
    monkeypatch.setattr(forven_deliver, "deliver",
                        lambda c, **kw: {"reel_ref": "FR-ABC123", "reel_public_id": "p"})
    monkeypatch.setattr(sz_store, "is_configured", lambda: True)
    monkeypatch.setattr(sz_store, "create_reel",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("db down")))

    response = client.post("/forven/deliver", json={"reel_id": "reel-1"})

    assert response.status_code == 200
    assert response.get_json()["reel_ref"] == "FR-ABC123"
