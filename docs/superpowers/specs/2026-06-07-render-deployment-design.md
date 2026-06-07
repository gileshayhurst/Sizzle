# Render Deployment Design

**Date:** 2026-06-07
**Scope:** Make both Flask services deployable to Render while keeping local mode identical to current behaviour.

---

## Goals

1. `APP_MODE=local` (default) — everything works exactly as it does today. No behaviour changes, no UX changes.
2. `APP_MODE=cloud` — file upload replaces folder picker; videos/transcripts/reels stored on a shared persistent disk; both services run as Docker containers on Render.
3. A single `render.yaml` lets the company connect their GitHub repo and deploy with one click.

---

## Mode Switching

A single environment variable `APP_MODE` controls which code path is active:

- **`local`** (default when env var is absent) — all existing code paths run unchanged.
- **`cloud`** — cloud code paths active: file upload UI, session-based storage under `DATA_ROOT`.

`APP_MODE` is read at module import time in a new `config.py`:

```python
import os
APP_MODE = os.environ.get("APP_MODE", "local")
def is_cloud() -> bool:
    return APP_MODE == "cloud"
```

---

## Storage

### Local mode
No change. Files live wherever they do today — wherever the user's OS puts the project folder and wherever videos are stored.

### Cloud mode — Why S3, not Render Persistent Disk
Render Persistent Disk can only be attached to **one** service. Since the app service (port 5000) and generator service (port 5001) are separate Render deployments, they cannot share a disk. S3-compatible object storage (AWS S3, Cloudflare R2, MinIO) is the correct solution: both services read and write to the same bucket independently, with no shared filesystem required.

Cloudflare R2 is recommended — it is S3-compatible and free up to 10 GB/month with no egress fees.

### S3 Storage layout (cloud mode)
```
bucket/
  sessions/
    {session_id}/
      video1.mp4          ← uploaded by app service
      video1.txt          ← transcript (written by app, read by generator)
      video2.mp4
      video2.txt
  library/
    sizzle_library.json   ← shared library, written by generator
```

### How the generator uses S3
The generator service cannot run ffmpeg directly on S3 objects. When a generation job starts, it:
1. Downloads each source video from S3 into a `tempfile.TemporaryDirectory` (same temp dir already used for clips)
2. Runs ffmpeg extraction and stitching against the local temp paths (unchanged)
3. Uploads the finished reel back to S3 under `sessions/{session_id}/`
4. Returns a presigned download URL to the frontend

The transcript `.txt` files are already downloaded with the videos in step 1 (they live in the same S3 prefix). The existing `_parse_transcript_lines` logic is unchanged.

### `storage.py` module
Exposes a unified interface that both services import:

```python
# Local backend uses pathlib; cloud backend uses boto3
def is_cloud() -> bool: ...
def upload_file(local_path: str, key: str) -> None: ...
def download_file(key: str, local_path: str) -> None: ...
def read_json(key: str) -> list | dict: ...
def write_json(key: str, data: list | dict) -> None: ...
def list_keys(prefix: str) -> list[str]: ...
def presigned_url(key: str, expires: int = 3600) -> str: ...
def new_session_key() -> str: ...          # returns "sessions/{uuid}"
def library_key() -> str: ...              # returns "library/sizzle_library.json"
```

Local backend: `upload_file` / `download_file` / `list_keys` operate on the local filesystem under `DATA_ROOT` (env var, defaults to project root). This allows local testing of cloud code paths without S3.

Cloud backend: All operations go through `boto3.client("s3")` configured via:
- `S3_BUCKET` — bucket name
- `S3_ACCESS_KEY` / `S3_SECRET_KEY` — credentials
- `S3_ENDPOINT_URL` — optional (set to R2 endpoint for Cloudflare R2)

### Library path
Both `app.py` and `generator_app.py` currently hardcode `sizzle_library.json` relative to `__file__`. Replace with `storage.read_json(storage.library_key())` / `storage.write_json(storage.library_key(), data)` in cloud mode, or the existing file path in local mode.

---

## Configuration Injection

The frontend (`app.js`) currently hardcodes `const GENERATOR_URL = 'http://localhost:5001'`. In cloud mode the generator service has a different Render URL.

`app.py` injects configuration into the HTML template as a `window.__CONFIG__` object:

```html
<!-- injected by Flask into index.html -->
<script>
  window.__CONFIG__ = {
    mode: "{{ app_mode }}",
    generatorUrl: "{{ generator_url }}"
  };
</script>
```

`app.js` replaces the hardcoded constant with:
```js
const GENERATOR_URL = (window.__CONFIG__ || {}).generatorUrl || 'http://localhost:5001';
const APP_MODE      = (window.__CONFIG__ || {}).mode || 'local';
```

Flask reads `GENERATOR_URL` env var (defaults to `http://localhost:5001`) and passes it to the template.

---

## File Input — Cloud Upload Flow

In cloud mode the folder-picker screen is replaced by a drag-and-drop file upload zone. The rest of the pipeline (transcribing → workspace → generation → result) is identical.

### New endpoint: `POST /upload`
- Accepts `multipart/form-data` with one or more video files
- Generates a session key via `storage.new_session_key()` (e.g. `"sessions/abc123"`)
- Writes each file to a local temp directory, then uploads to S3 (cloud) or keeps in local `DATA_ROOT/sessions/{id}/` (local)
- Returns `{"session_id": "…", "session_key": "sessions/abc123"}`

### How the session_key flows through the pipeline
In cloud mode, `session_key` replaces the `folder` path throughout. The app service downloads transcript files from S3 as needed (lazy, cached in a per-request temp dir). The generator service receives the `session_key` in the `/generate` POST body and downloads all required videos from S3 into its own temp dir before extraction.

