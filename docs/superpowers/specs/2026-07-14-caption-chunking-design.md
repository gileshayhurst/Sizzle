# Readable Two-Line Captions — Design

**Date:** 2026-07-14
**Status:** Approved (design)

## Problem

Reel captions currently show a whole participant turn as a single WebVTT cue.
Transcript lines are utterance-level (one line = one participant turn), up to
~375 characters spanning ~20 seconds. `captions.build_webvtt` emits one cue per
line, so the browser wraps that cue into 7+ lines that cover most of the video
frame for the entire clip — the opposite of readable captions.

Observed example (one real cue, 357 chars, ~23 s on screen):

```
00:00:05.000 --> 00:00:28.000
When we give him kibble, it doesn't do anything for me, but when we're able to
give him some food from the kitchen, um, that makes me really happy for him. Or
we'll bring him food. Like, he loves hamburgers, so sometimes we'll bring him a
hamburger home if we go out to eat, and those always make me happy...
```

## Goal

Traditional broadcast-style captions: **at most two short lines on screen at a
time (≤ 42 chars/line), advancing with the speech.** Never a wall of text.

## Where the fix lives

`captions.build_webvtt` is the single origin of every caption cue. All three
consumers read the VTT it produces:

- Soft `<track>` in the player / result screen — browser renders the VTT.
- Local ffmpeg burn-in (`subtitles` filter) — renders multi-line VTT natively.
- Cloud in-browser burn-in (`reel-encoder.js`) — parses the VTT and draws cues
  onto the canvas per frame.

So chunking in `build_webvtt` fixes the soft track and the local burn-in for
free. Only the cloud canvas renderer needs a small change to draw two lines
instead of one. **No transcription changes** — existing cached transcripts work
as-is (word-level timestamps are not cached and are not needed).

## Design

### 1. Chunking — `captions.py`

Add a pure helper that splits one transcript line's text into an ordered list of
cue strings:

```
LINE_MAX_CHARS = 42   # max chars per visible line
CUE_MAX_CHARS  = 84   # max chars per cue (two lines)

def _chunk_line(text) -> list[str]:
    # 1. Greedily pack whitespace-split words into cues of <= CUE_MAX_CHARS.
    #    A single word longer than CUE_MAX_CHARS becomes its own cue (not split
    #    mid-word).
    # 2. Within each cue, wrap into <= 2 lines of <= LINE_MAX_CHARS at a word
    #    boundary, joined with "\n". If the packed cue still exceeds two lines
    #    (only possible when a single word > LINE_MAX_CHARS), keep it as one
    #    over-long line rather than dropping text.
    # Returns [] for empty/whitespace-only input.
```

The 357-char turn above becomes ~5 cues, each a balanced two-liner.

### 2. Timing — `captions.py` (`build_webvtt`)

The per-line window is unchanged from today: `cue_start = clip_start +
(line.seconds - seg.start_sec)`, and `cue_end` is the next selected line's start
(same segment) or the clip end, clamped to the clip range.

For each line, anchor its chunks to proportional slots within the
`[cue_start, cue_end]` window, sized by each chunk's character count. Each
chunk keeps its scheduled start; the 6 s cap only ends a cue early:

```
window = cue_end - cue_start
total  = sum(vlen(chunk) for chunk in chunks)     # vlen = chars excl. the "\n"
acc    = 0
for chunk in chunks:
    start        = cue_start + window * (acc / total)
    acc         += vlen(chunk)
    nominal_end  = cue_start + window * (acc / total)   # == next chunk's start
    end          = min(nominal_end, start + MAX_CUE_SEC) # MAX_CUE_SEC = 6.0
    emit cue (start, end, chunk)
```

- Each chunk stays anchored to its proportional position, so captions track the
  speech. The 6 s cap ends a cue early (a caption-free gap until the next
  chunk's scheduled start) rather than shifting later chunks — nothing is pulled
  earlier or stretched to backfill.
- Because a selected clip's duration ≈ the time spent speaking that text,
  proportional slots land each two-line cue at a natural ~4–5 s.
- `vlen(chunk)` excludes the joining `\n` so the newline never skews the share.
- Skip zero/negative-duration cues (existing guard) and empty text.

Existing behavior preserved: interviewer lines already excluded upstream in
`collect_caption_lines`; `build_webvtt` still returns `None` when there are no
cues; the cumulative reel timeline (`title 5s + clip` per segment) is unchanged.

### 3. Cloud canvas renderer — `reel-encoder.js` `_drawCaption`

Today `_drawCaption` draws a single line with `ctx.fillText`, which renders a
`\n` as a missing-glyph box. Update it to:

- Split the cue text on `\n` into up to two lines.
- Draw the lines stacked and bottom-anchored (line 2 at the current baseline,
  line 1 one line-height above), each centered.
- Size the translucent background box to the widest line and both lines' height.

`_parseVtt` already preserves multi-line cue text (joins block lines with `\n`),
so no parser change is needed.

### 4. No change

- Local ffmpeg burn-in — the `subtitles` filter renders the multi-line VTT.
- Soft `<track>` — the browser renders the VTT cues; short cues sit at the
  bottom by default.
- `collect_caption_lines`, the reel timeline, the CC toggle, endpoints, storage.

## Testing

`tests/test_captions.py`:

- Update existing cue-count/text assertions (a long line now yields multiple
  chunked cues instead of one).
- `_chunk_line`: long turn → multiple cues, each ≤ 84 visible chars and ≤ 2
  lines of ≤ 42 chars; short line → single unchanged cue; empty/whitespace → [];
  a single word longer than the limit is kept whole (not split mid-word).
- `build_webvtt` timing: chunks of one line partition that line's window in
  order; each emitted cue ≤ 6.0 s; a very long trailing window is capped (gap
  before next line), not backfilled.
- Regression: interviewer lines still excluded; no cues → `None`.

No new dependencies. `_chunk_line` is a pure string function with a runnable
assert-based check.

## Out of scope

- Word-level caption timing (would require changing the transcript cache format
  and re-transcribing every video).
- Re-styling the soft `<track>` via `::cue` CSS or repositioning.
- Any change to how lines are selected or which lines become captions.
