"""
storage.py — unified file I/O abstraction for local and cloud (S3) backends.

APP_MODE env var controls the backend:
  - "local" (default): all operations use the local filesystem under DATA_ROOT.
  - "cloud": all operations use an S3-compatible object store (boto3).

Both backends expose identical function signatures so callers never branch on mode.
The is_cloud() helper is available for cases where behaviour must differ beyond I/O.
"""
import io
import json
import os
import uuid
from pathlib import Path


def is_cloud() -> bool:
    """Return True when APP_MODE=cloud."""
    return os.environ.get("APP_MODE", "local") == "cloud"


def _data_root() -> Path:
    """Local filesystem root — project dir by default, overridden by DATA_ROOT env var."""
    return Path(os.environ.get("DATA_ROOT", Path(__file__).parent))


# ── S3 client (lazy singleton) ────────────────────────────────────────────────

_s3_client = None


def _s3():
    global _s3_client
    if _s3_client is None:
        import boto3
        _s3_client = boto3.client(
            "s3",
            endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
            aws_access_key_id=os.environ["S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["S3_SECRET_KEY"],
        )
    return _s3_client


def _bucket() -> str:
    return os.environ["S3_BUCKET"]


# ── Public API ────────────────────────────────────────────────────────────────

def new_session_key() -> str:
    """Return a fresh unique S3 prefix / local folder name for an upload session."""
    return f"sessions/{uuid.uuid4().hex}"


def library_key() -> str:
    """S3 key / local relative path for the shared sizzle library JSON."""
    return "library/sizzle_library.json"


def upload_file(local_path: str, key: str) -> None:
    """Copy a local file into storage at the given key."""
    if is_cloud():
        import mimetypes
        content_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
        _s3().upload_file(
            local_path, _bucket(), key,
            ExtraArgs={"ContentType": content_type},
        )
    else:
        dest = _data_root() / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(local_path, dest)


def download_file(key: str, local_path: str) -> None:
    """Retrieve a file from storage and write it to local_path."""
    if is_cloud():
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        _s3().download_file(_bucket(), key, local_path)
    else:
        src = _data_root() / key
        import shutil
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, local_path)


def read_json(key: str) -> list | dict:
    """Read and deserialise a JSON file from storage. Returns [] on missing or corrupt."""
    if is_cloud():
        buf = io.BytesIO()
        try:
            _s3().download_fileobj(_bucket(), key, buf)
        except Exception:
            return []
        buf.seek(0)
        try:
            return json.loads(buf.read().decode("utf-8"))
        except (json.JSONDecodeError, ValueError):
            return []
    else:
        path = _data_root() / key
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []


def write_json(key: str, data: list | dict) -> None:
    """Serialise data to JSON and write to storage at the given key."""
    content = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    if is_cloud():
        _s3().upload_fileobj(io.BytesIO(content), _bucket(), key)
    else:
        path = _data_root() / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def list_keys(prefix: str) -> list[str]:
    """Return all storage keys whose path starts with prefix.

    Both backends return all descendant keys, not just immediate children,
    mirroring S3 list_objects_v2 behaviour.
    """
    if is_cloud():
        resp = _s3().list_objects_v2(Bucket=_bucket(), Prefix=prefix)
        return [obj["Key"] for obj in resp.get("Contents", [])]
    else:
        root = _data_root() / prefix
        if not root.exists():
            return []
        data_root = _data_root()
        return [
            str(p.relative_to(data_root)).replace("\\", "/")
            for p in root.rglob("*")
            if p.is_file()
        ]


def read_file_bytes(key: str) -> bytes:
    """Read a file from storage and return its raw bytes.

    In cloud mode fetches from S3/R2.  In local mode reads from disk.
    Raises on any I/O error — callers should handle exceptions.
    """
    if is_cloud():
        buf = io.BytesIO()
        _s3().download_fileobj(_bucket(), key, buf)
        return buf.getvalue()
    else:
        return (_data_root() / key).read_bytes()


def presigned_url(key: str, expires: int = 3600) -> str:
    """Generate a presigned download URL for a cloud-stored file.

    Raises RuntimeError when called in local mode — presigned URLs require S3.
    """
    if not is_cloud():
        raise RuntimeError("presigned_url is only available in cloud mode (APP_MODE=cloud)")
    return _s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": _bucket(), "Key": key},
        ExpiresIn=expires,
    )


def presigned_put_url(key: str, expires: int = 3600) -> str:
    """Generate a presigned PUT URL so the browser can upload a file directly to R2/S3.

    Raises RuntimeError when called in local mode — presigned URLs require S3.
    """
    if not is_cloud():
        raise RuntimeError("presigned_put_url is only available in cloud mode (APP_MODE=cloud)")
    return _s3().generate_presigned_url(
        "put_object",
        Params={"Bucket": _bucket(), "Key": key},
        ExpiresIn=expires,
    )


def load_library() -> list:
    """Load the sizzle library JSON. Returns [] on missing or corrupt."""
    if is_cloud():
        return read_json(library_key())
    path = _data_root() / "sizzle_library.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
