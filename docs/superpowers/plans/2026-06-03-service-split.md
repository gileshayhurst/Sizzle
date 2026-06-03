# Service Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the monolithic `app.py` into two independent Flask services — Analysis Service (`app.py`, port 5000) and Generation Service (`generator_app.py`, port 5001) — with the frontend routing generation calls to the correct port.

**Architecture:** Generation logic (ffmpeg, job tracking, library, title cards) moves to a new `generator_app.py` with CORS enabled. Analysis logic (Whisper, Claude, folder management) stays in `app.py`. Both services share the lower-level modules untouched. The frontend adds a `GENERATOR_URL` constant and prefixes the relevant fetch calls.

**Tech Stack:** Python, Flask, flask-cors, pytest, vanilla JS

---

## File Map

| File | Change |
|------|--------|
| `requirements.txt` | Add `flask-cors` |
| `generator_app.py` | **Create** — all generation logic extracted from app.py |
| `app.py` | **Modify** — remove generation code, keep analysis + folder routes |
| `static/app.js` | **Modify** — add `GENERATOR_URL`, prefix 8 fetch/src references |
| `tests/test_generator_app.py` | **Create** — all generator service tests |
| `tests/test_app.py` | **Modify** — remove tests that moved to test_generator_app.py |

---

## Task 1: Add flask-cors to requirements.txt and install it

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add flask-cors to requirements.txt**

Replace the current contents with:

```
anthropic
openai-whisper
pytest
flask>=2.0
flask-cors
```

- [ ] **Step 2: Install the new dependency**

Run in PowerShell:
```powershell
.\venv\Scripts\python.exe -m pip install flask-cors
```

Expected: `Successfully installed flask-cors-...` (or "already satisfied")

- [ ] **Step 3: Verify import works**

```powershell
.\venv\Scripts\python.exe -c "from flask_cors import CORS; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```powershell
git add requirements.txt
git commit -m "chore: add flask-cors dependency for generation service CORS"
```

---

## Task 2: Create tests/test_generator_app.py (write failing tests first)

**Files:**
- Create: `tests/test_generator_app.py`

These tests will FAIL until Task 3 creates `generator_app.py`. That is intentional — TDD.

- [ ] **Step 1: Create the test file**

Create `tests/test_generator_app.py` with this content:

```python
import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from generator_app import create_app


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    app = create_app(testing=True)
    with app.test_client() as c:
        yield c


# ─── _format_seconds ──────────────────────────────────────────────────────────

def test_format_seconds_zero():
    from generator_app import _format_seconds
    assert _format_seconds(0.0) == "0:00"


def test_format_seconds_minutes_and_seconds():
    from generator_app import _format_seconds
    assert _format_seconds(75.0) == "1:15"


def test_format_seconds_exact_minute():
    from generator_app import _format_seconds
    assert _format_seconds(120.0) == "2:00"


# ─── make_title_card ──────────────────────────────────────────────────────────

