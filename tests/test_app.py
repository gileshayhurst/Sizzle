import os
from unittest.mock import patch, MagicMock
from pathlib import Path

import pytest
from app import create_app


@pytest.fixture
def client():
    app = create_app(testing=True)
    with app.test_client() as c:
        yield c


def test_index_returns_200(client):
    resp = client.get("/")
    assert resp.status_code == 200


def test_index_returns_html(client):
    resp = client.get("/")
    assert b"<!DOCTYPE html>" in resp.data or b"<html" in resp.data


def test_load_folder_returns_video_list(client, tmp_path):
    (tmp_path / "video1.mp4").touch()
    (tmp_path / "video2.mp4").touch()
    (tmp_path / "notes.txt").write_text("[0:01] Speaker: hi", encoding="utf-8")
    resp = client.post("/load-folder", json={"folder": str(tmp_path)})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "job_id" in data
    assert set(data["files"]) == {"video1.mp4", "video2.mp4"}


def test_load_folder_missing_folder_returns_404(client):
    resp = client.post("/load-folder", json={"folder": "/nonexistent/folder/xyz"})
    assert resp.status_code == 404


def test_load_folder_no_videos_returns_422(client, tmp_path):
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    resp = client.post("/load-folder", json={"folder": str(tmp_path)})
    assert resp.status_code == 422


def test_status_returns_job_state(client):
    # Manually inject a job
    from app import _jobs, _jobs_lock
    import threading
    job_id = "test-job-123"
    with _jobs_lock:
        _jobs[job_id] = {
            "type": "transcription",
            "status": "running",
            "total": 3,
            "done": 1,
            "log": ["✓ video1.mp4 — done"],
            "result": None,
            "error": None,
            "cancel": threading.Event(),
        }
    resp = client.get(f"/status/{job_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "running"
    assert data["done"] == 1
    assert data["total"] == 3
    assert "✓ video1.mp4 — done" in data["log"]


def test_status_unknown_job_returns_404(client):
    resp = client.get("/status/nonexistent-id")
    assert resp.status_code == 404


def test_cancel_job(client):
    from app import _jobs, _jobs_lock
    import threading
    job_id = "cancel-test-456"
    cancel_event = threading.Event()
    with _jobs_lock:
        _jobs[job_id] = {
            "type": "generation",
            "status": "running",
            "total": 2,
            "done": 0,
            "log": [],
            "result": None,
            "error": None,
            "cancel": cancel_event,
        }
    resp = client.delete(f"/jobs/{job_id}")
    assert resp.status_code == 200
    assert cancel_event.is_set()
    with _jobs_lock:
        assert _jobs[job_id]["status"] == "cancelled"


def test_group_by_minute_buckets_lines():
    from app import _group_by_minute
    lines = [
        {"timestamp": "0:05", "seconds": 5.0,  "minute_bucket": 0, "raw": "a", "text": "a"},
        {"timestamp": "0:50", "seconds": 50.0, "minute_bucket": 0, "raw": "b", "text": "b"},
        {"timestamp": "1:10", "seconds": 70.0, "minute_bucket": 1, "raw": "c", "text": "c"},
    ]
    groups = _group_by_minute(lines)
    assert len(groups) == 2
    assert groups[0]["label"] == "0:00 – 1:00"
    assert len(groups[0]["lines"]) == 2
    assert groups[1]["label"] == "1:00 – 2:00"
    assert len(groups[1]["lines"]) == 1


def test_transcripts_endpoint_returns_structured_data(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:05] Speaker: Hello world.\n[1:10] Speaker: Second line.",
        encoding="utf-8"
    )
    resp = client.get(f"/transcripts?folder={tmp_path}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["files"]) == 1
    f = data["files"][0]
    assert f["name"] == "vid.mp4"
    assert len(f["lines"]) == 2
    assert f["lines"][0]["timestamp"] == "0:05"
    assert f["lines"][0]["minute_bucket"] == 0
    assert f["lines"][1]["minute_bucket"] == 1


def test_generate_returns_job_id(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")

    with patch("app.extract_clip"), \
         patch("app.stitch_clips"), \
         patch("app.check_ffmpeg"), \
         patch("app._library_add"):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {"vid.mp4": ["[0:05] Speaker: Hello."]},
            "prompt": "greetings",
            "output_filename": "out.mp4",
        })
    assert resp.status_code == 200
    data = resp.get_json()
    assert "job_id" in data


