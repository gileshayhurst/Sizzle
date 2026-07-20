import io
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


def test_group_lines_into_segments_uses_video_duration_for_last_segment():
    """When video_duration is provided, last segment ends at video end, not +10s."""
    from generator_app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 5.0},
        {"raw": "b", "seconds": 10.0},
    ]
    result = _group_lines_into_segments(lines, {"a", "b"}, video_duration=20.0)
    assert result == [(5.0, 20.0)]


def test_group_lines_into_segments_caps_last_segment_at_max_clip_seconds():
    """A video end far past the last line would give an over-long clip; the
    MAX_CLIP_SECONDS ceiling trims it instead of running to the video end."""
    from generator_app import _group_lines_into_segments
    from shared import MAX_CLIP_SECONDS
    lines = [
        {"raw": "a", "seconds": 5.0},
        {"raw": "b", "seconds": 10.0},
    ]
    result = _group_lines_into_segments(lines, {"a", "b"}, video_duration=300.0)
    assert result == [(5.0, 5.0 + MAX_CLIP_SECONDS)]


def test_group_lines_into_segments_falls_back_to_plus_ten_without_duration():
    """When video_duration is None, existing +10 behaviour is preserved."""
    from generator_app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 5.0},
        {"raw": "b", "seconds": 10.0},
    ]
    result = _group_lines_into_segments(lines, {"a", "b"}, video_duration=None)
    assert result == [(5.0, 20.0)]


def test_group_lines_into_segments_extends_short_segment_to_minimum():
    from generator_app import _group_lines_into_segments, MIN_CLIP_SECONDS
    lines = [
        {"raw": "a", "seconds": 10.0},
        {"raw": "b", "seconds": 10.0},  # same-timestamp boundary -> ~0s segment
    ]
    result = _group_lines_into_segments(lines, {"a"})
    assert result == [(10.0, 10.0 + MIN_CLIP_SECONDS)]


def test_group_lines_into_segments_drops_segment_that_cannot_reach_minimum():
    from generator_app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 29.5},
        {"raw": "b", "seconds": 30.0},
    ]
    # 'a' is the trailing selected run; video ends at 30.0 so the widest
    # possible clip is 0.5s < MIN_CLIP_SECONDS -> drop it entirely (no title card).
    result = _group_lines_into_segments(lines, {"a"}, video_duration=30.0)
    assert result == []


def test_group_lines_into_segments_caps_trailing_dead_air():
    """A selection followed by a long gap ends near the speech, not the next line."""
    from shared import CLIP_TAIL_RATE, START_BIAS_SECONDS, TAIL_BUFFER
    from generator_app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 41.0,
         "text": "one two three four five six seven eight nine ten eleven twelve"},
        {"raw": "b", "seconds": 59.0, "text": "next speaker"},  # 18s later, unselected
    ]
    result = _group_lines_into_segments(lines, {"a"})
    # Measured from where speech really starts (recorded start + the bias that
    # normalization applied), at the slower clip-tail rate.
    expected_end = 41.0 + START_BIAS_SECONDS + 12 / CLIP_TAIL_RATE + TAIL_BUFFER
    assert expected_end < 59.0, "must still end before the next line, not at it"
    assert result == [(41.0, expected_end)]
    assert expected_end < 59.0  # dead air trimmed rather than running to next line


def test_group_lines_into_segments_long_line_keeps_full_speech():
    """A long final line before a big gap keeps its full estimated speech length."""
    from shared import CLIP_TAIL_RATE, START_BIAS_SECONDS, TAIL_BUFFER
    from generator_app import _group_lines_into_segments
    lines = [
        {"raw": "a", "seconds": 10.0, "text": " ".join(["word"] * 30)},
        {"raw": "b", "seconds": 60.0, "text": "next"},  # 50s gap
    ]
    result = _group_lines_into_segments(lines, {"a"})
    expected_end = 10.0 + START_BIAS_SECONDS + 30 / CLIP_TAIL_RATE + TAIL_BUFFER
    assert expected_end < 60.0, "must still trim the gap, not run to the next line"
    assert result == [(10.0, expected_end)]
    assert expected_end - 10.0 > 15.0  # scales with word count, not a fixed cap


def test_group_lines_into_segments_floor_applies_after_cap():
    """A very short selected line is still extended to the MIN_CLIP_SECONDS floor."""
    from generator_app import _group_lines_into_segments, MIN_CLIP_SECONDS
    lines = [
        {"raw": "a", "seconds": 10.0, "text": "yes"},  # cap would end ~11.5
        {"raw": "b", "seconds": 10.3, "text": "next"},  # boundary only 0.3s away
    ]
    result = _group_lines_into_segments(lines, {"a"})
    assert result == [(10.0, 10.0 + MIN_CLIP_SECONDS)]