def test_make_title_card_generates_one_drawtext_per_line():
    from generator_app import make_title_card
    with patch("generator_app.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card(["NOBU", "from 1:23", "Segment 2 / 5"], 1920, 1080, "/tmp/card.mp4")
    args = mock_run.call_args[0][0]
    vf_idx = args.index("-vf")
    vf_value = args[vf_idx + 1]
    assert vf_value.count("drawtext=") == 3
    assert "NOBU" in vf_value
    assert "from 1:23" in vf_value
    assert "Segment 2 / 5" in vf_value


def test_make_title_card_calls_ffmpeg_with_correct_args():
    from generator_app import make_title_card
    with patch("generator_app.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card(["My Video"], 1920, 1080, "/tmp/card.mp4", duration=5.0)
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    joined = " ".join(cmd)
    assert cmd[0] == "ffmpeg"
    assert "1920x1080" in joined
    assert "My Video" in joined
    assert "/tmp/card.mp4" in joined
    assert "5.0" in joined


def test_make_title_card_escapes_special_characters():
    from generator_app import make_title_card
    apos = chr(0x27)
    curly = chr(0x2019)
    with patch("generator_app.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card(["It" + apos + "s 50% Done: Really"], 1280, 720, "/tmp/card.mp4")
    vf = mock_run.call_args[0][0][mock_run.call_args[0][0].index("-vf") + 1]
    text_val = vf.split("text=" + apos)[1].split(apos)[0]
    assert apos not in text_val
    assert curly in text_val
    assert "%%" in text_val
    assert "Done: Really" in text_val


def test_make_title_card_includes_fontfile_when_font_found():
    from generator_app import make_title_card
    with patch("generator_app.subprocess.run") as mock_run, \
         patch("generator_app._find_system_font", return_value="C:/Windows/Fonts/arial.ttf"):
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card(["Test"], 1920, 1080, "/tmp/card.mp4")
    joined = " ".join(mock_run.call_args[0][0])
    assert "fontfile=" in joined
    assert "arial.ttf" in joined


def test_make_title_card_omits_fontfile_when_none_found():
    from generator_app import make_title_card
    with patch("generator_app.subprocess.run") as mock_run, \
         patch("generator_app._find_system_font", return_value=None):
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card(["Test"], 1920, 1080, "/tmp/card.mp4")
    joined = " ".join(mock_run.call_args[0][0])
    assert "fontfile=" not in joined


def test_make_title_card_wraps_long_title():
    from generator_app import make_title_card
    with patch("generator_app.subprocess.run") as mock_run, \
         patch("generator_app._find_system_font", return_value=None):
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card(["New York", "Japanese restaurant", "Nobu"], 640, 352, "/tmp/card.mp4")
    cmd = mock_run.call_args[0][0]
    vf_arg = cmd[cmd.index("-vf") + 1]
    assert vf_arg.count("drawtext=") == 3


def test_make_title_card_does_not_wrap_short_title():
    from generator_app import make_title_card
    with patch("generator_app.subprocess.run") as mock_run, \
         patch("generator_app._find_system_font", return_value=None):
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card(["Nobu"], 1920, 1080, "/tmp/card.mp4")
    cmd = mock_run.call_args[0][0]
    vf_arg = cmd[cmd.index("-vf") + 1]
    assert vf_arg.count("drawtext=") == 1


# ─── get_video_dimensions ─────────────────────────────────────────────────────

def test_get_video_dimensions_returns_width_height():
    from generator_app import get_video_dimensions
    with patch("generator_app.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="1920,1080\n", returncode=0)
        w, h = get_video_dimensions("/fake/video.mp4")
    assert w == 1920
    assert h == 1080
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "ffprobe"
    assert "/fake/video.mp4" in cmd


def test_get_video_dimensions_falls_back_on_failure():
    from generator_app import get_video_dimensions
    with patch("generator_app.subprocess.run", side_effect=Exception("ffprobe missing")):
        w, h = get_video_dimensions("/fake/video.mp4")
    assert (w, h) == (1920, 1080)


# ─── _group_lines_into_segments ───────────────────────────────────────────────

def test_group_lines_into_segments_single_contiguous_block():
    from generator_app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 5.0},
        {"raw": "b", "seconds": 10.0},
        {"raw": "c", "seconds": 15.0},
        {"raw": "d", "seconds": 20.0},
    ]
    result = _group_lines_into_segments(lines, {"a", "b", "c"})
    assert result == [(5.0, 20.0)]


def test_group_lines_into_segments_two_clusters():
    from generator_app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 5.0},
        {"raw": "b", "seconds": 10.0},
        {"raw": "c", "seconds": 15.0},
        {"raw": "d", "seconds": 20.0},
    ]
    result = _group_lines_into_segments(lines, {"a", "c", "d"})
    assert result == [(5.0, 10.0), (15.0, 30.0)]


def test_group_lines_into_segments_all_selected():
    from generator_app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 5.0},
        {"raw": "b", "seconds": 10.0},
    ]
    result = _group_lines_into_segments(lines, {"a", "b"})
    assert result == [(5.0, 20.0)]


def test_group_lines_into_segments_none_selected():
    from generator_app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 5.0},
        {"raw": "b", "seconds": 10.0},
    ]
    result = _group_lines_into_segments(lines, set())
    assert result == []


# ─── Job / status / cancel routes ─────────────────────────────────────────────

def test_status_unknown_job_returns_404(client):
    resp = client.get("/status/nonexistent-id")
    assert resp.status_code == 404


def test_cancel_job(client):
    from generator_app import _jobs, _jobs_lock
    job_id = "cancel-gen-test-456"
    cancel_event = threading.Event()
    with _jobs_lock:
        _jobs[job_id] = {
            "type": "generation",
            "status": "running",
            "total": 2,
            "done": 0,
            "log": [],
            "result": None,
            "error": None,
            "cancel": cancel_event,
        }
    resp = client.delete(f"/jobs/{job_id}")
    assert resp.status_code == 200
    assert cancel_event.is_set()
    with _jobs_lock:
        assert _jobs[job_id]["status"] == "cancelled"


