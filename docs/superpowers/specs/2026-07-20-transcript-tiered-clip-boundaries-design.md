# Tiered clip boundaries from transcript timing

**Date:** 2026-07-20
**Status:** Approved, ready for implementation plan
**Supersedes:** `2026-07-14-trailing-dead-air-design.md`

## Problem

Three symptoms, reported from real client reels:

1. **Clips cut speech off mid-point** — the speaker's last words are lost,
   most often at the end of an answer, which is where the payoff line sits.
2. **Clips feel loose at the end** — some clips sit in silence before the cut.
3. **Captions are inaccurate** — cue timing drifts against the speech.

## Root cause

All three are the same defect: **the transcript records a start time per line
and nothing else.** Every other timing in the system is inferred from it.

- Sentence starts within a turn — interpolated by word count
  (`normalize_transcript`).
- Clip ends — estimated from word count (`SPEAKING_RATE`, `CLIP_TAIL_RATE`,
  `TAIL_BUFFER`).
- Caption cue times — partitioned by *character count* proportion
  (`captions.py`, `_vlen`).

Because a single missing input feeds all three, tuning any constant trades one
symptom against another. That is precisely what happened: `2026-07-14` added a
word-count tail estimate to fix symptom 2, and `2026-07-19` (clip-length
precision) later added `START_BIAS_SECONDS = 1.0`, pulling recorded starts
earlier to protect first words. Neither change was wrong alone. Composed, the
tail estimate measured from an already-biased start, so its safety margin became

```
TAIL_BUFFER - START_BIAS_SECONDS = 1.0 - 1.0 = 0.0s
```

Zero margin at the assumed speaking rate, negative below it — symptom 1.

**The lesson driving this design:** estimates derived from other estimates
compose in ways no single review catches. Real timestamps cannot be invalidated
by a later interpolation change.

## Why now

`2026-07-14` considered capturing real end times (its "Approach B") and rejected
it: it changes the transcript format read by Claude, the frontend, and captions,
and forces re-transcription of every cached video.

That reasoning was sound when the transcript format was fixed. It no longer is —
**Forven can change its export format**, and will most likely emit sentence-level
lines with start and end times. That new fact is what makes this design possible.

## Measured baseline

Removing the tail estimate makes plain transcripts looser. Measured across
**8 real Forven exports, 208 participant turns** (upper bound: speech end
estimated at a brisk 2.5 words/sec, so true gaps are smaller):

| statistic | trailing gap |
|-----------|--------------|
| median    | 2.6s |
| 75th pct  | 4.6s |
| 90th pct  | 7.0s |
| max       | 23.6s |

21% of turns would carry 5s or more. Half carry under 2.6s, which reads as
breathing room. This is the accepted interim cost until rich transcripts land;
it never omits or truncates client speech, which the estimate could.

## Design

### 1. Transcript format and parsing

A superset of the current format. Existing files parse unchanged.

```
[00:05] Participant: text           <- plain (today)
[00:05-00:12] Participant: text     <- rich (new, optional end)
```

Real exports use zero-padded minutes (`[00:05]`); the existing `\d+:\d{2}`
pattern already accepts both padded and unpadded forms.

`_LINE_RE` (`shared.py`) gains an optional `-M:SS` group. `parse_transcript_lines`
adds exactly one key per line:

```python
"end_seconds": float | None   # None when the line carries no end
```

All existing keys (`raw`, `timestamp`, `speaker`, `is_interviewer`, `text`,
`seconds`, `minute_bucket`) are unchanged.

**Tier detection** — one pure helper, `transcript_tier(lines) -> "rich" | "plain"`.
A file is **rich only if every respondent line carries a valid `end_seconds`**.
Any missing or malformed end demotes the whole file to plain.

Strict all-or-nothing is deliberate. A per-line fallback would mix exact and
estimated boundaries inside one reel, producing inconsistent output whose cause
is invisible — the failure mode that produced this spec.

**Validation** — an end that is not strictly greater than its start is treated as
absent (and therefore demotes the file). This guards against a malformed export
silently producing zero-length or dropped clips.

