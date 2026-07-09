# Browser-Side Reel Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move cloud-mode clip encoding from the Render free-plan server (0.1 vCPU, serial, 10+ min) into the user's browser using WebCodecs hardware H.264 encoding, with a transparent server fallback for unsupported browsers.

**Architecture:** The server keeps only cheap work (transcript parsing, segment planning, bookkeeping). A new `POST /plan` endpoint returns the ordered segment list and presigned GET URLs; `static/reel-encoder.js` downloads each clip with range requests, decodes VP9/Opus via mediabunny, applies the fade, re-encodes H.264/AAC, and muxes one MP4. The finished reel is PUT directly to R2; `POST /library` records it. Local-mode Python pipeline and all existing server routes are untouched.

**Tech Stack:** Python/Flask (server), WebCodecs API, mediabunny (webm demux, CDN→vendor), mp4-muxer v5 (MP4 mux, CDN→vendor), OffscreenCanvas (title cards).

---

## File map

| File | Change |
|---|---|
| `generator_app.py` | Extract `_build_segment_list`; add `POST /plan` and `POST /library` |
| `tests/test_generator_app.py` | Tests for `_build_segment_list` |
| `tests/test_browser_endpoints.py` | New — tests for `POST /plan` and `POST /library` |
| `static/vendor/mediabunny.mjs` | New — vendored mediabunny ESM bundle |
| `static/vendor/mp4-muxer.mjs` | New — vendored mp4-muxer ESM bundle |
| `static/reel-encoder.js` | New — browser encode pipeline, exposes `window.ReelEncoder` |
| `static/app.js` | Wire `submitGenerate` with WebCodecs detection + browser path |
| `templates/index.html` | Add `<script type="module" src="/static/reel-encoder.js">` |
| `spike.html` | Throwaway spike (Task 0, deleted after Task 1) |
| `gen_presigned.py` | Throwaway spike helper (Task 0, deleted after Task 1) |

---

## Task 0: Spike — validate mediabunny range-reads on FORVEN webms

The spike is a throwaway standalone page that proves mediabunny can range-read the cue-less VP9/Opus webms from R2 and produce a playable MP4. **Do not start Task 1 until the spike passes.** Delete `spike.html` and `gen_presigned.py` after Task 1 is committed.

**Files:**
- Create: `spike.html`
- Create: `gen_presigned.py`

- [ ] **Step 1: Install spike dependencies locally (not in requirements.txt)**

```powershell
npm install mediabunny mp4-muxer
```

Check that both installed. The plan uses their ESM builds, which will be served from `node_modules/` during the spike only.

- [ ] **Step 2: Write the presigned URL helper**

Create `gen_presigned.py`:

```python
"""One-off helper: print a presigned R2 URL for the first FORVEN webm found.
Run with: .\venv\Scripts\python.exe gen_presigned.py
"""
import os
from pathlib import Path

# Load .env
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

os.environ["APP_MODE"] = "cloud"

import importlib
import storage
importlib.reload(storage)

keys = storage.list_keys("sessions/")
webm_keys = [k for k in keys if k.endswith(".webm")]
if not webm_keys:
    print("No webm files found in sessions/")
    raise SystemExit(1)

# Pick one — grab 2 different ones for the two-segment test
key1 = webm_keys[0]
key2 = webm_keys[1] if len(webm_keys) > 1 else webm_keys[0]
print("Video 1:", key1)
print("URL 1:")
print(storage.presigned_url(key1, expires=3600))
print()
print("Video 2:", key2)
print("URL 2:")
print(storage.presigned_url(key2, expires=3600))
```

Run it:
```powershell
.\venv\Scripts\python.exe gen_presigned.py
```

Copy both URLs. You'll paste them into `spike.html`.

- [ ] **Step 3: Write spike.html**