# ─── /generate route ──────────────────────────────────────────────────────────

def test_generate_returns_job_id(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")

    with patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.make_title_card"), \
         patch("generator_app._library_add"):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {"vid.mp4": ["[0:05] Speaker: Hello."]},
            "prompt": "greetings",
            "output_filename": "out.mp4",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "job_id" in data
        job_id = data["job_id"]

        for _ in range(25):
            status = client.get(f"/status/{job_id}").get_json()["status"]
            if status in ("done", "error", "cancelled"):
                break
            time.sleep(0.2)


def test_generate_accepts_empty_prompt(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")

    with patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.make_title_card"), \
         patch("generator_app._library_add"):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {"vid.mp4": ["[0:05] Speaker: Hello."]},
            "output_filename": "out.mp4",
        })
        assert resp.status_code == 200
        assert "job_id" in resp.get_json()


def test_generate_missing_folder_returns_404(client):
    resp = client.post("/generate", json={
        "folder": "/nonexistent/xyz",
        "selections": {},
        "output_filename": "out.mp4",
    })
    assert resp.status_code == 404


def test_video_endpoint_not_found(client):
    resp = client.get("/video/nonexistent-job-id")
    assert resp.status_code == 404


# ─── Title card integration tests ─────────────────────────────────────────────

def test_title_card_inserted_between_videos(client, tmp_path):
    (tmp_path / "alpha.mp4").touch()
    (tmp_path / "alpha.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")
    (tmp_path / "beta.mp4").touch()
    (tmp_path / "beta.txt").write_text("[0:10] Speaker: World.", encoding="utf-8")

    with patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.make_title_card") as mock_card, \
         patch("generator_app._library_add"):
        mock_card.reset_mock()
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {
                "alpha.mp4": ["[0:05] Speaker: Hello."],
                "beta.mp4": ["[0:10] Speaker: World."],
            },
            "prompt": "",
            "output_filename": "out.mp4",
        })
        assert resp.status_code == 200
        job_id = resp.get_json()["job_id"]

        for _ in range(25):
            status = client.get(f"/status/{job_id}").get_json()["status"]
            if status in ("done", "error", "cancelled"):
                break
            time.sleep(0.2)

        assert status == "done", f"Job ended in unexpected state: {status}"

    assert mock_card.call_count == 2
    calls = [c[0][0] for c in mock_card.call_args_list]
    assert calls[0][0] == "alpha"
    assert calls[0][2] == "Segment 1 / 2"
    assert calls[1][0] == "beta"
    assert calls[1][2] == "Segment 2 / 2"


def test_segment_title_cards_inserted_within_video(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:05] Speaker: First line.\n"
        "[0:15] Speaker: Gap line.\n"
        "[0:25] Speaker: Second cluster.",
        encoding="utf-8",
    )

    with patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.make_title_card") as mock_card, \
         patch("generator_app._library_add"):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {
                "vid.mp4": [
                    "[0:05] Speaker: First line.",
                    "[0:25] Speaker: Second cluster.",
                ],
            },
            "prompt": "",
            "output_filename": "out.mp4",
        })
        assert resp.status_code == 200
        job_id = resp.get_json()["job_id"]

        for _ in range(25):
            status = client.get(f"/status/{job_id}").get_json()["status"]
            if status in ("done", "error", "cancelled"):
                break
            time.sleep(0.2)

        assert status == "done", f"Job ended in unexpected state: {status}"

    assert mock_card.call_count == 2
    calls = [c[0][0] for c in mock_card.call_args_list]
    assert calls[0][0] == "vid"
    assert calls[0][1].startswith("from ")
    assert calls[0][2] == "Segment 1 / 2"
    assert calls[1][0] == "vid"
    assert calls[1][1].startswith("from ")
    assert calls[1][2] == "Segment 2 / 2"


def test_generation_result_includes_segment_starts(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:05] Speaker: Hello.\n[1:10] Speaker: World.", encoding="utf-8"
    )

    from generator_app import _jobs
    with patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.make_title_card"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app._library_add"):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "checkbox",
            "selections": {"vid.mp4": ["[0:05] Speaker: Hello.", "[1:10] Speaker: World."]},
            "prompt": "greetings",
            "output_filename": "out.mp4",
        })
        job_id = resp.get_json()["job_id"]

        for _ in range(50):
            time.sleep(0.1)
            if _jobs.get(job_id, {}).get("status") in ("done", "error"):
                break

    result = _jobs[job_id]["result"]
    assert result is not None
    assert "segment_starts" in result
    assert isinstance(result["segment_starts"], list)
    assert len(result["segment_starts"]) >= 1


