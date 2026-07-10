# Priority-Scored Analysis + Reel-Length Slider — Design

**Date:** 2026-07-10
**Status:** Approved for planning

## Problem

Sizzle reels are meant to portray only the highlights — the most compelling
pieces of evidence. In practice generated reels have run 8+ minutes because
`/analyze` selects every correlated segment and the reel includes all of them.
A sizzle reel should be ~2–3 minutes.

Two changes fix this:

1. **A priority system.** Not everything correlated is included — only the best
   evidence. Claude scores each candidate segment by how compelling it is.
2. **A reel-length slider.** Appears after analysis. Its range is derived from
   the segments that *could* be selected. An **optimal** marker (reasonably
   short, set from the best evidence) is where it starts. Dragging it up or down
   adds or removes segments in priority order.

## Non-Goals

- No change to the generation pipeline, clip ordering, title cards, or output
  format. Priority governs **inclusion only**; the reel still plays per-video in
  chronological order.
- No new JS test framework (the repo has none today).
- No backend endpoint for the slider — selection math is client-side.

## Key Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Priority source | Claude scores every relevant segment in one pass (no extra API call) |
| Slider granularity | Whole segments; slider snaps to segment boundaries |
| Optimal definition | Quality bar: all segments scoring ≥ 8, with a ~180s soft cap |
| Manual edits vs. slider | Manual line edits allowed; moving the slider recomputes a pure priority prefix and discards manual tweaks |
| Add-analyze interaction | Additive — new segments join the pool; slider range/optimal recompute over the merged pool |
| Computation location | Frontend, over the scored segment pool returned by `/analyze` |
| Slider UI | Option A — simple slider row in the `.analyze-zone`, amber fill, ◆ optimal marker, min/max labels |

---

## Component 1 — Scored analysis (backend)

### `claude_client.py`

Change the system prompt so Claude returns **every** segment that genuinely
addresses the topic, each with a compellingness score, instead of only "the 2–4
most substantive."

- **Response format:** one segment per line, `M:SS-M:SS|N` where `N` is an
  integer 1–10. Multiple segments separated by newlines (commas also tolerated
  by the parser). `none` when nothing is relevant (unchanged).
- **Score rubric** (added to the prompt):
  - **9–10** — direct, vivid, quotable evidence; the strongest possible moment.
  - **7–8** — clearly on-topic and substantive.
  - **5–6** — relevant but ordinary.
  - **1–4** — passing mention.
- **All existing rules stay verbatim:** interviewer/moderator exclusion,
  start-as-late / end-as-early, verbatim-only timestamps, positive-sentiment
  filtering when the prompt asks for positive opinions, subject-must-be-primary.
- The "return only 2–4" instruction is removed; Claude now returns the full
  relevant set so the client can rank and threshold.

### `timestamp_parser.py`

`parse_timestamps()` currently returns `list[str]` of `M:SS-M:SS`. Add a
score-aware parse **without breaking the existing signature**:

- Add `parse_scored_timestamps(response: str) -> list[tuple[str, int]] | None`
  returning `(range, score)` pairs. A range with no `|N` suffix defaults to
  **score 5**. A malformed score (non-integer, out of 1–10) clamps to 1–10 or
  defaults to 5. `none` → `None`.
- Keep `parse_timestamps()` as-is (used elsewhere / as a fallback). It may be
  implemented in terms of the scored parser (drop the scores).

### `app.py` — `_run_analyze`

Maps ranges to transcript lines exactly as today (including the interviewer
skip and the ±0.5s tolerance window). New return shape:

```json
{
  "segments": {
    "video1.mp4": [
      {"start": "0:24", "end": "0:31", "start_seconds": 24.0,
       "end_seconds": 31.0, "duration_seconds": 7.0, "score": 9,
       "lines": ["[0:24] Agent: ...", "[0:27] Agent: ..."]}
    ]
  },
  "highlights": { "video1.mp4": ["[0:24] Agent: ...", ...] }
}
```

- `segments[file]` — one entry per scored range that mapped to ≥ 1 respondent
  line, sorted by start time within the file.
- `highlights[file]` — retained for backward compatibility: the union of all
  `lines` across that file's segments (the current response contract). Existing
  tests and any non-slider fallback path keep working.
- **Segment hygiene:** a range that maps to zero respondent lines (e.g. entirely
  interviewer) is dropped. Note the generator *extends* clips shorter than
  `MIN_CLIP_SECONDS` (1.5s) to that floor rather than rejecting them, so the
  pool does **not** need to pre-filter on that floor; only truly empty
  (zero-line) segments are dropped.

---

## Component 2 — Priority model & selection math (frontend, pure functions)

Add small, individually testable pure functions to `app.js`. No DOM, no
network — deterministic given the pool.

### Candidate pool

Flatten `segments` across all files into one array. Each candidate carries:
`file`, `score`, `duration_seconds`, `start_seconds`, `lines`.

### Priority order

Sort the pool into a single deterministic order:

1. `score` descending
2. `duration_seconds` ascending (more evidence per second first)
3. file order (as in `state.files`)
4. `start_seconds` ascending

**Selection at a slider position is the greedy prefix** — the first *K*
candidates in priority order. Therefore the only achievable durations are the
cumulative sums of segment durations along the priority order, and the slider
snaps to exactly those cumulative points.

### Optimal point

1. Qualifying set = all candidates with `score ≥ 8`.
2. If none score ≥ 8, fall back to all candidates sharing the highest score
   present in the pool.
