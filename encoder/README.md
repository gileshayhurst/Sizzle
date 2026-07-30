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
