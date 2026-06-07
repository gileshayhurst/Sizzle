# Render Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make both Flask services (app.py port 5000, generator_app.py port 5001) deployable on Render via Docker while keeping `APP_MODE=local` (the default) identical to current behaviour in every way.

**Architecture:** A new `storage.py` module abstracts all file I/O — local backend uses pathlib under `DATA_ROOT`, cloud backend uses boto3/S3. `APP_MODE` and `GENERATOR_URL` are injected into the frontend via `window.__CONFIG__`. In cloud mode, videos are uploaded via a new `POST /upload` endpoint and stored in S3; the generator downloads them from S3 into a temp dir before running ffmpeg. Deployment artifacts (Dockerfiles, `docker-compose.yml`, `render.yaml`) complete the package.

**Tech Stack:** Python 3.11, Flask, boto3 (S3-compatible), Docker, Cloudflare R2 / AWS S3

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `storage.py` | **Create** | Mode detection, S3/local file I/O abstraction |
| `app.py` | **Modify** | WinGet platform guard, config injection into template, `POST /upload`, cloud-aware library |
| `generator_app.py` | **Modify** | WinGet platform guard, Linux fonts, cloud-aware library, S3 download before extraction, S3 upload after stitching |
| `templates/index.html` | **Modify** | Inject `window.__CONFIG__` script block |
| `static/app.js` | **Modify** | Read `GENERATOR_URL`/`APP_MODE` from `window.__CONFIG__`, cloud upload UI |
| `requirements.txt` | **Modify** | Add `boto3` |
| `Dockerfile.app` | **Create** | Docker image for app service |
| `Dockerfile.generator` | **Create** | Docker image for generator service |
| `docker-compose.yml` | **Create** | Local Docker multi-service testing |
| `render.yaml` | **Create** | Render one-click deployment config |
| `.env.example` | **Create** | Documents all env vars |
| `tests/test_storage.py` | **Create** | Unit tests for storage.py local backend |
| `tests/test_upload_endpoint.py` | **Create** | Tests for POST /upload and config injection |
| `tests/test_generator_cloud.py` | **Create** | Tests for generator cloud download/upload flow |

---

## Task 1: `storage.py` — file I/O abstraction

**Files:**
- Create: `storage.py`
- Create: `tests/test_storage.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add boto3 to requirements**

Open `requirements.txt` and add `boto3` as a new line:

```
anthropic
openai-whisper
pytest
flask>=2.0
flask-cors
boto3
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_storage.py`:

```python
"""Tests for storage.py — exercises the local backend only (no real S3)."""
import importlib
import json
import os
import pytest
from pathlib import Path


# ── helpers ────────────────────────────────────────────────────────────────────

def reload_storage(monkeypatch, tmp_path, mode="local"):
    """Reload storage module with fresh env so module-level checks re-run."""
    monkeypatch.setenv("APP_MODE", mode)
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    import storage
    importlib.reload(storage)
    return storage


# ── is_cloud / data_root ───────────────────────────────────────────────────────

def test_is_cloud_false_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_MODE", raising=False)
    s = reload_storage(monkeypatch, tmp_path)
    assert s.is_cloud() is False


def test_is_cloud_true_when_env_set(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path, mode="cloud")
    assert s.is_cloud() is True


# ── new_session_key ────────────────────────────────────────────────────────────

