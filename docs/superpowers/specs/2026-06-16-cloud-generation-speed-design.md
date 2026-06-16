# Cloud Generation Speed — Design Spec

**Date:** 2026-06-16  
**Status:** Approved for implementation

## Problem

In cloud mode, the generation pipeline has two serial bottlenecks that dominate wall-clock time:

1. **All session files are downloaded to Render before any work starts** — including videos the user never selected. For a session with 10 source videos and 4 selected, 6 full video files are downloaded for nothing. Large video files make this painful.

2. **Upload runs after stitch completes** — the finished reel sits on disk doing nothing while a separate sequential upload sends it to R2. These two operations could overlap entirely.

Both are cloud-mode-only problems. Local mode is unaffected.

---

## Feature 1: ffmpeg Direct from R2 Presigned URLs

### Goal

Eliminate video file downloads entirely. Pass presigned R2 URLs directly to ffmpeg as input sources. ffmpeg/ffprobe both support HTTP(S) inputs natively and use range requests to seek — pulling only the bytes needed for each clip, not the full file.

### Flow (cloud mode, new)

```
/generate called
→ list all keys under session_key in R2
→ download only .txt transcript files to temp dir (tiny)
→ generate presigned URLs (2hr TTL) for video files that have selections
→ skip all other files entirely
→ run _run_generation with video_urls dict
→ ffmpeg receives presigned HTTPS URL as -i input
```

### Changes

**`generator_app.py` — `/generate` endpoint (cloud branch)**

Replace the current "download everything" loop:

```python
for key in storage.list_keys(session_key + "/"):
    filename = Path(key).name
    storage.download_file(key, os.path.join(tmp_session_dir, filename))
```

With a selective approach:
1. Separate keys into video keys and txt keys by extension
2. Identify which video filenames appear in `selections`
3. Download only the txt files whose corresponding video has selections
4. Call `storage.presigned_url(key)` for each selected video key → build `video_urls: dict[str, str]` mapping `filename → presigned_url`
5. Build synthetic `Path` objects: `Path(tmp_session_dir) / filename` — these point into the temp dir but the video file does not exist on disk. Only `.name` and `.with_suffix(".txt")` are used for lookups; both work correctly since txt files are downloaded.
6. Pass `video_paths` and `video_urls` directly to `_run_generation`, bypassing the internal `scan_videos` call

**`generator_app.py` — `_run_generation`**

Add optional parameter: `video_urls: dict[str, str] | None = None`

When `video_urls` is provided:
- In the plan phase, use `video_urls[vp.name]` as `video_path` instead of `str(vp)`
- For `get_video_duration(...)` and `get_video_dimensions(...)`, pass the presigned URL — ffprobe accepts HTTP inputs identically to file paths
- For `extract_clip(...)`, the `video_path` argument receives the presigned URL — ffmpeg handles it transparently

When `video_urls` is `None` (local mode), all existing code paths are unchanged.

### Presigned URL TTL

Generate with 2-hour TTL. Standard generation of a 4–8 segment reel on Render completes well under 10 minutes. 2 hours provides a large safety margin.

### Error handling

ffmpeg HTTP errors produce less readable messages ("invalid data found when processing input") compared to file-not-found errors. On extraction failure, log the filename (not the full presigned URL, which contains credentials) alongside the ffmpeg stderr output.

### Scope

- **In scope:** cloud mode `/generate` endpoint; `_run_generation` signature
- **Out of scope:** local mode (no changes), transcription flow, `/load-folder`

---

## Feature 2: Streaming Upload

### Goal

Overlap the stitch and R2 upload phases. Instead of stitch → upload sequentially, pipe ffmpeg's stitch output to both the local file and S3 simultaneously. Upload completes at the same time as the stitch.

### Flow (cloud mode, new)

```
Phase 3 — Assemble:
  ffmpeg concat → stdout pipe → read loop → local file on disk
                                           → S3 multipart upload (concurrent)
  proc.wait() → both destinations already fully written
```

### Changes

**`video_editor.py` — `stitch_clips`**

Add optional `stream: bool = False` parameter.

When `stream=False` (default, all existing callers): behaviour unchanged.

When `stream=True`:
- Use `pipe:1` as the ffmpeg output target instead of `output_path`
- Add `-movflags frag_keyframe+empty_moov -f mp4` to the command
- Return the `subprocess.Popen` object (not `subprocess.run`) so the caller controls stdout reading

**Why fragmented MP4:** Regular MP4 writes the `moov` atom at the end of the file, which requires seeking backwards — impossible on a pipe. `-movflags frag_keyframe+empty_moov` produces a fragmented MP4 that writes all data forward-only. Fragmented MP4 is supported by all modern browsers and plays correctly in the existing video player.

**`storage.py` — new function `upload_stream(key, stream)`**

Runs an S3 multipart upload from a readable byte stream:
1. `create_multipart_upload` to obtain an upload ID
2. Read 5MB chunks from stream, `upload_part` each chunk (S3 minimum part size is 5MB except the last)
3. On end-of-stream, flush any remaining bytes as the final part (may be <5MB — permitted)
4. `complete_multipart_upload`
5. On any exception: `abort_multipart_upload` to avoid orphaned S3 parts, then re-raise

**`generator_app.py` — `_run_generation`, Phase 3 (cloud mode only)**

Replace the current:
```python
stitch_clips(clip_paths, output_path)
storage.upload_file(output_path, reel_s3_key)
```

With:
1. Call `stitch_clips(clip_paths, output_path, stream=True)` → returns a `Popen` object
2. Start a daemon thread to drain ffmpeg stderr (required — if stderr fills its pipe buffer, ffmpeg blocks and the whole process deadlocks)
3. Open `output_path` for binary write
4. Run tee-read loop: read 64KB chunks from `proc.stdout`, write each chunk to local file and feed to `upload_stream`
5. After stdout is exhausted: call `proc.wait()`, check returncode, log stderr on failure
6. Presigned download URL is generated from the already-completed upload

Local mode uses `stitch_clips(clip_paths, output_path)` (no `stream` argument) — no change.

### Failure handling

If the multipart upload fails mid-stream:
- Abort the multipart upload (prevents orphaned S3 parts, which cost money)
- The local file was written in parallel and is complete — `/video/<job_id>` can still serve it
- Log the error; job continues to completion without a cloud download URL
- The existing fallback path in `/video/<job_id>` (serve from disk) handles this gracefully

### Scope

- **In scope:** `stitch_clips` in `video_editor.py`; `storage.py` (new function); `_run_generation` Phase 3
- **Out of scope:** title card generation, clip extraction, local mode

---

## Implementation Order

1. Feature 1 first — it's the larger win and self-contained to `generator_app.py`
2. Feature 2 second — touches three files but each change is bounded

## Testing

Both features are cloud-mode-only. Tests should:
- Mock `storage.presigned_url` and `storage.list_keys` for Feature 1
- Assert that `storage.download_file` is **not** called for video files in Feature 1
- Mock `storage.upload_stream` for Feature 2 and assert it is called instead of `storage.upload_file` during stitch
- Verify local mode tests continue to pass unchanged
