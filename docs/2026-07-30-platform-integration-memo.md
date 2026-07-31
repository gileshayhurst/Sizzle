# Sizzle Reel — deployment handover

**From:** Giles · **Date:** 2026-07-30

How to run this code on your infrastructure. It currently runs on a separate Render account
with its own object storage, entirely isolated from HumanLens. Nothing about that is load-
bearing — it's all Docker and environment variables, and it was built to be moved.

---

## The one thing to know first

Everything here eats the same input: **a folder (or bucket prefix) of `video.mp4` +
`video.txt` pairs**, where the `.txt` is one line per turn:

```
[MM:SS] Participant: What they said.
```

That's the whole contract. The encoder, the analysis step, the clip generation and the
caption builder all work off those two files sitting beside each other with the same stem.

**So integration is mostly one adapter:** something on your side that takes an interview
session and writes those two files into a prefix. Everything downstream already works. If
you get that adapter right, the rest of this document is environment variables.

---

## Three deployable units

They're independent — you can take one and ignore the others.

| Unit | What it does | Image |
|---|---|---|
| **`encoder/`** | The piece the brief was about. Plain transcript in → rich transcript with real per-sentence start/end times out. | `encoder/Dockerfile` |
| **`app.py`** | Web app: transcription, Claude analysis, and the UI. Port 5000. | `Dockerfile.app` |
| **`generator_app.py`** | Clip extraction, stitching, reel library. Port 5001. | `Dockerfile.generator` |

`encoder/` imports nothing from the other two. It has its own `requirements.txt`, and
notably **needs no ffmpeg** — it decodes audio through PyAV. The other two images install
ffmpeg because clip extraction requires it.

---

## Deploying the encoder alone

This is the smallest useful thing and probably where to start. Three ways to run it, same
code underneath:

**As a library.** `from encoder.core import encode` — pure functions, no Flask, no I/O.
Takes transcript text plus ASR word timings, returns the rich transcript and stats. If you
already have word-level timings from somewhere, this is a direct import with no service at
all.

**As a batch job.** `python -m encoder.job <prefix> --workers 8` encodes every un-encoded
interview under a storage prefix in parallel, writes the rich transcript to `<stem>.txt`,
and preserves the original as `<stem>.forven.txt`. Needs bucket credentials and nothing
else. About 90 seconds per interview per core.

**As a service.** `POST /encode/words` and `POST /encode`, gunicorn, one worker. Only the
second endpoint loads a Whisper model — set `ENCODER_AUDIO_FALLBACK=0` and the service runs
comfortably in 512 MB.

The batch job is the fast path and the one I'd suggest wiring to an admin action.

---

## Deploying the whole tool

### 1. Point storage at your bucket

`storage.py` is the only file that knows where data lives. It switches on `APP_MODE`:

- `APP_MODE=local` — plain filesystem, for development
- `APP_MODE=cloud` — S3-compatible, which is what you want

Set `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_ENDPOINT_URL` and it will talk to
your bucket instead of mine. The API is deliberately small — upload, download, read/write
JSON, list keys, presign — so if you'd rather it used your existing storage service, that's
the one file to reimplement.

Key layout it expects: `sessions/<uuid>/` for input files, `library/sizzle_library.json`
for generated reels.

### 2. Write the adapter

The part only you can do: turn an interview session into the input contract above. In
practice that means writing the recording (as MP4) and a `[MM:SS] Role: text` transcript
into a `sessions/<uuid>/` prefix. If your recordings are WebM you'll want a transcode step —
the clip extractor re-encodes anyway, but seeking into browser-produced WebM is unreliable.

### 3. Deploy the services

`render.yaml` already describes all three with their environment variables — copy the blocks
into your blueprint and fill in the values. They're Docker services, so nothing depends on
the host's Python or system packages.

Other environment variables worth knowing:

- `ANTHROPIC_API_KEY` — the analysis step calls Claude. This is the only external API.
- `GENERATOR_URL` — injected into the frontend so it knows where the generator lives.
- `ALLOWED_ORIGINS` — comma-separated CORS origins on the generator and encoder. Leave it
  unset and CORS stays permissive, which is fine locally and not fine in production.

### 4. Put authentication in front of it

**This app has no auth of any kind.** It was built isolated and single-user, so every
endpoint is open, including the encode dispatch. Nothing in it will resist being called by
a stranger.

That's the one thing that can't be skipped and can't be inherited — your platform already
has an org and sharing model, and mine has no concept of a user to map onto it. Simplest
version is to put the services behind your existing session check and scope each storage
prefix to an org.

### 5. Add the entry point

Once it's deployed, integration into the flow is a link that opens the app against a
prepared session prefix. If you'd rather rebuild the UI inside HumanLens, the useful HTTP
surface is small — `/analyze`, `/transcripts`, `/generate`, `/library` — and the frontend is
vanilla JS with no framework, so it's a reference implementation rather than something to
port into React.

---

## Things that will bite

- **State lives in JSON blobs, not a database.** The reel library, recent folders and prompt
  history are files in storage. That works, but on a real platform they probably want to be
  tables — worth deciding early rather than after there's data.
- **Don't rewrite a transcript while a session is open.** Selections are keyed by the text
  of the line, so if the encoder rewrites `.txt` underneath a user who already picked
  moments, their picks stop matching and generation fails. The app handles this by encoding
  *before* it opens a folder, and waiting. Keep that ordering.
- **Render one-off jobs need a paid base service.** A generous `planId` on the job doesn't
  get you past it — Render checks the base service's plan. Irrelevant if you run the batch
  job somewhere else.
- **Clip extraction must re-encode.** There's a tempting `-c copy` optimisation that
  produces clips starting mid-GOP; they freeze on playback. It's commented in the code.
- **Nothing of my infrastructure transfers.** Separate cloud accounts, and an API key that
  would need revoking rather than moving. Assume a fresh deploy, not a migration.

---

Happy to walk through any of it, or to do the first deploy alongside whoever picks it up.
