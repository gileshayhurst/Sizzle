# Faster-Whisper Transcription Speedup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `openai-whisper` transcription engine with `faster-whisper` (base/int8) and parallelize transcription across videos, tuned adaptively to the host CPU count.

**Architecture:** A thin adapter in `transcriber.py` converts faster-whisper `Segment` objects into the dict shape the existing (unchanged) `_split_into_sentences` already consumes. `app.py` constructs a shared `WhisperModel` keyed by its thread config and runs the per-video transcription jobs through a `ThreadPoolExecutor` whose worker count and per-job CPU threads are computed from `os.cpu_count()`. Cancellation stays responsive via a `wait(timeout=0.5)` polling loop.

**Tech Stack:** Python, faster-whisper (CTranslate2), Flask, `concurrent.futures`, pytest.

**Design doc:** `docs/superpowers/specs/2026-07-06-faster-whisper-transcription-design.md`

---

## File Map

| File | Change |
|------|--------|
| `requirements.txt` | Add `faster-whisper`. |
| `transcriber.py` | Rewrite `transcribe_video` to use faster-whisper's `(segments, info)` API; add `_segment_to_dict` adapter. `_split_into_sentences` / `_seconds_to_timestamp` unchanged. |
| `tests/test_transcriber.py` | Rewrite the 5 `transcribe_video` tests to mock the faster-whisper model; add adapter test. The 9 `_split_into_sentences` tests stay unchanged. |
| `app.py` | Add `_compute_transcription_parallelism` helper + `_WHISPER_CACHE_DIR`; change `_get_whisper_model` to a keyed cache building a `WhisperModel`; parallelize `_transcribe`. |
| `tests/test_app.py` | Add parallelism-helper tests. Existing transcription-cancel test must still pass. |
| `sizzle.py` | Construct a `WhisperModel` instead of `whisper.load_model("base")`. |

---

## Task 1: Add the faster-whisper dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add `faster-whisper` to `requirements.txt`**

Change the file to:

```
anthropic
pytest
flask>=2.0
flask-cors
flask-sock
boto3
faster-whisper
```

- [ ] **Step 2: Install it into the venv**

Run: `.\venv\Scripts\python.exe -m pip install faster-whisper`
Expected: installs `faster-whisper` and its `ctranslate2` / `tokenizers` deps, ending with `Successfully installed ...`.

- [ ] **Step 3: Verify the import works**

Run: `.\venv\Scripts\python.exe -c "from faster_whisper import WhisperModel; print('ok')"`
Expected: prints `ok` with no traceback.

- [ ] **Step 4: Commit**

```
git add requirements.txt
git commit -m "build: add faster-whisper dependency"
```

---

## Task 2: Rewrite `transcribe_video` with a faster-whisper adapter

**Files:**
- Modify: `transcriber.py`
- Test: `tests/test_transcriber.py`

faster-whisper's API differs from openai-whisper:
- `model.transcribe(path, word_timestamps=True)` returns a 2-tuple `(segments, info)` where `segments` is a **generator** of `Segment` objects.
- Each `Segment` has attributes `.start`, `.end`, `.text`, `.words`. `.words` is either `None` or a list of `Word` objects with `.word`, `.start`, `.end`.

The adapter converts each `Segment` into the dict `{"start", "text", "words": [{"word","start","end"}, ...]}` that `_split_into_sentences` already expects (with `words == []` when there are no word timestamps, preserving the existing segment-level fallback).

- [ ] **Step 1: Rewrite the `transcribe_video` tests**

Replace the section of `tests/test_transcriber.py` from the `_make_mock_model` helper (line ~25) through `test_loads_base_model` (line ~60) with the following. Leave everything from `# --- _split_into_sentences ---` onward **unchanged**.