def test_generate_accepts_empty_prompt(client, tmp_path):
    """prompt is now optional at generate time (stored for library only)."""
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")
    with patch("app.extract_clip"), \
         patch("app.stitch_clips"), \
         patch("app.check_ffmpeg"), \
         patch("app._library_add"):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {"vid.mp4": ["[0:05] Speaker: Hello."]},
            "output_filename": "out.mp4",
        })
    assert resp.status_code == 200
    assert "job_id" in resp.get_json()


def test_library_starts_empty(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.LIBRARY_PATH", tmp_path / "lib.json")
    resp = client.get("/library")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_library_delete_removes_entry(client, tmp_path, monkeypatch):
    import app as app_module
    import json
    lib_path = tmp_path / "lib.json"
    monkeypatch.setattr(app_module, "LIBRARY_PATH", lib_path)
    # Seed one entry
    lib_path.write_text(json.dumps([{"id": "abc123", "filename": "x.mp4"}]), encoding="utf-8")
    resp = client.delete("/library/abc123")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    remaining = json.loads(lib_path.read_text(encoding="utf-8"))
    assert remaining == []


def test_video_endpoint_not_found(client):
    resp = client.get("/video/nonexistent-job-id")
    assert resp.status_code == 404


def test_get_video_dimensions_returns_width_height():
    from app import get_video_dimensions
    with patch("app.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="1920,1080\n", returncode=0)
        w, h = get_video_dimensions("/fake/video.mp4")
    assert w == 1920
    assert h == 1080
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "ffprobe"
    assert "/fake/video.mp4" in cmd


def test_get_video_dimensions_falls_back_on_failure():
    from app import get_video_dimensions
    with patch("app.subprocess.run", side_effect=Exception("ffprobe missing")):
        w, h = get_video_dimensions("/fake/video.mp4")
    assert (w, h) == (1920, 1080)


def test_make_title_card_calls_ffmpeg_with_correct_args():
    from app import make_title_card
    with patch("app.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card("My Video", 1920, 1080, "/tmp/card.mp4", duration=5.0)
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    joined = " ".join(cmd)
    assert cmd[0] == "ffmpeg"
    assert "1920x1080" in joined
    assert "My Video" in joined
    assert "/tmp/card.mp4" in joined
    assert "5.0" in joined


def test_make_title_card_escapes_special_characters():
    from app import make_title_card
    with patch("app.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card("It's 50% Done: Really", 1280, 720, "/tmp/card.mp4")
    joined = " ".join(mock_run.call_args[0][0])
    assert "\\'" in joined        # apostrophe escaped
    assert "%%" in joined          # percent escaped
    assert "\\:" in joined         # colon escaped


def test_title_card_inserted_between_videos(client, tmp_path):
    """make_title_card is called once between two source videos."""
    import time

    (tmp_path / "alpha.mp4").touch()
    (tmp_path / "alpha.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")
    (tmp_path / "beta.mp4").touch()
    (tmp_path / "beta.txt").write_text("[0:10] Speaker: World.", encoding="utf-8")

    with patch("app.extract_clip"), \
         patch("app.stitch_clips"), \
         patch("app.check_ffmpeg"), \
         patch("app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("app.make_title_card") as mock_card, \
         patch("app._library_add"):

        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {
                "alpha.mp4": ["[0:05] Speaker: Hello."],
                "beta.mp4": ["[0:10] Speaker: World."],
            },
            "prompt": "",
            "output_filename": "out.mp4",
        })
        assert resp.status_code == 200
        job_id = resp.get_json()["job_id"]

        for _ in range(25):
            status = client.get(f"/status/{job_id}").get_json()["status"]
            if status in ("done", "error", "cancelled"):
                break
            time.sleep(0.2)

        assert status == "done", f"Job ended in unexpected state: {status}"

    # One video-name title card between alpha and beta
    assert mock_card.call_count == 1
    assert mock_card.call_args[0][0] == "beta"


def test_make_title_card_includes_fontfile_when_font_found():
    """fontfile= is injected into the drawtext filter when a system font is found."""
    from app import make_title_card
    with patch("app.subprocess.run") as mock_run, \
         patch("app._find_system_font", return_value="C:/Windows/Fonts/arial.ttf"):
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card("Test", 1920, 1080, "/tmp/card.mp4")
    joined = " ".join(mock_run.call_args[0][0])
    assert "fontfile=" in joined
    assert "arial.ttf" in joined


def test_make_title_card_omits_fontfile_when_none_found():
    """No fontfile= is added when _find_system_font returns None (non-Windows / no font)."""
    from app import make_title_card
    with patch("app.subprocess.run") as mock_run, \
         patch("app._find_system_font", return_value=None):
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card("Test", 1920, 1080, "/tmp/card.mp4")
    joined = " ".join(mock_run.call_args[0][0])
    assert "fontfile=" not in joined


def test_make_title_card_wraps_long_title():
    """Long title is split into multiple drawtext filters (one per line)."""
    from app import make_title_card
    # 640×352, fontsize=24, chars_per_line≈37 — this 55-char title must wrap
    long_name = "New York Japanese restaurant Nobu Downtown food reviews"
    with patch("app.subprocess.run") as mock_run, \
         patch("app._find_system_font", return_value=None):
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card(long_name, 640, 352, "/tmp/card.mp4")
    cmd = mock_run.call_args[0][0]
    vf_arg = cmd[cmd.index("-vf") + 1]
    assert vf_arg.count("drawtext=") > 1


def test_make_title_card_does_not_wrap_short_title():
    """Short titles that fit on one line use a single drawtext filter."""
    from app import make_title_card
    with patch("app.subprocess.run") as mock_run, \
         patch("app._find_system_font", return_value=None):
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card("Nobu", 1920, 1080, "/tmp/card.mp4")
    cmd = mock_run.call_args[0][0]
    vf_arg = cmd[cmd.index("-vf") + 1]
    assert vf_arg.count("drawtext=") == 1


def test_group_lines_into_segments_single_contiguous_block():
    from app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 5.0},
        {"raw": "b", "seconds": 10.0},
        {"raw": "c", "seconds": 15.0},
        {"raw": "d", "seconds": 20.0},  # unselected
    ]
    result = _group_lines_into_segments(lines, {"a", "b", "c"})
    # end = first unselected line (d) at 20.0
    assert result == [(5.0, 20.0)]


def test_group_lines_into_segments_two_clusters():
    from app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 5.0},
        {"raw": "b", "seconds": 10.0},  # unselected — splits the groups
        {"raw": "c", "seconds": 15.0},
        {"raw": "d", "seconds": 20.0},
    ]
    result = _group_lines_into_segments(lines, {"a", "c", "d"})
    # segment 1: a(5.0) ends at b(10.0)
    # segment 2: c,d — last line + 10 = 30.0
    assert result == [(5.0, 10.0), (15.0, 30.0)]