Create `spike.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Sizzle Reel — Browser Encode Spike</title>
  <style>
    body { font-family: monospace; padding: 24px; background: #111; color: #eee; }
    textarea { width: 100%; height: 60px; font-family: monospace; font-size: 12px; }
    button { margin: 8px 4px; padding: 8px 16px; cursor: pointer; }
    #log { white-space: pre-wrap; font-size: 12px; }
    video { max-width: 640px; display: block; margin: 16px 0; background: #000; }
  </style>
</head>
<body>
<h2>Browser Encode Spike</h2>
<p>Paste presigned R2 URLs (from gen_presigned.py) and click Run.</p>

<label>URL 1 (first ~30s clip, start=10, end=40):</label><br>
<textarea id="url1" placeholder="https://..."></textarea>

<label>URL 2 (second ~30s clip, start=60, end=90):</label><br>
<textarea id="url2" placeholder="https://..."></textarea>

<button id="btn-run">Run spike</button>
<button id="btn-play" disabled>Play result</button>
<video id="result" controls></video>
<pre id="log">Waiting…</pre>

<script type="module">
// NOTE: serves from node_modules during spike only. After spike, vendor into static/vendor/.
import { Demuxer } from './node_modules/mediabunny/dist/mediabunny.js';
import { Muxer, ArrayBufferTarget } from './node_modules/mp4-muxer/dist/mp4-muxer.js';

const log = (msg) => { document.getElementById('log').textContent += '\n' + msg; console.log(msg); };

const WIDTH = 1280, HEIGHT = 720, FPS = 30, SAMPLE_RATE = 48000;

// ── Title card: draw on OffscreenCanvas and encode frames ─────────────────────
async function encodeTitleCard(titleLines, durationSec, videoEnc, audioEnc, tsOffsetUsec) {
  const canvas = new OffscreenCanvas(WIDTH, HEIGHT);
  const ctx = canvas.getContext('2d');
  const totalFrames = Math.round(durationSec * FPS);
  const fadeInFrames = Math.round(2.0 * FPS);
  const fontSize = Math.max(24, Math.floor(HEIGHT / 15));
  const lineHeight = fontSize * 1.4;
  const startY = HEIGHT / 2 - (titleLines.length - 1) * lineHeight / 2;
  const silenceCount = Math.round(durationSec * SAMPLE_RATE);

  for (let i = 0; i < totalFrames; i++) {
    const alpha = Math.min(1.0, i / Math.max(1, fadeInFrames));
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, WIDTH, HEIGHT);
    ctx.globalAlpha = alpha;
    ctx.fillStyle = '#fff';
    ctx.font = `bold ${fontSize}px sans-serif`;
    ctx.textAlign = 'center';
    titleLines.forEach((line, idx) => ctx.fillText(line, WIDTH / 2, startY + idx * lineHeight));
    ctx.globalAlpha = 1.0;

    const tsUsec = tsOffsetUsec + Math.round(i * 1_000_000 / FPS);
    const frame = new VideoFrame(canvas, { timestamp: tsUsec });
    videoEnc.encode(frame, { keyFrame: i === 0 });
    frame.close();

    if (i % 15 === 0) await new Promise(r => setTimeout(r, 0));
  }

  // Silent audio for the title card duration
  const chunkSize = 1024;
  for (let offset = 0; offset < silenceCount; offset += chunkSize) {
    const count = Math.min(chunkSize, silenceCount - offset);
    const data = new Float32Array(count * 2); // stereo silence, interleaved
    const tsUsec = tsOffsetUsec + Math.round(offset * 1_000_000 / SAMPLE_RATE);
    const audioData = new AudioData({
      format: 'f32-planar',
      sampleRate: SAMPLE_RATE,
      numberOfFrames: count,
      numberOfChannels: 2,
      timestamp: tsUsec,
      data,
    });
    audioEnc.encode(audioData);
    audioData.close();
    if (offset % (chunkSize * 16) === 0) await new Promise(r => setTimeout(r, 0));
  }

  return tsOffsetUsec + Math.round(durationSec * 1_000_000);
}

// ── Clip: demux webm, decode VP9/Opus, apply fade-out, encode H.264/AAC ───────
async function encodeClip(url, startSec, endSec, videoEnc, audioEnc, videoDecoder,
                          audioDecoder, tsOffsetUsec, signal) {
  // MEDIABUNNY API: validate exact method names + callback shape in the spike.
  // The pattern below reflects the author's other libraries (mp4-muxer, webm-muxer).
  // Adjust if the console shows "not a function" errors and look at mediabunny's
  // README / TypeScript declarations in node_modules/mediabunny/dist/*.d.ts.

  const demuxer = new Demuxer();

  // mediabunny is expected to do HTTP range requests internally.
  // If load() takes a URL string it will range-read. If it requires ArrayBuffer,
  // replace with: const buf = await fetch(url, { signal }).then(r => r.arrayBuffer());
  //               await demuxer.load(buf);
  await demuxer.load(url, { signal });
  log(`  demuxer loaded: video=${demuxer.videoDecoderConfig?.codec}, audio=${demuxer.audioDecoderConfig?.codec}`);

  if (!videoDecoder.configured) {
    videoDecoder.configure(demuxer.videoDecoderConfig);
  }
  if (!audioDecoder.configured) {
    audioDecoder.configure(demuxer.audioDecoderConfig);
  }

  const clipDurationSec = endSec - startSec;
  const fadeOutSec = 2.0;
  const fadeStartSec = Math.max(0, clipDurationSec - fadeOutSec);

  // Collect decoded frames for fade-out processing
  const pendingVideoFrames = [];
  const pendingAudioFrames = [];

  const origVideoOutput = videoDecoder.onoutput;
  videoDecoder.onoutput = (frame) => {
    pendingVideoFrames.push(frame);
  };
  const origAudioOutput = audioDecoder.onoutput;
  audioDecoder.onoutput = (data) => {
    pendingAudioFrames.push(data);
  };

  // Extract encoded chunks in [startSec, endSec].
  // Adapt this call to the actual mediabunny API found in the spike.
  // Alternatives: demuxer.seek(startSec) + demuxer.read() in a loop,
  // or demuxer.extractRange(startSec, endSec, { onVideoChunk, onAudioChunk }).
  await demuxer.extractRange(startSec, endSec, {
    onVideoChunk: (chunk) => videoDecoder.decode(chunk),
    onAudioChunk: (chunk) => audioDecoder.decode(chunk),
  });

  await videoDecoder.flush();
  await audioDecoder.flush();

  videoDecoder.onoutput = origVideoOutput;
  audioDecoder.onoutput = origAudioOutput;

  // Apply video fade-out and re-encode
  const canvas = new OffscreenCanvas(WIDTH, HEIGHT);
  const ctx = canvas.getContext('2d');
  let videoFrameIdx = 0;
  for (const frame of pendingVideoFrames) {
    const relSec = (frame.timestamp / 1_000_000) - startSec;
    const tsUsec = tsOffsetUsec + Math.round(relSec * 1_000_000);
    const alpha = relSec >= fadeStartSec
      ? Math.max(0, 1.0 - (relSec - fadeStartSec) / fadeOutSec)
      : 1.0;

    ctx.globalAlpha = alpha;
    ctx.drawImage(frame, 0, 0, WIDTH, HEIGHT);
    ctx.globalAlpha = 1.0;

    const outFrame = new VideoFrame(canvas, { timestamp: tsUsec });
    videoEnc.encode(outFrame, { keyFrame: videoFrameIdx === 0 });
    outFrame.close();
    frame.close();
    videoFrameIdx++;
    if (videoFrameIdx % 15 === 0) await new Promise(r => setTimeout(r, 0));
  }

  // Apply audio fade-out and re-encode
  for (const data of pendingAudioFrames) {
    const relSec = (data.timestamp / 1_000_000) - startSec;
    const tsUsec = tsOffsetUsec + Math.round(relSec * 1_000_000);
    const count = data.numberOfFrames;
    const ch0 = new Float32Array(count);
    const ch1 = new Float32Array(count);
    data.copyTo(ch0, { planeIndex: 0 });
    data.copyTo(ch1, { planeIndex: 1 });

    const fadeGain = relSec >= fadeStartSec
      ? Math.max(0, 1.0 - (relSec - fadeStartSec) / fadeOutSec)
      : 1.0;
    for (let i = 0; i < count; i++) { ch0[i] *= fadeGain; ch1[i] *= fadeGain; }

    const merged = new Float32Array(count * 2);
    merged.set(ch0, 0);
    merged.set(ch1, count);
    const outData = new AudioData({
      format: 'f32-planar',
      sampleRate: SAMPLE_RATE,
      numberOfFrames: count,
      numberOfChannels: 2,
      timestamp: tsUsec,
      data: merged,
    });
    audioEnc.encode(outData);
    outData.close();
    data.close();
  }

  return tsOffsetUsec + Math.round(clipDurationSec * 1_000_000);
}

// ── Main spike entry point ─────────────────────────────────────────────────────
document.getElementById('btn-run').addEventListener('click', async () => {
  document.getElementById('btn-play').disabled = true;
  document.getElementById('log').textContent = 'Starting…';

  const url1 = document.getElementById('url1').value.trim();
  const url2 = document.getElementById('url2').value.trim();
  if (!url1) { log('Paste URL 1 first.'); return; }

  const controller = new AbortController();

  const target = new ArrayBufferTarget();
  const muxer = new Muxer({
    target,
    video: { codec: 'avc', width: WIDTH, height: HEIGHT },
    audio: { codec: 'aac', sampleRate: SAMPLE_RATE, numberOfChannels: 2 },
    fastStart: 'in-memory',
  });

  const videoEncoder = new VideoEncoder({
    output: (chunk, meta) => muxer.addVideoChunk(chunk, meta),
    error: (e) => log('VideoEncoder error: ' + e),
  });
  videoEncoder.configure({
    codec: 'avc1.42001f',
    width: WIDTH, height: HEIGHT,
    bitrate: 3_000_000,
    framerate: FPS,
  });

  const audioEncoder = new AudioEncoder({
    output: (chunk, meta) => muxer.addAudioChunk(chunk, meta),
    error: (e) => log('AudioEncoder error: ' + e),
  });
  audioEncoder.configure({
    codec: 'mp4a.40.2',
    sampleRate: SAMPLE_RATE,
    numberOfChannels: 2,
    bitrate: 128_000,
  });

  const videoDecoder = new VideoDecoder({
    output: () => {}, // replaced per-clip
    error: (e) => log('VideoDecoder error: ' + e),
  });
  videoDecoder.configured = false;

  const audioDecoder = new AudioDecoder({
    output: () => {}, // replaced per-clip
    error: (e) => log('AudioDecoder error: ' + e),
  });
  audioDecoder.configured = false;

  try {
    let ts = 0;

    log('Encoding title card 1…');
    ts = await encodeTitleCard(
      ['Clip 1', 'from 0:10', 'Segment 1 / 2'], 5.0, videoEncoder, audioEncoder, ts
    );

    log('Encoding clip 1 (10–40s)…');
    ts = await encodeClip(url1, 10, 40, videoEncoder, audioEncoder, videoDecoder, audioDecoder, ts, controller.signal);

    if (url2) {
      log('Encoding title card 2…');
      ts = await encodeTitleCard(
        ['Clip 2', 'from 1:00', 'Segment 2 / 2'], 5.0, videoEncoder, audioEncoder, ts
      );
      log('Encoding clip 2 (60–90s)…');
      ts = await encodeClip(url2, 60, 90, videoEncoder, audioEncoder, videoDecoder, audioDecoder, ts, controller.signal);
    }

    await videoEncoder.flush();
    await audioEncoder.flush();
    muxer.finalize();

    const blob = new Blob([target.buffer], { type: 'video/mp4' });
    const objUrl = URL.createObjectURL(blob);
    const video = document.getElementById('result');
    video.src = objUrl;
    document.getElementById('btn-play').disabled = false;
    document.getElementById('btn-play').onclick = () => video.play();
    log(`\nDone. MP4 size: ${(blob.size / 1024 / 1024).toFixed(1)} MB`);
  } catch (err) {
    log('ERROR: ' + err.stack);
  }
});
</script>
</body>
</html>
```

