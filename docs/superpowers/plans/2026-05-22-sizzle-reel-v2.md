# Sizzle Reel v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the sizzle reel CLI to accept raw video files as input, transcribe them locally with Whisper, and stitch the relevant segments into a single output video using ffmpeg.

**Architecture:** Three stages — (1) transcribe each video with local Whisper and cache the `.txt` alongside the video, (2) pass each transcript to Claude to identify relevant timestamp ranges (existing pipeline, unchanged), (3) extract clips with ffmpeg and concatenate them into the output file. Two new modules (`transcriber.py`, `video_editor.py`) and two updated ones (`loader.py`, `sizzle.py`).

**Tech Stack:** Python 3.11+, `openai-whisper`, `torch` (pulled in by whisper), `ffmpeg` system binary, `anthropic` SDK, `pytest`, `pathlib`, `subprocess`, `tempfile`

---

## File Structure

```
Sizzle Reel/
├── sizzle.py                  # Updated: full pipeline orchestration
├── loader.py                  # Updated: scan_videos() added
├── claude_client.py           # Unchanged
├── timestamp_parser.py        # Unchanged
├── transcriber.py             # NEW: transcribe_video(), _seconds_to_timestamp()
├── video_editor.py            # NEW: check_ffmpeg(), parse_timestamp_to_seconds(),
│                              #      extract_clip(), stitch_clips()
├── requirements.txt           # Updated: add openai-whisper
└── tests/
    ├── test_loader.py         # Updated: scan_videos tests added
    ├── test_transcriber.py    # NEW
    ├── test_video_editor.py   # NEW
    ├── test_claude_client.py  # Unchanged
    └── test_timestamp_parser.py  # Unchanged
```

---

## Task 1: Install Whisper

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add openai-whisper to requirements.txt**

Replace the contents of `requirements.txt` with:

```
anthropic
openai-whisper
pytest
```

- [ ] **Step 2: Install the new dependency**

Run:
```
pip install -r requirements.txt
```

Expected: packages install without error. `openai-whisper` pulls in `torch` automatically — this may take a few minutes on first install.

Verify:
```
python -c "import whisper; print('ok')"
```

- [ ] **Step 3: Verify ffmpeg is installed**

Run:
```
ffmpeg -version
```

If not installed:
- Windows: `winget install ffmpeg`
- Mac: `brew install ffmpeg`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore: add openai-whisper dependency"
```

---

## Task 2: scan_videos() in loader.py

**Files:**
- Modify: `loader.py`
- Modify: `tests/test_loader.py`

- [ ] **Step 1: Write the failing tests**

First, add `scan_videos` to the existing import at the top of `tests/test_loader.py`:

```python
from loader import load_transcripts, scan_videos
```

Then add these test functions to the bottom of `tests/test_loader.py`:

```python
def test_scan_videos_returns_sorted_path_list(tmp_path):
    (tmp_path / "b.mp4").touch()
    (tmp_path / "a.mp4").touch()
    result = scan_videos(str(tmp_path))
    assert [p.name for p in result] == ["a.mp4", "b.mp4"]


def test_scan_videos_supports_all_extensions(tmp_path):
    (tmp_path / "clip.mp4").touch()
    (tmp_path / "clip.mov").touch()
    (tmp_path / "clip.avi").touch()
    (tmp_path / "clip.mkv").touch()
    result = scan_videos(str(tmp_path))
    assert len(result) == 4


def test_scan_videos_ignores_non_video_files(tmp_path):
    (tmp_path / "video.mp4").touch()
    (tmp_path / "notes.txt").write_text("notes")
    (tmp_path / "doc.pdf").touch()
    result = scan_videos(str(tmp_path))
    assert len(result) == 1
    assert result[0].name == "video.mp4"


def test_scan_videos_raises_file_not_found_on_missing_folder():
    with pytest.raises(FileNotFoundError, match="Folder not found"):
        scan_videos("/nonexistent/path/that/does/not/exist")


def test_scan_videos_raises_value_error_on_no_video_files(tmp_path):
    (tmp_path / "notes.txt").write_text("notes")
    with pytest.raises(ValueError, match="No video files found"):
        scan_videos(str(tmp_path))


def test_scan_videos_returns_path_objects(tmp_path):
    (tmp_path / "video.mp4").touch()
    result = scan_videos(str(tmp_path))
    assert all(isinstance(p, Path) for p in result)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_loader.py -v -k "scan_videos"`

Expected: `ImportError: cannot import name 'scan_videos' from 'loader'`

- [ ] **Step 3: Implement scan_videos() in loader.py**

Replace the contents of `loader.py` with:

```python
from pathlib import Path

