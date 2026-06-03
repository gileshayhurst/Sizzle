import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from generator_app import create_app


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    app = create_app(testing=True)
    with app.test_client() as c:
        yield c


# ─── _format_seconds ──────────────────────────────────────────────────────────

def test_format_seconds_zero():
    from generator_app import _format_seconds
    assert _format_seconds(0.0) == "0:00"


def test_format_seconds_minutes_and_seconds():
    from generator_app import _format_seconds
    assert _format_seconds(75.0) == "1:15"


def test_format_seconds_exact_minute():
    from generator_app import _format_seconds
    assert _format_seconds(120.0) == "2:00"


# ─── make_title_card ──────────────────────────────────────────────────────────

def test_make_title_card_generates_one_drawtext_per_line():
    from generator_app import make_title_card
    with patch("generator_app.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card(["NOBU", "from 1:23", "Segment 2 / 5"], 1920, 1080, "/tmp/card.mp4")
    args = mock_run.call_args[0][0]
    vf_idx = args.index("-vf")
    vf_value = args[vf_idx + 1]
    assert vf_value.count("drawtext=") == 3
    assert "NOBU" in vf_value
    assert "from 1:23" in vf_value
    assert "Segment 2 / 5" in vf_value


def test_make_title_card_calls_ffmpeg_with_correct_args():
    from generator_app import make_title_card
    with patch("generator_app.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card(["My Video"], 1920, 1080, "/tmp/card.mp4", duration=5.0)
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    joined = " ".join(cmd)
    assert cmd[0] == "ffmpeg"
    assert "1920x1080" in joined
    assert "My Video" in joined
    assert "/tmp/card.mp4" in joined
    assert "5.0" in joined


def test_make_title_card_escapes_special_characters():
    from generator_app import make_title_card
    apos = chr(0x27)
    curly = chr(0x2019)
    with patch("generator_app.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card(["It" + apos + "s 50% Done: Really"], 1280, 720, "/tmp/card.mp4")
    vf = mock_run.call_args[0][0][mock_run.call_args[0][0].index("-vf") + 1]
    text_val = vf.split("text=" + apos)[1].split(apos)[0]
    assert apos not in text_val
    assert curly in text_val
    assert "%%" in text_val
    assert "Done: Really" in text_val


def test_make_title_card_includes_fontfile_when_font_found():
    from generator_app import make_title_card
    with patch("generator_app.subprocess.run") as mock_run, \
         patch("generator_app._find_system_font", return_value="C:/Windows/Fonts/arial.ttf"):
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card(["Test"], 1920, 1080, "/tmp/card.mp4")
    joined = " ".join(mock_run.call_args[0][0])
    assert "fontfile=" in joined
    assert "arial.ttf" in joined


def test_make_title_card_omits_fontfile_when_none_found():
    from generator_app import make_title_card
    with patch("generator_app.subprocess.run") as mock_run, \
         patch("generator_app._find_system_font", return_value=None):
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card(["Test"], 1920, 1080, "/tmp/card.mp4")
    joined = " ".join(mock_run.call_args[0][0])
    assert "fontfile=" not in joined


def test_make_title_card_wraps_long_title():
    from generator_app import make_title_card
    with patch("generator_app.subprocess.run") as mock_run, \
         patch("generator_app._find_system_font", return_value=None):
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card(["New York", "Japanese restaurant", "Nobu"], 640, 352, "/tmp/card.mp4")
    cmd = mock_run.call_args[0][0]
    vf_arg = cmd[cmd.index("-vf") + 1]
    assert vf_arg.count("drawtext=") == 3


def test_make_title_card_does_not_wrap_short_title():
    from generator_app import make_title_card
    with patch("generator_app.subprocess.run") as mock_run, \
         patch("generator_app._find_system_font", return_value=None):
        mock_run.return_value = MagicMock(returncode=0)
        make_title_card(["Nobu"], 1920, 1080, "/tmp/card.mp4")
    cmd = mock_run.call_args[0][0]
    vf_arg = cmd[cmd.index("-vf") + 1]
    assert vf_arg.count("drawtext=") == 1


# ─── get_video_dimensions ─────────────────────────────────────────────────────

def test_get_video_dimensions_returns_width_height():
    from generator_app import get_video_dimensions
    with patch("generator_app.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="1920,1080\n", returncode=0)
        w, h = get_video_dimensions("/fake/video.mp4")
    assert w == 1920
    assert h == 1080
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "ffprobe"
    assert "/fake/video.mp4" in cmd


def test_get_video_dimensions_falls_back_on_failure():
    from generator_app import get_video_dimensions
    with patch("generator_app.subprocess.run", side_effect=Exception("ffprobe missing")):
        w, h = get_video_dimensions("/fake/video.mp4")
    assert (w, h) == (1920, 1080)


# ─── _group_lines_into_segments ───────────────────────────────────────────────

def test_group_lines_into_segments_single_contiguous_block():
    from generator_app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 5.0},
        {"raw": "b", "seconds": 10.0},
        {"raw": "c", "seconds": 15.0},
        {"raw": "d", "seconds": 20.0},
    ]
    result = _group_lines_into_segments(lines, {"a", "b", "c"})
    assert result == [(5.0, 20.0)]


def test_group_lines_into_segments_two_clusters():
    from generator_app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 5.0},
        {"raw": "b", "seconds": 10.0},
        {"raw": "c", "seconds": 15.0},
        {"raw": "d", "seconds": 20.0},
    ]
    result = _group_lines_into_segments(lines, {"a", "c", "d"})
    assert result == [(5.0, 10.0), (15.0, 30.0)]