def test_get_video_duration_returns_seconds():
    from generator_app import get_video_duration
    from unittest.mock import patch, MagicMock
    with patch("generator_app.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="127.5\n", returncode=0)
        assert get_video_duration("/fake/video.mp4") == 127.5


def test_get_video_duration_returns_none_on_failure():
    from generator_app import get_video_duration
    from unittest.mock import patch
    with patch("generator_app.subprocess.run", side_effect=Exception("ffprobe missing")):
        assert get_video_duration("/fake/video.mp4") is None


# ─── Job / status / cancel routes ─────────────────────────────────────────────

def test_status_unknown_job_returns_404(client):
    resp = client.get("/status/nonexistent-id")
    assert resp.status_code == 404


# ─── Library delete ───────────────────────────────────────────────────────────

def test_delete_library_entry_removes_from_json(client, tmp_path):
    """DELETE /library/<id> removes the entry; file is not deleted."""
    reel_file = tmp_path / "reel.mp4"
    reel_file.write_bytes(b"fake reel")
    entry = {
        "id": "del-test-1",
        "filename": "reel.mp4",
        "path": str(reel_file),
        "source_folder": "test/",
        "prompt": "test",
        "duration_seconds": 10,
        "clip_count": 1,
        "segment_starts": [],
        "created_at": "2026-06-08T00:00:00",
    }
    with patch("generator_app._load_library", return_value=[entry]), \
         patch("generator_app._save_library") as mock_save:
        resp = client.delete(f"/library/del-test-1")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    saved = mock_save.call_args[0][0]
    assert not any(e["id"] == "del-test-1" for e in saved)
    assert reel_file.exists()   # file NOT deleted


def test_delete_library_entry_with_delete_file_removes_file(client, tmp_path):
    """DELETE /library/<id>?delete_file=true also deletes the .mp4 file."""
    reel_file = tmp_path / "reel.mp4"
    reel_file.write_bytes(b"fake reel")
    entry = {
        "id": "del-test-2",
        "filename": "reel.mp4",
        "path": str(reel_file),
        "source_folder": "test/",
        "prompt": "test",
        "duration_seconds": 10,
        "clip_count": 1,
        "segment_starts": [],
        "created_at": "2026-06-08T00:00:00",
    }
    with patch("generator_app._load_library", return_value=[entry]), \
         patch("generator_app._save_library"):
        resp = client.delete(f"/library/del-test-2?delete_file=true")
    assert resp.status_code == 200
    assert not reel_file.exists()   # file IS deleted


def test_delete_library_entry_not_found_returns_404(client):
    """DELETE /library/<id> returns 404 when the id doesn't exist."""
    with patch("generator_app._load_library", return_value=[]):
        resp = client.delete("/library/no-such-id")
    assert resp.status_code == 404


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
    assert not without_flag.headers.get("Content-Disposition", "").startswith("attachment")


def test_library_captions_serves_local_sidecar(tmp_path, monkeypatch):
    import generator_app
    reel = tmp_path / "reel.mp4"
    reel.write_bytes(b"x")
    (tmp_path / "reel.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhi\n",
                                       encoding="utf-8")
    app = generator_app.create_app(testing=True)
    monkeypatch.setattr(generator_app, "_load_library", lambda: [
        {"id": "e1", "path": str(reel), "filename": "reel.mp4",
         "captions_filename": "reel.vtt"},
    ])
    c = app.test_client()
    resp = c.get("/library-captions/e1")
    assert resp.status_code == 200
    assert resp.mimetype == "text/vtt"
    assert b"WEBVTT" in resp.data


def test_library_captions_404_when_no_captions(monkeypatch):
    import generator_app
    app = generator_app.create_app(testing=True)
    monkeypatch.setattr(generator_app, "_load_library", lambda: [
        {"id": "e2", "path": "", "filename": "reel.mp4"},  # no caption fields
    ])
    resp = app.test_client().get("/library-captions/e2")
    assert resp.status_code == 404


def test_library_video_cloud_sanitizes_filename_in_disposition():
    """The cloud presigned-URL Content-Disposition must not let a filename break
    out of the quoted token or inject a header (", \\, CR, LF stripped)."""
    entry = {
        "id": "dl-test-2",
        "filename": 'evil".mp4\r\nX-Injected: 1',
        "path": "/nonexistent/reel.mp4",   # forces the cloud fallback branch
        "reel_s3_key": "sessions/abc/reel.mp4",
    }
    captured = {}

    def fake_presigned(key, **kwargs):
        captured.update(kwargs)
        return "https://r2.example.com/vid.mp4"

    from generator_app import create_app
    client = create_app(testing=True).test_client()
    with patch("generator_app._load_library", return_value=[entry]), \
         patch("generator_app.storage.is_cloud", return_value=True), \
         patch("generator_app.storage.presigned_url", side_effect=fake_presigned):
        resp = client.get("/library-video/dl-test-2?download=1")
    assert resp.status_code == 302
    disp = captured["content_disposition"]
    assert disp == 'attachment; filename="evil.mp4X-Injected: 1"'
    assert '"' not in disp[len("attachment; filename=\""):-1]
    assert "\r" not in disp and "\n" not in disp


# ─── Library edit ─────────────────────────────────────────────────────────────

def test_patch_library_entry_updates_title_and_notes(client):
    """PATCH /library/<id> updates title and notes fields and returns updated entry."""
    entry = {
        "id": "edit-test-1",
        "filename": "reel.mp4",
        "path": "/tmp/reel.mp4",
        "source_folder": "test/",
        "prompt": "test",
        "duration_seconds": 10,
        "clip_count": 1,
        "segment_starts": [],
        "created_at": "2026-06-08T00:00:00",
    }
    with patch("generator_app._load_library", return_value=[entry]), \
         patch("generator_app._save_library") as mock_save:
        resp = client.patch(
            "/library/edit-test-1",
            json={"title": "My Reel", "notes": "Great footage"},
            content_type="application/json",
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["title"] == "My Reel"
    assert data["notes"] == "Great footage"
    saved = mock_save.call_args[0][0]
    updated = next(e for e in saved if e["id"] == "edit-test-1")
    assert updated["title"] == "My Reel"
    assert updated["notes"] == "Great footage"


def test_patch_library_entry_not_found_returns_404(client):
    """PATCH /library/<id> returns 404 when the id doesn't exist."""
    with patch("generator_app._load_library", return_value=[]):
        resp = client.patch(
            "/library/no-such-id",
            json={"title": "X"},
            content_type="application/json",
        )
    assert resp.status_code == 404


def test_patch_library_entry_ignores_unknown_keys(client):
    """PATCH /library/<id> silently ignores fields other than title and notes."""
    entry = {
        "id": "edit-test-2",
        "filename": "reel.mp4",
        "path": "/tmp/reel.mp4",
        "source_folder": "test/",
        "prompt": "original",
        "duration_seconds": 10,
        "clip_count": 1,
        "segment_starts": [],
        "created_at": "2026-06-08T00:00:00",
    }
    with patch("generator_app._load_library", return_value=[entry]), \
         patch("generator_app._save_library") as mock_save:
        resp = client.patch(
            "/library/edit-test-2",
            json={"title": "New", "prompt": "hacked", "id": "spoofed"},
            content_type="application/json",
        )
    assert resp.status_code == 200
    saved = mock_save.call_args[0][0]
    updated = next(e for e in saved if e["id"] == "edit-test-2")
    assert updated["prompt"] == "original"   # not overwritten
    assert updated["id"] == "edit-test-2"    # not overwritten


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


def test_cancel_does_not_overwrite_done_status(client):
    """Cancelling a completed job must leave status='done'."""
    from generator_app import _jobs, _jobs_lock
    job_id = "cancel-race-done-test"
    with _jobs_lock:
        _jobs[job_id] = {
            "type": "generation", "status": "done",
            "total": 1, "done": 1, "log": [], "result": {"filename": "x.mp4"},
            "error": None, "cancel": threading.Event(),
        }
    resp = client.delete(f"/jobs/{job_id}")
    assert resp.status_code == 200
    with _jobs_lock:
        assert _jobs[job_id]["status"] == "done"   # must NOT become "cancelled"


# ─── /generate route ──────────────────────────────────────────────────────────

def test_generate_returns_job_id(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")

    with patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.check_ffmpeg"), \
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


# ─── Identification overlay integration tests ─────────────────────────────────
# Overlay lines are burned onto each clip, so they arrive as extract_clip's
# `title_lines` argument (positional index 5), not a separate title card.

def _run_generate_and_wait(client, tmp_path, selections, id_options=None):
    """Drive /generate to completion with a captured extract_clip; return the
    list of title_lines passed for each clip, in order."""
    with patch("generator_app.extract_clip") as mock_extract, \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app._library_add"):
        body = {
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": selections,
            "prompt": "",
            "output_filename": "out.mp4",
        }
        if id_options is not None:
            body["id_options"] = id_options
        resp = client.post("/generate", json=body)
        assert resp.status_code == 200
        job_id = resp.get_json()["job_id"]
        for _ in range(25):
            status = client.get(f"/status/{job_id}").get_json()["status"]
            if status in ("done", "error", "cancelled"):
                break
            time.sleep(0.2)
        assert status == "done", f"Job ended in unexpected state: {status}"
    return [c[0][5] for c in mock_extract.call_args_list]


def test_overlay_titles_ordered_across_videos(client, tmp_path):
    (tmp_path / "alpha.mp4").touch()
    (tmp_path / "alpha.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")
    (tmp_path / "beta.mp4").touch()
    (tmp_path / "beta.txt").write_text("[0:10] Speaker: World.", encoding="utf-8")

    titles = _run_generate_and_wait(client, tmp_path, {
        "alpha.mp4": ["[0:05] Speaker: Hello."],
        "beta.mp4": ["[0:10] Speaker: World."],
    })
    assert len(titles) == 2
    assert titles[0][0] == "alpha"
    assert titles[0][2] == "Segment 1 / 2"
    assert titles[1][0] == "beta"
    assert titles[1][2] == "Segment 2 / 2"


def test_overlay_titles_for_multiple_segments_within_video(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:05] Speaker: First line.\n"
        "[0:15] Speaker: Gap line.\n"
        "[0:25] Speaker: Second cluster.",
        encoding="utf-8",
    )

    titles = _run_generate_and_wait(client, tmp_path, {
        "vid.mp4": [
            "[0:05] Speaker: First line.",
            "[0:25] Speaker: Second cluster.",
        ],
    })
    assert len(titles) == 2
    assert titles[0][0] == "vid"
    assert titles[0][1].startswith("from ")
    assert titles[0][2] == "Segment 1 / 2"
    assert titles[1][0] == "vid"
    assert titles[1][2] == "Segment 2 / 2"


def test_id_options_select_which_overlay_lines(client, tmp_path):
    """Unchecked identification boxes drop their line; all-off yields no overlay."""
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")
    sel = {"vid.mp4": ["[0:05] Speaker: Hello."]}

    name_only = _run_generate_and_wait(
        client, tmp_path, sel, {"name": True, "timestamp": False, "segment": False})
    assert name_only[0] == ["vid"]

    none = _run_generate_and_wait(
        client, tmp_path, sel, {"name": False, "timestamp": False, "segment": False})
    assert none[0] == []


def test_generation_result_includes_segment_starts(client, tmp_path):
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:05] Speaker: Hello.\n[1:10] Speaker: World.", encoding="utf-8"
    )

    from generator_app import _jobs
    with patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.check_ffmpeg"), \
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
    # segment_starts must point to the TITLE CARD start so Prev/Next navigation
    # lands at the beginning of the transition screen.  First card starts at t=0.
    assert result["segment_starts"][0] == 0.0, (
        f"segment_starts[0] should be 0.0 (title card start) "
        f"but got {result['segment_starts'][0]}"
    )


