# Reel-form rework — design

**Date:** 2026-07-16
**Status:** Approved (pending spec review)

## Goal

Rework the general form of generated reels toward the market-research norm:
**1–3 minute total runtime, made of shorter, punchier clips.** Two levers:

1. Trim each clip down to the "meat" (~5–12s money quote) instead of the whole
   on-topic span.
2. Replace the 5-second title cards with a 0-second text overlay burned onto the
   clip, with per-reel control over what identifying info appears.

## Research backing (why these numbers)

- Total reel: **1–2 min ideal, 1–3 min acceptable**; research showreels tolerate
  up to ~5 min. Shorter is better.
- Clip length in a *research* context is governed by a complete spoken thought —
  realistically **~5–15s**, not the 1–3s marketing-sizzle norm (sub-comprehension
  clips defeat the purpose).
- Transitions: **plain hard cuts** are the norm; flashy effects are discouraged.
  The real "transition" device in research reels is a **title/name card**, not a
  visual wipe.

## Corrected diagnosis (important context)

An earlier hypothesis — "Claude picks a tight range and the generator discards
it" — is **false**. Real transcripts show a transcript line is a short ~2–3s
sentence. A 30s clip forms when `_group_lines_into_segments`
(`generator_app.py`) merges a run of consecutive on-topic lines, and those lines
are consecutive because **Claude marked that whole ~30s span as relevant**.
Claude's range and the clip length are essentially the same. So the lever for
30s→8s is making the *selected region tighter than "everything on-topic"* —
achieved here by tightening what Claude returns.

## Design

### 1. Clip trimming — tighten the analyze prompt

Change **only** the system prompt in `claude_client.py`. Today it returns every
on-topic range as wide as the respondent stays on-topic. New instruction: for
each relevant region, return **the single most compelling sub-range — the money
quote, one to two sentences, as tight as possible (typically ~5–12s)** — still
scored 1–10, still multiple ranges per transcript allowed.

Everything downstream is unchanged: line mapping (`app.py` `_run_analyze`),
grouping (`generator_app.py` `_group_lines_into_segments`), and the tail-trim
heuristic. Clips come out short because the ranges come out short. **No payload,
plumbing, or caption-collection changes.** This is the entire trim feature.

Manual (checkbox/highlight) selection is unaffected — it is user-driven and has
no AI range.

### 2. Title cards → 0-second text overlays

Delete the separate title-card generation on both render paths and burn the
label onto the clip instead.

**Local (`video_editor.py` + `generator_app.py`):**
- Add `drawtext` to `extract_clip`'s existing `-vf` chain (the clip already
  re-encodes, so the overlay is free — no extra pass). `extract_clip` gains
  optional params: `title_lines`, `font_path`, and fade timing.
- Delete `make_title_card` and the title-card branch of the Phase 1/2/3 pipeline.
  The plan becomes a flat list of clips — no title+clip pairs, no serial
  title-card pass. Net deletion.
- Reuse the `textfile=` + `cwd=tmp_dir` colon workaround (from the deleted
  `make_title_card`) so Windows drive-letter colons don't break the filter.

**Cloud (`static/reel-encoder.js`):**
- Delete `_encodeTitleCard`.
- In the existing clip-draw loop, draw the label per-frame the same way
  `_drawCaption` already does, reusing the fade logic from `_encodeTitleCard`.
- `total = segments.length` (drop the `× 2` title+clip count); `segment_starts`
  push the clip start only.

### 3. "Identification Options" — new create-screen control

On the create/generate screen, to the right of the **Output filename** field:
- Heading **"Identification Options"** + helper text
  *"Choose what identifying information to include in each clip"*.
- Three checkboxes, **all checked by default**:
  - **Name** — participant name (video stem)
  - **Timestamp** — where the clip begins in the source (`from M:SS`)
  - **Segment tracker** — position in the reel (`Segment N / total`)
- Touches `templates/index.html`, `static/app.js` (state + generate payload),
  `static/style.css` (styled per DESIGN.md).

### 4. How the flags flow

The three booleans ride the generate payload (local `/generate`, cloud `/plan`).
Both endpoints already share `_build_segment_list`, so **`title_lines` is
composed from the checked flags in that one function** — the renderers stay dumb
and just draw whatever lines they are handed. The flags must be threaded into
`_build_segment_list` from both call sites.

- All three unchecked → empty `title_lines` → no overlay (free "clean video" mode).
- Existing `title_lines` order preserved: name, then `from M:SS`, then
  `Segment N / total`, each included only if its flag is set.

### 5. Overlay behavior (both renderers)

- **Position:** top-center, traditional-title style.
- **Animation:** fade in (~0.3s) → hold → fade out (~0.3s). A title that never
  leaves competes with the quote and captions; symmetric fade keeps the frame
  clean for most of the clip.
- **Legibility:** subtle shadow/scrim so light text survives over arbitrary
  video content.

### 6. Caption timeline ripple

- Pass `title_card_duration=0` to `build_webvtt` (the parameter already exists);
  drop the 5s title-card term from the reel timeline so `clip_start = reel_t`.
- Captions remain bottom-anchored; the identification overlay is top-anchored —
  no collision.
- `segment_starts` now point to clip starts (no 5s offset); library chapter
  markers still work, just tighter.

Everything else (MIN_CLIP_SECONDS floor, concat demuxer, stitch) is unchanged.

## Testing

- **Prompt:** smoke check that responses still parse via `parse_scored_timestamps`
  and ranges stay bounded. (The prompt is behavioral; assert structure, not exact
  ranges.)
- **Captions:** assert the `title_card_duration=0` timeline — first clip's cues
  start at `reel_t` with no 5s offset.
- **`_build_segment_list`:** assert `title_lines` respects each flag combination,
  including all-off → empty list.
- **Generate endpoint:** existing end-to-end tests with the three flags defaulted
  on. Keep `patch("generator_app._library_add")`.

## Out of scope

- Manual-selection trimming (only the AI-analyze path is tightened).
- Any change to transitions beyond removing the title-card interstitial (hard
  cuts remain).
- A hard cap on total reel length or clip count — governed by user selection.

## Files touched

- `claude_client.py` — prompt.
- `video_editor.py` — `extract_clip` gains overlay drawtext.
- `generator_app.py` — delete `make_title_card` + title-card pipeline branch;
  compose `title_lines` from flags in the shared `_build_segment_list` (covers
  both `/generate` and `/plan`); `title_card_duration=0`.
- `captions.py` — no code change expected (already parameterized); verify call sites.
- `static/reel-encoder.js` — delete `_encodeTitleCard`; draw overlay in clip loop.
- `templates/index.html`, `static/app.js`, `static/style.css` — Identification
  Options control + payload.
- `tests/` — new/updated assertions per Testing.