- [ ] **Step 4: Serve spike.html from the generator service and open it**

Add a temporary route to `generator_app.py` (inside `create_app`, after existing routes):

```python
@app.get("/spike")
def spike():
    return send_file(Path(__file__).parent / "spike.html")
```

Start the generator service:
```powershell
.\venv\Scripts\python.exe -c "from generator_app import create_app; create_app().run(debug=True, port=5001)"
```

Open `http://localhost:5001/spike` in Chrome, paste the two presigned URLs from Step 2, click **Run spike**, observe the console and `#log` output.

- [ ] **Step 5: Validate spike results**

Expected happy path:
- `demuxer loaded: video=vp09.00…, audio=opus` — mediabunny loaded and identified codecs
- No errors from VideoDecoder, AudioDecoder, VideoEncoder, AudioEncoder
- `Done. MP4 size: X.X MB` — a non-zero size
- Clicking **Play result** plays a video with two title cards and two faded clips

If you see `Demuxer.extractRange is not a function`:
- Open `node_modules/mediabunny/dist/mediabunny.js` (or `.d.ts`)  
- Find the method that iterates chunks in a time range
- Update `spike.html` accordingly and re-test

If you see R2 CORS errors for `Range` header:
- Run `.\venv\Scripts\python.exe set_cors.py` after adding `"Range"` to AllowedHeaders in `set_cors.py`:
  ```python
  'AllowedHeaders': ['Content-Type', 'Range'],
  'ExposeHeaders': ['Content-Range', 'Accept-Ranges', 'Content-Length'],
  ```

- [ ] **Step 6: Note the exact mediabunny API that worked**

In the spike `#log` area or a scratch note, record:
- The exact `Demuxer` constructor call
- The exact `load()` call signature
- The exact method for iterating chunks in `[start, end]`
- The shape of each chunk (does it give `EncodedVideoChunk` or something else?)

You will use this in Task 4.

- [ ] **Step 7: Remove the temporary `/spike` route from generator_app.py**

```python
# DELETE these lines from create_app:
@app.get("/spike")
def spike():
    return send_file(Path(__file__).parent / "spike.html")
```

---

## Task 1: Extract `_build_segment_list`; confirm existing tests pass

**Files:**
- Modify: `generator_app.py` (~lines 355–448)
- Test: `tests/test_generator_app.py`

This refactor makes the segment-planning logic available to both `_run_generation_impl` and the new `POST /plan` endpoint. Existing tests must all pass unchanged after this step.

- [ ] **Step 1: Write tests for `_build_segment_list` before touching the implementation**

Add to `tests/test_generator_app.py`:

```python
# ─── _build_segment_list ─────────────────────────────────────────────────────

def test_build_segment_list_returns_segments_with_correct_fields(tmp_path):
    from generator_app import _build_segment_list
    transcript = "[0:10] Speaker: Hello world. Great content here.\n[0:20] Speaker: Second line."
    (tmp_path / "video.webm").write_bytes(b"")
    (tmp_path / "video.txt").write_text(transcript, encoding="utf-8")
    vp = tmp_path / "video.webm"
    selections = {"video.webm": ["[0:10] Speaker: Hello world. Great content here."]}
    with patch("generator_app.get_video_duration", return_value=60.0):
        result = _build_segment_list([vp], selections)
    assert len(result) == 1
    seg = result[0]
    assert seg["video_name"] == "video.webm"
    assert seg["video_stem"] == "video"
    assert seg["start_sec"] == 10.0
    assert seg["title_lines"][0] == "video"
    assert seg["title_lines"][1] == "from 0:10"
    assert seg["title_lines"][2] == "Segment 1 / 1"


def test_build_segment_list_numbers_segments_across_videos(tmp_path):
    from generator_app import _build_segment_list
    for name in ["a.webm", "b.webm"]:
        (tmp_path / name).write_bytes(b"")
        (tmp_path / name).with_suffix(".txt").write_text(
            "[0:05] Speaker: Clip from this file.", encoding="utf-8"
        )
    vps = [tmp_path / "a.webm", tmp_path / "b.webm"]
    sel = {
        "a.webm": ["[0:05] Speaker: Clip from this file."],
        "b.webm": ["[0:05] Speaker: Clip from this file."],
    }
    with patch("generator_app.get_video_duration", return_value=60.0):
        result = _build_segment_list(vps, sel)
    assert len(result) == 2
    assert result[0]["title_lines"][2] == "Segment 1 / 2"
    assert result[1]["title_lines"][2] == "Segment 2 / 2"


def test_build_segment_list_skips_video_with_no_txt(tmp_path):
    from generator_app import _build_segment_list
    vp = tmp_path / "video.webm"
    vp.write_bytes(b"")
    # No .txt file
    with patch("generator_app.get_video_duration", return_value=60.0):
        result = _build_segment_list([vp], {"video.webm": ["[0:05] Speaker: Hi."]})
    assert result == []


def test_build_segment_list_uses_video_urls_for_ffmpeg_input(tmp_path):
    from generator_app import _build_segment_list
    vp = tmp_path / "video.webm"
    vp.write_bytes(b"")
    (tmp_path / "video.txt").write_text("[0:05] Speaker: Hi there.", encoding="utf-8")
    presigned = "https://r2.example.com/video.webm?sig=abc"
    with patch("generator_app.get_video_duration", return_value=60.0):
        result = _build_segment_list([vp], {"video.webm": ["[0:05] Speaker: Hi there."]},
                                     video_urls={"video.webm": presigned})
    assert result[0]["ffmpeg_input"] == presigned
```

- [ ] **Step 2: Run the new tests to confirm they fail (function not yet extracted)**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py -k "build_segment_list" -v
```

Expected: 4 failures with `ImportError` or `AttributeError` (function doesn't exist yet).

- [ ] **Step 3: Extract `_build_segment_list` from `_run_generation_impl`**

In `generator_app.py`, add this function immediately before `_run_generation` (around line 314):

```python
def _build_segment_list(
    video_paths: list,
    selections: dict,
    video_urls: dict | None = None,
) -> list[dict]:
    """Parse transcripts and group selected lines into ordered segments.

    Shared between POST /generate (via _run_generation_impl) and POST /plan
    so segment ordering, timing, and title-card text are computed once.

    Returns a list of dicts ordered as they appear in video_paths:
      video_name, video_stem, ffmpeg_input, start_sec, end_sec, title_lines
    Videos with no transcript or no matching selections are skipped silently.
    """
    grouped = []
    for vp in video_paths:
        selected_raws = selections.get(vp.name, [])
        if not selected_raws:
            continue
        txt_path = vp.with_suffix(".txt")
        if not txt_path.exists():
            continue
        all_lines = _parse_transcript_lines(txt_path.read_text(encoding="utf-8"))
        ffmpeg_input = video_urls.get(vp.name, str(vp)) if video_urls else str(vp)
        duration = get_video_duration(ffmpeg_input)
        segs = _group_lines_into_segments(all_lines, set(selected_raws), video_duration=duration)
        if segs:
            grouped.append((vp, segs, ffmpeg_input))

    total_segs = sum(len(segs) for _, segs, _ in grouped)
    result = []
    seg_num = 0
    for vp, segs, ffmpeg_input in grouped:
        for start_sec, end_sec in segs:
            seg_num += 1
            result.append({
                "video_name": vp.name,
                "video_stem": vp.stem,
                "ffmpeg_input": ffmpeg_input,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "title_lines": [
                    vp.stem,
                    f"from {_format_seconds(start_sec)}",
                    f"Segment {seg_num} / {total_segs}",
                ],
            })
    return result