# ─── /library routes ──────────────────────────────────────────────────────────

def test_library_starts_empty(client, tmp_path, monkeypatch):
    with patch("storage.load_library", return_value=[]):
        resp = client.get("/library")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_library_delete_removes_entry(client, tmp_path, monkeypatch):
    import generator_app as gen_module
    initial_entries = [{"id": "abc123", "filename": "x.mp4"}]
    saved = []
    with patch("storage.load_library", return_value=list(initial_entries)), \
         patch.object(gen_module, "_save_library", side_effect=lambda entries: saved.extend(entries)):
        resp = client.delete("/library/abc123")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert saved == []


# ─── Error-recovery in _run_generation ───────────────────────────────────────

def test_webm_source_uses_mp4_temp_clip(client, tmp_path):
    """extract_clip must receive a .mp4 output path even when the source is .webm.

    Previously vp.suffix was used, which caused ffmpeg to fail when writing
    H.264/AAC into a WebM container — silently dropping the clip from the reel.
    """
    (tmp_path / "vid.webm").touch()
    (tmp_path / "vid.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")

    with patch("generator_app.extract_clip") as mock_extract, \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app._library_add"):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {"vid.webm": ["[0:05] Speaker: Hello."]},
            "output_filename": "out.mp4",
        })
        assert resp.status_code == 200
        job_id = resp.get_json()["job_id"]

        for _ in range(25):
            import time; time.sleep(0.1)
            status = client.get(f"/status/{job_id}").get_json()["status"]
            if status in ("done", "error", "cancelled"):
                break

    assert mock_extract.called, "extract_clip should have been called"
    output_path_arg = mock_extract.call_args[0][3]   # 4th positional arg
    assert output_path_arg.endswith(".mp4"), (
        f"extract_clip output must be .mp4, got: {output_path_arg}"
    )


