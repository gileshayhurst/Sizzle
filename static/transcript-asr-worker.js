/**
 * Whisper word-timestamp worker.
 *
 * Runs entirely on the client. Only the resulting word list (~30 KB for a
 * 4-minute interview) leaves the browser -- the audio never does.
 *
 * The model is an ANCHOR source, not a transcript source: the canonical words
 * come from the client's own transcript, and this only says WHEN they were
 * spoken. That is why `tiny` is the right size here -- measured on a real
 * interview it anchored as many sentences as `base` (47 of 48), because a
 * sentence needs only one matched word to be pinned.
 *
 * The library, the ONNX runtime and the model weights all load from the CDN.
 * The app sets no CSP, so this needs no build step. Vendoring only the 558 KB
 * library while its 12-25 MB runtime and ~40 MB of weights came from the CDN
 * anyway bought nothing, so it is not vendored. To self-host, put all three
 * behind R2 and set env.remoteHost / env.backends.onnx.wasm.wasmPaths.
 *
 * The explicit dist path is deliberate: dist/transformers.min.js bundles the
 * ONNX runtime, whereas dist/transformers.web.min.js carries a bare specifier
 * ("onnxruntime-web/webgpu") that no browser can resolve -- and import maps do
 * not apply to module workers. Pinned to an exact version so a CDN-side major
 * bump cannot silently change inference behaviour.
 */
import { pipeline, env } from 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.2.0/dist/transformers.min.js';

// No local model server; fetch from the hub.
env.allowLocalModels = false;

// Word-level timestamps require a model exported WITH cross-attentions. The
// onnx-community q8 export drops them and fails at inference with "Model
// outputs must contain cross attentions"; the Xenova export keeps them.
// Verified in-browser 2026-07-28 -- do not swap this for a smaller export
// without re-testing word timestamps.
export const MODEL_ID = 'Xenova/whisper-tiny.en';

// The WASM backend cannot load this model's quantised weights: both q8 and
// fp16 fail at session creation with "TransposeDQWeightsForMatMulNBits Missing
// required scale". fp32 works, and for a model this small it is not even slower
// -- measured on a 6s clip it beat WebGPU (4.7s vs 7.8s). WebGPU keeps the
// library default, which works there.
// Verified in-browser 2026-07-28. Do NOT set a quantised dtype for wasm.
const DEVICE_DTYPE = { wasm: 'fp32' };

let transcriber = null;
let loadedDevice = null;

async function getTranscriber(device) {
  if (transcriber && loadedDevice === device) return transcriber;
  const dtype = DEVICE_DTYPE[device];
  transcriber = await pipeline('automatic-speech-recognition', MODEL_ID, {
    device,
    ...(dtype ? { dtype } : {}),
    progress_callback: (report) => {
      if (report.status === 'progress' && report.total) {
        self.postMessage({
          type: 'progress',
          phase: 'model',
          fraction: report.loaded / report.total,
        });
      }
    },
  });
  loadedDevice = device;
  return transcriber;
}

self.onmessage = async (event) => {
  const { pcm, device } = event.data;
  try {
    const asr = await getTranscriber(device);
    self.postMessage({ type: 'progress', phase: 'transcribe', fraction: 0 });

    const output = await asr(pcm, {
      return_timestamps: 'word',
      // Whisper sees 30s at a time; the stride gives chunks overlap so a word
      // straddling a boundary is not lost.
      chunk_length_s: 30,
      stride_length_s: 5,
    });

    const words = (output.chunks || [])
      .filter(c => c.timestamp && c.timestamp[0] != null && c.timestamp[1] != null)
      .map(c => ({ w: c.text, s: c.timestamp[0], e: c.timestamp[1] }));

    self.postMessage({ type: 'done', words });
  } catch (err) {
    self.postMessage({ type: 'error', message: String((err && err.message) || err) });
  }
};
