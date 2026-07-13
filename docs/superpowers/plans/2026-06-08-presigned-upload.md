# Presigned Direct-to-R2 Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current single-POST `/upload` endpoint (which sends all video bytes through Render, hitting its request body size limit) with a presigned direct-to-R2 upload flow where the browser uploads each file straight to Cloudflare R2, bypassing Render entirely.

**Architecture:** The frontend requests a session key and one presigned PUT URL per file from a new `/upload/prepare` endpoint. The browser then PUTs each file directly to R2 (in parallel) using the presigned URLs. Once all uploads complete, the frontend calls a new `/upload/commit` endpoint (no body — just the session key) so the server records the session. Local mode (`APP_MODE=local`) is unchanged — it still uses the existing `/upload` path which saves files to disk directly.

**Tech Stack:** Python/Flask (server), boto3 `generate_presigned_url` with `put_object` op (S3 presigned PUT), vanilla JS `fetch` with `PUT` method (client uploads), Cloudflare R2 CORS policy required on the bucket.

---

## File Map

| File | Change |
|------|--------|
| `storage.py` | Add `presigned_put_url(key, expires)` function |
| `app.py` | Add `POST /upload/prepare` and `POST /upload/commit` endpoints; keep existing `POST /upload` for local mode |
| `static/app.js` | Replace `doUpload()` in `initCloudMode` with presigned upload flow; add per-file progress |
| `tests/test_upload_endpoint.py` | Add tests for `/upload/prepare` and `/upload/commit` |
| `tests/test_storage.py` | Add test for `presigned_put_url` |

---

## Task 1: Add `presigned_put_url` to storage.py

**Files:**
- Modify: `storage.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing test**

Open `tests/test_storage.py` and add at the bottom:

```python
def test_presigned_put_url_raises_in_local_mode(monkeypatch):
    monkeypatch.delenv("APP_MODE", raising=False)
    with pytest.raises(RuntimeError, match="only available in cloud mode"):
        storage.presigned_put_url("sessions/abc/video.mp4")


