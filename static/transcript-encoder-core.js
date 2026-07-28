/**
 * Pure helpers for the browser transcript encoder.
 *
 * Deliberately imports nothing: mediabunny resolves from an absolute URL that
 * only exists in the browser, so keeping the arithmetic here is what makes it
 * testable under the node runner in tests/js/. Same split as the Python side,
 * where encoder/core is pure and the ASR adapter is not.
 */

export const TARGET_SAMPLE_RATE = 16000;

export const VIDEO_EXTS = ['.mp4', '.mov', '.avi', '.mkv', '.webm'];

/** True when a transcript already carries end timestamps (mirrors encoder.cli.is_rich). */
export function isRich(text) {
  return /^\[\d+:\d{2}-\d+:\d{2}\]/m.test(text);
}

/** Strip the final extension. "a.b.mp4" -> "a.b" */
export function stem(name) {
  const dot = name.lastIndexOf('.');
  return dot === -1 ? name : name.slice(0, dot);
}

/**
 * Pair each video with its same-stem .txt from a flat file list.
 * Returns [{video, transcript}] -- videos with no transcript are skipped,
 * because there is nothing to align against.
 */
export function pairFiles(files) {
  const texts = new Map();
  for (const file of files) {
    if (file.name.toLowerCase().endsWith('.txt')) texts.set(stem(file.name), file);
  }
  const pairs = [];
  for (const file of files) {
    const lower = file.name.toLowerCase();
    if (!VIDEO_EXTS.some(ext => lower.endsWith(ext))) continue;
    const transcript = texts.get(stem(file.name));
    if (transcript) pairs.push({ video: file, transcript });
  }
  return pairs;
}

/**
 * Average an AudioBuffer's channels to mono and resample to `rate`.
 *
 * Nearest-sample resampling is deliberate: the output feeds an ASR that is only
 * an ANCHOR source, and word boundaries survive it intact. Interpolating would
 * cost cycles on every sample of every interview to improve audio nobody hears.
 */
export function downmixResample(buffer, rate = TARGET_SAMPLE_RATE) {
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

/** Concatenate Float32Array chunks into one buffer. */
export function concatPcm(chunks) {
  let total = 0;
  for (const chunk of chunks) total += chunk.length;
  const out = new Float32Array(total);
  let offset = 0;
  for (const chunk of chunks) { out.set(chunk, offset); offset += chunk.length; }
  return out;
}

/** Wrap mono PCM as a 16-bit WAV blob, the format POST /encode accepts. */
export function pcmToWav(pcm, rate = TARGET_SAMPLE_RATE) {
  const buffer = new ArrayBuffer(44 + pcm.length * 2);
  const view = new DataView(buffer);
  const ascii = (offset, text) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };

  ascii(0, 'RIFF');
  view.setUint32(4, 36 + pcm.length * 2, true);
  ascii(8, 'WAVE');
  ascii(12, 'fmt ');
  view.setUint32(16, 16, true);          // subchunk size
  view.setUint16(20, 1, true);           // PCM
  view.setUint16(22, 1, true);           // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);    // byte rate
  view.setUint16(32, 2, true);           // block align
  view.setUint16(34, 16, true);          // bits per sample
  ascii(36, 'data');
  view.setUint32(40, pcm.length * 2, true);

  for (let i = 0; i < pcm.length; i++) {
    const sample = Math.max(-1, Math.min(1, pcm[i]));
    view.setInt16(44 + i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }
  return new Blob([buffer], { type: 'audio/wav' });
}