def test_failed_clip_skipped_from_reel(client, tmp_path):
    """When extract_clip fails for a segment, that clip is left out of the reel
    entirely — no title card to roll back, just one fewer stitched clip."""
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:05] Speaker: First.\n"
        "[0:15] Speaker: Gap.\n"
        "[0:25] Speaker: Second.",
        encoding="utf-8",
    )

    def fail_late_clip(*args, **kwargs):
        # args[1] is start_sec; the second segment starts at ~0:25. Keying on the
        # start (not call order) keeps this deterministic under parallel extraction.
        if args[1] >= 20:
            raise RuntimeError("simulated encode error")
        # First clip (start 0:05) succeeds

    stitched_with = []

    def capture_stitch(paths, out):
        stitched_with.extend(paths)

    with patch("generator_app.extract_clip", side_effect=fail_late_clip), \
         patch("generator_app.stitch_clips", side_effect=capture_stitch), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app._library_add"):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "checkbox",
            "selections": {
                "vid.mp4": ["[0:05] Speaker: First.", "[0:25] Speaker: Second."]
            },
            "output_filename": "out.mp4",
        })
        job_id = resp.get_json()["job_id"]

        for _ in range(25):
            import time; time.sleep(0.1)
            status = client.get(f"/status/{job_id}").get_json()["status"]
            if status in ("done", "error", "cancelled"):
                break

    # Two segments, second clip fails → only the first clip is stitched.
    assert len(stitched_with) == 1, (
        f"stitch_clips received {len(stitched_with)} paths; expected 1 (only clip 1)"
    )


def test_duration_seconds_is_sum_of_clip_durations(client, tmp_path):
    """No title cards → duration_seconds is just the summed clip content."""
    (tmp_path / "vid.mp4").touch()
    # Single selected line at 0:05. The trailing dead-air cap ends the clip at
    # the estimated speech end, measured from where speech really starts.
    (tmp_path / "vid.txt").write_text("[0:05] Speaker: Hello.", encoding="utf-8")
    from shared import CLIP_TAIL_RATE, START_BIAS_SECONDS, TAIL_BUFFER
    one_word_clip = START_BIAS_SECONDS + 1 / CLIP_TAIL_RATE + TAIL_BUFFER

    from generator_app import _jobs
    with patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app._library_add"):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {"vid.mp4": ["[0:05] Speaker: Hello."]},
            "output_filename": "out.mp4",
        })
        job_id = resp.get_json()["job_id"]

        for _ in range(50):
            import time; time.sleep(0.1)
            if _jobs.get(job_id, {}).get("status") in ("done", "error"):
                break

    result = _jobs[job_id]["result"]
    assert result is not None
    # 1 segment, clip content only. A title card would have added ~5s on top,
    # so this stays far below that even as the tail estimate moves.
    assert result["duration_seconds"] == int(one_word_clip), (
        f"duration_seconds={result['duration_seconds']}; "
        f"expected {int(one_word_clip)} ({one_word_clip}s of content, no title card)"
    )


# ─── Parallel clip extraction ─────────────────────────────────────────────────

