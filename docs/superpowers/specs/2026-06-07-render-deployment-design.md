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

### Cloud mode
A `DATA_ROOT` environment variable points to the persistent storage mount. On Render this will be `/data` (a Render Persistent Disk). On a developer's machine it defaults to the project root.

```
DATA_ROOT/
  sessions/
    {session_id}/       ← one dir per upload batch
      video1.mp4
      video1.txt        ← transcript cache (same as local)
      video2.mp4
      video2.txt
      sizzle_reel.mp4   ← generated output saved here
  sizzle_library.json   ← shared across all sessions
```

The session directory is passed through the existing pipeline as the `folder` parameter. **`scan_videos()`, `transcribe_video()`, `extract_clip()`, and the library code all work against it unchanged** — they already operate on any folder path.

A new `storage.py` module exposes:
- `data_root() -> Path` — resolves `DATA_ROOT` env var, defaults to project root
- `new_session() -> tuple[str, Path]` — creates `sessions/{uuid}/` under data root, returns `(session_id, path)`
- `session_path(session_id) -> Path` — returns the session dir path
- `library_path() -> Path` — returns `{DATA_ROOT}/sizzle_library.json`

Both `app.py` and `generator_app.py` import `library_path()` from `storage.py` and use it instead of the hardcoded `LIBRARY_PATH`.

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
- Creates a new session directory via `storage.new_session()`
- Writes uploaded files to disk
- Returns `{"session_id": "…", "folder": "/data/sessions/…"}`

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
    disk:
      name: sizzle-data
      mountPath: /data
      sizeGB: 10
    envVars:
      - key: APP_MODE
        value: cloud
      - key: DATA_ROOT
        value: /data
      - key: ANTHROPIC_API_KEY
        sync: false
      - key: GENERATOR_URL
        fromService:
          name: sizzle-generator
          type: web
          property: hostport

  - type: web
    name: sizzle-generator
    runtime: docker
    dockerfilePath: ./Dockerfile.generator
    disk:
      name: sizzle-data
      mountPath: /data
      sizeGB: 10
    envVars:
      - key: APP_MODE
        value: cloud
      - key: DATA_ROOT
        value: /data
      - key: ANTHROPIC_API_KEY
        sync: false
```

### `.env.example`
```
# Required for all modes
ANTHROPIC_API_KEY=your_key_here

# Cloud mode only
APP_MODE=cloud
DATA_ROOT=/data
GENERATOR_URL=https://sizzle-generator.onrender.com
```

---

## File Changes Summary

| File | Change |
|------|--------|
| `config.py` | **New** — `APP_MODE`, `is_cloud()` |
| `storage.py` | **New** — `data_root()`, `new_session()`, `session_path()`, `library_path()` |
| `app.py` | Platform fix, import `library_path`, inject `window.__CONFIG__`, add `POST /upload` |
| `generator_app.py` | Platform fix, Linux fonts, import `library_path` |
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
