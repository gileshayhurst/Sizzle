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

/**
 * Encode one video/transcript pair end to end.
 * Returns { rich, stats } or null when the transcript is already rich.
 */
export async function encodePair(video, transcriptText, encoderUrl, callbacks = {}) {
  const { onProgress = () => {}, signal } = callbacks;
  if (isRich(transcriptText)) return null;

  const pcm = await extractAudio(video, onProgress);
  return encodeViaServer(pcm, transcriptText, encoderUrl, signal);
}

window.TranscriptEncoder = {
  isSupported,
  extractAudio,
  encodeViaServer,
  encodePair,
  isRich,
  pairFiles,
  pcmToWav,
  stem,
};