def test_group_lines_into_segments_all_selected():
    from app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 5.0},
        {"raw": "b", "seconds": 10.0},
    ]
    result = _group_lines_into_segments(lines, {"a", "b"})
    # No line after the group — end = last.seconds + 10
    assert result == [(5.0, 20.0)]


def test_group_lines_into_segments_none_selected():
    from app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 5.0},
        {"raw": "b", "seconds": 10.0},
    ]
    result = _group_lines_into_segments(lines, set())
    assert result == []


def test_load_folder_excludes_generated_reels(client, tmp_path, monkeypatch):
    """Videos that appear in the library are treated as generated output, not source."""
    import app as app_module
    import json as _json

    source = tmp_path / "source.mp4"
    reel = tmp_path / "NOBU_sizzle.mp4"
    source.touch()
    reel.touch()
    (tmp_path / "source.txt").write_text("[0:05] Speaker: Hi.", encoding="utf-8")

    lib_path = tmp_path / "lib.json"
    lib_path.write_text(_json.dumps([{
        "id": "abc", "filename": "NOBU_sizzle.mp4", "path": str(reel),
        "source_folder": "tmp/", "prompt": "", "duration_seconds": 10,
        "clip_count": 1, "created_at": "2026-01-01T00:00:00",
    }]), encoding="utf-8")
    monkeypatch.setattr(app_module, "LIBRARY_PATH", lib_path)

    resp = client.post("/load-folder", json={"folder": str(tmp_path)})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "NOBU_sizzle.mp4" not in data["files"]
    assert "source.mp4" in data["files"]