def test_presigned_put_url_calls_s3_in_cloud_mode(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY", "key")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    mock_client = MagicMock()
    mock_client.generate_presigned_url.return_value = "https://r2.example.com/put-url"
    with patch("storage._s3", return_value=mock_client):
        url = storage.presigned_put_url("sessions/abc/video.mp4", expires=300)
    mock_client.generate_presigned_url.assert_called_once_with(
        "put_object",
        Params={"Bucket": "test-bucket", "Key": "sessions/abc/video.mp4"},
        ExpiresIn=300,
    )
    assert url == "https://r2.example.com/put-url"
```

Make sure `from unittest.mock import MagicMock, patch` and `import storage` are at the top of the test file (they likely already are).

- [ ] **Step 2: Run tests to verify they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_storage.py::test_presigned_put_url_raises_in_local_mode tests/test_storage.py::test_presigned_put_url_calls_s3_in_cloud_mode -v
```

Expected: FAIL — `AttributeError: module 'storage' has no attribute 'presigned_put_url'`

- [ ] **Step 3: Implement `presigned_put_url` in storage.py**

Add this function immediately after `presigned_url` (around line 151):

```python
def presigned_put_url(key: str, expires: int = 3600) -> str:
    """Generate a presigned PUT URL so the browser can upload a file directly to R2/S3.

    Raises RuntimeError when called in local mode — presigned URLs require S3.
    """
    if not is_cloud():
        raise RuntimeError("presigned_put_url is only available in cloud mode (APP_MODE=cloud)")
    return _s3().generate_presigned_url(
        "put_object",
        Params={"Bucket": _bucket(), "Key": key},
        ExpiresIn=expires,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```
.\venv\Scripts\python.exe -m pytest tests/test_storage.py::test_presigned_put_url_raises_in_local_mode tests/test_storage.py::test_presigned_put_url_calls_s3_in_cloud_mode -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite to check for regressions**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all previously-passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add storage.py tests/test_storage.py
git commit -m "feat: add presigned_put_url to storage module"
```

---

## Task 2: Add `/upload/prepare` and `/upload/commit` endpoints to app.py

**Files:**
- Modify: `app.py`
- Test: `tests/test_upload_endpoint.py`

These two endpoints replace the cloud path in `/upload`. The existing `/upload` endpoint is kept as-is (it's still used in local mode and by existing tests).

- [ ] **Step 1: Write the failing tests**

Open `tests/test_upload_endpoint.py`. Add these tests at the bottom:

```python
# ── /upload/prepare ──────────────────────────────────────────────────────────

def test_upload_prepare_returns_presigned_urls(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    with patch("storage.new_session_key", return_value="sessions/testsession"), \
         patch("storage.presigned_put_url", side_effect=lambda key, expires=3600: f"https://r2.example.com/{key}"):
        resp = client.post("/upload/prepare", json={
            "files": ["clip1.mp4", "clip1.txt", "clip2.mov"]
        })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["session_key"] == "sessions/testsession"
    assert data["folder"] == "sessions/testsession"
    # One presigned URL per file
    assert len(data["uploads"]) == 3
    assert data["uploads"][0]["filename"] == "clip1.mp4"
    assert "url" in data["uploads"][0]
    assert "key" in data["uploads"][0]


def test_upload_prepare_rejects_unsupported_extension(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    resp = client.post("/upload/prepare", json={"files": ["video.mp4", "readme.pdf"]})
    assert resp.status_code == 400
    assert "Unsupported" in resp.get_json()["error"]


def test_upload_prepare_requires_at_least_one_video(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    resp = client.post("/upload/prepare", json={"files": ["transcript.txt"]})
    assert resp.status_code == 400
    assert "video" in resp.get_json()["error"].lower()


def test_upload_prepare_requires_files_list(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    resp = client.post("/upload/prepare", json={})
    assert resp.status_code == 400


# ── /upload/commit ───────────────────────────────────────────────────────────

def test_upload_commit_returns_folder(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    resp = client.post("/upload/commit", json={
        "session_key": "sessions/testsession",
        "files": ["clip1.mp4", "clip1.txt"]
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["folder"] == "sessions/testsession"
    assert data["session_key"] == "sessions/testsession"


def test_upload_commit_rejects_missing_session_key(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    resp = client.post("/upload/commit", json={"files": ["clip1.mp4"]})
    assert resp.status_code == 400
```

The `client` fixture is already defined earlier in this test file (it creates a Flask test client from `create_app(testing=True)`). Check the top of the file and confirm — if the fixture is named differently, use its actual name.

- [ ] **Step 2: Run tests to verify they fail**

```
.\venv\Scripts\python.exe -m pytest tests/test_upload_endpoint.py::test_upload_prepare_returns_presigned_urls tests/test_upload_endpoint.py::test_upload_commit_returns_folder -v
```

Expected: FAIL — 404 (routes don't exist yet)

- [ ] **Step 3: Implement the two endpoints in app.py**

Inside `create_app()`, after the existing `@app.post("/upload")` block (around line 308), add:

```python
    @app.post("/upload/prepare")
    def upload_prepare():
        """Cloud-mode: validate filenames and return presigned S3 PUT URLs.

        The browser calls this first, uploads files directly to R2, then calls
        /upload/commit. No video bytes pass through this server.

        Request JSON: {"files": ["video1.mp4", "transcript1.txt", ...]}
        Response JSON: {
            "session_key": "sessions/<uuid>",
            "folder": "sessions/<uuid>",
            "uploads": [{"filename": "video1.mp4", "key": "sessions/<uuid>/video1.mp4", "url": "<presigned PUT URL>"}, ...]
        }
        """
        if not storage.is_cloud():
            return jsonify({"error": "This endpoint is only available in cloud mode"}), 400

        body = request.get_json(silent=True) or {}
        filenames = body.get("files", [])
        if not filenames:
            return jsonify({"error": "No files provided"}), 400

        has_video = False
        for name in filenames:
            ext = Path(name).suffix.lower()
            if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
                return jsonify({"error": f"Unsupported file type: {name}. Upload videos (.mp4 .mov .avi .mkv .webm) and/or transcripts (.txt)."}), 400
            if ext in _VIDEO_EXTENSIONS:
                has_video = True
        if not has_video:
            return jsonify({"error": "At least one video file is required."}), 400

        session_key = storage.new_session_key()
        uploads = []
        for name in filenames:
            safe_name = Path(name).name  # strip any path components
            key = f"{session_key}/{safe_name}"
            url = storage.presigned_put_url(key, expires=7200)  # 2hr window for large files
            uploads.append({"filename": safe_name, "key": key, "url": url})

        return jsonify({
            "session_key": session_key,
            "folder": session_key,
            "uploads": uploads,
        })

    @app.post("/upload/commit")
    def upload_commit():
        """Cloud-mode: acknowledge that the browser finished uploading to R2.

        Called after all presigned PUT uploads complete. Server just validates
        the request and echoes back the session info — no file I/O needed here
        since files are already in R2.

        Request JSON: {"session_key": "sessions/<uuid>", "files": ["video1.mp4", ...]}
        Response JSON: {"session_key": "sessions/<uuid>", "folder": "sessions/<uuid>", "files": [...]}
        """
        if not storage.is_cloud():
            return jsonify({"error": "This endpoint is only available in cloud mode"}), 400

        body = request.get_json(silent=True) or {}
        session_key = body.get("session_key")
        if not session_key:
            return jsonify({"error": "session_key is required"}), 400

        files = body.get("files", [])
        return jsonify({
            "session_key": session_key,
            "folder": session_key,
            "files": files,
        })
```

- [ ] **Step 4: Run the new tests**

```
.\venv\Scripts\python.exe -m pytest tests/test_upload_endpoint.py -v
```

Expected: all tests in this file PASS.

- [ ] **Step 5: Run full test suite**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_upload_endpoint.py
git commit -m "feat: add /upload/prepare and /upload/commit endpoints for presigned R2 uploads"
```

---

## Task 3: Replace `doUpload()` in app.js with presigned upload flow

**Files:**
- Modify: `static/app.js`

No backend tests needed here — this is pure frontend logic. Manual verification is in Task 4.

The current `doUpload()` POSTs all files to `/upload` as a single multipart form. Replace it with:
1. POST `/upload/prepare` with just the filenames (tiny request)
2. PUT each file directly to its presigned R2 URL (browser → R2, no Render involved)
3. POST `/upload/commit` with the session key (tiny request)
4. Call `openFolder(data.folder)` as before

- [ ] **Step 1: Locate `doUpload` in app.js**

Find the `async function doUpload()` block inside `initCloudMode` (around line 968). It currently reads:

```js
async function doUpload() {
  if (!selectedFiles.length) { ... }
  ...
  const formData = new FormData();
  selectedFiles.forEach(f => formData.append('files', f));
  try {
    const resp = await fetch('/upload', { method: 'POST', body: formData });
    const data = await resp.json();
    if (!resp.ok) { ... }
    _saveRecentSession(...);
    openFolder(data.folder);
  } catch (err) { ... }
  finally { ... }
}
```

- [ ] **Step 2: Replace `doUpload` with the presigned version**

Replace the entire `async function doUpload() { ... }` block (from `async function doUpload()` through its closing `}`) with:

```js
  async function doUpload() {
    if (!selectedFiles.length) {
      folderErr.textContent = 'Select a folder or files first.';
      folderErr.classList.remove('hidden');
      return;
    }
    folderErr.classList.add('hidden');
    uploadErr.classList.add('hidden');
    btnLoad.disabled = true;

    try {
      // Step 1: ask server for presigned PUT URLs (sends only filenames, no bytes)
      btnLoad.textContent = 'Preparing upload…';
      const prepResp = await fetch('/upload/prepare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ files: selectedFiles.map(f => f.name) }),
      });
      const prepData = await prepResp.json();
      if (!prepResp.ok) {
        folderErr.textContent = prepData.error || 'Upload preparation failed';
        folderErr.classList.remove('hidden');
        return;
      }

      // Step 2: upload each file directly to R2 using its presigned PUT URL
      const uploads = prepData.uploads; // [{filename, key, url}]
      let done = 0;
      btnLoad.textContent = `Uploading 0 / ${uploads.length}…`;

      await Promise.all(uploads.map(async ({ filename, url }) => {
        const file = selectedFiles.find(f => f.name === filename);
        const putResp = await fetch(url, {
          method: 'PUT',
          body: file,
          headers: { 'Content-Type': file.type || 'application/octet-stream' },
        });
        if (!putResp.ok) {
          throw new Error(`Failed to upload ${filename} (${putResp.status})`);
        }
        done++;
        btnLoad.textContent = `Uploading ${done} / ${uploads.length}…`;
      }));

      // Step 3: tell the server all uploads are done
      btnLoad.textContent = 'Finalising…';
      const commitResp = await fetch('/upload/commit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_key: prepData.session_key,
          files: selectedFiles.map(f => f.name),
        }),
      });
      const commitData = await commitResp.json();
      if (!commitResp.ok) {
        folderErr.textContent = commitData.error || 'Upload commit failed';
        folderErr.classList.remove('hidden');
        return;
      }

      // Step 4: record in recent sessions and open the folder as before
      _saveRecentSession(
        selectedFolderName,
        selectedFiles.filter(f => ext(f.name) !== '.txt').length,
        commitData.folder
      );
      openFolder(commitData.folder);

    } catch (err) {
      folderErr.textContent = 'Upload error: ' + err.message;
      folderErr.classList.remove('hidden');
    } finally {
      btnLoad.disabled = false;
      btnLoad.textContent = 'Upload & Transcribe';
    }
  }
```

- [ ] **Step 3: Commit**

```bash
git add static/app.js
git commit -m "feat: use presigned direct-to-R2 upload in cloud mode (bypass Render size limit)"
```

---

## Task 4: Configure R2 CORS policy

**Files:**
- No code changes — this is a one-time Cloudflare R2 dashboard configuration

Without CORS, the browser will get a CORS error when it tries to PUT to R2 from your app's domain.

- [ ] **Step 1: Log in to Cloudflare dashboard → R2 → your bucket → Settings → CORS Policy**

- [ ] **Step 2: Add this CORS rule** (replace `https://sizzle-app.onrender.com` with your actual app URL, or use `*` during testing):

```json
[
  {
    "AllowedOrigins": ["https://sizzle-app.onrender.com"],
    "AllowedMethods": ["PUT"],
    "AllowedHeaders": ["Content-Type"],
    "MaxAgeSeconds": 3600
  }
]
```

If you want to allow local development too:

```json
[
  {
    "AllowedOrigins": ["https://sizzle-app.onrender.com", "http://localhost:5000"],
    "AllowedMethods": ["PUT"],
    "AllowedHeaders": ["Content-Type"],
    "MaxAgeSeconds": 3600
  }
]
```

- [ ] **Step 3: Save the policy**

---

## Task 5: End-to-end verification on Render

- [ ] **Step 1: Deploy** — push the branch to GitHub and let Render redeploy both services (or trigger manual deploy from Render dashboard).

- [ ] **Step 2: Open the deployed app** and select the NOBU folder (724MB, 13 videos + transcripts).

- [ ] **Step 3: Click "Upload & Transcribe"** — you should see the button cycle through "Preparing upload…" → "Uploading N / 13…" → "Finalising…" → workspace screen.

- [ ] **Step 4: Confirm the workspace loads** with all video files listed in the sidebar and their transcripts visible.

- [ ] **Step 5: Run a quick Analyze** with a short prompt to confirm the cloud pipeline is still working end-to-end.

---

## Self-Review Notes

- The existing `/upload` endpoint is untouched — local mode behaviour is identical to before.
- `_ALLOWED_UPLOAD_EXTENSIONS` and `_VIDEO_EXTENSIONS` are already defined in `create_app()` scope and are reused in `/upload/prepare` — no duplication.
- Presigned PUT URLs expire after 2 hours (`expires=7200`), giving plenty of headroom for slow connections uploading large folders.
- `Promise.all` uploads all files in parallel. For very large folders on slow connections this could saturate bandwidth — acceptable trade-off vs sequential complexity for now.
- The `/upload/commit` endpoint does no file-system work (files are already in R2). It exists to give the frontend a clean confirmation signal and to record the session key.
- CORS must be set on the R2 bucket before Task 5 or PUT requests will fail in the browser with a CORS error.
