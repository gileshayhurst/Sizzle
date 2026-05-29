# Two-Stage UI: Analyze + Generate Separation — Design Spec

## Goal

Separate the Sizzle Reel workflow into two explicit stages: **Analyze** (Claude identifies relevant segments and auto-highlights them) and **Generate** (user-curated highlights are stitched into a reel with segment transitions). The user can review and edit highlights between the two stages.

## Architecture

The existing single `/generate` endpoint (which calls Claude then extracts clips in one shot) is split into two endpoints: `/analyze` (Claude call only, returns highlighted line sets) and a modified `/generate` (no Claude call, derives clip ranges directly from selected lines). A new "analyze bar" appears above the transcript scroll in the workspace. Segment title cards are inserted between non-contiguous selected clusters within a single video, in addition to the existing video-name title cards between different source videos.

## UI Layout Changes

### Analyze bar
A new horizontal strip is inserted between the transcript header (`#transcript-header`) and the transcript scroll (`#transcript-scroll`) in the workspace main panel. It contains:
- A full-width text input: placeholder "Describe what you're looking for…" (`id="analyze-input"`)
- An **Analyze** button (`id="btn-analyze"`, primary blue style)

The topbar "Analyze Everything" button is removed. The analyze bar replaces it.

### Footer simplification
The footer retains only:
- Output filename input
- **Generate Reel** button

The prompt input is removed from the footer entirely.

The Generate Reel button is disabled (greyed out) when zero lines are selected across all files.

### Checkbox mode (restored hybrid layout)
Each line in checkbox mode gets its own individual checkbox. The minute-group header retains its checkbox as a "select all / deselect all" toggle for that minute. States:
- **Minute header unchecked**: no lines in the group are checked
- **Minute header indeterminate** (–): some but not all lines checked
- **Minute header checked** (✓): all lines checked

Clicking the header toggles all lines in the group. Clicking an individual line checkbox toggles only that line and updates the header state.

## Backend: New `/analyze` Endpoint

`POST /analyze`

**Request body:**
```json
{ "folder": "/path/to/videos", "prompt": "best bites of black cod" }
```

**Behaviour:**
1. Scan videos in folder (same `scan_videos()` call).
2. For each video that has a `.txt` transcript, call `query_claude(transcript, prompt)` and `parse_timestamps(response)`.
3. For each returned `M:SS–M:SS` range, collect all transcript lines whose `seconds` value falls within `[start_sec, end_sec]` (inclusive of endpoints ±0.5 s tolerance).
4. Return per-video sets of matching raw line strings.

**Response:**
```json
{
  "highlights": {
    "video1.mp4": ["[0:05] Speaker: text…", "[0:10] Speaker: more text…"],
    "video2.mp4": []
  }
}
```

**Error handling:** If Claude returns an error for one video, log it and continue; that video gets an empty highlight set. If all videos error, return HTTP 500.

**Frontend behaviour on Analyze:**
- POST to `/analyze` (awaited directly — no job polling needed since it's just API calls, typically <5 s per video).
- Show a loading state: analyze button text becomes "Analyzing…", input disabled.
- On response: replace `state.highlighted[file]` or `state.checked[file]` for every file with the returned sets (replaces any prior selection, including manual edits — this is intentional: Analyze is a fresh query).
- Re-render the active transcript to show auto-selections.
- Store the prompt text in `state.lastPrompt`.
- On error: show an inline error message below the analyze bar.

## Backend: Modified `/generate` Endpoint

`POST /generate` — request body unchanged (`folder`, `mode`, `selections`, `prompt`, `output_filename`).

**Key change: no Claude call.** `_run_generation` now derives clip ranges from the provided selections directly.

### Clip grouping algorithm

For each video with selected lines:

1. Look up the full ordered transcript lines for that video (read from the `.txt` file).
2. Mark which lines are selected (appear in `selections[video_name]`).
3. Scan the transcript in order. Group consecutive selected lines where no unselected line appears between them into a **segment**. Any unselected line between two selected lines starts a new segment.
4. For each segment:
   - `start_sec` = `seconds` of the first line in the segment
   - `end_sec` = `seconds` of the first line **after** the segment in the full transcript (the next line, selected or not). If the segment ends at the last transcript line, `end_sec = last_line.seconds + 10`.
5. Pass `(start_sec, end_sec)` to `extract_clip`.

### Segment title cards

A **global segment counter** `seg_num` starts at 1 and increments across the whole reel.

Title card insertion rules (in order):
- Before the first clip of each source video **except the very first**: insert a video-name title card (existing behaviour, `vp.stem` as name).
- Between any two consecutive clips **from the same source video** (i.e., between segment N and segment N+1 within that video): insert a "Segment {seg_num}" title card. Increment `seg_num` after inserting.

Note: `seg_num` increments only when a segment card is inserted between gaps within a video. The counter does **not** increment for cross-video transitions (those get the video-name card instead).

**Example reel for NOBU (2 segments) → Review (2 segments):**
```
[clip NOBU-seg1] → [Segment 1 card] → [clip NOBU-seg2] →
[NOBU title card] →  ← NO: this is wrong; video title card goes BEFORE first clip of that video
```

Correct order:
```
[clip NOBU-seg1] → [Segment 1] → [clip NOBU-seg2] → [Review title card] → [clip Review-seg1] → [Segment 2] → [clip Review-seg2]
```

`seg_num` starts at 1 per reel generation (not persisted across runs).

### Prompt for library
`_run_generation` receives `prompt` in its signature (unchanged). The value passed is `state.lastPrompt` from the frontend (set when Analyze was last run). If the user never ran Analyze, the value is whatever is in the analyze input box at generate time (may be empty string — stored as `""` in library).

## Bug Fixes

### Stacked event listeners (highlight mode)
**Root cause:** `renderHighlightMode` sets `scroll.innerHTML = ''` (removing line elements) but adds new `mousedown` and `mousemove` listeners on `scroll` itself every call. After N calls, N listeners fire per event. Each listener re-reads `hl.has(raw)` from the already-mutated Set, causing the toggle to flip an even number of times — line ends up in its original state (appears to do nothing).

**Fix:** Use an `AbortController`. Store it on the render function's closure (or on `state`). Call `controller.abort()` before each render and create a fresh `AbortController`. Pass `{ signal: controller.signal }` to `addEventListener` calls so the previous listeners are automatically removed.

### Checkbox mode full re-render on click
**Root cause:** Every click on a minute-header calls `renderCheckboxMode`, destroying and recreating all DOM elements including their event listeners. This is wasteful and will compound with per-line checkboxes.

**Fix:** Render the checkbox tree once per file-select. On clicks, mutate the DOM in place: toggle the `checked`/`indeterminate` CSS class on the affected `cb-box` elements, and update `state.checked` — no full re-render. Only re-render when switching files or switching modes.

## State Model Changes

```js
state = {
  // existing fields unchanged
  lastPrompt: '',   // NEW: prompt used for the most recent Analyze call
}
```

`state.highlighted` and `state.checked` continue to be the source of truth for Generate. Both are Sets of raw line strings per filename.

## Files Changed

- `app.py` — add `_run_analyze()` + `/analyze` route; modify `_run_generation` (remove Claude call, add clip grouping, add segment title cards)
- `templates/index.html` — add analyze bar markup, remove footer prompt input
- `static/app.js` — analyze flow, AbortController fix, checkbox per-line restore, generate-button enabled state, `state.lastPrompt`
- `static/style.css` — analyze bar styles, re-enable per-line checkbox styles
- `tests/test_app.py` — tests for `/analyze` endpoint and new clip grouping logic
