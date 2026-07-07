# Speaker-Aware Clips + Empty-Clip Fix — Design

**Date:** 2026-07-07
**Status:** Approved (pending spec review)

## Problem

Two defects observed in a generated sizzle reel:

1. **Interviewer clips.** Reels sometimes contain clips where the AI interview
   agent is talking. A sizzle reel should only show the *respondent*
   (Participant), never the interviewer.
2. **Empty clip.** One "clip" was just a title card followed immediately by the
   next title card, with no actual video in between.

## Root Causes

### 1. Interviewer clips
The input transcripts already label speakers — `[M:SS] Interviewer: …` and
`[M:SS] Participant: …`. But the app discards the label:

- `parse_transcript_lines` in `shared.py` matches the speaker with `\w+` and
  never captures it, so every parsed line is just text with no "who."
- `query_claude` sends the raw transcript (labels included) to Claude. When a
  prompt happens to match interviewer text — e.g. the agent asks *"Have you
  heard of Freshpet?"* — `/analyze` can return the **interviewer's** timestamp,
  and those lines flow into the selection and into a clip.

The normal input path provides a labeled transcript alongside the video. Whisper
is only a fallback for videos with no transcript, and it emits unlabeled
`Speaker:` lines.

### 2. Empty clip
`_group_lines_into_segments` in `generator_app.py` sets a segment's end to the
**next line's** start-second. When a selected run ends right before a line with
the same or near-identical timestamp (transcripts do emit consecutive
same-second lines), the range collapses to ~0 seconds. ffmpeg still produces a
tiny valid file (`ok=True`), so the paired title card is kept and stitched,
yielding: title card → imperceptible clip → next title card.

## Design

### Part 1 — Speaker-aware clips

**Behavior contract:**
- `/analyze` (Claude) **never** returns interviewer-labeled lines — the
  automatic path excludes the interviewer.
- **Manual selection overrides exclusion.** If a user explicitly checks or
  brushes an interviewer line, it is included in the clip. This needs no special
  case: manual selection already forces a line into the clip range, so an
  explicitly-selected interviewer line is simply kept.
- Videos with no speaker labels (Whisper fallback, all `Speaker:`) behave exactly
  as today — everything selectable, nothing excluded. Detection **fails safe
  toward keeping content**: any label not recognized as an interviewer is treated
  as a respondent.

**Changes:**

1. **Capture the speaker (`shared.py`).**
   - Change `_LINE_RE` to capture the speaker group:
     `^\[(\d+:\d{2})\]\s+(\w[\w ]*?):\s*(.*)` (allows multi-word labels like
     "AI Agent").
   - Add `"speaker"` (the raw captured label, stripped) and a derived
     `"is_interviewer"` boolean to each line dict. Existing keys
     (`raw`, `timestamp`, `text`, `seconds`, `minute_bucket`) are unchanged, so
     all current callers keep working.

2. **Centralize interviewer detection (`shared.py`).**
   - Add a module constant:
     `INTERVIEWER_LABELS = {"interviewer", "ai", "ai agent", "ai interviewer",
     "agent", "moderator", "bot", "assistant", "host"}`.
   - Add helper `is_interviewer_label(speaker: str) -> bool` — case-insensitive,
     whitespace-normalized membership test. `is_interviewer` on each parsed line
     is computed via this helper.

3. **Filter `/analyze` results (`app.py`, `_run_analyze._analyze_one`).**
   - When collecting `matched` lines from Claude's ranges, skip any line whose
     `is_interviewer` is true. Interviewer lines never enter the analyze
     highlights.

4. **Tighten the Claude system prompt (`claude_client.py`).**
   - Add a rule: only return ranges spoken by the respondent/participant; never
     return ranges where the interviewer/agent/moderator is speaking, even if the
     topic word appears in their question. This reduces wasted matches; the
     post-filter in step 3 is the authoritative guard.

5. **Frontend cue (`static/app.js`, `static/style.css`, and the `/analyze` +
   transcript payloads).**
   - Surface `speaker` / `is_interviewer` to the frontend for each line (the
     transcript is parsed client-side from raw text today; the frontend will
     derive the label from the same raw line, or the backend will include it —
     see Open Implementation Notes).
   - Interviewer lines render with a de-emphasized style and a small
     "Interviewer" marker, while remaining clickable/brushable (so the manual
     override is still possible). This makes it visible which lines `/analyze`
     will skip. Style must follow DESIGN.md (light "Bright Studio" system), not
     the legacy dark theme.

### Part 2 — Empty-clip fix

**Change (`generator_app.py`, `_group_lines_into_segments`):**

- Introduce `MIN_CLIP_SECONDS = 1.5`.
- When a segment `(start, end)` is built, if `end - start < MIN_CLIP_SECONDS`,
  extend `end` to `start + MIN_CLIP_SECONDS`, clamped to the video's duration
  (`video_duration` when known).
- If the segment still cannot reach the minimum (e.g. the selected line sits at
  the very end of the video with no room), **drop the segment entirely** so no
  title card is emitted for it. A lone title card with no clip must never reach
  assembly.
- Because degenerate segments are removed at the source (segment building), the
  plan phase, `total_segs` count, and "Segment N / total" numbering stay
  consistent — they are computed from the filtered segment list.

`1.5s` is short enough to keep terse-but-real answers (e.g. *"Yes."*) and long
enough to eliminate the empty-frame artifact.

## Non-Goals

- Speaker **diarization** for unlabeled (Whisper) transcripts. Out of scope; the
  fallback keeps today's behavior.
- Detecting the interviewer by voice fingerprint. We rely solely on transcript
  labels.
- Reworking the two selection modes (checkbox / highlight) beyond the visual cue.

## Testing

- **`shared.py`:** `parse_transcript_lines` captures `speaker` and
  `is_interviewer` for `Interviewer`, `Participant`, multi-word `AI Agent`, and
  unlabeled `Speaker`. `is_interviewer_label` is case-insensitive across the
  synonym set and returns false for `Participant`/`Speaker`/unknown labels.
- **`app.py`:** `_run_analyze` excludes interviewer lines from highlights even
  when Claude returns a range covering an interviewer turn; participant lines in
  the same range are still returned.
- **`generator_app.py`:** `_group_lines_into_segments` never yields a segment
  shorter than `MIN_CLIP_SECONDS`; a selection that would collapse to ~0s is
  either extended to the floor or dropped, and a dropped segment removes its
  title card so assembly never sees a lone title. Existing generate-endpoint
  tests continue to `patch("generator_app._library_add")`.

## Open Implementation Notes

- **Where the frontend gets the speaker label:** the transcript view is built
  client-side from raw `[M:SS] Speaker: text` lines. The implementation plan will
  decide whether the frontend re-derives `is_interviewer` from the raw label
  (mirroring `INTERVIEWER_LABELS`) or the backend attaches it to the
  `/transcripts` and `/load-folder` payloads. Backend-attached is preferred to
  keep the synonym list in one place; a small JS mirror is acceptable if payload
  shape changes are undesirable.