# ─── /library routes ──────────────────────────────────────────────────────────

def test_library_starts_empty(client, tmp_path, monkeypatch):
    import generator_app as gen_module
    monkeypatch.setattr(gen_module, "LIBRARY_PATH", tmp_path / "lib.json")
    resp = client.get("/library")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_library_delete_removes_entry(client, tmp_path, monkeypatch):
    import generator_app as gen_module
    lib_path = tmp_path / "lib.json"
    monkeypatch.setattr(gen_module, "LIBRARY_PATH", lib_path)
    lib_path.write_text(json.dumps([{"id": "abc123", "filename": "x.mp4"}]), encoding="utf-8")
    resp = client.delete("/library/abc123")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    remaining = json.loads(lib_path.read_text(encoding="utf-8"))
    assert remaining == []
```

- [ ] **Step 2: Run the tests to confirm they all fail (module not found)**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py -v 2>&1 | Select-Object -First 20
```

Expected: `ModuleNotFoundError: No module named 'generator_app'` — this is correct.

---

## Task 3: Create generator_app.py

**Files:**
- Create: `generator_app.py`

- [ ] **Step 1: Create generator_app.py with the full generation service**

Create `generator_app.py` with this content:

```python
import json
import os
import re as _re
import shutil
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path

# Load ANTHROPIC_API_KEY from .env if not already set.
_env_file = Path(__file__).parent / ".env"
if not os.environ.get("ANTHROPIC_API_KEY") and _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8-sig").splitlines():
        _line = _line.strip()
        if _line.startswith("ANTHROPIC_API_KEY=") and not _line.startswith("#"):
            os.environ["ANTHROPIC_API_KEY"] = _line.split("=", 1)[1].strip().strip('"').strip("'")
            break

# WinGet ffmpeg PATH patch.
if not shutil.which("ffmpeg"):
    _winget_base = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    for _bin in sorted(_winget_base.glob("Gyan.FFmpeg*/*/bin")):
        os.environ["PATH"] = str(_bin) + os.pathsep + os.environ.get("PATH", "")
        break

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

from loader import scan_videos
from timestamp_parser import parse_timestamps
from video_editor import check_ffmpeg, extract_clip, parse_timestamp_to_seconds, stitch_clips

LIBRARY_PATH = Path(__file__).parent / "sizzle_library.json"

_jobs: dict = {}
_jobs_lock = threading.Lock()
_library_lock = threading.Lock()

_LINE_RE = _re.compile(r'^\[(\d+:\d{2})\]\s+\w+:\s*(.*)')


# ─── Job helpers ──────────────────────────────────────────────────────────────

def _new_job(job_type: str, total: int) -> str:
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {
            "type": job_type,
            "status": "running",
            "total": total,
            "done": 0,
            "log": [],
            "result": None,
            "error": None,
            "cancel": threading.Event(),
        }
    return job_id


def _append_log(job_id: str, message: str) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["log"].append(message)


# ─── Library helpers ──────────────────────────────────────────────────────────

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


def _library_add(entry: dict) -> None:
    with _library_lock:
        entries = _load_library()
        entries.insert(0, entry)
        _save_library(entries)


def _filter_generated_reels(video_paths: list) -> list:
    """Remove paths recorded as generated reels. Fails open."""
    try:
        library_paths = {Path(e["path"]).resolve() for e in _load_library()}
    except Exception:
        return video_paths
    return [vp for vp in video_paths if vp.resolve() not in library_paths]


# ─── Transcript parsing (needed by _run_generation) ───────────────────────────

def _parse_transcript_lines(raw_text: str) -> list:
    lines = []
    for raw in raw_text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        m = _LINE_RE.match(raw)
        if not m:
            continue
        ts, text = m.group(1), m.group(2)
        seconds = parse_timestamp_to_seconds(ts)
        lines.append({
            "raw": raw,
            "timestamp": ts,
            "text": text,
            "seconds": seconds,
            "minute_bucket": int(seconds) // 60,
        })
    return lines


def _group_lines_into_segments(
    all_lines: list, selected_raws: set
) -> list:
    """Convert selected transcript lines into (start_sec, end_sec) clip ranges."""
    segments = []
    current = []

    for line in all_lines:
        if line["raw"] in selected_raws:
            current.append(line)
        else:
            if current:
                segments.append((current[0]["seconds"], line["seconds"]))
                current = []

    if current:
        segments.append((current[0]["seconds"], current[-1]["seconds"] + 10.0))

    return segments


# ─── ffmpeg helpers ───────────────────────────────────────────────────────────

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


def _format_seconds(sec: float) -> str:
    """Format seconds as M:SS for display on title cards."""
    m = int(sec) // 60
    s = int(sec) % 60
    return f"{m}:{s:02d}"


def get_video_dimensions(video_path: str) -> tuple:
    """Return (width, height) of the first video stream. Falls back to 1920x1080."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0",
                video_path,
            ],
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
        )
        w, h = result.stdout.strip().split(",")
        return int(w), int(h)
    except Exception as exc:
        print(f"Warning: could not probe dimensions for {video_path}: {exc}",
              file=__import__("sys").stderr)
        return (1920, 1080)


def make_title_card(
    lines: list, width: int, height: int, output_path: str, duration: float = 5.0
) -> None:
    """Generate a black title card with white centred text, encoded H.264/AAC."""
    fontsize = max(24, height // 15)

    def _escape(s: str) -> str:
        return (
            s.replace("\\", "\\\\")
             .replace("'", "’")
             .replace("%", "%%")
        )

    font = _find_system_font()
    if font:
        escaped_font = font.replace("\\", "/").replace(":", "\\:")
        fontfile_arg = f"fontfile='{escaped_font}':"
    else:
        fontfile_arg = ""

    line_height = int(fontsize * 1.2)
    spacing = 8
    n = len(lines)
    total_h = n * line_height + (n - 1) * spacing

    filters = []
    for i, line in enumerate(lines):
        if n == 1:
            y_expr = "(h-text_h)/2"
        else:
            y_off = i * (line_height + spacing)
            y_expr = f"(h-{total_h})/2+{y_off}"
        filters.append(
            f"drawtext={fontfile_arg}text='{_escape(line)}':fontcolor=white"
            f":fontsize={fontsize}:x=(w-text_w)/2:y={y_expr}"
        )

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=black:size={width}x{height}:rate=30",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-vf", ",".join(filters),
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac",
            "-t", str(duration),
            output_path,
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        print(result.stderr.decode(errors="replace"), file=__import__("sys").stderr)
        result.check_returncode()


# ─── Generation worker ────────────────────────────────────────────────────────

def _run_generation(job_id: str, folder: str, mode: str,
                    selections: dict, prompt: str, output_filename: str) -> None:
    """Extract and stitch clips from selected transcript lines."""
    job = _jobs[job_id]
    try:
        video_paths = scan_videos(folder)
    except Exception as exc:
        with _jobs_lock:
            job["status"] = "error"
            job["error"] = str(exc)
        return
    video_paths = _filter_generated_reels(video_paths)

    video_segments = []

    for vp in video_paths:
        if job["cancel"].is_set():
            with _jobs_lock:
                job["status"] = "cancelled"
            return

        selected_raws = selections.get(vp.name, [])
        if not selected_raws:
            continue

        txt_path = vp.with_suffix(".txt")
        if not txt_path.exists():
            _append_log(job_id, f"· {vp.name} — no transcript, skipping")
            continue

        all_lines = _parse_transcript_lines(txt_path.read_text(encoding="utf-8"))
        segs = _group_lines_into_segments(all_lines, set(selected_raws))

        if segs:
            _append_log(job_id, f"✓ {vp.name} — {len(segs)} segment(s)")
            video_segments.append((vp, segs))
        else:
            _append_log(job_id, f"· {vp.name} — selections produced no segments")

        with _jobs_lock:
            job["done"] += 1

    if not video_segments:
        with _jobs_lock:
            job["status"] = "error"
            job["error"] = "No segments found in selections"
        return

    TITLE_CARD_DURATION = 5.0
    total_segs = sum(len(segs) for _, segs in video_segments)

    _append_log(job_id, "· Extracting clips...")
    output_path = str(Path(folder) / output_filename)

    with tempfile.TemporaryDirectory() as tmp_dir:
        clip_paths = []
        clip_durations = []
        segment_starts = []
        cumulative_time = 0.0
        clip_index = 0
        seg_num = 0

        for vp, segs in video_segments:
            if job["cancel"].is_set():
                with _jobs_lock:
                    job["status"] = "cancelled"
                return

            try:
                width, height = get_video_dimensions(str(vp))
            except Exception:
                width, height = 1920, 1080

            for start_sec, end_sec in segs:
                seg_num += 1

                card_path = os.path.join(tmp_dir, f"clip_{clip_index:04d}.mp4")
                card_lines = [
                    vp.stem,
                    f"from {_format_seconds(start_sec)}",
                    f"Segment {seg_num} / {total_segs}",
                ]
                try:
                    make_title_card(card_lines, width, height, card_path)
                    clip_paths.append(card_path)
                    clip_index += 1
                    cumulative_time += TITLE_CARD_DURATION
                except Exception as exc:
                    _append_log(job_id, f"· Could not create title card for {vp.name}: {exc}")

                segment_starts.append(cumulative_time)

                clip_path = os.path.join(tmp_dir, f"clip_{clip_index:04d}{vp.suffix}")
                try:
                    extract_clip(str(vp), start_sec, end_sec, clip_path)
                    clip_paths.append(clip_path)
                    clip_durations.append(end_sec - start_sec)
                    cumulative_time += end_sec - start_sec
                    clip_index += 1
                except Exception as exc:
                    segment_starts.pop()
                    _append_log(
                        job_id,
                        f"✗ {vp.name} [{start_sec:.1f}-{end_sec:.1f}] — extraction failed: {exc}",
                    )

        if not clip_paths:
            with _jobs_lock:
                job["status"] = "error"
                job["error"] = "No clips could be extracted"
            return

        _append_log(job_id, "· Stitching reel...")
        try:
            stitch_clips(clip_paths, output_path)
        except Exception as exc:
            with _jobs_lock:
                job["status"] = "error"
                job["error"] = f"Stitch failed: {exc}"
            return

    duration = int(sum(clip_durations))
    result = {
        "path": output_path,
        "filename": output_filename,
        "clip_count": len(clip_durations),
        "duration_seconds": duration,
        "segment_starts": segment_starts,
    }

    _append_log(job_id, f"✓ Done — saved to {output_filename}")
    with _jobs_lock:
        job["status"] = "done"
        job["result"] = result

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


# ─── Flask app ────────────────────────────────────────────────────────────────

def create_app(testing: bool = False) -> Flask:
    app = Flask(__name__)
    CORS(app)
    app.config["TESTING"] = testing

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

        try:
            video_paths = scan_videos(folder)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 422
        video_paths = _filter_generated_reels(video_paths)

        selected_count = sum(1 for p in video_paths if selections.get(p.name))
        job_id = _new_job("generation", max(selected_count, 1))
        threading.Thread(
            target=_run_generation,
            args=(job_id, folder, mode, selections, prompt, output_filename),
            daemon=True,
        ).start()
        return jsonify({"job_id": job_id})

    @app.get("/status/<job_id>")
    def job_status(job_id):
        with _jobs_lock:
            job = _jobs.get(job_id)
        if job is None:
            return jsonify({"error": "not found"}), 404
        return jsonify({
            "type": job["type"],
            "status": job["status"],
            "total": job["total"],
            "done": job["done"],
            "log": list(job["log"]),
            "result": job["result"],
            "error": job["error"],
        })

    @app.delete("/jobs/<job_id>")
    def cancel_job(job_id):
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job:
                job["cancel"].set()
                _jobs[job_id]["status"] = "cancelled"
        return jsonify({"ok": True})

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

    @app.get("/library")
    def get_library():
        return jsonify(_load_library())

    @app.delete("/library/<entry_id>")
    def delete_library_entry(entry_id):
        with _library_lock:
            entries = _load_library()
            entries = [e for e in entries if e["id"] != entry_id]
            _save_library(entries)
        return jsonify({"ok": True})

    @app.post("/open-folder")
    def open_folder_in_explorer():
        folder = (request.get_json() or {}).get("folder", "").strip()
        if folder and Path(folder).exists():
            import subprocess as _sp
            _sp.Popen(['explorer', folder])
        return jsonify({"ok": True})

    return app


if __name__ == "__main__":
    create_app().run(debug=True, port=5001)
```

