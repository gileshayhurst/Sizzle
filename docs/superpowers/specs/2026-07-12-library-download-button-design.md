# Library Download Button — Design

**Date:** 2026-07-12
**Status:** Approved, ready for implementation plan

## Problem

Library cover cards let users Edit, Delete, Play, and Show a generated reel, but
there is no one-click way to **download** the reel file. Downloading currently
requires the "Show" flow (open folder / open in new tab), which is indirect.

## Goal

Add a download button to each library card that saves the reel's `.mp4` to the
user's machine in one click, in both local and cloud modes.

## Approach

Reuse the existing `/library-video/<entry_id>` endpoint, which already serves the
reel (local `send_file`, or a redirect to a presigned R2 URL). Today it forces
`inline` (view in browser). Add a `?download=1` flag that flips the response's
`Content-Disposition` from `inline` to `attachment`, so the browser saves instead
of playing. No new endpoint, no storage-layer changes.

## Changes

### Frontend — `static/app.js`, `_renderCardBody` (~line 1962)

- Add a third `reel-btn-icon` button (download ↓ SVG) to `iconRow`.
- Insert it **first**, so the row reads **Download · Edit · Delete**.
- Set `title` / `aria-label` to "Download".
- Click handler: kick off a download of
  `${GENERATOR_URL}/library-video/${entry.id}?download=1` via a temporary
  `<a>` element (`a.href = url; a.download = ''`), appended, clicked, removed.
  The `download` attribute alone won't force a save cross-origin — the
  `?download=1` server flag does the real work; the anchor just starts the
  navigation without leaving the page.

### Backend — `generator_app.py`, `/library-video/<entry_id>` (~line 1048)

- Read `download = request.args.get("download") == "1"`.
- **Local path** (`send_file`): when `download`, pass
  `as_attachment=True, download_name=entry.get("filename", "reel.mp4")`.
- **Cloud path** (presigned URL): when `download`, build `content_disposition`
  as `attachment; filename="..."` instead of the current `inline; ...`. R2
  honors the disposition, so the redirect target downloads rather than plays.

## Testing

One assertion-style test (runs in the existing local-mode suite): a library
entry pointing at a real file on disk, `GET /library-video/<id>?download=1`
returns `200` with `Content-Disposition` starting with `attachment`. A companion
assertion that without the flag it is **not** `attachment` guards the
play-vs-download split.

## Out of scope (YAGNI)

- Download progress UI — the browser's native download handles this.
- Rename-on-download dialog — the browser's Save As covers it.
- Batch / "download all" — single-card download only.