def test_parallel_extraction_all_succeed(client, tmp_path):
    """All clips extracted; stitch receives one clip per segment (no title cards)."""
    (tmp_path / "v1.mp4").touch()
    (tmp_path / "v2.mp4").touch()
    (tmp_path / "v1.txt").write_text("[0:01] Speaker: hello\n[0:10] Speaker: done\n", encoding="utf-8")
    (tmp_path / "v2.txt").write_text("[0:01] Speaker: world\n[0:10] Speaker: end\n", encoding="utf-8")

    stitched = []

    def fake_extract(*args, **kwargs):
        from pathlib import Path
        Path(args[3]).write_bytes(b"clip")   # output_path is the 4th positional arg

    def fake_stitch(paths, out):
        stitched.extend(paths)
        from pathlib import Path
        Path(out).write_bytes(b"reel")

    with patch("generator_app.extract_clip", side_effect=fake_extract), \
         patch("generator_app.stitch_clips", side_effect=fake_stitch), \
         patch("generator_app._library_add"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {
                "v1.mp4": ["[0:01] Speaker: hello"],
                "v2.mp4": ["[0:01] Speaker: world"],
            },
            "output_filename": "out.mp4",
            "prompt": "test",
        })
    assert resp.status_code == 200
    job_id = resp.get_json()["job_id"]
    from generator_app import _jobs
    job = _jobs[job_id]
    assert job["status"] == "done"
    # Two segments → two clips → 2 paths
    assert len(stitched) == 2


def test_parallel_extraction_failed_clip_skipped(client, tmp_path):
    """A failed clip extraction skips that segment; other segments still appear."""
    (tmp_path / "v1.mp4").touch()
    (tmp_path / "v2.mp4").touch()
    (tmp_path / "v1.txt").write_text("[0:01] Speaker: bad\n[0:10] Speaker: end\n", encoding="utf-8")
    (tmp_path / "v2.txt").write_text("[0:01] Speaker: good\n[0:10] Speaker: end\n", encoding="utf-8")

    stitched = []

    def fake_extract(*args, **kwargs):
        from pathlib import Path
        if "v1" in args[0]:                  # video_path is the 1st positional arg
            raise RuntimeError("extraction failed")
        Path(args[3]).write_bytes(b"clip")

    def fake_stitch(paths, out):
        stitched.extend(paths)
        from pathlib import Path
        Path(out).write_bytes(b"reel")

    with patch("generator_app.extract_clip", side_effect=fake_extract), \
         patch("generator_app.stitch_clips", side_effect=fake_stitch), \
         patch("generator_app._library_add"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {
                "v1.mp4": ["[0:01] Speaker: bad"],
                "v2.mp4": ["[0:01] Speaker: good"],
            },
            "output_filename": "out.mp4",
            "prompt": "test",
        })
    assert resp.status_code == 200
    job_id = resp.get_json()["job_id"]
    from generator_app import _jobs
    job = _jobs[job_id]
    assert job["status"] == "done"
    # v1 failed, v2 succeeded → 1 clip = 1 path
    assert len(stitched) == 1


def test_parallel_extraction_all_fail_returns_error(client, tmp_path):
    """When every clip fails, job status is 'error'."""
    (tmp_path / "v1.mp4").touch()
    (tmp_path / "v1.txt").write_text("[0:01] Speaker: hi\n[0:10] Speaker: bye\n", encoding="utf-8")

    def fake_extract(*args, **kwargs):
        raise RuntimeError("always fails")

    with patch("generator_app.extract_clip", side_effect=fake_extract), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app._library_add"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "highlight",
            "selections": {"v1.mp4": ["[0:01] Speaker: hi"]},
            "output_filename": "out.mp4",
            "prompt": "test",
        })
    assert resp.status_code == 200
    job_id = resp.get_json()["job_id"]
    from generator_app import _jobs
    assert _jobs[job_id]["status"] == "error"


def test_duration_seconds_excludes_failed_clip(client, tmp_path):
    """A failed clip contributes nothing to duration_seconds — only surviving
    clips are summed (no title-card time in the total either)."""
    (tmp_path / "vid.mp4").touch()
    (tmp_path / "vid.txt").write_text(
        "[0:05] Speaker: First.\n"
        "[0:15] Speaker: Gap.\n"
        "[0:25] Speaker: Second.",
        encoding="utf-8",
    )

    def fail_late_clip(*args, **kwargs):
        # args[1] is start_sec; the second segment starts at ~0:25.
        if args[1] >= 20:
            raise RuntimeError("simulated encode error")
        # First clip (start 0:05) succeeds

    from generator_app import _jobs
    with patch("generator_app.extract_clip", side_effect=fail_late_clip), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app._library_add"):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "mode": "checkbox",
            "selections": {
                "vid.mp4": ["[0:05] Speaker: First.", "[0:25] Speaker: Second."]
            },
            "output_filename": "out.mp4",
        })
        job_id = resp.get_json()["job_id"]

        for _ in range(50):
            import time; time.sleep(0.1)
            if _jobs.get(job_id, {}).get("status") in ("done", "error"):
                break

    result = _jobs[job_id]["result"]
    assert result is not None, f"Job should complete, got: {_jobs[job_id]}"
    # Only segment 1 survives: "First." (1 word). Both clips would roughly
    # double this, so the assertion still distinguishes the failure case.
    from shared import CLIP_TAIL_RATE, START_BIAS_SECONDS, TAIL_BUFFER
    one_word_clip = START_BIAS_SECONDS + 1 / CLIP_TAIL_RATE + TAIL_BUFFER
    assert result["duration_seconds"] == int(one_word_clip), (
        f"duration_seconds={result['duration_seconds']}; "
        "failed clip must not contribute to duration"
    )


# ─── WebSocket ────────────────────────────────────────────────────────────────

class _MockWS:
    """Minimal WebSocket stub for unit-testing the job_ws handler."""

    def __init__(self):
        self.sent = []

    def send(self, data):
        self.sent.append(json.loads(data))


