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
 * title_lines are burned onto each clip as a top-anchored, fading title
 * overlay (no separate title card).
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

const TITLE_SHOW_SEC = 3.0;   // how long the identification overlay stays up
const TITLE_FADE_SEC = 0.3;   // fade in / fade out duration
const TRANSITION_FADE_SEC = 0.4;  // symmetric head/tail dip between clips
const FPS = 30;
const SAMPLE_RATE = 48000;
const CHANNELS = 2;
const VIDEO_BITRATE = 3_000_000;
const AUDIO_BITRATE = 128_000;

function _throwIfAborted(signal) {
  if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
}

// Symmetric transition: fade in over the head, fade out over the tail. Applied
// to both video (canvas alpha) and audio (sample gain) so clips dip between each
// other now that there is no title card separating them.
function _transitionGain(relSec, clipDurationSec) {
  const fadeIn = Math.min(1.0, relSec / TRANSITION_FADE_SEC);
  const outStart = Math.max(0, clipDurationSec - TRANSITION_FADE_SEC);
  const fadeOut = relSec < outStart
    ? 1.0
    : Math.max(0, 1.0 - (relSec - outStart) / TRANSITION_FADE_SEC);
  return Math.min(fadeIn, fadeOut);
}

// Countdown of the clip's remaining whole seconds, pinned top-right on a
// translucent box (matches the server-side ffmpeg timer).
function _drawTimer(ctx, remainingSec, width, height, fontSize, alpha = 1) {
  if (alpha <= 0) return;
  const whole = Math.max(0, Math.ceil(remainingSec));
  const text = `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.font = `bold ${fontSize}px sans-serif`;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  const padX = fontSize * 0.4, padY = fontSize * 0.25;
  const tw = ctx.measureText(text).width;
  const margin = Math.max(12, Math.floor(fontSize * 0.6));
  const boxW = tw + padX * 2, boxH = fontSize + padY * 2;
  const x = width - boxW - margin, y = margin;
  ctx.fillStyle = 'rgba(0,0,0,0.5)';
  ctx.fillRect(x, y, boxW, boxH);
  ctx.fillStyle = '#fff';
  ctx.fillText(text, x + padX, y + padY);
  ctx.restore();
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

// Title overlay opacity at `relSec` into a clip: fade in, hold, fade out, gone.
// Mirrors video_editor._title_alpha_expr so both renderers behave the same.
function _titleAlpha(relSec, clipDurationSec) {
  const show = Math.min(TITLE_SHOW_SEC, clipDurationSec);
  if (relSec >= show) return 0;
  if (relSec < TITLE_FADE_SEC) return relSec / TITLE_FADE_SEC;
  if (relSec < show - TITLE_FADE_SEC) return 1;
  return Math.max(0, (show - relSec) / TITLE_FADE_SEC);
}

// Draw the identification lines, top-anchored, white text with a drop shadow so
// they stay legible over arbitrary video. `alpha` fades the whole overlay.
function _drawTitle(ctx, titleLines, width, height, alpha) {
  if (!titleLines || !titleLines.length || alpha <= 0) return;
  const fontSize = Math.max(20, Math.floor(height / 22));
  const lineHeight = Math.round(fontSize * 1.35);
  const top = Math.max(fontSize, Math.floor(height / 14)) + fontSize;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.font = `bold ${fontSize}px sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'alphabetic';
  ctx.shadowColor = 'rgba(0,0,0,0.8)';
  ctx.shadowOffsetX = 2;
  ctx.shadowOffsetY = 2;
  ctx.shadowBlur = 3;
  ctx.fillStyle = '#fff';
  titleLines.forEach((line, i) => ctx.fillText(line, width / 2, top + i * lineHeight));
  ctx.restore();
}

