"""Client for Forven's Video Access API (v1).

Auth is a single Bearer header; there are no cookies, sessions, or CSRF tokens.
Keys are per-environment and never crossed: a prod key reads prod sources, a
staging key writes staging reels. See developer-docs/video_access_api.md.

Uses urllib rather than requests - this repo has no requests dependency.
"""

import json
from dataclasses import dataclass
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

STAGING_BASE = "https://staging.forven.ai/api/v1"
PRODUCTION_BASE = "https://www.forven.ai/api/v1"

MAX_PAGE_SIZE = 200
TIMEOUT_SECONDS = 30


class ForvenApiError(Exception):
    """Any non-success response from the API."""


class AuthError(ForvenApiError):
    """401 - key missing, malformed, unknown, or revoked."""


class CapabilityError(ForvenApiError):
    """403 - key lacks allow_download or allow_upload_reels."""


class NotVisibleError(ForvenApiError):
    """404 - feature flag off, unknown tenant, or ref not visible to it.

    The API makes 'does not exist' and 'not visible to you' deliberately
    indistinguishable. Check the tenant echo before assuming a bug.
    """


class RateLimited(ForvenApiError):
    """429 - windowed limit exceeded."""


_STATUS_ERRORS = {
    401: AuthError,
    403: CapabilityError,
    404: NotVisibleError,
    429: RateLimited,
}


@dataclass
class InterviewPage:
    tenant_public_id: str
    tenant_name: str
    rows: list
    next_cursor: str | None


class ForvenClient:
    def __init__(self, base_url: str, api_key: str):
        if not api_key:
            raise ForvenApiError("Missing Forven API key.")
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key

    def _request(self, method: str, path: str, *, query: dict | None = None,
                 body: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlparse.urlencode({k: v for k, v in query.items() if v is not None})}"

        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request_obj = urlrequest.Request(url=url, data=data, headers=headers, method=method)

        try:
            with urlrequest.urlopen(request_obj, timeout=TIMEOUT_SECONDS) as response:
                raw = response.read().decode("utf-8")
        except urlerror.HTTPError as exc:
            raise self._to_error(exc)
        except urlerror.URLError as exc:
            raise ForvenApiError(f"Unable to reach Forven: {exc.reason}")

        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            raise ForvenApiError("Invalid JSON response from Forven.")

    def _to_error(self, exc) -> ForvenApiError:
        raw = exc.read().decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {}
        detail = parsed.get("message") or parsed.get("error") or raw.strip()
        cls = _STATUS_ERRORS.get(exc.code, ForvenApiError)
        return cls(f"Forven API {exc.code}: {detail}")

    def list_interviews(self, tenant_public_id: str, *, since: str | None = None,
                        source: str | None = None, page_size: int = 100,
                        cursor: str | None = None) -> InterviewPage:
        payload = self._request(
            "GET",
            f"/tenants/{tenant_public_id}/interviews",
            query={
                "since": since,
                "source": source,
                "page_size": min(page_size, MAX_PAGE_SIZE),
                "cursor": cursor,
            },
        )
        tenant = payload.get("tenant") or {}
        return InterviewPage(
            tenant_public_id=str(tenant.get("public_id") or ""),
            tenant_name=str(tenant.get("name") or ""),
            rows=payload.get("interviews") or [],
            next_cursor=payload.get("next_cursor"),
        )

    def iter_interviews(self, tenant_public_id: str, *, since: str | None = None,
                        source: str | None = None, page_size: int = 100):
        """Yield every visible interview row, draining the cursor.

        Rows arrive in stable insertion order, NOT sorted by interview_date.
        Callers must not stop early on a date heuristic - drain to exhaustion.
        """
        cursor = None
        while True:
            page = self.list_interviews(
                tenant_public_id, since=since, source=source,
                page_size=page_size, cursor=cursor,
            )
            for row in page.rows:
                yield row
            cursor = page.next_cursor
            if not cursor:
                return

    def get_transcript(self, tenant_public_id: str, interview_ref: str) -> dict:
        """Transcript, entries, metadata and compiled script for one interview."""
        return self._request(
            "GET", f"/tenants/{tenant_public_id}/interviews/{interview_ref}/transcript"
        )

    def media_link(self, tenant_public_id: str, interview_ref: str, *,
                   disposition: str = "inline") -> dict:
        """A presigned media URL, valid 60 minutes. Never store these - re-mint.

        disposition='attachment' downloads and needs the key's allow_download;
        without it the API returns 403 (CapabilityError).
        """
        return self._request(
            "GET",
            f"/tenants/{tenant_public_id}/interviews/{interview_ref}/media-link",
            query={"disposition": disposition},
        )

    def reel_upload_start(self, tenant_public_id: str, content_type: str = "video/mp4") -> dict:
        """Get a presigned PUT target for a finished reel. Needs allow_upload_reels."""
        return self._request(
            "POST", f"/tenants/{tenant_public_id}/reels/upload-start",
            body={"content_type": content_type},
        )

    def reel_register(self, tenant_public_id: str, *, s3_key: str, title: str,
                      duration_seconds: int, source_interview_refs: list,
                      metadata: dict | None = None) -> dict:
        """Register an uploaded reel. s3_key must be the one the PUT used."""
        return self._request(
            "POST", f"/tenants/{tenant_public_id}/reels",
            body={
                "s3_key": s3_key,
                "title": title,
                "duration_seconds": duration_seconds,
                "source_interview_refs": source_interview_refs,
                "metadata": metadata or {},
            },
        )


# The API's roles, mapped to the labels the existing pipeline expects.
_ROLE_LABELS = {"agent": "Interviewer", "user": "Participant"}


def entries_to_contract_text(entries) -> str:
    """Render transcript_entries as the tool's '[MM:SS] Role: text' contract.

    These timings are TURN-level and on a different clock than the video - they
    are a starting point for forced alignment, never a basis for cutting clips.
    Minutes are not wrapped at 60: an hour-long interview reads [62:03], which
    the downstream parser handles.
    """
    lines = []
    for entry in entries or []:
        message = (entry.get("message") or "").strip()
        if not message:
            continue
        seconds = int(entry.get("time_in_call_secs") or 0)
        label = _ROLE_LABELS.get(entry.get("role"), "Participant")
        lines.append(f"[{seconds // 60:02d}:{seconds % 60:02d}] {label}: {message}")
    return "".join(f"{line}\n" for line in lines)


def is_presigned_expiry(status_code: int, body: bytes) -> bool:
    """True when a 403 came from S3 for an expired presigned URL.

    The API answers JSON; S3 answers XML. A 403 with an XML AccessDenied body
    means the 60-minute window closed, NOT that the key lacks a capability.
    """
    if status_code != 403:
        return False
    return b"<Code>AccessDenied</Code>" in body or b"<Error>" in body