_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}


def load_transcripts(folder_path: str) -> dict[str, str]:
    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")
    files = sorted(folder.glob("*.txt"))
    if not files:
        raise ValueError(f"No .txt files found in: {folder_path}")
    return {f.name: f.read_text(encoding="utf-8") for f in files}


def scan_videos(folder_path: str) -> list[Path]:
    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")
    files = sorted(f for f in folder.iterdir() if f.suffix.lower() in _VIDEO_EXTENSIONS)
    if not files:
        raise ValueError(f"No video files found in: {folder_path}")
    return files
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_loader.py -v`

Expected: all 11 tests PASSED (5 existing + 6 new)

- [ ] **Step 5: Commit**

```bash
git add loader.py tests/test_loader.py
git commit -m "feat: add scan_videos() to loader"
```

---

## Task 3: transcriber.py

**Files:**
- Create: `transcriber.py`
- Create: `tests/test_transcriber.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_transcriber.py`:

```python
from unittest.mock import MagicMock, patch
from transcriber import transcribe_video, _seconds_to_timestamp


def test_seconds_to_timestamp_zero():
    assert _seconds_to_timestamp(0.0) == "0:00"


def test_seconds_to_timestamp_whole_minutes():
    assert _seconds_to_timestamp(60.0) == "1:00"


def test_seconds_to_timestamp_minutes_and_seconds():
    assert _seconds_to_timestamp(125.0) == "2:05"


def test_seconds_to_timestamp_pads_single_digit_seconds():
    assert _seconds_to_timestamp(5.0) == "0:05"


def test_seconds_to_timestamp_truncates_fractional_seconds():
    assert _seconds_to_timestamp(65.9) == "1:05"


def _make_mock_model(segments):
    mock_model = MagicMock()
    mock_model.transcribe.return_value = {"segments": segments}
    return mock_model


def test_formats_single_segment():
    with patch("transcriber.whisper.load_model") as mock_load:
        mock_load.return_value = _make_mock_model([{"start": 5.0, "text": "Hello there"}])
        result = transcribe_video("video.mp4")
    assert result == "[0:05] Speaker: Hello there"


def test_formats_multiple_segments():
    segments = [
        {"start": 5.0, "text": "Hello there"},
        {"start": 65.0, "text": "And then she said"},
    ]
    with patch("transcriber.whisper.load_model") as mock_load:
        mock_load.return_value = _make_mock_model(segments)
        result = transcribe_video("video.mp4")
    assert result == "[0:05] Speaker: Hello there\n[1:05] Speaker: And then she said"


def test_strips_whitespace_from_segment_text():
    with patch("transcriber.whisper.load_model") as mock_load:
        mock_load.return_value = _make_mock_model([{"start": 0.0, "text": "  padded  "}])
        result = transcribe_video("video.mp4")
    assert result == "[0:00] Speaker: padded"


def test_loads_base_model():
    with patch("transcriber.whisper.load_model") as mock_load:
        mock_load.return_value = _make_mock_model([{"start": 0.0, "text": "Test"}])
        transcribe_video("video.mp4")
    mock_load.assert_called_once_with("base")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_transcriber.py -v`

Expected: `ModuleNotFoundError: No module named 'transcriber'`

- [ ] **Step 3: Implement transcriber.py**

Create `transcriber.py`:

```python
import whisper


def _seconds_to_timestamp(seconds: float) -> str:
    total = int(seconds)
    m = total // 60
    s = total % 60
    return f"{m}:{s:02d}"


