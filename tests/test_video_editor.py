import pytest
from unittest.mock import MagicMock, patch
from subprocess import CalledProcessError
from video_editor import (
    check_ffmpeg,
    parse_timestamp_to_seconds,
    extract_clip,
    stitch_clips,
)


def test_parse_timestamp_to_seconds_zero():
    assert parse_timestamp_to_seconds("0:00") == 0.0


def test_parse_timestamp_to_seconds_minutes_and_seconds():
    assert parse_timestamp_to_seconds("1:05") == 65.0


def test_parse_timestamp_to_seconds_large():
    assert parse_timestamp_to_seconds("12:30") == 750.0


def test_check_ffmpeg_raises_when_not_found():
    with patch("video_editor.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(RuntimeError, match="ffmpeg not found"):
            check_ffmpeg()


def test_check_ffmpeg_passes_when_found():
    with patch("video_editor.subprocess.run", return_value=MagicMock()):
        check_ffmpeg()  # should not raise


def test_extract_clip_calls_correct_ffmpeg_args():
    with patch("video_editor.subprocess.run") as mock_run:
        extract_clip("input.mp4", 5.0, 30.0, "clip.mp4")
    args = mock_run.call_args[0][0]
    assert args == [
        "ffmpeg", "-y",
        "-i", "input.mp4",
        "-ss", "5.0",
        "-to", "30.0",
        "-c", "copy",
        "clip.mp4",
    ]


def test_stitch_clips_calls_ffmpeg_concat(tmp_path):
    output = str(tmp_path / "out.webm")
    with patch("video_editor.subprocess.run") as mock_run:
        stitch_clips(["/tmp/clip_0.webm", "/tmp/clip_1.webm"], output)
    args = mock_run.call_args[0][0]
    assert "-f" in args
    assert "concat" in args
    assert output in args
    assert "-c" in args
    assert "copy" in args
    assert "libx264" not in args
    assert "aac" not in args


def test_stitch_clips_concat_list_contains_clip_paths(tmp_path):
    output = str(tmp_path / "out.mp4")
    captured = []

    def mock_run(cmd, **kwargs):
        if "-f" in cmd and "concat" in cmd:
            list_file = cmd[cmd.index("-i") + 1]
            with open(list_file) as f:
                captured.append(f.read())
        return MagicMock()

    with patch("video_editor.subprocess.run", side_effect=mock_run):
        stitch_clips(["/tmp/clip_0.mp4", "/tmp/clip_1.mp4"], output)

    assert len(captured) == 1
    assert "/tmp/clip_0.mp4" in captured[0]
    assert "/tmp/clip_1.mp4" in captured[0]


def test_extract_clip_propagates_ffmpeg_error():
    with patch("video_editor.subprocess.run", side_effect=CalledProcessError(1, "ffmpeg")):
        with pytest.raises(CalledProcessError):
            extract_clip("in.mp4", 0.0, 5.0, "out.mp4")


def test_stitch_clips_propagates_ffmpeg_error(tmp_path):
    output = str(tmp_path / "out.mp4")
    call_count = 0

    def mock_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1 and "-f" in cmd and "concat" in cmd:
            raise CalledProcessError(1, "ffmpeg")
        return MagicMock()

    with patch("video_editor.subprocess.run", side_effect=mock_run):
        with pytest.raises(CalledProcessError):
            stitch_clips(["/tmp/clip_0.mp4"], output)
