# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Design Context

Frontend/UX work is governed by two root docs — read them before touching `templates/`, `static/style.css`, or `static/app.js`:

- **[PRODUCT.md](PRODUCT.md)** — strategic: register (`product`), users (paying market-research clients), purpose, brand personality, anti-references, design principles, WCAG AA.
- **[DESIGN.md](DESIGN.md)** — visual system, matched to the parent **Forven / HumanLens** platform. North Star: **"The Bright Studio"** (light chrome, dark video stage, single warm **Studio Amber** accent). Tokens, type scale, component states, and named rules live here; `.impeccable/design.json` is the machine-readable sidecar.

The Bright Studio conversion is complete — the app is fully on this light system. New/edited UI must follow DESIGN.md.

## Commands

```powershell
# Run all tests
.\venv\Scripts\python.exe -m pytest tests/ -v

# Run a single test file
.\venv\Scripts\python.exe -m pytest tests/test_app.py -v

# Run a single test
.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_load_folder_saves_to_recent -v

# Start the main web app (port 5000)
.\venv\Scripts\python.exe -c "from app import create_app; create_app().run(debug=True)"

# Start the generator service (port 5001)
.\venv\Scripts\python.exe -c "from generator_app import create_app; create_app().run(debug=True, port=5001)"

# Generate synthetic test data (MP4+TXT pairs, 5 business categories)
.\venv\Scripts\python.exe create_test_data.py <output_folder>
```

## Environment

- `ANTHROPIC_API_KEY` — required for `app.py`; also auto-loaded from a `.env` file in the project root
- `ffmpeg` — required system binary (`winget install ffmpeg` on Windows). Both `app.py` and `generator_app.py` patch the WinGet install path into `PATH` at startup.
- **Windows note:** ffmpeg is on the PowerShell PATH but NOT the bash/tool-shell PATH. Run ffmpeg-dependent commands from PowerShell.
- Python deps: `pip install -r requirements.txt` (`anthropic`, `faster-whisper`, `flask`, `flask-cors`, `flask-sock`, `boto3`, `pytest`)
- A `venv` is present in the repo root — prefix commands with `.\venv\Scripts\python.exe`

**Cloud mode env vars** (required when `APP_MODE=cloud`):
- `APP_MODE=cloud` — switches `storage.py` to the S3/R2 backend
- `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` — R2/S3 credentials
- `DATA_ROOT` — optional local filesystem root override (defaults to project dir)
- `GENERATOR_URL` — injected into `index.html` by `app.py` so the frontend knows where the generator service lives

## Architecture

The project has **two independent Flask services** plus shared lower-level modules:

```
app.py          → main app (port 5000)   — transcription, analysis, frontend
generator_app.py → generator service (port 5001) — video extraction, library, WebSocket
```

In production the main app runs on Vercel and the generator service runs on Render.

### Main app — `app.py`

Flask factory pattern: `create_app(testing=False)`. All routes defined inside the factory. Single-file, no blueprints.

**HTTP API:**

1. **`POST /analyze`** — synchronous. Calls Claude (`query_claude`) on every transcript in the folder, returns per-video matched raw transcript lines as `{"highlights": {filename: [raw_line, ...]}}`. The only place Claude is called.

2. **`POST /load-folder`** — scans a folder, transcribes any videos without cached `.txt` files (Whisper, lazy-loaded via `_get_whisper_model()`), returns file list and existing transcript content. Saves to `recent_folders.json`.

3. **`GET /transcripts`** — returns transcript text for files in a folder (cloud upload path).

4. **Prompt history**: `GET/POST /prompt-history` and `POST /prompt-history/templates` — persists recent prompts and saved templates to `prompt_history.json`.

**Frontend** (`templates/index.html`, `static/app.js`, `static/style.css`):
- Single-page app, no framework. All state in a `state` object in `app.js`.
- **Screens:** folder-picker → transcribing → workspace → generating → result (plus Library tab overlay)
- **Workspace layout:** two vertical zones — `.analyze-zone` (top, fixed height, prompt input) and `.transcript-zone` (bottom, flex:1, scrollable). The outer `.workspace-layout` flex container uses `flex-direction: row` — if this is missing, the sidebar stacks on top of the main panel instead of sitting to its left.
- **Two selection modes:** Checkbox (click individual lines; grouped by minute-bucket) and Highlight (drag-to-brush; `mousedown`+`mousemove` on document, AbortController cleans up listeners on re-render).
- **Highlight mode scroll:** `mousemove` listener is on `document` (not the scroll container) so auto-scroll fires even when the mouse drifts outside the transcript. Auto-scroll edge check runs **before** the lineEl check — during drag the mouse is always over a line, so checking lineEl first would mean auto-scroll never fires.
- **Additive analyze:** After the first analyze, an `#analyze-add-row` appears. Running it calls `/analyze` and **unions** the results into existing selections (does not replace). `_clearSelections()` hides the row and clears its input.
- **Selection persistence:** `localStorage` key `sizzle_sel_<folder>` persists selected lines across page reloads. `_clearSelections()` wipes it; called after generation succeeds.

