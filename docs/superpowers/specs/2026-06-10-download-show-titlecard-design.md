---
name: download-show-titlecard-fixes
description: Three fixes — auto-save generated reels to the user's input folder (cloud mode), Show button redesign with "not downloaded" dialog, and title card text cutoff on transitions.
metadata:
  type: project
---

# Design: Download / Show / Title Card Fixes

**Date:** 2026-06-10  
**Scope:** Cloud-mode download UX, library Show button behaviour, title card text overflow  

---

## 1. Auto-Save Reel to Input Folder (Cloud Mode)

### Problem
In cloud mode the "⬇ Download" button on the result screen calls
`window.open(presignedUrl, '_blank')`, which saves to the browser's system
Downloads folder. Users want the reel to land in the same local folder they
uploaded from.

### Constraint
The `webkitdirectory` picker exposes only the folder *name* (e.g. `ChickenVideos`),
not the full OS path. To write back to that folder the browser needs a separate
write-permission grant via the File System Access API.

### Solution

#### 1a. One-time output folder picker
- A **"Set output folder"** button appears in cloud mode on:
  - The result screen (below the video player)
  - The library header
- Clicking it calls `window.showDirectoryPicker({ mode: 'readwrite' })`.
- The returned `FileSystemDirectoryHandle` is persisted in **IndexedDB**
  (key: `sizzle_output_dir_handle`) so it survives page reloads.
- The chosen folder name is stored in `localStorage` key
  `sizzle_output_folder_name` for display in the UI.

#### 1b. Auto-write on generation complete
After a reel finishes generating (WebSocket `done` message received):
1. Fetch the video bytes from `${GENERATOR_URL}/video/${jobId}` (existing proxy
   endpoint — returns the file with CORS headers).
2. Write the bytes into the stored directory handle:
   ```js
   const fh = await dirHandle.getFileHandle(filename, { create: true });
   const writable = await fh.createWritable();
   await writable.write(blob);
   await writable.close();
   ```
3. Record in `localStorage` key `sizzle_downloads`:
   ```json
   { "<entryId>": { "folderName": "ChickenVideos", "filename": "sizzle_reel.mp4" } }
   ```
4. Update the result screen button label from "⬇ Download" to
   "✓ Saved to ChickenVideos / Open in browser".

If no directory handle is stored yet, fall back to the existing
`window.open(presignedUrl)` behaviour and prompt the user to set an output
folder.

#### 1c. Server-side OS path detection (for Explorer reveal)
To let the Show button open Windows Explorer to the exact file, the generator
service needs the full OS path. Since the generator runs on the same machine:

1. After writing to the directory handle (step 1b), write a tiny probe file into
   it: `sizzle_probe_<uuid>.tmp` (content: the uuid string).
2. Call `POST /find-local-folder` on the generator service with
   `{ probe_name: "sizzle_probe_<uuid>.tmp", probe_content: "<uuid>" }`.
3. Server scans these paths (fast — shallow search):
   ```
   %USERPROFILE%\Downloads\**   (depth 2)
   %USERPROFILE%\Videos\**      (depth 2)
   %USERPROFILE%\Documents\**   (depth 2)
   %USERPROFILE%\Desktop\**     (depth 2)
   %USERPROFILE%\Pictures\**    (depth 2)
   ```
   For each candidate folder, checks if `probe_name` exists and content matches.
4. Returns `{ "path": "C:\\Users\\giles\\Videos\\ChickenVideos" }` or `null`.
5. Client deletes the probe file from the directory handle.
6. If a path is found, store it in the `sizzle_downloads` record:
   `{ "<entryId>": { "folderName": "...", "filename": "...", "localFolderPath": "..." } }`.

If the scan returns `null` (folder is in an unusual location), the Show button
falls back to the browser player (see §2 below).

---

## 2. Library Show Button Redesign

### Problem
In cloud mode the Show button opens the video through the generator proxy
(`/library-video/<id>`), which plays it in a new browser tab. It doesn't
reflect whether the user has saved the file locally.

