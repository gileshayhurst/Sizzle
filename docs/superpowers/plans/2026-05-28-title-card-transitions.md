# Title Card Transitions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Insert a 5-second black title card showing the source video name (no extension, white text) between clips from different source videos in the generated reel.

**Architecture:** Two new helper functions (`get_video_dimensions`, `make_title_card`) are added to `app.py`. The generation loop in `_run_generation` tracks the previous source video and inserts a title card between consecutive source videos. Title card clips are added to `clip_paths` but NOT to `clip_durations`, so `duration_seconds` reflects content only. `video_editor.py` is not modified.

**Tech Stack:** Python, ffmpeg (`lavfi` colour source, `drawtext` filter), ffprobe, pytest

---

## File Structure

- **Modify:** `app.py` — add `import subprocess`, add `get_video_dimensions` and `make_title_card` helpers after `_library_add`, update `_run_generation`
- **Modify:** `tests/test_app.py` — add tests for both helpers and the integration

---

### Task 1: `get_video_dimensions` helper

**Files:**
- Modify: `app.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Add `import subprocess` to `app.py`**

Open `app.py`. The current imports block starts at line 1. Add `import subprocess` after `import shutil`:

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

- [ ] **Step 2: Write the failing tests**

Add these two tests to `tests/test_app.py` (before the final blank line):

```python
def test_get_video_dimensions_returns_width_height():
    from app import get_video_dimensions
    with patch("app.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=b"1920,1080\n", returncode=0)
        w, h = get_video_dimensions("/fake/video.mp4")
    assert w == 1920
    assert h == 1080
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "ffprobe"
    assert "/fake/video.mp4" in cmd


def test_get_video_dimensions_falls_back_on_failure():
    from app import get_video_dimensions
    with patch("app.subprocess.run", side_effect=Exception("ffprobe missing")):
        w, h = get_video_dimensions("/fake/video.mp4")
    assert (w, h) == (1920, 1080)
```

- [ ] **Step 3: Run tests to confirm they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_get_video_dimensions_returns_width_height tests/test_app.py::test_get_video_dimensions_falls_back_on_failure -v
```

Expected: `FAILED` — `get_video_dimensions` does not exist yet.

- [ ] **Step 4: Implement `get_video_dimensions` in `app.py`**

Add this function after `_library_add` and before `_run_generation`:

```python
def get_video_dimensions(video_path: str) -> tuple[int, int]:
    """Return (width, height) of the first video stream. Falls back to 1920×1080."""
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
        )
        w, h = result.stdout.decode().strip().split(",")
        return int(w), int(h)
    except Exception:
        return (1920, 1080)
```

- [ ] **Step 5: Run tests to confirm they pass**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_get_video_dimensions_returns_width_height tests/test_app.py::test_get_video_dimensions_falls_back_on_failure -v
```

Expected: `PASSED`

- [ ] **Step 6: Run full suite — confirm no regressions**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: All existing tests + 2 new tests pass.

- [ ] **Step 7: Commit**

```
git add app.py tests/test_app.py
git commit -m "feat: add get_video_dimensions helper using ffprobe"
```

---

### Task 2: `make_title_card` helper

**Files:**
- Modify: `app.py` (add function immediately after `get_video_dimensions`)
- Test: `tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

Add these two tests to `tests/test_app.py`:

```python
def test_make_title_card_calls_ffmpeg_with_correct_args():
    from app import make_title_card
    with patch("app.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card("My Video", 1920, 1080, "/tmp/card.mp4", duration=5.0)
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    joined = " ".join(cmd)
    assert cmd[0] == "ffmpeg"
    assert "1920x1080" in joined
    assert "My Video" in joined
    assert "/tmp/card.mp4" in joined
    assert "5.0" in joined


def test_make_title_card_escapes_special_characters():
    from app import make_title_card
    with patch("app.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card("It's 50% Done: Really", 1280, 720, "/tmp/card.mp4")
    joined = " ".join(mock_run.call_args[0][0])
    assert "\\'" in joined        # apostrophe escaped
    assert "%%" in joined          # percent escaped
    assert "\\:" in joined         # colon escaped
```

- [ ] **Step 2: Run tests to confirm they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_make_title_card_calls_ffmpeg_with_correct_args tests/test_app.py::test_make_title_card_escapes_special_characters -v
```

Expected: `FAILED` — `make_title_card` does not exist yet.

- [ ] **Step 3: Implement `make_title_card` in `app.py`**

Add this function immediately after `get_video_dimensions`, before `_run_generation`:

```python
def make_title_card(
    name: str, width: int, height: int, output_path: str, duration: float = 5.0
) -> None:
    """Generate a black title card with white centred text, encoded H.264/AAC."""
    # Escape special characters for ffmpeg drawtext filter
    safe = (
        name
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace("%", "%%")
    )
    fontsize = max(24, height // 15)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=black:size={width}x{height}:rate=30",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-vf", (
                f"drawtext=text='{safe}':fontcolor=white:fontsize={fontsize}"
                f":x=(w-text_w)/2:y=(h-text_h)/2"
            ),
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac",
            "-t", str(duration),
            output_path,
        ],
        check=True,
        capture_output=True,
    )
