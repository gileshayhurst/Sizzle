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
        "-ss", "5.0",
        "-i", "input.mp4",
        "-t", "25.0",
        "-avoid_negative_ts", "make_zero",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-r", "30",
        "-c:a", "aac",
        "-ar", "48000",
        "-ac", "2",
        "clip.mp4",
    ]


def test_extract_clip_normalises_audio_to_48k_stereo():
    """All content clips must have identical audio parameters to title cards (48 kHz stereo)
    so the concat demuxer sees a single consistent audio timebase and doesn't drift."""
    with patch("video_editor.subprocess.run") as mock_run:
        extract_clip("input.mp4", 0.0, 10.0, "clip.mp4")
    args = mock_run.call_args[0][0]
    joined = " ".join(args)
    assert "-ar 48000" in joined, "extract_clip must force 48 kHz to match title cards"
    assert "-ac 2" in joined, "extract_clip must force stereo to match title cards"


def test_extract_clip_does_not_use_stream_copy():
    """Stream copy (-c copy) in extract_clip produces clips that may start on P/B frames,
    causing a visible freeze at every clip transition when the output is assembled."""
    with patch("video_editor.subprocess.run") as mock_run:
        extract_clip("input.mp4", 5.0, 30.0, "clip.mp4")
    args = mock_run.call_args[0][0]
    assert "-c" not in args or args[args.index("-c") + 1] != "copy", \
        "extract_clip must not use -c copy: it produces non-keyframe clip starts that freeze on playback"


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
    """stitch_clips must raise when ffmpeg exits non-zero.

    The mock must return a non-zero returncode (not raise directly) because
    stitch_clips uses check=False and relies on result.check_returncode().
    Raising from subprocess.run would bypass that path entirely.
    """
    output = str(tmp_path / "out.mp4")

    def mock_run(cmd, **kwargs):
        if "-f" in cmd and "concat" in cmd:
            m = MagicMock()
            m.returncode = 1
            m.stderr = b"concat failed"
            m.check_returncode.side_effect = CalledProcessError(1, "ffmpeg")
            return m
        return MagicMock(returncode=0)

    with patch("video_editor.subprocess.run", side_effect=mock_run):
        with pytest.raises(CalledProcessError):
            stitch_clips(["/tmp/clip_0.mp4"], output)