```

- [ ] **Step 4: Refactor `_run_generation_impl` to call `_build_segment_list`**

Replace the existing `video_segments` loop and Phase-1 plan-building block in `_run_generation_impl` (lines ~367–448) with:

```python
    # Build segment plan (shared with POST /plan)
    segments = _build_segment_list(video_paths, selections, video_urls)

    # Log per-video results and advance progress counter
    segment_video_names = {s["video_name"] for s in segments}
    for vp in video_paths:
        if job["cancel"].is_set():
            with _jobs_lock:
                job["status"] = "cancelled"
            return
        selected_raws = selections.get(vp.name, [])
        if not selected_raws:
            continue
        if not vp.with_suffix(".txt").exists():
            _append_log(job_id, f"· {vp.name} — no transcript, skipping")
        elif vp.name not in segment_video_names:
            _append_log(job_id, f"· {vp.name} — selections produced no segments")
        else:
            count = sum(1 for s in segments if s["video_name"] == vp.name)
            _append_log(job_id, f"✓ {vp.name} — {count} segment(s)")
        with _jobs_lock:
            job["done"] += 1

    if not segments:
        with _jobs_lock:
            job["status"] = "error"
            job["error"] = "No segments found in selections"
        return

    TITLE_CARD_DURATION = 5.0
    total_segs = len(segments)
    _append_log(job_id, "· Extracting clips...")
    output_path = str(Path(folder) / output_filename)

    with tempfile.TemporaryDirectory() as tmp_dir:
        # ── Phase 1: Plan ────────────────────────────────────────────────
        plan = []
        item_idx = 0
        _dim_cache: dict = {}
        for seg in segments:
            ffmpeg_input = seg["ffmpeg_input"]
            if ffmpeg_input not in _dim_cache:
                try:
                    _dim_cache[ffmpeg_input] = get_video_dimensions(ffmpeg_input)
                except Exception:
                    _dim_cache[ffmpeg_input] = (1920, 1080)
            width, height = _dim_cache[ffmpeg_input]
            card_path = os.path.join(tmp_dir, f"clip_{item_idx:04d}.mp4")
            item_idx += 1
            clip_path = os.path.join(tmp_dir, f"clip_{item_idx:04d}.mp4")
            item_idx += 1
            plan.append({
                "type": "title",
                "path": card_path,
                "lines": seg["title_lines"],
                "width": width,
                "height": height,
                "ok": False,
                "error": None,
            })
            plan.append({
                "type": "clip",
                "path": clip_path,
                "video_path": ffmpeg_input,
                "start_sec": seg["start_sec"],
                "end_sec": seg["end_sec"],
                "ok": False,
                "error": None,
            })

        # ── Phase 2: Execute ── (unchanged from here down)
```

Everything from "Phase 2: Execute" onwards stays identical.

- [ ] **Step 5: Run ALL existing tests to confirm no regressions**

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests pass. If any generate-flow test fails, the refactor introduced a bug — fix before proceeding.

- [ ] **Step 6: Run the new _build_segment_list tests**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py -k "build_segment_list" -v
```

Expected: 4 PASS.

- [ ] **Step 7: Commit**

```bash
git add generator_app.py tests/test_generator_app.py
git commit -m "refactor: extract _build_segment_list shared by /generate and /plan"
```

---

## Task 2: Add `POST /plan` endpoint

**Files:**
- Modify: `generator_app.py` (new route inside `create_app`)
- Create: `tests/test_browser_endpoints.py`

- [ ] **Step 1: Write failing tests for POST /plan**

Create `tests/test_browser_endpoints.py`:

```python
"""Tests for POST /plan and POST /library browser-pipeline endpoints."""
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture
def cloud_client(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY", "key")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:9000")
    import importlib, storage, generator_app
    importlib.reload(storage)
    importlib.reload(generator_app)
    app = generator_app.create_app(testing=True)
    with app.test_client() as c:
        yield c


@pytest.fixture
def local_client():
    import importlib, generator_app
    # Ensure APP_MODE is not set to cloud
    os.environ.pop("APP_MODE", None)
    importlib.reload(generator_app)
    app = generator_app.create_app(testing=True)
    with app.test_client() as c:
        yield c


# ─── POST /plan ───────────────────────────────────────────────────────────────

def test_plan_returns_400_in_local_mode(local_client):
    resp = local_client.post("/plan", json={"session_key": "sessions/abc"})
    assert resp.status_code == 400
    assert "cloud" in resp.get_json()["error"].lower()


def test_plan_returns_400_without_session_key(cloud_client):
    resp = cloud_client.post("/plan", json={"selections": {}})
    assert resp.status_code == 400


def test_plan_returns_422_when_no_segments_found(cloud_client, tmp_path):
    session_key = "sessions/test123"
    # list_keys returns nothing
    with patch("generator_app.storage.list_keys", return_value=[]):
        resp = cloud_client.post("/plan", json={
            "session_key": session_key,
            "selections": {"video.webm": ["[0:10] Speaker: Hi."]},
        })
    assert resp.status_code == 422


def test_plan_returns_segment_list_with_correct_shape(cloud_client, tmp_path):
    import importlib, generator_app
    session_key = "sessions/test456"
    transcript = "[0:10] Speaker: This is great content.\n[0:25] Speaker: End."
    raw_line = "[0:10] Speaker: This is great content."

    # Write transcript to a temp file so _build_segment_list can read it
    txt_path = tmp_path / "interview.txt"
    txt_path.write_text(transcript, encoding="utf-8")

    def fake_download(key, local_path):
        if key.endswith(".txt"):
            Path(local_path).write_text(transcript, encoding="utf-8")

    presigned_get = "https://r2.example.com/interview.webm?sig=xyz"

    with patch("generator_app.storage.list_keys",
               return_value=[f"{session_key}/interview.webm", f"{session_key}/interview.txt"]), \
         patch("generator_app.storage.download_file", side_effect=fake_download), \
         patch("generator_app.storage.presigned_url", return_value=presigned_get), \
         patch("generator_app.storage.presigned_put_url", return_value="https://r2.example.com/put"), \
         patch("generator_app.get_video_duration", return_value=60.0), \
         patch("generator_app.get_video_dimensions", return_value=(1280, 720)):

        resp = cloud_client.post("/plan", json={
            "session_key": session_key,
            "selections": {"interview.webm": [raw_line]},
            "output_filename": "reel.mp4",
        })

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["session_key"] == session_key
    assert data["output_filename"] == "reel.mp4"
    assert data["width"] == 1280
    assert data["height"] == 720
    assert "presigned_put_url" in data
    assert "reel_key" in data
    assert len(data["segments"]) == 1
    seg = data["segments"][0]
    assert seg["video"] == "interview.webm"
    assert seg["presigned_get_url"] == presigned_get
    assert seg["start_sec"] == 10.0
    assert seg["title_lines"][0] == "interview"
    assert seg["title_lines"][1] == "from 0:10"
    assert seg["title_lines"][2] == "Segment 1 / 1"
```

