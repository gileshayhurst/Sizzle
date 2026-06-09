# Performance, Library, and Persistence Improvements — Design Spec

**Date:** 2026-06-08

## Overview

Four improvements to the Sizzle Reel web app:

1. **Parallel clip extraction** — extract ffmpeg clips concurrently to reduce generation time
2. **WebSocket generation updates** — replace 2-second polling with push updates during generation
3. **Library delete and edit** — remove reels from the library and rename/annotate them
4. **Selection persistence** — save and restore checkbox/highlight selections across sessions

---

## 1. Parallel Clip Extraction

### Current behaviour
`_run_generation` in `generator_app.py` loops over `video_segments` and calls `make_title_card` then `extract_clip` serially for each segment. Clip extraction is the bottleneck — each ffmpeg re-encode blocks the next from starting.

### New behaviour
Generation splits into three phases:

**Phase 1 — Plan.** Walk `video_segments` and build an ordered `plan` list. Each item is a dict tagged `"title"` or `"clip"` with all parameters needed to produce it (paths, timestamps, card text). No ffmpeg runs during this phase.

**Phase 2 — Execute.** Iterate the plan:
- `"title"` items: call `make_title_card(...)` immediately (fast, ~0.1s). Record the output path on the item.
- `"clip"` items: submit `extract_clip(...)` to a `ThreadPoolExecutor` with `max_workers=min(4, os.cpu_count() or 4)`. Store the `Future` on the item.

Then wait: iterate the plan again and call `.result()` on each clip future. If the job cancel event is set between `.result()` calls, cancel remaining futures and exit early. If a clip raises, log the error and mark the item failed (consistent with existing error-handling behaviour). Progress increments (`job["done"] += 1`) happen as each future completes.

**Phase 3 — Stitch.** Collect `item["path"]` from the plan in original order, filtering out failed items, and pass to `stitch_clips(...)` unchanged.

### Worker count
`min(4, os.cpu_count() or 4)` — 4 concurrent ffmpeg re-encodes saturates most machines without thrashing. Machines with fewer cores are handled by OS scheduling.

### Files changed
- `generator_app.py` — `_run_generation` function only

---

## 2. WebSocket for Generation Progress

### Current behaviour
After `POST /generate`, the frontend calls `setInterval` to poll `GET /status/<job_id>` on `generator_app.py` every 2 seconds. This adds up to 2 seconds of lag per update and generates constant HTTP traffic.

### New behaviour
`generator_app.py` exposes a WebSocket endpoint. After starting a generation job the frontend opens a WebSocket connection and receives push updates as they happen.

### Dependency
Add `flask-sock` to `requirements.txt`. Minimal library — one decorator, no Socket.IO protocol overhead.

### Backend (`generator_app.py`)

New route: `@sock.route('/ws/job/<job_id>')`

The handler loops at 200ms intervals and sends JSON messages:
- `{"type": "log", "message": "..."}` — one message per new log line since last check
- `{"type": "progress", "done": N, "total": N}` — sent each iteration
- `{"type": "done", "status": "done"|"error"|"cancelled", "result": {...}, "error": "..."}` — sent once when the job reaches a terminal state, then the connection closes

The existing `GET /status/<job_id>` endpoint is **not removed** — it is used by tests and the cancel flow.

### Frontend (`static/app.js`)

`pollGeneration(jobId)` is replaced by `watchGeneration(jobId)`.

- Constructs the WebSocket URL from `GENERATOR_URL`, swapping `http`/`https` → `ws`/`wss`
- Handles three message types matching the backend above
- On `"done"` status: calls existing `showResult(result)`
- On `"error"` or `"cancelled"`: shows existing error display
- `ws.onerror`: closes gracefully with a user-facing error message

Cancel flow is unchanged — `DELETE /jobs/<job_id>` remains a plain HTTP call.

**Cloud mode:** `GENERATOR_URL` in cloud mode is an `https://` Render URL. The `https` → `wss` swap handles this automatically.

### Files changed
- `generator_app.py` — add `flask-sock` init, new WS route
- `requirements.txt` — add `flask-sock`
- `static/app.js` — replace `pollGeneration` with `watchGeneration`

---

## 3. Library Delete and Edit

### Data model changes

Two new optional fields added to each `sizzle_library.json` entry:

| Field | Type | Default if absent |
|-------|------|-------------------|
| `title` | string | `filename` value |
| `notes` | string | `""` |

