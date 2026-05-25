# Encoding Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate H264 re-encoding from the stitching step so the sizzle reel is produced in seconds instead of minutes.

**Architecture:** Source videos are `.webm` (VP9/Opus). Intermediate clips are already extracted with `-c copy` (no re-encode). The only bottleneck is the final stitch, which currently re-encodes to H264/AAC. Reverting it to `-c copy` and defaulting the output to `.webm` eliminates all encoding work entirely.

**Tech Stack:** Python 3.11+, ffmpeg system binary, pytest

---

## File Structure

```
Sizzle Reel/
├── video_editor.py          # Modify: stitch_clips() — revert to -c copy
├── sizzle.py                # Modify: default --output from .mp4 to .webm
└── tests/
    └── test_video_editor.py # Modify: add codec assertion to stitch test
```

---

## Task 1: Update stitch_clips to use stream copy

**Files:**
- Modify: `tests/test_video_editor.py` (update existing test)
- Modify: `video_editor.py` (revert codec args)

- [ ] **Step 1: Update the existing test to assert `-c copy` is used**

In `tests/test_video_editor.py`, find `test_stitch_clips_calls_ffmpeg_concat` (currently at line 49) and replace it with:

```python
def test_stitch_clips_calls_ffmpeg_concat(tmp_path):
    output = str(tmp_path / "out.webm")
    with patch("video_editor.subprocess.run") as mock_run:
        stitch_clips(["/tmp/clip_0.webm", "/tmp/clip_1.webm"], output)
    args = mock_run.call_args[0][0]
    assert "-f" in args
    assert "concat" in args
    assert output in args
    assert "-c" in args
    assert "copy" in args
    assert "libx264" not in args
    assert "aac" not in args
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_video_editor.py::test_stitch_clips_calls_ffmpeg_concat -v`

Expected: FAIL — `assert "libx264" not in args` fails because the current implementation still uses libx264.

- [ ] **Step 3: Update stitch_clips in video_editor.py**

In `video_editor.py`, replace the subprocess.run call inside `stitch_clips` — change from:

```python
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list_path,
                "-c:v", "libx264",
                "-c:a", "aac",
                output_path,
            ],
            check=True,
            capture_output=True,
        )
```

To:

```python
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
```

- [ ] **Step 4: Run the full video_editor test suite to verify all tests pass**

Run: `pytest tests/test_video_editor.py -v`

Expected: all 8 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add video_editor.py tests/test_video_editor.py
git commit -m "perf: revert stitch_clips to -c copy, eliminate H264 re-encoding"
```

---

## Task 2: Update default output filename in sizzle.py

**Files:**
- Modify: `sizzle.py`

No unit tests for `main()` — validated by running the full test suite.

- [ ] **Step 1: Update the default output argument in sizzle.py**

In `sizzle.py`, find this line (around line 23):

```python
        "--output", default="sizzle_reel.mp4",
        help="Output file path (default: sizzle_reel.mp4)"
```

Replace with:

```python
        "--output", default="sizzle_reel.webm",
        help="Output file path (default: sizzle_reel.webm)"
```

- [ ] **Step 2: Run the full test suite to confirm nothing is broken**

Run: `pytest tests/ -v`

Expected: all tests PASSED

- [ ] **Step 3: Commit**

```bash
git add sizzle.py
git commit -m "fix: default output to sizzle_reel.webm to match source codec"
```