def test_group_lines_into_segments_all_selected():
    from generator_app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 5.0},
        {"raw": "b", "seconds": 10.0},
    ]
    result = _group_lines_into_segments(lines, {"a", "b"})
    assert result == [(5.0, 20.0)]


def test_group_lines_into_segments_none_selected():
    from generator_app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 5.0},
        {"raw": "b", "seconds": 10.0},
    ]
    result = _group_lines_into_segments(lines, set())
    assert result == []


# ─── Job / status / cancel routes ─────────────────────────────────────────────

def test_status_unknown_job_returns_404(client):
    resp = client.get("/status/nonexistent-id")
    assert resp.status_code == 404


def test_cancel_job(client):
    from generator_app import _jobs, _jobs_lock
    job_id = "cancel-gen-test-456"
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


# ─── /generate route ──────────────────────────────────────────────────────────

def test_generate_returns_job_id(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")

    with patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.make_title_card"), \
         patch("generator_app._library_add"):
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
        job_id = data["job_id"]

        for _ in range(25):
            status = client.get(f"/status/{job_id}").get_json()["status"]
            if status in ("done", "error", "cancelled"):
                break
            time.sleep(0.2)


def test_generate_accepts_empty_prompt(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")

    with patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.make_title_card"), \
         patch("generator_app._library_add"):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {"vid.mp4": ["[0:05] Speaker: Hello."]},
            "output_filename": "out.mp4",
        })
        assert resp.status_code == 200
        assert "job_id" in resp.get_json()


def test_generate_missing_folder_returns_404(client):
    resp = client.post("/generate", json={
        "folder": "/nonexistent/xyz",
        "selections": {},
        "output_filename": "out.mp4",
    })
    assert resp.status_code == 404


def test_video_endpoint_not_found(client):
    resp = client.get("/video/nonexistent-job-id")
    assert resp.status_code == 404


# ─── Title card integration tests ─────────────────────────────────────────────

def test_title_card_inserted_between_videos(client, tmp_path):
    (tmp_path / "alpha.mp4").touch()
    (tmp_path / "alpha.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")
    (tmp_path / "beta.mp4").touch()
    (tmp_path / "beta.txt").write_text("[0:10] Speaker: World.", encoding="utf-8")

    with patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.make_title_card") as mock_card, \
         patch("generator_app._library_add"):
        mock_card.reset_mock()
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

    assert mock_card.call_count == 2
    calls = [c[0][0] for c in mock_card.call_args_list]
    assert calls[0][0] == "alpha"
    assert calls[0][2] == "Segment 1 / 2"
    assert calls[1][0] == "beta"
    assert calls[1][2] == "Segment 2 / 2"


def test_segment_title_cards_inserted_within_video(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:05] Speaker: First line.\n"
        "[0:15] Speaker: Gap line.\n"
        "[0:25] Speaker: Second cluster.",
        encoding="utf-8",
    )

    with patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.make_title_card") as mock_card, \
         patch("generator_app._library_add"):
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

    assert mock_card.call_count == 2
    calls = [c[0][0] for c in mock_card.call_args_list]
    assert calls[0][0] == "vid"
    assert calls[0][1].startswith("from ")
    assert calls[0][2] == "Segment 1 / 2"
    assert calls[1][0] == "vid"
    assert calls[1][1].startswith("from ")
    assert calls[1][2] == "Segment 2 / 2"


def test_generation_result_includes_segment_starts(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:05] Speaker: Hello.\n[1:10] Speaker: World.", encoding="utf-8"
    )

    from generator_app import _jobs
    with patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.make_title_card"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app._library_add"):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "checkbox",
            "selections": {"vid.mp4": ["[0:05] Speaker: Hello.", "[1:10] Speaker: World."]},
            "prompt": "greetings",
            "output_filename": "out.mp4",
        })
        job_id = resp.get_json()["job_id"]

        for _ in range(50):
            time.sleep(0.1)
            if _jobs.get(job_id, {}).get("status") in ("done", "error"):
                break

    result = _jobs[job_id]["result"]
    assert result is not None
    assert "segment_starts" in result
    assert isinstance(result["segment_starts"], list)
    assert len(result["segment_starts"]) >= 1


# ─── /library routes ──────────────────────────────────────────────────────────

def test_library_starts_empty(client, tmp_path, monkeypatch):
    import generator_app as gen_module
    monkeypatch.setattr(gen_module, "LIBRARY_PATH", tmp_path / "lib.json")
    resp = client.get("/library")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_library_delete_removes_entry(client, tmp_path, monkeypatch):
    import generator_app as gen_module
    lib_path = tmp_path / "lib.json"
    monkeypatch.setattr(gen_module, "LIBRARY_PATH", lib_path)
    lib_path.write_text(json.dumps([{"id": "abc123", "filename": "x.mp4"}]), encoding="utf-8")
    resp = client.delete("/library/abc123")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    remaining = json.loads(lib_path.read_text(encoding="utf-8"))
    assert remaining == []
