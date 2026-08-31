"""Unit tests for ingesting Forven interviews into a session prefix."""

import forven_ingest


class _FakeClient:
    def __init__(self):
        self.downloaded = []

    def get_transcript(self, tenant, ref):
        return {
            "transcript_status": "done",
            "transcript_entries": [
                {"role": "agent", "message": "Q", "time_in_call_secs": 0},
                {"role": "user", "message": "A", "time_in_call_secs": 5},
            ],
        }

    def media_link(self, tenant, ref, disposition="inline"):
        return {"url": f"https://s3.example/{ref}", "expires_at": "later"}


def test_ingest_writes_a_pair_per_interview(monkeypatch, tmp_path):
    written = {}
    monkeypatch.setattr(forven_ingest.storage, "upload_bytes",
                        lambda key, data, content_type="application/octet-stream": written.__setitem__(key, data))
    monkeypatch.setattr(forven_ingest, "_download_to_storage",
                        lambda url, key: written.__setitem__(key, b"video-bytes"))

    session_key = forven_ingest.ingest(
        _FakeClient(), tenant_public_id="t1", refs=["AAAA1111"], session_key="sessions/abc"
    )

    assert session_key == "sessions/abc"
    assert written["sessions/abc/AAAA1111.txt"].decode() == "[00:00] Interviewer: Q\n[00:05] Participant: A\n"
    assert written["sessions/abc/AAAA1111.mp4"] == b"video-bytes"


def test_ingest_skips_an_interview_with_no_transcript(monkeypatch):
    class _NoTranscript(_FakeClient):
        def get_transcript(self, tenant, ref):
            return {"transcript_status": "processing", "transcript_entries": None}

    written = {}
    monkeypatch.setattr(forven_ingest.storage, "upload_bytes",
                        lambda key, data, content_type="application/octet-stream": written.__setitem__(key, data))
    monkeypatch.setattr(forven_ingest, "_download_to_storage", lambda url, key: None)

    forven_ingest.ingest(_NoTranscript(), tenant_public_id="t1", refs=["BBBB2222"],
                         session_key="sessions/abc")

    assert written == {}


def test_download_uses_upload_file_so_it_works_in_local_mode(monkeypatch, tmp_path):
    """storage.upload_stream is cloud-only and raises in local mode."""
    import io

    class _FakeResponse:
        def __init__(self, data):
            self._buf = io.BytesIO(data)

        def read(self, size=-1):
            return self._buf.read(size)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    uploaded = {}

    def fake_upload_file(local_path, key):
        with open(local_path, "rb") as handle:
            uploaded[key] = handle.read()

    def exploding_upload_stream(key, readable):
        raise AssertionError("upload_stream is cloud-only and must not be used here")

    monkeypatch.setattr(forven_ingest.urlrequest, "urlopen",
                        lambda url, timeout=None: _FakeResponse(b"video-bytes"))
    monkeypatch.setattr(forven_ingest.storage, "upload_file", fake_upload_file)
    monkeypatch.setattr(forven_ingest.storage, "upload_stream", exploding_upload_stream,
                        raising=False)

    forven_ingest._download_to_storage("https://s3.example/x", "sessions/abc/X.mp4")

    assert uploaded["sessions/abc/X.mp4"] == b"video-bytes"
