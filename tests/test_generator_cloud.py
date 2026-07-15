"""Tests for generator_app.py cloud mode: S3 download/upload flow."""
import os
import io
from unittest.mock import patch, MagicMock, call
from pathlib import Path
import pytest


@pytest.fixture
def cloud_client(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY", "key")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:9000")
    import importlib, storage, generator_app
    importlib.reload(storage)
    importlib.reload(generator_app)
    app = generator_app.create_app(testing=True)
    with app.test_client() as c:
        yield c


def test_load_library_uses_storage_in_cloud_mode(monkeypatch, tmp_path):
    """_load_library in generator_app reads from storage.read_json in cloud mode."""
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY", "key")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    import importlib, storage, generator_app
    importlib.reload(storage)
    importlib.reload(generator_app)

    fake_entries = [{"id": "1", "filename": "reel.mp4"}]
    with patch("generator_app.storage.read_json", return_value=fake_entries) as mock_rj:
        result = generator_app._load_library()
    mock_rj.assert_called_once_with(storage.library_key())
    assert result == fake_entries


def test_save_library_uses_storage_in_cloud_mode(monkeypatch):
    """_save_library in generator_app writes via storage.write_json in cloud mode."""
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY", "key")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    import importlib, storage, generator_app
    importlib.reload(storage)
    importlib.reload(generator_app)

    data = [{"id": "2", "filename": "reel2.mp4"}]
    with patch("generator_app.storage.write_json") as mock_wj:
        generator_app._save_library(data)
    mock_wj.assert_called_once_with(storage.library_key(), data)


def test_generate_endpoint_accepts_session_key_in_cloud_mode(cloud_client, tmp_path):
    """POST /generate in cloud mode accepts session_key, downloads only txt files, and uses presigned URLs."""
    session_key = "sessions/test123"
    txt_content = "[0:00] Speaker: Hello world."

    def fake_list_keys(prefix):
        return [f"{session_key}/video.mp4", f"{session_key}/video.txt"]

    def fake_download(key, local_path):
        if key.endswith(".txt"):
            Path(local_path).write_text(txt_content, encoding="utf-8")

    selections = {"video.mp4": ["[0:00] Speaker: Hello world."]}

    # stitch_clips_to_pipe returns a Popen-like object; its stdout is read by the tee loop
    mock_proc = MagicMock()
    mock_proc.stdout = io.BytesIO(b"fake mp4 data")
    mock_proc.stderr = io.BytesIO(b"")
    mock_proc.returncode = 0
    mock_proc._concat_list_path = str(tmp_path / "_concat.txt")
    Path(mock_proc._concat_list_path).touch()
    mock_proc.wait.return_value = None

    with patch("generator_app.storage.list_keys", side_effect=fake_list_keys), \
         patch("generator_app.storage.download_file", side_effect=fake_download), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.get_video_duration", return_value=None), \
         patch("generator_app.make_title_card"), \
         patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips_to_pipe", return_value=mock_proc), \
         patch("generator_app.storage.upload_stream"), \
         patch("generator_app.storage.presigned_url", return_value="https://s3/reel.mp4"), \
         patch("generator_app._library_add"):
        resp = cloud_client.post("/generate", json={
            "session_key": session_key,
            "mode": "checkbox",
            "selections": selections,
            "prompt": "test",
            "output_filename": "out.mp4",
        })

    assert resp.status_code == 200
    body = resp.get_json()
    assert "job_id" in body


