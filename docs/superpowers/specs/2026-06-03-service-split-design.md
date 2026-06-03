# Service Split Design
**Date:** 2026-06-03
**Status:** Approved

## Goal

Split the monolithic Flask app into two independent Flask services — an Analysis Service and a Generation Service — so that each can be deployed separately in the future (e.g. on Render) without rewriting logic.

This is a refactoring task only. No deployment is happening now. The two services will run as separate local processes and communicate through the existing shared filesystem and, from the frontend, via direct fetch calls to each service's port.

---

## Project File Hierarchy (Post-Split)

```
Sizzle Reel/
├── app.py                      # Analysis Service (port 5000) — transcription, analysis, folder mgmt
├── generator_app.py            # Generation Service (port 5001) — video stitching, job mgmt, library
│
├── templates/
│   └── index.html              # Single-page frontend — served by Analysis Service
├── static/
│   ├── app.js                  # Frontend JS — updated to route generate/library calls to port 5001
│   └── style.css
│
├── loader.py                   # Shared: scan_videos()
├── transcriber.py              # Shared: transcribe_video()
├── claude_client.py            # Shared: query_claude()
├── timestamp_parser.py         # Shared: parse_timestamps()
├── video_editor.py             # Shared: extract_clip(), stitch_clips(), get_video_dimensions()
│
├── sizzle.py                   # Legacy CLI tool (unchanged)
├── create_test_data.py         # Test data generator (unchanged)
│
├── sizzle_library.json         # Owned by Generation Service — list of generated reels
├── recent_folders.json         # Owned by Analysis Service — last 5 opened folders
│
├── requirements.txt            # Add: flask-cors
├── .env                        # ANTHROPIC_API_KEY
│
├── tests/
│   └── test_app.py             # Updated import paths for generator_app routes
│
└── docs/
    └── superpowers/
        ├── specs/
        │   └── 2026-06-03-service-split-design.md   # This file
        └── plans/
            └── 2026-06-03-service-split.md           # Implementation plan (forthcoming)
```

---

## Service Definitions

### Analysis Service — `app.py` (port 5000)

Handles everything up to and including Claude analysis. Also serves the frontend.

**Routes:**
| Method | Path | Description |
|--------|------|-------------|
| POST | `/load-folder` | Scans folder, triggers transcription, returns video list |
| GET | `/transcripts` | Returns cached transcripts for a folder |
| POST | `/analyze` | Calls Claude on each transcript, returns matched lines |
| GET | `/recent-folders` | Returns last 5 opened folders |

**Owns:**
- Whisper model lazy-loading (`_get_whisper_model`)
- `recent_folders.json` read/write
- `_filter_generated_reels()` — reads `sizzle_library.json` directly (shared local file)

**Imports from shared modules:** `loader`, `transcriber`, `claude_client`, `timestamp_parser`

---

### Generation Service — `generator_app.py` (port 5001)

Handles async video generation, job tracking, and the sizzle reel library.

**Routes:**
| Method | Path | Description |
|--------|------|-------------|
| POST | `/generate` | Starts a generation job, returns job_id |
| GET | `/status/<job_id>` | Polls job progress and result |
| DELETE | `/jobs/<job_id>` | Cancels a running job |
| GET | `/library` | Returns all generated reels |
| POST | `/library/...` | Library management endpoints |

**Owns:**
- `_jobs` dict + `_new_job()`
- `_run_generation` (daemon thread)
- `make_title_card()`
- `_group_lines_into_segments()`
- `_library_add()`, `_library_load()`
- `sizzle_library.json` read/write

**Imports from shared modules:** `loader`, `video_editor`, `timestamp_parser`

**CORS:** Enabled via `flask-cors` so the browser (served from port 5000) can make cross-origin requests to port 5001.

---

## Frontend Changes (`static/app.js`)

Add a single constant at the top of `app.js`:

```js
const GENERATOR_URL = 'http://localhost:5001';
```

All `fetch` calls targeting these paths prepend `GENERATOR_URL`:
- `/generate`
- `/status/<job_id>`
- `/jobs/<job_id>`
- `/library` and any `/library/...` sub-paths

All other fetch calls (load-folder, analyze, transcripts, recent-folders) remain unchanged, targeting the default origin (port 5000).

---

## Local Development

Run two terminal windows simultaneously:

```powershell
# Terminal 1 — Analysis Service
.\venv\Scripts\python.exe -c "from app import create_app; create_app().run(port=5000, debug=True)"

# Terminal 2 — Generation Service
.\venv\Scripts\python.exe -c "from generator_app import create_app; create_app().run(port=5001, debug=True)"
```

Open the app at `http://localhost:5000` as normal.

---

## Error Handling

- **Generation Service unreachable:** The frontend `POST /generate` fetch must catch network errors and display a user-visible message (e.g. "Generation service unavailable") rather than silently hanging. The existing job/status error states handle failures mid-job; this covers the initial connection.
- **Shared files:** Both services read `sizzle_library.json` (Analysis Service reads it for `_filter_generated_reels`; Generation Service owns writes). Since both run locally and writes are infrequent, no locking is needed now. A future deployment will replace this with a database or API call.

---

## Testing

Existing tests that import from `app` and mock `_library_add` or exercise generate-flow routes must be updated:
- Change `patch("app._library_add")` → `patch("generator_app._library_add")`
- Change `app.test_client()` for generate routes → `generator_app.create_app().test_client()`

No logic changes to tests are expected — only import path updates.

---

## What Is Not Changing

- All shared lower-level modules (`loader.py`, `transcriber.py`, `claude_client.py`, `timestamp_parser.py`, `video_editor.py`) — untouched
- `sizzle.py` legacy CLI — untouched
- `templates/index.html` — untouched
- `static/style.css` — untouched
- Persistence file formats (`sizzle_library.json`, `recent_folders.json`) — untouched
- All existing business logic — just moved, not rewritten

---

## Future Deployment Notes (Not In Scope Now)

When deploying to Render or similar:
- Whisper cannot run on small instances — swap `transcriber.py` for a hosted transcription API (e.g. Deepgram, AssemblyAI)
- Video files must move via cloud storage (S3-compatible) since Render's filesystem is ephemeral
- `GENERATOR_URL` in `app.js` becomes the deployed Generation Service URL
- `sizzle_library.json` and `recent_folders.json` are replaced by a database
- CORS config on the Generation Service is updated to allow the deployed frontend origin