def _call_job_ws(job_id):
    """Invoke _job_ws_impl directly with a mock WebSocket."""
    from generator_app import _job_ws_impl
    ws = _MockWS()
    _job_ws_impl(ws, job_id)
    return ws.sent


def test_ws_done_job_sends_log_progress_done():
    """A job already in 'done' state delivers log, progress, and done messages."""
    from generator_app import _jobs, _jobs_lock
    job_id = "ws-test-done"
    with _jobs_lock:
        _jobs[job_id] = {
            "type": "generation",
            "status": "done",
            "total": 1,
            "done": 1,
            "log": ["✓ Done"],
            "result": {
                "filename": "test.mp4",
                "clip_count": 2,
                "duration_seconds": 30,
                "segment_starts": [],
                "path": "/tmp/test.mp4",
            },
            "error": None,
            "cancel": threading.Event(),
        }

    messages = _call_job_ws(job_id)

    types = [m["type"] for m in messages]
    assert "log" in types
    assert "progress" in types
    assert "done" in types

    done_msg = next(m for m in messages if m["type"] == "done")
    assert done_msg["status"] == "done"
    assert done_msg["result"]["filename"] == "test.mp4"


def test_ws_unknown_job_sends_error_done():
    """An unknown job_id causes the WS to send a done/error message and close."""
    messages = _call_job_ws("nonexistent-job-xyz")

    assert len(messages) == 1
    assert messages[0]["type"] == "done"
    assert messages[0]["status"] == "error"
    assert "not found" in messages[0]["error"]


# ─── /open-folder ─────────────────────────────────────────────────────────────

def test_open_folder_returns_ok_when_folder_missing(client):
    """open-folder returns 200 even when the path does not exist."""
    resp = client.post("/open-folder", json={"folder": "/nonexistent/path/xyz"})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_open_folder_handles_popen_exception(client, tmp_path):
    """open-folder returns 200 even when subprocess.Popen raises (e.g. no explorer on Linux)."""
    with patch("generator_app.subprocess.Popen",
               side_effect=FileNotFoundError("No such file: 'explorer'")):
        resp = client.post("/open-folder", json={"folder": str(tmp_path)})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_open_folder_uses_select_syntax_when_file_path_given(client, tmp_path):
    """/select,filepath syntax is used when file_path is provided and exists."""
    reel = tmp_path / "reel.mp4"
    reel.write_bytes(b"fake")
    with patch("generator_app.subprocess.Popen") as mock_popen:
        resp = client.post("/open-folder", json={
            "folder": str(tmp_path),
            "file_path": str(reel),
        })
    assert resp.status_code == 200
    call_args = mock_popen.call_args[0][0]   # first positional arg (the command list)
    assert any("/select," in a for a in call_args), \
        f"Expected /select, in explorer args, got: {call_args}"


def test_open_folder_falls_back_to_folder_when_file_path_missing(client, tmp_path):
    """Falls back to opening the folder when file_path is absent."""
    with patch("generator_app.subprocess.Popen") as mock_popen:
        resp = client.post("/open-folder", json={"folder": str(tmp_path)})
    assert resp.status_code == 200
    call_args = mock_popen.call_args[0][0]
    assert not any("/select," in a for a in call_args), \
        "Should not use /select, when only folder is given"


# ─── /find-local-folder ───────────────────────────────────────────────────────

def test_find_local_folder_locates_probe_file(tmp_path, client):
    """Endpoint finds the probe file and returns the directory path."""
    folder = tmp_path / "Downloads" / "MyVideos"
    folder.mkdir(parents=True)
    probe = folder / "sizzle_probe_abc123.tmp"
    probe.write_text("abc123", encoding="utf-8")

    with patch("generator_app.Path.home", return_value=tmp_path):
        resp = client.post("/find-local-folder", json={
            "probe_name": "sizzle_probe_abc123.tmp",
            "probe_content": "abc123",
        })

    assert resp.status_code == 200
    assert resp.get_json()["path"] == str(folder)


def test_find_local_folder_returns_null_when_not_found(tmp_path, client):
    """Returns {"path": null} when no matching probe file exists."""
    with patch("generator_app.Path.home", return_value=tmp_path):
        resp = client.post("/find-local-folder", json={
            "probe_name": "sizzle_probe_missing.tmp",
            "probe_content": "nothing",
        })

    assert resp.status_code == 200
    assert resp.get_json()["path"] is None


def test_find_local_folder_returns_null_on_empty_params(client):
    """Missing probe params → {"path": null}, no crash."""
    resp = client.post("/find-local-folder", json={})
    assert resp.status_code == 200
    assert resp.get_json()["path"] is None


def test_find_local_folder_rejects_traversal_probe_name(client):
    resp = client.post("/find-local-folder", json={
        "probe_name": "../../etc/passwd",
        "probe_content": "anything",
    })
    assert resp.status_code == 200
    assert resp.get_json()["path"] is None


# ─── entry_id in result ───────────────────────────────────────────────────────

def test_generation_result_includes_entry_id(tmp_path, client):
    """The job result must include entry_id matching the library entry."""
    video = tmp_path / "clip.mp4"
    video.touch()
    txt = tmp_path / "clip.txt"
    txt.write_text("[0:00] Speaker: Hello world\n", encoding="utf-8")

    captured_entry = {}

    def fake_add(entry):
        captured_entry.update(entry)

    with patch("generator_app._library_add", side_effect=fake_add), \
         patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "prompt": "test",
            "output_filename": "out.mp4",
            "selections": {"clip.mp4": ["[0:00] Speaker: Hello world"]},
        })
        assert resp.status_code == 200
        job_id = resp.get_json()["job_id"]

    from generator_app import _jobs
    result = _jobs[job_id]["result"]
    assert "entry_id" in result, "result must contain entry_id"
    assert result["entry_id"] == captured_entry["id"]


