# Rich-transcript analysis: tighter clips from better input

**Date:** 2026-07-21
**Status:** Approved, not implemented
**Scope:** A + B + C1 (C2 explicitly out of scope — see Rejected alternatives)

## Problem

`shared.transcript_tier()` (shipped 2026-07-20) already classifies a transcript
as **rich** (every respondent line carries a real `[M:SS-M:SS]` end) or
**plain**, and rich clips already end at real transcript times instead of the
next line's start. The plumbing works.

But the *analysis* is tier-blind. A rich transcript produces substantially the
same clip selection a plain one would, for three reasons:

1. **Claude gets a better transcript and identical instructions.**
   `claude_client._SYSTEM_PROMPT` never mentions the `[M:SS-M:SS]` form and
   tells Claude "Only use timestamps that appear verbatim in the transcript."
   In rich tier a line's *end* is a verbatim timestamp — Claude could name a
   true end-of-speech — but nothing tells it so, and it keeps quoting starts.

2. **Claude's range is rounded back out to whole lines.** `app.py:262` carries
   the comment *"Claude's end timestamp is only the start of the last line."*
   That is true in plain tier and **false in rich**. The code discards the range
   and recomputes from `group_lines_into_segments` regardless, so the one place
   rich data could tighten a clip is gated by a plain-tier assumption.

3. **Matching ignores `end_seconds`.** `app.py:257` matches on line start only
   (`start_sec - 0.5 <= line["seconds"] <= end_sec + 0.5`). A line starting at
   0:24 and running to 0:58 is pulled in whole even when Claude's range ended at
   0:26. Symmetrically, a line starting 1s *before* the range but lying 95%
   inside it is dropped, costing the money quote its opening.

Additionally, the anchored turn-level format (option 2 in the Forven brief) has
a latent correctness bug today: such a line *has* a valid end, so
`transcript_tier` already calls it **rich** and `read_transcript` returns it
unchanged — inline anchors would ride through as literal text into captions and
burned-in overlays.

## Goal

When Forven ships a richer export, clip selection gets measurably more succinct
and better targeted, **whichever of the two candidate formats they choose**
(sentence-level `[MM:SS-MM:SS]`, or turn-level with inline ~5s anchors). The
format is not yet decided; the design must not bet on one.

## Design

Three changes, all in existing modules. No new files.

### A — Tier-aware analysis prompt (`claude_client.py`)

`query_claude(transcript, prompt, tier="plain")`. The system prompt gains a
tier-specific clause appended to the shared body, rather than a forked copy.

The rich clause states that lines carry `[start-end]`, that the end is the
speaker's real stop time, and that ranges should therefore begin at a line's
start and end at a line's end without padding past one.

The system prompt sits outside the prompt-cache breakpoint (only the transcript
block is cached), so branching on tier costs nothing.

### B — Overlap-aware matching (`shared.lines_in_range`)

Match logic moves out of `app._run_analyze` into `shared.py`, next to
`transcript_tier`, so it is pure and unit-testable:

    lines_in_range(all_lines, start_sec, end_sec) -> list[dict]

- **Plain tier:** current predicate unchanged
  (`start_sec - 0.5 <= line["seconds"] <= end_sec + 0.5`). There are no real
  ends to overlap against.
- **Rich tier:** include a line when its speech interval
  `[seconds, end_seconds]` overlaps Claude's range by more than half of the
  **line's own** duration (`MIN_LINE_OVERLAP_RATIO = 0.5`).

The ratio is against the line's duration, not the range's, so the predicate is
symmetric: it drops a 34s line that merely grazes the range, and keeps a line
that starts just before the range but lies almost entirely inside it.

A line with `end_seconds is None` (possible on interviewer lines even inside a
rich file) falls back to the plain start-predicate rather than dividing by a
missing duration.

### C1 — Anchor expansion (`shared.expand_anchors`)

    expand_anchors(raw_text) -> str

Splits each anchored turn line into consecutive rich lines:

    [00:04-00:20] P: text [00:09] more [00:14] still more

