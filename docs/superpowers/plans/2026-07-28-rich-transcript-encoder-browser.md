# Rich Transcript Encoder — Browser Half Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the cloud app produce rich transcripts automatically, with the ASR running on the client so neither video nor audio costs server bandwidth or CPU.

**Architecture:** Two phases. **2a** adds browser audio extraction (mediabunny) and wires it into the cloud upload flow, using the already-built `POST /encode` fallback. **2b** swaps the ASR into the browser (transformers.js whisper-tiny in a Worker) and switches to `POST /encode/words`. Both phases hit the same `encoder.core.encode`, so the algorithm never forks.

**Tech Stack:** mediabunny (vendored), transformers.js (2b), existing `static/app.js` upload flow, `encoder/service.py`.

**Spec:** `docs/superpowers/specs/2026-07-28-rich-transcript-encoder-design.md`
**Precedes:** plan `2026-07-28-rich-transcript-encoder.md` (core, CLI, service — complete)

**Status: 2a and 2b COMPLETE** (2026-07-28, commits `d191948`, `ef9696b`, `b949e7e`). Verified
in-browser end to end: `path=browser-webgpu`, real word timings, correct rich output. Two defects
were found only by running it — the `.web.` build's unresolvable bare specifier, and the
onnx-community export's missing cross-attentions (see CLAUDE.md). Task 8 (right-sizing the encoder
service now that it only handles fallbacks) is **not** done.

---

## Why 2a before 2b

The spec's primary path is browser-side ASR, and that remains the target. But the two halves of that work are independent:

- **Audio extraction + upload wiring** is needed by *both* paths and is what actually delivers the capability to clients.
- **Where the ASR runs** is a swap of one stage behind an HTTP boundary.

Shipping 2a first means the cloud app gains working rich transcripts using the fallback endpoint that already exists and is already tested, and 2b becomes an optimisation that removes server CPU rather than a prerequisite. If 2b hits trouble (WebGPU variance, model hosting, Worker bundling), 2a is still shipped and working.

**End state is identical to the spec.** This is sequencing, not a scope reduction.

---

## Phase 2a — audio extraction and cloud wiring

### Task 1: `extractAudio` in `static/transcript-encoder.js`

