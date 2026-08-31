"""Tests for recording which interviews an encoder job actually aligned.

The encoder holds no database credentials by design, so the web app records the
outcome on its behalf when it polls the job. What matters here is that it
records the truth rather than the job's exit status: an interview whose
sentences would not anchor is skipped by the encoder and keeps turn-level
timings, and marking it aligned would be wrong for exactly the interviews that
cut badly.
"""

import pytest

import render_jobs
import storage
import sz_store
from app import create_app

SESSION = "sessions/11111111-2222-3333-4444-555555555555"

# ALIGNED was encoded, so the encoder preserved its original alongside it.
# UNANCHORED was skipped: no .forven.txt was ever written.
KEYS = [
    f"{SESSION}/ALIGNED.mp4",
    f"{SESSION}/ALIGNED.txt",
    f"{SESSION}/ALIGNED.forven.txt",
    f"{SESSION}/UNANCHORED.mp4",
    f"{SESSION}/UNANCHORED.txt",
]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(render_jobs, "is_configured", lambda: True)
    monkeypatch.setattr(storage, "list_keys", lambda prefix: KEYS)
    monkeypatch.setattr(sz_store, "is_configured", lambda: True)
    return create_app(testing=True).test_client()


@pytest.fixture()
def marked(monkeypatch):
    calls = []
    monkeypatch.setattr(sz_store, "mark_aligned", lambda refs: calls.append(list(refs)))
    return calls


def _job(status):
    return lambda job_id: {"job_id": job_id, "status": status}


def test_only_the_interviews_that_encoded_are_marked(client, marked, monkeypatch):
    monkeypatch.setattr(render_jobs, "get_job", _job("succeeded"))

    response = client.get(f"/encode-status/j1?session_key={SESSION}")

    assert response.status_code == 200
    assert marked == [["ALIGNED"]]


def test_nothing_is_marked_while_the_job_is_still_running(client, marked, monkeypatch):
    monkeypatch.setattr(render_jobs, "get_job", _job("running"))

    assert client.get(f"/encode-status/j1?session_key={SESSION}").status_code == 200
    assert marked == []


def test_a_failed_job_marks_nothing(client, marked, monkeypatch):
    monkeypatch.setattr(render_jobs, "get_job", _job("failed"))

    client.get(f"/encode-status/j1?session_key={SESSION}")

    assert marked == []


def test_a_bogus_session_key_is_ignored(client, marked, monkeypatch):
    """The key reaches a storage prefix, so it gets the same regex as dispatch."""
    monkeypatch.setattr(render_jobs, "get_job", _job("succeeded"))

    client.get("/encode-status/j1?session_key=../../etc")

    assert marked == []


def test_status_is_still_reported_when_recording_fails(client, monkeypatch):
    """Polling is how the browser knows alignment finished. A database problem
    must not make a finished job look unfinished."""
    monkeypatch.setattr(render_jobs, "get_job", _job("succeeded"))
    monkeypatch.setattr(sz_store, "mark_aligned",
                        lambda refs: (_ for _ in ()).throw(RuntimeError("db down")))

    response = client.get(f"/encode-status/j1?session_key={SESSION}")

    assert response.status_code == 200
    assert response.get_json()["status"] == "succeeded"


def test_no_session_key_still_returns_the_status(client, marked, monkeypatch):
    monkeypatch.setattr(render_jobs, "get_job", _job("succeeded"))

    assert client.get("/encode-status/j1").get_json()["status"] == "succeeded"
    assert marked == []