def test_cloud_temp_dir_cleanup_scheduled(client, tmp_path):
    """In cloud mode, a deferred cleanup timer must be started after generation."""
    session_key = "sessions/test"
    txt_content = "[0:05] Speaker: Hi."

    timers_started = []
    real_timer = __import__("threading").Timer

    def fake_timer(delay, fn):
        timers_started.append(delay)
        t = real_timer(0.001, lambda: None)  # fires immediately but harmlessly
        return t

    def fake_list_keys(prefix):
        return [f"{session_key}/vid.mp4", f"{session_key}/vid.txt"]

    def fake_download(key, local_path):
        if key.endswith(".txt"):
            from pathlib import Path as _Path
            _Path(local_path).write_text(txt_content, encoding="utf-8")

    mock_proc = MagicMock()
    mock_proc.stdout = io.BytesIO(b"fake mp4 data")
    mock_proc.stderr = io.BytesIO(b"")
    mock_proc.returncode = 0
    mock_proc._concat_list_path = str(tmp_path / "_concat_cleanup.txt")
    Path(mock_proc._concat_list_path).touch()
    mock_proc.wait.return_value = None

    with patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips_to_pipe", return_value=mock_proc), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)), \
         patch("generator_app.get_video_duration", return_value=None), \
         patch("generator_app._library_add"), \
         patch("storage.is_cloud", return_value=True), \
         patch("generator_app.storage.is_cloud", return_value=True), \
         patch("generator_app.storage.list_keys", side_effect=fake_list_keys), \
         patch("generator_app.storage.download_file", side_effect=fake_download), \
         patch("generator_app.storage.presigned_url", return_value="https://r2.example.com/vid.mp4"), \
         patch("generator_app.storage.upload_stream"), \
         patch("generator_app.threading.Timer", side_effect=fake_timer):
        resp = client.post("/generate", json={
            "session_key": session_key,
            "mode": "highlight",
            "selections": {"vid.mp4": ["[0:05] Speaker: Hi."]},
            "output_filename": "out.mp4",
        })

    assert resp.status_code == 200
    assert len(timers_started) >= 1
    # A 1-hour cleanup timer must be scheduled. (Not necessarily first in the
    # list — flask-limiter's in-memory storage also schedules short-lived timers.)
    assert 3600 in timers_started   # 1 hour


# ─── _build_segment_list ─────────────────────────────────────────────────────

def test_build_segment_list_returns_segments_with_correct_fields(tmp_path):
    from generator_app import _build_segment_list
    # Turn-level source line; read_transcript() splits it into sentence-level
    # lines on read, so the selection below is the sentence-level raw string
    # that actually appears after normalization (see shared.normalize_transcript).
    transcript = "[0:10] Speaker: Hello world. Great content here.\n[0:20] Speaker: Second line."
    (tmp_path / "video.webm").write_bytes(b"")
    (tmp_path / "video.txt").write_text(transcript, encoding="utf-8")
    vp = tmp_path / "video.webm"
    selections = {"video.webm": ["[0:10] Speaker: Hello world."]}
    with patch("generator_app.get_video_duration", return_value=60.0):
        result = _build_segment_list([vp], selections)
    assert len(result) == 1
    seg = result[0]
    assert seg["video_name"] == "video.webm"
    assert seg["video_stem"] == "video"
    assert seg["start_sec"] == 10.0
    assert seg["title_lines"][0] == "video"
    assert seg["title_lines"][1] == "from 0:10"
    assert seg["title_lines"][2] == "Segment 1 / 1"


def test_build_segment_list_numbers_segments_across_videos(tmp_path):
    from generator_app import _build_segment_list
    for name in ["a.webm", "b.webm"]:
        (tmp_path / name).write_bytes(b"")
        (tmp_path / name).with_suffix(".txt").write_text(
            "[0:05] Speaker: Clip from this file.", encoding="utf-8"
        )
    vps = [tmp_path / "a.webm", tmp_path / "b.webm"]
    sel = {
        "a.webm": ["[0:05] Speaker: Clip from this file."],
        "b.webm": ["[0:05] Speaker: Clip from this file."],
    }
    with patch("generator_app.get_video_duration", return_value=60.0):
        result = _build_segment_list(vps, sel)
    assert len(result) == 2
    assert result[0]["title_lines"][2] == "Segment 1 / 2"
    assert result[1]["title_lines"][2] == "Segment 2 / 2"


def test_build_segment_list_skips_video_with_no_txt(tmp_path):
    from generator_app import _build_segment_list
    vp = tmp_path / "video.webm"
    vp.write_bytes(b"")
    # No .txt file
    with patch("generator_app.get_video_duration", return_value=60.0):
        result = _build_segment_list([vp], {"video.webm": ["[0:05] Speaker: Hi."]})
    assert result == []


def test_build_segment_list_uses_video_urls_for_ffmpeg_input(tmp_path):
    from generator_app import _build_segment_list
    vp = tmp_path / "video.webm"
    vp.write_bytes(b"")
    (tmp_path / "video.txt").write_text("[0:05] Speaker: Hi there.", encoding="utf-8")
    presigned = "https://r2.example.com/video.webm?sig=abc"
    with patch("generator_app.get_video_duration", return_value=60.0):
        result = _build_segment_list([vp], {"video.webm": ["[0:05] Speaker: Hi there."]},
                                     video_urls={"video.webm": presigned})
    assert result[0]["ffmpeg_input"] == presigned


