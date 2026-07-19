# Clip-Length Precision — Sentence Normalization + Hard Cap

**Date:** 2026-07-19
**Status:** Approved for planning

## Problem

The reel-form rework (`7d19bc5`) instructed Claude to return tight 5–12s
"money quote" ranges, but generated reels still contain 30–60s clips. The
prompt change could not work because the rest of the pipeline operates at
transcript-*line* granularity:

1. **Line-granularity ceiling.** The system prompt orders Claude to "only use
   timestamps that appear verbatim in the transcript" (`claude_client.py`).
   Production transcripts are **Forven platform exports** — one line per whole
   participant turn, with `Interviewer:`/`Participant:` speaker labels. A turn
   is often 30–60+ seconds (measured in `FORVEN VIDEOS`: the `[01:29]` turn
   runs to `[02:30]`). The tightest range Claude can express is one whole turn.
2. **Ranges snap back to whole lines.** `/analyze` converts Claude's range to
   the set of lines whose start falls inside it (`app.py`), and
   `shared.group_lines_into_segments` cuts the clip from the first selected
   line's start to the last line's estimated spoken end. Claude's end
   timestamp is effectively "start of the last included line" — that line then
   plays in full.
3. **Generous tail estimate.** The last line's end is estimated at
   `words / SPEAKING_RATE (2.0) + TAIL_BUFFER (1.0)`. A 60-word turn adds ~31s.
4. **Our sentence-level transcriber never runs on production data.** The
   Whisper transcriber (sentence splitting since `9520035`) only runs when no
   `.txt` exists. Forven folders always ship a `.txt`, so it is bypassed.
   Deleting the `.txt` to force re-transcription is not an option: it would
   destroy the speaker labels that interviewer-exclusion depends on.

**Constraint confirmed with user:** Forven exports (turn-level timestamps,
speaker labels) are the norm for real usage. The fix must operate on external
transcripts and preserve labels.

## Decision

Approach A from brainstorming: **sentence-split turn lines with interpolated
timestamps, applied on read**, plus a **hard clip cap (~22s)** as a safety
net. Whisper word-alignment (Approach B) was rejected as the first step —
minutes of compute per video, fiddly fuzzy alignment, and impossible in cloud
mode (neither Vercel nor Render free tier can run Whisper) — but remains the
precision upgrade path behind the same normalization interface. Cutting at
Claude's raw range (Approach C) was rejected because without finer verbatim
timestamps Claude would fabricate sub-turn timing, and it fixes only the AI
path while re-plumbing selection identity everywhere.

## Design

### 1. `normalize_transcript(raw_text) -> str` — pure function in `shared.py`

For each `[M:SS] Speaker: text` line containing 2+ sentences:

- Split text on terminal punctuation (`.` `!` `?`, reusing the transcriber's
  sentence-boundary convention).
- Emit one line per sentence, same speaker label, format
  `[M:SS] Speaker: sentence`.
- Interpolate each sentence's timestamp by word-count proportion across the
  turn's **estimated speech window**:
  `window_end = min(next_line_start, turn_start + words / SPEAKING_RATE + TAIL_BUFFER)`.
  Interpolating over the speech window (not the raw turn window) prevents the
  systematic *late* skew caused by trailing dead air inside Forven turn
  windows — and late starts are the harmful direction (they chop words).
- Bias every interpolated start **~1s early** (`START_BIAS_SECONDS = 1.0`),
  clamped to the turn start. Converts residual error into the harmless
  direction (a beat of lead-in, softened by the existing 0.4s fade-in).
- Interpolated timestamps must be monotonically non-decreasing within a turn
  and never exceed the next line's original timestamp.

Properties:

- **Deterministic** — same input bytes always produce same output bytes. Both
  services derive identical lines, so raw-line selection identity
  (analyze → select → generate, `localStorage` persistence) keeps working.
- **Idempotent** — single-sentence lines pass through byte-identical;
  `normalize(normalize(x)) == normalize(x)`.
- **Fail-safe** — a turn with no terminal punctuation is left as one line
  (the hard cap catches it). Lines that don't match the transcript format
  pass through untouched.
- Applies uniformly to all speakers (no interviewer special-casing — their
  lines are excluded from clips elsewhere).

### 2. Normalize on read at every transcript entry point

The `.txt` files are client data and are **never mutated**. Each service
normalizes as it reads:

- `app.py`: `/load-folder` (content returned to UI), `/transcripts`,
  `_run_analyze` (text sent to Claude **and** the lines parsed for matching).
- `generator_app.py`: `_build_segment_list` (generate + `/plan` paths) —
  which also feeds `collect_caption_lines`, so captions inherit sentence
  granularity and stay internally consistent with clip cuts.

Claude then sees the finer timestamps *verbatim in the transcript*, making
the existing 5–12s prompt instruction achievable — **no prompt change**. The
checkbox/highlight UI, minute-bucket grouping, captions, and the
create-screen length estimate all inherit sentence granularity automatically
because they are already line-based.

Implementation note: route reads through one shared helper (or call
`normalize_transcript` immediately after each raw read) so no entry point can
be missed; mirror the `_filter_generated_reels` "call it in every code path"
rule in CLAUDE.md.

### 3. Hard cap — `MAX_CLIP_SECONDS = 22` in `shared.group_lines_into_segments`

In `_finalize`: `end = min(end, start + MAX_CLIP_SECONDS)` (applied after the
dead-air trim, before the `MIN_CLIP_SECONDS` floor). Chosen at the
user-approved 20–25s level: it only catches pathological cases
(unpunctuated monologues, over-wide ranges); normal clips still end on
natural sentence boundaries. Generator clips and the create-screen estimate
share this function, so both stay in agreement.

## Accepted trade-offs

- **±1–3s interpolation error.** With speech-window interpolation and the 1s
  early bias, the realistic failure mode is a clip carrying ~2s of lead-in or
  starting a word into an "Um," — cosmetic, not broken. Mid-turn multi-second
  pauses can still shift later sentences. Never produces wrong content, wrong
  speaker, or captions desynced from the cut (both derive from the same
  numbers). Status-quo error was 30–60s.
- **Stale saved selections.** Previously-saved `localStorage` selections
  reference old line text and will silently reset once. Harmless.
- **Perf: negligible.** Pure string processing, microseconds per transcript.
  Claude payload grows ~1–2% (repeated line prefixes); determinism keeps the
  prompt-cache block byte-identical across analyzes. UI renders ~4× more rows
  (still tiny). Generation gets *faster* — shorter clips, less encoding.

## Testing

- `normalize_transcript` unit tests: multi-sentence turn splits; timestamps
  monotonic, within the turn window, ≤ next line's start; early bias applied;
  labels preserved; single-sentence and non-matching lines byte-identical;
  idempotency; no-punctuation passthrough; determinism.
- `group_lines_into_segments`: cap test (long unpunctuated line → clip ≤ 22s);
  existing dead-air and floor tests stay green.
- End-to-end: analyze → select → generate line matching works on a normalized
  transcript (raw-line identity across both services).
- Full suite (296 tests) stays green.

## Out of scope

- Whisper word-alignment refinement (upgrade path if ±2s proves visible).
- Prompt changes in `claude_client.py`.
- Rewriting/mutating client `.txt` files.