def test_generate_cloud_uses_streaming_upload_not_upload_file(cloud_client, tmp_path):
    """In cloud mode, generation must use upload_stream for the reel, not upload_file."""
    session_key = "sessions/streaming_test"
    txt_content = "[0:00] Speaker: Hello world."

    def fake_list_keys(prefix):
        return [f"{session_key}/video.mp4", f"{session_key}/video.txt"]

    def fake_download(key, local_path):
        if key.endswith(".txt"):
            Path(local_path).write_text(txt_content, encoding="utf-8")

    mock_proc = MagicMock()
    mock_proc.stdout = io.BytesIO(b"fake mp4 data")
    mock_proc.stderr = io.BytesIO(b"")
    mock_proc.returncode = 0
    mock_proc._concat_list_path = str(tmp_path / "_concat2.txt")
    Path(mock_proc._concat_list_path).touch()
    mock_proc.wait.return_value = None

    mock_upload_stream = MagicMock()
    mock_upload_file = MagicMock()

    with patch("generator_app.storage.list_keys", side_effect=fake_list_keys), \
         patch("generator_app.storage.download_file", side_effect=fake_download), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.get_video_duration", return_value=None), \
         patch("generator_app.make_title_card"), \
         patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips_to_pipe", return_value=mock_proc), \
         patch("generator_app.storage.upload_stream", mock_upload_stream), \
         patch("generator_app.storage.upload_file", mock_upload_file), \
         patch("generator_app.storage.presigned_url", return_value="https://s3/reel.mp4"), \
         patch("generator_app._library_add"):
        cloud_client.post("/generate", json={
            "session_key": session_key,
            "mode": "checkbox",
            "selections": {"video.mp4": ["[0:00] Speaker: Hello world."]},
            "prompt": "test",
            "output_filename": "out.mp4",
        })

    mock_upload_stream.assert_called_once()
    mock_upload_file.assert_not_called()


def test_generate_cloud_upload_failure_does_not_hang(cloud_client, tmp_path):
    """If upload_stream raises, the job must complete (not deadlock on proc.wait)."""
    session_key = "sessions/upload_fail_test"
    txt_content = "[0:00] Speaker: Hello world."

    def fake_list_keys(prefix):
        return [f"{session_key}/video.mp4", f"{session_key}/video.txt"]

    def fake_download(key, local_path):
        if key.endswith(".txt"):
            Path(local_path).write_text(txt_content, encoding="utf-8")

    mock_proc = MagicMock()
    mock_proc.stdout = io.BytesIO(b"partial data")
    mock_proc.stderr = io.BytesIO(b"")
    mock_proc.returncode = 0
    mock_proc._concat_list_path = str(tmp_path / "_concat_fail.txt")
    Path(mock_proc._concat_list_path).touch()
    mock_proc.wait.return_value = None

    with patch("generator_app.storage.list_keys", side_effect=fake_list_keys), \
         patch("generator_app.storage.download_file", side_effect=fake_download), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.get_video_duration", return_value=None), \
         patch("generator_app.make_title_card"), \
         patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips_to_pipe", return_value=mock_proc), \
         patch("generator_app.storage.upload_stream", side_effect=OSError("S3 network failure")), \
         patch("generator_app.storage.presigned_url", return_value="https://s3/reel.mp4"), \
         patch("generator_app._library_add"):
        resp = cloud_client.post("/generate", json={
            "session_key": session_key,
            "mode": "checkbox",
            "selections": {"video.mp4": ["[0:00] Speaker: Hello world."]},
            "prompt": "test",
            "output_filename": "out.mp4",
        })

    # Request must return (not deadlock); proc.stdout.close() prevents pipe blockage
    assert resp.status_code == 200
    mock_proc.stdout.close()  # already called by code; verify no double-close crash
    mock_proc.wait.assert_called()


def test_run_generation_skips_scan_videos_when_paths_provided(tmp_path):
    """When video_paths is provided, _run_generation must not call scan_videos."""
    import importlib, generator_app
    importlib.reload(generator_app)

    (tmp_path / "video.txt").write_text("[0:00] Speaker: Hello world.", encoding="utf-8")
    vp = tmp_path / "video.mp4"
    job_id = generator_app._new_job("generation", 1)

    with patch("generator_app.scan_videos") as mock_scan, \
         patch("generator_app.get_video_duration", return_value=None), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.make_title_card"), \
         patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app._library_add"):
        generator_app._run_generation(
            job_id, str(tmp_path),
            {"video.mp4": ["[0:00] Speaker: Hello world."]},
            "test prompt", "out.mp4",
            video_paths=[vp],
            video_urls={"video.mp4": "https://r2.example.com/presigned/video.mp4"},
        )

    mock_scan.assert_not_called()