def test_build_segment_list_skips_duration_probe_for_remote_input(tmp_path):
    """ffprobe is never run against a presigned URL.

    On the cloud /plan path ffmpeg_input is an R2 URL; probing it over HTTP from
    Render burns the full 5s timeout per video and returns None anyway. The
    browser encoder clamps clip ends itself (input.computeDuration() in
    static/reel-encoder.js), so the probe is pure wasted wall time here.
    """
    from generator_app import _build_segment_list
    vp = tmp_path / "video.webm"
    vp.write_bytes(b"")
    (tmp_path / "video.txt").write_text("[0:05] Speaker: Hi there.", encoding="utf-8")
    selections = {"video.webm": ["[0:05] Speaker: Hi there."]}

    with patch("generator_app.get_video_duration") as probe:
        result = _build_segment_list(
            [vp], selections,
            video_urls={"video.webm": "https://r2.example.com/video.webm?sig=abc"},
        )
    assert result, "segments should still be built without a duration"
    probe.assert_not_called()


def test_build_segment_list_still_probes_local_input(tmp_path):
    """The local /generate path keeps probing — there ffprobe is fast and the
    duration is what clamps a trailing segment to the real end of the video."""
    from generator_app import _build_segment_list
    vp = tmp_path / "video.webm"
    vp.write_bytes(b"")
    (tmp_path / "video.txt").write_text("[0:05] Speaker: Hi there.", encoding="utf-8")
    selections = {"video.webm": ["[0:05] Speaker: Hi there."]}

    with patch("generator_app.get_video_duration", return_value=60.0) as probe:
        _build_segment_list([vp], selections)
    probe.assert_called_once_with(str(vp))


# ─── Captions (Task 2) ────────────────────────────────────────────────────────

def test_build_segment_list_attaches_caption_lines(tmp_path):
    import generator_app
    from pathlib import Path

    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"")
    (tmp_path / "clip.txt").write_text(
        "[0:10] Guest: first selected line\n"
        "[0:12] Guest: second selected line\n"
        "[0:20] Guest: unselected\n",
        encoding="utf-8",
    )
    selections = {"clip.mp4": [
        "[0:10] Guest: first selected line",
        "[0:12] Guest: second selected line",
    ]}

    import unittest.mock as m
    with m.patch("generator_app.get_video_duration", return_value=60.0):
        segs = generator_app._build_segment_list([Path(vid)], selections)

    assert len(segs) == 1
    cl = segs[0]["caption_lines"]
    assert [c["text"] for c in cl] == ["first selected line", "second selected line"]
    assert [c["seconds"] for c in cl] == [10.0, 12.0]


def test_local_generation_writes_vtt_sidecar(client, tmp_path):
    video = tmp_path / "clip.mp4"
    video.touch()
    txt = tmp_path / "clip.txt"
    txt.write_text("[0:00] Speaker: Hello world\n", encoding="utf-8")

    captured = {}

    def fake_add(entry):
        captured["entry"] = entry

    with patch("generator_app._library_add", side_effect=fake_add), \
         patch("generator_app.extract_clip"), \
         patch("generator_app.stitch_clips"), \
         patch("generator_app.check_ffmpeg"), \
         patch("generator_app.get_video_dimensions", return_value=(1920, 1080)):
        resp = client.post("/generate", json={
            "folder": str(tmp_path),
            "prompt": "test",
            "output_filename": "reel.mp4",
            "selections": {"clip.mp4": ["[0:00] Speaker: Hello world"]},
        })
        assert resp.status_code == 200

    sidecar = tmp_path / "reel.vtt"
    assert sidecar.exists()
    assert sidecar.read_text(encoding="utf-8").startswith("WEBVTT")
    assert captured["entry"]["captions_filename"] == "reel.vtt"


def test_download_captioned_runs_ffmpeg_subtitles(tmp_path, monkeypatch):
    import generator_app, subprocess, unittest.mock as m
    reel = tmp_path / "reel.mp4"; reel.write_bytes(b"x")
    (tmp_path / "reel.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhi\n",
                                       encoding="utf-8")
    app = generator_app.create_app(testing=True)
    monkeypatch.setattr(generator_app, "_load_library", lambda: [
        {"id": "e1", "path": str(reel), "filename": "reel.mp4",
         "captions_filename": "reel.vtt"},
    ])

    calls = {}
    def fake_run(cmd, *a, **k):
        calls["cmd"] = cmd
        # Emulate ffmpeg producing the output file (last arg).
        Path(cmd[-1]).write_bytes(b"captioned")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(generator_app.subprocess, "run", fake_run)
    resp = app.test_client().post("/library/e1/download-captioned")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("video/mp4")
    # ffmpeg invoked with a subtitles filter referencing the VTT
    joined = " ".join(calls["cmd"])
    assert "subtitles" in joined
    assert "-vf" in calls["cmd"]


def test_download_captioned_404_without_captions(monkeypatch):
    import generator_app
    app = generator_app.create_app(testing=True)
    monkeypatch.setattr(generator_app, "_load_library", lambda: [
        {"id": "e2", "path": "/x/reel.mp4", "filename": "reel.mp4"},
    ])
    resp = app.test_client().post("/library/e2/download-captioned")
    assert resp.status_code == 404
