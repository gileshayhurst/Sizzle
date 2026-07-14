/**
 * Browser-side reel encoder for cloud mode.
 * Exposes window.ReelEncoder = { isSupported(), generate(plan, callbacks) }.
 *
 * Implementation note: this is built on mediabunny's high-level pipeline, which
 * handles BOTH decode (CanvasSink / AudioBufferSink over an HTTP-range UrlSource)
 * AND encode+mux (Output + Mp4OutputFormat + CanvasSource + AudioBufferSource).
 * There is no separate muxer dependency and no hand-rolled WebCodecs juggling —
 * mediabunny wraps the browser's hardware H.264/AAC encoders internally.
 *
 * Plan shape (from POST /plan):
 *   { session_key, output_filename, width, height, reel_key,
 *     presigned_put_url, segments: [{ video, presigned_get_url, start_sec, end_sec, title_lines }] }
 *
 * Callbacks: { onLog(msg), onProgress(done, total), signal (AbortSignal), generatorUrl }
 *
 * Returns: { entry_id, filename, duration_seconds, clip_count, segment_starts }
 * Throws on any error (caller retries via server /generate) or DOMException
 * 'AbortError' when cancelled.
 */

import {
  Input,
  UrlSource,
  ALL_FORMATS,
  Output,
  Mp4OutputFormat,
  BufferTarget,
  CanvasSource,
  AudioBufferSource,
  CanvasSink,
  AudioBufferSink,
  canEncodeVideo,
} from '/static/vendor/mediabunny.mjs';

const TITLE_CARD_DURATION_SEC = 5.0;
const TITLE_FADE_IN_SEC = 2.0;
const CLIP_FADE_OUT_SEC = 2.0;
const FPS = 30;
const SAMPLE_RATE = 48000;
const CHANNELS = 2;
const VIDEO_BITRATE = 3_000_000;
const AUDIO_BITRATE = 128_000;

function _throwIfAborted(signal) {
  if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
}

function _fadeOutGain(relSec, clipDurationSec) {
  const fadeStart = Math.max(0, clipDurationSec - CLIP_FADE_OUT_SEC);
  if (relSec < fadeStart) return 1.0;
  return Math.max(0, 1.0 - (relSec - fadeStart) / CLIP_FADE_OUT_SEC);
}

// Parse a WebVTT string into [{start, end, text}] (seconds). Minimal parser:
// handles 'HH:MM:SS.mmm' and 'MM:SS.mmm' cue timings, ignores styling blocks.
function _parseVtt(vtt) {
  const cues = [];
  const toSec = t => {
    const parts = t.trim().split(':').map(parseFloat);
    return parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2]
                              : parts[0] * 60 + parts[1];
  };
  for (const block of vtt.split(/\n\n+/)) {
    const line = block.split('\n').find(l => l.includes('-->'));
    if (!line) continue;
    const [a, b] = line.split('-->');
    const text = block.split('\n').slice(block.split('\n').indexOf(line) + 1).join('\n').trim();
    if (text) cues.push({ start: toSec(a), end: toSec(b), text });
  }
  return cues;
}