def test_library_video_redirects_to_presigned_url_in_cloud(cloud_client, tmp_path):
    """When the local reel file is gone, /library-video redirects to a presigned
    R2 GET URL with a forced media Content-Type instead of proxying the bytes
    through the host — proxying burns host bandwidth on every single view."""
    entry = {
        "id": "abc123",
        "filename": "my reel.mp4",
        "path": str(tmp_path / "gone.mp4"),   # does not exist → cloud fallback
        "reel_s3_key": "sessions/x/reel.mp4",
    }
    with patch("generator_app._load_library", return_value=[entry]), \
         patch("generator_app.storage.presigned_url",
               return_value="https://r2.example/reel.mp4?sig=1") as mock_ps:
        resp = cloud_client.get("/library-video/abc123", follow_redirects=False)

    assert resp.status_code in (302, 307)
    assert resp.headers["Location"] == "https://r2.example/reel.mp4?sig=1"
    # Must force a media Content-Type so Chrome's ORB permits the cross-origin load.
    _, kwargs = mock_ps.call_args
    assert kwargs.get("content_type") == "video/mp4"


def test_run_generation_marks_job_error_on_unexpected_exception(tmp_path):
    """Any unexpected exception mid-generation must drive the job to a terminal
    'error' state. If it were left 'running', the progress WebSocket would keep
    streaming frozen progress and the UI would sit at 'finalizing' forever."""
    import importlib, generator_app
    importlib.reload(generator_app)

    (tmp_path / "video.txt").write_text("[0:00] Speaker: Hello world.", encoding="utf-8")
    vp = tmp_path / "video.mp4"
    job_id = generator_app._new_job("generation", 1)

    # get_video_duration is called with no surrounding try/except; make it blow up.
    with patch("generator_app.get_video_duration", side_effect=RuntimeError("boom")):
        generator_app._run_generation(
            job_id, str(tmp_path),
            {"video.mp4": ["[0:00] Speaker: Hello world."]},
            "test prompt", "out.mp4",
            video_paths=[vp],
            video_urls={"video.mp4": "https://r2.example.com/presigned/video.mp4"},
        )

    job = generator_app._jobs[job_id]
    assert job["status"] == "error"
    assert "boom" in (job["error"] or "")


def test_generate_cloud_does_not_download_video_files(cloud_client, tmp_path):
    """In cloud mode, /generate must NOT call download_file for video files."""
    session_key = "sessions/test456"
    txt_content = "[0:00] Speaker: Hello world."

    def fake_list_keys(prefix):
        return [f"{session_key}/video.mp4", f"{session_key}/video.txt"]

    downloaded_keys = []

    def fake_download(key, local_path):
        downloaded_keys.append(key)
        if key.endswith(".txt"):
            Path(local_path).write_text(txt_content, encoding="utf-8")

    selections = {"video.mp4": ["[0:00] Speaker: Hello world."]}

    mock_proc = MagicMock()
    mock_proc.stdout = io.BytesIO(b"fake mp4 data")
    mock_proc.stderr = io.BytesIO(b"")
    mock_proc.returncode = 0
    mock_proc._concat_list_path = str(tmp_path / "_concat_dltest.txt")
    Path(mock_proc._concat_list_path).touch()
    mock_proc.wait.return_value = None

    with patch("generator_app.storage.list_keys", side_effect=fake_list_keys), \
         patch("generator_app.storage.download_file", side_effect=fake_download), \
         patch("generator_app.storage.presigned_url", return_value="https://r2.example.com/video.mp4"), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.get_video_duration", return_value=None), \
         patch("generator_app.make_title_card"), \
         patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips_to_pipe", return_value=mock_proc), \
         patch("generator_app.storage.upload_stream"), \
         patch("generator_app._library_add"):
        resp = cloud_client.post("/generate", json={
            "session_key": session_key,
            "mode": "checkbox",
            "selections": selections,
            "prompt": "test",
            "output_filename": "out.mp4",
        })

    assert resp.status_code == 200
    # No video file should have been downloaded
    assert not any(k.endswith(".mp4") for k in downloaded_keys), \
        f"Expected no .mp4 downloads, but got: {downloaded_keys}"
    # The txt file for the selected video should have been downloaded
    assert any(k.endswith(".txt") for k in downloaded_keys)