### New Behaviour

#### "Not downloaded" state (no entry in `sizzle_downloads`)
Clicking Show opens a modal overlay:

```
┌──────────────────────────────────────────┐
│  You have not downloaded this file       │
│                                          │
│  "sizzle_reel.mp4" has not been saved   │
│  to your local machine.                  │
│                                          │
│  [Save to ChickenVideos]  [View in Browser]  [✕]  │
└──────────────────────────────────────────┘
```

- **Save to ChickenVideos**: triggers the same auto-write flow as §1b (fetches
  bytes, writes to stored directory handle, runs probe scan). If no directory
  handle is set, prompts "Set output folder first" and opens `showDirectoryPicker()`.
- **View in Browser**: opens `${GENERATOR_URL}/library-video/${entry.id}` in a
  new tab (existing behaviour).

#### "Downloaded" state (entry present in `sizzle_downloads`)
- If `localFolderPath` is known: call `POST /open-folder` on the generator with
  `{ folder: localFolderPath, file_path: localFolderPath + "/" + filename }`.
  This opens Windows Explorer with the file highlighted — identical to local mode.
- If `localFolderPath` is null (probe scan failed): open the file from the
  directory handle as a blob URL in the browser player.

The button label in the library card changes to reflect state:
- Not downloaded: `📂 Show` (unchanged)
- Downloaded, path known: `📂 Show` (opens Explorer on click)
- Downloaded, path unknown: `🌐 View` (opens in browser)

In local mode the Show button is unchanged (calls `/open-folder` as before).

---

## 3. Title Card Text Cutoff

### Problem
Long video filenames produce title card text wider than the frame. The ffmpeg
`drawtext` expression `x=(w-text_w)/2` goes negative when `text_w > w`,
clipping the left edge of the text (e.g. "Chicken" renders as "hicken").

### Solution

#### 3a. Pre-render font size reduction (Python, in `make_title_card`)
Before building the ffmpeg filter, estimate text width and reduce `fontsize`
until the longest line fits within the frame with 80px of total horizontal
margin:

```python
# Rough estimate: Arial ~0.55× aspect ratio at given fontsize
max_chars = max(len(line) for line in lines)
usable_width = width - 80
while fontsize > 16 and max_chars * fontsize * 0.55 > usable_width:
    fontsize = int(fontsize * 0.9)
```

This ensures the estimated text width never exceeds `width - 80`.

#### 3b. ffmpeg expression safety clamp
Even with the pre-check, update the `x` expression to clamp against the actual
rendered width at encode time:

```
x=max(20,(w-text_w)/2)
```

This prevents any edge-case overflow reaching the encoder.

---

## Data Flow Summary

```
Upload folder selected
        │
        ▼
showDirectoryPicker() → FileSystemDirectoryHandle (IndexedDB)
        │
        ▼
Generation complete (WS done)
        │
        ├─ fetch /video/<jobId> → bytes
        ├─ write bytes → dirHandle.getFileHandle(filename)
        ├─ write probe file → /find-local-folder → OS path (optional)
        └─ update localStorage sizzle_downloads[entryId]
                │
                ▼
        Result screen: "✓ Saved to ChickenVideos"
        Library Show button: opens Explorer (if path known) or browser player
```

---

## Affected Files

| File | Change |
|------|--------|
| `static/app.js` | Output folder picker, auto-write on generation, Show button modal, downloaded-state logic |
| `generator_app.py` | `POST /find-local-folder` endpoint |
| `generator_app.py` | `make_title_card` font-size reduction + x clamp |
| `templates/index.html` | "Set output folder" button, "not downloaded" modal markup |
| `static/style.css` | Modal and state-indicator styles |

---

## Out of Scope

- Local mode Show button (already works via `/open-folder`)
- Mac/Linux Explorer equivalents (`open`, `xdg-open`) — Windows only for now
- Persisting directory handles across browser profiles or devices