// ── Clip: range-read webm, decode VP9/Opus, apply fade-out, burn title, re-encode ─
async function _encodeClip(url, startSec, endSec, titleLines, width, height, ctx, videoSource, audioSource, startTs, signal) {
  const clipDurationSec = endSec - startSec;
  const input = new Input({ formats: ALL_FORMATS, source: new UrlSource(url) });

  try {
    const videoTrack = await input.getPrimaryVideoTrack();
    const audioTrack = await input.getPrimaryAudioTrack();

    // ── Video: decode sequentially, resample onto a fixed 30fps grid ───────────
    // Use CanvasSink.canvases() (sequential, pre-decoding) — NOT
    // canvasesAtTimestamps(), which is the *sparse* API and yields null for any
    // grid point that falls between the source's sample timestamps. This encoder
    // drew those nulls as BLACK, so whole clips came out black whenever a
    // source's frame timing didn't line up with our 30fps grid. Instead we walk
    // the decoded frames and hold the most-recent one across output frames. The
    // held frame is copied into an offscreen buffer because CanvasSink recycles
    // its pool canvases (poolSize), so the yielded canvas is not safe to retain.
    const timerFontSize = Math.max(20, Math.floor(height / 22));
    if (videoTrack) {
      const sink = new CanvasSink(videoTrack, {
        width, height, fit: 'contain', poolSize: 2,
      });
      const frameCount = Math.max(1, Math.round(clipDurationSec * FPS));

      const holdCanvas = new OffscreenCanvas(width, height);
      const holdCtx = holdCanvas.getContext('2d');
      let haveFrame = false;

      const srcIter = sink.canvases(startSec, endSec);
      let nextSrc = await srcIter.next();
      // Prime with the first decoded frame so the clip never opens on black even
      // if that frame starts slightly after startSec.
      if (!nextSrc.done && nextSrc.value) {
        holdCtx.drawImage(nextSrc.value.canvas, 0, 0, width, height);
        haveFrame = true;
        nextSrc = await srcIter.next();
      }

      for (let i = 0; i < frameCount; i++) {
        _throwIfAborted(signal);
        const relSec = i / FPS;
        const outT = startSec + relSec;
        // Advance to the newest source frame due at or before this output time.
        while (!nextSrc.done && nextSrc.value && nextSrc.value.timestamp <= outT + 1e-3) {
          holdCtx.clearRect(0, 0, width, height);
          holdCtx.drawImage(nextSrc.value.canvas, 0, 0, width, height);
          haveFrame = true;
          nextSrc = await srcIter.next();
        }
        const alpha = _transitionGain(relSec, clipDurationSec);
        ctx.fillStyle = '#000';
        ctx.fillRect(0, 0, width, height);
        if (haveFrame) {
          ctx.globalAlpha = alpha;
          ctx.drawImage(holdCanvas, 0, 0, width, height);
          ctx.globalAlpha = 1.0;
        }
        // Overlays dip with the frame during the head/tail transition (the
        // server's fade filter dims drawtext the same way).
        _drawTitle(ctx, titleLines, width, height, _titleAlpha(relSec, clipDurationSec) * alpha);
        _drawTimer(ctx, clipDurationSec - relSec, width, height, timerFontSize, alpha);
        await videoSource.add(startTs + relSec, 1 / FPS);
      }
    }

    // ── Audio: decode, apply transition gain, encode (or silent-pad) ───────────
    if (audioTrack) {
      const audioSink = new AudioBufferSink(audioTrack);
      for await (const { buffer, timestamp } of audioSink.buffers(startSec, endSec)) {
        _throwIfAborted(signal);
        const relSec = timestamp - startSec;
        const gain = _transitionGain(relSec, clipDurationSec);
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
    const total = segments.length; // one clip per segment (title burned onto clip)
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

    // ── Encode each segment's clip, in order, into one stream ───────────────────
    let ts = 0; // seconds
    const segmentStarts = [];

    try {
      for (let i = 0; i < segments.length; i++) {
        _throwIfAborted(signal);
        const seg = segments[i];

        segmentStarts.push(ts);
        log(`· Encoding clip ${i + 1}/${segments.length} (${seg.start_sec.toFixed(1)}–${seg.end_sec.toFixed(1)}s)…`);
        ts = await _encodeClip(seg.presigned_get_url, seg.start_sec, seg.end_sec, seg.title_lines,
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
