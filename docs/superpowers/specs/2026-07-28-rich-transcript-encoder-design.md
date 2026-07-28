# Rich Transcript Encoder — Design

**Date:** 2026-07-28
**Status:** Design approved. Not built.
**Relationship to `sizzle_reel_design.md`:** that document designs this capability for the
Forven/HumanLens repo. This document designs it for Sizzle Reel, against measured evidence.
§"Departures" records where the two differ and why.

---

## 1. Problem

`shared.py` already consumes **rich** transcripts — `[M:SS-M:SS] Speaker: text`, sentence-level, with
real end times — and uses them for exact clip boundaries and caption timing
(`transcript_tier`, `lines_in_range`, `group_lines_into_segments`).

Nothing produces them for production input. Forven exports are **plain tier**: turn-level, start-only,
one line per 30–60s speaker turn.

```
[02:01] Participant: We try to get him groomed every couple months, um, just 'cause he gets
matted 'cause he laid, lays down so much. But we t- try to keep him, um, around the family.
We'll give him pets. We have a toddler that likes to play with him...
```

That single line is the smallest addressable unit today. Every clip drawn from it carries trailing
air, and the third sentence of a five-sentence turn cannot be clipped at all.

**The encoder closes this gap:** given a video and its plain Forven transcript, it produces a rich
transcript carrying Forven's exact words and speaker labels with real per-sentence start and end
times measured from the audio.

---

## 2. Evidence (spike, 2026-07-28)

Run against `FORVEN VIDEOS/forven-interview-14076753-….webm` (4:23, 1280×720 VP9/Opus) and its
plain `.txt` (21 turns). Prototype: `faster-whisper` word timestamps reconciled to Forven text with
`difflib.SequenceMatcher`.

| Measurement | Result |
|---|---|
| Source WebM | 53.3 MB |
| Mono 16 kHz WAV / Opus @24k | 8.05 MB / **0.67 MB** |
| WebM duration metadata | **`N/A`** — absent, as the design doc predicted |
| True duration vs transcript's last line | 263.7s vs `[04:23]` — agree |
| Whisper `base`: word match / exact sentences / drift / time | 96.1% / **47 of 48** / median −0.32s / 19.6s |
| Whisper `tiny`: word match / exact sentences / drift / time | 92.5% / **47 of 48** / median −0.06s / 16.1s |

Three findings drive the design:

1. **Anchor sufficiency.** Dropping `base` → `tiny` costs 3.6 points of word match and **zero
   sentences**. A sentence needs only one or two matched words to be pinned, because Forven supplies
   all the actual text. The ASR is an anchor source, not a transcript source. *This is what makes
   browser-side viable.*
2. **No clock offset.** Turn-start drift is median −0.06s to −0.32s with no systematic bias. The
   ElevenLabs-vs-MediaRecorder clock divergence central to `sizzle_reel_design.md` §2.4 **does not
   exist in Forven exports.**
3. **A hosted forced-alignment vendor is unnecessary.** 47/48 exact boundaries from a free,
   already-installed model. No API key, no per-minute cost, no unverified file-size limit.

Caveat: one interview, clean audio, two speakers. §9 lists the validation this needs.

---

## 3. Scope

**In scope.** The `encoder` package (core, ASR adapter, service, CLI), the browser half, and wiring
into the cloud upload flow so an uploaded video with a plain `.txt` yields a rich `.txt` in R2.

**Out of scope.** Any change to `shared.py`, `app.py`'s analysis, or the generator. Any change to the
transcript file format. Reel definition, rendering, access control. `needs_review` schema, merged-word
repair, VAD, mezzanine transcoding (see §8).

---

## 4. Architecture

One new top-level package. It imports **nothing** from `app.py`, `generator_app.py`, or `shared.py`.
Its only coupling to the rest of the system is the transcript file format, so the folder can be lifted
out and deployed independently.

```
encoder/
  core/                    pure Python — no Flask, no ffmpeg, no ML
    forven.py              plain transcript → turns → sentences
    reconcile.py           align Forven tokens ↔ ASR words → per-sentence times + confidence
    emit.py                format rich lines; monotonicity clamp; rounding rules
  asr/
    local.py               faster-whisper → word stream (fallback path + CLI)
  service.py               Flask; own Dockerfile + requirements
  cli.py                   python -m encoder <folder>
static/transcript-encoder.js   browser half
```

