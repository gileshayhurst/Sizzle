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
        _client = boto3.client(
            "s3",
            endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
            aws_access_key_id=os.environ["S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["S3_SECRET_KEY"],
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


def download(key: str, local_path) -> None:
    client().download_file(bucket(), key, str(local_path))


def read_text(key: str) -> str:
    body = client().get_object(Bucket=bucket(), Key=key)["Body"].read()
    return body.decode("utf-8-sig")


def upload_text(key: str, text: str) -> None:
    client().put_object(
        Bucket=bucket(), Key=key,
        Body=text.encode("utf-8"), ContentType="text/plain; charset=utf-8",
    )
