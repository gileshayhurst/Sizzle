"""Minimal S3/R2 access for the one-off job.

Deliberately does NOT import the app's storage.py: this package is standalone and
its Dockerfile copies only `encoder/`, so an import of app code would not even
build. The overlap is a few boto3 calls and is worth the independence.
"""
import os

_client = None


def client():
    """Lazily build the S3 client so importing this module needs no credentials."""
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config
        _client = boto3.client(
            "s3",
            endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
            aws_access_key_id=os.environ["S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["S3_SECRET_KEY"],
            # R2 only accepts SigV4. The ordinary calls here sign V4 anyway, so
            # this was dormant until presigned_url arrived -- botocore falls back
            # to SigV2 specifically for presigning, and R2 answers 401. storage.py
            # carries the same pin for the same reason; duplicated rather than
            # imported because this package ships without the app (see module
            # docstring).
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )
    return _client


def bucket() -> str:
    return os.environ["S3_BUCKET"]


def list_keys(prefix: str) -> list[str]:
    """Every key under `prefix`, following pagination."""
    keys = []
    token = None
    while True:
        kwargs = {"Bucket": bucket(), "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = client().list_objects_v2(**kwargs)
        keys.extend(item["Key"] for item in page.get("Contents", []))
        if not page.get("IsTruncated"):
            return keys
        token = page.get("NextContinuationToken")


def presigned_url(key: str, expires: int = 21600) -> str:
    """A time-limited GET URL for `key`, for streaming rather than downloading.

    This replaced a download-to-temp-file step. Render one-off jobs get a **2 GB
    /tmp**, and a single interview here reaches 1.4 GB, so a temp file was not
    just wasteful: 8 workers blew the volume instantly, and anything over 2 GB
    would fail even at --workers 1. faster-whisper decodes the source itself via
    PyAV, and PyAV opens an HTTP URL as readily as a path, so handing it this
    keeps peak disk at zero. Measured slightly FASTER too (0.86x), because the
    network read overlaps demux instead of paying a separate decode pass over
    bytes that had to be written and re-read.

    6h is far longer than any single encode needs; the URL never leaves this
    process.
    """
    return client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket(), "Key": key},
        ExpiresIn=expires,
    )


def read_text(key: str) -> str:
    body = client().get_object(Bucket=bucket(), Key=key)["Body"].read()
    return body.decode("utf-8-sig")


def upload_text(key: str, text: str) -> None:
    client().put_object(
        Bucket=bucket(), Key=key,
        Body=text.encode("utf-8"), ContentType="text/plain; charset=utf-8",
    )