// Draw up to two caption lines, bottom-centre, white text on a translucent box.
// Cue text may contain a '\n' (two-line cue from captions.build_webvtt).
function _drawCaption(ctx, text, width, height, fontSize) {
  const lines = String(text).split('\n').slice(0, 2);
  ctx.font = `bold ${fontSize}px sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'alphabetic';
  const padX = fontSize * 0.5, padY = fontSize * 0.3;
  const lineH = Math.round(fontSize * 1.25);
  const widest = Math.max(...lines.map(l => ctx.measureText(l).width));
  const boxW = Math.min(width * 0.9, widest + padX * 2);
  const boxH = lineH * lines.length + padY * 2;
  const boxTop = height - fontSize * 0.6 - boxH;   // small margin from the bottom
  ctx.fillStyle = 'rgba(0,0,0,0.55)';
  ctx.fillRect((width - boxW) / 2, boxTop, boxW, boxH);
  ctx.fillStyle = '#fff';
  lines.forEach((l, i) => {
    ctx.fillText(l, width / 2, boxTop + padY + lineH * i + fontSize);
  });
}

// ── Title card: draw text with fade-in on the shared canvas + silent audio ──────
async function _encodeTitleCard(titleLines, width, height, ctx, videoSource, audioSource, startTs, signal) {
  const totalFrames = Math.round(TITLE_CARD_DURATION_SEC * FPS);
  const fadeInFrames = Math.round(TITLE_FADE_IN_SEC * FPS);
  const fontSize = Math.max(24, Math.floor(height / 15));
  const lineHeight = Math.round(fontSize * 1.4);
  const totalTextH = titleLines.length * lineHeight;
  const baseY = Math.round((height - totalTextH) / 2 + fontSize);

  for (let i = 0; i < totalFrames; i++) {
    _throwIfAborted(signal);
    const alpha = Math.min(1.0, i / Math.max(1, fadeInFrames - 1));
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, width, height);
    ctx.globalAlpha = alpha;
    ctx.fillStyle = '#fff';
    ctx.font = `bold ${fontSize}px sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'alphabetic';
    titleLines.forEach((line, idx) => ctx.fillText(line, width / 2, baseY + idx * lineHeight));
    ctx.globalAlpha = 1.0;
    await videoSource.add(startTs + i / FPS, 1 / FPS);
  }

  // Silent stereo audio for the full title-card duration.
  const silence = new AudioBuffer({
    length: Math.round(TITLE_CARD_DURATION_SEC * SAMPLE_RATE),
    numberOfChannels: CHANNELS,
    sampleRate: SAMPLE_RATE,
  });
  await audioSource.add(silence);

  return startTs + TITLE_CARD_DURATION_SEC;
}

// ── Clip: range-read webm, decode VP9/Opus, apply fade-out, re-encode ───────────
async function _encodeClip(url, startSec, endSec, width, height, ctx, videoSource, audioSource, startTs, signal) {
  const clipDurationSec = endSec - startSec;
  const input = new Input({ formats: ALL_FORMATS, source: new UrlSource(url) });

  try {
    const videoTrack = await input.getPrimaryVideoTrack();
    const audioTrack = await input.getPrimaryAudioTrack();

    // ── Video: sample onto a fixed 30fps grid, apply fade, encode ──────────────
    if (videoTrack) {
      const sink = new CanvasSink(videoTrack, {
        width, height, fit: 'contain', poolSize: 2,
      });
      const frameCount = Math.max(1, Math.round(clipDurationSec * FPS));
      const timestamps = [];
      for (let i = 0; i < frameCount; i++) timestamps.push(startSec + i / FPS);

      let i = 0;
      for await (const wrapped of sink.canvasesAtTimestamps(timestamps)) {
        _throwIfAborted(signal);
        const relSec = i / FPS;
        const alpha = _fadeOutGain(relSec, clipDurationSec);
        ctx.fillStyle = '#000';
        ctx.fillRect(0, 0, width, height);
        if (wrapped) {
          ctx.globalAlpha = alpha;
          ctx.drawImage(wrapped.canvas, 0, 0, width, height);
          ctx.globalAlpha = 1.0;
        }
        await videoSource.add(startTs + relSec, 1 / FPS);
        i++;
      }
    }

    // ── Audio: decode, apply fade-out gain, encode (or silent-pad) ─────────────
    if (audioTrack) {
      const audioSink = new AudioBufferSink(audioTrack);
      for await (const { buffer, timestamp } of audioSink.buffers(startSec, endSec)) {
        _throwIfAborted(signal);
        const relSec = timestamp - startSec;
        const gain = _fadeOutGain(relSec, clipDurationSec);
        if (gain < 1.0) {
          for (let ch = 0; ch < buffer.numberOfChannels; ch++) {
            const data = buffer.getChannelData(ch);
            for (let s = 0; s < data.length; s++) data[s] *= gain;
          }
        }
        await audioSource.add(buffer);
      }
    } else {
      // Keep the audio timeline aligned with video when a source has no audio.
      const silence = new AudioBuffer({
        length: Math.round(clipDurationSec * SAMPLE_RATE),
        numberOfChannels: CHANNELS,
        sampleRate: SAMPLE_RATE,
      });
      await audioSource.add(silence);
    }
  } finally {
    input.dispose();
  }

  return startTs + clipDurationSec;
}

