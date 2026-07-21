# Anchored transcript tier (inline 5-second markers)

**Date:** 2026-07-20
**Status:** Designed, NOT implemented — deferred pending Forven's format decision
**Extends:** `2026-07-20-transcript-tiered-clip-boundaries-design.md` (shipped)

## Why this is deferred

Building this now would be speculative. Forven has not committed to an export
format, and the most likely option — sentence-level lines with start and end
times — is already shipped and makes anchors nearly redundant. This document
exists so Forven can be briefed with two concrete options and so the work can
start immediately if they pick this one.

**Do not implement until Forven confirms which format they will emit.**

## Problem

The shipped design has two tiers:

- **plain** — `[M:SS] Speaker: <whole 30-60s turn>`. Sentence positions inside
  the turn are interpolated by word count. Error is bounded only by the turn
  length, so ±30s is possible.
- **rich** — `[M:SS-M:SS] Speaker: <one sentence>`. Exact; no interpolation.

If Forven cannot restructure turns into sentence-level lines, plain tier is all
we get, and its interpolation is the guesswork this project set out to remove.

## Format

A turn keeps its `[start-end]` header and carries inline timestamps marking
where in the text each ~5s point falls:

```
[00:04-00:20] Participant: He's a Corgi mix. [00:09] We got him as a rescue,
so he's anywhere from ten to thirteen years old. [00:14] He's really lazy now.
```

**Placement:** roughly every 5 seconds, snapped to the nearest word boundary.
An anchor must never split a word. Exact spacing is not required and must not be
assumed by the parser — treat any inline timestamp as an anchor wherever it
appears.

**Semantics:** an anchor states that the words *following* it begin at that
time. The line's own `[start]` is an implicit anchor at position 0; the line's
`[end]` is an implicit anchor at the end of the text.

## The key architectural insight

**Anchors need no new downstream path.** They do not add a third branch to
`group_lines_into_segments` or to `captions.build_webvtt`.

`normalize_transcript` already splits turn-level lines into sentences and
assigns each an interpolated timestamp. Anchors simply give that function
better input: instead of interpolating linearly across the whole turn by word
count, it interpolates **piecewise between adjacent anchors**.

So the anchored tier is a *preprocessing* improvement, and its output is
ordinary sentence-level lines. Better still, because each sentence's window is
bounded by known anchors, normalization can emit them in the **rich** format
with both a start and an end — meaning an anchored transcript is upgraded into
a rich one on read, and every downstream path already shipped works unchanged.

```
anchored turn ──normalize_transcript──> rich sentence lines ──> existing pipeline
```

This is the whole reason this design is cheap: the expensive half already exists.

## What it does and does not buy

**Does:** bounds interpolation error to the anchor spacing (~5s) instead of the
turn length (30-60s). On the measured Forven corpus that is roughly a
6-12x reduction in worst-case sentence-timing error.

**Does not:** eliminate interpolation. A sentence starting between two anchors
still has its exact start estimated within that ≤5s window. This is a precision
improvement, not the exactness that sentence-level lines give.

**Marginal value if sentence-level ships too:** small. Anchors would then only
sharpen sub-cue caption timing *inside* sentences longer than ~5s — the residual
the shipped spec already accepts. If Forven can do sentence-level, prefer it and
skip this entirely.

## Design

### Parsing

`_LINE_RE` matches the header as today. A second pass extracts inline anchors
from the text:

- `_ANCHOR_RE = r'\[(\d+:\d{2})\]'` applied to the text body only, never the header.
- Returns `[(char_offset, seconds), ...]` plus the text with anchors **removed**.
- The cleaned text is what reaches `text`, captions, and Claude. Anchors must
  never appear in caption output or in a prompt.

### Tier detection

`transcript_tier` gains a third return value, `"anchored"`, ordered by
precision: `rich` > `anchored` > `plain`.

A file is `anchored` when it is not `rich` and **every respondent line carries
at least one inline anchor**. Same strict all-or-nothing rule and same reason:
mixing anchored and un-anchored lines would produce inconsistent precision with
no visible cause.

### Normalization

`normalize_transcript` gains an anchored branch:

1. Split the turn into sentences as today.
2. Build the anchor list, prepending `(0, line_start)` and appending
   `(len(text), line_end)`.
3. For each sentence, locate its character offset. Find the bracketing anchors
   and interpolate **within that pair only**, by word count as today.
4. Emit `[start-end]` per sentence, where the end is the next sentence's start
   (or the turn's end for the last).

Step 4 means the output is rich-tier, so `read_transcript` returns text that
classifies as `rich` and every shipped downstream path applies unchanged.

`START_BIAS_SECONDS` should **not** be applied in the anchored branch. It exists
to compensate for late-skewing whole-turn estimates; with a ≤5s bracketed window
the skew it corrects is largely gone, and applying it would pull starts earlier
than the anchor says they are — overriding real data with a fudge factor.

### Edge cases

| Condition | Behaviour |
|-----------|-----------|
| Anchor before the line's start, or after its end | Ignore that anchor; if any remain the line is still anchored |
| Anchors out of chronological order | Line is treated as un-anchored; file demotes to plain |
| Anchor mid-word (exporter bug) | Snap to the following word boundary; do not split the word |
| Text legitimately containing `[M:SS]` | Indistinguishable from an anchor. Accepted: spoken transcripts do not contain bracketed timestamps |
| Anchored line with only one anchor | Valid — bracketed by the implicit start/end anchors |

### Migration

The `raw` line string changes for any re-exported file, so this needs a
**`sizzle_sel_v4_` and `sizzle_pool_v4_` bump**, both together. See the shipped
spec's migration section — the pool key was missed the first time and silently
defeated the selection bump.

## Testing

- Parser: anchors extracted with correct offsets; cleaned text has none.
- Tier: all-anchored → `anchored`; one un-anchored respondent line → `plain`;
  out-of-order anchors → `plain`.
- Normalization: a sentence starting just after an anchor gets a time within 5s
  of that anchor, NOT the whole-turn interpolation. Assert against a fixture
  where the two differ by more than 5s, or the test proves nothing.
- Round-trip: an anchored file, read through `read_transcript`, classifies as
  `rich`.
- Anchors never appear in caption text or in a Claude prompt.
- JS: the `v4` key bump, guarded like `v3`.

## What to ask Forven

Two options, in preference order:

1. **Sentence-level lines with start and end** — `[MM:SS-MM:SS] Speaker: <one
   sentence>`. Preferred: exact, already supported, needs no further work here.
2. **Turn-level lines with inline ~5s anchors** — the format above. Acceptable
   fallback if turns cannot be split.

For **either** option: **end timestamps must round UP, not truncate.** A
truncated end lands earlier than the speech and clips the final word, which is
the defect this whole line of work removed. Sub-second precision would be better
still; whole seconds rounded up are sufficient.

## Out of scope

- Word-level timings.
- Anchors finer than ~5s (diminishing returns against sentence-level lines).
- Using anchors to *split* clips at anchor points rather than sentence
  boundaries — anchors land on a time grid, not on meaning, so cutting there
  would break mid-sentence.
