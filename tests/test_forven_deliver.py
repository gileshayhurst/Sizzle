"""Unit tests for delivering a finished reel back to Forven."""

import pytest

import forven_deliver


class _FakeClient:
    def __init__(self):
        self.starts = 0
        self.registered = None

    def reel_upload_start(self, tenant, content_type="video/mp4"):
        self.starts += 1
        return {"s3_key": f"reels/t/{self.starts}.mp4",
                "upload_url": f"https://s3.example/put/{self.starts}",
                "content_type": content_type, "expires_at": "later"}

    def reel_register(self, tenant, *, s3_key, title, duration_seconds,
                      source_interview_refs, metadata=None):
        self.registered = {"s3_key": s3_key, "title": title,
                           "duration_seconds": duration_seconds,
                           "refs": source_interview_refs, "metadata": metadata}
        return {"reel_public_id": "pub-1", "reel_ref": "K4M9P2"}


def test_deliver_uploads_then_registers_the_same_key(monkeypatch, tmp_path):
    reel = tmp_path / "reel.mp4"
    reel.write_bytes(b"video")
    client = _FakeClient()
    monkeypatch.setattr(forven_deliver, "_put_file", lambda url, path, content_type: 200)

    result = forven_deliver.deliver(
        client, tenant_public_id="t1", reel_path=str(reel), title="Q3 highlights",
        duration_seconds=95, source_interview_refs=["AAAA1111"],
    )

    assert result["reel_ref"] == "K4M9P2"
    assert client.registered["s3_key"] == "reels/t/1.mp4"
    assert client.registered["refs"] == ["AAAA1111"]


def test_an_expired_upload_url_restarts_with_a_new_pair(monkeypatch, tmp_path):
    reel = tmp_path / "reel.mp4"
    reel.write_bytes(b"video")
    client = _FakeClient()
    attempts = {"n": 0}

    def flaky_put(url, path, content_type):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise forven_deliver.UploadExpired("presigned URL expired")
        return 200

    monkeypatch.setattr(forven_deliver, "_put_file", flaky_put)

    forven_deliver.deliver(
        client, tenant_public_id="t1", reel_path=str(reel), title="t",
        duration_seconds=10, source_interview_refs=["AAAA1111"],
    )

    assert client.starts == 2
    assert client.registered["s3_key"] == "reels/t/2.mp4"


def test_empty_source_refs_are_refused_before_any_upload(tmp_path):
    reel = tmp_path / "reel.mp4"
    reel.write_bytes(b"video")
    client = _FakeClient()

    with pytest.raises(ValueError):
        forven_deliver.deliver(
            client, tenant_public_id="t1", reel_path=str(reel), title="t",
            duration_seconds=10, source_interview_refs=[],
        )

    assert client.starts == 0