### Frontend change
On startup, the frontend reads `APP_MODE` from `window.__CONFIG__`. When `APP_MODE === 'cloud'`:
- The folder-picker screen shows an upload zone (`<input type="file" multiple>` + drag-and-drop) instead of the path input + Browse button
- On successful upload, calls the existing `openFolder(folder)` with the returned `folder` path
- Everything from that point on is identical to local mode

When `APP_MODE === 'local'`:
- Folder picker screen is unchanged

---

## Platform Portability Fixes

Two Windows-specific issues that break Linux containers:

### 1. WinGet PATH patch
Current code unconditionally tries to add WinGet's ffmpeg path. On Linux this silently does nothing harmful, but it's noise. Wrap it:

```python
import sys
if not shutil.which("ffmpeg") and sys.platform == "win32":
    _winget_base = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    ...
```

Applied to both `app.py` and `generator_app.py`.

### 2. `_find_system_font()` in `generator_app.py`
Currently only looks for Windows font paths. Add Linux fallbacks so title cards render correctly in Docker:

```python
candidates = [
    # Windows
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/calibri.ttf"),
    # Linux (Debian/Ubuntu — installed via apt dejavu-fonts-ttf)
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
]
```

The Dockerfiles install `fonts-dejavu-core` via apt so this path is guaranteed to exist.

---

## Library Path Unification

Both `app.py` and `generator_app.py` currently hardcode `sizzle_library.json` relative to `__file__`. In cloud mode the library must live on the persistent disk (otherwise it resets on every redeploy).

`storage.library_path()` returns:
- Local: `Path(__file__).parent / "sizzle_library.json"` (identical to today)
- Cloud: `Path(DATA_ROOT) / "sizzle_library.json"`

Both services import this and use it instead of their own hardcoded paths.

---

## Deployment Artifacts

### `Dockerfile.app`
```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["python", "-c", "from app import create_app; create_app().run(host='0.0.0.0', port=5000)"]
```

### `Dockerfile.generator`
```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5001
CMD ["python", "-c", "from generator_app import create_app; create_app().run(host='0.0.0.0', port=5001)"]
```

### `docker-compose.yml` (local Docker testing)
```yaml
services:
  app:
    build:
      context: .
      dockerfile: Dockerfile.app
    ports: ["5000:5000"]
    environment:
      - APP_MODE=cloud
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - GENERATOR_URL=http://generator:5001
      - DATA_ROOT=/data
    volumes:
      - sizzle_data:/data
    depends_on: [generator]

  generator:
    build:
      context: .
      dockerfile: Dockerfile.generator
    ports: ["5001:5001"]
    environment:
      - APP_MODE=cloud
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - DATA_ROOT=/data
    volumes:
      - sizzle_data:/data

volumes:
  sizzle_data:
```

### `render.yaml`
```yaml
services:
  - type: web
    name: sizzle-app
    runtime: docker
    dockerfilePath: ./Dockerfile.app
    envVars:
      - key: APP_MODE
        value: cloud
      - key: ANTHROPIC_API_KEY
        sync: false
      - key: GENERATOR_URL
        fromService:
          name: sizzle-generator
          type: web
          property: hostport
      - key: S3_BUCKET
        sync: false
      - key: S3_ACCESS_KEY
        sync: false
      - key: S3_SECRET_KEY
        sync: false
      - key: S3_ENDPOINT_URL
        sync: false          # set to R2 endpoint for Cloudflare R2

  - type: web
    name: sizzle-generator
    runtime: docker
    dockerfilePath: ./Dockerfile.generator
    envVars:
      - key: APP_MODE
        value: cloud
      - key: S3_BUCKET
        sync: false
      - key: S3_ACCESS_KEY
        sync: false
      - key: S3_SECRET_KEY
        sync: false
      - key: S3_ENDPOINT_URL
        sync: false
```

### `.env.example`
```
# Required for all modes
ANTHROPIC_API_KEY=your_key_here

# Cloud mode only
APP_MODE=cloud
GENERATOR_URL=https://sizzle-generator.onrender.com

# S3-compatible storage (cloud mode)
# Use Cloudflare R2 endpoint for R2, omit for AWS S3
S3_BUCKET=your-bucket-name
S3_ACCESS_KEY=your-access-key
S3_SECRET_KEY=your-secret-key
S3_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
```

---

## File Changes Summary

| File | Change |
|------|--------|
| `storage.py` | **New** — `is_cloud()`, `upload_file()`, `download_file()`, `read_json()`, `write_json()`, `list_keys()`, `presigned_url()`, `new_session_key()`, `library_key()` |
| `app.py` | Platform fix, inject `window.__CONFIG__`, add `POST /upload`, cloud library read/write |
| `generator_app.py` | Platform fix, Linux fonts, cloud library read/write, S3 video download before extraction, S3 reel upload after stitching |
| `templates/index.html` | Inject `window.__CONFIG__` block |
| `static/app.js` | Read `GENERATOR_URL`/`APP_MODE` from `window.__CONFIG__`; upload zone for cloud mode |
| `Dockerfile.app` | **New** |
| `Dockerfile.generator` | **New** |
| `docker-compose.yml` | **New** |
| `render.yaml` | **New** |
| `.env.example` | **New** |
| `tests/test_storage.py` | **New** — unit tests for storage module |
| `tests/test_config_endpoint.py` | **New** — tests for `/config` and `/upload` |

---

## What Does Not Change

- All Flask route logic in `app.py` and `generator_app.py`
- `loader.py`, `transcriber.py`, `video_editor.py`, `claude_client.py`, `shared.py`, `timestamp_parser.py`
- All existing tests
- `run.ps1` for local non-Docker startup
- The entire transcript/analysis/workspace/generation/result UX flow
- Clip extraction, stitching, title card generation
