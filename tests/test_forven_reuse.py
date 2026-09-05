"""Tests for reusing interviews we have already pulled.

Alignment is the expensive step - the whole video through faster-whisper on a
job - and an interview recording does not change, so paying for it again every
time the same interview appears in another reel was pure waste. What matters
here is that reuse never quietly serves something that is not actually there,
and never claims an alignment that has not happened.
"""

import pytest

import forven_api
import forven_ingest
import storage
import sz_store
from app import create_app

SESSION = "sessions/aaaa"
HELD = "sessions/older"


class _FakeClient:
    def __init__(self, base_url, api_key):
        pass


@pytest.fixture()
def env(monkeypatch):
    """A held, aligned copy of KEPT; nothing held for FRESH."""
    monkeypatch.setenv("FORVEN_STAGING_API_KEY", "fvk_stg")
    monkeypatch.setenv("FORVEN_STAGING_TENANT_ID", "stg-tenant")
    monkeypatch.setenv("FORVEN_SOURCE_ENV", "staging")
    monkeypatch.setattr(forven_api, "ForvenClient", _FakeClient)
    monkeypatch.setattr(storage, "new_session_key", lambda: SESSION)
    monkeypatch.setattr(sz_store, "is_configured", lambda: True)
    monkeypatch.setattr(sz_store, "ingested_sessions",
                        lambda refs: {"KEPT": {"session_key": HELD, "aligned_at": "t"}})

    state = {"copies": [], "fetched": None, "recorded": []}

    monkeypatch.setattr(storage, "list_keys", lambda prefix: (
        [f"{HELD}/KEPT.txt", f"{HELD}/KEPT.mp4"] if prefix == HELD
        else [f"{SESSION}/KEPT.txt", f"{SESSION}/KEPT.mp4",
              f"{SESSION}/FRESH.txt", f"{SESSION}/FRESH.mp4"]))
    monkeypatch.setattr(storage, "copy_key",
                        lambda src, dest: state["copies"].append((src, dest)))

    def fake_ingest(client, *, tenant_public_id, refs, session_key, log=print):
        state["fetched"] = list(refs)
        return session_key

    monkeypatch.setattr(forven_ingest, "ingest", fake_ingest)
    monkeypatch.setattr(sz_store, "record_ingested",
                        lambda refs, **kw: state["recorded"].append(
                            (sorted(refs), kw.get("preserve_aligned"))))

    # An aligned transcript needs no encoding; a freshly fetched one does.
    import app as app_module
    monkeypatch.setattr(app_module, "_sessions_needing_encode", lambda keys: [])

    state["client"] = create_app(testing=True).test_client()
    return state


def test_a_held_interview_is_copied_and_only_the_new_one_is_fetched(env):
    body = env["client"].post("/forven/pull",
                              json={"refs": ["KEPT", "FRESH"]}).get_json()

    assert env["fetched"] == ["FRESH"]
    assert sorted(d for _, d in env["copies"]) == [
        f"{SESSION}/KEPT.mp4", f"{SESSION}/KEPT.txt"]
    assert body["reused"] == 1


def test_a_reused_alignment_is_kept_and_a_fresh_pull_clears_it(env):
    env["client"].post("/forven/pull", json={"refs": ["KEPT", "FRESH"]})

    recorded = dict((tuple(refs), preserve) for refs, preserve in env["recorded"])
    assert recorded[("KEPT",)] is True
    assert recorded[("FRESH",)] is False


def test_refresh_forces_a_fresh_fetch(env):
    """For when Forven re-transcribes an interview and our copy goes stale."""
    env["client"].post("/forven/pull",
                       json={"refs": ["KEPT"], "refresh": True})

    assert env["fetched"] == ["KEPT"]
    assert env["copies"] == []


def test_an_index_row_whose_files_are_gone_is_not_reused(env, monkeypatch):
    """The index records what we did; the files decide what we still have."""
    monkeypatch.setattr(storage, "list_keys", lambda prefix: (
        [] if prefix == HELD else [f"{SESSION}/KEPT.txt", f"{SESSION}/KEPT.mp4"]))

    body = env["client"].post("/forven/pull", json={"refs": ["KEPT"]}).get_json()

    assert env["fetched"] == ["KEPT"]
    assert body["reused"] == 0


def test_a_plain_held_transcript_is_reused_but_not_called_aligned(env, monkeypatch):
    """Copying still saves the download; the encoder must still run."""
    import app as app_module
    monkeypatch.setattr(app_module, "_sessions_needing_encode",
                        lambda keys: ["something pending"])
    monkeypatch.setattr(storage, "list_keys", lambda prefix: (
        [f"{HELD}/KEPT.txt", f"{HELD}/KEPT.mp4"] if prefix == HELD
        else [f"{SESSION}/KEPT.txt", f"{SESSION}/KEPT.mp4"]))

    env["client"].post("/forven/pull", json={"refs": ["KEPT"]})

    assert env["fetched"] is None          # not re-downloaded
    assert env["recorded"] == [(["KEPT"], False)]   # but not claimed as aligned


def test_a_failed_copy_falls_back_to_fetching(env, monkeypatch):
    monkeypatch.setattr(storage, "copy_key",
                        lambda src, dest: (_ for _ in ()).throw(RuntimeError("no such key")))

    body = env["client"].post("/forven/pull", json={"refs": ["KEPT"]}).get_json()

    assert env["fetched"] == ["KEPT"]
    assert body["reused"] == 0
