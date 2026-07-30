# Transcript Encoder

Turns a video plus its plain Forven transcript into a **rich** transcript —
`[M:SS-M:SS] Speaker: sentence` — with real per-sentence start and end times.

Standalone: imports nothing from the Sizzle Reel app. The only contract is the
transcript file format, so this folder can be deployed on its own.

## CLI

```
python -m encoder "FORVEN VIDEOS"
python -m encoder "FORVEN VIDEOS" --in-place --model tiny
```

Writes `<video>.rich.txt`. With `--in-place`, writes `<video>.txt` and preserves
the original as `<video>.forven.txt` — the `.txt` beside a video is client data
and is never overwritten silently.

## Bulk encoding: the one-off job (fastest path)

```
python -m encoder.job sessions/<uuid> --workers 8 --model tiny
```

Encodes every un-encoded interview in an R2 session **in parallel**, writing the
rich transcript back to `<stem>.txt` and preserving the client's original as
`<stem>.forven.txt`.

Run it as a **Render one-off job** against the `sizzle-encoder` service, requesting
a generous `planId` (up to 32 GB / 16 CPU).

> ⚠️ **A paid plan is mandatory for jobs.** Verified 2026-07-30: Render rejects
> `POST /services/{id}/jobs` with *"free tier plans are not supported for jobs"*
> when the job's resolved plan is free. Without `ENCODER_JOB_PLAN_ID` the job
> inherits the base service's instance type, so a free `sizzle-encoder` fails
> every dispatch. Setting a paid `ENCODER_JOB_PLAN_ID` is the cheaper fix — the
> service stays free and you pay per second only while a job runs.

Rationale is `sizzle_reel_design.md` §8:

- A persistent service billed at rest is the wrong shape for work this bursty.
- One-off jobs are **billed per second while running** and scale to zero, so an
  oversized instance is roughly cost-neutral and far faster for CPU-bound work.
- So: request lots of CPU, encode the whole folder at once, pay pennies.

Measured server rate is ~90s per interview on one core (~13x realtime), so a dozen
interviews across 8 workers is a few minutes of wall clock rather than the ~6 hours
the browser path would take for the same folder.

**Pulling video from R2 into the job is free** — R2 egress costs nothing and Render
bills outbound, not inbound. The "never send video to the encoder" rule protects
the always-on web service, whose metered egress and 512 MB ceiling are the real
constraints; it does not apply to a big one-off job reading object storage.

**Triggering is deliberately manual** (dashboard → *Run a one-off job*), matching
D9 "encoding is ALWAYS admin-initiated". Direct launch from the web app needs a
`RENDER_API_KEY` on an internet-facing service plus the §10 security controls —
not worth building until manual triggering proves insufficient.

## Service

```
POST /encode/words   {transcript, words}          -> {rich, stats}
POST /encode         multipart transcript+audio   -> {rich, stats}
GET  /health
```

`/encode/words` is the primary path: the browser runs the ASR and posts a ~30 KB
word list. `/encode` is the fallback for browsers that cannot, and takes mono
16 kHz audio (~0.7 MB for a 4-minute interview). **Never send video to this
service** — that is the whole point of the split.

## How it works

The ASR is an *anchor* source, not a transcript source — Forven supplies every
word that reaches the output. `difflib` maps the ASR's word stream onto Forven's
canonical text, so each sentence inherits real times while keeping the client's
exact wording and speaker labels.

Measured on a real 4:23 interview: 47 of 48 sentences got both boundaries from
matched words, and `tiny` scored the same as `base` on that metric — a sentence
needs only one matched word to be pinned. Turn-start drift against Forven's own
timestamps was a median −0.06s, so there is no clock offset to correct.

## Browser path dependencies

Nothing for the ASR is vendored: the transformers.js library, the ONNX runtime and
the whisper-tiny weights all load from the CDN, version-pinned in
`static/transcript-asr-worker.js`. If the CDN is unreachable the browser path
fails and the client falls back to `POST /encode` (or keeps its plain transcript
when the fallback is disabled) — degraded, never broken.

**The WASM backend needs `dtype: 'fp32'`.** The quantised weights (`q8`, `fp16`)
fail at session creation with "TransposeDQWeightsForMatMulNBits Missing required
scale", which broke every browser without WebGPU. fp32 costs nothing at this
model size — measured on a 6s clip, WASM (5.3s) beat WebGPU (6.4s) with identical
output.

**The browser ASR is time-bounded** (`asrTimeoutMs`: 180s base + 8s per second of
audio). On expiry the worker is *terminated* — not just rejected, or it would keep
burning CPU — and the client degrades to the audio fallback, or to its plain
transcript.

## ⚠️ Measured cost of the browser path at production length

A **19.4 minute** interview, on a 16-core desktop with WebGPU:

| | Browser (`tiny`, WebGPU) | Server (`base`, faster-whisper) |
|---|---|---|
| Wall time | **1918s (32 min)** | ~90s |
| Rate | 1.65x realtime | ~0.08x realtime |
| Sentences | 200 | 200 |
| Exact anchors | 198 | 198 |
| Word match | 93.13% | 93.1% |

**Output is equivalent; speed is not.** The server is roughly **20x faster** for
the same result, because `faster-whisper`/CTranslate2 is far better optimised than
ONNX-in-a-browser.

Two consequences:

- The anchor-sufficiency thesis holds at full length — `tiny` in a browser matched
  `base` on the server exactly (200/198/93.1%). Model size genuinely does not
  matter for this job.
- Making the browser primary trades ~90s of server CPU for **32 minutes of the
  client's** time and battery, and a mid-range laptop or a WASM-only browser will
  be slower still. For long interviews the server path is worth preferring; the
  browser path is most defensible for short clips, for privacy-sensitive audio, or
  when server CPU genuinely cannot be paid for.

## Match rate is a truncation alarm

A low `match_rate` usually means **the video is shorter than the transcript**, not
that alignment is bad: text that isn't in the video cannot anchor to it.

Measured across the 11 reference interviews, ten scored 90–100%. The one outlier
scored 51.4% — session `4e7ccf39`, whose recording is 8:21 against a 14:15
transcript. So treat anything below ~85% as "check whether the recording is
complete" before suspecting the encoder.

Sentences past the end of the video fall back to interpolated times and can
therefore point past the media. That degrades safely — `group_lines_into_segments`
clamps to `video_duration`, and the browser encoder clamps via `computeDuration()`
— but such clips are worthless, so a truncated source is worth catching here.

## Env

- `ENCODER_MODEL_SIZE` — whisper size for the fallback path (default `base`; `tiny`
  anchors just as well, see above, and is the better choice when memory is tight)
- `ENCODER_AUDIO_FALLBACK` — set `0`/`false`/`off` to make `POST /encode` return
  `503` instead of loading Whisper (default on)
- `ALLOWED_ORIGINS` — comma-separated CORS origins (default `*`)

## Sizing: the model is only needed by the fallback

`/encode/words` loads **no model** — it is pure text processing, and the Whisper
model is lazy-loaded behind a double-checked lock so importing the service costs
nothing. Only `POST /encode` allocates it.

So a memory-constrained deployment (Render free tier, 512 MB) can serve the
primary path safely by setting `ENCODER_AUDIO_FALLBACK=0`. That matters because
an OOM takes the whole container down and interrupts other requests, whereas a
`503` fails only the one caller — and that caller keeps its plain transcript,
which still produces a working reel, just with looser clip boundaries.

Enable the fallback (and size the instance for it) when you need to support
browsers that cannot run the model at all.
