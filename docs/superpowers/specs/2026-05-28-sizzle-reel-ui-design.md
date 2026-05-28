# Sizzle Reel — Local Web UI Design Spec
**Date:** 2026-05-28

## Overview

A local browser-based interface for the existing Sizzle Reel pipeline. The user opens a Flask app in their browser, picks a folder of videos, selects which transcript content to analyze (via checkboxes or line highlights), enters a prompt, and watches the generated reel play back in the same window. A persistent library tracks all past reels.

---

## Tech Stack

- **Backend:** Flask (Python), background threads for long-running operations, polling for progress
- **Frontend:** Single HTML page with vanilla JS — no framework
- **Persistence:** `sizzle_library.json` in the app directory tracks generated reels

---

## Page Structure

### Top Navigation

Two tabs always visible in the topbar:

- **✦ Create** — the main workspace (transcript selection + generation)
- **📼 Library** — grid of all past reels

### Topbar (Create view)

Left-to-right: logo, active folder badge, **Analyze Everything** button, mode toggle (Checkbox | Highlight).

- The folder badge updates when a new folder is loaded.
- **Analyze Everything** bypasses all line-level selection and sends the full unfiltered transcripts from all videos to Claude.
- The mode toggle switches all transcript views simultaneously.

---

## Folder Loading & Transcription

1. On load, a folder picker is shown: a text field displaying the current path and a **Browse** button.
2. Clicking **Browse** calls a Flask endpoint that opens a native OS folder dialog via `tkinter.filedialog.askdirectory()` and returns the selected path. The user can also type or paste a path directly into the text field.
3. Flask scans the folder for video files (`.mp4`, `.mov`, `.avi`, `.mkv`, `.webm`).
4. Any video lacking a `.txt` transcript is transcribed in a background thread using Whisper.
5. A progress screen shows transcription status ("Transcribing 2/3 videos...").
6. Once all transcripts are ready, the Create workspace appears.
7. On subsequent runs, pre-existing `.txt` files are reused — no re-transcription.

---

## Create Workspace Layout

**Sidebar (left, 170px):** Lists all video files in the folder, sorted alphabetically. Each entry shows the filename and a badge counting how many lines are checked/highlighted. Clicking an entry loads that transcript in the main panel.

**Main panel (right):** Shows the transcript for the selected file in the active mode.

**Footer (bottom of main panel):**

| Field | Description |
|---|---|
| Output filename | Editable text input; defaults to `{folder_name}_sizzle.mp4` |
| Prompt | Free-text input for the Claude prompt |
| Generate Reel | Submits the job |

---

## Checkbox Mode

- Transcript lines are grouped into 1-minute buckets (0:00–1:00, 1:00–2:00, etc.).
- Each line has a checkbox on its left. Clicking toggles a green check.
- Each minute-group has a **check all** link (checks every line in that group).
- The transcript header has a **check all** link (checks every line in the file).
- Sidebar badge shows the count of checked lines for that file.
- Only checked lines are included in the filtered transcript sent to Claude.

---

## Highlight Mode

- Every transcript line is individually clickable — clicking toggles an amber highlight.
- **Click and drag** in any direction brushes highlight across multiple lines as the cursor passes over them (same feel as selecting text), with auto-scroll when dragging near the top or bottom edge.
- Each transcript header has a **highlight all** link.
- Sidebar badge shows the count of highlighted lines for that file.
- Only highlighted lines are included in the filtered transcript sent to Claude.

---

## How Selection Feeds Generation

When the user clicks **Generate Reel**, the frontend sends:

- The active mode (`checkbox` or `highlight`, or `all` if Analyze Everything was used)
- Per-file: the list of selected transcript lines (timestamps + text)
- The prompt string
- The output filename

Flask rebuilds a filtered transcript string from the selected lines and passes it to the existing `query_claude()` function. The rest of the pipeline (`parse_timestamps`, `extract_clip`, `stitch_clips`) runs unchanged.

---

## Generation Progress

- Job runs in a background thread; frontend polls `/status/<job_id>` every 2 seconds.
- A progress bar and live log show per-video status:
  - `✓ NOBU.mp4 — found segments: 0:05–0:38, 1:12–1:52`
  - `⟳ WingReactions.mp4 — analyzing...`
  - `· Extracting clips...`
- A **Cancel** button stops the background thread and cleans up temp files.

---

## Result & Playback

When generation completes:

- The progress screen is replaced by an embedded HTML5 `<video>` player.
- Flask serves the output file through a `/video/<path>` endpoint so the browser can stream it.
- Below the player: a **New Reel** button (returns to the transcript selection view with the same folder loaded) and an **Open Folder** button (opens the output folder in Windows Explorer via `os.startfile`).
- The output file is saved to the input folder using the filename from the footer input.
- The reel is added to the library automatically on completion.

---

## Library

Accessed via the **📼 Library** tab. Persisted to `sizzle_library.json` in the app directory.

**Grid layout:** 3 columns, newest first (sortable). Each card shows:

- Thumbnail area with play icon and duration
- Filename
- Date, clip count, source folder
- Prompt used (italicised)
- Actions: **▶ Play** (opens embedded player), **📂 Show** (opens folder in Explorer), **🗑 Delete** (removes entry from library; does not delete the file)

**Library entry schema:**
```json
{
  "id": "uuid",
  "filename": "NOBU_sizzle.mp4",
  "path": "C:/Users/giles/Downloads/NOBU/NOBU_sizzle.mp4",
  "source_folder": "NOBU/",
  "prompt": "best bites of black cod",
  "duration_seconds": 47,
  "clip_count": 3,
  "created_at": "2026-05-28T14:32:00"
}
```

---

## Flask Routes

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/` | Serve main HTML page |
| `POST` | `/load-folder` | Scan folder, start transcription, return job ID |
| `GET` | `/status/<job_id>` | Poll job progress (transcription or generation) |
| `POST` | `/generate` | Start generation job, return job ID |
| `GET` | `/video/<path:filepath>` | Stream a video file to the browser |
| `GET` | `/library` | Return all library entries as JSON |
| `DELETE` | `/library/<id>` | Remove a library entry |

---

## File Structure

```
Sizzle Reel/
├── sizzle.py               # existing CLI (unchanged)
├── app.py                  # NEW: Flask app entry point
├── sizzle_library.json     # NEW: persisted library (auto-created)
├── static/
│   ├── app.js              # NEW: frontend JS
│   └── style.css           # NEW: frontend CSS
├── templates/
│   └── index.html          # NEW: single-page HTML template
├── claude_client.py        # existing (unchanged)
├── loader.py               # existing (unchanged)
├── timestamp_parser.py     # existing (unchanged)
├── transcriber.py          # existing (unchanged)
├── video_editor.py         # existing (unchanged)
└── ...
```

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| No videos in selected folder | Show inline error message in folder picker |
| Transcription fails for one video | Log warning in progress panel, continue with others |
| No segments found for a video | Log in progress panel, skip; don't abort generation |
| Claude API error | Show error in progress log, mark job failed |
| ffmpeg not found | Show error before job starts |
| Library JSON corrupted | Reset to empty array, log warning to console |

---

## Out of Scope

- Multi-user access or authentication
- Remote/cloud deployment
- Editing or trimming clips after generation
- Saving selection state between sessions