- [ ] **Step 2: Run the new tests to confirm they fail**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_browser_endpoints.py -v
```

Expected: all fail with 404 (route doesn't exist yet).

- [ ] **Step 3: Add `POST /plan` to `generator_app.py`**

Inside `create_app`, after the existing `@app.post("/generate")` route, add:

```python
    @app.post("/plan")
    def plan():
        """Return the ordered segment plan + presigned URLs for browser-side encoding."""
        if not storage.is_cloud():
            return jsonify({"error": "browser planning is only available in cloud mode"}), 400

        body = request.get_json() or {}
        session_key = (body.get("session_key") or "").strip()
        if not session_key:
            return jsonify({"error": "session_key required"}), 400

        VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
        selections = body.get("selections", {})
        output_filename = Path((body.get("output_filename") or "sizzle_reel.mp4").strip()).name

        all_keys = storage.list_keys(session_key + "/")
        selected_filenames = set(selections.keys())

        tmp_dir = tempfile.mkdtemp(prefix="sizzle_plan_")
        try:
            # Download only the .txt transcripts we need (same logic as /generate)
            for key in all_keys:
                p = Path(key)
                if p.suffix.lower() == ".txt":
                    stem = p.stem
                    if any(Path(fn).stem == stem for fn in selected_filenames):
                        storage.download_file(key, os.path.join(tmp_dir, p.name))

            # Generate presigned GET URLs for selected video files (2-hour TTL)
            video_urls: dict = {}
            for key in all_keys:
                p = Path(key)
                if p.suffix.lower() in VIDEO_EXTS and p.name in selected_filenames:
                    video_urls[p.name] = storage.presigned_url(key, expires=7200)

            # Synthetic Path objects pointing to downloaded transcripts
            video_paths = sorted(
                [Path(tmp_dir) / fn for fn in video_urls],
                key=lambda p: p.name,
            )

            segments = _build_segment_list(video_paths, selections, video_urls)
            if not segments:
                return jsonify({"error": "No segments found in selections"}), 422

            # Probe dimensions from the first video's presigned URL (cheap, ~0.1s)
            try:
                width, height = get_video_dimensions(segments[0]["ffmpeg_input"])
            except Exception:
                width, height = 1920, 1080

            # Presigned PUT URL so the browser can upload the finished reel to R2
            reel_key = f"{session_key}/{output_filename}"
            presigned_put = storage.presigned_put_url(reel_key, expires=7200)

            return jsonify({
                "session_key": session_key,
                "output_filename": output_filename,
                "width": width,
                "height": height,
                "reel_key": reel_key,
                "presigned_put_url": presigned_put,
                "segments": [
                    {
                        "video": seg["video_name"],
                        "presigned_get_url": seg["ffmpeg_input"],
                        "start_sec": seg["start_sec"],
                        "end_sec": seg["end_sec"],
                        "title_lines": seg["title_lines"],
                    }
                    for seg in segments
                ],
            })
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 4: Run all tests**

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add generator_app.py tests/test_browser_endpoints.py
git commit -m "feat: POST /plan endpoint for browser-side reel generation"
```

---

## Task 3: Add `POST /library` endpoint

**Files:**
- Modify: `generator_app.py`
- Modify: `tests/test_browser_endpoints.py`

- [ ] **Step 1: Write failing tests for POST /library**

Append to `tests/test_browser_endpoints.py`:

```python
# ─── POST /library ────────────────────────────────────────────────────────────

def test_library_post_returns_400_without_required_fields(cloud_client):
    resp = cloud_client.post("/library", json={})
    assert resp.status_code == 400

    resp = cloud_client.post("/library", json={"session_key": "sessions/abc"})
    assert resp.status_code == 400  # missing output_filename


def test_library_post_creates_entry_with_correct_reel_s3_key(cloud_client):
    with patch("generator_app._library_add") as mock_add:
        resp = cloud_client.post("/library", json={
            "session_key": "sessions/abc123",
            "output_filename": "my_reel.mp4",
            "prompt": "exciting moments",
            "duration_seconds": 95,
            "clip_count": 4,
            "segment_starts": [0, 10.5, 30.2, 55.0],
        })
    assert resp.status_code == 200
    data = resp.get_json()
    assert "id" in data

    mock_add.assert_called_once()
    entry = mock_add.call_args[0][0]
    assert entry["reel_s3_key"] == "sessions/abc123/my_reel.mp4"
    assert entry["filename"] == "my_reel.mp4"
    assert entry["prompt"] == "exciting moments"
    assert entry["duration_seconds"] == 95
    assert entry["clip_count"] == 4
    assert entry["segment_starts"] == [0, 10.5, 30.2, 55.0]
    assert entry["source_folder"] == "abc123/"
    assert "created_at" in entry
    assert "id" in entry


def test_library_post_returns_same_id_as_entry(cloud_client):
    with patch("generator_app._library_add"):
        resp = cloud_client.post("/library", json={
            "session_key": "sessions/xyz",
            "output_filename": "reel.mp4",
        })
    assert resp.status_code == 200
    # The returned id should be a UUID string
    import re
    assert re.match(r"[0-9a-f-]{36}", resp.get_json()["id"])
```

- [ ] **Step 2: Run the new tests to confirm they fail**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_browser_endpoints.py -k "library_post" -v
```

Expected: all fail with 404.

- [ ] **Step 3: Add `POST /library` to `generator_app.py`**

Inside `create_app`, after the `POST /plan` route:

```python
    @app.post("/library")
    def library_add_endpoint():
        """Record a reel that the browser encoded and uploaded directly to R2."""
        body = request.get_json() or {}
        session_key = (body.get("session_key") or "").strip()
        output_filename = (body.get("output_filename") or "").strip()
        if not session_key or not output_filename:
            return jsonify({"error": "session_key and output_filename required"}), 400

        entry = {
            "id": str(uuid.uuid4()),
            "filename": output_filename,
            "path": "",
            "source_folder": Path(session_key).name + "/",
            "prompt": body.get("prompt", ""),
            "duration_seconds": int(body.get("duration_seconds", 0)),
            "clip_count": int(body.get("clip_count", 0)),
            "segment_starts": body.get("segment_starts", []),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "reel_s3_key": f"{session_key}/{output_filename}",
        }
        _library_add(entry)
        return jsonify({"id": entry["id"]})
```

- [ ] **Step 4: Run all tests**

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add generator_app.py tests/test_browser_endpoints.py
git commit -m "feat: POST /library endpoint for browser-uploaded reels"
```

---

## Task 4: Vendor mediabunny + mp4-muxer; build `reel-encoder.js`

**Files:**
- Create: `static/vendor/mediabunny.mjs`
- Create: `static/vendor/mp4-muxer.mjs`
- Create: `static/reel-encoder.js`

This task adapts the spike-validated code into production. Have the spike's working `encodeClip` Demuxer calls open beside you while writing.

- [ ] **Step 1: Vendor the two library ESM bundles**

```powershell
# If node_modules not present from spike, reinstall:
npm install mediabunny mp4-muxer

New-Item -ItemType Directory -Force static/vendor

# Copy ESM builds — adjust paths if the dist filenames differ
Copy-Item node_modules/mediabunny/dist/mediabunny.js static/vendor/mediabunny.mjs
Copy-Item node_modules/mp4-muxer/dist/mp4-muxer.js static/vendor/mp4-muxer.mjs
```

Verify both files exist and are non-empty:
```powershell
(Get-Item static/vendor/mediabunny.mjs).Length
(Get-Item static/vendor/mp4-muxer.mjs).Length
```

Expected: both > 0 bytes.

- [ ] **Step 2: Create `static/reel-encoder.js`**

This module exposes `window.ReelEncoder`. It is loaded as `<script type="module">` in `index.html`. The spike-validated mediabunny API is used for `encodeClip`'s `demuxer.load()` and `demuxer.extractRange()` — adapt those two calls if the spike used different method names.

Create `static/reel-encoder.js`:

```javascript
/**
 * Browser-side reel encoder for cloud mode.
 * Exposes window.ReelEncoder = { isSupported(), generate(plan, callbacks) }.
 *
 * Plan shape (from POST /plan):
 *   { session_key, output_filename, width, height, reel_key,
 *     presigned_put_url, segments: [{ video, presigned_get_url, start_sec, end_sec, title_lines }] }
 *
 * Callbacks: { onLog(msg), onProgress(done, total), signal (AbortSignal) }
 *
 * Returns: { entry_id, filename, duration_seconds, clip_count, segment_starts }
 * Throws on any error (caller retries via server /generate).
 */