```python
from types import SimpleNamespace


def _word(word: str, start: float, end: float):
    return SimpleNamespace(word=word, start=start, end=end)


def _segment(start: float, text: str, words=None):
    return SimpleNamespace(start=start, end=start + 1.0, text=text, words=words)


def _make_mock_model(segments):
    """Mock a faster-whisper WhisperModel: .transcribe() returns (segments_gen, info)."""
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (iter(segments), SimpleNamespace(language="en"))
    return mock_model


def test_formats_single_segment():
    model = _make_mock_model([_segment(5.0, "Hello there")])
    result = transcribe_video("video.mp4", model=model)
    assert result == "[0:05] Speaker: Hello there"


def test_formats_multiple_segments():
    model = _make_mock_model([
        _segment(5.0, "Hello there"),
        _segment(65.0, "And then she said"),
    ])
    result = transcribe_video("video.mp4", model=model)
    assert result == "[0:05] Speaker: Hello there\n[1:05] Speaker: And then she said"


def test_strips_whitespace_from_segment_text():
    model = _make_mock_model([_segment(0.0, "  padded  ")])
    result = transcribe_video("video.mp4", model=model)
    assert result == "[0:00] Speaker: padded"


def test_requests_word_timestamps():
    model = _make_mock_model([_segment(0.0, "Test")])
    transcribe_video("video.mp4", model=model)
    _, kwargs = model.transcribe.call_args
    assert kwargs.get("word_timestamps") is True


def test_builds_base_model_when_none_provided():
    fake_model = _make_mock_model([_segment(0.0, "Test")])
    with patch("faster_whisper.WhisperModel", return_value=fake_model) as mock_ctor:
        transcribe_video("video.mp4")
    mock_ctor.assert_called_once()
    assert mock_ctor.call_args[0][0] == "base"


def test_segment_to_dict_maps_word_objects():
    from transcriber import _segment_to_dict
    seg = _segment(2.0, "Hi there.", words=[_word("Hi", 2.0, 2.3), _word(" there.", 2.3, 2.9)])
    d = _segment_to_dict(seg)
    assert d == {
        "start": 2.0,
        "text": "Hi there.",
        "words": [
            {"word": "Hi", "start": 2.0, "end": 2.3},
            {"word": " there.", "start": 2.3, "end": 2.9},
        ],
    }


def test_segment_to_dict_handles_no_words():
    from transcriber import _segment_to_dict
    seg = _segment(1.0, "No words", words=None)
    d = _segment_to_dict(seg)
    assert d == {"start": 1.0, "text": "No words", "words": []}
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_transcriber.py -v`
Expected: the rewritten `transcribe_video` tests and the two `_segment_to_dict` tests FAIL (`_segment_to_dict` not defined; `transcribe_video` still unpacks a dict). The 9 `_split_into_sentences` tests still PASS.

- [ ] **Step 3: Rewrite `transcribe_video` and add the adapter in `transcriber.py`**

Replace the existing `transcribe_video` function (lines ~42-52) with:

```python
def _segment_to_dict(segment) -> dict:
    """Adapt a faster-whisper Segment object to the dict shape _split_into_sentences expects."""
    words = []
    if segment.words:
        words = [{"word": w.word, "start": w.start, "end": w.end} for w in segment.words]
    return {"start": segment.start, "text": segment.text, "words": words}


def transcribe_video(video_path: str, model=None) -> str:
    if model is None:
        from faster_whisper import WhisperModel
        model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(video_path, word_timestamps=True)
    lines = []
    for segment in segments:
        seg_dict = _segment_to_dict(segment)
        for start, text in _split_into_sentences(seg_dict):
            ts = _seconds_to_timestamp(start)
            lines.append(f"[{ts}] Speaker: {text}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_transcriber.py -v`
Expected: all tests PASS (rewritten `transcribe_video` tests, both adapter tests, and the unchanged 9 `_split_into_sentences` tests).

- [ ] **Step 5: Commit**

```
git add transcriber.py tests/test_transcriber.py
git commit -m "feat: swap transcriber to faster-whisper with Segment->dict adapter"
```

---

## Task 3: Add the adaptive parallelism helper

**Files:**
- Modify: `app.py`
- Test: `tests/test_app.py`

A pure function computes `(workers, cpu_threads)` from the CPU count and the number of videos, guaranteeing the pipeline never oversubscribes the CPU.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py` (near the other unit tests):

```python
import pytest as _pytest


@_pytest.mark.parametrize("cpu_count,num_videos", [
    (1, 1), (1, 3), (2, 1), (2, 3), (4, 1), (4, 2), (4, 3),
    (8, 1), (8, 5), (8, 20), (16, 3), (3, 3), (6, 4),
])
def test_compute_transcription_parallelism_invariants(cpu_count, num_videos):
    from app import _compute_transcription_parallelism
    workers, cpu_threads = _compute_transcription_parallelism(cpu_count, num_videos)
    assert workers >= 1
    assert cpu_threads >= 1
    assert workers <= num_videos
    assert workers * cpu_threads <= cpu_count


def test_compute_transcription_parallelism_single_core():
    from app import _compute_transcription_parallelism
    assert _compute_transcription_parallelism(1, 5) == (1, 1)


def test_compute_transcription_parallelism_uses_half_cores_as_worker_ceiling():
    from app import _compute_transcription_parallelism
    # 8 cores, plenty of videos -> ceiling of 4 workers, 2 threads each
    assert _compute_transcription_parallelism(8, 10) == (4, 2)