def test_generate_cloud_calls_presigned_url_for_selected_video(cloud_client, tmp_path):
    """In cloud mode, /generate must call storage.presigned_url for the selected video key."""
    session_key = "sessions/test789"
    txt_content = "[0:00] Speaker: Hello world."

    def fake_list_keys(prefix):
        return [f"{session_key}/video.mp4", f"{session_key}/video.txt"]

    def fake_download(key, local_path):
        if key.endswith(".txt"):
            Path(local_path).write_text(txt_content, encoding="utf-8")

    presigned_calls = []

    def fake_presigned(key, expires=3600):
        presigned_calls.append((key, expires))
        return f"https://r2.example.com/{key}"

    selections = {"video.mp4": ["[0:00] Speaker: Hello world."]}

    mock_proc = MagicMock()
    mock_proc.stdout = io.BytesIO(b"fake mp4 data")
    mock_proc.stderr = io.BytesIO(b"")
    mock_proc.returncode = 0
    mock_proc._concat_list_path = str(tmp_path / "_concat_presign.txt")
    Path(mock_proc._concat_list_path).touch()
    mock_proc.wait.return_value = None

    with patch("generator_app.storage.list_keys", side_effect=fake_list_keys), \
         patch("generator_app.storage.download_file", side_effect=fake_download), \
         patch("generator_app.storage.presigned_url", side_effect=fake_presigned), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.get_video_duration", return_value=None), \
         patch("generator_app.make_title_card"), \
         patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips_to_pipe", return_value=mock_proc), \
         patch("generator_app.storage.upload_stream"), \
         patch("generator_app._library_add"):
        resp = cloud_client.post("/generate", json={
            "session_key": session_key,
            "mode": "checkbox",
            "selections": selections,
            "prompt": "test",
            "output_filename": "out.mp4",
        })

    assert resp.status_code == 200
    # presigned_url must have been called for the video key with a 2hr TTL
    video_key_calls = [c for c in presigned_calls if c[0].endswith(".mp4") and "out.mp4" not in c[0]]
    assert len(video_key_calls) >= 1
    assert video_key_calls[0][1] == 7200, "Video input presigned URL must use 2-hour TTL"


def test_run_generation_passes_presigned_url_to_extract_clip(tmp_path):
    """When video_urls is provided, extract_clip must receive the presigned URL."""
    import importlib, generator_app
    importlib.reload(generator_app)

    (tmp_path / "video.txt").write_text("[0:00] Speaker: Hello world.", encoding="utf-8")
    vp = tmp_path / "video.mp4"
    presigned = "https://r2.example.com/presigned/video.mp4?token=abc"
    captured = []

    job_id = generator_app._new_job("generation", 1)

    with patch("generator_app.get_video_duration", return_value=None), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.make_title_card"), \
         patch("generator_app.extract_clip", side_effect=lambda vp, *a, **kw: captured.append(vp)), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app._library_add"):
        generator_app._run_generation(
            job_id, str(tmp_path),
            {"video.mp4": ["[0:00] Speaker: Hello world."]},
            "test prompt", "out.mp4",
            video_paths=[vp],
            video_urls={"video.mp4": presigned},
        )

    assert captured == [presigned]
