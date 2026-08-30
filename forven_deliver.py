"""Deliver a finished reel into Forven: upload-start, PUT, register.

The presigned PUT lives 60 minutes. If it expires we do NOT retry the same URL:
we call upload-start again and use the new s3_key and upload_url as a pair,
because register must be given the key the successful PUT actually used. The
abandoned key created nothing and needs no cleanup.
"""

from urllib import error as urlerror
from urllib import request as urlrequest

from forven_api import is_presigned_expiry

PUT_TIMEOUT_SECONDS = 600
MAX_UPLOAD_ATTEMPTS = 2


class UploadExpired(Exception):
    """The presigned PUT URL passed its 60-minute window."""


def _put_file(upload_url: str, reel_path: str, content_type: str) -> int:
    with open(reel_path, "rb") as handle:
        request_obj = urlrequest.Request(
            url=upload_url, data=handle.read(),
            headers={"Content-Type": content_type}, method="PUT",
        )
        try:
            with urlrequest.urlopen(request_obj, timeout=PUT_TIMEOUT_SECONDS) as response:
                return response.status
        except urlerror.HTTPError as exc:
            body = exc.read()
            if is_presigned_expiry(exc.code, body):
                raise UploadExpired("presigned upload URL expired")
            raise


def deliver(client, *, tenant_public_id: str, reel_path: str, title: str,
            duration_seconds: int, source_interview_refs: list,
            metadata: dict | None = None, content_type: str = "video/mp4") -> dict:
    """Upload and register one reel. Returns the register response.

    source_interview_refs must be EXACTLY the interviews whose footage is in the
    finished cut - not the candidates considered. Extra refs wrongly restrict
    where the reel may be shown; missing refs break participant erasure.
    """
    if not source_interview_refs:
        raise ValueError("source_interview_refs must name the interviews in the cut.")

    last_error = None
    for _ in range(MAX_UPLOAD_ATTEMPTS):
        start = client.reel_upload_start(tenant_public_id, content_type=content_type)
        try:
            _put_file(start["upload_url"], reel_path, start["content_type"])
        except UploadExpired as exc:
            last_error = exc
            continue
        return client.reel_register(
            tenant_public_id,
            s3_key=start["s3_key"], title=title,
            duration_seconds=duration_seconds,
            source_interview_refs=list(source_interview_refs),
            metadata=metadata,
        )

    raise UploadExpired(f"upload kept expiring after {MAX_UPLOAD_ATTEMPTS} attempts: {last_error}")
