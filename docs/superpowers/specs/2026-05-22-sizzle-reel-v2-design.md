# Sizzle Reel Generator v2 — Design Spec
**Date:** 2026-05-22

## Overview

An expansion of the existing sizzle reel CLI tool. The original tool accepted pre-made transcripts and reported relevant timestamp ranges. This version accepts raw video files as input, transcribes them locally using Whisper, and — after identifying relevant segments via Claude — uses ffmpeg to extract those clips and stitch them into a single output video (the sizzle reel).

## Updated CLI Interface

```
python sizzle.py <videos_folder> "<prompt>" [--output sizzle_reel.mp4]
```

**Example:**
```
python sizzle.py ./interviews "What do people say about the hospitality of the waiters?" --output highlights.mp4
```

**Example terminal output:**
```
interview1.mp4:  0:30-1:45, 2:10-2:40
interview2.mp4:  no relevant segments found
interview3.mp4:  0:05-0:18, 3:44-4:02
Sizzle reel saved to highlights.mp4
```

## Architecture

Three sequential stages:

**Stage 1 — Transcription**
Scan the input folder for video files. For each video, check if a `.txt` transcript already exists with the same base name in the same folder. If it does, skip transcription. If not, run Whisper locally and save the transcript as `{video_name}.txt` alongside the video. This cache means re-running with a different prompt does not re-transcribe.

**Stage 2 — Timestamp extraction (existing)**
Pass each transcript and the user's prompt to Claude. Parse the response into `M:SS-M:SS` timestamp ranges. This stage is unchanged from v1.

**Stage 3 — Video assembly**
For each (video, segment) pair, extract the clip using ffmpeg into a system temp directory. Once all clips are extracted, concatenate them in order — all clips from the first video, then all from the second, etc. — into the output file. Temp clips are cleaned up after stitching.

## Transcript Format

Whisper segments are formatted as speaker-turn lines to match the format the existing Claude pipeline expects:

```
[0:05] Speaker: Text of the segment here.
[0:18] Speaker: Next segment of speech.
```

Seconds are converted to `M:SS`. "Speaker" is used as a generic label since Whisper does not perform speaker diarization. This format is identical to v1 transcripts and requires no changes to `claude_client.py` or `timestamp_parser.py`.

## Components

| Module | Function | Responsibility |
|---|---|---|
| `transcriber.py` | `transcribe_video(video_path)` | Runs Whisper on a video file, returns formatted transcript string |
| `video_editor.py` | `extract_clip(video_path, start_sec, end_sec, output_path)` | Extracts a single clip via ffmpeg subprocess |
| `video_editor.py` | `stitch_clips(clip_paths, output_path)` | Concatenates clips via ffmpeg concat demuxer |
| `loader.py` | `scan_videos(folder_path)` | Finds and sorts video files in a folder |
| `loader.py` | `load_transcripts(folder_path)` | Existing — loads `.txt` files (unchanged) |
| `sizzle.py` | `main()` | Updated orchestration: scan → transcribe → query → extract → stitch |
| `claude_client.py` | `query_claude(transcript, prompt)` | Unchanged |
| `timestamp_parser.py` | `parse_timestamps(response)` | Unchanged |

## Data Flow

1. User provides a folder path, a prompt string, and an optional output filename.
2. `scan_videos(folder)` returns a sorted list of video file paths.
3. For each video:
   - If `{video_name}.txt` exists and is non-empty in the same folder, read it as the transcript.
   - Otherwise, call `transcribe_video(video_path)`, save the result as `{video_name}.txt`, then use it.
4. For each transcript, call `query_claude(transcript, prompt)` then `parse_timestamps(response)`.
5. For each (video, segments) pair where segments exist, call `extract_clip()` for each segment into a temp directory.
6. Call `stitch_clips(all_clips_in_order, output_path)` to produce the final video.
7. Delete temp clips.

## ffmpeg Operations

**Clip extraction** (fast, no re-encode):
```
ffmpeg -i input.mp4 -ss {start} -to {end} -c copy clip_N.mp4
```

**Concatenation** via concat demuxer:
```
# concat_list.txt
file '/tmp/sizzle/clip_0.mp4'
file '/tmp/sizzle/clip_1.mp4'
...

ffmpeg -f concat -safe 0 -i concat_list.txt -c copy output.mp4
```

## Error Handling

| Scenario | Behavior |
|---|---|
| Folder does not exist or has no video files | Print clear error and exit |
| `ffmpeg` binary not found on PATH | Detect at startup, print install instructions, exit |
| Whisper fails on a video | Log warning to stderr, skip that video |
| Existing `.txt` transcript is empty or zero bytes | Re-transcribe rather than passing empty text to Claude |
| Claude returns unparseable response | Log per-file warning to stderr, continue (existing behavior) |
| No relevant segments found in any video | Print message, exit without creating output file |
| A single clip extraction fails | Log warning to stderr, skip that segment |
| No clips extracted across all videos | Print message, exit without creating empty output |

## Dependencies

**New:**
- `openai-whisper` — local Whisper transcription (pulls in `torch`)
- `ffmpeg` — system binary, must be installed separately

**Existing (unchanged):**
- `anthropic` — Claude API SDK
- `argparse`, `pathlib`, `subprocess`, `tempfile` — stdlib

Install: `pip install openai-whisper`
ffmpeg install: `winget install ffmpeg` (Windows) or `brew install ffmpeg` (Mac)

## File Structure

```
Sizzle Reel/
├── sizzle.py                        # Updated CLI entry point
├── loader.py                        # Updated: scan_videos() added
├── claude_client.py                 # Unchanged
├── timestamp_parser.py              # Unchanged
├── transcriber.py                   # NEW: transcribe_video()
├── video_editor.py                  # NEW: extract_clip(), stitch_clips()
├── requirements.txt                 # Updated: add openai-whisper
├── tests/
│   ├── test_loader.py               # Updated: scan_videos tests added
│   ├── test_transcriber.py          # NEW
│   ├── test_video_editor.py         # NEW
│   ├── test_claude_client.py        # Unchanged
│   ├── test_timestamp_parser.py     # Unchanged
│   └── fixtures/
│       ├── restaurant_review_1.txt
│       ├── restaurant_review_2.txt
│       └── off_topic.txt
└── docs/
    └── superpowers/
        ├── specs/
        │   ├── 2026-05-21-sizzle-reel-design.md
        │   └── 2026-05-22-sizzle-reel-v2-design.md
        └── plans/
            └── 2026-05-21-sizzle-reel.md
```

## Testing

**`test_transcriber.py`** — mocks `whisper.load_model` and `model.transcribe`. Verifies: correct `[M:SS] Speaker: text` formatting, correct seconds-to-timestamp conversion (e.g. 65.3s → `[1:05]`), transcript saved to expected `.txt` path.

**`test_video_editor.py`** — mocks `subprocess.run`. Verifies: correct ffmpeg arguments for clip extraction, correct concat list file contents, temp files cleaned up after stitching.

**`test_loader.py` (updated)** — adds `scan_videos` tests using `tmp_path`: only video extensions returned, alphabetical sort, `FileNotFoundError` on missing folder, `ValueError` on empty folder.

**End-to-end manual test** — requires real video files, ffmpeg installed, and `ANTHROPIC_API_KEY` set. Validates that the full pipeline produces a playable `.mp4` output.
