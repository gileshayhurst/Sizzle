"""Tests for the Render one-off job dispatch (design doc D6)."""
from unittest.mock import patch

import pytest

import render_jobs
from app import _sessions_needing_encode, create_app

SESSION = "sessions/2f8a1c40-1111-2222-3333-444455556666"
PLAIN = "[00:13] Participant: He's a Corgi mix."
RICH = "[0:13-0:16] Participant: He's a Corgi mix."


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "test-key")
    monkeypatch.setenv("RENDER_ENCODER_SERVICE_ID", "srv-test")
    app = create_app(testing=True)
    app.config["RATELIMIT_ENABLED"] = False
    return app.test_client()


# ── start_command: the fixed-entrypoint allow-list ────────────────────────────

def test_start_command_is_built_from_a_closed_template(monkeypatch):
    monkeypatch.setenv("ENCODER_JOB_WORKERS", "8")
    assert render_jobs.start_command(SESSION) == f"python -m encoder.job {SESSION} --workers 8"


@pytest.mark.parametrize("bad", [
    "sessions/../../etc/passwd",
    "sessions/x; rm -rf /",
    "sessions/$(whoami)",
    "library/foo",
    "sessions/",
    "",
    None,
])
def test_start_command_refuses_anything_but_a_session_key(bad):
    """No free-text command may ever reach the Render API."""
    with pytest.raises(ValueError):
        render_jobs.start_command(bad)


def test_get_job_validates_the_job_id():
    with pytest.raises(ValueError):
        render_jobs.get_job("../services/other")


# ── concurrency cap ───────────────────────────────────────────────────────────

def test_create_job_refuses_when_the_cap_is_reached(monkeypatch):
    """Render documents no job limit and bills per second, so we cap app-side."""
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setenv("RENDER_ENCODER_SERVICE_ID", "srv-1")
    monkeypatch.setenv("ENCODER_MAX_CONCURRENT_JOBS", "2")
    with patch("render_jobs.running_job_count", return_value=2), \
         patch("render_jobs._request") as req:
        with pytest.raises(render_jobs.RenderError, match="already in flight"):
            render_jobs.create_encode_job(SESSION)
    req.assert_not_called()