def transcribe_video(video_path: str) -> str:
    model = whisper.load_model("base")
    result = model.transcribe(video_path)
    lines = []
    for segment in result["segments"]:
        ts = _seconds_to_timestamp(segment["start"])
        text = segment["text"].strip()
        lines.append(f"[{ts}] Speaker: {text}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_transcriber.py -v`

Expected: 9 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add transcriber.py tests/test_transcriber.py
git commit -m "feat: add transcriber using local Whisper"
```

---

## Task 4: video_editor.py

**Files:**
- Create: `video_editor.py`
- Create: `tests/test_video_editor.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_video_editor.py`:

```python
import pytest
from unittest.mock import MagicMock, patch
from video_editor import (
    check_ffmpeg,
    parse_timestamp_to_seconds,
    extract_clip,
    stitch_clips,
)


def test_parse_timestamp_to_seconds_zero():
    assert parse_timestamp_to_seconds("0:00") == 0.0


def test_parse_timestamp_to_seconds_minutes_and_seconds():
    assert parse_timestamp_to_seconds("1:05") == 65.0


def test_parse_timestamp_to_seconds_large():
    assert parse_timestamp_to_seconds("12:30") == 750.0


def test_check_ffmpeg_raises_when_not_found():
    with patch("video_editor.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(RuntimeError, match="ffmpeg not found"):
            check_ffmpeg()


def test_check_ffmpeg_passes_when_found():
    with patch("video_editor.subprocess.run", return_value=MagicMock()):
        check_ffmpeg()  # should not raise


def test_extract_clip_calls_correct_ffmpeg_args():
    with patch("video_editor.subprocess.run") as mock_run:
        extract_clip("input.mp4", 5.0, 30.0, "clip.mp4")
    args = mock_run.call_args[0][0]
    assert args == [
        "ffmpeg", "-y",
        "-i", "input.mp4",
        "-ss", "5.0",
        "-to", "30.0",
        "-c", "copy",
        "clip.mp4",
    ]


def test_stitch_clips_calls_ffmpeg_concat(tmp_path):
    output = str(tmp_path / "out.mp4")
    with patch("video_editor.subprocess.run") as mock_run:
        stitch_clips(["/tmp/clip_0.mp4", "/tmp/clip_1.mp4"], output)
    args = mock_run.call_args[0][0]
    assert "-f" in args
    assert "concat" in args
    assert output in args


def test_stitch_clips_concat_list_contains_clip_paths(tmp_path):
    output = str(tmp_path / "out.mp4")
    captured = []

    def mock_run(cmd, **kwargs):
        if "-f" in cmd and "concat" in cmd:
            list_file = cmd[cmd.index("-i") + 1]
            with open(list_file) as f:
                captured.append(f.read())
        return MagicMock()

    with patch("video_editor.subprocess.run", side_effect=mock_run):
        stitch_clips(["/tmp/clip_0.mp4", "/tmp/clip_1.mp4"], output)

    assert len(captured) == 1
    assert "/tmp/clip_0.mp4" in captured[0]
    assert "/tmp/clip_1.mp4" in captured[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_video_editor.py -v`

Expected: `ModuleNotFoundError: No module named 'video_editor'`

- [ ] **Step 3: Implement video_editor.py**

Create `video_editor.py`:

```python
import os
import subprocess
import tempfile


def check_ffmpeg() -> None:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True)
    except FileNotFoundError:
        raise RuntimeError(
            "ffmpeg not found. Install it with:\n"
            "  Windows: winget install ffmpeg\n"
            "  Mac: brew install ffmpeg"
        )


def parse_timestamp_to_seconds(ts: str) -> float:
    parts = ts.split(":")
    return float(int(parts[0]) * 60 + int(parts[1]))


def extract_clip(video_path: str, start_sec: float, end_sec: float, output_path: str) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_path,
            "-ss", str(start_sec),
            "-to", str(end_sec),
            "-c", "copy",
            output_path,
        ],
        check=True,
        capture_output=True,
    )


