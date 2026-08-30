"""Unit tests for the end-to-end ingest pipeline."""

import forven_pipeline


def test_sync_ingests_only_unseen_refs(monkeypatch):
    seen = {"AAAA1111"}
    rows = [{"interview_ref": "AAAA1111"}, {"interview_ref": "BBBB2222"}]
    ingested = {}

    class _Client:
        def iter_interviews(self, tenant, **kwargs):
            return iter(rows)

    monkeypatch.setattr(forven_pipeline, "already_ingested", lambda: seen)
    monkeypatch.setattr(
        forven_pipeline, "ingest",
        lambda client, *, tenant_public_id, refs, session_key, log=print: ingested.update({"refs": refs}) or session_key,
    )
    monkeypatch.setattr(forven_pipeline, "record_ingested", lambda refs: None)
    monkeypatch.setattr(forven_pipeline.storage, "new_session_key", lambda: "sessions/new")

    session_key = forven_pipeline.sync(_Client(), tenant_public_id="t1")

    assert ingested["refs"] == ["BBBB2222"]
    assert session_key == "sessions/new"


def test_sync_returns_none_when_there_is_nothing_new(monkeypatch):
    class _Client:
        def iter_interviews(self, tenant, **kwargs):
            return iter([{"interview_ref": "AAAA1111"}])

    monkeypatch.setattr(forven_pipeline, "already_ingested", lambda: {"AAAA1111"})

    assert forven_pipeline.sync(_Client(), tenant_public_id="t1") is None