3. If the qualifying set's total duration exceeds the **180s soft cap**, remove
   its lowest-priority members (from the end of the priority order) until it
   fits — but never below one segment.
4. Optimal duration = total duration of the resulting set. Because the set is a
   priority prefix by construction, it corresponds to a valid slider snap point.

### Slider range

- **min** = duration of the single highest-priority segment (first prefix).
- **max** = total duration of the entire pool.
- If the pool has exactly one segment, min == max == optimal (slider is inert /
  hidden — see UI).

### Selection application

Given a chosen duration, compute the priority prefix whose cumulative duration
is the largest snap point ≤ the chosen value (but always ≥ 1 segment). The
selected lines are the union of that prefix's `lines`. Apply to **both**
`state.checked[file]` and `state.highlighted[file]` (as `runAnalyze` does today)
so mode-switching preserves the selection.

---

## Component 3 — Slider UI (Option A)

### Markup (`templates/index.html`)

A new `#reel-length-row` inside `.analyze-zone`, after `#analyze-add-row`,
`hidden` until an analyze completes and yields ≥ 2 candidate segments.

```
#reel-length-row
  .reel-length-status   → "Reel length · 2:10 · 9 of 21 segments"
  .reel-slider-wrap
    input#reel-slider[type=range]   (min/max in seconds, step small)
    .reel-optimal-marker (◆, positioned at optimal fraction)
  .reel-slider-ends → min label (left) · max label (right)
```

Native `<input type="range">` for keyboard accessibility (WCAG AA per
DESIGN.md). On `input`, snap the raw value to the nearest cumulative snap point,
recompute the prefix, update the selection, re-render transcript + badges, and
update the status line. Debounce transcript re-render if needed for smoothness.

### Styling (`static/style.css`)

Follow **DESIGN.md** ("The Bright Studio"): light chrome, **Studio Amber**
accent for the filled portion of the track and the ◆ marker. Use design tokens,
not literal hex. Must render correctly in the existing light theme surfaces.

### Behavior

- **After analyze:** selection = optimal prefix; thumb sits on the ◆; status
  shows the optimal length and segment count.
- **Dragging:** recomputes the pure priority prefix for the snapped duration;
  discards any manual edits; status updates live.
- **Manual line edit afterwards** (checkbox/highlight click): selection diverges
  from any prefix. Status switches to *"Custom selection · drag slider to
  reset"*; thumb dims. Next slider drag returns to pure-priority behavior.
- **Fewer than 2 candidates:** row stays hidden; behaves like today (optimal =
  the single segment or the existing selection).

---

## Component 4 — Additive analyze (`+ Add to selection`)

`runAddAnalyze` currently unions new highlight lines into the selection. New
behavior:

1. Parse the new prompt's scored segments and **merge into the pool.** When a
   new range overlaps an existing candidate in the same file, dedupe keeping the
   **higher score**.
2. Union the new qualifying (`score ≥ 8`) segments' lines into the current
   selection (preserves today's "add" intent).
3. Recompute slider **range and optimal** over the merged pool. Because the
   resulting selection is generally no longer a single priority prefix, set the
   slider to the **custom** state. Dragging re-ranks across the full merged pool.

---

## Component 5 — Persistence

Today `localStorage["sizzle_sel_<folder>"]` persists selected lines. Add
`localStorage["sizzle_pool_<folder>"]` persisting the candidate pool plus the
current slider position (or `custom` flag), so the slider and its range survive
a page reload. Both keys are cleared by `_clearSelections()` after a successful
generation (as today).

---

## Component 6 — Error handling

- **Missing/garbled score** → default 5; never a hard failure.
- **`none` everywhere** → no slider; existing empty state.
- **A file's analyze call errors** → contributes no candidates; other files
  proceed (existing behavior).
- **Empty (zero-line) segment** → dropped at pool-build time so the slider never
  offers a segment that would map to nothing.
- **Malformed `/analyze` JSON / timeout** → existing error banner path unchanged.

---

## Component 7 — Testing (pytest)

New/updated tests in `tests/`:

- `parse_scored_timestamps`: with suffix, without suffix (→ 5), garbled suffix
  (→ clamp/5), `none` (→ None), commas and newlines.
- `parse_timestamps`: unchanged behavior (regression).
- `/analyze` response includes `segments` with the documented shape, correct
  score, and correct line mapping per range.
- `highlights` equals the union of `segments[file]` lines (contract preserved).
- Interviewer lines still excluded from segment `lines`.
- Zero-line segment dropped from `segments`.
- Continue to `patch("app._library_add")` where generate-adjacent paths are
  exercised (per CLAUDE.md).

The frontend selection math (priority sort, prefix-at-duration, optimal
computation) is specified precisely enough above to verify by hand and by
inspection; it is factored into pure functions to keep it reviewable. No JS test
framework is added.

---

## Files Touched

- `claude_client.py` — scored system prompt + response format.
- `timestamp_parser.py` — `parse_scored_timestamps`.
- `app.py` — `_run_analyze` returns `segments` (+ retained `highlights`).
- `static/app.js` — pool build, priority/optimal math, slider wiring, add-analyze
  merge, persistence.
- `templates/index.html` — `#reel-length-row`.
- `static/style.css` — slider styling (DESIGN.md tokens).
- `tests/test_app.py` (and/or a new test module) — parser + `/analyze` tests.
