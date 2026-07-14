# Fix trailing dead air on reel clips

**Date:** 2026-07-14
**Status:** Approved, ready for implementation plan

## Problem

Some reel clips play great, then sit on 5-10s of blank/silent space before the
next segment. This dead air majorly slows the reel down.

## Root cause

`_group_lines_into_segments` (generator_app.py) sets each clip's **end** to the
*start time of the next unselected transcript line*. But transcripts only ever
recorded line **start** times (`transcriber.py` outputs `[M:SS] Speaker: text`
and discards Whisper's word-level end times). When a selected line is followed
by a long interview pause — the interviewer talking off-mic, or the respondent
thinking — the next line's start can be 10-20s later. The clip runs into all of
that pause, so you get the speaker's good answer followed by them sitting
silently until the cut.

Real gaps observed in `FORVEN VIDEOS/Picky.txt`:
- `[0:09]` → `[0:30]` = 21s
- `[0:41]` → `[0:59]` = 18s
- `[2:43]` → `[2:59]` = 16s

The last selection in a video is a separate, minor case (clip runs to
`video_duration`), but the dominant problem is the trailing-gap case above.

## Approach

Estimate when the last selected line's speech actually ends, from its own word
count, and cap the clip end there. Chosen over (B) re-transcribing to capture
real end times — which changes the transcript format read by Claude, the
frontend, and captions, and forces re-transcription of every cached video — and
over (C) a fixed trailing cap, which would truncate genuinely long closing
sentences.

This approach needs no re-transcription and works on all existing cached
transcripts immediately.

## Design

Single change inside `_group_lines_into_segments`, in its `_finalize` helper
(generator_app.py). No other files change.

### The cap

```
word_count = len(last_selected_line["text"].split())
speech_end = last_selected_line["seconds"] + word_count / SPEAKING_RATE + TAIL_BUFFER
end        = min(original_end, speech_end)
```

`_finalize` currently receives the run's first-line start and the boundary end
(the next line's start, or `video_duration` for the final run). It must also
receive the **last** selected line of the run so it can estimate that line's
speech duration. Both call sites pass `current[-1]`:

- mid-run: `_finalize(current[0]["seconds"], line["seconds"], current[-1])`
- trailing run: `_finalize(current[0]["seconds"], end, current[-1])`

### Constants

- `SPEAKING_RATE = 2.0` words/sec. Conversational English runs ~2.5 wps;
  assuming a slower 2.0 makes the estimate longer than reality for nearly
  everyone, biasing toward leaving a sliver of air rather than cutting a word.
- `TAIL_BUFFER = 1.0` seconds, added on top for the trailing consonant/breath
  and unusually slow speakers.

Both live as module-level constants near `MIN_CLIP_SECONDS`, tunable.

### Ordering and safety

1. The cap is a `min()` against the current end, so it can only ever **shorten**
   a clip — worst case it changes nothing (today's behavior), best case it trims
   dead air. It can never make a reel worse.
2. The existing `MIN_CLIP_SECONDS = 1.5` floor runs **after** the cap, so a
   short final line never collapses below the minimum clip length.

### Known ceiling

The only case that can still clip slightly early is a sentence longer than ~18
words spoken slower than ~1.8 wps — a rare combination, and even then only a
fraction of a second past the 1.0s buffer. Marked with a comment in the code;
the fix is to nudge `SPEAKING_RATE` down.

### Worked example

`Picky.txt` line 5: 18 words, starts `0:41`, next line `0:59`.
Cap = `41 + 18/2.0 + 1.0 = 51s`. Clip ends ~0:51 instead of 0:59 — trims ~8s,
keeps every word (real speech ends ~0:48).

## Testing

One focused test in `tests/` covering `_group_lines_into_segments`:

1. A selection followed by a large (~15s) gap → segment end is capped near the
   estimated speech end, well short of the next line's start.
2. A long final line (many words) → segment end is **not** trimmed below its
   estimated speech length (guards the "never clip too early" constraint).
3. The `MIN_CLIP_SECONDS` floor still applies to a very short selected line.

No new dependencies, no fixtures — plain transcript-line dicts built inline.

## Out of scope

- Capturing real Whisper end times / transcript format change (Approach B).
- Internal gaps between two *selected* lines (both selected → kept contiguous;
  only the trailing segment boundary is capped).
- Caption timing changes (captions derive from the same segments and benefit
  automatically once the end is tighter; no separate work).
