# Loading-Folder Modal with Cancel — Design

**Date:** 2026-07-08
**Status:** Approved

## Problem

When a user opens a folder (most visibly via Recent Folders), the only loading
feedback is small muted italic text — "Loading folder…" — rendered in the
`#folder-error` div. It is easy to miss. In cloud mode the load can take ~10
seconds while `/load-folder` blocks inside `_ensure_cloud_session` downloading
transcripts from R2, and there is no way to stop it.

## Goal

Replace the small text with a modal popup (matching the app's existing overlay
style) that says "Loading folder", shows progress, and has an ✕ in the corner
that cancels the load — including the server-side download in cloud mode.

## 1. UI — the modal

A new overlay in `templates/index.html` using the existing `.overlay` /
`.overlay-card` / `.overlay-close` pattern (same as the library player and
"not downloaded" modals, so it matches DESIGN.md automatically):

- **Title:** "Loading folder", with the folder name beneath it.
- **Progress:** a progress bar (reusing `.progress-bar-wrap` / `.progress-bar`
  from the transcribing screen) plus a status line. Cloud mode shows real
  progress ("Downloading transcripts… 3 of 12"); local mode shows a brief
  indeterminate state since the scan is near-instant.
- **✕ button** top-right — cancels the load and returns to the folder picker.

The modal replaces the `folder-loading` small-text state entirely and appears
on every path into `openFolder()`: the recent-folders list, the folder-badge
dropdown, the Open Folder button, and cloud recent uploads.

## 2. Backend — async cloud session download

Today, cloud-mode `/load-folder` blocks the request thread inside
`_ensure_cloud_session` (app.py) while it downloads session transcripts.

New behaviour: when the session is **not** already cached, `/load-folder`
returns `{"job_id": ..., "job_type": "session_download"}` immediately and does
the work in a background daemon thread using the existing job system
(`_new_job`, `GET /status/<job_id>`, `DELETE /jobs/<job_id>`):

- The thread lists the session's keys, downloads each `.txt` one at a time —
  checking the job's cancel `Event` between files and updating `done`/`total`
  — and touches the 0-byte video placeholders.
- After the download it runs the same post-download logic that lives in
  `/load-folder` today (scan_videos, `_filter_generated_reels`, generated-reel
  sidecar filtering, transcript-presence checks, `_save_recent_folder`) and
  sets `job["result"] = {"folder": <local tmp>, "files": [...]}` — or an error
  string in `job["error"]` for the no-videos / no-transcripts cases.
- **On cancel:** delete the half-populated temp dir and remove the session
  from `_cloud_session_dirs` / `_cloud_session_ready` **before** releasing
  waiters, so a retry re-downloads cleanly. A concurrent waiter inside
  `_ensure_cloud_session` that wakes to a missing cache entry treats it as
  cancelled and raises an error instead of returning a broken path.
- If the session **is** already cached, `/load-folder` responds synchronously
  with files as today; the modal just flashes briefly.

Local mode is untouched server-side: the directory scan is fast, and
transcription already runs as a cancellable background job with its own
progress screen.

## 3. Frontend flow — `openFolder()` rewrite (static/app.js)

1. Show the modal; start the `/load-folder` fetch with an `AbortController`.
2. **✕ during the fetch:** abort the controller, close the modal, re-enable
   the picker. (At this point the cloud server either hasn't started a
   download or answers instantly from cache — nothing is orphaned.)
3. **Response contains a `session_download` job:** poll `/status/<job_id>`
   (same pattern as `pollTranscription`), updating the modal's bar and count.
   ✕ now calls `DELETE /jobs/<job_id>`, stops polling, and closes the modal.
4. **Job done:** close the modal → `loadTranscripts()` → workspace, as today.
   **Job error:** close the modal and show the message in the red
   `#folder-error` text.
5. **Local-mode transcription needed** — the response carries a `job_id` with
   no `job_type` field (only cloud `session_download` responses set it):
   close the modal and hand off to the existing transcribing screen unchanged.

## 4. Error handling

- Network/fetch failures keep today's behaviour — "Could not open folder — try
  uploading your files again." in red — with the modal closed first.
- Job errors (no videos, no transcripts, download failure) surface the same
  way after the modal closes.
- Cancel is silent: modal closes, picker returns to its idle state.

## 5. Testing

New pytest coverage (storage mocked throughout — no real R2):

- Cloud `/load-folder` on an uncached session returns a `session_download` job.
- The job completes and `result` carries the correct `folder`/`files`.
- `DELETE /jobs/<job_id>` mid-download cancels the job, cleans the session
  cache maps and temp dir, and a subsequent `/load-folder` retry re-downloads
  and succeeds.
- Cached-session `/load-folder` stays synchronous (no job).
- Local-mode `/load-folder` contract is unchanged.
