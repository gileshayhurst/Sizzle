# Reel Captions — Design

**Date:** 2026-07-13
**Status:** Approved

## Context

Generated reels are dropped into stakeholder reports and presentations. Users
want captions they can turn on and off in the library. Reels are built as
`[title card 5s][clip]…` pairs stitched together; the selected transcript lines
(`[M:SS] Speaker: text`, interviewer lines already excluded) carry per-line
timing, so caption text is **derived from the transcript, not AI-generated**.

The server downloads the `.txt` transcripts into a temp dir in every mode —
local generate, cloud `/generate` (generator_app.py:829), and cloud `/plan`
(generator_app.py:925) — so the full line-level transcript is available
server-side everywhere. Caption text is therefore built once in Python and
reused in all modes; no JS duplication.

## Decisions (from brainstorming)

1. **Form:** soft captions (WebVTT track, toggleable in the player) **plus** a
   burn-in-on-download path for a shareable captioned MP4.
2. **Toggle UX:** a CC button in the library video player, remembered globally
   (not per-reel).
3. **Scope:** new reels only. Existing library entries have no caption data and
   show no CC button. No backfill.
4. **Cloud burn-in:** runs in the browser via mediabunny (consistent with why
   cloud reel encoding lives in the browser); local burn-in uses server ffmpeg.

## Phase 1 — Soft captions (independently shippable)

### Timeline math

The reel timeline is `[title₁ 5s][clip₁][title₂ 5s][clip₂]…`. For a selected
line at source time `line.seconds` in segment *i* with source range
`seg_start → seg_end`:

```
clip_duration       = seg_end − seg_start          # encoder cuts exactly this
segment_start[i]    = Σ over prior segments of (TITLE_CARD_DURATION + clip_duration)
cue_start_in_reel   = segment_start[i] + TITLE_CARD_DURATION + (line.seconds − seg_start)
cue_end             = next line's cue_start, clamped to
                      segment_start[i] + TITLE_CARD_DURATION + clip_duration
```

`TITLE_CARD_DURATION` is the existing constant (5.0s). Everything is determined
from the plan — no dependency on the encode. Cues cover only clip spans; title
cards render none (they already display text). No speaker labels — just the
spoken words.

**Known ceiling:** if a segment's extraction fails and assembly drops the pair
(rare), captions after that point drift. Marked with a `ponytail:` comment
rather than building failure-aware re-timing.

### `build_webvtt(segments) → str` (shared)

A pure function producing a WebVTT string. `_build_segment_list` already parses
transcripts; extend each segment dict it returns to carry per-line caption data:
`caption_lines: [{"text": str, "seconds": float}, …]` (respondent lines only,
within the segment's source range). `build_webvtt` consumes the ordered segment
list, walks the cumulative timeline above, and emits standard WebVTT cues.

Called server-side in both `_run_generation_impl` (local + cloud generate) and
`POST /plan` (cloud browser path). One Python implementation.

Lives in a new module `captions.py` (`build_webvtt`, plus a `WEBVTT_MIME =
"text/vtt"` constant) so the timing logic is isolated and unit-testable without
importing the Flask app.

### Storage & serving

- **Local:** write `{stem}.vtt` beside the reel MP4. Library entry gains
  `captions_filename`.
- **Cloud:** `POST /plan` returns `captions_vtt` (text) + `captions_key`
  (`{session_key}/{stem}.vtt`) + `captions_put_url` (presigned PUT). The browser
  uploads the VTT immediately after a successful reel encode (same lifecycle as
  the reel), then records `captions_key` in its `POST /library` call. Library
  entry gains `captions_key`.
- **Serving:** new endpoint `GET /library-captions/<id>` on the generator →
  `Content-Type: text/vtt`. Local reads the sidecar; cloud reads the object via
  `storage.read_file_bytes(captions_key)` (kilobytes of text — proxying is fine,
  unlike metered video). flask-cors already supplies CORS headers.
- Entries missing both caption fields (every existing reel) show no CC button.

### Player CC toggle

The library player overlay (templates/index.html:246) gains a **CC button**
beside the existing prev/next controls.

- On open, when the entry has captions: append
  `<track kind="captions" src="{GENERATOR_URL}/library-captions/<id>" default>`
  to `#library-video` and set `crossorigin="anonymous"` on the video element.
- The button flips `video.textTracks[0].mode` between `showing` and `hidden`.
- **Remembered:** `localStorage` key `sizzle_captions_on` (default off), applied
  every time the player opens so CC is consistent across reels.
- Button is hidden entirely when the entry has no caption track.
- Styled per DESIGN.md: amber-tint active pill when on, ghost when off (the
  `.mode-btn` pattern). Carries a literal "CC" label so state is not color-only
  (WCAG 1.4.1).

## Phase 2 — Burn-in on download (builds on Phase 1)

The library card's download control gains a second action, **"Download with
captions"**, shown only when the entry has captions. Plain "Download" is
unchanged.

- **Local:** new endpoint `POST /library/<id>/download-captioned` → ffmpeg burns
  the sidecar VTT over the stored reel (`-vf subtitles=…` with white text on a
  semi-transparent box for legibility over any footage) into a temp file, served
  as an attachment. One re-encode, on the box that already has ffmpeg.
- **Cloud:** browser-side `ReelEncoder.burnCaptions(reelUrl, vttText, callbacks)`
  — decode the reel from its presigned R2 URL through the CanvasSink the encoder
  already uses, draw each frame to canvas, overlay the active cue's text via the
  same `fillText` path title cards use, re-encode through the existing
  CanvasSource/mediabunny pipeline, and hand the result to a browser download
  (no R2 upload — it's a one-off local export). New logic is cue-lookup-per-frame
  and decoding an existing MP4 rather than source clips.
- Both show progress (reuse the generation progress affordance) and are
  cancellable. Re-encode generation-loss is accepted for a rare, deliberate
  export.

## Testing

- **Python (pytest):**
  - `captions.build_webvtt` — cue offsets across multi-segment / multi-video
    reels, clamping at clip boundaries, single-line segments. When the segment
    list yields zero cues, `build_webvtt` returns `None`; generation then writes
    no sidecar / sets no caption field, and the entry shows no CC button.
  - `GET /library-captions/<id>` returns `text/vtt` in both modes (mocked
    storage); 404 for an entry without captions.
  - Generation writes the sidecar (local) and `/plan` returns `captions_vtt` +
    `captions_key` (cloud) — extend existing generate/plan tests, keeping the
    `patch("generator_app._library_add")` guard.
  - Local burn-in endpoint: assert the ffmpeg command shape and that an output
    file is produced (ffmpeg mocked in unit tests).
- **Browser (verified live in the running app):** CC button toggles the track
  and persists across reels; caption timing lines up with playback; local and
  cloud burn-in each produce a captioned file. One real ffmpeg smoke run for
  local burn-in.

## Out of scope

- Backfilling captions for existing library reels.
- Editing caption text or styling in-app.
- Per-reel caption state (the toggle is a global viewer preference).
- Speaker labels in captions.
