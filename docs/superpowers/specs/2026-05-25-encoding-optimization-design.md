# Encoding Optimization — Design Spec
**Date:** 2026-05-25

## Overview

Eliminate the H264 re-encoding step from the sizzle reel stitching pipeline. Source videos are `.webm` (VP9/Opus codec). The current `stitch_clips` function re-encodes to H264/AAC to produce a valid `.mp4`, which takes several minutes. Since `.webm` output is acceptable, we can revert to `-c copy` throughout and reduce stitching time from minutes to seconds.

## Root Cause

The re-encoding was introduced to fix a codec mismatch: `.webm` source codec cannot be stream-copied into an `.mp4` container. The fix was correct but slow. The better solution is to stay in `.webm` format end-to-end.

## Changes

### `video_editor.py` — `stitch_clips`

Revert from:
```python
"-c:v", "libx264",
"-c:a", "aac",
```

To:
```python
"-c", "copy",
```

Intermediate clips are already `.webm` with VP9/Opus. Stream copying requires no decode/encode cycle — ffmpeg just concatenates the raw bitstreams.

### `sizzle.py` — default output filename

Change default from `sizzle_reel.mp4` to `sizzle_reel.webm`.

Users can still specify any filename via `--output`. They should use `.webm` extension to match the source codec.

### `tests/test_video_editor.py` — `test_stitch_clips_calls_ffmpeg_concat`

Update expected ffmpeg args to reflect `-c copy` instead of `-c:v libx264 -c:a aac`.

## Data Flow (unchanged)

1. Source `.webm` videos → Whisper transcription → Claude timestamp extraction
2. ffmpeg `-c copy` extracts `.webm` clips into temp directory
3. ffmpeg `-c copy` concatenates `.webm` clips into output `.webm`
4. Temp directory cleaned up

## Performance Impact

| Step | Before | After |
|---|---|---|
| Clip extraction | ~instant (already `-c copy`) | ~instant (unchanged) |
| Stitching | Several minutes (H264 encode) | Seconds (stream copy) |

## Constraints

- Output format is `.webm`. All modern browsers and video players (VLC, Windows Media Player, Chrome, Firefox) support `.webm`.
- If sources are mixed formats (e.g. `.mp4` + `.webm`), stream copy during stitch will fail. This is out of scope — current use case is homogeneous source format.