def stitch_clips(clip_paths: list[str], output_path: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        concat_list_path = f.name
        for path in clip_paths:
            f.write(f"file '{path}'\n")
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list_path,
                "-c", "copy",
                output_path,
            ],
            check=True,
            capture_output=True,
        )
    finally:
        os.unlink(concat_list_path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_video_editor.py -v`

Expected: 8 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add video_editor.py tests/test_video_editor.py
git commit -m "feat: add video_editor with ffmpeg clip extraction and stitching"
```

---

## Task 5: Update sizzle.py

**Files:**
- Modify: `sizzle.py`

No unit tests for `main()` — it is pure orchestration and is validated by the end-to-end test in Task 6.

- [ ] **Step 1: Run the full test suite to confirm baseline**

Run: `pytest tests/ -v`

Expected: all existing tests PASSED

- [ ] **Step 2: Replace sizzle.py**

Replace the entire contents of `sizzle.py` with:

```python
import argparse
import os
import sys
import tempfile
from pathlib import Path

from claude_client import query_claude
from loader import scan_videos
from timestamp_parser import parse_timestamps
from transcriber import transcribe_video
from video_editor import check_ffmpeg, extract_clip, parse_timestamp_to_seconds, stitch_clips


def main():
    parser = argparse.ArgumentParser(
        description="Generate a sizzle reel from relevant segments across video files."
    )
    parser.add_argument("folder", help="Path to folder containing video files")
    parser.add_argument("prompt", help="Topic to search for in the videos")
    parser.add_argument(
        "--output", default="sizzle_reel.mp4",
        help="Output file path (default: sizzle_reel.mp4)"
    )
    args = parser.parse_args()

    try:
        check_ffmpeg()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        video_paths = scan_videos(args.folder)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    video_segments: list[tuple[Path, list[str]]] = []

    for video_path in video_paths:
        transcript_path = video_path.with_suffix(".txt")

        if transcript_path.exists() and transcript_path.stat().st_size > 0:
            transcript = transcript_path.read_text(encoding="utf-8")
        else:
            print(f"Transcribing {video_path.name}...", file=sys.stderr)
            try:
                transcript = transcribe_video(str(video_path))
            except Exception as e:
                print(f"{video_path.name}: [warning: transcription failed — {e}]", file=sys.stderr)
                continue
            transcript_path.write_text(transcript, encoding="utf-8")

        try:
            response = query_claude(transcript, args.prompt)
            segments = parse_timestamps(response)
        except Exception as e:
            print(f"{video_path.name}: [warning: API error — {e}]", file=sys.stderr)
            continue

        if segments:
            print(f"{video_path.name}:  {', '.join(segments)}")
            video_segments.append((video_path, segments))
        else:
            print(f"{video_path.name}:  no relevant segments found")

    if not video_segments:
        print("No relevant segments found in any video. No output created.", file=sys.stderr)
        sys.exit(0)

    with tempfile.TemporaryDirectory() as tmp_dir:
        clip_paths = []
        clip_index = 0
        for video_path, segments in video_segments:
            for segment in segments:
                start_str, end_str = segment.split("-")
                start_sec = parse_timestamp_to_seconds(start_str)
                end_sec = parse_timestamp_to_seconds(end_str)
                clip_path = os.path.join(tmp_dir, f"clip_{clip_index:04d}.mp4")
                try:
                    extract_clip(str(video_path), start_sec, end_sec, clip_path)
                    clip_paths.append(clip_path)
                    clip_index += 1
                except Exception as e:
                    print(
                        f"{video_path.name} [{segment}]: [warning: clip extraction failed — {e}]",
                        file=sys.stderr,
                    )

        if not clip_paths:
            print("No clips could be extracted. No output created.", file=sys.stderr)
            sys.exit(1)

        try:
            stitch_clips(clip_paths, args.output)
        except Exception as e:
            print(f"Error: stitching failed — {e}", file=sys.stderr)
            sys.exit(1)

    print(f"Sizzle reel saved to {args.output}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the full test suite to confirm nothing is broken**

Run: `pytest tests/ -v`

Expected: all tests PASSED

- [ ] **Step 4: Commit**

```bash
git add sizzle.py
git commit -m "feat: update sizzle.py for full video pipeline"
```

---

## Task 6: End-to-End Manual Test

**Files:** none (uses real video files, live Claude API, system ffmpeg)

Requires:
- `ANTHROPIC_API_KEY` set in environment
- `ffmpeg` installed and on PATH
- A folder of real video files (`.mp4`, `.mov`, `.avi`, or `.mkv`)

If you don't have test videos, record a few short clips (30–90 seconds each) or download free stock footage. Interview-style or review-style content works well for testing the prompt.

- [ ] **Step 1: Run with a focused prompt (expect matches in at least one video)**

```
python sizzle.py <your_videos_folder> "What do people say about the service?" --output highlights.mp4
```

Expected terminal output (filenames and timestamps will vary):
```
Transcribing interview1.mp4...
Transcribing interview2.mp4...
interview1.mp4:  0:30-1:45
interview2.mp4:  no relevant segments found
Sizzle reel saved to highlights.mp4
```

Verify: open `highlights.mp4` and confirm it plays and contains the expected content.

- [ ] **Step 2: Re-run with the same prompt to confirm transcript caching**

Run the same command again immediately.

Expected: `Transcribing ...` lines do NOT appear (cached `.txt` files are used). The run completes much faster.

- [ ] **Step 3: Run with an off-topic prompt (expect no output file)**

```
python sizzle.py <your_videos_folder> "What do people say about rocket propulsion?"
```

Expected:
```
interview1.mp4:  no relevant segments found
interview2.mp4:  no relevant segments found
No relevant segments found in any video. No output created.
```

Verify: no `sizzle_reel.mp4` file is created.

- [ ] **Step 4: Run with a bad folder path (expect clean error)**

```
python sizzle.py /nonexistent/path "some prompt"
```

Expected:
```
Error: Folder not found: /nonexistent/path
```

- [ ] **Step 5: Commit**

```bash
git add .
git commit -m "test: verify end-to-end manual tests pass"
```