def test_transcripts_excludes_generated_reels(client, tmp_path, monkeypatch):
    """GET /transcripts filters out library entries so generated reels don't appear
    in the sidebar."""
    import app as app_module
    import json as _json

    source = tmp_path / "source.mp4"
    reel = tmp_path / "NOBU_sizzle.mp4"
    source.touch()
    reel.touch()
    (tmp_path / "source.txt").write_text("[0:05] Speaker: Hi.", encoding="utf-8")

    lib_path = tmp_path / "lib.json"
    lib_path.write_text(_json.dumps([{
        "id": "abc", "filename": "NOBU_sizzle.mp4", "path": str(reel),
        "source_folder": "tmp/", "prompt": "", "duration_seconds": 10,
        "clip_count": 1, "created_at": "2026-01-01T00:00:00",
    }]), encoding="utf-8")
    monkeypatch.setattr(app_module, "LIBRARY_PATH", lib_path)

    resp = client.get(f"/transcripts?folder={tmp_path}")
    assert resp.status_code == 200
    data = resp.get_json()
    names = [f["name"] for f in data["files"]]
    assert "NOBU_sizzle.mp4" not in names
    assert "source.mp4" in names


def test_analyze_returns_highlights(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:05] Speaker: Hello world.\n[0:15] Speaker: Black cod is amazing.",
        encoding="utf-8",
    )
    with patch("app.query_claude", return_value="0:05-0:20"):
        resp = client.post("/analyze", json={"folder": str(tmp_path), "prompt": "food"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "highlights" in data
    assert "vid.mp4" in data["highlights"]
    # both lines fall within 0:05-0:20
    assert len(data["highlights"]["vid.mp4"]) == 2


def test_analyze_missing_prompt_returns_400(client, tmp_path):
    resp = client.post("/analyze", json={"folder": str(tmp_path)})
    assert resp.status_code == 400


def test_analyze_missing_folder_returns_404(client):
    resp = client.post("/analyze", json={"folder": "/nonexistent/xyz", "prompt": "food"})
    assert resp.status_code == 404


def test_analyze_no_matches_returns_empty_list(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")
    with patch("app.query_claude", return_value="none"):
        resp = client.post("/analyze", json={"folder": str(tmp_path), "prompt": "food"})
    assert resp.status_code == 200
    assert resp.get_json()["highlights"]["vid.mp4"] == []


def test_segment_title_cards_inserted_within_video(client, tmp_path):
    """Segment title cards appear between non-contiguous clusters in the same video."""
    import time

    (tmp_path / "vid.mp4").touch()
    # Two lines with a gap line between them — will produce 2 segments
    (tmp_path / "vid.txt").write_text(
        "[0:05] Speaker: First line.\n"
        "[0:15] Speaker: Gap line.\n"
        "[0:25] Speaker: Second cluster.",
        encoding="utf-8",
    )

    with patch("app.extract_clip"), \
         patch("app.stitch_clips"), \
         patch("app.check_ffmpeg"), \
         patch("app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("app.make_title_card") as mock_card, \
         patch("app._library_add"):

        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {
                "vid.mp4": [
                    "[0:05] Speaker: First line.",
                    "[0:25] Speaker: Second cluster.",
                ],
            },
            "prompt": "",
            "output_filename": "out.mp4",
        })
        assert resp.status_code == 200
        job_id = resp.get_json()["job_id"]

        for _ in range(25):
            status = client.get(f"/status/{job_id}").get_json()["status"]
            if status in ("done", "error", "cancelled"):
                break
            time.sleep(0.2)

        assert status == "done", f"Job ended in unexpected state: {status}"

    # One segment card between the two clusters (no cross-video card, only one video)
    assert mock_card.call_count == 1
    assert mock_card.call_args[0][0] == "Segment 1"
