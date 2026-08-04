/**
 * Pure helpers for the cloud upload flow. Exposed as window.UploadFilters.
 *
 * Imports nothing, so the zero-dependency node runner in tests/js/ can test the
 * semantics directly — which matters here, because these have to agree with the
 * server's filtering in app.py (`set(sidecar.read_text().splitlines())` then
 * `p.name not in locally_generated`).
 */

/**
 * Written into a folder by the generator whenever it produces a reel there
 * (generator_app.py, local mode). It travels with the folder, so it is the only
 * way to recognise a generated reel before anything has been uploaded.
 */
export const GENERATED_REELS_MARKER = 'sizzle_generated_reels.txt';

/**
 * Filenames listed in the marker.
 *
 * Python's splitlines() handles CRLF and a trailing newline; split(/\r?\n/) plus
 * a trim keeps this equivalent, and tolerates a hand-edited marker.
 */
export function parseGeneratedReels(markerText) {
  return new Set(
    String(markerText || '')
      .split(/\r?\n/)
      .map(line => line.trim())
      .filter(Boolean),
  );
}

/**
 * Drop previously generated reels from a selection.
 *
 * The server already refuses to treat these as source videos, but it only learns
 * of them AFTER the upload — so without this the user pays to transfer every
 * reel in the folder (~1.5 GB on the reference folder) before it is discarded.
 * Matching is on the exact filename, mirroring the server.
 */
export function excludeGeneratedReels(files, markerText) {
  const generated = parseGeneratedReels(markerText);
  if (!generated.size) return files;
  return files.filter(file => !generated.has(file.name));
}

/**
 * PUT one file to its presigned R2 URL, retrying transient failures.
 *
 * Interviews here run to 1.4 GB, and the browser uploads them sequentially, so a
 * single dropped connection used to throw straight out of doUpload -- back to
 * the folder picker, with a half-populated session still sitting in R2 and
 * showing up in the recent list. Minutes of transfer lost to one blip.
 *
 * Retries a thrown fetch (network drop) and 5xx/429. Everything else is
 * terminal: a 403 means the signature is expired or wrong, and re-sending 1.4 GB
 * to be refused again helps nobody.
 *
 * `fetchImpl` and `sleep` are injected so the node runner can exercise this
 * without a network or a real delay -- this file still imports nothing.
 */
export async function putWithRetry(url, file, {
  attempts = 3,
  fetchImpl = (typeof fetch !== 'undefined' ? fetch : null),
  sleep = ms => new Promise(r => setTimeout(r, ms)),
} = {}) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    let resp;
    try {
      resp = await fetchImpl(url, { method: 'PUT', body: file });
    } catch (err) {
      lastError = err;
      if (attempt === attempts) throw err;
      await sleep(attempt * 1000);
      continue;
    }
    if (resp.ok) return resp;
    if (resp.status !== 429 && resp.status < 500) {
      throw new Error(`upload refused (${resp.status})`);
    }
    lastError = new Error(`upload failed (${resp.status})`);
    if (attempt === attempts) throw lastError;
    await sleep(attempt * 1000);
  }
  throw lastError;
}

if (typeof window !== 'undefined') {
  window.UploadFilters = {
    GENERATED_REELS_MARKER,
    parseGeneratedReels,
    excludeGeneratedReels,
    putWithRetry,
  };
}
