/**
 * Browser-side transcript encoder for cloud mode.
 * Exposes window.TranscriptEncoder.
 *
 * Turns a selected video plus its plain Forven transcript into a RICH
 * transcript ([M:SS-M:SS] Speaker: sentence) that shared.py consumes directly.
 *
 * What crosses the network, and what deliberately does not:
 *   video  -> browser to R2 only, by presigned PUT, exactly as before
 *   audio  -> mono 16 kHz WAV to the encoder service (~8 MB for 4 minutes)
 *   result -> a few KB of text
 * The encoder service must never receive video; that split is the reason it
 * exists as a separate program.
 *
 * Phase 2b will run the ASR here in a Worker and post a ~30 KB word list to
 * /encode/words instead. The algorithm does not move -- both paths call the
 * same encoder.core.encode on the server.
 */
import { Input, BlobSource, ALL_FORMATS, AudioBufferSink } from '/static/vendor/mediabunny.mjs';

import {
  TARGET_SAMPLE_RATE,
  asrTimeoutMs,
  concatPcm,
  downmixResample,
  isRich,
  pairFiles,
  pcmToWav,
  stem,
} from '/static/transcript-encoder-core.js';

/** Whether this browser can decode audio for encoding at all. */
export function isSupported() {
  return typeof AudioBuffer !== 'undefined' && typeof WebAssembly !== 'undefined';
}

/**
 * Decode `file` to mono 16 kHz PCM.
 *
 * mediabunny streams the decode, so a 250 MB video never lands in memory whole
 * -- only the resulting PCM does, which is 2 bytes per sample: about 28 MB for
 * a 15-minute interview.
 */
export async function extractAudio(file, onProgress = () => {}) {
  const input = new Input({ formats: ALL_FORMATS, source: new BlobSource(file) });
  const track = await input.getPrimaryAudioTrack();
  if (!track) throw new Error(`${file.name} has no audio track`);

  let duration = 0;
  try {
    duration = await input.computeDuration();
  } catch {
    duration = 0;   // progress reporting only; not worth failing the encode over
  }

  const chunks = [];
  const sink = new AudioBufferSink(track);
  for await (const { buffer, timestamp } of sink.buffers()) {
    chunks.push(downmixResample(buffer, TARGET_SAMPLE_RATE));
    if (duration) onProgress(Math.min(1, timestamp / duration));
  }
  onProgress(1);
  return concatPcm(chunks);
}

/**
 * Send audio plus the plain transcript to the encoder service.
 * Returns { rich, stats }.
 */
export async function encodeViaServer(pcm, transcriptText, encoderUrl, signal) {
  const form = new FormData();
  form.append('transcript', transcriptText);
  form.append('audio', pcmToWav(pcm), 'align.wav');

  const response = await fetch(`${encoderUrl.replace(/\/$/, '')}/encode`, {
    method: 'POST',
    body: form,
    signal,
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    throw new Error(`Encoder failed (${response.status}) ${detail.slice(0, 200)}`);
  }
  return response.json();
}

/** Whether this browser can run the ASR locally, and on what device. */
export async function asrDevice() {
  if (typeof Worker === 'undefined') return null;
  if (typeof navigator !== 'undefined' && navigator.gpu) {
    try {
      if (await navigator.gpu.requestAdapter()) return 'webgpu';
    } catch { /* fall through to wasm */ }
  }
  return typeof WebAssembly !== 'undefined' ? 'wasm' : null;
}

/**
 * Run Whisper in a Worker and return a word stream.
 *
 * The audio never leaves the browser on this path -- only the resulting word
 * list does, which is ~30 KB against ~8 MB of WAV.
 */
export function wordsInBrowser(pcm, device, onProgress = () => {}, timeoutMs = null) {
  // Computed BEFORE postMessage: transferring the buffer detaches it and
  // pcm.length becomes 0.
  const budget = timeoutMs === null ? asrTimeoutMs(pcm.length / TARGET_SAMPLE_RATE) : timeoutMs;

  return new Promise((resolve, reject) => {
    const worker = new Worker('/static/transcript-asr-worker.js', { type: 'module' });

    let settled = false;
    const finish = (fn, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      worker.terminate();
      fn(value);
    };

    // Terminating the worker is what actually stops the work -- a rejected
    // promise alone would leave it burning CPU for the rest of the session.
    const timer = setTimeout(
      () => finish(reject, new Error(
        `local transcription exceeded its ${Math.round(budget / 1000)}s budget`)),
      budget,
    );

    worker.onmessage = (event) => {
      const msg = event.data;
      if (msg.type === 'progress') onProgress(msg);
      else if (msg.type === 'done') finish(resolve, msg.words);
      else if (msg.type === 'error') finish(reject, new Error(msg.message));
    };
    worker.onerror = (err) => finish(reject, new Error(err.message || 'ASR worker failed'));

    // Transfer the PCM buffer rather than copying it -- it is ~28 MB for a
    // 15-minute interview.
    worker.postMessage({ pcm, device }, [pcm.buffer]);
  });
}

/** Send a word stream plus the plain transcript to the encoder service. */
export async function encodeViaWords(words, transcriptText, encoderUrl, signal) {
  const response = await fetch(`${encoderUrl.replace(/\/$/, '')}/encode/words`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transcript: transcriptText, words }),
    signal,
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    throw new Error(`Encoder failed (${response.status}) ${detail.slice(0, 200)}`);
  }
  return response.json();
}

/**
 * Encode one video/transcript pair end to end.
 * Returns { rich, stats, path } or null when the transcript is already rich.
 *
 * Prefers the browser path: zero server CPU, and the audio never leaves the
 * client. Falls back to shipping the audio when the browser cannot run the
 * model. Both paths hit the same encoder.core.encode on the server, so the
 * alignment algorithm never forks.
 */
export async function encodePair(video, transcriptText, encoderUrl, callbacks = {}) {
  const { onProgress = () => {}, onLog = () => {}, signal, timeoutMs = null } = callbacks;
  if (isRich(transcriptText)) return null;

  const pcm = await extractAudio(video, p => onProgress({ phase: 'audio', fraction: p }));

  const device = await asrDevice();
  if (device) {
    try {
      const words = await wordsInBrowser(pcm, device, onProgress, timeoutMs);
      if (words.length) {
        const result = await encodeViaWords(words, transcriptText, encoderUrl, signal);
        return { ...result, path: `browser-${device}` };
      }
      onLog('Local transcription produced no words; sending audio instead.');
    } catch (err) {
      onLog(`Local transcription unavailable (${err.message}); sending audio instead.`);
    }
  }

  // Fallback. pcm.buffer may have been transferred to the worker above, in
  // which case re-extract rather than posting an empty buffer.
  const audio = pcm.length ? pcm : await extractAudio(video);
  const result = await encodeViaServer(audio, transcriptText, encoderUrl, signal);
  return { ...result, path: 'server' };
}

window.TranscriptEncoder = {
  isSupported,
  asrDevice,
  asrTimeoutMs,
  extractAudio,
  wordsInBrowser,
  encodeViaWords,
  encodeViaServer,
  encodePair,
  isRich,
  pairFiles,
  pcmToWav,
  stem,
};
