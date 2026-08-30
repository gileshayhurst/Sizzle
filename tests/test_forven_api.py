"""Unit tests for the Forven Video Access API client."""

import io
import json
from urllib import error as urlerror

import pytest

import forven_api


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _http_error(code, payload):
    return urlerror.HTTPError(
        "https://staging.forven.ai/api/v1/x",
        code,
        "err",
        {},
        io.BytesIO(json.dumps(payload).encode("utf-8")),
    )


def test_sends_the_bearer_header(monkeypatch):
    captured = {}

    def fake_urlopen(request_obj, timeout=None):
        captured["auth"] = request_obj.get_header("Authorization")
        captured["url"] = request_obj.full_url
        return _FakeResponse({"tenant": {"public_id": "t1", "name": "Alpha"}, "interviews": [], "next_cursor": None})

    monkeypatch.setattr(forven_api.urlrequest, "urlopen", fake_urlopen)
    client = forven_api.ForvenClient("https://staging.forven.ai/api/v1", "fvk_abc")

    client.list_interviews("tenant-1")

    assert captured["auth"] == "Bearer fvk_abc"
    assert "/tenants/tenant-1/interviews" in captured["url"]


def test_list_returns_rows_cursor_and_tenant_echo(monkeypatch):
    monkeypatch.setattr(
        forven_api.urlrequest,
        "urlopen",
        lambda r, timeout=None: _FakeResponse(
            {
                "tenant": {"public_id": "t1", "name": "Alpha Client"},
                "interviews": [{"interview_ref": "AAAA1111"}],
                "next_cursor": "AAAA1111",
            }
        ),
    )
    client = forven_api.ForvenClient("https://staging.forven.ai/api/v1", "fvk_abc")

    page = client.list_interviews("tenant-1")

    assert page.tenant_name == "Alpha Client"
    assert page.rows[0]["interview_ref"] == "AAAA1111"
    assert page.next_cursor == "AAAA1111"


@pytest.mark.parametrize(
    "code,expected",
    [
        (401, forven_api.AuthError),
        (403, forven_api.CapabilityError),
        (404, forven_api.NotVisibleError),
        (429, forven_api.RateLimited),
        (400, forven_api.ForvenApiError),
    ],
)
def test_status_codes_map_to_typed_errors(monkeypatch, code, expected):
    monkeypatch.setattr(
        forven_api.urlrequest,
        "urlopen",
        lambda r, timeout=None: (_ for _ in ()).throw(_http_error(code, {"error": "e", "message": "m"})),
    )
    client = forven_api.ForvenClient("https://staging.forven.ai/api/v1", "fvk_abc")

    with pytest.raises(expected):
        client.list_interviews("tenant-1")


def test_page_size_is_clamped_to_the_documented_maximum(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        forven_api.urlrequest,
        "urlopen",
        lambda r, timeout=None: (
            captured.__setitem__("url", r.full_url),
            _FakeResponse({"tenant": {"public_id": "t", "name": "n"}, "interviews": [], "next_cursor": None}),
        )[1],
    )
    client = forven_api.ForvenClient("https://staging.forven.ai/api/v1", "fvk_abc")

    client.list_interviews("tenant-1", page_size=5000)

    assert "page_size=200" in captured["url"]


def test_iter_interviews_drains_every_page(monkeypatch):
    pages = [
        {"tenant": {"public_id": "t", "name": "n"},
         "interviews": [{"interview_ref": "A"}, {"interview_ref": "B"}],
         "next_cursor": "B"},
        {"tenant": {"public_id": "t", "name": "n"},
         "interviews": [{"interview_ref": "C"}],
         "next_cursor": None},
    ]
    calls = []

    def fake_urlopen(request_obj, timeout=None):
        calls.append(request_obj.full_url)
        return _FakeResponse(pages[len(calls) - 1])

    monkeypatch.setattr(forven_api.urlrequest, "urlopen", fake_urlopen)
    client = forven_api.ForvenClient("https://staging.forven.ai/api/v1", "fvk_abc")

    refs = [row["interview_ref"] for row in client.iter_interviews("tenant-1")]

    assert refs == ["A", "B", "C"]
    assert "cursor=B" in calls[1]


def test_get_transcript_returns_text_entries_and_script(monkeypatch):
    monkeypatch.setattr(
        forven_api.urlrequest,
        "urlopen",
        lambda r, timeout=None: _FakeResponse(
            {
                "tenant": {"public_id": "t", "name": "n"},
                "interview_ref": "AAAA1111",
                "transcript_status": "done",
                "transcript_text": "Interviewer: Hi\nParticipant: Hello",
                "transcript_entries": [
                    {"role": "agent", "message": "Hi", "time_in_call_secs": 1},
                    {"role": "user", "message": "Hello", "time_in_call_secs": 3},
                ],
                "interview_script": "compiled script",
            }
        ),
    )
    client = forven_api.ForvenClient("https://www.forven.ai/api/v1", "fvk_abc")

    result = client.get_transcript("tenant-1", "AAAA1111")

    assert result["transcript_status"] == "done"
    assert len(result["transcript_entries"]) == 2
    assert result["interview_script"] == "compiled script"


def test_entries_become_the_mmss_contract():
    entries = [
        {"role": "agent", "message": "How is the dog?", "time_in_call_secs": 0},
        {"role": "user", "message": "Lazy.", "time_in_call_secs": 64},
        {"role": "user", "message": "Very lazy.", "time_in_call_secs": 3723},
    ]

    text = forven_api.entries_to_contract_text(entries)

    assert text.splitlines() == [
        "[00:00] Interviewer: How is the dog?",
        "[01:04] Participant: Lazy.",
        "[62:03] Participant: Very lazy.",
    ]


def test_contract_text_skips_entries_without_a_message():
    entries = [
        {"role": "user", "message": "", "time_in_call_secs": 1},
        {"role": "user", "message": "Real.", "time_in_call_secs": 2},
    ]

    assert forven_api.entries_to_contract_text(entries) == "[00:02] Participant: Real.\n"


def test_contract_text_of_nothing_is_empty():
    assert forven_api.entries_to_contract_text([]) == ""
    assert forven_api.entries_to_contract_text(None) == ""


def test_media_link_requests_the_asked_for_disposition(monkeypatch):
    captured = {}

    def fake_urlopen(request_obj, timeout=None):
        captured["url"] = request_obj.full_url
        return _FakeResponse(
            {"tenant": {"public_id": "t", "name": "n"},
             "url": "https://s3.example/signed", "expires_at": "2026-08-30T12:00:00+00:00"}
        )

    monkeypatch.setattr(forven_api.urlrequest, "urlopen", fake_urlopen)
    client = forven_api.ForvenClient("https://www.forven.ai/api/v1", "fvk_abc")

    link = client.media_link("tenant-1", "AAAA1111", disposition="attachment")

    assert link["url"] == "https://s3.example/signed"
    assert "disposition=attachment" in captured["url"]


def test_expired_presigned_url_is_recognised():
    xml = b"<Error><Code>AccessDenied</Code><Message>Request has expired</Message></Error>"

    assert forven_api.is_presigned_expiry(403, xml) is True
    assert forven_api.is_presigned_expiry(403, b'{"error": "forbidden"}') is False
    assert forven_api.is_presigned_expiry(200, xml) is False
