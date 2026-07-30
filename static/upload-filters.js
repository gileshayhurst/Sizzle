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

if (typeof window !== 'undefined') {
  window.UploadFilters = {
    GENERATED_REELS_MARKER,
    parseGeneratedReels,
    excludeGeneratedReels,
  };
}
