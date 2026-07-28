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

## Env

- `ENCODER_MODEL_SIZE` — whisper size for the fallback path (default `base`)
- `ALLOWED_ORIGINS` — comma-separated CORS origins (default `*`)