becomes

    [00:04-00:09] P: text
    [00:09-00:14] P: more
    [00:14-00:20] P: still more

`read_transcript` becomes:

    parse -> tier
      rich  -> expand_anchors(text)
      plain -> normalize_transcript(text)

This is the piece that makes the design format-agnostic: sentence-level input
needs no expansion (no inline anchors, returned unchanged), anchored input is
normalized *into* sentence-level rich, and everything downstream sees one shape.

`expand_anchors` must be **pure, deterministic, and idempotent** — both services
call `read_transcript` independently and the resulting `raw` strings are the
cross-service selection identity. Idempotency holds by construction: its own
output contains no inline anchors.

Anchor failure modes, all failing safe toward the whole turn:

| Case | Behaviour |
|---|---|
| No inline anchors | Line unchanged |
| Anchor outside the line's `[start, end]` | Drop **all** anchors on that line, keep whole turn |
| Non-monotonic anchors | Drop, keep whole turn |
| Anchor at position 0, or trailing anchor with no text | Skip the empty chunk; neighbour absorbs the span |
| Interviewer lines | Expanded uniformly, no special case |

Never fabricate a boundary from a malformed anchor. A dropped anchor costs
precision; a fabricated one produces a wrong cut with an invisible cause — the
same reasoning behind rich tier being strictly all-or-nothing.

### Data flow: unchanged downstream

`group_lines_into_segments` already trusts `end_seconds` in rich tier.
`captions.py` already re-times per line. `generator_app._build_segment_list`
still receives `{video: [raw, ...]}` and still recomputes identical boundaries,
because `expand_anchors` is deterministic and both services route through
`read_transcript`. That invariant is what makes C1 cheap.

### Required migration

`expand_anchors` changes the `raw` strings for anchored files, and `raw` is the
selection identity. Bump the localStorage selection key
**`sizzle_sel_v2_` → `sizzle_sel_v3_`** (and the candidate-pool key alongside
it, per commit `64e398d`). Skipping this restores stale selections that render
nothing and then fail at generate time with "No segments found in selections".

## Testing

All three changes are pure functions.

- `expand_anchors`: plain text unchanged; sentence-level rich unchanged; a
  3-anchor turn splits into 3 correctly-bounded lines; malformed anchors fall
  back to the whole turn; `expand_anchors(expand_anchors(x)) == expand_anchors(x)`.
- **Cross-service determinism** (load-bearing): an anchored fixture read through
  `app`'s path and `generator_app`'s path yields byte-identical strings. If this
  breaks, selections silently stop matching across services.
- `lines_in_range`: long line grazing the range is dropped; line starting before
  the range but 95% inside is kept; plain-tier predicate unchanged (regression
  guard).
- Existing generate-flow tests keep `patch("generator_app._library_add")` per
  CLAUDE.md.
- **Outcome measurement:** the same content analyzed rich vs plain, same prompt,
  yields a lower total clip duration. Without this the premise of the whole
  change is unfalsifiable.

## Rejected alternatives

**C2 — carrying `{raw, start, end}` through the selection payload.** Would let a
clip honour an arbitrary Claude trim point ("the money quote is 0:16.5–0:21.2")
that the transcript never names. Rejected for now: it rewrites the cross-service
selection contract, both services, `static/reel-encoder.js`, and `captions.py`
cue re-timing — and the boundaries it buys are *mid-sentence* cuts, which
`shared.py` currently treats as a defect (`MAX_CLIP_SECONDS` is documented as
"the only remaining path by which a clip can end mid-sentence"). Revisit only if
A+B+C1 measurably fails to tighten clips.

**Word-count estimation for boundaries.** Permanently rejected; see
`docs/superpowers/specs/2026-07-20-transcript-tiered-clip-boundaries-design.md`.

## Dependencies

None blocking. A and B ship value on sentence-level rich input alone. C1 is
inert until an anchored transcript appears, but is required correctness the day
one does — it should not be deferred separately.
