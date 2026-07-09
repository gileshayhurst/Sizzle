# Browser-Side Reel Generation (Cloud Mode) — Design

**Date:** 2026-07-09
**Status:** Approved, pending implementation
**Author:** brainstormed with Claude

## Problem

Cloud-mode reel generation runs on Render's free plan (~0.1 vCPU). Clip extraction
re-encodes every clip to H.264/AAC (`extract_clip`, [video_editor.py](../../../video_editor.py)),
which is CPU-bound. To avoid OOM kills from concurrent ffmpeg processes decoding large
VP9 `.webm` files, cloud mode is serialized to `max_workers=1`
([generator_app.py:472](../../../generator_app.py)). The result: a real workload
(10–15 clips of ~30s from six ~10-minute videos) takes 10+ minutes and effectively
does not complete. Parallelism doesn't help because two encodes just split the same
sliver of CPU.

## Goal

Move the heavy video encoding **off the server and into the user's browser** using
WebCodecs (the browser's native, hardware-accelerated H.264 encoder). The server keeps
only cheap work (transcript parsing, planning, bookkeeping), which the free plan handles
comfortably. Local desktop mode is untouched.

## Decisions (from brainstorming)

| Decision | Choice |
| --- | --- |
| Pipeline scope | Keep the Python/ffmpeg pipeline for **local** mode; add browser encoding for **cloud** mode only. |
| Browser support | Full coverage: browser encoding where WebCodecs exists (Chrome/Edge/Safari 16.4+), **server fallback** to the existing `/generate` pipeline otherwise. |
| Fade-out | Keep it — 2s fade-out on **both** video and audio, matching current output. |
| Engine | WebCodecs via **`mediabunny`** (demux/decode/encode/mux wrapper). Rejected `ffmpeg.wasm` (software-only, ~25MB, too slow — undercuts the goal) and raw hand-written WebCodecs (needless demuxer/muxer risk). |
| Packaging | **Vendor** the `mediabunny` ESM bundle into `static/vendor/` — no build step, no runtime CDN dependency (the repo has no `package.json`/bundler; `app.js` is a plain `<script>`). |
| Title cards | Re-rendered in a browser `<canvas>`, visually matched (not pixel-identical) to the ffmpeg `drawtext` version. Divergence only matters if the same reel is regenerated on both paths. |
| Progress transport | Browser path drives the existing progress UI locally — **no WebSocket** (sidesteps the idle-drop problem). |

## Architecture

### Principle: share the *planning*, fork only the *rendering*

The current server pipeline (`_run_generation_impl`) has three phases:

1. **Plan** — parse transcripts, group selections into segments, compute title-card text + ordering ([generator_app.py:411-448](../../../generator_app.py)).
2. **Execute** — ffmpeg encodes title cards + clips (the expensive part).
3. **Assemble** — concat demuxer stitches clips.

Only Execute is expensive. So planning stays on the server and is **shared** by both the
existing pipeline and the new browser path; only rendering forks. This is the key
guard against the two-paths-drift risk: segments, title text, and ordering are computed
in exactly one place.

```
Cloud generate (browser-capable)        Cloud generate (fallback / local)
  POST /plan  ──► shared planner           POST /generate ──► shared planner
       │                                          │
       ▼                                          ▼
  browser: mediabunny                        server: ffmpeg (unchanged)
   decode/fade/encode/mux                     extract_clip + stitch
       │                                          │
  PUT reel ──► R2 (presigned)                upload_stream ──► R2
       │                                          │
  POST /library (record entry)               _library_add (record entry)
       │                                          │
       └──────────────► /library-video/<id> serves both ◄────────────┘
```

### Server changes

All new server work is cheap (no ffmpeg) and runs fine on the free plan.

1. **Refactor: extract the shared planner.** Pull today's Phase-1 plan-building out of
   `_run_generation_impl` into a function (e.g. `_build_plan(...)`) that returns the
   ordered segment list with per-segment `{video, start_sec, end_sec, title_lines, width, height}`.
   Both `/generate` and `/plan` call it. `_group_lines_into_segments`, `_parse_transcript_lines`,
   title-line formatting, and `get_video_duration`/`get_video_dimensions` are reused as-is.

2. **`POST /plan`** — new endpoint. Cloud only. Body mirrors `/generate`
   (`session_key`, `mode`, `selections`, `prompt`, `output_filename`). It:
   - downloads only the needed `.txt` transcripts (as `/generate` does today, [generator_app.py:790-795](../../../generator_app.py)),
   - runs the shared planner,
   - generates presigned **GET** URLs for the selected source videos (reusing [generator_app.py:802](../../../generator_app.py)),
   - returns:
     ```json
     {
       "session_key": "...",
       "output_filename": "sizzle_reel.mp4",
       "width": 1280, "height": 720,
       "segments": [
         { "video": "forven-....webm",
           "presigned_get_url": "https://...",
           "start_sec": 12.0, "end_sec": 42.0,
           "title_lines": ["forven-...", "from 0:12", "Segment 1 / 12"] }
       ]
     }
     ```
   - **`width`/`height` is a single output resolution for the whole reel**, not per
     segment. The concatenated MP4 must have one uniform resolution (the ffmpeg path
     relies on this implicitly today — `-c copy` concat requires it). The server derives
     it via `get_video_dimensions` (ffprobe, ~0.1s, cheap) on the first selected video,
     defaulting to `1920×1080` on error as the current code does
     ([generator_app.py:419-420](../../../generator_app.py)). The browser renders title
     cards at this resolution and **scales each clip to fit it** during encode, so mixed-
     resolution sources still produce a valid single-resolution reel.

3. **`POST /library`** — new endpoint. Browser calls it after a successful upload with
   `{ session_key, output_filename, prompt, duration_seconds, clip_count, segment_starts }`.
   Server constructs the library entry (same shape as [generator_app.py:675-690](../../../generator_app.py))
   with `reel_s3_key = "{session_key}/{output_filename}"` and calls `_library_add`.
   Returns `{ "id": "<entry-id>" }`.

4. **Unchanged:** `/generate`, `/library-video/<id>`, `/video/<job_id>`, the WebSocket,
   and all `storage.py` functions. `storage.presigned_put_url` already exists and is used
   by the browser for upload. Playback works with no changes because the reel lands at the
   same S3 key the redirect endpoint already serves.

### Browser pipeline

New file `static/reel-encoder.js`; `mediabunny` vendored at `static/vendor/mediabunny.mjs`.
Loaded as an ES module (`<script type="module">`) so it can `import` the vendored bundle.
`app.js` stays a classic script and calls into the module via a small global (e.g.
`window.ReelEncoder.generate(plan, callbacks)`).

Per segment, in planned order, streamed into a **single** `mediabunny` MP4 `Output`
(concatenation is therefore free — no separate stitch step):

1. **Title card** — draw `title_lines` on an offscreen `<canvas>` sized to `width×height`
   (5s duration, 2s fade-in via canvas alpha) and feed frames to the H.264 `VideoEncoder`.

2. **Clip** — `mediabunny` reads the source from `presigned_get_url` using **HTTP range
   requests**, fetching only the bytes for `[start_sec, end_sec]` (not the whole ~50MB webm):
   - decode VP9 video + Opus audio,
   - apply the fade over the final 2s: video via canvas alpha ramp, audio via a gain ramp
     on decoded PCM samples,
   - encode to H.264 video + AAC audio.

Normalise to the same parameters the ffmpeg path uses so output is consistent: **30 fps**,
**48 kHz stereo AAC** (matches [video_editor.py:37-40](../../../video_editor.py)).

After the last segment:
- finalize the `Output` to an MP4 `Blob`,
- `PUT` it to R2 via `storage.presigned_put_url("{session_key}/{output_filename}")`,
- `POST /library` with the metadata,
- transition to the result screen (reusing existing result-screen wiring in `app.js`).

### Progress, cancel, fallback

- **Progress:** the encoder reports per-segment completion; `reel-encoder.js` updates the
  existing `#gen-log` and `#gen-bar` directly. No WebSocket for this path.
- **Cancel:** an `AbortController` aborts in-flight range fetches; the per-segment loop
  checks a cancel flag between segments and tears down encoders. No server round-trip.
- **Fallback:** on page load, feature-detect `window.VideoEncoder`/`window.VideoDecoder`.
  If absent → use the current server `/generate` flow unchanged. If present but the browser
  encode **throws** at any point → catch, log, and transparently retry via `/generate`, so a
  WebCodecs edge case never produces a failed reel. The decision point lives in
  `submitGenerate` ([static/app.js:1176](../../../static/app.js)).

## Data flow (browser path, happy path)

1. User clicks Generate → `submitGenerate` detects WebCodecs support.
2. `POST /plan` → `{ segments: [...], session_key, output_filename, width, height }`.
3. `ReelEncoder.generate(plan)` — for each segment: title card + faded clip → one MP4 Output.
4. `PUT` MP4 to R2 at `{session_key}/{output_filename}`.
5. `POST /library` → `{ id }`.
6. Result screen; playback via `GET /library-video/<id>` (unchanged).

## Testing

- **Pytest** for `POST /plan` and `POST /library` (pure Python, fully testable). Mock
  `_library_add` per the CLAUDE.md rule to avoid writing the real `sizzle_library.json`.
  Assert `/plan` returns correct segment ordering/title text for a known transcript, and
  that `POST /library` produces an entry with the right `reel_s3_key`.
- **Refactor safety:** existing generate-flow tests must still pass unchanged after the
  planner extraction (the server pipeline behavior is identical).
- **Browser encode** is verified by the spike (below) and a manual cloud run in each target
  browser. Optional later: a Playwright smoke test.

## Build sequence

**Step 0 — Spike (throwaway, do first).** A standalone `spike.html` that loads `mediabunny`,
reads one real `FORVEN` VP9/Opus webm via a presigned URL with **range requests**, applies a
2s video+audio fade, encodes H.264/AAC, and muxes a playable MP4 concatenating **two**
segments. Verify it plays in Chrome/Edge (and Safari 16.4+ if targeted). This retires the
riskiest unknowns — range-reads and clean fades — before any endpoint or UI work. If
`mediabunny` can't range-read or fade cleanly, we learn it in a day.

Then, once the spike passes:

1. Refactor the shared planner out of `_run_generation_impl`; confirm existing tests pass.
2. Add `POST /plan` + tests.
3. Add `POST /library` + tests.
4. Vendor `mediabunny`; build `static/reel-encoder.js` (title cards, clip decode/fade/encode, mux).
5. Wire `submitGenerate` fallback + support detection; drive progress/cancel from the browser.
6. Manual cloud run end-to-end in each target browser; confirm playback + library entry.

## Out of scope

- Local desktop mode (unchanged).
- Firefox/legacy support beyond the server fallback.
- Pixel-identical title cards across the two paths.
- Making `GEN_WORKERS` tunable (tracked separately) — the browser path removes the
  motivation for server-side parallelism in the common case.