### Generator service — `generator_app.py`

Flask factory pattern: `create_app(testing=False)`. Uses `flask-cors` and `flask-sock`.

**HTTP + WebSocket API:**

- **`POST /generate`** — starts an async generation job in a daemon thread; returns `{"job_id": "..."}`.
- **`WS /ws/job/<job_id>`** — WebSocket stream for live progress: emits `{type: "log"}`, `{type: "progress"}`, and `{type: "done"}` messages at 200ms intervals. The frontend connects here; HTTP polling via `GET /status/<job_id>` is the fallback.
- **`GET /status/<job_id>`** — HTTP polling fallback for progress.
- **`DELETE /jobs/<job_id>`** — sets the cancel `threading.Event`; the generation thread checks it between phases.
- **`GET /video/<job_id>`** — serves the generated reel from disk (local temp file first, R2 redirect as fallback).
- **`GET /library`** — returns library entries. Does NOT inject presigned URLs — all video playback goes through `/library-video/<id>`.
- **`GET /library-video/<id>`** — serves the local temp file when present, else **redirects** to a presigned R2 GET URL that forces `Content-Type: video/mp4` (via S3 `ResponseContentType`). That forced media type, plus R2 CORS now allowing `GET` (see `set_cors.py`), satisfies Chrome's ORB. This replaced an earlier Flask byte-proxy that dodged `ERR_BLOCKED_BY_ORB` but streamed every playback through the host, burning metered bandwidth per view. If ORB ever regresses, revert this endpoint to the proxy.
- **`DELETE /library/<id>`** — removes library entry; `?delete_file=true` also deletes the file from disk.
- **`PATCH /library/<id>`** — edits `title` and `notes` fields on a library entry.
- **`GET /library-captions/<id>`** — serves the reel's WebVTT track (`text/vtt`): local `.vtt` sidecar first, else proxies the cloud `captions_key` bytes (the VTT is tiny text, unlike metered video). Used by the library player's `<track>` and by the cloud burn-in path.
- **`POST /library/<id>/download-captioned`** — **local mode only** (cloud returns 400). Burns the reel's VTT into a downloadable MP4 via ffmpeg's `subtitles` filter (`-vf subtitles=…:force_style=…`, audio `-c copy`) and streams it as an attachment. Cloud burns in-browser instead (see `reel-encoder.js`).
- **`POST /open-folder`** — launches Windows Explorer to a folder (no-op on Linux).

**Generation pipeline** (`_run_generation`):

1. **Phase 1 — Plan:** Build an ordered list of `{type: "title"|"clip", ...}` items. Every segment gets a title card + clip pair. Title card text: video stem, start timestamp, "Segment N / total".
2. **Phase 2 — Execute:** Title cards serially (fast, ~0.1s each via ffmpeg `drawtext`). Clips **in parallel** using `ThreadPoolExecutor(max_workers=min(4, cpu_count))` — clips run concurrently, waiting on all futures before proceeding.
3. **Phase 3 — Assemble:** Skips any pair where either the title card or clip failed; stitches remaining clips with the concat demuxer (`-c copy`).

In cloud mode: downloads all session files from S3 into a temp dir before extraction; uploads the finished reel to `{session_key}/{output_filename}` and records `reel_s3_key` in the library entry.

**Job dict keys:** `type`, `status`, `total`, `done`, `log[]`, `result`, `error`, `cancel` (threading.Event), `_thread`.

### Storage abstraction — `storage.py`

Switches between local filesystem and S3/R2 based on `APP_MODE` env var. Both backends expose identical function signatures:

- `upload_file(local_path, key)` — copies file to storage (sets ContentType via mimetypes in cloud mode)
- `download_file(key, local_path)` — retrieves file from storage
- `read_json(key)` / `write_json(key, data)` — read/write JSON
- `list_keys(prefix)` — list all keys under a prefix
- `read_file_bytes(key)` — read raw bytes (used by `/library-captions` cloud path to proxy the VTT)
- `upload_bytes(key, data, content_type=...)` — write an in-memory bytes payload (used to upload the caption VTT without a temp-file round-trip; does **not** call `upload_file`, so the cloud "reel goes via `upload_stream`" test invariant stays intact)
- `presigned_url(key)` / `presigned_put_url(key)` — generate presigned S3 URLs (cloud only)
- `new_session_key()` — returns `sessions/<uuid>` prefix for upload sessions
- `library_key()` — returns `library/sizzle_library.json`

### Shared lower-level modules