`core/reconcile.py` is the asset: it holds the only subtle correctness, it is pure, and it has
**exactly one implementation** shared by the browser path, the fallback path, and the CLI.

### Component contracts

| Module | Input | Output |
|---|---|---|
| `forven.parse` | plain transcript text | `[{start, role, text}]` turns |
| `forven.sentences` | a turn's text | `[str]` sentences (§6 rules) |
| `reconcile.align` | Forven sentences + word stream | `[{role, text, start, end, confidence}]` |
| `emit.rich` | reconciled sentences | rich transcript text + stats |
| `asr.local.words` | audio path | word stream |

**Word stream** is the interface between ASR and core, and is identical in both paths:

```json
[{"w": "kibble", "s": 55.42, "e": 55.88}, ...]
```

---

## 5. Data flow

```
PRIMARY (browser ASR)                          FALLBACK (server ASR)
─────────────────────                          ─────────────────────
video File (never uploaded to us)              video File
  └ mediabunny AudioBufferSink → 16k mono        └ mediabunny → 16k mono Opus (~0.7 MB)
  └ transformers.js whisper-tiny in a Worker           │
      WebGPU, falling back to WASM                     │
      ▼                                                ▼
  POST /encode/words {transcript, words}   POST /encode {transcript, audio}
      ~30 KB                                      ~0.7 MB
            │                                          │
            └──────────────► encoder core ◄────────────┘
                                  │              (asr/local.py runs first)
                                  ▼
                          rich transcript text + stats
                                  │
                   browser PUTs the .txt to R2 (presigned, as today)
```

Video goes browser → R2 only, exactly as it does now. `isSupported()` selects the path; the user sees
one operation either way.

### Service endpoints

| Endpoint | Body | Returns |
|---|---|---|
| `POST /encode/words` | `{transcript: str, words: [...]}` | `{rich: str, stats: {...}}` |
| `POST /encode` | multipart: `transcript`, `audio` | `{rich: str, stats: {...}}` |
| `GET /health` | — | `{ok: true}` |

Both funnel into the same core. The faster-whisper model is **lazy-loaded** behind a double-checked
lock, mirroring `app._get_whisper_model()`, so the primary path costs no RAM.

### Browser interface

`window.TranscriptEncoder = { isSupported(), encode(file, transcriptText, callbacks) }` — the same
shape as the existing `window.ReelEncoder`.

Model weights are served from the app's own origin (`/static/models/`), consistent with the vendored
`static/vendor/mediabunny.mjs` precedent and with the strict-CSP constraint. If repo size becomes a
problem, they move to R2 behind the existing presigned-GET pattern.

---

## 6. Algorithm

**Sentence splitting.** Split on terminal `.` `?` `!` followed by whitespace and a capital, digit or
opening quote. Do not split after an abbreviation (`p.m.`, `Dr.`, a single initial). Disfluency
prefixes ("Um,", "So,") stay attached to their sentence. Short answers ("Yeah.") remain their own
sentence. These are `sizzle_reel_design.md` §6c's rules, which were derived from and validated
against real transcripts.

**Reconciliation.** Normalise both token streams (lowercase, strip non-alphanumerics apart from the
apostrophe). Run `difflib.SequenceMatcher(autojunk=False)` over Forven tokens vs ASR tokens and take
the matching blocks, giving a partial map from Forven token index → ASR word start/end. A sentence's
start is the first mapped token at or after its first index; its end is the last mapped token at or
before its last index.

**Confidence** is the fraction of a sentence's tokens that matched.

**Fallbacks.** A sentence with one side mapped derives the other from the neighbouring boundary. A
sentence with neither (1 of 48 in the spike) starts at the previous sentence's end and is given a
word-count-proportional duration. Both cases are counted in stats.

