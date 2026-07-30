# Sizzle Reels — Clip Extraction from Interview Video (DESIGN 2026-07-25)

**Status:** DESIGN — **not built**. No schema, no code, no migration yet. Written to be picked up cold.

**Revised 2026-07-25 after design review.** Three P0 corrections were folded in — two were defects in
the original draft, not refinements: alignment must run on **participant turns only** (§3 D1a), and the
alignment WAV must be derived **from the mezzanine** (§6 step 3). A **Phase 0 spike now gates all
implementation** (§14) — the P0 findings are unfalsifiable until one real recording has been aligned
end-to-end, and each fails silently rather than loudly. Also added: transcript repair (§6a),
low-confidence policy (§6b), the v1 agent-bleed limitation (§7a), the ffmpeg sequencing constraint
(§8), a buildable detection control + job concurrency cap (§10), and agency-viewer composition (§11a).

**What it is:** A **sizzle reel** is a short video assembled from clips taken out of one or more
interview recordings — the participant's own words, stitched together. An **external process**
(outside this repo) decides *which sentences* belong in the reel.

## ⛳ Scope (set by the operator 2026-07-25) — read this first

> **The work in scope is TRANSCRIPT ENCODING: a one-time operation per interview that produces a
> time-addressable, per-sentence index. Reels are assembled on demand, later, against that index.**

| | |
|---|---|
| **IN SCOPE — the encode** | Probe → mezzanine → alignment audio → transcript repair → participant-turn alignment → sentence segmentation → VAD → divergence check. The resulting **segment index** + the **mezzanine**, the job that produces them, the admin UI to run/track/re-run, the **backfill** of existing interviews, and exposing the index to the external selector. |
| **OUT OF SCOPE — deferred** | Reel definition + storage (`sizzle_reels` / `sizzle_reel_clips`), reel admin UI, virtual preview, the render job, applying the cut-quality rules, and the agent-bleed sign-off (§7a). |
| **⛔ EXPLICITLY NOT BUILT — reel access control** | **No sizzle-reel access-control capability is implemented in this work** (operator, 2026-07-25). §11 (intersection predicate), §11a (agency composition), the cross-org share-time warning, and the revoke cascade are **specification only**, retained for the later phase. Do not build any part of them now — including "just the model" or "just the predicate." |

**Why the reel design stays in this doc.** The encode is a **one-time, expensive-to-repeat** operation
over every interview. Its output format is only correct if it anticipates its consumer — so the
downstream design is retained here as the specification the encoding is built *against*, not as work
to do now.

**Two consequences that are easy to get wrong:**

1. **The mezzanine is part of the ENCODE, not of reel-building.** A timestamp is meaningful only
   relative to a specific video artifact. Aligning against the WebM now and producing the mezzanine
   later would make every stored timestamp wrong by an unknown constant — the §6 step-3 defect,
   deferred and much harder to detect. The encode must fix the timeline it encodes against.
2. **Compute-now-use-later is deliberate.** VAD silence boundaries, alignment `confidence`, and
   `raw_text` are all written by the encode even though only reel-building reads them, because
   recomputing them requires the audio again and a second pass over every interview.

**Re-encoding is expected eventually** (a better aligner, a changed repair rule), so segments carry an
`encoder_version` — enabling a **targeted** re-encode rather than all-or-nothing.

### ⚖️ Decision priorities (operator, 2026-07-25) — apply these to every open choice

> **1. Ease of use → 2. Quality → 3. Cost.** In that order.

Use this to settle trade-offs rather than re-deriving them. Consequences already applied:

- **Hosted alignment API over self-hosting** (§3) — wins on all three, decisively on ease. Region
  co-location and vendor-independence are *risk* arguments, not ease/quality/cost ones, so they do not
  outrank this ranking.
- **Don't micro-tune `planId`** (§8) — pick a generous plan and move on. Per-second billing makes an
  oversized instance roughly cost-neutral for CPU-bound work, and sweeping it is ease-of-use spend on
  the lowest-priority axis.
- **Bounded exceptions where cost is a *failure*, not an optimization** — the backfill concurrency cap
  (§10) stays, because an unbounded loop launching hundreds of paid instances is a runaway, not an
  inefficiency.

**The problem this design solves:** clipping a whole *turn* is approximately possible today;
clipping the **3rd sentence of a 5-sentence turn** is not. §2 shows the situation is worse than that
framing suggests — we don't currently have reliable timing for turn-start clips either.

**Locked decisions:**

| # | Decision | §  |
|---|---|---|
| D1 | **Forced-align the known transcript text against the recording's own audio track.** Not interpolation, not re-transcription, not ElevenLabs' turn timestamps. | §3 |
| D1a | **Align participant turns only**, two-stage (recover the clock offset from an anchor, then windowed per-turn). The agent's opening is frequently **absent** from the recorded audio, so a both-roles pass corrupts the start of every interview. Agent spans are *derived* from inter-turn gaps, not aligned. | §3 |
| D2 | **Segment IDs are the contract** with the external selector — it consumes a canonical numbered sentence list and returns IDs, not text. | §4 |
| D3 | **Normalize each recording to a mezzanine MP4** (fixed short GOP, one canvas, corrected orientation) before anything clips it. | §5 |
| D4 | **A reel is a definition, not a file.** Virtual preview while editing; render to MP4 on publish. | §7 |
| D5 | **Execution substrate = Render one-off jobs**, sized per job via `planId`. No ffmpeg on the web dyno; no AWS MediaConvert in v1. | §8 |
| D6 | **Direct launch from the web app**, not a cron dispatcher. Retry/reconcile rides an interval-gated admin-blueprint hook. | §9 |
| D7 | **Reels may mix interviews across studies and orgs** at platform-admin discretion, with a mandatory share-time warning naming every commissioning org represented. | §11 |
| D8 | **A reel is viewable by an org only if *every* source interview is actively shared to it** — authorization is an intersection, re-checked per request. | §11 |
| D9 | **Encoding is ALWAYS admin-initiated. Never automatic.** No hook in the upload-finalize path; the encode pipeline touches **nothing** in the participant flow. | §6 |

---

## 1. Current state (verified in code 2026-07-25)

| Thing | Reality |
|---|---|
| Video | WebM (VP8/VP9 + Opus) produced by browser `MediaRecorder`, streamed to S3 via multipart. Source of record. |
| Audio | **One track**, an `AudioContext` mix of participant mic + ElevenLabs agent TTS (`App.jsx` `startRecording`). |
| Transcript | Raw ElevenLabs conversation payload in `interview_sessions.transcript_json`; text rendered by `services/voici_runtime/transcripts.py`. |
| Transcript timing | **`time_in_call_secs` per turn — and nothing else.** |
| ffmpeg / ffprobe | **Not present anywhere in the repo.** |
| `render.yaml` | **Only `type: web`** (prod `HumanLens-v3` + `forven-staging`). No worker, no cron, no jobs. |
| Background work | Opportunistic `before_request` hooks on the participant blueprint. `backend/workers/` is an empty package. |
| Video access | `video_shares` grants + the anonymized `curated_view`, chokepointed at `services/video_sharing.py::active_share_for_viewer`. |

---

## 2. The core problem — we have no usable clip timing today

`build_transcript_text()` reads `item.get("time_in_call_secs")` and renders `[MM:SS]`. That is the
entirety of our timing data. Four distinct deficiencies follow:

1. **No word-level timing.** Nothing addresses a sentence inside a turn.
2. **No turn *end* times.** Only starts. Inferring an end from the next turn's start sweeps in
   trailing silence plus the agent's think time.