// ── Public API ──────────────────────────────────────────────────────────────────
window.ReelEncoder = {
  isSupported() {
    return (
      typeof VideoEncoder !== 'undefined' &&
      typeof AudioEncoder !== 'undefined' &&
      typeof OffscreenCanvas !== 'undefined' &&
      typeof AudioBuffer !== 'undefined'
    );
  },

  async generate(plan, { onLog, onProgress, signal, generatorUrl } = {}) {
    const log = onLog || console.log;
    const progress = onProgress || (() => {});
    const { width, height, segments, presigned_put_url,
            session_key, output_filename } = plan;
    const total = segments.length * 2; // title + clip per segment
    let done = 0;

    // Fail fast if this browser cannot encode H.264 at this resolution.
    if (!(await canEncodeVideo('avc', { width, height, bitrate: VIDEO_BITRATE }))) {
      throw new Error('Browser cannot encode H.264 at this resolution');
    }

    // ── Set up the single MP4 Output (concatenation is free — one stream) ───────
    const target = new BufferTarget();
    const output = new Output({
      format: new Mp4OutputFormat({ fastStart: 'in-memory' }),
      target,
    });

    const canvas = new OffscreenCanvas(width, height);
    const ctx = canvas.getContext('2d', { alpha: false });

    const videoSource = new CanvasSource(canvas, {
      codec: 'avc',
      bitrate: VIDEO_BITRATE,
    });
    const audioSource = new AudioBufferSource({
      codec: 'aac',
      bitrate: AUDIO_BITRATE,
      // Normalize every buffer (decoded Opus or generated silence) to stereo 48k.
      transform: { numberOfChannels: CHANNELS, sampleRate: SAMPLE_RATE },
    });

    output.addVideoTrack(videoSource, { frameRate: FPS });
    output.addAudioTrack(audioSource);
    await output.start();

    // ── Encode each segment: title card + clip, in order, into one stream ───────
    let ts = 0; // seconds
    const segmentStarts = [];

    try {
      for (let i = 0; i < segments.length; i++) {
        _throwIfAborted(signal);
        const seg = segments[i];

        log(`· Title card ${i + 1}/${segments.length}: ${seg.title_lines[0]}`);
        segmentStarts.push(ts);
        ts = await _encodeTitleCard(seg.title_lines, width, height, ctx, videoSource, audioSource, ts, signal);
        progress(++done, total);

        _throwIfAborted(signal);
        log(`· Encoding clip ${i + 1}/${segments.length} (${seg.start_sec.toFixed(1)}–${seg.end_sec.toFixed(1)}s)…`);
        ts = await _encodeClip(seg.presigned_get_url, seg.start_sec, seg.end_sec,
                               width, height, ctx, videoSource, audioSource, ts, signal);
        progress(++done, total);
        log(`✓ Clip ${i + 1} done`);
      }

      await output.finalize();
    } catch (err) {
      try { await output.cancel(); } catch { /* already torn down */ }
      throw err;
    }

    const totalDurationSec = ts;
    const mp4Blob = new Blob([target.buffer], { type: 'video/mp4' });
    log(`· Uploading reel (${(mp4Blob.size / 1024 / 1024).toFixed(1)} MB) to cloud…`);

    // ── PUT the finished reel straight to R2 ────────────────────────────────────
    const putResp = await fetch(presigned_put_url, {
      method: 'PUT',
      headers: { 'Content-Type': 'video/mp4' },
      body: mp4Blob,
      signal,
    });
    if (!putResp.ok) {
      throw new Error(`R2 upload failed: ${putResp.status} ${putResp.statusText}`);
    }
    log('✓ Reel uploaded to cloud storage');

    // ── Upload the caption track (if the plan produced one) ─────────────────
    let captionsKey = null;
    if (plan.captions_vtt && plan.captions_put_url) {
      const capResp = await fetch(plan.captions_put_url, {
        method: 'PUT',
        headers: { 'Content-Type': 'text/vtt' },
        body: plan.captions_vtt,
        signal,
      });
      if (capResp.ok) {
        captionsKey = plan.captions_key;
        log('✓ Captions uploaded');
      } else {
        log(`· Captions upload skipped (${capResp.status})`);
      }
    }

    // ── Record the reel in the shared library ───────────────────────────────────
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
        captions_key: captionsKey,
      }),
      signal,
    });
    if (!libResp.ok) throw new Error(`Library record failed: ${libResp.status}`);
    const { id: entry_id } = await libResp.json();
    log('✓ Done — saved to library');

    return {
      entry_id,
      filename: output_filename,
      duration_seconds: Math.round(totalDurationSec),
      clip_count: segments.length,
      segment_starts: segmentStarts,
    };
  },

  async burnCaptions(reelUrl, vttText, { onLog, onProgress, signal } = {}) {
    const log = onLog || console.log;
    const progress = onProgress || (() => {});
    const cues = _parseVtt(vttText);

    const input = new Input({ formats: ALL_FORMATS, source: new UrlSource(reelUrl) });
    const videoTrack = await input.getPrimaryVideoTrack();
    const audioTrack = await input.getPrimaryAudioTrack();
    const width = videoTrack.displayWidth, height = videoTrack.displayHeight;

    if (!(await canEncodeVideo('avc', { width, height, bitrate: VIDEO_BITRATE }))) {
      throw new Error('Browser cannot encode H.264 at this resolution');
    }

    const target = new BufferTarget();
    const output = new Output({ format: new Mp4OutputFormat({ fastStart: 'in-memory' }), target });
    const canvas = new OffscreenCanvas(width, height);
    const ctx = canvas.getContext('2d', { alpha: false });
    const videoSource = new CanvasSource(canvas, { codec: 'avc', bitrate: VIDEO_BITRATE });
    const audioSource = new AudioBufferSource({
      codec: 'aac', bitrate: AUDIO_BITRATE,
      transform: { numberOfChannels: CHANNELS, sampleRate: SAMPLE_RATE },
    });
    output.addVideoTrack(videoSource, { frameRate: FPS });
    output.addAudioTrack(audioSource);
    await output.start();

    const fontSize = Math.max(20, Math.floor(height / 22));
    try {
      const durationSec = await input.computeDuration();
      const totalFrames = Math.max(1, Math.round(durationSec * FPS));
      const sink = new CanvasSink(videoTrack, { width, height, fit: 'contain', poolSize: 2 });
      let f = 0;
      for await (const { canvas: frame, timestamp } of sink.canvases(0)) {
        _throwIfAborted(signal);
        ctx.drawImage(frame, 0, 0, width, height);
        const cue = cues.find(c => timestamp >= c.start && timestamp < c.end);
        if (cue) _drawCaption(ctx, cue.text, width, height, fontSize);
        await videoSource.add(f / FPS, 1 / FPS);
        if (++f % 15 === 0) progress(f, totalFrames);
        if (f >= totalFrames) break;
      }
      if (audioTrack) {
        const asink = new AudioBufferSink(audioTrack);
        for await (const { buffer } of asink.buffers()) {
          _throwIfAborted(signal);
          await audioSource.add(buffer);
        }
      }
      await output.finalize();
    } catch (err) {
      try { await output.cancel(); } catch { /* torn down */ }
      throw err;
    }
    log('✓ Captions burned in');
    return new Blob([target.buffer], { type: 'video/mp4' });
  },
};