def test_new_session_key_format(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    key = s.new_session_key()
    assert key.startswith("sessions/")
    assert len(key) > len("sessions/") + 8  # has a uuid hex


def test_new_session_key_unique(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    assert s.new_session_key() != s.new_session_key()


# ── library_key ────────────────────────────────────────────────────────────────

def test_library_key(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    assert s.library_key() == "library/sizzle_library.json"


# ── upload_file / download_file (local backend) ───────────────────────────────

def test_upload_creates_file_under_data_root(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    src = tmp_path / "video.mp4"
    src.write_bytes(b"fake video data")

    s.upload_file(str(src), "sessions/abc/video.mp4")

    dest = tmp_path / "sessions" / "abc" / "video.mp4"
    assert dest.exists()
    assert dest.read_bytes() == b"fake video data"


def test_download_retrieves_file(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    # Pre-plant a file in the data root
    (tmp_path / "sessions" / "abc").mkdir(parents=True)
    (tmp_path / "sessions" / "abc" / "clip.mp4").write_bytes(b"clip bytes")

    out = tmp_path / "downloaded.mp4"
    s.download_file("sessions/abc/clip.mp4", str(out))
    assert out.read_bytes() == b"clip bytes"


# ── read_json / write_json (local backend) ────────────────────────────────────

def test_write_then_read_json(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    data = [{"id": "1", "name": "test reel"}]
    s.write_json("library/sizzle_library.json", data)
    assert s.read_json("library/sizzle_library.json") == data


def test_read_json_missing_returns_empty_list(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    assert s.read_json("nonexistent/file.json") == []


def test_read_json_corrupt_returns_empty_list(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    bad_file = tmp_path / "library" / "sizzle_library.json"
    bad_file.parent.mkdir(parents=True)
    bad_file.write_text("not json", encoding="utf-8")
    assert s.read_json("library/sizzle_library.json") == []


# ── list_keys (local backend) ─────────────────────────────────────────────────

def test_list_keys_returns_files_in_prefix(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    prefix_dir = tmp_path / "sessions" / "abc"
    prefix_dir.mkdir(parents=True)
    (prefix_dir / "video.mp4").write_bytes(b"")
    (prefix_dir / "video.txt").write_text("transcript", encoding="utf-8")

    keys = s.list_keys("sessions/abc")
    assert "sessions/abc/video.mp4" in keys
    assert "sessions/abc/video.txt" in keys


def test_list_keys_empty_prefix_returns_empty(monkeypatch, tmp_path):
    s = reload_storage(monkeypatch, tmp_path)
    assert s.list_keys("sessions/nonexistent") == []
```

- [ ] **Step 3: Run tests to verify they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_storage.py -v
```

Expected: `ModuleNotFoundError: No module named 'storage'` or similar — all fail.

- [ ] **Step 4: Create `storage.py`**

```python
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
        _s3().upload_file(local_path, _bucket(), key)
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
    """Return all storage keys whose path starts with prefix."""
    if is_cloud():
        resp = _s3().list_objects_v2(Bucket=_bucket(), Prefix=prefix)
        return [obj["Key"] for obj in resp.get("Contents", [])]
    else:
        root = _data_root() / prefix
        if not root.exists():
            return []
        return [
            str(Path(prefix) / p.name).replace("\\", "/")
            for p in root.iterdir()
            if p.is_file()
        ]


def presigned_url(key: str, expires: int = 3600) -> str:
    """Generate a presigned download URL for a cloud-stored file (cloud mode only)."""
    return _s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": _bucket(), "Key": key},
        ExpiresIn=expires,
    )
```

- [ ] **Step 5: Run tests — all should pass**

```
.\venv\Scripts\python.exe -m pytest tests/test_storage.py -v
```

Expected: all 12 tests pass.

- [ ] **Step 6: Run full test suite to confirm no regressions**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all existing tests still pass.

- [ ] **Step 7: Commit**

```
git add storage.py tests/test_storage.py requirements.txt
git commit -m "feat: add storage.py abstraction (local + S3 backends) and boto3 dep"
```

---

## Task 2: Platform portability fixes

**Files:**
- Modify: `app.py` (lines 18-24 — WinGet PATH patch block)
- Modify: `generator_app.py` (lines 20-25 — WinGet PATH patch block; lines 122-133 — `_find_system_font`)

The WinGet block in both files currently runs unconditionally. On Linux it harmlessly finds nothing, but it's cleaner and correct to guard it. The font finder only has Windows paths — Docker containers use Debian/Ubuntu where DejaVu fonts are installed via apt.

- [ ] **Step 1: Write the failing test for Linux font path detection**

Create `tests/test_platform.py`:

```python
"""Tests for platform portability fixes."""
from unittest.mock import patch
from pathlib import Path


def test_find_system_font_returns_linux_path_when_windows_fonts_absent():
    """When Windows font dirs don't exist but a Linux path does, return the Linux path."""
    import generator_app

    linux_font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    def fake_exists(self):
        # Only the Linux DejaVu path "exists" in this mock
        return str(self) == linux_font

    with patch.object(Path, "exists", fake_exists):
        result = generator_app._find_system_font()

    assert result == linux_font


def test_find_system_font_prefers_windows_font_when_present(tmp_path):
    """Windows fonts take precedence over Linux paths when both exist."""
    import generator_app

    arial = tmp_path / "arial.ttf"
    arial.write_bytes(b"fake font")

    candidates_seen = []

    original_candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibri.ttf"),
        Path("C:/Windows/Fonts/verdana.ttf"),
        Path("C:/Windows/Fonts/times.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ]

    def fake_exists(self):
        return str(self) == str(arial) or str(self) == str(Path("C:/Windows/Fonts/arial.ttf"))

    with patch.object(Path, "exists", fake_exists):
        result = generator_app._find_system_font()

    # Should return the first match — Windows arial
    assert result is not None


def test_find_system_font_returns_none_when_no_fonts():
    """Returns None when no candidate font path exists."""
    import generator_app

    with patch.object(Path, "exists", lambda self: False):
        result = generator_app._find_system_font()

    assert result is None
```

- [ ] **Step 2: Run to verify test 1 fails (Linux path not in candidates yet)**

```
.\venv\Scripts\python.exe -m pytest tests/test_platform.py::test_find_system_font_returns_linux_path_when_windows_fonts_absent -v
```

Expected: FAIL — the Linux font path isn't in `_find_system_font`'s candidates list.

- [ ] **Step 3: Patch WinGet block in `app.py`**

Find lines 18-24 in `app.py` — the WinGet ffmpeg block:

```python
# WinGet installs ffmpeg to a user-local path that isn't on the subprocess PATH.
# Patch it in at startup so all child processes (ffmpeg, whisper) can find it.
if not shutil.which("ffmpeg"):
    _winget_base = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    for _bin in sorted(_winget_base.glob("Gyan.FFmpeg*/*/bin")):
        os.environ["PATH"] = str(_bin) + os.pathsep + os.environ.get("PATH", "")
        break
```

Replace with:

```python
# WinGet installs ffmpeg to a user-local path that isn't on the subprocess PATH.
# Guard to Windows only — Linux containers find ffmpeg via the system PATH (apt install).
import sys as _sys
if not shutil.which("ffmpeg") and _sys.platform == "win32":
    _winget_base = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    for _bin in sorted(_winget_base.glob("Gyan.FFmpeg*/*/bin")):
        os.environ["PATH"] = str(_bin) + os.pathsep + os.environ.get("PATH", "")
        break
```

- [ ] **Step 4: Patch WinGet block in `generator_app.py`**

Find lines 20-25 — the same WinGet block:

```python
# WinGet ffmpeg PATH patch.
if not shutil.which("ffmpeg"):
    _winget_base = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    for _bin in sorted(_winget_base.glob("Gyan.FFmpeg*/*/bin")):
        os.environ["PATH"] = str(_bin) + os.pathsep + os.environ.get("PATH", "")
        break
```

Replace with:

```python
# WinGet ffmpeg PATH patch — Windows only.
import sys as _sys
if not shutil.which("ffmpeg") and _sys.platform == "win32":
    _winget_base = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    for _bin in sorted(_winget_base.glob("Gyan.FFmpeg*/*/bin")):
        os.environ["PATH"] = str(_bin) + os.pathsep + os.environ.get("PATH", "")
        break
```

- [ ] **Step 5: Add Linux font paths to `_find_system_font` in `generator_app.py`**

Find `_find_system_font` (around line 122):

```python
def _find_system_font() -> str | None:
    """Return a path to a TTF font on this system, or None."""
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibri.ttf"),
        Path("C:/Windows/Fonts/verdana.ttf"),
        Path("C:/Windows/Fonts/times.ttf"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None
```

Replace with:

```python
def _find_system_font() -> str | None:
    """Return a path to a TTF font on this system, or None.

    Checks Windows font directories first, then Linux paths installed via
    apt fonts-dejavu-core (present in the project's Dockerfiles).
    """
    candidates = [
        # Windows
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibri.ttf"),
        Path("C:/Windows/Fonts/verdana.ttf"),
        Path("C:/Windows/Fonts/times.ttf"),
        # Linux — Debian/Ubuntu (fonts-dejavu-core apt package)
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None
```

- [ ] **Step 6: Run platform tests — all should pass**

```
.\venv\Scripts\python.exe -m pytest tests/test_platform.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 7: Run full suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```
git add app.py generator_app.py tests/test_platform.py
git commit -m "fix: guard WinGet PATH patch to Windows only; add Linux font paths for Docker"
```

---

## Task 3: Config injection — `window.__CONFIG__` and GENERATOR_URL

**Files:**
- Modify: `app.py` — `index()` route passes config to template
- Modify: `templates/index.html` — inject `window.__CONFIG__` script block
- Modify: `static/app.js` — read `GENERATOR_URL` and `APP_MODE` from `window.__CONFIG__`
- Create: `tests/test_upload_endpoint.py` — config injection test

The frontend currently hardcodes `const GENERATOR_URL = 'http://localhost:5001'`. In cloud mode the generator lives at a different URL. Flask injects the correct URL through the template.

- [ ] **Step 1: Write the failing test for config injection**

Create `tests/test_upload_endpoint.py` — add only the config test for now:

```python
"""Tests for POST /upload and config injection in app.py."""
import pytest
from unittest.mock import patch


@pytest.fixture
def client():
    from app import create_app
    app = create_app(testing=True)
    with app.test_client() as c:
        yield c


def test_index_injects_generator_url(client, monkeypatch):
    """GET / should include window.__CONFIG__ with the configured generator URL."""
    monkeypatch.setenv("GENERATOR_URL", "https://my-generator.onrender.com")
    # Reload app module so it picks up the env var at module scope if needed
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "window.__CONFIG__" in html
    assert "https://my-generator.onrender.com" in html


def test_index_injects_default_generator_url_when_env_absent(client, monkeypatch):
    """When GENERATOR_URL is not set, the default localhost:5001 is injected."""
    monkeypatch.delenv("GENERATOR_URL", raising=False)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "localhost:5001" in html


def test_index_injects_app_mode(client, monkeypatch):
    """GET / should inject the APP_MODE into window.__CONFIG__."""
    monkeypatch.setenv("APP_MODE", "cloud")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "cloud" in resp.data.decode()
```

- [ ] **Step 2: Run to confirm tests fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_upload_endpoint.py::test_index_injects_generator_url -v
```

Expected: FAIL — `window.__CONFIG__` not in the rendered HTML.

- [ ] **Step 3: Update `app.py` index() route to pass config to template**

Find the `index()` route in `create_app` (around line 215):

```python
    @app.get("/")
    def index():
        return render_template("index.html")
```

Replace with:

```python
    @app.get("/")
    def index():
        return render_template(
            "index.html",
            app_mode=os.environ.get("APP_MODE", "local"),
            generator_url=os.environ.get("GENERATOR_URL", "http://localhost:5001"),
        )
```

- [ ] **Step 4: Update `templates/index.html` to inject `window.__CONFIG__`**

Open `templates/index.html`. Find `<head>` and add a script block as the **last element before `</head>`**:

```html
  <script>
    window.__CONFIG__ = {
      mode: "{{ app_mode }}",
      generatorUrl: "{{ generator_url }}"
    };
  </script>
</head>
```

The complete `<head>` section should now end with:
```html
  <link rel="stylesheet" href="/static/style.css">
  <script>
    window.__CONFIG__ = {
      mode: "{{ app_mode }}",
      generatorUrl: "{{ generator_url }}"
    };
  </script>
</head>
```

- [ ] **Step 5: Update `static/app.js` to read from `window.__CONFIG__`**

Find line 1 of `static/app.js`:

```js
const GENERATOR_URL = 'http://localhost:5001';
```

Replace with:

```js
const GENERATOR_URL = (window.__CONFIG__ || {}).generatorUrl || 'http://localhost:5001';
const APP_MODE      = (window.__CONFIG__ || {}).mode || 'local';
```

- [ ] **Step 6: Run config tests — all should pass**

```
.\venv\Scripts\python.exe -m pytest tests/test_upload_endpoint.py -v
```

Expected: all 3 config tests pass.

- [ ] **Step 7: Run full suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```
git add app.py templates/index.html static/app.js tests/test_upload_endpoint.py
git commit -m "feat: inject GENERATOR_URL and APP_MODE into frontend via window.__CONFIG__"
```

---

## Task 4: Cloud upload endpoint + cloud-aware library in `app.py`

**Files:**
- Modify: `app.py` — add `POST /upload`, cloud-aware `_load_library`
- Modify: `static/app.js` — upload zone for cloud mode
- Modify: `templates/index.html` — add upload zone HTML (hidden by default)
- Modify: `tests/test_upload_endpoint.py` — add upload endpoint tests

In cloud mode, users upload videos via the browser instead of selecting a local folder. After upload the session is stored under `DATA_ROOT/sessions/{uuid}/` (locally on the app container) and also pushed to S3 for the generator to access. The `_load_library` in `app.py` routes through `storage.read_json` in cloud mode so the library persists in S3.

- [ ] **Step 1: Write failing tests for upload endpoint**

Add to `tests/test_upload_endpoint.py`:

```python
import io
import os
from unittest.mock import patch, MagicMock


def test_upload_returns_session_info_local_mode(tmp_path, monkeypatch):
    """POST /upload in local mode stores files and returns session metadata."""
    monkeypatch.setenv("APP_MODE", "local")
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    import importlib, storage, app as app_mod
    importlib.reload(storage)
    importlib.reload(app_mod)

    flask_app = app_mod.create_app(testing=True)
    with flask_app.test_client() as c:
        data = {
            "files": (io.BytesIO(b"fake mp4"), "video1.mp4"),
        }
        resp = c.post("/upload", data=data, content_type="multipart/form-data")

    assert resp.status_code == 200
    body = resp.get_json()
    assert "session_key" in body
    assert body["session_key"].startswith("sessions/")
    # File should exist under DATA_ROOT
    session_dir = tmp_path / body["session_key"]
    assert (session_dir / "video1.mp4").exists()


def test_upload_rejects_non_video_files(tmp_path, monkeypatch):
    """POST /upload returns 400 if a non-video file is included."""
    monkeypatch.setenv("APP_MODE", "local")
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    import importlib, storage, app as app_mod
    importlib.reload(storage)
    importlib.reload(app_mod)

    flask_app = app_mod.create_app(testing=True)
    with flask_app.test_client() as c:
        data = {
            "files": (io.BytesIO(b"not a video"), "document.pdf"),
        }
        resp = c.post("/upload", data=data, content_type="multipart/form-data")

    assert resp.status_code == 400


def test_upload_requires_at_least_one_file(tmp_path, monkeypatch):
    """POST /upload with no files returns 400."""
    monkeypatch.setenv("APP_MODE", "local")
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    import importlib, storage, app as app_mod
    importlib.reload(storage)
    importlib.reload(app_mod)

    flask_app = app_mod.create_app(testing=True)
    with flask_app.test_client() as c:
        resp = c.post("/upload", data={}, content_type="multipart/form-data")

    assert resp.status_code == 400
```

- [ ] **Step 2: Run to confirm they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_upload_endpoint.py::test_upload_returns_session_info_local_mode -v
```

Expected: FAIL — `/upload` route does not exist.

- [ ] **Step 3: Update `_load_library` in `app.py` to be cloud-aware**

Find `_load_library` in `app.py` (around line 126):

```python
def _load_library() -> list:
    library_path = Path(__file__).parent / "sizzle_library.json"
    if not library_path.exists():
        return []
    try:
        with library_path.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
```

Replace with:

```python
def _load_library() -> list:
    from storage import is_cloud, read_json, library_key
    if is_cloud():
        return read_json(library_key())
    library_path = Path(__file__).parent / "sizzle_library.json"
    if not library_path.exists():
        return []
    try:
        with library_path.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
```

- [ ] **Step 4: Add `POST /upload` route inside `create_app` in `app.py`**

Add this route inside `create_app`, immediately after the `@app.post("/browse")` route (around line 224):

```python
    _VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

    @app.post("/upload")
    def upload():
        """Cloud-mode endpoint: receive uploaded video files and store as a session."""
        from storage import new_session_key, upload_file, is_cloud
        import tempfile

        files = request.files.getlist("files")
        if not files or all(f.filename == "" for f in files):
            return jsonify({"error": "No files provided"}), 400

        # Validate all files are videos before writing any
        for f in files:
            if Path(f.filename).suffix.lower() not in _VIDEO_EXTENSIONS:
                return jsonify({"error": f"Not a video file: {f.filename}"}), 400

        session_key = new_session_key()

        # Determine local session directory
        if is_cloud():
            # Use a persistent temp dir on the app container (survives the request)
            import tempfile as _tf
            session_dir = Path(_tf.mkdtemp(prefix="sizzle_"))
        else:
            from storage import _data_root
            session_dir = _data_root() / session_key
            session_dir.mkdir(parents=True, exist_ok=True)

        saved_names = []
        for f in files:
            filename = Path(f.filename).name  # strip any path components
            dest = session_dir / filename
            f.save(str(dest))
            if is_cloud():
                upload_file(str(dest), f"{session_key}/{filename}")
            saved_names.append(filename)

        return jsonify({
            "session_key": session_key,
            "folder": str(session_dir),
            "files": saved_names,
        })
```

- [ ] **Step 5: Run upload tests — all should pass**

```
.\venv\Scripts\python.exe -m pytest tests/test_upload_endpoint.py -v
```

Expected: all 6 tests pass (3 config + 3 upload).

- [ ] **Step 6: Add upload zone HTML to `templates/index.html`**

Find the folder-picker form section (`<div class="picker-form">`). Add the upload zone **immediately after** the folder-picker `<div class="picker-form">` closing `</div>` and before the closing `</div>` of `picker-split`:

```html
          <!-- CLOUD MODE: upload zone (shown/hidden by app.js based on APP_MODE) -->
          <div id="cloud-upload-form" class="hidden">
            <h2>Upload videos</h2>
            <p class="subtitle">Select or drag your video files to create a sizzle reel.</p>
            <div id="upload-dropzone" class="upload-dropzone">
              <div class="upload-icon">📹</div>
              <div class="upload-text">Drop video files here</div>
              <div class="upload-subtext">or</div>
              <label class="btn-primary upload-label">
                Browse files
                <input id="file-input" type="file" multiple accept=".mp4,.mov,.avi,.mkv,.webm" style="display:none">
              </label>
            </div>
            <ul id="upload-file-list" class="upload-file-list"></ul>
            <button id="btn-upload" class="btn-primary hidden">Upload &amp; Transcribe</button>
            <div id="upload-error" class="error-msg hidden"></div>
          </div>
```

- [ ] **Step 7: Add upload CSS to `static/style.css`**

Add at the end of `static/style.css`:

```css
/* ── Cloud upload zone ────────────────────────────────────────────────────── */
.upload-dropzone {
  border: 2px dashed #555;
  border-radius: 8px;
  padding: 40px 20px;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
  margin-bottom: 16px;
}
.upload-dropzone.drag-over {
  border-color: #e63946;
  background: rgba(230, 57, 70, 0.05);
}
.upload-icon { font-size: 48px; margin-bottom: 8px; }
.upload-text { font-size: 1.1rem; font-weight: 600; margin-bottom: 4px; }
.upload-subtext { color: #888; font-size: 0.9rem; margin-bottom: 12px; }
.upload-label { cursor: pointer; display: inline-block; }
.upload-file-list { list-style: none; padding: 0; margin: 0 0 16px; }
.upload-file-list li { padding: 4px 0; font-size: 0.9rem; color: #ccc; }
```

- [ ] **Step 8: Add upload zone logic to `static/app.js`**

Add the following block at the end of `static/app.js` (before the final closing if any):

```js
// ─── Cloud upload zone ────────────────────────────────────────────────────────
(function initUploadZone() {
  if (APP_MODE !== 'cloud') {
    // Local mode: show folder picker, hide upload zone
    $('cloud-upload-form').classList.add('hidden');
    return;
  }

  // Cloud mode: hide folder picker controls, show upload zone
  $('btn-browse').closest('.folder-input-row')?.classList.add('hidden');
  document.querySelector('.picker-form h2')?.closest('.picker-form')
    ?.querySelectorAll(':scope > *:not(#cloud-upload-form)')
    .forEach(el => el.classList.add('hidden'));
  $('cloud-upload-form').classList.remove('hidden');

  const dropzone = $('upload-dropzone');
  const fileInput = $('file-input');
  const fileList  = $('upload-file-list');
  const btnUpload = $('btn-upload');
  const uploadErr = $('upload-error');
  let selectedFiles = [];

  function renderFileList() {
    fileList.innerHTML = '';
    selectedFiles.forEach(f => {
      const li = document.createElement('li');
      li.textContent = '📹 ' + f.name;
      fileList.appendChild(li);
    });
    btnUpload.classList.toggle('hidden', selectedFiles.length === 0);
  }

  fileInput.addEventListener('change', () => {
    selectedFiles = Array.from(fileInput.files);
    renderFileList();
  });

  dropzone.addEventListener('dragover', e => {
    e.preventDefault();
    dropzone.classList.add('drag-over');
  });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag-over'));
  dropzone.addEventListener('drop', e => {
    e.preventDefault();
    dropzone.classList.remove('drag-over');
    selectedFiles = Array.from(e.dataTransfer.files);
    renderFileList();
  });

  btnUpload.addEventListener('click', async () => {
    if (selectedFiles.length === 0) return;
    uploadErr.classList.add('hidden');
    btnUpload.disabled = true;
    btnUpload.textContent = 'Uploading…';

    const formData = new FormData();
    selectedFiles.forEach(f => formData.append('files', f));

    try {
      const resp = await fetch('/upload', { method: 'POST', body: formData });
      const data = await resp.json();
      if (!resp.ok) {
        uploadErr.textContent = data.error || 'Upload failed';
        uploadErr.classList.remove('hidden');
        return;
      }
      // After upload, proceed exactly as if a folder was opened
      openFolder(data.folder);
    } catch (err) {
      uploadErr.textContent = 'Network error: ' + err.message;
      uploadErr.classList.remove('hidden');
    } finally {
      btnUpload.disabled = false;
      btnUpload.textContent = 'Upload & Transcribe';
    }
  });
})();
```

- [ ] **Step 9: Run full test suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 10: Commit**

```
git add app.py templates/index.html static/app.js static/style.css tests/test_upload_endpoint.py
git commit -m "feat: add POST /upload endpoint and cloud upload zone UI; cloud-aware _load_library"
```

---

## Task 5: Generator cloud changes — S3 download, S3 upload, cloud library

**Files:**
- Modify: `generator_app.py` — cloud-aware `_load_library`/`_save_library`, S3 download before `_run_generation`, S3 reel upload after stitching, `/video` and `/library-video` redirect in cloud mode
- Create: `tests/test_generator_cloud.py`

In cloud mode the generator: downloads all session videos + transcripts from S3 into a temp dir, runs the existing generation pipeline unchanged against that dir, uploads the finished reel to S3, and returns a presigned URL. Library read/write goes through `storage`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_generator_cloud.py`:

```python
"""Tests for generator_app.py cloud mode: S3 download/upload flow."""
import os
import io
from unittest.mock import patch, MagicMock, call
from pathlib import Path
import pytest


@pytest.fixture
def cloud_client(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY", "key")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:9000")
    import importlib, storage, generator_app
    importlib.reload(storage)
    importlib.reload(generator_app)
    app = generator_app.create_app(testing=True)
    with app.test_client() as c:
        yield c


def test_load_library_uses_storage_in_cloud_mode(monkeypatch, tmp_path):
    """_load_library in generator_app reads from storage.read_json in cloud mode."""
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY", "key")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    import importlib, storage, generator_app
    importlib.reload(storage)
    importlib.reload(generator_app)

    fake_entries = [{"id": "1", "filename": "reel.mp4"}]
    with patch("generator_app.storage.read_json", return_value=fake_entries) as mock_rj:
        result = generator_app._load_library()
    mock_rj.assert_called_once_with(storage.library_key())
    assert result == fake_entries


def test_save_library_uses_storage_in_cloud_mode(monkeypatch):
    """_save_library in generator_app writes via storage.write_json in cloud mode."""
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY", "key")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    import importlib, storage, generator_app
    importlib.reload(storage)
    importlib.reload(generator_app)

    data = [{"id": "2", "filename": "reel2.mp4"}]
    with patch("generator_app.storage.write_json") as mock_wj:
        generator_app._save_library(data)
    mock_wj.assert_called_once_with(storage.library_key(), data)


def test_generate_endpoint_accepts_session_key_in_cloud_mode(cloud_client, tmp_path):
    """POST /generate in cloud mode accepts session_key and downloads files from S3."""
    # Provide a minimal session: one video with a transcript
    session_key = "sessions/test123"
    mp4_bytes = b"fake mp4"
    txt_content = "[0:00] Speaker: Hello world."

    def fake_list_keys(prefix):
        return [f"{session_key}/video.mp4", f"{session_key}/video.txt"]

    def fake_download(key, local_path):
        if key.endswith(".mp4"):
            Path(local_path).write_bytes(mp4_bytes)
        else:
            Path(local_path).write_text(txt_content, encoding="utf-8")

    selections = {"video.mp4": ["[0:00] Speaker: Hello world."]}

    with patch("generator_app.storage.list_keys", side_effect=fake_list_keys), \
         patch("generator_app.storage.download_file", side_effect=fake_download), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.make_title_card"), \
         patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.storage.upload_file"), \
         patch("generator_app.storage.presigned_url", return_value="https://s3/reel.mp4"), \
         patch("generator_app._library_add"):
        resp = cloud_client.post("/generate", json={
            "session_key": session_key,
            "mode": "checkbox",
            "selections": selections,
            "prompt": "test",
            "output_filename": "out.mp4",
        })

    assert resp.status_code == 200
    body = resp.get_json()
    assert "job_id" in body
```

- [ ] **Step 2: Run to confirm tests fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_generator_cloud.py -v
```

Expected: all fail — `_load_library` still uses local path; `/generate` doesn't accept `session_key`.

- [ ] **Step 3: Add `import storage` to `generator_app.py`**

Near the top of `generator_app.py`, after the existing imports, add:

```python
import storage
```

- [ ] **Step 4: Replace `_load_library` and `_save_library` in `generator_app.py` to be cloud-aware**

Find `_load_library` (around line 67):

```python
def _load_library() -> list:
    if not LIBRARY_PATH.exists():
        return []
    try:
        with LIBRARY_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_library(entries: list) -> None:
    with LIBRARY_PATH.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
```

Replace with:

```python
def _load_library() -> list:
    if storage.is_cloud():
        return storage.read_json(storage.library_key())
    if not LIBRARY_PATH.exists():
        return []
    try:
        with LIBRARY_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_library(entries: list) -> None:
    if storage.is_cloud():
        storage.write_json(storage.library_key(), entries)
        return
    with LIBRARY_PATH.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
```

- [ ] **Step 5: Update `_run_generation` to upload reel to S3 in cloud mode**

In `generator_app.py`, find the section after `stitch_clips` is called (around line 380-410). The current code is:

```python
        stitch_clips(clip_paths, output_path)
    except Exception as exc:
        with _jobs_lock:
            job["status"] = "error"
            job["error"] = f"Stitch failed: {exc}"
        return

duration = int(sum(clip_durations) + title_card_count * TITLE_CARD_DURATION)
result = {
    "path": output_path,
    "filename": output_filename,
    "clip_count": len(clip_durations),
    "duration_seconds": duration,
    "segment_starts": segment_starts,
}
```

Replace the `duration` and `result` block with:

```python
        stitch_clips(clip_paths, output_path)
    except Exception as exc:
        with _jobs_lock:
            job["status"] = "error"
            job["error"] = f"Stitch failed: {exc}"
        return

duration = int(sum(clip_durations) + title_card_count * TITLE_CARD_DURATION)

# In cloud mode: upload the finished reel to S3 and add a presigned download URL.
reel_download_url = None
if storage.is_cloud() and session_key:
    reel_s3_key = f"{session_key}/{output_filename}"
    try:
        storage.upload_file(output_path, reel_s3_key)
        reel_download_url = storage.presigned_url(reel_s3_key)
    except Exception as exc:
        _append_log(job_id, f"⚠ Could not upload reel to S3: {exc}")

result = {
    "path": output_path,
    "filename": output_filename,
    "clip_count": len(clip_durations),
    "duration_seconds": duration,
    "segment_starts": segment_starts,
}
if reel_download_url:
    result["download_url"] = reel_download_url
```

- [ ] **Step 6: Add `session_key` parameter to `_run_generation` and the generate endpoint**

`_run_generation` signature currently:

```python
def _run_generation(job_id, folder, mode, selections, prompt, output_filename):
```

Change to:

```python
def _run_generation(job_id, folder, mode, selections, prompt, output_filename, session_key=None):
```

Then find the `generate` endpoint in `create_app` (around line 423):

```python
    @app.post("/generate")
    def generate():
        body = request.get_json() or {}
        folder = body.get("folder", "").strip()
        prompt = body.get("prompt", "").strip()
        mode = body.get("mode", "highlight")
        selections = body.get("selections", {})
        output_filename = body.get("output_filename", "sizzle_reel.mp4").strip()
        output_filename = Path(output_filename).name

        if not folder or not Path(folder).exists():
            return jsonify({"error": "Folder not found"}), 404

        try:
            check_ffmpeg()
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 500
```

Replace with:

```python
    @app.post("/generate")
    def generate():
        import tempfile as _tmpfile
        body = request.get_json() or {}
        prompt = body.get("prompt", "").strip()
        mode = body.get("mode", "highlight")
        selections = body.get("selections", {})
        output_filename = body.get("output_filename", "sizzle_reel.mp4").strip()
        output_filename = Path(output_filename).name
        session_key = body.get("session_key", "").strip() or None

        if storage.is_cloud():
            if not session_key:
                return jsonify({"error": "session_key required in cloud mode"}), 400
            # Download all session files from S3 into a local temp dir for ffmpeg
            tmp_session_dir = _tmpfile.mkdtemp(prefix="sizzle_gen_")
            for key in storage.list_keys(session_key + "/"):
                filename = Path(key).name
                storage.download_file(key, os.path.join(tmp_session_dir, filename))
            folder = tmp_session_dir
        else:
            folder = body.get("folder", "").strip()
            if not folder or not Path(folder).exists():
                return jsonify({"error": "Folder not found"}), 404

        try:
            check_ffmpeg()
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 500
```

Also update the two call sites for `_run_generation` inside the endpoint to pass `session_key`:

Find:
```python
            _run_generation(job_id, folder, mode, selections, prompt, output_filename)
```
Replace with:
```python
            _run_generation(job_id, folder, mode, selections, prompt, output_filename, session_key=session_key)
```

And:
```python
                target=_run_generation,
                args=(job_id, folder, mode, selections, prompt, output_filename),
```
Replace with:
```python
                target=_run_generation,
                args=(job_id, folder, mode, selections, prompt, output_filename, session_key),
```

- [ ] **Step 7: Make `/video/<job_id>` redirect to presigned URL in cloud mode**

Find the `serve_video` route (around line 492):

```python
    @app.get("/video/<job_id>")
    def serve_video(job_id):
        with _jobs_lock:
            job = _jobs.get(job_id)
        if not job or not job.get("result"):
            return jsonify({"error": "not found"}), 404
        path = Path(job["result"]["path"])
        if not path.is_file():
            return jsonify({"error": "file not found on disk"}), 404
        return send_file(str(path), conditional=True)
```

Replace with:

```python
    @app.get("/video/<job_id>")
    def serve_video(job_id):
        from flask import redirect as _redirect
        with _jobs_lock:
            job = _jobs.get(job_id)
        if not job or not job.get("result"):
            return jsonify({"error": "not found"}), 404
        result = job["result"]
        if storage.is_cloud() and result.get("download_url"):
            return _redirect(result["download_url"])
        path = Path(result["path"])
        if not path.is_file():
            return jsonify({"error": "file not found on disk"}), 404
        return send_file(str(path), conditional=True)
```

- [ ] **Step 8: Make `/library-video/<entry_id>` redirect in cloud mode**

Find `serve_library_video` (around line 503):

```python
    @app.get("/library-video/<entry_id>")
    def serve_library_video(entry_id):
        entries = _load_library()
        entry = next((e for e in entries if e["id"] == entry_id), None)
        if not entry:
            return jsonify({"error": "not found"}), 404
        path = Path(entry["path"])
        if not path.is_file():
            return jsonify({"error": "file not found on disk"}), 404
        return send_file(str(path), conditional=True)
```

Replace with:

```python
    @app.get("/library-video/<entry_id>")
    def serve_library_video(entry_id):
        from flask import redirect as _redirect
        entries = _load_library()
        entry = next((e for e in entries if e["id"] == entry_id), None)
        if not entry:
            return jsonify({"error": "not found"}), 404
        if storage.is_cloud() and entry.get("download_url"):
            return _redirect(entry["download_url"])
        path = Path(entry["path"])
        if not path.is_file():
            return jsonify({"error": "file not found on disk"}), 404
        return send_file(str(path), conditional=True)
```

- [ ] **Step 9: Store `download_url` in library entries in cloud mode**

Find the `_library_add` call at end of `_run_generation` (around line 400):

```python
    _library_add({
        "id": str(uuid.uuid4()),
        "filename": output_filename,
        "path": output_path,
        "source_folder": Path(folder).name + "/",
        "prompt": prompt,
        "duration_seconds": duration,
        "clip_count": len(clip_durations),
        "segment_starts": segment_starts,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })
```

Replace with:

```python
    library_entry = {
        "id": str(uuid.uuid4()),
        "filename": output_filename,
        "path": output_path,
        "source_folder": (Path(session_key).name if session_key else Path(folder).name) + "/",
        "prompt": prompt,
        "duration_seconds": duration,
        "clip_count": len(clip_durations),
        "segment_starts": segment_starts,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    if reel_download_url:
        library_entry["download_url"] = reel_download_url
    _library_add(library_entry)
```

- [ ] **Step 10: Run generator cloud tests — all should pass**

```
.\venv\Scripts\python.exe -m pytest tests/test_generator_cloud.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 11: Run full suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 12: Commit**

```
git add generator_app.py tests/test_generator_cloud.py
git commit -m "feat: generator cloud mode — S3 download/upload, cloud-aware library, presigned URLs"
```

---

## Task 6: Deployment artifacts

**Files:**
- Create: `Dockerfile.app`
- Create: `Dockerfile.generator`
- Create: `docker-compose.yml`
- Create: `render.yaml`
- Create: `.env.example`
- Create: `.dockerignore`

No tests for this task — artifacts are verified by building and running the images.

- [ ] **Step 1: Create `.dockerignore`**

Create `.dockerignore` in the project root to keep images small:

```
venv/
__pycache__/
*.pyc
*.pyo
.env
*.mp4
*.mov
*.avi
*.mkv
*.webm
*.txt
sizzle_library.json
recent_folders.json
docs/
tests/
*.md
.git/
```

- [ ] **Step 2: Create `Dockerfile.app`**

```dockerfile
FROM python:3.11-slim

# ffmpeg for Whisper audio extraction; fonts for title card generation fallback
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["python", "-c", "from app import create_app; create_app().run(host='0.0.0.0', port=5000)"]
```

- [ ] **Step 3: Create `Dockerfile.generator`**

```dockerfile
FROM python:3.11-slim

# ffmpeg for clip extraction and stitching; fonts for title cards
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5001

CMD ["python", "-c", "from generator_app import create_app; create_app().run(host='0.0.0.0', port=5001)"]
```

- [ ] **Step 4: Create `docker-compose.yml`**

This file is for **local Docker testing only** (i.e., testing the cloud code path without deploying to Render). Copy your `.env` before running.

```yaml
services:
  app:
    build:
      context: .
      dockerfile: Dockerfile.app
    ports:
      - "5000:5000"
    environment:
      - APP_MODE=cloud
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - GENERATOR_URL=http://generator:5001
      - S3_BUCKET=${S3_BUCKET}
      - S3_ACCESS_KEY=${S3_ACCESS_KEY}
      - S3_SECRET_KEY=${S3_SECRET_KEY}
      - S3_ENDPOINT_URL=${S3_ENDPOINT_URL}
    depends_on:
      - generator

  generator:
    build:
      context: .
      dockerfile: Dockerfile.generator
    ports:
      - "5001:5001"
    environment:
      - APP_MODE=cloud
      - S3_BUCKET=${S3_BUCKET}
      - S3_ACCESS_KEY=${S3_ACCESS_KEY}
      - S3_SECRET_KEY=${S3_SECRET_KEY}
      - S3_ENDPOINT_URL=${S3_ENDPOINT_URL}
```

- [ ] **Step 5: Create `render.yaml`**

Render reads this file automatically when you connect a GitHub repo. `sync: false` means the value is **not** stored in the YAML — it must be set in the Render dashboard (keeps secrets out of git).

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
        sync: false

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

- [ ] **Step 6: Create `.env.example`**

```
# ── Required in all modes ──────────────────────────────────────────────────────
ANTHROPIC_API_KEY=your_anthropic_key_here

# ── Cloud mode only ────────────────────────────────────────────────────────────
APP_MODE=cloud

# URL of the sizzle-generator service (set automatically by render.yaml;
# set manually if running docker-compose locally or on another platform)
GENERATOR_URL=https://sizzle-generator.onrender.com

# S3-compatible storage — use Cloudflare R2 (free tier) or AWS S3
# For R2: get account ID from the Cloudflare dashboard
S3_BUCKET=your-bucket-name
S3_ACCESS_KEY=your-access-key-id
S3_SECRET_KEY=your-secret-access-key
S3_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
# Leave S3_ENDPOINT_URL blank/unset for standard AWS S3
```

- [ ] **Step 7: Run final full test suite to confirm nothing broke**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```
git add Dockerfile.app Dockerfile.generator docker-compose.yml render.yaml .env.example .dockerignore
git commit -m "feat: add Dockerfiles, docker-compose.yml, render.yaml, .env.example for Render deployment"
```

---

## Self-Review Against Spec

| Spec requirement | Covered by |
|---|---|
| `APP_MODE=local` identical to current behaviour | All tasks add cloud branches only; local paths unchanged |
| `APP_MODE=cloud` uses S3 for storage | Tasks 1, 4, 5 |
| `render.yaml` one-click deploy | Task 6 |
| WinGet PATH guard to Windows only | Task 2 |
| Linux font paths for Docker title cards | Task 2 |
| `window.__CONFIG__` config injection | Task 3 |
| `GENERATOR_URL` from env var | Task 3 |
| `POST /upload` endpoint | Task 4 |
| Cloud upload zone UI | Task 4 |
| Cloud-aware `_load_library` in both services | Tasks 4, 5 |
| Generator downloads from S3 before extraction | Task 5 |
| Generator uploads reel to S3, returns presigned URL | Task 5 |
| `/video` and `/library-video` redirect in cloud mode | Task 5 |
| Library entries include `download_url` in cloud mode | Task 5 |
| `Dockerfile.app`, `Dockerfile.generator` | Task 6 |
| `docker-compose.yml` | Task 6 |
| `.env.example` | Task 6 |
| Tests for all new behaviour | Tasks 1, 2, 3, 4, 5 |

All spec requirements covered. No placeholders.