import { Demuxer } from '/static/vendor/mediabunny.mjs';
import { Muxer, ArrayBufferTarget } from '/static/vendor/mp4-muxer.mjs';

const TITLE_CARD_DURATION_SEC = 5.0;
const TITLE_FADE_IN_SEC = 2.0;
const CLIP_FADE_OUT_SEC = 2.0;
const FPS = 30;
const SAMPLE_RATE = 48000;
const CHANNELS = 2;
const VIDEO_BITRATE = 3_000_000;
const AUDIO_BITRATE = 128_000;

// ── Title card ─────────────────────────────────────────────────────────────────
async function _encodeTitleCard(titleLines, width, height, videoEnc, audioEnc, tsUsec) {
  const canvas = new OffscreenCanvas(width, height);
  const ctx = canvas.getContext('2d');
  const totalFrames = Math.round(TITLE_CARD_DURATION_SEC * FPS);
  const fadeInFrames = Math.round(TITLE_FADE_IN_SEC * FPS);
  const fontSize = Math.max(24, Math.floor(height / 15));
  const lineHeight = Math.round(fontSize * 1.4);
  const totalTextH = titleLines.length * lineHeight;
  const baseY = Math.round((height - totalTextH) / 2 + fontSize);

  for (let i = 0; i < totalFrames; i++) {
    const alpha = Math.min(1.0, i / Math.max(1, fadeInFrames - 1));
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, width, height);
    ctx.globalAlpha = alpha;
    ctx.fillStyle = '#fff';
    ctx.font = `bold ${fontSize}px sans-serif`;
    ctx.textAlign = 'center';
    titleLines.forEach((line, idx) => ctx.fillText(line, width / 2, baseY + idx * lineHeight));
    ctx.globalAlpha = 1.0;

    const frameTs = tsUsec + Math.round(i * 1_000_000 / FPS);
    const frame = new VideoFrame(canvas, { timestamp: frameTs });
    videoEnc.encode(frame, { keyFrame: i === 0 });
    frame.close();
    if (i % 15 === 0) await new Promise(r => setTimeout(r, 0));
  }

  // Silent audio (fade-in matches video fade)
  const totalSamples = Math.round(TITLE_CARD_DURATION_SEC * SAMPLE_RATE);
  const chunkSize = 2048;
  for (let offset = 0; offset < totalSamples; offset += chunkSize) {
    const count = Math.min(chunkSize, totalSamples - offset);
    const frameTs = tsUsec + Math.round(offset * 1_000_000 / SAMPLE_RATE);
    const audioData = new AudioData({
      format: 'f32-planar',
      sampleRate: SAMPLE_RATE,
      numberOfFrames: count,
      numberOfChannels: CHANNELS,
      timestamp: frameTs,
      data: new Float32Array(count * CHANNELS), // silence
    });
    audioEnc.encode(audioData);
    audioData.close();
    if (offset % (chunkSize * 8) === 0) await new Promise(r => setTimeout(r, 0));
  }

  return tsUsec + Math.round(TITLE_CARD_DURATION_SEC * 1_000_000);
}

// ── Clip (demux VP9/Opus → decode → fade → encode H.264/AAC) ──────────────────
async function _encodeClip(presignedUrl, startSec, endSec, width, height,
                            videoEnc, audioEnc, tsUsec, signal) {
  const clipDurationSec = endSec - startSec;
  const fadeStartSec = Math.max(0, clipDurationSec - CLIP_FADE_OUT_SEC);

  // ── Collect all decoded video frames and audio data for the clip ─────────────
  // (decode first, then encode with fade, so we can apply the ramp in order)
  const videoFrames = [];
  const audioChunks = [];
  let videoConfigured = false;
  let audioConfigured = false;

  const videoDecoder = new VideoDecoder({
    output: (frame) => videoFrames.push(frame),
    error: (e) => { throw new Error('VideoDecoder: ' + e.message); },
  });

  const audioDecoder = new AudioDecoder({
    output: (data) => audioChunks.push(data),
    error: (e) => { throw new Error('AudioDecoder: ' + e.message); },
  });

  // MEDIABUNNY API — validated in spike. Adjust method names if they differ.
  const demuxer = new Demuxer();
  await demuxer.load(presignedUrl, { signal });

  if (!videoConfigured) {
    videoDecoder.configure(demuxer.videoDecoderConfig);
    videoConfigured = true;
  }
  if (!audioConfigured) {
    audioDecoder.configure(demuxer.audioDecoderConfig);
    audioConfigured = true;
  }

  // Extract encoded chunks for [startSec, endSec] using range requests.
  // Adapt this call to match the exact method name from the spike.
  await demuxer.extractRange(startSec, endSec, {
    onVideoChunk: (chunk) => videoDecoder.decode(chunk),
    onAudioChunk: (chunk) => audioDecoder.decode(chunk),
  });

  await videoDecoder.flush();
  await audioDecoder.flush();
  videoDecoder.close();
  audioDecoder.close();

  // ── Video: apply fade-out and encode ─────────────────────────────────────────
  const canvas = new OffscreenCanvas(width, height);
  const ctx = canvas.getContext('2d');
  let frameIdx = 0;
  for (const frame of videoFrames) {
    if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
    const relSec = (frame.timestamp / 1_000_000) - startSec;
    const fadeAlpha = relSec >= fadeStartSec
      ? Math.max(0, 1.0 - (relSec - fadeStartSec) / CLIP_FADE_OUT_SEC)
      : 1.0;

    ctx.clearRect(0, 0, width, height);
    ctx.globalAlpha = fadeAlpha;
    ctx.drawImage(frame, 0, 0, width, height);
    ctx.globalAlpha = 1.0;
    frame.close();

    const outTs = tsUsec + Math.round(relSec * 1_000_000);
    const outFrame = new VideoFrame(canvas, { timestamp: outTs });
    videoEnc.encode(outFrame, { keyFrame: frameIdx === 0 });
    outFrame.close();
    frameIdx++;
    if (frameIdx % 15 === 0) await new Promise(r => setTimeout(r, 0));
  }

  // ── Audio: apply fade-out and encode ─────────────────────────────────────────
  for (const data of audioChunks) {
    const relSec = (data.timestamp / 1_000_000) - startSec;
    const fadeGain = relSec >= fadeStartSec
      ? Math.max(0, 1.0 - (relSec - fadeStartSec) / CLIP_FADE_OUT_SEC)
      : 1.0;
    const count = data.numberOfFrames;
    const ch0 = new Float32Array(count);
    const ch1 = new Float32Array(count);
    data.copyTo(ch0, { planeIndex: 0 });
    data.copyTo(ch1, { planeIndex: 1 });
    for (let i = 0; i < count; i++) { ch0[i] *= fadeGain; ch1[i] *= fadeGain; }
    data.close();

    // mp4-muxer wants interleaved f32-planar — merge into one Float32Array
    const merged = new Float32Array(count * CHANNELS);
    merged.set(ch0, 0);
    merged.set(ch1, count);
    const outTs = tsUsec + Math.round(relSec * 1_000_000);
    const outData = new AudioData({
      format: 'f32-planar',
      sampleRate: SAMPLE_RATE,
      numberOfFrames: count,
      numberOfChannels: CHANNELS,
      timestamp: outTs,
      data: merged,
    });
    audioEnc.encode(outData);
    outData.close();
  }

  return tsUsec + Math.round(clipDurationSec * 1_000_000);
}

