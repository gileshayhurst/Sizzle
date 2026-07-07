# Faster-Whisper Transcription Speedup — Design

**Date:** 2026-07-06
**Status:** Approved (pending spec review)

## Problem

Transcription is the dominant cost in the pipeline for long videos. The web app
(`app.py`) uses the stock `openai-whisper` `base` model on CPU. On Render (no GPU)
this runs at roughly real-time or slower, so a 15-minute video takes ~10–15 minutes
to transcribe. Worse, transcription is **serial** (`max_workers=1` in
[app.py](../../../app.py) `_transcribe`), so three 15-minute videos take 30–45
minutes before the user can begin selecting highlights.

This dwarfs every other cost in the pipeline (clip extraction, stitching, upload)
combined.

## Goal

Cut transcription wall-clock time with two independent, stacking levers:

- **Lever A — engine swap:** Replace `openai-whisper` with `faster-whisper`
  (a CTranslate2 reimplementation of the same Whisper models). ~4x faster on CPU,
  same `base` weights, near-identical accuracy, lower memory.
- **Lever B — adaptive parallelism:** Transcribe multiple videos concurrently,
  auto-tuned to the detected CPU count so it scales correctly on any Render instance
  without configuration.

## Decisions

- **Model tier:** `base` — the same weights used today. Chosen for a faithful swap:
  identical transcript quality, multilingual, zero accuracy risk. Pure speed win.
- **Compute type:** `int8` — where most of the CPU speedup comes from. WER impact on
  `base` is negligible in practice. Fallback to `float32` (one-line change) if quality
  regressions are ever observed.
- **Parallelism:** adaptive to `os.cpu_count()`, since the Render instance size is
  unknown. No hardcoded worker count, no config to maintain.

## Architecture

The swap is contained to three files plus a dependency change. The key design move
that keeps it clean: `_split_into_sentences()` (and its 9 tests) operates on plain
dicts of the shape `{"start", "text", "words": [{"word", "start", "end"}]}`.
faster-whisper instead returns `Segment` objects (a generator, not a list) with a
different attribute shape (`segment.start`, `segment.text`, `segment.words[].word/.start/.end`).

We introduce a **thin adapter** inside `transcribe_video()` that converts
faster-whisper `Segment` objects into the exact dict shape `_split_into_sentences`
already expects. The sentence-splitting logic — the tricky, well-tested part — does
not change at all. Only the engine call and the small translation layer change.

```
transcribe_video(path, model)
  ├─ segments, info = model.transcribe(path, word_timestamps=True)   ← faster-whisper API
  │        returns (segments_generator, info)
  ├─ for each Segment → adapt to dict {start, text, words:[{word,start,end}]}
  └─ _split_into_sentences(dict)                                     ← UNCHANGED
```

### Files touched

| File | Change |
|------|--------|
| `transcriber.py` | Engine call (`model.transcribe` unpacks 2-tuple) + Segment→dict adapter. `_split_into_sentences` and `_seconds_to_timestamp` unchanged. |
| `requirements.txt` | Replace `openai-whisper` with `faster-whisper`. |
| `app.py` | `_get_whisper_model()` constructs `WhisperModel(...)` instead of `whisper.load_model("base")`; `_transcribe` uses adaptive worker count instead of `max_workers=1`. |
| `sizzle.py` | Model construction updated to `WhisperModel(...)`; stays single-video-at-a-time (no parallelism in the CLI). |

## Model construction

```python
from faster_whisper import WhisperModel
WhisperModel(
    "base",
    device="cpu",
    compute_type="int8",
    cpu_threads=cpu_threads,   # cores per concurrent job (see Adaptive parallelism)
    num_workers=workers,       # allow `workers` concurrent .transcribe() calls
)
```

Replaces `whisper.load_model("base")`. The existing lazy double-checked-lock structure
in `_get_whisper_model()` is preserved; only the constructed object changes. `num_workers`
matters only for the parallel web path; in `sizzle.py` (single video at a time) it is 1.

## Adaptive parallelism

Let `C = os.cpu_count()` and `V = number of videos needing transcription`:

- **Workers** = `min(V, max(1, C // 2))` — never more parallel jobs than videos, and
  leave cores for each job's internal CTranslate2 threads.
- **cpu_threads per job** = `max(1, C // workers)` — divide cores evenly across
  concurrent jobs so the CPU is not oversubscribed.

Examples:
- 1-core box: 1 worker × 1 thread → pure faster-whisper win, no parallelism overhead.
- 4-core box, 3 videos: 2 workers × 2 threads → real overlap without oversubscription.

This replaces the current `max_workers=1` ThreadPoolExecutor in `app.py` `_transcribe`
with the computed worker count. A **single shared `WhisperModel` instance** (constructed
with `cpu_threads = C // workers` and `num_workers = workers`) is reused across all
concurrent jobs — memory-efficient, and faster-whisper supports concurrent `.transcribe()`
calls on one model via `num_workers`. Videos are submitted to a
`ThreadPoolExecutor(max_workers=workers)`. `sizzle.py` stays single-video-at-a-time
(one model, `num_workers=1`).

**Invariant:** `workers * cpu_threads <= C` for all `C >= 1, V >= 1`.

## Cold-start caching

faster-whisper downloads CTranslate2 weights from HuggingFace Hub on first use
(~145MB for `base`). Render's filesystem is ephemeral, so without a fixed cache
location the weights re-download on every cold boot, adding latency to the first
transcription after a deploy or spin-down.

The design pins a stable cache path via `download_root` (or the `HF_HUB_CACHE` env
var) and, ideally, pre-warms the model at service startup — the same pattern as the
existing lazy `_get_whisper_model()` lock, just constructing a `WhisperModel`.

## Error handling

faster-whisper's `transcribe()` returns a lazy generator — exceptions can surface when
the segments are **iterated**, not when `transcribe()` is called. The adapter
materializes segments inside `transcribe_video()`'s scope so a mid-stream failure is
caught and surfaces as the same per-video error the current pipeline already handles
(the `future` result loop in `app.py` `_transcribe` already wraps this). No new error
paths are introduced for callers.

## Testing

- **`_split_into_sentences` tests (9):** unchanged — that function's contract is untouched.
- **`transcribe_video` tests (5):** rewritten. They currently mock `whisper.load_model`
  returning a dict. They will instead mock a `WhisperModel` whose `.transcribe()`
  returns `(iter([FakeSegment(...)]), info)`, where `FakeSegment` mimics the
  `.start/.text/.words` attributes. This verifies the adapter correctly translates
  faster-whisper's object shape → dict shape → the expected `[M:SS] Speaker: text` output.
- **New adapter test:** Segment.words (objects) → dict words translation specifically.
- **New parallelism-math test:** pure function of `(C, V)`; asserts
  `workers * cpu_threads <= C` and `workers <= V` across a range of inputs.

## Out of scope (YAGNI)

GPU support, model-tier switching UI, `distil-*` models, VAD/silence-skipping,
streaming partial transcripts. This is a pure engine swap plus adaptive parallelism.

## Expected impact

- Single 15-min video: ~12 min → ~3 min (faster-whisper alone).
- Three 15-min videos on a multi-core instance: ~45 min → ~3–4 min (both levers).
- No new paid dependencies, no per-minute cost.
