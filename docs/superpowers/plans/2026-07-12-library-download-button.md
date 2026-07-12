# Library Download Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a one-click download button to each library card that saves the reel `.mp4` in both local and cloud modes.

**Architecture:** Reuse the existing `/library-video/<entry_id>` endpoint, which serves the local file or redirects to a presigned R2 URL. Add a `?download=1` flag that flips `Content-Disposition` from `inline` to `attachment`. The frontend adds a download icon that hits that URL. No new endpoint, no storage-layer changes.

**Tech Stack:** Flask (`generator_app.py`), vanilla JS (`static/app.js`), pytest.

---

### Task 1: Backend — honor `?download=1` on `/library-video/<id>`

**Files:**
- Modify: `generator_app.py` (`serve_library_video`, ~line 1048)
- Test: `tests/test_generator_app.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_generator_app.py`:

```python
def test_library_video_download_flag_sets_attachment(client, tmp_path):
    """GET /library-video/<id>?download=1 serves the file as an attachment."""
    reel_file = tmp_path / "reel.mp4"
    reel_file.write_bytes(b"fake reel")
    entry = {
        "id": "dl-test-1",
        "filename": "reel.mp4",
        "path": str(reel_file),
        "source_folder": "test/",
        "prompt": "test",
        "duration_seconds": 10,
        "clip_count": 1,
        "segment_starts": [],
        "created_at": "2026-07-12T00:00:00",
    }
    with patch("generator_app._load_library", return_value=[entry]):
        with_flag = client.get("/library-video/dl-test-1?download=1")
        without_flag = client.get("/library-video/dl-test-1")
    assert with_flag.status_code == 200
    assert with_flag.headers["Content-Disposition"].startswith("attachment")
    assert "reel.mp4" in with_flag.headers["Content-Disposition"]
    # Without the flag it must NOT be an attachment (guards the play-vs-download split)
    assert not without_flag.headers.get("Content-Disposition", "").startswith("attachment")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py::test_library_video_download_flag_sets_attachment -v`
Expected: FAIL — `Content-Disposition` is absent or `inline` when the flag is set.

- [ ] **Step 3: Write minimal implementation**

In `generator_app.py`, `serve_library_video`. Read the flag at the top of the function (after the entry lookup) and thread it into both serve paths.

Add after the `entry` is resolved (just before the local-file `send_file`):

```python
        download = request.args.get("download") == "1"
```

Change the local-file branch from:

```python
        if path.is_file():
            return send_file(str(path), conditional=True)
```

to:

```python
        if path.is_file():
            return send_file(
                str(path),
                conditional=True,
                as_attachment=download,
                download_name=entry.get("filename", "reel.mp4"),
            )
```

Change the cloud presigned branch's `content_disposition` from the hard-coded
`inline; ...` to pick `attachment` when `download` is set:

```python
        if storage.is_cloud() and entry.get("reel_s3_key"):
            try:
                disposition = "attachment" if download else "inline"
                url = storage.presigned_url(
                    entry["reel_s3_key"],
                    content_type="video/mp4",
                    content_disposition=(
                        f'{disposition}; filename="{entry.get("filename", "reel.mp4")}"'
                    ),
                )
                return redirect(url)
            except Exception as exc:
                return jsonify({"error": f"cloud fetch failed: {exc}"}), 502
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py::test_library_video_download_flag_sets_attachment -v`
Expected: PASS

- [ ] **Step 5: Run the full generator test file to confirm no regressions**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_generator_app.py -v`
Expected: all pass (existing `/library-video` playback behavior unchanged when no flag).

- [ ] **Step 6: Commit**

```bash
git add generator_app.py tests/test_generator_app.py
git commit -m "feat: support ?download=1 attachment disposition on /library-video"
```

---

### Task 2: Frontend — download icon on the card

**Files:**
- Modify: `static/app.js` (`_renderCardBody`, ~lines 1959-1976 and the event-listener block ~line 2074)

- [ ] **Step 1: Add the download button to the icon row**

In `_renderCardBody`, immediately before the `editBtn` is created (~line 1962), add a download button, and prepend it to `iconRow` so the order reads Download · Edit · Delete.

Insert before `const editBtn = ...`:

```javascript
  const downloadBtn = document.createElement('button');
  downloadBtn.className = 'reel-btn-icon';
  downloadBtn.title = 'Download';
  downloadBtn.setAttribute('aria-label', 'Download');
  downloadBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M12 4v10m0 0l-4-4m4 4l4-4M5 19h14" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
```

Change the append block from:

```javascript
  iconRow.appendChild(editBtn);
  iconRow.appendChild(deleteBtn);
```

to:

```javascript
  iconRow.appendChild(downloadBtn);
  iconRow.appendChild(editBtn);
  iconRow.appendChild(deleteBtn);
```

- [ ] **Step 2: Wire the click handler**

In the "Event listeners" block, next to the existing `deleteBtn`/`editBtn`
listeners (~line 2074), add:

```javascript
  downloadBtn.addEventListener('click', () => {
    const a = document.createElement('a');
    a.href = `${GENERATOR_URL}/library-video/${entry.id}?download=1`;
    a.download = entry.filename || 'reel.mp4';
    document.body.appendChild(a);
    a.click();
    a.remove();
  });
```

- [ ] **Step 3: Manual verification (dev server)**

Start the generator service and main app, open the Library tab, and confirm a
download icon sits left of Edit/Delete on each card and clicking it downloads
the `.mp4`. (Frontend has no unit-test harness; the backend attachment behavior
is covered by Task 1.)

- [ ] **Step 4: Commit**

```bash
git add static/app.js
git commit -m "feat: add download button to library cards"
```

---

## Notes

- `GENERATOR_URL`, `entry`, and `iconRow` are all already in scope inside `_renderCardBody` — no new imports or globals.
- The `a.download` attribute is best-effort (ignored cross-origin); the `?download=1` server flag is what guarantees the save.
- No new storage functions: `storage.presigned_url` already accepts `content_disposition`.
