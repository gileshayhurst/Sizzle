# Sizzle Reel Improvements — Design Spec

**Date:** 2026-06-03

## Overview

Four improvements to the Sizzle Reel web app:

1. Enhanced title cards — show video name, clip start time, and segment X/Y
2. Audio desync fix — correct timestamp drift in extracted clips
3. Folder switcher dropdown — click the workspace folder badge to switch folders
4. Segment skip controls — prev/next segment buttons in both video players

---

## 1. Enhanced Title Cards

### Current behaviour
`make_title_card(name, width, height, output_path, duration=5.0)` renders a single centred string. There are two call sites in `_run_generation`:
- Before each source video: passes `vp.stem`
- Between non-contiguous segments in the same video: passes `"Segment {seg_num}"`

### New behaviour
Every title card shows three lines:
- Line 1: video filename stem (e.g. `NOBU_interview`)
- Line 2: clip start time in the source video (e.g. `from 1:23`)
- Line 3: segment position in the reel (e.g. `Segment 3 / 9`)

### Implementation

**`make_title_card` signature change:**
```python
def make_title_card(lines: list[str], width: int, height: int, output_path: str, duration: float = 5.0) -> None:
```
The existing multi-line drawtext logic (one `drawtext` filter per line, vertically centred as a group) handles a list already — this is a signature rename plus call-site updates.

**Compute total segment count before the generation loop:**
```python
total_segs = sum(len(segs) for _, segs in video_segments)
```

**`seg_num` counter:** increments for every content clip extracted (starts at 1). Both the video-name card and the between-segment card use `seg_num` before it increments for that clip.

**`format_start(sec)` helper:** formats seconds as `M:SS` (e.g. `75.0 → "1:15"`).

**Unified card text for every title card:**
```python
card_lines = [vp.stem, f"from {format_start(start_sec)}", f"Segment {seg_num} / {total_segs}"]
make_title_card(card_lines, width, height, card_path)
```

---

## 2. Audio Desync Fix

### Root cause
`extract_clip` currently uses output-side seeking (`-ss` after `-i`). Audio packets don't align to frame boundaries, so the audio start point drifts slightly from the video start. Concatenating many such clips compounds the drift into audible desync.

### Fix
Move `-ss` before `-i` (input-side fast seek) and use `-t duration` instead of `-to end_sec`. Add `-avoid_negative_ts make_zero` so every clip's timestamps start at zero — required for the concat demuxer to stitch without gaps.

```python
# Before
["-i", video_path, "-ss", str(start_sec), "-to", str(end_sec), ...]

# After
["-ss", str(start_sec), "-i", video_path, "-t", str(end_sec - start_sec),
 "-avoid_negative_ts", "make_zero", ...]
```

The re-encode to H.264/AAC (`-c:v libx264 -c:a aac`) is preserved — it corrects any frame-alignment imprecision from the fast seek.

---

## 3. Folder Switcher Dropdown

### Current behaviour
`folder-badge` in the workspace topbar is static text showing `📁 foldername/`.

### New behaviour
Clicking the badge opens an inline dropdown anchored below it. The dropdown contains:
1. Recent folders (from `/recent-folders`, already exists)
2. A "📂 Select new folder..." item at the bottom

Selecting a recent folder calls `openFolder(path)` and closes the dropdown. Selecting "Select new folder" triggers the existing folder-picker button flow. Clicking outside or pressing Escape closes the dropdown.

### Visual affordance
The badge gains a `▾` chevron suffix to indicate interactivity. Cursor changes to `pointer`.

### Implementation
Pure frontend — no backend changes. On badge click:
1. Fetch `/recent-folders`
2. Build a `<div class="folder-dropdown">` with one `<button>` per recent folder + the "Select new folder" item
3. Position it absolutely below the badge (using `getBoundingClientRect`)
4. Attach a one-time `mousedown` listener on `document` to dismiss on outside click
5. Attach a one-time `keydown` listener for Escape to dismiss

---

## 4. Segment Skip Controls

### Tracking segment boundaries
During `_run_generation`, accumulate `cumulative_output_time` (float, seconds) as clips and title cards are appended to `clip_paths`. Record `segment_starts: list[float]` — one entry per content clip — at the moment each content clip is about to be added:

```
cumulative_output_time += 5.0      # title card succeeds → add 5s
segment_starts.append(cumulative_output_time)
cumulative_output_time += (end_sec - start_sec)  # content clip
```

If a title card fails (existing try/except), skip adding its 5.0 seconds. If a content clip fails (existing try/except), skip appending to `segment_starts`.

### Storing boundary data
`segment_starts` is added to the job `result` dict and to the `sizzle_library.json` entry (new field, defaults to `[]` for old entries).

### Player controls
Both the **result player** (`screen-result`) and the **library player** (`library-player-overlay`) get **⏮ Prev** and **⏭ Next** buttons rendered alongside existing controls.

**Skip logic (JS):**
```js
function skipToSegment(video, segmentStarts, direction) {
  const t = video.currentTime;
  if (direction === 'next') {
    const target = segmentStarts.find(s => s > t + 0.5);
    if (target !== undefined) video.currentTime = target;
  } else {
    const targets = segmentStarts.filter(s => s < t - 0.5);
    if (targets.length) video.currentTime = targets[targets.length - 1];
  }
}
```

`segment_starts` is passed into `showResult(result)` (available in job result) and `openLibraryPlayer(entry)` (read from `entry.segment_starts ?? []`).

---

## Files Changed

| File | Changes |
|------|---------|
| `app.py` | `make_title_card` signature; title card call sites; `segment_starts` tracking; result dict; library entry |
| `video_editor.py` | `extract_clip` seek flags |
| `static/app.js` | Folder badge dropdown; skip controls wiring; `showResult` / `openLibraryPlayer` updates |
| `static/style.css` | Dropdown styles; skip button styles |
| `templates/index.html` | Prev/Next button elements in both players |