Old entries without these fields work unchanged. The frontend treats missing fields as their defaults.

### Backend (`app.py`)

**`DELETE /library/<id>`**
- Optional query param `?delete_file=true`
- Loads library, finds entry by `id`, removes it, saves library back
- If `delete_file=true` and file path exists on disk, deletes the `.mp4` file (uses `storage` in cloud mode)
- Returns `{"ok": true}` on success, 404 if id not found

**`PATCH /library/<id>`**
- Accepts JSON body `{"title": "...", "notes": "..."}`
- Finds entry, updates only `title` and `notes` fields, saves
- Returns the updated entry
- Ignores unknown keys

### Frontend (`static/app.js`, `static/style.css`, `templates/index.html`)

Each library card gets two icon buttons in its top-right corner: pencil (edit) and trash (delete).

**Delete flow:**
1. Clicking trash replaces the card's action area with an inline confirmation showing three options: "Remove from library", "Also delete file", and "Cancel"
2. "Remove from library" calls `DELETE /library/<id>`
3. "Also delete file" calls `DELETE /library/<id>?delete_file=true`
4. On success the card fades out and is removed from the DOM
5. "Cancel" restores the normal card view

**Edit flow:**
1. Clicking pencil transforms the card's title and notes area into an inline form: a text input pre-filled with `title ?? filename`, and a `<textarea>` pre-filled with `notes`
2. Save and Cancel buttons appear
3. On Save: calls `PATCH /library/<id>`, updates the card DOM in place, exits edit mode
4. Pressing Escape cancels without saving

**Library player overlay:** title display updated to show `entry.title ?? entry.filename` so renamed reels display their friendly name.

### Files changed
- `app.py` — `DELETE /library/<id>` and `PATCH /library/<id>` endpoints
- `static/app.js` — delete/edit button wiring, confirmation and inline edit UI
- `static/style.css` — card button styles, inline form styles, fade-out animation
- `templates/index.html` — pencil/trash button elements on the library card template

---

## 4. Selection Persistence

### Current behaviour
`state.checked` and `state.highlighted` are initialised empty on every page load. Closing the browser or refreshing loses all selections.

### New behaviour
Selections are automatically saved to `localStorage` on every change and restored when the same folder is opened again.

### Storage format

**Key:** `sizzle_sel_<folder>` where `<folder>` is the raw folder path string as used in `state.folder`.

**Value (JSON):**
```json
{
  "checked":     { "video1.mp4": ["[0:12] Speaker: text", ...], ... },
  "highlighted": { "video1.mp4": ["[0:12] Speaker: text", ...], ... }
}
```

`Set` values are serialised as arrays of raw line strings and restored as `new Set(array)`.

### Save

A `_saveSelections()` helper writes the current `state.checked` and `state.highlighted` to localStorage. Called after every mutation:
- `runAnalyze()` applies highlights
- Checkbox toggle
- Highlight drag end
- "Check all" / "Clear all"

### Load

Called at the end of `loadTranscripts()`, after `state.files` is populated. Reads from localStorage, then for each file present in the current file list restores `checked` and `highlighted` as Sets. Files no longer present in the folder are silently skipped. Malformed JSON is silently ignored.

### Expiry
No automatic expiry. localStorage persists until the user clears browser storage. Data is small (arrays of short strings keyed per folder) and stale entries accumulate without affecting anything.

### Files changed
- `static/app.js` — `_saveSelections()` helper, load call in `loadTranscripts()`, save calls at each mutation site

---

## Files Changed Summary

| File | Changes |
|------|---------|
| `generator_app.py` | Parallel extraction (three-phase plan/execute/stitch), `flask-sock` init, WS route |
| `requirements.txt` | Add `flask-sock` |
| `app.py` | `DELETE /library/<id>`, `PATCH /library/<id>` |
| `static/app.js` | `watchGeneration`, delete/edit wiring, `_saveSelections`, load/restore |
| `static/style.css` | Card button styles, inline edit form, fade-out animation |
| `templates/index.html` | Pencil/trash button elements on library cards |

---

## What Does Not Change

- `GET /status/<job_id>` — kept for tests and the cancel flow
- All existing tests
- Transcription polling (stays on 2-second interval — short enough that WebSocket isn't needed)
- The clip extraction, stitching, and title card logic itself — only the execution order changes
- Cloud mode behaviour for all features other than the library file-delete path