- **`shared.py`** — `parse_transcript_lines(raw_text)`: parses `[M:SS] Speaker: text` lines into dicts with `raw`, `timestamp`, `text`, `seconds`, `minute_bucket`. Used by both `app.py` and `generator_app.py`.
- **`captions.py`** — pure WebVTT builder (no Flask/ffmpeg imports). `collect_caption_lines(all_lines, selected_raws, seg_start, seg_end)` returns the selected respondent lines in a segment's range (interviewer lines excluded). `build_webvtt(segments, title_card_duration=5.0)` walks the reel timeline (each segment = 5s title card + clip) and re-times each caption cue to `seg_start + title_card_duration + (line.seconds - clip_start)`, clamping to the clip end; returns `None` if there are no cues. Captions are **derived from the selected transcript lines, not AI-generated**. Used by `generator_app._run_generation` (server VTT) and the `/plan` cloud path.
- **`loader.py`** — `scan_videos()`: finds `.mp4 .mov .avi .mkv .webm` files in a folder, sorted alphabetically.
- **`transcriber.py`** — `transcribe_video()`: runs the faster-whisper `base` model (CTranslate2, `compute_type="int8"`) with `word_timestamps=True`, splits segments on terminal punctuation via `_split_into_sentences()`. Output format: `[M:SS] Speaker: text`. Transcripts cached as `{video}.txt`; delete the `.txt` to force re-transcription. `transcribe_video(video_path, model=...)` accepts a pre-constructed model.
- **`claude_client.py`** — `query_claude(transcript, prompt)`: sends transcript + prompt to `claude-opus-4-8` with the transcript block prompt-cached (`cache_control: ephemeral`). System prompt instructs Claude to return every relevant `M:SS-M:SS` range scored `|1..10` (or `none`), starting as late and ending as early as possible.
- **`timestamp_parser.py`** — `parse_scored_timestamps()`: extracts `M:SS-M:SS|score` pairs from Claude's response with a regex; missing scores default to 5, out-of-range scores clamp to 1..10.
- **`video_editor.py`** — `extract_clip()`: re-encodes to H.264/AAC with fade-out (required — stream copy produces P/B-frame starts that freeze on playback). `stitch_clips()`: concat demuxer with `-c copy` (safe because clips are I-frame-aligned). `get_video_dimensions()`, `parse_timestamp_to_seconds()`.

## Persistence files (project root, gitignored)

- `sizzle_library.json` — generated reel entries: `id`, `path`, `filename`, `title`, `notes`, `prompt`, `duration_seconds`, `clip_count`, `segment_starts`, `created_at`, `reel_s3_key` (cloud only), `captions_filename` (local `.vtt` sidecar name) / `captions_key` (cloud VTT object key) — both optional, present only when the reel has captions
- `recent_folders.json` — last 5 opened folders: `path`, `video_count`, `last_opened`
- `prompt_history.json` — recent prompts (last 10) and saved templates: `{recent: [], templates: [{name, text}]}`

## Key Behaviours

- **`_filter_generated_reels` is called in every code path** that calls `scan_videos` (`load_folder`, `/transcripts`, `_run_analyze`, `_run_generation`, `/generate`). If you add a new code path that scans videos, add the filter call.
- **Tests mock `_library_add`** in generate-endpoint tests to prevent writing to the real `sizzle_library.json` during pytest. Always add `patch("generator_app._library_add")` (or `patch("app._library_add")`) when writing tests that exercise the generate flow end-to-end.
- **Whisper is lazy-loaded** in the web app (`_get_whisper_model()` with a double-checked lock) so the first `/load-folder` that needs transcription triggers the load.
- **ffmpeg re-encoding** in `extract_clip` uses `-c:v libx264 -preset fast -c:a aac`. Do not switch to `-c copy` for clip extraction — it produces clips that start mid-GOP, causing visible freezes at every transition.
- **Title card ffmpeg quirk:** The `drawtext` filter uses `textfile=` (content written to side-car `.txt` files) and a relative `fontfile=` path with `cwd=tmp_dir`. This avoids Windows drive-letter colons inside filter option strings (ffmpeg 8.x on Windows treats `:` as an option separator even inside quoted values).
- **Library video playback:** `/library-video/<id>` redirects to a presigned R2 URL with a forced `video/mp4` Content-Type (not a Flask byte-proxy) to keep playback off the host's metered bandwidth. This relies on R2 CORS allowing `GET` (`set_cors.py`) and the forced media type to satisfy Chrome's ORB. If playback breaks with `ERR_BLOCKED_BY_ORB`, the safe rollback is to proxy the bytes through Flask again.
- **Test suite runs in `testing=True` mode** which executes generation synchronously so mock patches don't leak across tests.
- **Captions are soft by default, burned only on download.** Generation writes a WebVTT track alongside the reel (local `.vtt` sidecar; cloud object at `captions_key`). The library player shows a **CC toggle** whose on/off choice is remembered in `localStorage` (`sizzle_captions_on`) — it flips the `<track>`'s `textTracks[0].mode`. "Download with captions" hard-burns them: local mode via the server ffmpeg route, cloud mode in-browser via `ReelEncoder.burnCaptions` (`static/reel-encoder.js` — mediabunny `CanvasSink` decode + per-frame `_drawCaption`, re-encode with `CanvasSource`). The Render free tier deliberately does **not** re-encode server-side, which is why cloud burn-in runs in the browser.