```

- [ ] **Step 4: Run tests to confirm they pass**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_make_title_card_calls_ffmpeg_with_correct_args tests/test_app.py::test_make_title_card_escapes_special_characters -v
```

Expected: `PASSED`

- [ ] **Step 5: Run full suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```
git add app.py tests/test_app.py
git commit -m "feat: add make_title_card using ffmpeg lavfi and drawtext"
```

---

### Task 3: Integrate title cards into `_run_generation`

**Files:**
- Modify: `app.py` (`_run_generation`, the `with tempfile.TemporaryDirectory()` block)
- Test: `tests/test_app.py`

**Context:** `_run_generation` loops over `video_segments` (a list of `(Path, [segments])` tuples, already filtered to only videos Claude found segments in). The clip extraction block starts with `with tempfile.TemporaryDirectory() as tmp_dir:`. We add a `prev_vp` tracker and insert a title card before each video except the first.

- [ ] **Step 1: Write the failing integration test**

Add to `tests/test_app.py`:

```python
def test_title_card_inserted_between_videos(client, tmp_path):
    """make_title_card is called once between two source videos."""
    import time

    (tmp_path / "alpha.mp4").touch()
    (tmp_path / "alpha.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")
    (tmp_path / "beta.mp4").touch()
    (tmp_path / "beta.txt").write_text("[0:10] Speaker: World.", encoding="utf-8")

    with patch("app.query_claude", return_value="0:05-0:10"), \
         patch("app.extract_clip"), \
         patch("app.stitch_clips"), \
         patch("app.check_ffmpeg"), \
         patch("app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("app.make_title_card") as mock_card:

        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "all",
            "selections": {},
            "prompt": "highlights",
            "output_filename": "out.mp4",
        })
        assert resp.status_code == 200
        job_id = resp.get_json()["job_id"]

        # Poll until the background thread finishes (max 5 s)
        for _ in range(25):
            status = client.get(f"/status/{job_id}").get_json()["status"]
            if status in ("done", "error", "cancelled"):
                break
            time.sleep(0.2)

    # Exactly one title card — between alpha and beta
    assert mock_card.call_count == 1
    # First positional arg is the video name (stem only, no extension)
    assert mock_card.call_args[0][0] == "beta"
```

- [ ] **Step 2: Run test to confirm it fails**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_title_card_inserted_between_videos -v
```

Expected: `FAILED` — `make_title_card` is never called.

- [ ] **Step 3: Modify `_run_generation` to insert title cards**

Find the `with tempfile.TemporaryDirectory() as tmp_dir:` block in `_run_generation`. The current code is:

```python
    with tempfile.TemporaryDirectory() as tmp_dir:
        clip_paths: list[str] = []
        clip_durations: list[float] = []
        clip_index = 0
        for vp, segments in video_segments:
            for seg in segments:
                start_str, end_str = seg.split("-", 1)
                start_sec = parse_timestamp_to_seconds(start_str)
                end_sec = parse_timestamp_to_seconds(end_str)
                clip_path = os.path.join(tmp_dir, f"clip_{clip_index:04d}{vp.suffix}")
                try:
                    extract_clip(str(vp), start_sec, end_sec, clip_path)
                    clip_paths.append(clip_path)
                    clip_durations.append(end_sec - start_sec)
                    clip_index += 1
                except Exception as exc:
                    _append_log(job_id, f"✗ {vp.name} [{seg}] — extraction failed: {exc}")
```

Replace it with:

```python
    with tempfile.TemporaryDirectory() as tmp_dir:
        clip_paths: list[str] = []
        clip_durations: list[float] = []
        clip_index = 0
        prev_vp = None
        for vp, segments in video_segments:
            # Insert a title card between consecutive source videos
            if prev_vp is not None:
                card_path = os.path.join(tmp_dir, f"clip_{clip_index:04d}.mp4")
                try:
                    width, height = get_video_dimensions(str(vp))
                    make_title_card(Path(vp.name).stem, width, height, card_path)
                    clip_paths.append(card_path)
                    clip_index += 1
                    # Do NOT append to clip_durations — title cards are not content
                except Exception as exc:
                    _append_log(job_id, f"· Could not create title card for {vp.name}: {exc}")
            prev_vp = vp

            for seg in segments:
                start_str, end_str = seg.split("-", 1)
                start_sec = parse_timestamp_to_seconds(start_str)
                end_sec = parse_timestamp_to_seconds(end_str)
                clip_path = os.path.join(tmp_dir, f"clip_{clip_index:04d}{vp.suffix}")
                try:
                    extract_clip(str(vp), start_sec, end_sec, clip_path)
                    clip_paths.append(clip_path)
                    clip_durations.append(end_sec - start_sec)
                    clip_index += 1
                except Exception as exc:
                    _append_log(job_id, f"✗ {vp.name} [{seg}] — extraction failed: {exc}")
```

- [ ] **Step 4: Run the integration test**

```
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_title_card_inserted_between_videos -v
```

Expected: `PASSED`

- [ ] **Step 5: Run the full test suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: All tests pass (74 existing + 5 new = 79 total).

- [ ] **Step 6: Commit**

```
git add app.py tests/test_app.py
git commit -m "feat: insert title card transitions between source videos in generated reel"
```