def test_compute_transcription_parallelism_caps_workers_at_video_count():
    from app import _compute_transcription_parallelism
    # 8 cores but only 2 videos -> 2 workers, 4 threads each
    assert _compute_transcription_parallelism(8, 2) == (2, 4)
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -k compute_transcription_parallelism -v`
Expected: FAIL — `_compute_transcription_parallelism` not defined.

- [ ] **Step 3: Implement the helper in `app.py`**

Add this function just above `_get_whisper_model` (around line 55):

```python
def _compute_transcription_parallelism(cpu_count: int, num_videos: int) -> tuple[int, int]:
    """Return (workers, cpu_threads) for parallel transcription.

    - workers: how many videos to transcribe concurrently. Capped at the video
      count and at half the cores, leaving room for each job's internal threads.
    - cpu_threads: CTranslate2 threads per job, dividing cores evenly across workers.

    Invariant: workers * cpu_threads <= cpu_count.
    """
    cpu_count = max(1, cpu_count)
    workers = min(num_videos, max(1, cpu_count // 2))
    cpu_threads = max(1, cpu_count // workers)
    return workers, cpu_threads
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py -k compute_transcription_parallelism -v`
Expected: all parametrized cases and the three explicit cases PASS.

- [ ] **Step 5: Commit**

```
git add app.py tests/test_app.py
git commit -m "feat: add adaptive transcription parallelism helper"
```

---

## Task 4: Build the faster-whisper model in `app.py` (keyed cache + disk cache)

**Files:**
- Modify: `app.py`

`_get_whisper_model` becomes a cache keyed by `(cpu_threads, num_workers)` so repeated jobs reuse a warm model, while different thread configs each get their own instance. A stable `download_root` avoids re-downloading weights on every cold boot.

- [ ] **Step 1: Replace the model globals and loader in `app.py`**

Replace the current globals (lines ~52-53):

```python
_whisper_model = None
_model_lock = threading.Lock()
```

with:

```python
_whisper_models: dict = {}
_model_lock = threading.Lock()
_WHISPER_CACHE_DIR = os.environ.get(
    "WHISPER_CACHE_DIR", str(Path(__file__).parent / ".whisper_cache")
)
```

Then replace the entire `_get_whisper_model` function (lines ~56-63):

```python
def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        with _model_lock:
            if _whisper_model is None:
                import whisper as _whisper
                _whisper_model = _whisper.load_model("base")
    return _whisper_model
```

with:

```python
def _get_whisper_model(cpu_threads: int = 0, num_workers: int = 1):
    """Return a cached faster-whisper base model configured for the given thread layout.

    Cached by (cpu_threads, num_workers) so warm jobs reuse the model. compute_type
    int8 gives the CPU speedup; download_root pins weights so cold boots don't re-fetch.
    """
    key = (cpu_threads, num_workers)
    model = _whisper_models.get(key)
    if model is None:
        with _model_lock:
            model = _whisper_models.get(key)
            if model is None:
                from faster_whisper import WhisperModel
                model = WhisperModel(
                    "base",
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=cpu_threads,
                    num_workers=num_workers,
                    download_root=_WHISPER_CACHE_DIR,
                )
                _whisper_models[key] = model
    return model
```

Note: faster-whisper accepts `cpu_threads=0` to mean "let CTranslate2 decide," which is a safe default for the zero-arg case.

- [ ] **Step 2: Verify the app still imports**

Run: `.\venv\Scripts\python.exe -c "import app; print('ok')"`
Expected: prints `ok` with no traceback.

- [ ] **Step 3: Run the full suite to confirm nothing regressed**

Run: `.\venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all tests still pass. (The cancel test at `tests/test_app.py` patches `app._get_whisper_model`, so the new signature is irrelevant to it — the mock ignores args.)

- [ ] **Step 4: Ignore the local model cache dir in git**

Add this line to `.gitignore` (create the file if it does not exist):

```
.whisper_cache/
```

- [ ] **Step 5: Commit**

```
git add app.py .gitignore
git commit -m "feat: build faster-whisper model with keyed cache and pinned download_root"
```

---

## Task 5: Parallelize `_transcribe` in `app.py`

**Files:**
- Modify: `app.py` (the `_transcribe` inner function inside the `/load-folder` route)

Submit all videos at once through a `ThreadPoolExecutor`, sized by the parallelism helper, while keeping: responsive cancellation (checked every 0.5s), per-video progress (`done`), per-video `.txt` write + cloud upload, and per-video error isolation.

- [ ] **Step 1: Replace the `_transcribe` function body**

Replace the entire `_transcribe` definition (from `def _transcribe():` through the `threading.Thread(target=_transcribe, daemon=True).start()` line — roughly lines 480-524) with:

```python
        def _transcribe():
            cancel_event = _jobs[job_id]["cancel"]
            cpu_count = os.cpu_count() or 1
            workers, cpu_threads = _compute_transcription_parallelism(
                cpu_count, len(needs_transcription)
            )
            model = _get_whisper_model(cpu_threads, workers)
            _append_log(
                job_id,
                f"⟳ transcribing {len(needs_transcription)} video(s) "
                f"({workers} at a time)...",
            )

            def _do_one(vp):
                transcript = transcribe_video(str(vp), model=model)
                vp.with_suffix(".txt").write_text(transcript, encoding="utf-8")
                if storage.is_cloud():
                    for sk, td in _cloud_session_dirs.items():
                        if td == str(vp.parent):
                            storage.upload_file(
                                str(vp.with_suffix(".txt")),
                                f"{sk}/{vp.stem}.txt",
                            )
                            break

            executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
            futures = {executor.submit(_do_one, vp): vp for vp in needs_transcription}
            pending = set(futures)
            done_count = 0
            try:
                while pending:
                    if cancel_event.is_set():
                        executor.shutdown(wait=False, cancel_futures=True)
                        with _jobs_lock:
                            _jobs[job_id]["status"] = "cancelled"
                        _append_log(job_id, "✗ transcription cancelled")
                        return
                    just_done, pending = concurrent.futures.wait(
                        pending,
                        timeout=0.5,
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    for future in just_done:
                        vp = futures[future]
                        try:
                            future.result()
                            _append_log(job_id, f"✓ {vp.name} — done")
                        except Exception as exc:
                            _append_log(job_id, f"✗ {vp.name} — failed: {exc}")
                            with _jobs_lock:
                                _jobs[job_id]["error"] = f"{vp.name}: {exc}"
                        done_count += 1
                        with _jobs_lock:
                            _jobs[job_id]["done"] = done_count
            finally:
                executor.shutdown(wait=False)
            with _jobs_lock:
                _jobs[job_id]["status"] = "done"
                _jobs[job_id]["result"] = {"folder": folder, "files": filenames}

        threading.Thread(target=_transcribe, daemon=True).start()
        return jsonify({"job_id": job_id, "files": filenames, "folder": folder})
```

- [ ] **Step 2: Run the transcription-cancel test**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_app.py::test_transcription_cancel_mid_video_stops_within_one_second -v`
Expected: PASS — cancellation still resolves to `cancelled` within the 1.5s deadline (the `wait(timeout=0.5)` loop checks the cancel flag between polls).

- [ ] **Step 3: Run the full suite**

Run: `.\venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```
git add app.py
git commit -m "feat: parallelize transcription across videos with responsive cancel"
```

---

## Task 6: Update the `sizzle.py` CLI to build a faster-whisper model

**Files:**
- Modify: `sizzle.py`

- [ ] **Step 1: Replace the whisper import**

Change line 7 of `sizzle.py`:

```python
# OLD:
import whisper

# NEW:
from faster_whisper import WhisperModel
```

- [ ] **Step 2: Replace the model construction**

Change the model-loading line (around line 45):

```python
# OLD:
    print("Loading Whisper model...", file=sys.stderr)
    whisper_model = whisper.load_model("base")

# NEW:
    print("Loading Whisper model...", file=sys.stderr)
    whisper_model = WhisperModel(
        "base",
        device="cpu",
        compute_type="int8",
        cpu_threads=os.cpu_count() or 1,
        num_workers=1,
    )
```

(`os` is already imported at the top of `sizzle.py`.)

- [ ] **Step 3: Verify the CLI imports cleanly**

Run: `.\venv\Scripts\python.exe -c "import sizzle; print('ok')"`
Expected: prints `ok` with no traceback.

- [ ] **Step 4: Run the full suite one final time**

Run: `.\venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```
git add sizzle.py
git commit -m "feat: build faster-whisper model in sizzle CLI"
```

---

## Self-Review Checklist (completed before handoff)

- [x] **Spec coverage:** Engine swap → Tasks 1, 2, 6. Adaptive parallelism → Tasks 3, 5. Model construction (int8, single shared instance, num_workers) → Task 4. Cold-start caching (download_root) → Task 4. Error handling (materialize segments in scope, per-video isolation) → Tasks 2 + 5. Testing (adapter, parallelism math, rewritten transcribe tests, unchanged split tests) → Tasks 2, 3.
- [x] **No placeholders:** every code step shows the full before/after.
- [x] **Type consistency:** `_segment_to_dict` (Task 2) produces the exact dict `_split_into_sentences` consumes. `_compute_transcription_parallelism` returns `(workers, cpu_threads)` (Task 3), consumed in that order in Task 5. `_get_whisper_model(cpu_threads, num_workers)` signature (Task 4) matches the call in Task 5. The existing `app._get_whisper_model` patch target name is preserved so `test_transcription_cancel_...` keeps working.
- [x] **Cancellation preserved:** Task 5 uses `wait(timeout=0.5)` polling so the existing ~1s cancel test still passes.
