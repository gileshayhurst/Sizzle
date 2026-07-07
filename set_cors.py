"""
One-time script to set Cloudflare R2 CORS policy via the S3 API.
Reads credentials from .env (same file used by the app).

Run:  .\venv\Scripts\python.exe set_cors.py
"""
import os
import boto3
from pathlib import Path

# ── Load credentials from .env ───────────────────────────────────────────────
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip("\"'"))

# ── Connect to R2 ────────────────────────────────────────────────────────────
s3 = boto3.client(
    "s3",
    endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
    aws_access_key_id=os.environ["S3_ACCESS_KEY"],
    aws_secret_access_key=os.environ["S3_SECRET_KEY"],
)
bucket = os.environ["S3_BUCKET"]

# ── Set CORS ─────────────────────────────────────────────────────────────────
# NOTE: put_bucket_cors REPLACES the bucket's entire CORS policy — every rule
# the bucket needs must be listed here, or running this script will break
# whatever the omitted rule was serving.
s3.put_bucket_cors(
    Bucket=bucket,
    CORSConfiguration={
        "CORSRules": [
            # Rule 1 — playback (verbatim the rule that fixed Chrome ORB for
            # presigned GET redirects; do not narrow it without re-testing
            # library playback).
            {
                "AllowedOrigins": ["*"],
                "AllowedMethods": ["GET", "HEAD"],
                "MaxAgeSeconds": 3600,
            },
            # Rule 2 — direct browser→R2 presigned PUT uploads. PUT is never a
            # "simple" CORS request, so the browser preflights it; the fetch in
            # app.js doUpload() sends Content-Type (from File.type), which must
            # be allowed here or the preflight fails with "Failed to fetch".
            {
                "AllowedOrigins": ["https://sizzle-app-q1p9.onrender.com"],
                "AllowedMethods": ["PUT"],
                "AllowedHeaders": ["content-type"],
                "MaxAgeSeconds": 3600,
            },
        ]
    },
)
print(f"✅  CORS policy set on bucket '{bucket}'")

# ── Verify ───────────────────────────────────────────────────────────────────
resp = s3.get_bucket_cors(Bucket=bucket)
print("Current rules:")
for rule in resp["CORSRules"]:
    print(" ", rule)