- [ ] **Step 2: Run the generator tests — they should now pass**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py -v
```

Expected: All tests pass.

- [ ] **Step 3: Run the existing app tests to confirm nothing broke yet**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_app.py -v
```

Expected: All tests pass (app.py is untouched at this point).

- [ ] **Step 4: Commit**

```powershell
git add generator_app.py tests/test_generator_app.py
git commit -m "feat: create generation service (generator_app.py) with full test coverage"
```

---

## Task 4: Slim down app.py — remove generation code

**Files:**
- Modify: `app.py`

All logic below is being *deleted* from app.py. The functions are already live in generator_app.py.

- [ ] **Step 1: Remove unused imports from app.py**

In `app.py`, change the import block at the top. Remove `tempfile` from the stdlib imports, and slim the `video_editor` import:

Old:
```python
import json
import os
import re as _re
import shutil
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
```

New (remove `subprocess` and `tempfile`):
```python
import json
import os
import re as _re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
```

Old video_editor import line:
```python
from video_editor import check_ffmpeg, extract_clip, parse_timestamp_to_seconds, stitch_clips
```

New:
```python
from video_editor import parse_timestamp_to_seconds
```

- [ ] **Step 2: Remove generation-only module-level state**

Remove these two lines (keep `_jobs_lock`, `_jobs`, `_model_lock`, `_whisper_model`, `_recent_folders_lock`):