def test_running_job_count_ignores_finished_jobs(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setenv("RENDER_ENCODER_SERVICE_ID", "srv-1")
    payload = [
        {"job": {"id": "a", "status": "succeeded"}},
        {"job": {"id": "b", "status": "running"}},
        {"job": {"id": "c", "status": "failed"}},
        {"job": {"id": "d", "status": "pending"}},
    ]
    with patch("render_jobs._request", return_value=payload):
        assert render_jobs.running_job_count() == 2


def test_dispatch_error_says_what_plan_was_sent(monkeypatch):
    """"free tier not supported" is ambiguous unless we report what we sent."""
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setenv("RENDER_ENCODER_SERVICE_ID", "srv-1")
    monkeypatch.setenv("ENCODER_JOB_PLAN_ID", "plan-srv-010")
    with patch("render_jobs.running_job_count", return_value=0), \
         patch("render_jobs._request",
               side_effect=render_jobs.RenderError("free tier plans are not supported")):
        with pytest.raises(render_jobs.RenderError, match=r"planId=plan-srv-010"):
            render_jobs.create_encode_job(SESSION)


def test_dispatch_error_says_when_no_plan_was_sent(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setenv("RENDER_ENCODER_SERVICE_ID", "srv-1")
    monkeypatch.delenv("ENCODER_JOB_PLAN_ID", raising=False)
    with patch("render_jobs.running_job_count", return_value=0), \
         patch("render_jobs._request",
               side_effect=render_jobs.RenderError("free tier plans are not supported")):
        with pytest.raises(render_jobs.RenderError, match=r"no planId sent"):
            render_jobs.create_encode_job(SESSION)


def test_create_job_omits_plan_id_when_unset(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setenv("RENDER_ENCODER_SERVICE_ID", "srv-1")
    monkeypatch.delenv("ENCODER_JOB_PLAN_ID", raising=False)
    with patch("render_jobs.running_job_count", return_value=0), \
         patch("render_jobs._request", return_value={"id": "job-1", "status": "pending"}) as req:
        render_jobs.create_encode_job(SESSION)
    assert "planId" not in req.call_args[0][2]


def test_create_job_passes_plan_id_when_set(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setenv("RENDER_ENCODER_SERVICE_ID", "srv-1")
    monkeypatch.setenv("ENCODER_JOB_PLAN_ID", "plan-srv-014")
    with patch("render_jobs.running_job_count", return_value=0), \
         patch("render_jobs._request", return_value={"id": "job-1", "status": "pending"}) as req:
        result = render_jobs.create_encode_job(SESSION)
    assert req.call_args[0][2]["planId"] == "plan-srv-014"
    assert result["plan_id"] == "plan-srv-014"


# ── _sessions_needing_encode ──────────────────────────────────────────────────

def test_needing_encode_finds_plain_transcripts():
    keys = [f"{SESSION}/a.mp4", f"{SESSION}/a.txt"]
    with patch("app.storage.read_file_bytes", return_value=PLAIN.encode()):
        assert _sessions_needing_encode(keys) == [f"{SESSION}/a.mp4"]


def test_needing_encode_skips_rich_transcripts():
    keys = [f"{SESSION}/a.mp4", f"{SESSION}/a.txt"]
    with patch("app.storage.read_file_bytes", return_value=RICH.encode()):
        assert _sessions_needing_encode(keys) == []


def test_needing_encode_skips_videos_with_no_transcript():
    with patch("app.storage.read_file_bytes", return_value=PLAIN.encode()):
        assert _sessions_needing_encode([f"{SESSION}/lonely.mp4"]) == []


# ── endpoint behaviour ────────────────────────────────────────────────────────

def test_encode_session_requires_cloud_mode(client):
    with patch("app.storage.is_cloud", return_value=False):
        resp = client.post("/encode-session", json={"session_key": SESSION})
    assert resp.status_code == 400


def test_encode_session_rejects_a_bad_session_key(client):
    with patch("app.storage.is_cloud", return_value=True):
        resp = client.post("/encode-session", json={"session_key": "sessions/x; rm -rf /"})
    assert resp.status_code == 400


def test_encode_session_404s_for_an_unknown_session(client):
    with patch("app.storage.is_cloud", return_value=True), \
         patch("app.storage.list_keys", return_value=[]):
        resp = client.post("/encode-session", json={"session_key": SESSION})
    assert resp.status_code == 404


def test_encode_session_reports_no_transcripts_as_an_error_not_a_tick(client):
    """A session with videos but no .txt is misconfigured, not already encoded."""
    with patch("app.storage.is_cloud", return_value=True), \
         patch("app.storage.list_keys", return_value=[f"{SESSION}/a.mp4", f"{SESSION}/b.mp4"]), \
         patch("app.render_jobs.create_encode_job") as create:
        resp = client.post("/encode-session", json={"session_key": SESSION})
    assert resp.status_code == 422
    assert "transcript" in resp.get_json()["error"]
    create.assert_not_called()


def test_encode_session_records_a_failed_dispatch_for_later_diagnosis(client):
    """The browser's error vanishes in seconds; the failure must leave a trace."""
    written = {}
    with patch("app.storage.is_cloud", return_value=True), \
         patch("app.storage.list_keys", return_value=[f"{SESSION}/a.mp4", f"{SESSION}/a.txt"]), \
         patch("app.storage.read_file_bytes", return_value=PLAIN.encode()), \
         patch("app.storage.write_json", side_effect=lambda k, d: written.__setitem__(k, d)), \
         patch("app.render_jobs.create_encode_job",
               side_effect=render_jobs.RenderError("service srv-xyz not found")):
        resp = client.post("/encode-session", json={"session_key": SESSION})

    assert resp.status_code == 503
    record = written[f"{SESSION}/encode_job.json"]
    assert record["error"] == "service srv-xyz not found"
    assert record["interviews"] == [f"{SESSION}/a.mp4"]


def test_encode_session_skips_when_everything_is_already_rich(client):
    """A repeat call must be free, not a duplicate encode."""
    with patch("app.storage.is_cloud", return_value=True), \
         patch("app.storage.list_keys", return_value=[f"{SESSION}/a.mp4", f"{SESSION}/a.txt"]), \
         patch("app.storage.read_file_bytes", return_value=RICH.encode()), \
         patch("app.render_jobs.create_encode_job") as create:
        resp = client.post("/encode-session", json={"session_key": SESSION})
    assert resp.get_json() == {"skipped": True, "reason": "all transcripts already rich"}
    create.assert_not_called()


def test_encode_session_dispatches_and_records(client):
    written = {}
    job = {"job_id": "job-9", "status": "pending", "plan_id": "plan-srv-014",
           "start_command": f"python -m encoder.job {SESSION} --workers 8"}
    with patch("app.storage.is_cloud", return_value=True), \
         patch("app.storage.list_keys", return_value=[f"{SESSION}/a.mp4", f"{SESSION}/a.txt"]), \
         patch("app.storage.read_file_bytes", return_value=PLAIN.encode()), \
         patch("app.storage.write_json", side_effect=lambda k, d: written.__setitem__(k, d)), \
         patch("app.render_jobs.create_encode_job", return_value=job):
        resp = client.post("/encode-session", json={"session_key": SESSION})

    body = resp.get_json()
    assert body == {"job_id": "job-9", "status": "pending", "interviews": 1}
    # Recorded so Render's job list can be reconciled against our own dispatches.
    record = written[f"{SESSION}/encode_job.json"]
    assert record["job_id"] == "job-9"
    assert record["start_command"] == job["start_command"]


def test_encode_session_still_succeeds_if_the_audit_write_fails(client):
    """The job is already running; losing the note must not fail the request."""
    job = {"job_id": "job-9", "status": "pending", "plan_id": None, "start_command": "cmd"}
    with patch("app.storage.is_cloud", return_value=True), \
         patch("app.storage.list_keys", return_value=[f"{SESSION}/a.mp4", f"{SESSION}/a.txt"]), \
         patch("app.storage.read_file_bytes", return_value=PLAIN.encode()), \
         patch("app.storage.write_json", side_effect=OSError("R2 down")), \
         patch("app.render_jobs.create_encode_job", return_value=job):
        resp = client.post("/encode-session", json={"session_key": SESSION})
    assert resp.status_code == 200
    assert resp.get_json()["job_id"] == "job-9"


def test_encode_session_surfaces_dispatch_failure_immediately(client):
    """D6 chose direct launch precisely so this is visible, not deferred."""
    with patch("app.storage.is_cloud", return_value=True), \
         patch("app.storage.list_keys", return_value=[f"{SESSION}/a.mp4", f"{SESSION}/a.txt"]), \
         patch("app.storage.read_file_bytes", return_value=PLAIN.encode()), \
         patch("app.render_jobs.create_encode_job",
               side_effect=render_jobs.RenderError("2 job(s) already in flight")):
        resp = client.post("/encode-session", json={"session_key": SESSION})
    assert resp.status_code == 503
    assert "in flight" in resp.get_json()["error"]


def test_encode_session_503s_when_not_configured(monkeypatch):
    monkeypatch.delenv("RENDER_API_KEY", raising=False)
    monkeypatch.delenv("RENDER_ENCODER_SERVICE_ID", raising=False)
    app = create_app(testing=True)
    app.config["RATELIMIT_ENABLED"] = False
    with patch("app.storage.is_cloud", return_value=True):
        resp = app.test_client().post("/encode-session", json={"session_key": SESSION})
    assert resp.status_code == 503


def test_encode_status_returns_job_state(client):
    with patch("app.render_jobs.get_job",
               return_value={"job_id": "job-9", "status": "succeeded",
                             "started_at": "t0", "finished_at": "t1"}):
        resp = client.get("/encode-status/job-9")
    assert resp.get_json()["status"] == "succeeded"


def test_encode_status_rejects_a_malformed_job_id(client):
    with patch("app.render_jobs.get_job", side_effect=ValueError("invalid job id")):
        resp = client.get("/encode-status/..%2Fother")
    assert resp.status_code in (400, 404)
