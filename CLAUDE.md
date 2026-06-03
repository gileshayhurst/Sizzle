# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```powershell
# Run all tests
.\venv\Scripts\python.exe -m pytest tests/ -v

# Run a single test file
.\venv\Scripts\python.exe -m pytest tests/test_app.py -v

# Run a single test
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_load_folder_saves_to_recent -v

# Start the web app (primary interface)
.\venv\Scripts\python.exe -c "from app import create_app; create_app().run(debug=True)"

# Run the legacy CLI tool
.\venv\Scripts\python.exe sizzle.py <videos_folder> --prompt words here
.\venv\Scripts\python.exe sizzle.py <videos_folder> --prompt words here --output custom_name.mp4

# Generate synthetic test data (MP4+TXT pairs, 5 business categories)
.\venv\Scripts\python.exe create_test_data.py <output_folder>
```

## Environment

- `ANTHROPIC_API_KEY` — required; also auto-loaded from a `.env` file in the project root
- `ffmpeg` — required system binary (`winget install ffmpeg` on Windows). `app.py` patches the WinGet install path into `PATH` at startup, so subprocess calls find it even if the shell PATH doesn't include it.
- **Windows note:** ffmpeg is on the PowerShell PATH but NOT the bash/tool-shell PATH. Run ffmpeg-dependent commands from PowerShell.
- Python deps: `pip install -r requirements.txt` (`anthropic`, `openai-whisper`, `flask`, `pytest`)
- A `venv` is present in the repo root — prefix commands with `.\venv\Scripts\python.exe`

## Architecture

There are two independent entry points that share the lower-level modules:

### Web app (primary) — `app.py` + `templates/index.html` + `static/`

Flask app factory pattern: `create_app(testing=False)` returns the Flask instance; all routes are defined inside it. The app is single-file (`app.py`) with no blueprints.

**Two-stage pipeline exposed via HTTP:**

1. **`POST /analyze`** — synchronous. Calls Claude (`query_claude`) on every transcript in the folder, returns per-video matched raw transcript lines as `{"highlights": {filename: [raw_line, ...]}}`. This is the only point where Claude is called.

2. **`POST /generate`** — async job. Spawns `_run_generation` in a daemon thread. Extracts clips via ffmpeg, inserts title cards, stitches with the concat demuxer. Progress polled via `GET /status/<job_id>`.

**Job system:** `_new_job()` creates a UUID-keyed dict in `_jobs` with `status`, `done/total`, `log[]`, `result`, and a `threading.Event` for cancellation. `DELETE /jobs/<job_id>` sets the cancel event; the generation thread checks it between videos.

**Segment logic** (`_group_lines_into_segments`): converts a set of selected raw transcript lines into `(start_sec, end_sec)` clip ranges. An unselected line between two selected lines splits them into separate segments. End time is the timestamp of the first unselected line after the segment, or `last_line.seconds + 10` if the segment runs to the end.

**Title cards:** `make_title_card()` generates a black H.264/AAC card with centred text via ffmpeg `drawtext`. A video-name title card is inserted **before every source video** (including the first). A "Segment N" card is inserted between non-contiguous selected clusters within the same video.

**Generated-reel filtering:** `_filter_generated_reels()` cross-references `sizzle_library.json` to exclude previously generated reels from `scan_videos()` results — prevents a generated sizzle reel saved into the source folder from being re-transcribed on the next open.

**Persistence files** (project root, gitignored):
- `sizzle_library.json` — list of generated reel entries (id, path, filename, prompt, duration, clip_count, created_at)
- `recent_folders.json` — last 5 opened folders (path, video_count, last_opened ISO timestamp), deduped by path, most-recent-first

**Frontend** (`templates/index.html`, `static/app.js`, `static/style.css`):
- Single-page app, no framework. All state in a `state` object in `app.js`.
- **Screens:** folder-picker → transcribing → workspace → generating → result (plus Library tab overlay)
- **Workspace layout:** two vertical zones — `.analyze-zone` (top, fixed height, prompt input) and `.transcript-zone` (bottom, flex:1, scrollable). The outer `.workspace-layout` flex container uses `flex-direction: row` — if this is missing, the sidebar stacks on top of the main panel instead of sitting to its left.
- **Two selection modes:** Checkbox (click individual lines; grouped by minute-bucket) and Highlight (drag-to-brush; `mousedown`+`mousemove` on document, AbortController cleans up listeners on re-render).
- **Highlight mode scroll:** `mousemove` listener is on `document` (not the scroll container) so auto-scroll fires even when the mouse drifts outside the transcript. Auto-scroll edge check runs **before** the lineEl check — during drag the mouse is always over a line, so checking lineEl first would mean auto-scroll never fires.

### CLI tool (legacy) — `sizzle.py`

Orchestrates the same lower-level modules directly without Flask. Loads Whisper once, iterates videos, calls Claude, extracts clips. Still functional but not the active development target.

### Shared lower-level modules

- **`loader.py`** — `scan_videos()`: finds `.mp4 .mov .avi .mkv .webm` files in a folder, sorted alphabetically.
- **`transcriber.py`** — `transcribe_video()`: runs Whisper `base` model with `word_timestamps=True`, splits segments on terminal punctuation via `_split_into_sentences()` for sub-segment timestamp precision. Output format: `[M:SS] Speaker: text`. Transcripts cached as `{video}.txt`; delete the `.txt` to force re-transcription.
- **`claude_client.py`** — `query_claude(transcript, prompt)`: sends transcript + prompt to `claude-opus-4-7`. System prompt instructs Claude to return 2–4 `M:SS-M:SS` ranges (or `none`), starting as late and ending as early as possible.
- **`timestamp_parser.py`** — `parse_timestamps()`: extracts `M:SS-M:SS` ranges from Claude's response with a regex.
- **`video_editor.py`** — `extract_clip()`: re-encodes to H.264/AAC (required — stream copy produces P/B-frame starts that freeze on playback). `stitch_clips()`: concat demuxer with `-c copy` (safe because all clips are now I-frame-aligned). `get_video_dimensions()`, `parse_timestamp_to_seconds()`.

## Key Behaviours

- **`_filter_generated_reels` is called in every code path** that calls `scan_videos` (`load_folder`, `/transcripts`, `_run_analyze`, `_run_generation`, `/generate`). If you add a new code path that scans videos, add the filter call.
- **Tests mock `_library_add`** in generate-endpoint tests to prevent writing to the real `sizzle_library.json` on disk during pytest runs. Always add `patch("app._library_add")` when writing new tests that exercise the generate flow end-to-end.
- **Whisper is lazy-loaded** in the web app (`_get_whisper_model()` with a double-checked lock) so the first `/load-folder` that needs transcription triggers the load. In `sizzle.py` the model is loaded once up front.
- **ffmpeg re-encoding** in `extract_clip` uses `-c:v libx264 -preset fast -c:a aac`. Do not switch to `-c copy` for clip extraction — it produces clips that start mid-GOP, causing visible freezes at every transition.