```python
_library_lock = threading.Lock()
```

- [ ] **Step 3: Remove generation-only helper functions**

Delete the following function bodies entirely from app.py:
- `_group_lines_into_segments` (lines ~138–164)
- `_save_library` (the write function — keep `_load_library` and `_filter_generated_reels`)
- `_library_add`
- `_find_system_font`
- `_format_seconds`
- `get_video_dimensions`
- `make_title_card`
- `_run_generation`

Keep these in app.py:
- `_get_whisper_model`
- `_new_job`
- `_append_log`
- `_pick_directory`
- `_parse_transcript_lines`
- `_group_by_minute`
- `_filter_generated_reels`
- `_load_library` (read-only; used by `_filter_generated_reels`)
- `_load_recent_folders`
- `_save_recent_folder`

- [ ] **Step 4: Remove generation-only routes from create_app()**

Inside `create_app()`, delete these route functions:
- `generate` (`@app.post("/generate")`)
- `serve_video` (`@app.get("/video/<job_id>")`)
- `serve_library_video` (`@app.get("/library-video/<entry_id>")`)
- `get_library` (`@app.get("/library")`)
- `delete_library_entry` (`@app.delete("/library/<entry_id>")`)
- `open_folder_in_explorer` (`@app.post("/open-folder")`)