`read_transcript` remains the single entry point for all transcript reads
(CLAUDE.md's existing rule). It gains the tier decision:

- **plain** → normalize as today
- **rich** → return unchanged, no normalization at all

Forven's sentences are already the target granularity; interpolating over real
timestamps would only corrupt them.

### 2. Clip boundaries

**Rich tier:** clip end *is* the last selected line's `end_seconds`.

**Plain tier:** clip end is the start of the first line *after* the selected run
(unselected by definition, since the run ended there) — the only end-adjacent
timestamp that exists in the file. Unchanged from current behaviour apart from
the removal of the estimate that shortened it.

**Deleted:** `CLIP_TAIL_RATE`, `TAIL_BUFFER`, and the entire `speech_end`
calculation in `_finalize`.

**Retained:** `SPEAKING_RATE` and `START_BIAS_SECONDS`, but *only* inside
`normalize_transcript`'s sentence splitting, and therefore only on the plain
tier. There they do granularity work (without splitting, a plain transcript
yields whole 30–60s turns as single clips). They no longer influence any clip
end.

**Retained:** `MIN_CLIP_SECONDS` (1.5s floor), `MAX_CLIP_SECONDS` (40s ceiling).
Both are safety nets, not predictions.

**The ceiling still truncates real speech, in both tiers.** A selected run
genuinely spanning 50s is cut at 40s mid-sentence, even when rich timing proves
the speaker is still talking. This is accepted deliberately: it is a *reel
pacing* decision, not a timing guess, and a single 40s+ clip is too long for a
highlight reel regardless of what the transcript says. It is called out here
because it is the one remaining path by which a clip can end mid-sentence, and
it should not come as a surprise later. If long single answers must survive
intact, raise the ceiling — do not reintroduce an estimate.

**Final line of a file:** the current `last_line + 10.0` fallback is guesswork
and is removed. Rich tier uses `end_seconds`. Plain tier uses `video_duration`
when known; otherwise the browser encoder's `computeDuration()` clamp bounds it,
which is reliable including on streaming-header WebM.

**No "max trailing silence" constant is added.** Any such bound cannot tell
speech from silence and would truncate long closing sentences — reintroducing
symptom 1 under a new name. The fix for plain-tier looseness is the rich
transcript.

### 3. Captions

**Rich tier:** `collect_caption_lines` carries `end_seconds` through; each
sentence is a cue timed by its own real start and end, re-timed onto the reel:

```
cue start = seg_start + (line.seconds     - clip_start)
cue end   = seg_start + (line.end_seconds - clip_start)   # clamped to clip end
```

`MAX_CUE_SEC` is unnecessary in this tier — a real end means a cue lasts exactly
as long as its sentence.

**Plain tier:** unchanged proportional chunking.

**Accepted residual:** a sentence longer than two display lines (~84 chars) is
still split into multiple cues whose sub-times are interpolated *within that
sentence*. Bounded to a few seconds rather than a 30–60s turn — roughly an order
of magnitude tighter. Eliminating it entirely requires word-level timings, which
is more than reels need.

Line wrapping (`LINE_MAX_CHARS`, the 2-line cue shape) is unchanged: layout, not
timing.

### 4. Migration and compatibility

**Selection identity breaks on tier change.** The `raw` line string is the
cross-service selection key. Re-exporting a transcript changes every `raw` in
that file.

- `localStorage` selections require a **`sizzle_sel_v3_`** bump. Without it,
  stale v2 entries restore into `state.checked`, render nothing, and ship dead
  strings to the generator — the failure the v2 bump was created to fix.
- In-flight selections are lost when a transcript is re-exported mid-session.
  Acceptable; must fail loudly rather than produce an empty reel.

**Both services must agree on tier.** `app.py` and `generator_app.py` parse the
same `.txt` independently and their `raw` strings are the contract, so
`transcript_tier` must be pure and deterministic.

**The `.txt` on disk is client data and is never rewritten.** Unchanged.

**`transcriber.py` emits the rich format.** Whisper already computes `w.end` per
word and discards it. Emitting it makes app-transcribed videos rich-tier for
free. Existing cached `.txt` files remain plain until deleted and
re-transcribed — no migration step, no version marker.

**No format version marker.** The presence of `-M:SS` is self-describing.

### 5. Error handling

| Condition | Behaviour |
|-----------|-----------|
| Malformed end (`end <= start`) | Treat as absent; file demotes to plain |
| Mixed rich/plain lines in one file | File is plain |
| Mixed rich/plain files in one folder | Legal; per-file tier. Produces a reel mixing tight and loose clips |
| Transcript timestamp beyond video duration | Out of scope (see below) |

## Testing

New tests, in dependency order:

1. **Parser** — plain line → `end_seconds is None`; rich line → parsed float;
   malformed end → `None`.
2. **Tier detection** — all-rich → `rich`; one plain line → `plain`; one
   malformed end → `plain`.
3. **`read_transcript`** — rich file returns byte-identical text (proving
   normalization is bypassed, not coincidentally idempotent); plain file still
   normalizes.
4. **Rich clip boundary** — clip end equals the transcript's end *exactly*.
   Load-bearing: any estimate creeping back in fails this test.
5. **Plain clip boundary** — clip end equals the next line's start, and does not
   move when only the last line's *word count* changes (proves no word-count
   influence remains).
6. **Captions** — rich cue start/end come from the sentence's own times; plain
   path unchanged.
7. **Cross-service determinism** — extend the existing guard from `0fdc2b5` to
   cover tier.
8. **`transcriber.py`** — emits rich format from Whisper word ends.

**Tests removed by this design:** `test_clip_tail_buffer_survives_the_start_bias`,
`test_group_lines_into_segments_caps_trailing_dead_air`, and
`test_group_lines_into_segments_long_line_keeps_full_speech` all guard the
deleted mechanism. The two `duration_seconds` tests need recomputed expectations.

**JS test infrastructure.** A minimal node-based runner is added as part of this
work. The `v3` bump's failure mode is silent (selections vanish, reels come out
empty) and must not ship untested. This also covers `optimalDuration`, currently
unguarded.

**Fixtures:** one rich-tier and one plain-tier transcript under `tests/fixtures/`.

**Not covered by tests:** whether reels subjectively stop feeling loose. That
needs a real Forven export with end times and human review.

## Out of scope

- **Transcripts referencing timestamps beyond their video's duration.** Observed
  in production (a clip requested 555–576s from a shorter video and was skipped).
  Confirmed a data-integrity problem on the source side, not a timing defect.
  Current behaviour is retained. One fix only: the browser encoder's skip log
  reads `seg.video_name`, but `/plan` serialises that field as `video`, so the
  message prints `undefined` instead of naming the file.
- Word-level timings.
- Force-alignment against audio (rejected: cloud mode has no local video file, so
  it would work locally and fail in production).
- UI signalling of mixed-tier folders.