**Rounding.** Start truncates (earlier is safe lead-in); end rounds **up** (truncating an end clips
the speaker's final word — the defect the rich format exists to remove). This matches
`transcriber._seconds_to_timestamp_ceil`.

**Monotonicity clamp.** Truncate-start plus ceil-end can make a line overlap its successor
(`0:13-0:16` followed by `0:15-0:22` in the spike). Rule: `end = min(ceil(end), next_emitted_start)`,
where `next_emitted_start` is the **already-rounded** start of the following line — the value that
actually appears in the file, so the clamp is checked against what a reader parses. If that would
leave `end <= start`, keep the unclamped value and accept the overlap. This preserves the
never-clip-the-last-word guarantee while keeping output monotonic in the normal case.

**Stats** returned per file: token match rate, and counts of sentences that are exact, partial, and
unanchored. Surfaced in the UI. Deliberately **no** sidecar file and **no** `needs_review` schema —
see §8.

---

## 7. What this does not change

- **`shared.py` is untouched.** Output is already sentence-level rich, so `read_transcript`'s rich
  branch passes it through `expand_anchors` unchanged.
- **Interviewer exclusion keeps working.** Forven's `Interviewer:` / `Participant:` labels pass
  straight through to `is_interviewer_label`.
- **No Forven `.txt` present?** Existing behaviour stands — `transcriber.py` already emits rich format
  from Whisper alone.
- **Determinism stops being a constraint.** `normalize_transcript` must be deterministic because both
  services normalise independently at read time. The rich `.txt` is produced **once and stored**, so
  only the file needs to agree; ASR non-determinism is irrelevant. This design removes that constraint
  rather than inheriting it.

---

## 8. Departures from `sizzle_reel_design.md`

| That design | Here | Why |
|---|---|---|
| Hosted forced-alignment API (D1), with vendor limits as the #1 unknown | Local/browser Whisper as an anchor source | Spike: 47/48 exact boundaries free. No vendor, key, or per-minute cost. |
| Two-stage clock-offset recovery (D1a) | None | Spike: drift is median −0.06s with no systematic bias. Forven's clock already matches the video. |
| Align participant turns only (D1a) | Both roles | That rule exists because HumanLens' agent audio is often absent from the recording. Forven exports have both roles present; both are needed for captions and context anyway. |
| Render one-off jobs, `media_jobs`, concurrency caps, admin job UI (D5, D6, §10, §13) | None | Each encode runs in its own tab against no shared resource. There is no queue to manage. |
| Mezzanine transcode before alignment (D3, §6 step 3) | None | That requirement exists to force one shared encode timeline. Here timings come from audio decoded out of the delivered file itself, and the clip extractor already re-encodes. |
| `needs_review` + `review_tokens` schema, merged-word flagging (§6a) | Per-file stats only | No merged-word corruption observed in Forven exports. Build the detector when there is something to detect. |
| Segment IDs as the selector contract (D2) | The transcript file | The selector here is this app's own analysis path, which already keys on raw line text. |

---

## 9. Testing

- **Core is pure**, so it unit-tests against synthetic word streams with no audio: sentence splitting
  (including abbreviations and short answers), reconciliation against a stream with dropped and
  substituted words, the rounding rules, and the monotonicity clamp.
- **Golden test.** The real interview's word list is checked in as a ~30 KB fixture with its expected
  rich output. This pins the 47/48 result so a regression is visible.
- **Validation before trusting it broadly.** Run the CLI across the remaining `FORVEN VIDEOS`
  interviews, including a noisier one and the longest available, and confirm the match rate and
  exact-sentence counts hold. Watch clips cut from the resulting timings — that is the only real proof.

---

## 10. Build order

1. **`core/` + `cli.py` + tests.** Hardened spike code. Immediately useful: encode the existing
   `FORVEN VIDEOS` folder locally and validate per §9.
2. **`service.py` + Dockerfile + requirements.** Both endpoints, lazy-loaded model.
3. **`static/transcript-encoder.js`.** mediabunny audio extraction, transformers.js worker, capability
   detection, fallback to `POST /encode`.
4. **Wire into the cloud upload flow** so an uploaded video with a plain `.txt` produces a rich `.txt`.

Steps 1–2 deliver the fallback path and the CLI. Step 3 delivers the primary path. The CLI falls out
of step 1 rather than being separate work.
