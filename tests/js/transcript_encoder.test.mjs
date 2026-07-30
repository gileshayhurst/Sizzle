import assert from 'node:assert';

import {
  ASR_TIMEOUT_BASE_MS,
  ASR_TIMEOUT_PER_AUDIO_SEC_MS,
  asrTimeoutMs,
  concatPcm,
  downmixResample,
  isRich,
  pairFiles,
  pcmToWav,
  stem,
} from '../../static/transcript-encoder-core.js';

function fakeBuffer(channels, sampleRate) {
  return {
    numberOfChannels: channels.length,
    length: channels[0].length,
    sampleRate,
    getChannelData: (i) => channels[i],
  };
}

// ── isRich ────────────────────────────────────────────────────────────────────

export function test_is_rich_detects_end_timestamps() {
  assert.strictEqual(isRich('[0:13-0:15] Participant: Hi.'), true);
  assert.strictEqual(isRich('[0:13] Participant: Hi.'), false);
}

export function test_is_rich_finds_a_rich_line_anywhere_in_the_file() {
  assert.strictEqual(isRich('junk\n[2:05-2:08] Participant: Hi.'), true);
}

// ── stem / pairFiles ──────────────────────────────────────────────────────────

export function test_stem_strips_only_the_final_extension() {
  assert.strictEqual(stem('forven-interview-1a.2b.webm'), 'forven-interview-1a.2b');
  assert.strictEqual(stem('noextension'), 'noextension');
}

export function test_pair_files_matches_video_to_same_stem_transcript() {
  const files = [{ name: 'a.mp4' }, { name: 'a.txt' }, { name: 'b.mp4' }];
  const pairs = pairFiles(files);
  assert.strictEqual(pairs.length, 1);
  assert.strictEqual(pairs[0].video.name, 'a.mp4');
  assert.strictEqual(pairs[0].transcript.name, 'a.txt');
}

export function test_pair_files_ignores_transcripts_with_no_video() {
  assert.deepStrictEqual(pairFiles([{ name: 'orphan.txt' }]), []);
}

export function test_pair_files_handles_every_video_extension() {
  const files = [];
  for (const ext of ['mp4', 'mov', 'avi', 'mkv', 'webm']) {
    files.push({ name: `clip.${ext}` }, { name: 'clip.txt' });
  }
  assert.strictEqual(pairFiles(files).length, 5);
}

// ── asrTimeoutMs ──────────────────────────────────────────────────────────────

export function test_asr_timeout_scales_with_audio_length() {
  assert.strictEqual(asrTimeoutMs(0), ASR_TIMEOUT_BASE_MS);
  assert.strictEqual(asrTimeoutMs(10), ASR_TIMEOUT_BASE_MS + 10 * ASR_TIMEOUT_PER_AUDIO_SEC_MS);
}

export function test_asr_timeout_gives_a_long_interview_real_headroom() {
  // A 15-minute interview measured at ~0.85x realtime must not be cut off.
  const budget = asrTimeoutMs(900);
  assert.ok(budget > 900 * 1000, `budget ${budget}ms must exceed realtime`);
}

export function test_asr_timeout_never_returns_zero_for_bad_input() {
  // A detached buffer yields length 0, and NaN must not disable the timeout.
  for (const bad of [0, -5, NaN, undefined, Infinity]) {
    assert.strictEqual(asrTimeoutMs(bad), ASR_TIMEOUT_BASE_MS, String(bad));
  }
}

// ── downmixResample ───────────────────────────────────────────────────────────

export function test_downmix_averages_stereo_to_mono() {
  const buffer = fakeBuffer([new Float32Array([1, 1]), new Float32Array([-1, -1])], 16000);
  assert.deepStrictEqual(Array.from(downmixResample(buffer, 16000)), [0, 0]);
}

export function test_downmix_passes_mono_through_at_matching_rate() {
  const buffer = fakeBuffer([new Float32Array([0.25, -0.5])], 16000);
  assert.deepStrictEqual(Array.from(downmixResample(buffer, 16000)), [0.25, -0.5]);
}

export function test_resample_halves_length_when_input_rate_is_doubled() {
  const buffer = fakeBuffer([new Float32Array([0, 1, 2, 3])], 32000);
  const out = downmixResample(buffer, 16000);
  assert.strictEqual(out.length, 2);
  assert.deepStrictEqual(Array.from(out), [0, 2]);
}

// ── concatPcm ─────────────────────────────────────────────────────────────────

export function test_concat_pcm_joins_chunks_in_order() {
  const out = concatPcm([new Float32Array([1, 2]), new Float32Array([3])]);
  assert.deepStrictEqual(Array.from(out), [1, 2, 3]);
}

export function test_concat_pcm_handles_no_chunks() {
  assert.strictEqual(concatPcm([]).length, 0);
}

// ── pcmToWav ──────────────────────────────────────────────────────────────────

export async function test_wav_size_is_header_plus_two_bytes_per_sample() {
  const blob = pcmToWav(new Float32Array([0, 0.5, -0.5]), 16000);
  assert.strictEqual(blob.size, 44 + 6);
  assert.strictEqual(blob.type, 'audio/wav');
}

export async function test_wav_header_declares_mono_16k_16bit() {
  const blob = pcmToWav(new Float32Array([0]), 16000);
  const view = new DataView(await blob.arrayBuffer());
  const ascii = (o, n) => String.fromCharCode(...new Uint8Array(view.buffer, o, n));

  assert.strictEqual(ascii(0, 4), 'RIFF');
  assert.strictEqual(ascii(8, 4), 'WAVE');
  assert.strictEqual(view.getUint16(22, true), 1, 'channels');
  assert.strictEqual(view.getUint32(24, true), 16000, 'sample rate');
  assert.strictEqual(view.getUint16(34, true), 16, 'bits per sample');
  assert.strictEqual(ascii(36, 4), 'data');
}

export async function test_wav_clamps_samples_outside_unit_range() {
  const blob = pcmToWav(new Float32Array([2, -2]), 16000);
  const view = new DataView(await blob.arrayBuffer());
  assert.strictEqual(view.getInt16(44, true), 0x7fff);
  assert.strictEqual(view.getInt16(46, true), -0x8000);
}