3. **Whole-second truncation** in the rendered form (`int(time_secs)`).
4. **A different clock origin.** `time_in_call_secs` is measured from the *ElevenLabs conversation*
   start. The video is measured from the *MediaRecorder* start. `beginUploadSession()` runs in
   parallel with `startSession`, and the recorder begins mic-only then polls up to 20 s to discover
   the agent's audio element — so the offset between the two clocks is **unknown and varies per
   session**.

> **Consequence:** "clip from the start of a turn" is not actually reliable today. It merely *appears*
> to work because a turn boundary has silence on both sides, which forgives a second or two of error.

There is also a **fifth** problem specific to this codebase: the recorded video can be **shorter than
the conversation**. The canonical incident (session `4e7ccf39…`, 2026-06-25) had a 14:21 conversation
and an 8:21 recording. Clip timings derived from ElevenLabs' clock would silently point past the end
of the file.

---

## 3. Decision D1 — Forced-align the transcript against **our own** recording audio

We already have the exact words. We are solving for *when*, not *what* — which is forced alignment,
a far easier and cheaper problem than transcription.

**Options considered:**

| Option | Verdict |
|---|---|
| **(a) Proportional interpolation** — estimate sentence offsets from character/word counts within the turn | **Rejected.** Cheap and needs no new infrastructure, but it will clip words mid-syllable. Unacceptable for customer-facing output. |
| **(b) Re-transcribe with a word-timestamp ASR** (Whisper, Deepgram, AssemblyAI) | **Rejected.** Produces *different text* from the transcript the selector saw, creating a reconciliation problem we don't need. |
| **(c) Align against ElevenLabs' conversation audio** | **Rejected.** Timings land in EL's clock, so the §2.4 offset problem survives and must be solved separately. |
| **(d) Align against the video's own audio track** | **CHOSEN.** | 

**Why (d) wins — it collapses four problems into one step:**

| Problem | Resolution |
|---|---|
| Mid-turn sentence timing | Word-level timestamps → sentence boundaries fall out |
| No turn end times | Alignment yields end-of-last-word per turn |
| Whole-second granularity | Word-level alignment is ~20–50 ms |
| Different clock origin | **Timings are natively in video-file time by construction.** There is no offset to compute. |

**Free byproducts:**

- **Truncation detection.** Transcript text that fails to align is text that isn't in the video. This
  is the "partial video divergence flag" that `video_runtime.md` lists as an unbuilt Phase-1 item.
- **Speaker ranges without diarization.** The transcript already names the role of each turn, so
  alignment yields exact participant-vs-agent time ranges for free.

### ⛔ D1a — Align **participant turns only**, not the both-roles transcript (P0, review 2026-07-25)

An earlier draft of this doc said to align the *full* both-roles transcript against the mixed audio.
**That is wrong and produces silently bad output.** Verified in `App.jsx` (~L3665–3742): the recorder
connects the participant mic **immediately**, but the agent's TTS is discovered by **polling every
400 ms for up to 20 s** — `setAudioMixStatus("mic-only")` exists precisely because that path is real.
The agent speaks first (`[00:00] Interviewer: Hello…`), so **the agent's opening is frequently not in
the recorded audio at all**.

A forced aligner must map every input token to some time span. Given text that isn't present, it
smears those tokens across whatever audio exists — corrupting alignment **at the start of the
interview**, which is the reel's highest-value stretch. The participant mic is present from t=0, so
participant-turn alignment is both more robust *and* sufficient for the selector.

**Two-stage approach** (windowing alone doesn't work — per-turn windows need boundaries in *video*
time, which is the very thing being solved for):

1. **Establish the clock offset.** Align one long, unambiguous participant turn with a **wide search
   window** to recover the constant offset between the ElevenLabs conversation clock and the
   mezzanine timeline.
2. **Per-turn windowed alignment.** Use that offset to bracket each participant turn, and align each
   turn's text within its own window. Errors stay contained to a turn instead of propagating.

**Agent-turn timing still exists — derive it, don't align it.** The "include the preceding
interviewer question" flag (§6) needs agent spans. Take them as the **gap between participant turn
*N*'s end and turn *N+1*'s start** — the agent's speech fills that gap. Approximate, but the question
clip only needs to be bounded, not word-accurate.

**The mixed-audio caveat still applies to output:** clips carry whatever is in the single mixed track
(see §7a). Separating the voices requires the participant-only track tracked in `runbook.md` Open
Items `[2026-07-25]`.

### Aligner choice (v1: hosted API, behind an interface)

**Settled: a hosted forced-alignment API** (ElevenLabs' forced-alignment endpoint is the natural first
candidate — same vendor as the transcript, so tokenization and normalization match). Under the
decision priorities above it wins on **all three** axes, decisively on ease:

| | Hosted API (chosen) | Self-hosted (wav2vec2-class, ~300 MB) |
|---|---|---|
| **1 Ease** | One HTTP call — no model weights, no ML deps to pin, no build change, no plan sizing | Weights to download or bake into the build (which bloats the **web service's** artifact, since jobs share it), ML deps, sizing, local debugging |
| **2 Quality** | Same vendor as the transcript — tokenization/normalization already match | Good, but we own the tuning |
| **3 Cost** | Per-minute; negligible at current volume | Cheaper only at volume we don't have |

Keep it behind a narrow interface: the durable artifact is the **segment index**, which does not care
how the timings were produced.

**Self-hosting stays available but is not recommended.** It becomes the right call only if
**vendor-independence or data residency** becomes a driver — neither is in the current priority
ranking. It is technically feasible (`planId` lets a job request up to 32 GB / 16 CPU, §8), and we do
**not** need Whisper since we already have the text. Note that self-hosting would also keep audio
entirely within us-west-2, which is a residency argument, not an ease/quality/cost one.

> ⚠️ **The one risk in this choice is an ease-of-use risk.** If the hosted aligner's file-size or
> duration limit rejects a real 15-minute recording, we would need chunking — which is exactly the
> complexity this choice was made to avoid. **This is the highest-value question in Phase 0** (§14).

> ⚠️ **System-of-record line.** Per `CLAUDE.md`, vendors supply content and handshakes, never
> authoritative metric values. **Alignment offsets are derived content** (clip-authoring data), not a
> metric — they do not drive billing, quota, or approval.
>
> **Truncation is decided by our own duration probe** (`ffprobe` on our own file, §6 step 1) compared
> against our own `conversation_duration_seconds`. That comparison is the signal, full stop.
> **Alignment failure is a corroborating hint only** — useful for showing *where* the video stops, never
> the thing that decides *whether* it is short. Wiring a vendor's alignment output into an approval or
> billing decision crosses the line and requires explicit operator sign-off.

**Verify before building:** confirm the chosen aligner's file-size / duration limits against a real
15-minute staging recording. This is unverified.

---

## 4. Decision D2 — Segment IDs are the contract with the external selector

**Confirmed with the operator 2026-07-25:** the external selection process **can** be changed to
consume and return IDs.

Preprocessing publishes a canonical, numbered, immutable sentence list per interview. The selector
reads that list and returns **segment IDs**. It performs no timing derivation and no text matching.

| Option | Verdict |
|---|---|
| Selector returns **segment IDs** | **CHOSEN.** No string matching, no timing logic on their side, no drift. |
| Selector returns **raw sentence text**, we fuzzy-match back to segments | Rejected. Adds a failure mode where a paraphrased or lightly-edited sentence silently fails to match — or matches the *wrong* segment. |

A text-matching fallback may still be built later as a convenience layer, but IDs are the contract.

---

## 5. Decision D3 — Normalize to a mezzanine before clipping

The source WebM is a poor cutting master:

- **MediaRecorder WebM commonly lacks reliable duration metadata and a seek index**, and ours is a
  concatenation of multipart chunks.
- **Sparse keyframes.** Stream-copy cuts land only on keyframes; arbitrary sentence boundaries
  require re-encoding.
- **Heterogeneous sources.** Participants record at whatever resolution, framerate and orientation
  their device produces — including the known **sideways-mobile** case
  (`project_mobile_video_orientation`). Concatenating clips across participants without a common
  canvas either fails or forces a full re-encode of every clip at render time.

So each recording is transcoded **once** to a mezzanine MP4 — H.264 + AAC, **fixed short GOP (1–2 s)**,
`+faststart`, single canvas size, single framerate, square pixels, corrected orientation, loudness-
normalized audio.

**The original WebM is never modified or replaced.** It remains the system of record.

---

## 6. Preprocessing pipeline — the answer to "what preprocessing is needed"

> **D9 — ALWAYS ADMIN-INITIATED (operator, 2026-07-25).** An operator triggers the encode from the
> admin UI, per interview or over a selection. There is **no automatic trigger** — nothing hooks the
> upload-finalize path, and **the encode pipeline touches nothing in the participant flow.** That is a
> deliberate risk reduction: the participant flow serves silent users who never report bugs, so keeping
> this work entirely on the admin side means it cannot regress that surface.
>
> Consequence: **"backfill" is not a special mode** — it's selecting the already-recorded interviews
> and pressing encode. Same path, same job, same UI.

Run once per interview, asynchronously, **on operator request**:

1. **Probe** (`ffprobe`) — true duration, codecs, resolution, framerate, rotation flag. Persist.
   This alone fixes the long-standing "WebM has no duration" problem and surfaces sideways recordings.
2. **Transcode to the mezzanine** (§5).
3. **⛔ Extract the alignment WAV FROM THE MEZZANINE** — mono 16 kHz — **not from the source WebM**
   (P0, review 2026-07-25; see the box below).
4. **Repair transcript text** (§6a) — bounded to the one observed corruption pattern.
5. **Forced-align participant turns** against that WAV, two-stage per §3 D1a → word-level timings in
   mezzanine time.
6. **Sentence-segment** each turn; persist each sentence as an addressable row with `start_ms`,
   `end_ms`, role, turn index, sentence index, alignment `confidence`, raw + repaired text, and a
   stable `public_id`.
7. **VAD pass** — record silence boundaries for snap-to-natural-boundary at cut time.
8. **Divergence check** — our probed video duration vs `conversation_duration_seconds` (§3); flag
   truncation.
9. *(Optional)* thumbnail sprite, waveform peaks.

> ⛔ **Step 3 is a correctness requirement, not a preference.** D1's central claim — timings land in
> video-file time *by construction* — holds **only if the audio aligned against and the video cut from
> share one encode timeline.** Deriving WAV and MP4 in parallel from the source WebM does not
> guarantee that: the mezzanine normalizes framerate and applies loudness processing, and the source
> is a chunk-concatenated WebM with unreliable timestamps. A parallel derivation can leave a **constant
> offset that shifts every clip with no error surfaced**. Extracting the WAV from the finished
> mezzanine makes the shared timeline structural.

### Cut-quality rules (do not skip these)

- **Never cut on the exact word boundary.** Expand to the nearest silence within ~±300 ms.
- Add **150–250 ms lead-in / lead-out** padding.
- Support an **"include the preceding interviewer turn"** flag — a sizzle-reel answer frequently
  needs its question for context. Its bounds come from the derived agent span (§3 D1a), not alignment.
- Snap to the mezzanine's GOP where it doesn't hurt the edit, to keep re-encode work small.
- **⚠️ Snap-to-silence may find no silence at a participant→agent boundary.** The track is mixed, and
  the agent often begins immediately. When no silence is found inside the search window, **fall back to
  fixed padding** rather than expanding the search — an unbounded search will swallow the agent's first
  words. Interacts with §3 D1a and §7a.
- **Low-confidence policy (§6b)** — never emit a clip whose segment alignment is below the floor
  without a human seeing it.

### 6a. Transcript repair — bounded to the observed pattern

**Observed:** join points with a dropped space inside long turns — `havesome`, `wegive`,
`healthylives`, `lovecarrots`, `um,he's`. Measured offsets within the message were **240 / 351 / 372 /
479 / 484 / 507** characters — *not* a fixed width, and *only* in long turns. That pattern fits
**streaming ASR partial-result concatenation dropping the joining space** (partial boundaries follow
speech pauses, not character counts; short turns fit in one partial and are never affected).

**⚠️ Diagnosis is not yet confirmed.** The corruption was observed in `interview-analysis/*.md`, which
are hand-made analysis artifacts, **not** a DB dump. `build_transcript_text` joins nothing within a
message, so it is very likely present in `transcript_json` itself — but **one SQL query settles it**
before anything is built:

```sql
SELECT public_id FROM interview_sessions WHERE transcript_json::text LIKE '%havesome%';
```

### ⛔ Empirically validated rule: FIX punctuation, FLAG merged words — never auto-split

**Prototyped and run against all nine transcripts 2026-07-25.** The obvious rule — "split a token that
isn't a word but does split into two words" — was implemented and **failed badly**:

| Attempt | Result |
|---|---|
| Split using `/usr/share/dict/words` | **61 false positives in one interview.** `conducting → conduct ing`, `started → star ted`, `bones → bon es`, `foods → fo ods`. The dictionary (web2) lacks inflected forms and contains obscure fragments (`ing`, `ted`, `es`), so almost any word splits. |
| Split using a corpus-frequency vocabulary | Caught 4 real corruptions but produced **18 false positives across 9 transcripts**: `someone`, `wholesome`, `homemade`, `whenever`, `dinnertime`, `whatever`, `lifetime`, `anywhere`, `outcomes`… |

**The finding is structural, not a tuning problem:** `havesome` and `wholesome` are *indistinguishable*
by dictionary lookup — both absent from a small vocabulary, both splitting into two real words. **Any
splitter that repairs one corrupts the other.** English compounding makes automatic correction unsafe
at any threshold.

**So the rule is:**

| Pattern | Action | Why |
|---|---|---|
| Space dropped after clause/sentence punctuation (`um,he's`) | **FIX** | A letter immediately following punctuation is *always* wrong. Cannot false-positive. |
| Merged words (`havesome`) | **FLAG — never edit** | Set `needs_review` + `review_tokens` on the segment. A false-positive **flag** costs a human two seconds; a false-positive **edit** silently corrupts the canonical index the selector reads *and* the aligner aligns against. |

- **Store both** `raw_text` and `text`, so any repair is auditable and the index re-derivable.
- **Count and log** repairs and flags, so a change in the upstream pattern is visible, not silent.
- **Report upstream to ElevenLabs** if the SQL confirms it originates in their payload.
- Detector volume is tiny — **2 flags in Session `9ff33531`** (both false positives: `whenever`,
  `whatnot`), ~2.4 per interview across the corpus. That is a negligible review burden.

**Explicitly NOT in scope:** general spell correction, grammar repair, punctuation inference,
ML normalization, fuzzy reconstruction — **or automatic word-splitting**. An unrepaired merged token
costs **one** bad word boundary inside a single segment: contained, flagged, and visible via
`confidence`. That is the acceptable residual.

### 6c. What a "sentence" is (definition, revisable)

Derived from the nine real transcripts in `interview-analysis/four-30-Jun-2026/` and validated by
running it over them. A **sentence** is the addressable, clippable unit — the rule is pragmatic, not
linguistically pure.

**Split on:** terminal punctuation `.` `?` `!` when followed by whitespace + a capital letter, digit,
or opening quote — **or** end of turn.

**Do NOT split on:**
- **Commas, semicolons, colons.** Spoken sentences run long and comma-heavy ("Um, one is a German
  shepherd mix and the other is a pit bull lab mix.") — splitting there fragments a single thought.
- **Ellipsis `...`** — it marks a pause or an interruption, not a boundary. `?...` is one terminal.
- **After an abbreviation** — `p.m.`, `a.m.`, `Mr.`, `Dr.`, or a single initial. Real cases in the
  corpus: "around 4:00 or 5:00 p.m."
- **Mid-word self-corrections** — "a husky m- a husky", "t- try", "f-uh" stay inside their sentence.

**Other rules:**
- **Disfluency prefixes stay attached.** "Um," / "Uh," / "So," / "I mean," lead their sentence rather
  than forming their own.
- **Short answers remain their own segment.** "Yeah." / "Sure." / "Mm-hmm." are emitted, not merged —
  faithfulness beats convenience, and they are sometimes needed for context. `word_count` is stored so
  the consumer can filter; **6 of 99** participant segments in `9ff33531` are one word.
- **Zero-word turns produce no segments.** A turn that is only `...` (silence / no response) yields
  nothing clippable, but still consumes a `turn_index` so indices stay stable.
- **Both roles are segmented and stored.** Interviewer segments are needed for the
  "include the preceding question" flag; only participant segments are normally clip candidates.

**Measured on Session `9ff33531`:** 96 turns → **197 segments** (99 participant). Participant segment
length min 1 / **median 11** / max 48 words.

> **This definition is expected to change.** That is why segments carry `encoder_version` — a revised
> rule triggers a targeted re-encode rather than an all-or-nothing one.

**Worked example** — `interview-analysis/encoded-transcript-9ff33531.json`, generated by the prototype
encoder. Alignment fields are `null` there because the audio was not processed; they are deliberately
**not** interpolated from turn timestamps (that is the approach D1 rejects).

### 6d. FUTURE (not in scope) — LLM semantic segmentation

Noted 2026-07-25 for a later `encoder_version`. **Not built; v1 stays rules-based (§6c) and
flag-don't-fix (§6a).** Recorded here so the reasoning doesn't have to be re-derived.

> **⚖️ Note for whoever picks this up: the decision priorities favor it.** On **ease** it removes both
> the per-corpus rule tuning and the merged-word review queue; on **quality** it segments on quotable
> thought rather than the ASR's punctuation guess; on **cost** it is ~$0.04/interview. The only mark
> against it — non-determinism — is a correctness concern, not one of the three axes. It is deferred
> for **scope discipline**, not because it scored badly. **Re-evaluate it immediately after Phase 0**,
> once there is real segmented output to compare against, rather than parking it indefinitely.

**The idea.** An LLM decides segment boundaries *semantically* — where a **quotable thought** starts
and ends — instead of on ASR punctuation. For a sizzle reel the quotable thought is the unit you
actually want; a grammatical sentence is a proxy for it.

**⛔ The non-negotiable constraint: the LLM emits WORD INDICES, never timestamps.** It reads the
transcript with word timings already attached and returns boundaries as indices into the aligned word
list; timestamps come from the aligner by lookup.

> A forced aligner solves an explicit optimization over audio frames and returns a **score** — when it
> is unsure, `confidence` says so. An LLM emits a *token that looks like a timestamp*, with no
> frame-level alignment and no honest uncertainty signal. A hallucinated `start_ms` 800 ms off chops
> the first word of a clip with nothing flagging it — exactly the silent-failure class this design
> exists to eliminate. **Timing is a measurement problem, not a judgment problem.** Structuring the
> call so the LLM cannot emit a time makes that failure impossible rather than unlikely.

**What it would additionally buy:**
- **Resolves the §6a merged-word flags.** `havesome` vs `wholesome` is trivial with sentence context —
  the exact discrimination that defeated dictionary lookup. This **removes the manual review queue**
  (~2.4 items/interview) rather than letting it accumulate.
- **Removes the dependence on ElevenLabs' ASR punctuation** as a boundary source (see §6c — that
  punctuation is a model's guess, not ground truth).
- Can score **clip-worthiness**, giving the external selector a better starting set.

**Costs / caveats:**
- **Not reproducible.** Boundaries can shift between runs, and segment IDs are the contract with the
  selector (D2). Contained by the existing design — IDs are **stored, not recomputed on read**, so
  non-determinism only bites at re-encode, which `encoder_version` already makes a deliberate
  versioned event. A re-encode does invalidate the selector's prior output.
- **Claude accepts no audio input** (verified against the current model catalog 2026-07-25 — the
  published capability surface exposes image input, not audio). So the "hand an LLM the transcript
  *and* the audio, get back an encoded transcript" shape is **not available with Claude** and would
  introduce a different vendor into the critical path of the core data asset. This future step is a
  **text-only pass over already-aligned output** — which is why it composes cleanly.

### 6b. Low-confidence alignment policy

Storing `confidence` and never reading it would reproduce exactly the bad cut this design exists to
prevent. So:

- **Expose `confidence` on the segment index** the external selector reads, so it can avoid weak
  segments itself.
- **Enforce a floor at render time.** A clip whose segment is below the floor is **blocked from
  publish** and surfaced in the admin UI for a human to confirm or trim.
- Calibrate the floor during the Phase 0 spike (§14) against real output — do not guess it up front.

---

## 7. Decision D4 — A reel is a definition; rendering is a separate step

A reel is an **ordered list of clip references** (segment IDs + per-clip padding + options), not a
video file. Cheap to create, cheap to revise, cheap to review, and auditable as data.

Two modes, both useful:

- **Virtual preview (while editing).** The player seeks the mezzanine(s) across clip ranges. Zero
  encode cost, instant iteration.
- **Rendered output (on publish).** One MP4 in S3 — shareable, downloadable, plays anywhere.

Rendering happens only at publish, so editing never pays encode cost.

### 7a. ⚠️ Stated v1 limitation — agent-voice bleed at clip seams (needs operator sign-off)

The recording has **one mixed audio track** (participant + agent TTS). Three v1 features push clip
edges into agent speech: lead-in/lead-out padding, the "include the preceding interviewer question"
flag, and snap-to-silence falling back to fixed padding where no silence exists (§6).

**Consequence:** a v1 reel will sometimes carry a fragment of the interviewer's voice at a seam. It
cannot be removed by post-processing — the voices are summed into one channel and are not separable.

This is **not a bug to fix in v1**; it is a property of how interviews have been recorded to date, and
it is permanent for every interview already recorded. It resolves only for interviews captured *after*
the participant-only audio track lands (`runbook.md` Open Items `[2026-07-25]`).

> **Get explicit operator sign-off on this as an accepted v1 limitation before a reel reaches a
> client.** If seam-clean audio is required for the first client deliverable, the participant-only
> track becomes a prerequisite rather than a follow-up, and the first reels can only be built from
> interviews recorded after it ships.

---

## 8. Decision D5 — Execution substrate: Render one-off jobs

### Why not in the web service

Prod (`HumanLens-v3`) is **Starter: 0.5 CPU / 512 MB**, `WEB_CONCURRENCY=2`, each gunicorn worker
~200–300 MB — already near the ceiling (`HL_MAX_CONTENT_LENGTH_MB=8` exists as an anti-OOM guard).
A 15-minute 720p encode is minutes of *full-core* CPU. Running it in-process would stall request
handling and risk OOM-killing the container **while interviews are live**, with none of the
protection drain mode provides. Not viable at any scale.

### Options considered

| Option | Verdict |
|---|---|
| **ffmpeg in the web service** | **Rejected** — see above. |
| **AWS MediaConvert** (S3-native, multi-input clip + concat in one API call) | **Rejected for v1** as over-engineered for current volume: it requires a new AWS client, an IAM policy + service role, and an SNS/EventBridge → webhook completion path. **Swap path kept open** — keep transcode/render behind an interface and this becomes a drop-in when volume justifies it. |
| **Render background worker service** | Rejected — a persistent service billed at rest for bursty work. |
| **Render one-off jobs** | **CHOSEN.** |

### Render one-off job facts (verified against Render docs 2026-07-25)

Source: <https://render.com/docs/one-off-jobs>

| Property | Fact | Why it matters here |
|---|---|---|
| Build & env | *"A one-off job uses the same build artifact and configuration as one of your existing Render services"* — the base service's **most recent successful build artifact** plus **all** its environment variables. Snapshot is immutable for the life of the job. | ffmpeg must be in the **base service's build**. All credentials are inherited — nothing new to plumb, but see §10. |
| Command | `startCommand` is **required and per-job**, and may differ from the base service's own. | The job *is* the work unit. No dispatch queue needed. |
| Sizing | Defaults to the base service's instance type; overridable with **`planId`**. Web/private/worker base services support `plan-srv-006` … `plan-srv-014` — **512 MB / 0.5 CPU up to 32 GB / 16 CPU**. | Transcode is not crippled at Starter. Self-hosted alignment becomes feasible. |
| Runtime limit | *"If a one-off job hasn't exited after 30 days, Render automatically terminates it."* No other documented per-job timeout; no documented concurrency limit. | Not a practical constraint. |
| Billing | *"While a one-off job is running, it's billed at the per-second rate for its specified instance type."* | Scales to zero. A bigger instance for less time is roughly cost-neutral and far faster for CPU-bound work. |
| Status | `Retrieve job` / `List jobs` return `status`, `startedAt`, `finishedAt`. | Used as a **fallback** liveness check — see §13. |
| Logs | *"Logs generated by one-off jobs are also included in your workspace's log stream."* | Enables the §10 detection control. `FilteredGunicornLogger` does not apply; job output is plain stdout. |
| Disk | *"A one-off job cannot access its base service's persistent disk (if it has one)."* | Rules out anything touching the legacy `media-video-recordings/` mount. Everything must go through S3. |
| Blueprint | One-off jobs need **no `render.yaml` change** — created against an existing service. | The blueprint stays untouched; no Blueprint sync, no review diff. |

API reference:
- Create — <https://api-docs.render.com/reference/post-job>
- Retrieve — <https://api-docs.render.com/reference/retrieve-job>
- List — <https://api-docs.render.com/reference/list-job>
- Cancel — <https://api-docs.render.com/reference/cancel-job>

### ffmpeg delivery

Because the job reuses the base service's build artifact, ffmpeg must be **in that build**.

- **Chosen:** a pip-installed static binary (`imageio-ffmpeg` / `static-ffmpeg`). One requirements
  line, versioned and reproducible, and it avoids converting the prod service to a Docker runtime —
  a change not worth making for this.
- **Alternative considered:** download a static build at job start into `/tmp`. Zero change to the
  prod build, but adds a runtime dependency on an external host. Rejected for reliability.

> ⚠️ **This is the one production-touching piece of the whole design.** Adding to
> `humanlens/requirements.txt` changes the **prod web service's build**; a broken build is a failed
> deploy. Staging auto-deploys on push to `main`, so that rehearsal is free — take it.

### Version note

A job runs the base service's **last successful build**. Prod deploys are manual, so a job launched
against prod runs **prod's deployed commit, not `main`**. Consistent with the existing
"prod truth is `/admin/diagnostics`" discipline — but don't assume a job picks up code you only pushed.

### ⚠️ Sequencing constraint — the ffmpeg chicken-and-egg (P1, review 2026-07-25)

These two facts combine into a hard ordering requirement:

1. A one-off job runs the base service's **most recent successful build artifact**.
2. **Prod deploys are manual** (staging auto-deploys on push).

Therefore: **no prod media job can run until a prod deploy has shipped ffmpeg.** A job launched before
that deploy fails with a confusing "command not found"-class error that looks like a code bug rather
than a missing dependency.

**Phase 1 order is fixed:**

1. Merge the requirements change → **staging auto-deploys** → run a job on staging to confirm ffmpeg
   is present and working in the built artifact.
2. **Manually deploy prod.**
3. **Only then** launch any prod media job.

The same applies to every later change to job code: a prod job runs prod's build, so a job entrypoint
that only exists on `main` does not exist to prod jobs.

---

## 9. Decision D6 — Direct launch, not a cron dispatcher

Both designs write a `media_jobs` row first and dispatch second, so a failed launch loses nothing.
They differ only in **where the dispatcher lives** and therefore **where the Render API key lives**.

| | **Direct launch (CHOSEN)** | Cron dispatcher (considered) |
|---|---|---|
| Render API key lives on | The internet-facing web service | A `type: cron` service with no inbound network surface |
| Web-service compromise | Also yields workspace-wide Render control | No infra escalation beyond what the web service already had |
| Time to running | ~45 s container provisioning¹ | ~45 s + up to one tick (1–2 min) |
| Dispatch-failure feedback | **Immediate**, on click | Deferred — operator sees `queued` |
| Retry of a failed dispatch | Needs a sweeper | Free (row is still `queued` next tick) |
| `render.yaml` change | **None** | One `type: cron` service |

¹ Inferred from the measured deploy-swap provisioning cost (~46 s on Starter, `render.md`), **not**
from a timed job start. Worth measuring once.

**Why direct launch was chosen.** The cron dispatcher was initially recommended, on the argument that
a periodic process is needed anyway and could also close **Fix #4** (the janitor's missing scheduled
trigger). The operator ruled Fix #4 **out of scope** — correctly, it is unrelated work. With that
removed from the ledger, the cron costs a genuinely new service and buys only the key relocation.

**Why the periodic need doesn't force a cron here.** Retry and dead-job reconciliation ride an
**interval-gated hook on the admin blueprint**, mirroring the established janitor pattern. The reason
that pattern *fails* for participant background work (Fix #4: two real panelists unreaped for ~16 h
because no `/p/*` traffic arrived) does not apply, because media jobs are **operator-initiated and
therefore self-correlating**: the operator queues a render from the admin UI and then watches the
progress page, generating exactly the requests that drive retry and reconciliation. This is the same
reasoning that justified the OTP-cleanup precedent (2026-04-22).

A job that dies at 03:00 with nobody watching stays unreconciled until someone opens the page — which
is acceptable, because nobody is waiting for it.

> **Explicitly rejected: "use the janitor instead."** The janitor executes **inside the web service
> process**, so it is not a separate security boundary — using it puts the key on the web app anyway.
> Its hooks are also on `/p/*` only, so operator-initiated renders would wait for the next
> *participant* to arrive. It resolves to direct launch, but slower and less reliable.

**Migration path.** The contract is "web writes a row, something dispatches it," so relocating
dispatch to a cron later is a small change.
⚠️ Render documents key **revocation but not rotation** (<https://render.com/docs/api>), so relocating
the key means **revoke-and-replace**, not rotation.

---

## 10. Job security model

### The threat

`RENDER_API_KEY` in the web service env allows `POST …/jobs` with an **arbitrary `startCommand`**,
executing with the base service's **full production environment** — prod DB, prod S3, prod PII key,
and every vendor token.

**Render has no least-privilege API key.** Keys are created from Account Settings (user-scoped, not
service-scoped) and the API spans services, datastores, deploys, environment groups, blueprints,
metrics and logs (<https://render.com/docs/api>). There is no documented way to restrict a key to
"may create jobs on service X."

### Rejected mitigation — a launch token validated inside the job

Considered and **rejected**: an attacker holding the API key **never runs our code**. They set
`startCommand` to `python -c "…"`, `psql $DATABASE_URL`, or simply `env`. A token check inside our
entrypoint gates only the legitimate path — which was never the attack. **The control must sit on who
can call Render's API**, not on what our job accepts.

### Controls that are actually built

| Control | Purpose |
|---|---|
| **Fixed allow-list of job entrypoints.** No free-text command ever reaches the Render API; `startCommand` is constructed server-side from a closed set. | Blocks application-level misuse. |
| **The DB row is the instruction.** The command line carries only an opaque `media_jobs.public_id`; the job loads that row and does exactly what it says. | A tampered command line cannot retarget a job. |
| **Atomic claim** — `UPDATE media_jobs SET status='running' … WHERE status='queued' RETURNING id`. | A double-dispatch cannot double-execute. Same pattern as the integrated screening claim and the quota bouncer. |
| **Platform-admin only, CSRF-protected, every creation audited** (`MEDIA_JOB_CREATED`). | Standard, and the precondition for the detection control below. |
| **Detection: reconcile the `List jobs` API against `media_jobs`.** Any Render job for the service with **no matching `media_jobs.render_job_id` was launched outside the application.** | Turns key theft from invisible into detectable. Build this on day one. |
| **Concurrency cap, enforced app-side.** Render documents **no** job concurrency limit, and billing is per-second — an unbounded dispatch loop could launch many paid instances at once. Cap concurrent in-flight `media_jobs` and refuse to exceed it. | Cost-safety. **Right-sized note (2026-07-25):** the backfill is **< 20 interviews** and every encode is operator-initiated (D9), so this is a cheap guardrail against a dispatch bug, not a live scaling concern. Keep it simple — a small constant is enough. |

> **Detection control — changed 2026-07-25 (review).** An earlier draft specified reconciling *Render's
> log stream* against `audit_logs`. That is likely not buildable without configuring a log drain to an
> external sink, which would have made a "build this day one" control aspirational. The `List jobs`
> endpoint is already cited in §8, returns exactly what's needed, and requires no new infrastructure —
> same control, actually implementable.

### Residual risk (accepted)

Web-service compromise now also yields workspace-wide Render control. This is a genuine escalation,
but **second-order**: an attacker with web RCE already holds the prod DB, S3, and the PII encryption
key, so the data is lost in that scenario regardless. What the key adds is infrastructure control and
persistence.

### Least privilege, later

Jobs inherit **all** of the base service's env vars — so a transcode job also holds Cint, Prolific and
PII credentials it has no use for. Inheritance is all-or-nothing; the only way to shrink it is to
attach jobs to a **different base service** with a reduced environment. This is the "later moved to a
different Render service" step the operator already anticipated, and this is its concrete motivation.

---

## 11. Access control — ⛔ SPECIFICATION ONLY, NOT BUILT IN THIS SCOPE

> **Nothing in §11 or §11a is implemented by the in-scope work** (operator decision, 2026-07-25).
> No reel share model, no intersection predicate, no agency composition, no cross-org warning, no
> revoke cascade — not even partially. This section exists so the later phase has a settled design and
> so the encode is built with the eventual consumer in view. **The one access question that IS in
> scope is §15.9 — what the external selector receives.**


### D8 — Authorization is an intersection

**Operator requirement:** sizzle reels are available only to orgs that have access to the full video.

A reel is viewable by org *O* **only if every source interview** has an active `video_share` to *O*
that passes the same gates as direct viewing (`status='active'`, `agency_only`, and the
`client_share_approved` cascade). Re-checked on **every** request, through a chokepoint analogous to
`active_share_for_viewer`.

Consequences:

- **Revocation cascades.** Revoking any single source makes the whole reel unavailable.
- **Rendered reels are served only via grant-checked presigned URLs** — never a public object.
- **A downloaded copy cannot be recalled.** Same limitation already accepted for the
  `organizations.is_forven_internal` download bridge.

### 11a. ⛔ Agency-viewer composition (P1 — cross-tenant; specify before Phase 3)

The intersection above was written for **direct client viewers only**. `video_sharing.py` has **two**
visibility paths, and the agency roll-up (shipped 2026-07-13, addendum 42) is currently unspecified
for reels. Since reels can span multiple interviews and multiple client orgs (D7), this is the
highest-stakes item in the design — it is cross-tenant exposure.

**Rules to implement:**

| Viewer | Sees the reel only if |
|---|---|
| **Client org viewer** | For **every** source clip: an `active` share to their org, **and** `agency_only = false` on that share. |
| **Agency viewer** | For **every** source clip: an `active` share to some org for which their agency holds an **ACTIVE `service_relationship`**. (Agency viewers are not blocked by `agency_only` — that flag withholds from the *client*, not the agency.) |

**`agency_only` composes to the STRICTER value.** If **any** clip in a reel is `agency_only = true`,
the whole reel is treated as `agency_only` — withheld from client viewers, visible to the agency.
Composing to the looser value would leak a deliberately-withheld clip into a client's view by bundling
it with a released one.

**A reel may never be shared *into* an agency org** — same Req-2 rule as `video_shares`.

**Multi-client reels and agencies:** a reel whose clips are shared to clients of *different* agencies
is visible to **no** agency viewer (no single agency holds a relationship covering every source). That
is the correct conservative outcome; surface it in the share UI so it doesn't read as a bug.

**Build with tests marked `@critical`**, alongside the existing agency isolation cases in
`tests/test_routes/test_agency_model.py`.

### D7 — Cross-study / cross-org mixing

**Operator decision 2026-07-25: allowed, at platform-admin discretion.**

The risk is real and was raised: a reel mixing two clients' studies, shared to one of them, exposes
the other client's respondents. The decision stands; the mitigation is to make the risk **visible
rather than invisible**:

> At share time the UI **names every distinct commissioning org** whose respondents appear in the
> reel, and **warns** when that set is larger than the destination org.

Full flexibility, no silent cross-client exposure.

### PII posture

Reels inherit the `/shared` posture: participants are visible on video (already true of shared
recordings), but reel titles, descriptions and any Forven-authored metadata are viewer-visible and
must stay **PII-free** — same rule as video-metadata labels (`video_metadata.md`).

---

## 12. Schema

> **Numbering: take the next available addendum / migration at build time.** This doc deliberately does
> **not** reserve a number, per the numbering rule in the runbook's Schema Change Policy. Confirm the
> current head against the Schema Change Log when you generate the migration — `down_revision` must point
> at the **actual** head, and reserved numbers have slipped three times in this repo already.

All additive (new tables + nullable columns) → **DB-ahead-of-code safe**.

| Object | Purpose |
|---|---|
| `interview_transcript_segments` | The addressable aligned unit — **the deliverable of the in-scope encode.** `interview_session_id`, `public_id`, `turn_index`, `sentence_index`, `role`, `text` (repaired), **`raw_text`** (as-delivered, so repairs are auditable and the index re-derivable — §6a), `word_count` (lets the consumer filter one-word answers without re-tokenizing), `start_ms`, `end_ms`, `confidence` (§6b), **`needs_review`** + `review_tokens` (merged-word flags — §6a; flagged, never auto-edited), **`encoder_version`** (enables a *targeted* re-encode when the aligner, repair rule, or **sentence definition** (§6c) changes, instead of all-or-nothing), `created_at`. Unique on `(interview_session_id, turn_index, sentence_index)`; indexed on `interview_session_id`. |
| `media_renditions` | Derived assets per session — kind (`mezzanine`, `align_audio`, `thumbnail`), storage key, probe results (duration, w/h, fps, rotation), status, error. Keeps the original WebM untouched. |
| `sizzle_reels` | Reel definition — `public_id`, title, owner scope, status (`draft`/`published`), output storage key, timestamps, `created_by_user_id`. |
| `sizzle_reel_clips` | Ordered clips — `sizzle_reel_id`, `position`, `interview_transcript_segment_id` (or explicit ms range), lead-in/lead-out padding, `include_preceding_question` flag. |
| `media_jobs` | Durable job intent + status — `public_id`, `job_type`, target ref, `status`, `plan_id`, `render_job_id`, `progress_pct`, `last_heartbeat_at`, `attempts`, `queued_at`/`started_at`/`finished_at`, `error`. |

Reel-share authorization reuses the `video_shares` **pattern** but needs its own predicate (§11), so
it gets its own chokepoint rather than overloading `active_share_for_viewer`.

**New env vars** (add to `.env.example` **and** `render.yaml` both blocks, same change):
`RENDER_API_KEY` (`sync: false`), `RENDER_SERVICE_ID`, plus `planId` defaults per job type.

---

## 13. Admin UI

The admin UI is the **only** launch path. Platform-admin only, CSRF-protected, audited, no free-text
command anywhere.

- **`/admin/media-jobs`** — list: job type, target, status, plan, queued/started/finished, duration,
  retry, cancel.
- **Contextual launch** — "Derive clips data" on the video's admin page; "Render reel" on the reel
  page. Both simply write a `media_jobs` row and dispatch.
- **Progress** — the job writes `status` + `progress_pct` + `last_heartbeat_at`. A stale heartbeat
  surfaces as **"needs attention"**, the same shape as the recording-recovery queue in
  `system_metrics_to_monitor.md`.
- **Cancel** → Render's `Cancel running job` endpoint.
- **Reconciliation** — the DB row is authoritative for *work*; Render's `Retrieve job` status is the
  fallback that catches jobs which died before writing anything (OOM, crash).

---

## 14. Implementation plan

| Phase | What | In scope? | Gate to exit |
|---|---|---|---|
| **0** | Manual dry run of the encode on one recording | ✅ | Clips a human watched are correct |
| **0.5** | Decision point — rules vs LLM segmentation | ✅ | §6d settled on evidence |
| **1** | Build the encode | ✅ | Every interview has a segment index + mezzanine |
| **2** | Reel definition + render | ⛔ deferred | — |
| **3** | Reel delivery + access control | ⛔ deferred | — |

---

### ⛔ PHASE 0 — Alignment spike. Gates everything else.

**One task: manually run the encode pipeline end-to-end on a single recording, then watch the clips it
produces.** Nothing is built and nothing ships — this is a dry run to prove the approach before any
infrastructure is built on top of it.

The three P0 findings (§3 D1a, §6 step 3, §6a) are **unfalsifiable claims until this runs**, and each
fails *silently* — wrong output, not an error.

**Needs none of the built infrastructure** — no schema, no Render jobs, no API key, no code shipped.
Direct `aws s3api` with an explicit `--bucket`/`--region` is safe from anywhere (`CLAUDE.md`), so
pulling the file needs no app run.

| # | Step | Notes |
|---|---|---|
| **0.1** | **SQL check** — does `transcript_json` contain `havesome`? | 30 seconds; independent of everything else, runnable today |
| **0.2** | Pull one staging recording from S3 | `aws s3api get-object`, explicit bucket + region |
| **0.3** | `ffmpeg` → transcode to a **mezzanine** MP4 | Fixed short GOP, one canvas, corrected orientation (§5) |
| **0.4** | `ffmpeg` → extract mono 16 kHz WAV **from the mezzanine** | ⛔ **Not** from the source WebM (§6 step 3) |
| **0.5** | Send WAV + transcript to the aligner → word timings | Participant turns only, two-stage (§3 D1a) |
| **0.6** | Cut 3–5 clips at sentence boundaries from those timings | Plain `ffmpeg -ss`/`-t` |
| **0.7** | **Watch them** | The only real proof |

**What those steps establish:**

| From step | Finding | If skipped |
|---|---|---|
| 0.1 | Is the corruption in ElevenLabs' payload or our handling? | Wrong fix applied at the wrong layer |
| 0.5 | **Does the aligner accept a 15-minute file at all?** | ⚠️ **Highest value — a rejection invalidates the settled aligner choice (§3) and forces chunking** |
| 0.5 | Do participant turns align to sane word boundaries? Does clock-offset recovery land? | Silently skewed clips |
| 0.6–0.7 | Is every clip shifted by a constant? | The WAV-from-mezzanine bug, invisible until someone watches closely |
| 0.6–0.7 | Do cuts land cleanly at participant→agent boundaries? | Agent's first words swallowed |
| 0.6–0.7 | What `confidence` value separates a good clip from a bad one? | Floor guessed instead of calibrated (§6b) |

**Deliverable:** a handful of actual clips, plus answers to all six. **Output is a revised doc, not
code.**

**Exit gate:** revise this doc against the results before Phase 1 starts.

---

### PHASE 0.5 — Decision point: rules vs LLM segmentation

With real aligned output in hand, run an LLM pass over the same recording and compare against the
rules-based segmenter (§6c). Then commit for Phase 1.

- The decision priorities favor the LLM (§6d) — easier, better unit, negligible cost.
- **There is no deadline pressure.** At **< 20 interviews** re-encoding is cheap, so this can also be
  revisited after Phase 1 without meaningful cost.
- Whatever is chosen becomes `encoder_version` v1.

---

### PHASE 1 — Build the encode. This is the whole of the in-scope work.

**Done when** every interview has a durable, time-addressable segment index and a mezzanine, produced
by a repeatable job an operator can run and re-run.

| # | Step | Depends on | Produces |
|---|---|---|---|
| **1.1** | **ffmpeg into the build** — add the pip static binary; merge → **staging auto-deploys** → run a job on staging to confirm ffmpeg is in the artifact → **manual prod deploy** | — | ffmpeg available to prod jobs |
| **1.2** | **Schema** — `interview_transcript_segments`, `media_renditions`, `media_jobs`; addendum + migration number taken at build time (§12); operator applies staging-first | — | Tables on staging + prod |
| **1.3** | **Job infrastructure** — Render API client, launch service with the fixed entrypoint allow-list, atomic claim, concurrency cap, audit, `List jobs` reconciliation (§10) | 1.2 | A job can be launched and tracked |
| **1.4** | **Encode pipeline** — probe → mezzanine → WAV from mezzanine → repair → two-stage participant alignment → segmentation → VAD → divergence check (§6) | 1.1, 1.2 | Mezzanine + segment index per interview |
| **1.5** | **Admin UI** — `/admin/media-jobs` list + contextual **Encode** / **Re-encode** triggers + progress + cancel (§13) | 1.3 | Operator can run and watch encodes |
| **1.6** | **Encode the existing interviews** — **< 20 total**; not a special mode, just select them and press encode (D9) | 1.1–1.5 | Index exists for the back catalogue |
| **1.7** | **Expose the segment index** to the external selector, per §15.9 | 1.4 | Selector can consume it |

**Ordering constraints that actually bind:**

- **1.1 is the long pole** — it needs a *manual prod deploy* before any prod job can run. Start it
  first; a job launched before that deploy fails with a confusing "command not found"-class error
  (§8 sequencing constraint).
- **1.2 precedes 1.3 and 1.4** — both write to those tables.
- **1.3 and 1.4 are independent of each other** once the schema exists — parallelizable.
- Everything else follows the dependency column.

**Exit gate — staging smoke is mandatory** (unit tests structurally cannot cover S3 behavior,
`CLAUDE.md`): confirm a job launches, writes its own status, and produces a mezzanine plus a segment
index; then **cut one clip by hand from the stored timings and watch it.** That is the only end-to-end
proof the index is correct.

---

### PHASE 2 — Reel definition + render ⛔ DEFERRED (out of scope)

Retained only so the encode is built against a known consumer.
`sizzle_reels` / `sizzle_reel_clips` · reel admin UI · virtual preview (seek the mezzanine, zero encode
cost) · render job · cut-quality rules applied (§6) · confidence floor enforced at publish (§6b).

### PHASE 3 — Reel delivery + access control ⛔ DEFERRED (out of scope)

**Nothing here is built in the current work, not even partially** (§11).
Reel share model · the intersection predicate (D8) · agency-viewer composition with `@critical` tests
(§11a) · cross-org share-time warning (D7) · revoke cascade.

### Fast-follow (independent of the phases above)

- **Re-evaluate §6d** if it wasn't adopted at Phase 0.5.
- **Participant-only audio track** (`runbook.md` Open Items `[2026-07-25]`) — **not retroactive**; every
  interview recorded before it ships is permanently mixed. Lands behind the Slice-2 bitrate cap.
- **Delete the dead `audioRecorder`** — five lines; it encodes Opus every second of every interview and
  discards it, costing participants CPU and battery today. No design work, no dependencies.

---

## 15. Open questions

**Answered by the Phase 0 spike (§14) — do not start Phase 1 until these are closed:**

1. **Aligner limits** — file-size / duration caps against a real 15-minute recording. Unverified.
2. **Transcript corruption origin** — is it in `transcript_json`? One SQL query (§6a). Determines
   whether we repair, fix our own handling, or report upstream to ElevenLabs.
3. **Clock-offset recovery** — does the two-stage anchor approach (§3 D1a) land reliably?
4. **`confidence` floor** — calibrate against real cuts (§6b), don't guess.

**In scope, answerable during the encode build:**

5. ~~**`planId` per job type**~~ — **settled by the decision priorities: pick a generous plan and move
   on.** Per-second billing makes an oversized instance roughly cost-neutral for CPU-bound work, so
   sweeping it spends ease-of-use effort on the lowest-priority axis. Revisit only if cost becomes a
   driver.
6. **Concurrency cap value** — what in-flight limit does the backfill actually need (§10)?
7. **Job start latency** — measure once; the ~45 s figure is inferred from deploy provisioning.
8. **Segment-index API shape** for the external selector (pull endpoint vs export).
9. **⚠️ What the external selector receives, and how.** The index carries **participant speech**, which
   the design already notes may contain spoken PII, and the selector runs **outside this repo**. Decide
   the exposure model (authenticated pull vs export), whether it gets text-only or text+timings, and
   what is redacted — **this is the one access-control question that is IN SCOPE**, since §11/§11a are
   deferred with reel delivery.
10. ~~**Sentence-segmentation rule**~~ — **DEFINED 2026-07-25, see §6c.** Derived from the nine real
    transcripts and validated by running it over them (96 turns → 197 segments on `9ff33531`).
    Expected to change; `encoder_version` makes that a targeted re-encode.

**Deferred with reel building (out of scope now, listed so the encode anticipates them):**

11. **Reel output spec** — canvas size, bitrate, burned-in captions or lower-thirds.
12. **Retention** — rendered reels kept indefinitely, or regenerated on demand from the definition?
13. **Agent-voice bleed at clip seams** (§7a) — accept as a stated v1 limitation, or make the
    participant-only audio track a prerequisite for the first client deliverable? *(Operator decision,
    not a measurement. Needed before a reel reaches a client, not before the encode ships.)*

---

## 16. Out of scope / related work

- **Fix #4 (janitor scheduled trigger) is explicitly OUT OF SCOPE.** It was considered as a
  co-beneficiary of a cron dispatcher and deliberately excluded by the operator (2026-07-25) to keep
  this scope contained. It remains open in `fixes_todo.md` on its own merits.
- **Participant-only audio track** — tracked in `runbook.md` Open Items `[2026-07-25]`. Today the
  agent's TTS is irreversibly mixed into the single recorded track, so this fix is **not
  retroactive**. Clipping participant soundbites works without it (turn-taking), but agent-free audio
  is impossible. Should land **behind** the Slice-2 bitrate cap, since upload-bandwidth starvation is
  the confirmed cause of the truncation incident (`video_runtime.md`).
- **Recording cut-off hardening Slice 2** — the bitrate cap and the server "partial video" divergence
  flag overlap with §6 step 8 here; coordinate so the divergence signal is implemented once.
- **LLM semantic segmentation** — noted as a future `encoder_version` in **§6d**, deliberately out of
  scope for v1. Would replace punctuation-based boundaries with quotable-thought boundaries and
  auto-resolve the merged-word flags. Hard constraint recorded there: the LLM emits **word indices,
  never timestamps** — timing stays measured by the aligner.

---

## 17. References

**Render (verified 2026-07-25):**
- One-off jobs — <https://render.com/docs/one-off-jobs>
- Create job — <https://api-docs.render.com/reference/post-job>
- Retrieve job — <https://api-docs.render.com/reference/retrieve-job>
- List jobs — <https://api-docs.render.com/reference/list-job>
- Cancel running job — <https://api-docs.render.com/reference/cancel-job>
- API keys / authentication — <https://render.com/docs/api>

**Internal:**
- `docs/runbooks/video_runtime.md` — authoritative for recording behavior, the truncation incident,
  and the hardening phases.
- `docs/runbooks/participant_session_metrics.md` — metric definitions and the system-of-record rule.
- `docs/runbooks/video_sharing_cross_account_design.md` — the `video_shares` grant model this extends.
- `docs/runbooks/video_metadata.md` — the viewer-visible / PII-free metadata rule.
- `docs/runbooks/render.md` — Render operational reference, deploy timings, Blueprint workflow.
- `docs/runbooks/fixes_todo.md` — Fix #4 (out of scope here).
- `CLAUDE.md` — system-of-record principle, schema change protocol, staging-smoke requirement.

**Code touchpoints:**
- `humanlens/frontend/voici/src/App.jsx` — `startRecording`, the audio mix, `MediaRecorder`.
- `humanlens/backend/services/voici_runtime/transcripts.py` — transcript rendering / `time_in_call_secs`.
- `humanlens/backend/services/s3_storage.py` — S3 primitives.
- `humanlens/backend/services/video_sharing.py` — the grant chokepoint.