Keep these routes:
- `index` (`GET /`)
- `browse` (`POST /browse`)
- `recent_folders` (`GET /recent-folders`)
- `load_folder` (`POST /load-folder`)
- `job_status` (`GET /status/<job_id>`)
- `cancel_job` (`DELETE /jobs/<job_id>`)
- `get_transcripts` (`GET /transcripts`)
- `analyze` (`POST /analyze`)

- [ ] **Step 5: Also remove the LIBRARY_PATH constant from app.py**

Delete:
```python
LIBRARY_PATH = Path(__file__).parent / "sizzle_library.json"
```

And update `_load_library` in app.py to use a local path reference instead:

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

- [ ] **Step 6: Run the app tests to confirm they still pass**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_app.py -v
```

Expected: All tests pass.

- [ ] **Step 7: Commit**

```powershell
git add app.py
git commit -m "refactor: strip generation code from app.py (moved to generator_app.py)"
```

---

## Task 5: Update tests/test_app.py — remove tests that moved

**Files:**
- Modify: `tests/test_app.py`

- [ ] **Step 1: Remove tests that have moved to test_generator_app.py**

Delete the following test functions from `tests/test_app.py`:
- `test_format_seconds_zero`
- `test_format_seconds_minutes_and_seconds`
- `test_format_seconds_exact_minute`
- `test_make_title_card_generates_one_drawtext_per_line`
- `test_make_title_card_calls_ffmpeg_with_correct_args`
- `test_make_title_card_escapes_special_characters`
- `test_make_title_card_includes_fontfile_when_font_found`
- `test_make_title_card_omits_fontfile_when_none_found`
- `test_make_title_card_wraps_long_title`
- `test_make_title_card_does_not_wrap_short_title`
- `test_get_video_dimensions_returns_width_height`
- `test_get_video_dimensions_falls_back_on_failure`
- `test_group_lines_into_segments_single_contiguous_block`
- `test_group_lines_into_segments_two_clusters`
- `test_group_lines_into_segments_all_selected`
- `test_group_lines_into_segments_none_selected`
- `test_cancel_job` (tests generation job cancel — now in test_generator_app.py)
- `test_generate_returns_job_id`
- `test_generate_accepts_empty_prompt`
- `test_library_starts_empty`
- `test_library_delete_removes_entry`
- `test_video_endpoint_not_found`
- `test_title_card_inserted_between_videos`
- `test_segment_title_cards_inserted_within_video`
- `test_generation_result_includes_segment_starts`

- [ ] **Step 2: Run the full test suite**

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: All remaining tests in `test_app.py` pass, all tests in `test_generator_app.py` pass. Zero failures.

- [ ] **Step 3: Commit**

```powershell
git add tests/test_app.py
git commit -m "refactor: remove generation tests from test_app.py (now in test_generator_app.py)"
```

---

## Task 6: Update static/app.js — add GENERATOR_URL and prefix fetch calls

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Add GENERATOR_URL constant at the top of app.js**

Insert this line as the very first line of `static/app.js` (before `// ─── State`):