// ── Public API ─────────────────────────────────────────────────────────────────
window.ReelEncoder = {
  isSupported() {
    return (
      typeof VideoEncoder !== 'undefined' &&
      typeof VideoDecoder !== 'undefined' &&
      typeof AudioEncoder !== 'undefined' &&
      typeof AudioDecoder !== 'undefined' &&
      typeof OffscreenCanvas !== 'undefined'
    );
  },

  async generate(plan, { onLog, onProgress, signal, generatorUrl } = {}) {
    const log = onLog || console.log;
    const progress = onProgress || (() => {});
    const { width, height, segments, presigned_put_url, reel_key,
            session_key, output_filename } = plan;
    const total = segments.length * 2; // title + clip per segment
    let done = 0;

    // ── Set up muxer ──────────────────────────────────────────────────────────
    const target = new ArrayBufferTarget();
    const muxer = new Muxer({
      target,
      video: { codec: 'avc', width, height },
      audio: { codec: 'aac', sampleRate: SAMPLE_RATE, numberOfChannels: CHANNELS },
      fastStart: 'in-memory',
    });

    const videoEncoder = new VideoEncoder({
      output: (chunk, meta) => muxer.addVideoChunk(chunk, meta),
      error: (e) => { throw new Error('VideoEncoder: ' + e.message); },
    });
    videoEncoder.configure({
      codec: 'avc1.42001f',
      width, height,
      bitrate: VIDEO_BITRATE,
      framerate: FPS,
    });

    const audioEncoder = new AudioEncoder({
      output: (chunk, meta) => muxer.addAudioChunk(chunk, meta),
      error: (e) => { throw new Error('AudioEncoder: ' + e.message); },
    });
    audioEncoder.configure({
      codec: 'mp4a.40.2',
      sampleRate: SAMPLE_RATE,
      numberOfChannels: CHANNELS,
      bitrate: AUDIO_BITRATE,
    });

    // ── Encode each segment: title card + clip ────────────────────────────────
    let tsUsec = 0;
    const segmentStarts = [];
    let totalDurationSec = 0;

    for (let i = 0; i < segments.length; i++) {
      if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
      const seg = segments[i];

      log(`· Title card ${i + 1}/${segments.length}: ${seg.title_lines[0]}`);
      segmentStarts.push(tsUsec / 1_000_000);
      tsUsec = await _encodeTitleCard(seg.title_lines, width, height, videoEncoder, audioEncoder, tsUsec);
      totalDurationSec += TITLE_CARD_DURATION_SEC;
      progress(++done, total);

      if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
      log(`· Encoding clip ${i + 1}/${segments.length} (${seg.start_sec.toFixed(1)}–${seg.end_sec.toFixed(1)}s)…`);
      tsUsec = await _encodeClip(
        seg.presigned_get_url, seg.start_sec, seg.end_sec,
        width, height, videoEncoder, audioEncoder, tsUsec, signal
      );
      const clipDuration = seg.end_sec - seg.start_sec;
      totalDurationSec += clipDuration;
      progress(++done, total);
      log(`✓ Clip ${i + 1} done`);
    }

    await videoEncoder.flush();
    await audioEncoder.flush();
    muxer.finalize();
    videoEncoder.close();
    audioEncoder.close();

    const mp4Blob = new Blob([target.buffer], { type: 'video/mp4' });
    log(`· Uploading reel (${(mp4Blob.size / 1024 / 1024).toFixed(1)} MB) to cloud…`);

    // ── PUT to R2 ─────────────────────────────────────────────────────────────
    const putResp = await fetch(presigned_put_url, {
      method: 'PUT',
      headers: { 'Content-Type': 'video/mp4' },
      body: mp4Blob,
      signal,
    });
    if (!putResp.ok) {
      throw new Error(`R2 upload failed: ${putResp.status} ${putResp.statusText}`);
    }
    log(`✓ Reel uploaded to cloud storage`);

    // ── Record in library ─────────────────────────────────────────────────────
    const libResp = await fetch(`${generatorUrl}/library`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_key,
        output_filename,
        prompt: plan.prompt || '',
        duration_seconds: Math.round(totalDurationSec),
        clip_count: segments.length,
        segment_starts: segmentStarts,
      }),
      signal,
    });
    if (!libResp.ok) throw new Error(`Library record failed: ${libResp.status}`);
    const { id: entry_id } = await libResp.json();
    log(`✓ Done — saved to library`);

    return {
      entry_id,
      filename: output_filename,
      duration_seconds: Math.round(totalDurationSec),
      clip_count: segments.length,
      segment_starts: segmentStarts,
    };
  },
};
```

- [ ] **Step 3: Run the generator service and verify reel-encoder.js loads without errors**

```powershell
.\venv\Scripts\python.exe -c "from generator_app import create_app; create_app().run(debug=True, port=5001)"
```

In a browser console, open `http://localhost:5001` (or wherever the main app serves), then:
```javascript
window.ReelEncoder.isSupported()
// Expected: true (in Chrome)
```

If you see a 404 for `/static/vendor/mediabunny.mjs`, the static file path isn't right — check that `static/vendor/` is inside the generator app's `static/` directory (it serves `static/` by default).

- [ ] **Step 4: Commit**

```bash
git add static/vendor/mediabunny.mjs static/vendor/mp4-muxer.mjs static/reel-encoder.js
git commit -m "feat: reel-encoder.js browser pipeline + vendored mediabunny + mp4-muxer"
```

---

## Task 5: Wire `submitGenerate` in `app.js` + load `reel-encoder.js`

**Files:**
- Modify: `templates/index.html`
- Modify: `static/app.js`

The browser path calls `ReelEncoder.generate(plan, callbacks)`. On success it calls the existing `showResult` and `_clearSelections`. On error it transparently falls back to the server `/generate` flow.

- [ ] **Step 1: Add the module script tag to `index.html`**

In `templates/index.html`, add after the existing `<script src="/static/app.js">` line:

```html
<script type="module" src="/static/reel-encoder.js"></script>
```

The `type="module"` tag loads asynchronously (non-blocking) and ES module scope is isolated, so `window.ReelEncoder` is set via the explicit assignment in `reel-encoder.js`.

- [ ] **Step 2: Extend `showResult` in `app.js` to support the browser path**

Find `function showResult(result)` (around line 1371). Inside it, find this line:

```javascript
  const src = `${GENERATOR_URL}/video/${state.resultJobId}`;
```

Replace it with:

```javascript
  // Browser path has no server job — serve from library-video directly.
  const src = state.resultJobId
    ? `${GENERATOR_URL}/video/${state.resultJobId}`
    : `${GENERATOR_URL}/library-video/${result.entry_id}`;
```

- [ ] **Step 3: Extend `_autoSaveReelResult` to support the browser path (no jobId)**

Find `_autoSaveReelResult` (around line 122). Inside it, find:

```javascript
    const resp = await fetch(`${GENERATOR_URL}/video/${jobId}`, { signal: controller.signal });
```

Replace with:

```javascript
    const videoSrc = jobId
      ? `${GENERATOR_URL}/video/${jobId}`
      : `${GENERATOR_URL}/library-video/${entryId}`;
    const resp = await fetch(videoSrc, { signal: controller.signal });
```

- [ ] **Step 4: Replace `submitGenerate` with a version that forks on WebCodecs support**

Find `async function submitGenerate(mode, selections)` (around line 1176). Replace the entire function body with:

