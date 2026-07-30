"""Render one-off job dispatch for the transcript encoder.

Implements D6 from sizzle_reel_design.md: the web app writes a record and
launches the job directly, rather than a cron dispatcher. Dispatch failure is
therefore immediate and visible instead of deferred.

Uses urllib from the stdlib rather than adding `requests` — two GETs and a POST
do not justify a dependency.

⚠️ Security posture, and how it differs from the design doc. §10 assumes the
dispatch endpoint sits behind platform-admin auth with CSRF; this app has **no
authentication at all**, so that control does not exist here. What remains:

  * a fixed entrypoint allow-list — no free-text command reaches the API
  * a validated session key — the only variable part of the command line
  * the session must exist and actually need work, so an attacker cannot
    manufacture cost against an arbitrary key
  * an app-side concurrency cap, since Render documents no job limit and
    billing is per second
  * a dispatch record in object storage, so Render jobs can be reconciled
    against jobs this app launched (the §10 detection control)

The residual risk §10 names is unchanged and worth restating: RENDER_API_KEY on
an internet-facing service grants workspace-wide Render control if that service
is compromised. Render documents revocation but not rotation, so replacing it
means revoke-and-replace.
"""
import json
import os
import re
import urllib.error
import urllib.request

RENDER_API = "https://api.render.com/v1"

# Mirrors encoder.job.SESSION_KEY_RE. Deliberately duplicated rather than
# imported: app.py must not depend on the encoder package, which ships in its
# own image and is not present in the web app's build.
SESSION_KEY_RE = re.compile(r"^sessions/[0-9a-fA-F-]{8,64}$")

# Statuses Render reports for a job that has stopped.
TERMINAL_STATUSES = {"succeeded", "failed", "canceled"}

DEFAULT_MAX_CONCURRENT = 2


class RenderError(RuntimeError):
    """A Render API call failed, or the integration is not configured."""


def is_configured() -> bool:
    return bool(os.environ.get("RENDER_API_KEY") and os.environ.get("RENDER_ENCODER_SERVICE_ID"))


def _request(method: str, path: str, body: dict | None = None) -> dict | list:
    key = os.environ.get("RENDER_API_KEY")
    if not key:
        raise RenderError("RENDER_API_KEY is not set")

    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{RENDER_API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if data else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise RenderError(f"Render API {method} {path} failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise RenderError(f"Render API unreachable: {exc.reason}") from exc


def _service_id() -> str:
    service_id = os.environ.get("RENDER_ENCODER_SERVICE_ID")
    if not service_id:
        raise RenderError("RENDER_ENCODER_SERVICE_ID is not set")
    return service_id


def start_command(session_key: str) -> str:
    """Build the job's command from a closed template.

    The session key is the ONLY variable part and is validated first, so a
    tampered request cannot retarget the job or inject a different command.
    """
    if not SESSION_KEY_RE.match(session_key or ""):
        raise ValueError(f"invalid session key: {session_key!r}")
    workers = int(os.environ.get("ENCODER_JOB_WORKERS", "8"))
    return f"python -m encoder.job {session_key} --workers {workers}"


def configured_plan_id() -> str | None:
    """The instance type this process will request for a job, if any.

    Read from the WEB app's environment — it is the dispatcher. Setting this on
    the encoder service instead has no effect, because that process never runs
    this code.
    """
    return os.environ.get("ENCODER_JOB_PLAN_ID") or None


def running_job_count() -> int:
    """How many encoder jobs are currently pending or running."""
    jobs = _request("GET", f"/services/{_service_id()}/jobs?limit=50")
    # Render wraps list results as [{"job": {...}}, …]; tolerate both shapes.
    count = 0
    for entry in jobs if isinstance(jobs, list) else []:
        job = entry.get("job", entry) if isinstance(entry, dict) else {}
        if job.get("status") not in TERMINAL_STATUSES:
            count += 1
    return count


def create_encode_job(session_key: str) -> dict:
    """Launch the encoder job for `session_key`. Returns Render's job object."""
    command = start_command(session_key)

    cap = int(os.environ.get("ENCODER_MAX_CONCURRENT_JOBS", DEFAULT_MAX_CONCURRENT))
    if running_job_count() >= cap:
        raise RenderError(
            f"{cap} encoder job(s) already in flight; refusing to launch another")

    body = {"startCommand": command}
    # Without a planId the job inherits the base service's instance type. Render
    # REFUSES a job whose resolved plan is free ("free tier plans are not
    # supported for jobs", verified 2026-07-30), so this is not merely a speed
    # dial — with a free base service it is the difference between working and
    # not. Per-second billing makes a bigger instance roughly cost-neutral for
    # CPU-bound work (design doc §8).
    plan_id = configured_plan_id()
    if plan_id:
        body["planId"] = plan_id

    try:
        result = _request("POST", f"/services/{_service_id()}/jobs", body)
    except RenderError as exc:
        # Say what we actually sent. "free tier not supported" is ambiguous
        # otherwise: it cannot be told apart from ENCODER_JOB_PLAN_ID being
        # unset, or set on the wrong service and so never reaching this process.
        sent = f"planId={plan_id}" if plan_id else "no planId sent (job inherits the base service plan)"
        raise RenderError(f"{exc} [{sent}]") from exc
    job = result.get("job", result) if isinstance(result, dict) else {}
    return {
        "job_id": job.get("id"),
        "status": job.get("status", "pending"),
        "plan_id": plan_id,
        "start_command": command,
    }


def get_job(job_id: str) -> dict:
    """Current status of one job."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id or ""):
        raise ValueError(f"invalid job id: {job_id!r}")
    result = _request("GET", f"/services/{_service_id()}/jobs/{job_id}")
    job = result.get("job", result) if isinstance(result, dict) else {}
    return {
        "job_id": job.get("id", job_id),
        "status": job.get("status", "pending"),
        "started_at": job.get("startedAt"),
        "finished_at": job.get("finishedAt"),
    }