```js
const GENERATOR_URL = 'http://localhost:5001';
```

- [ ] **Step 2: Update the /generate fetch call (line ~592)**

Old:
```js
  const resp = await fetch('/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
```

New:
```js
  const resp = await fetch(GENERATOR_URL + '/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
```

- [ ] **Step 3: Update the generation status poll fetch (in pollGeneration)**

Old:
```js
    const resp = await fetch(`/status/${jobId}`);
    const job = await resp.json();

    const pct = job.total > 0 ? Math.round((job.done / job.total) * 100) : 0;
    $('gen-bar').style.width = Math.max(pct, 5) + '%';
```

New:
```js
    const resp = await fetch(`${GENERATOR_URL}/status/${jobId}`);
    const job = await resp.json();

    const pct = job.total > 0 ? Math.round((job.done / job.total) * 100) : 0;
    $('gen-bar').style.width = Math.max(pct, 5) + '%';
```

- [ ] **Step 4: Update the generation cancel fetch (in pollGeneration)**

Old:
```js
    await fetch(`/jobs/${jobId}`, { method: 'DELETE' });
```

New:
```js
    await fetch(`${GENERATOR_URL}/jobs/${jobId}`, { method: 'DELETE' });
```

- [ ] **Step 5: Update the result video src (in showResult)**

Old:
```js
  const src = `/video/${state.resultJobId}`;
```

New:
```js
  const src = `${GENERATOR_URL}/video/${state.resultJobId}`;
```

- [ ] **Step 6: Update the /open-folder fetch in btn-open-folder handler**

Old:
```js
  await fetch('/open-folder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folder: state.folder }),
  });
```

New:
```js
  await fetch(GENERATOR_URL + '/open-folder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folder: state.folder }),
  });
```

- [ ] **Step 7: Update the /library fetch (in loadLibrary)**

Old:
```js
  const resp = await fetch('/library');
```

New:
```js
  const resp = await fetch(GENERATOR_URL + '/library');
```

- [ ] **Step 8: Update the /open-folder fetch in library card "Show" button**

Old:
```js
      await fetch('/open-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder }),
      });
```

New:
```js
      await fetch(GENERATOR_URL + '/open-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder }),
      });
```

- [ ] **Step 9: Update the /library delete fetch**

Old:
```js
      await fetch(`/library/${entry.id}`, { method: 'DELETE' });
```

New:
```js
      await fetch(`${GENERATOR_URL}/library/${entry.id}`, { method: 'DELETE' });
```

- [ ] **Step 10: Update the library video src**

Old:
```js
  $('library-source').src = `/library-video/${entry.id}`;
```

New:
```js
  $('library-source').src = `${GENERATOR_URL}/library-video/${entry.id}`;
```

- [ ] **Step 11: Run the full test suite one final time**

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 12: Commit**

```powershell
git add static/app.js
git commit -m "feat: route generation/library fetch calls to GENERATOR_URL (port 5001)"
```

---

## Running the Split App Locally

After implementation is complete, open two PowerShell terminals:

```powershell
# Terminal 1 — Analysis Service (port 5000)
.\venv\Scripts\python.exe -c "from app import create_app; create_app().run(port=5000, debug=True)"

# Terminal 2 — Generation Service (port 5001)
.\venv\Scripts\python.exe -c "from generator_app import create_app; create_app().run(port=5001, debug=True)"
```

Open `http://localhost:5000` as normal.