```javascript
async function submitGenerate(mode, selections) {
  const prompt = state.lastPrompt || $('analyze-input').value.trim();
  const outputBase = $('output-filename').value.trim().replace(/\.mp4$/i, '') || 'sizzle_reel';
  const outputFilename = outputBase + '.mp4';

  showScreen('screen-generating');
  $('gen-log').innerHTML = '';
  $('gen-bar').style.width = '0%';
  $('topbar-controls').classList.add('hidden');

  // In cloud mode, try the browser encode path if WebCodecs is available.
  if (APP_MODE === 'cloud' && window.ReelEncoder?.isSupported()) {
    try {
      await _submitGenerateBrowser(mode, selections, prompt, outputFilename);
      return;
    } catch (err) {
      if (err.name === 'AbortError') {
        // User cancelled — don't fall through to server
        showScreen('screen-workspace');
        $('topbar-controls').classList.remove('hidden');
        return;
      }
      appendLog('gen-log', `⚠ Browser encode failed (${err.message}) — retrying on server…`);
      $('gen-log').innerHTML = '';
      $('gen-bar').style.width = '0%';
    }
  }

  // Server path (local mode, unsupported browser, or browser fallback)
  await _submitGenerateServer(mode, selections, prompt, outputFilename);
}
```

- [ ] **Step 5: Extract the existing server generate logic into `_submitGenerateServer`**

Immediately after the new `submitGenerate`, add `_submitGenerateServer` (this is the original body of `submitGenerate`, renamed):

```javascript
async function _submitGenerateServer(mode, selections, prompt, outputFilename) {
  let resp, jobData;
  try {
    resp = await fetch(GENERATOR_URL + '/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        folder: state.folder,
        session_key: state.folder,
        mode,
        selections,
        prompt,
        output_filename: outputFilename,
      }),
    });
    jobData = await resp.json();
  } catch (err) {
    appendLog('gen-log', `✗ Could not reach generator service: ${err.message}`);
    $('topbar-controls').classList.remove('hidden');
    return;
  }

  const { job_id, error } = jobData;
  if (!resp.ok) {
    appendLog('gen-log', `✗ ${error || 'Failed to start generation'}`);
    $('topbar-controls').classList.remove('hidden');
    return;
  }

  state.currentJobId = job_id;
  watchGeneration(job_id);
}
```

- [ ] **Step 6: Add `_submitGenerateBrowser`**

Immediately after `_submitGenerateServer`, add:

```javascript
async function _submitGenerateBrowser(mode, selections, prompt, outputFilename) {
  const controller = new AbortController();
  _genTerminated = false;

  // Cancel button tears down the AbortController (no server job to DELETE)
  $('btn-cancel-gen').onclick = () => {
    _genTerminated = true;
    controller.abort();
    showScreen('screen-workspace');
    $('topbar-controls').classList.remove('hidden');
  };

  // POST /plan — get the segment list and presigned URLs
  appendLog('gen-log', '· Planning segments…');
  const planResp = await fetch(GENERATOR_URL + '/plan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_key: state.folder,
      mode,
      selections,
      prompt,
      output_filename: outputFilename,
    }),
    signal: controller.signal,
  });
  if (!planResp.ok) {
    const body = await planResp.json().catch(() => ({}));
    throw new Error(body.error || `Plan failed: ${planResp.status}`);
  }
  const plan = await planResp.json();
  plan.prompt = prompt;

  const total = plan.segments.length * 2;
  $('gen-bar').style.width = '5%';

  // ReelEncoder.generate drives the encode, upload, and library record
  const result = await window.ReelEncoder.generate(plan, {
    onLog: (msg) => appendLog('gen-log', msg),
    onProgress: (done, tot) => {
      const pct = tot > 0 ? Math.round((done / tot) * 100) : 0;
      $('gen-bar').style.width = Math.max(pct, 5) + '%';
    },
    signal: controller.signal,
    generatorUrl: GENERATOR_URL,
  });

  if (_genTerminated) return; // cancelled mid-encode

  // result: { entry_id, filename, duration_seconds, clip_count, segment_starts }
  _genTerminated = true;
  $('gen-bar').style.width = '100%';
  state.resultJobId = null; // no server job
  _clearSelections();

  // Show result — same screen as server path
  showResult({
    ...result,
    download_url: null, // library-video endpoint handles playback
  });

  // Auto-save (uses entry_id when jobId is null — see _autoSaveReelResult patch)
  if (result.entry_id) {
    const openBtn = $('btn-open-folder');
    openBtn.textContent = 'Saving…';
    openBtn.disabled = true;
    _autoSaveReelResult(null, result.filename, result.entry_id)
      .then(saved => {
        openBtn.disabled = false;
        if (saved) {
          openBtn.textContent = `✓ Saved to ${saved.folderName}`;
          openBtn.dataset.savedPath = saved.localFolderPath || '';
          openBtn.dataset.savedFilename = result.filename;
        } else {
          openBtn.textContent = 'Download';
        }
      })
      .catch(() => {
        openBtn.disabled = false;
        openBtn.textContent = 'Download';
      });
  }
}
```

- [ ] **Step 7: Start the app and verify the browser path is active**

```powershell
.\venv\Scripts\python.exe -c "from app import create_app; create_app().run(debug=True)"
.\venv\Scripts\python.exe -c "from generator_app import create_app; create_app().run(debug=True, port=5001)"
```

Open the app in Chrome. Open DevTools console. Load a folder and make some selections.
Before clicking Generate, run:

```javascript
window.ReelEncoder.isSupported()
// Expected: true in Chrome
```

Click Generate. In the `#gen-log` you should see:
```
· Planning segments…
· Title card 1/N: ...
· Encoding clip 1/N (X.X–Y.Ys)…
✓ Clip 1 done
...
· Uploading reel (X.X MB) to cloud…
✓ Reel uploaded to cloud storage
✓ Done — saved to library
```

Instead of the old:
```
· Extracting clips...
⟳ Live connection dropped — checking progress…
```

- [ ] **Step 8: Verify playback and library entry in the result screen**

After generation:
- The result screen video should play (served via `/library-video/<entry_id>`)
- The Library tab should show the new reel entry
- Open DevTools → Network tab: confirm no request to `/generate` (the server pipeline was not invoked)

- [ ] **Step 9: Delete the spike files**

```bash
git rm spike.html gen_presigned.py
```

- [ ] **Step 10: Commit**

```bash
git add templates/index.html static/app.js
git commit -m "feat: browser-side reel generation in cloud mode via WebCodecs

Browser path active when window.ReelEncoder.isSupported() (Chrome/Edge/Safari 16.4+).
Falls back to server /generate transparently on error or unsupported browser.
Server path and local-mode pipeline unchanged."
```

---

## Self-review checklist

- [x] **Spec coverage**
  - `_build_segment_list` extracted and shared → Task 1
  - `POST /plan` + tests → Task 2
  - `POST /library` + tests → Task 3
  - mediabunny + mp4-muxer vendored → Task 4
  - `reel-encoder.js` title card + fade + encode + PUT + library record → Task 4
  - `submitGenerate` WebCodecs detection + browser fork + server fallback → Task 5
  - Progress via `onLog`/`onProgress` (no WebSocket) → Task 5
  - Cancel via AbortController → Task 5
  - Local mode unchanged → no tasks (nothing to do)
  - Existing tests confirmed passing → Task 1 Step 5

- [x] **No placeholders**
  - mediabunny `extractRange` call is noted as "adapt from spike" with exact code provided — this is a documented adaptation point, not a TBD.

- [x] **Type consistency**
  - `_build_segment_list` used in Tasks 1, 2, 3 — same function name throughout
  - `window.ReelEncoder.generate(plan, callbacks)` — same shape in Tasks 4 and 5
  - `result.entry_id` — returned by `ReelEncoder.generate` in Task 4, consumed in Task 5
  - `_handleGenerationTerminal` — not called from the browser path; `showResult` is called directly