Decode any selected video to mono 16 kHz and return both a `Float32Array` (what 2b's ASR wants) and a WAV `Blob` (what `POST /encode` wants). ~0.7 MB per 4-minute interview as Opus, ~8 MB as WAV; WAV is fine over the wire and avoids an encoder dependency.

**Files:**
- Create: `static/transcript-encoder.js`
- Test: `tests/js/transcript-encoder.test.js` (follow the existing `tests/js/` convention)

- [ ] **Step 1: Write the module**

```js
/**
 * Browser-side transcript encoder.
 * Exposes window.TranscriptEncoder = { isSupported(), extractAudio(file), encode(file, transcriptText, opts) }.
 *
 * Audio never leaves the client on the 2b path, and only ~8 MB of mono 16 kHz
 * WAV leaves it on the 2a path. The VIDEO never reaches our servers at all --
 * it goes browser -> R2 by presigned PUT, as it already did.
 */
import { Input, BlobSource, ALL_FORMATS, AudioBufferSink } from '/static/vendor/mediabunny.mjs';

export const TARGET_SAMPLE_RATE = 16000;

export function isSupported() {
  return typeof AudioBuffer !== 'undefined' && typeof WebAssembly !== 'undefined';
}

/** Decode `file` to mono 16 kHz PCM. Returns a Float32Array. */
export async function extractAudio(file, onProgress = () => {}) {
  const input = new Input({ formats: ALL_FORMATS, source: new BlobSource(file) });
  const track = await input.getPrimaryAudioTrack();
  if (!track) throw new Error('No audio track in this file');

  const duration = await input.computeDuration();
  const chunks = [];
  let total = 0;

  const sink = new AudioBufferSink(track);
  for await (const { buffer, timestamp } of sink.buffers()) {
    const mono = _downmixResample(buffer, TARGET_SAMPLE_RATE);
    chunks.push(mono);
    total += mono.length;
    if (duration) onProgress(Math.min(1, timestamp / duration));
  }

  const pcm = new Float32Array(total);
  let offset = 0;
  for (const chunk of chunks) { pcm.set(chunk, offset); offset += chunk.length; }
  return pcm;
}

/** Average channels to mono and linearly resample to `rate`. */
export function _downmixResample(buffer, rate) {
  const channels = buffer.numberOfChannels;
  const ratio = buffer.sampleRate / rate;
  const outLength = Math.floor(buffer.length / ratio);
  const out = new Float32Array(outLength);
  const data = [];
  for (let ch = 0; ch < channels; ch++) data.push(buffer.getChannelData(ch));

  for (let i = 0; i < outLength; i++) {
    const src = Math.floor(i * ratio);
    let sum = 0;
    for (let ch = 0; ch < channels; ch++) sum += data[ch][src];
    out[i] = sum / channels;
  }
  return out;
}

/** Wrap mono PCM as a 16-bit WAV blob for POST /encode. */
export function pcmToWav(pcm, rate = TARGET_SAMPLE_RATE) {
  const buffer = new ArrayBuffer(44 + pcm.length * 2);
  const view = new DataView(buffer);
  const str = (offset, s) => { for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i)); };

  str(0, 'RIFF');
  view.setUint32(4, 36 + pcm.length * 2, true);
  str(8, 'WAVEfmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);          // PCM
  view.setUint16(22, 1, true);          // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);   // byte rate
  view.setUint16(32, 2, true);          // block align
  view.setUint16(34, 16, true);         // bits per sample
  str(36, 'data');
  view.setUint32(40, pcm.length * 2, true);

  for (let i = 0; i < pcm.length; i++) {
    const s = Math.max(-1, Math.min(1, pcm[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buffer], { type: 'audio/wav' });
}
```

- [ ] **Step 2: Test `_downmixResample` and `pcmToWav`**

These are the two pure functions and the only places arithmetic can go wrong.

```js
import { _downmixResample, pcmToWav, TARGET_SAMPLE_RATE } from '../../static/transcript-encoder.js';

function fakeBuffer(channelData, sampleRate) {
  return {
    numberOfChannels: channelData.length,
    length: channelData[0].length,
    sampleRate,
    getChannelData: (i) => channelData[i],
  };
}

test('downmix averages stereo to mono', () => {
  const buf = fakeBuffer([new Float32Array([1, 1]), new Float32Array([-1, -1])], 16000);
  expect(Array.from(_downmixResample(buf, 16000))).toEqual([0, 0]);
});

test('resample halves length when input rate is double', () => {
  const buf = fakeBuffer([new Float32Array([0, 1, 0, 1])], 32000);
  expect(_downmixResample(buf, 16000).length).toBe(2);
});

test('wav header declares mono 16k 16-bit', () => {
  const blob = pcmToWav(new Float32Array([0, 0.5]), TARGET_SAMPLE_RATE);
  expect(blob.size).toBe(44 + 4);
  expect(blob.type).toBe('audio/wav');
});
```

- [ ] **Step 3: Run and commit**

Run the JS suite per the existing `tests/js/` runner.

```bash
git add static/transcript-encoder.js tests/js/transcript-encoder.test.js
git commit -m "feat(encoder): browser audio extraction to mono 16kHz"
```

---

### Task 2: `encodeViaServer` — POST to the encoder service

- [ ] **Step 1: Add to `static/transcript-encoder.js`**

```js
/**
 * Encode one interview. Sends mono 16 kHz WAV plus the plain transcript to the
 * encoder service and returns { rich, stats }.
 *
 * Never send video here -- the whole point of the split is that the encoder
 * service sees audio only.
 */
export async function encodeViaServer(pcm, transcriptText, encoderUrl, signal) {
  const form = new FormData();
  form.append('transcript', transcriptText);
  form.append('audio', pcmToWav(pcm), 'align.wav');

  const resp = await fetch(`${encoderUrl}/encode`, { method: 'POST', body: form, signal });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => '');
    throw new Error(`Encoder failed (${resp.status}): ${detail.slice(0, 200)}`);
  }
  return resp.json();
}

/** True when a transcript already carries end timestamps (mirrors encoder.cli.is_rich). */
export function isRich(text) {
  return /^\[\d+:\d{2}-\d+:\d{2}\]/m.test(text);
}

window.TranscriptEncoder = { isSupported, extractAudio, encodeViaServer, isRich, pcmToWav };
```

- [ ] **Step 2: Test `isRich` against both tiers, then commit**

```js
test('isRich distinguishes tiers', () => {
  expect(isRich('[0:13-0:15] Participant: Hi.')).toBe(true);
  expect(isRich('[0:13] Participant: Hi.')).toBe(false);
});
```

```bash
git commit -am "feat(encoder): browser client for POST /encode"
```

---

### Task 3: `ENCODER_URL` wiring

Mirror how `GENERATOR_URL` is injected, so the frontend knows where the encoder lives.

**Files:** `app.py` (index route), `templates/index.html`, `.env.example`, `render.yaml`

- [ ] **Step 1:** Add `ENCODER_URL` to the `render_template` call in `app.py`'s `index()`, alongside `generator_url`.
- [ ] **Step 2:** Add the matching `<meta>` / inline constant in `templates/index.html`, following exactly what `GENERATOR_URL` does there.
- [ ] **Step 3:** Add `ENCODER_URL=` to `.env.example` and a `sync: false` entry to the `sizzle-app` block in `render.yaml`.
- [ ] **Step 4:** Add a third service block to `render.yaml`:

```yaml
  - type: web
    name: sizzle-encoder
    runtime: docker
    dockerfilePath: ./encoder/Dockerfile
    plan: starter          # needs ~1 GB for the Whisper model; free tier will OOM
    envVars:
      - key: ENCODER_MODEL_SIZE
        value: base
      - key: ALLOWED_ORIGINS
        sync: false
```

- [ ] **Step 5:** Commit.

---

### Task 4: Hook into the cloud upload flow

**Files:** `static/app.js` (the upload handler around L2660–2740)

Current flow: `/upload/prepare` → PUT each file to R2 → `/upload/commit` → `openFolder`.

New behaviour, inserted between the PUTs and `/upload/commit`:

1. Pair each video with a same-stem `.txt` from the selection.
2. Read the `.txt` (`await file.text()`); skip pairs where `isRich()` is already true.
3. For each remaining pair: `extractAudio` → `encodeViaServer` → obtain `{rich, stats}`.
4. Request presigned PUTs for `<stem>.forven.txt` (the original) and re-PUT `<stem>.txt` (now rich).
5. Report per-file stats in the existing `#transcribe-log` line.

- [ ] **Step 1:** Add the pairing helper and the encode loop, driven by the existing progress UI.
- [ ] **Step 2:** Make failure non-fatal — if encoding throws, log it and leave the plain `.txt` in place. A plain transcript still works; a failed upload does not.
- [ ] **Step 3:** Surface `stats.match_rate` below ~85% as a visible warning, since that usually means a truncated recording rather than a bad encode.
- [ ] **Step 4:** Commit.

---

### Task 5: Verify in the browser

- [ ] Start the app and the encoder service; upload one real interview plus its plain `.txt`.
- [ ] Confirm via `read_network_requests` that the `POST /encode` body is single-digit MB and **no video** goes anywhere but R2.
- [ ] Confirm the workspace shows sentence-level lines and that `shared.transcript_tier` sees rich.
- [ ] Generate a reel and watch it.

---

## Phase 2b — move the ASR into the browser

Only start once 2a is shipped and verified.

### Task 6: Worker running whisper-tiny

- [ ] **Step 1:** Add `@huggingface/transformers` to `package.json` and vendor its ESM build to `static/vendor/` alongside `mediabunny.mjs`.
- [ ] **Step 2:** Create `static/transcript-asr-worker.js` — receives a `Float32Array`, runs `automatic-speech-recognition` with `return_timestamps: 'word'`, posts back `[{w, s, e}]`.
- [ ] **Step 3:** Model weights: leave transformers.js pointing at its default remote host initially (**the app sets no CSP**, verified 2026-07-28, so this works with no build change). Move to R2 behind the existing presigned-GET pattern if client-side latency or vendor dependence becomes a concern.
- [ ] **Step 4:** Use `whisper-tiny.en`. The spike showed `tiny` anchors as many sentences as `base` (47/48), so the larger model buys nothing here.

### Task 7: Capability detection and path selection

- [ ] **Step 1:** `isSupported()` gains a WebGPU probe; keep WASM as the fallback *within* the browser path.
- [ ] **Step 2:** `encode()` tries the Worker; on failure or unsupported, falls back to `encodeViaServer`. The user sees one operation.
- [ ] **Step 3:** Switch the browser path to `POST /encode/words` — ~30 KB instead of ~8 MB.
- [ ] **Step 4:** Verify both paths in the browser, then commit.

### Task 8: Right-size the encoder service

- [ ] Once the browser path is primary, the service handles only fallbacks. Re-evaluate whether `plan: starter` is still needed, and consider splitting the thin `/encode/words` endpoint onto free-tier hosting with the ASR fallback elsewhere.

---

## Open question for Task 4

Whether the original plain transcript should be preserved as `<stem>.forven.txt` in R2 (mirroring the CLI's `--in-place`) or simply replaced. Preserving costs one extra small PUT and keeps the client's delivered artifact intact, which is why the CLI does it. **Default: preserve.**
