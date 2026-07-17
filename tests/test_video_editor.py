import pytest
import os
import tempfile
import subprocess
from pathlib import Path
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


def _captured_cmd(mock_run):
    """Return the ffmpeg argv list from the first subprocess.run call."""
    return mock_run.call_args[0][0]


def test_extract_clip_no_fade_has_no_vf():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = type("R", (), {"returncode": 0})()
        extract_clip("input.mp4", 0.0, 10.0, "out.mp4")
    cmd = _captured_cmd(mock_run)
    assert "-vf" not in cmd


def test_extract_clip_fade_out_adds_vf_and_af():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = type("R", (), {"returncode": 0})()
        extract_clip("input.mp4", 0.0, 10.0, "out.mp4", fade_out_secs=2.0)
    cmd = _captured_cmd(mock_run)
    assert "-vf" in cmd
    vf_val = cmd[cmd.index("-vf") + 1]
    assert "fade=t=out" in vf_val
    assert "st=8.0" in vf_val   # 10.0 - 2.0
    assert "d=2.0" in vf_val
    assert "-af" in cmd
    af_val = cmd[cmd.index("-af") + 1]
    assert "afade=t=out" in af_val
    assert "st=8.0" in af_val
    assert "d=2.0" in af_val


def test_extract_clip_fade_clamped_for_short_clip():
    """Clip shorter than fade duration: fade_start clamped to 0."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = type("R", (), {"returncode": 0})()
        extract_clip("input.mp4", 5.0, 6.0, "out.mp4", fade_out_secs=2.0)
    cmd = _captured_cmd(mock_run)
    vf_val = cmd[cmd.index("-vf") + 1]
    # duration = 1.0, fade_start = max(0, 1.0-2.0) = 0.0
    assert "st=0.0" in vf_val


def test_extract_clip_burns_title_overlay():
    """title_lines are burned onto the clip as top-anchored drawtext with a
    fading alpha (escaped commas so ffmpeg doesn't treat them as separators)."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = type("R", (), {"returncode": 0})()
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "clip_0000.mp4")
            extract_clip("input.mp4", 0.0, 8.0, out,
                         title_lines=["Sarah K", "from 1:24"], font_path=None, height=1080)
            # textfiles written next to the clip, one per line
            assert os.path.exists(os.path.join(tmp, "clip_0000_t0.txt"))
            assert os.path.exists(os.path.join(tmp, "clip_0000_t1.txt"))
    cmd = _captured_cmd(mock_run)
    vf_val = cmd[cmd.index("-vf") + 1]
    assert vf_val.count("drawtext=") == 2
    assert "alpha=" in vf_val
    assert "\\," in vf_val        # commas in the alpha expr are escaped
    assert mock_run.call_args.kwargs.get("cwd") == tmp  # relative paths resolve here


def test_extract_clip_no_title_lines_no_drawtext():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = type("R", (), {"returncode": 0})()
        extract_clip("input.mp4", 0.0, 8.0, "out.mp4", title_lines=[])
    cmd = _captured_cmd(mock_run)
    assert "-vf" not in cmd


def test_extract_clip_shows_timer():
    """show_timer burns a per-second countdown: one enable-gated drawtext per
    whole second, each with its 'M:SS' text in a side-car file (no colon in the
    filter string), and the countdown runs down (0:08 → 0:01)."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = type("R", (), {"returncode": 0})()
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "clip_0000.mp4")
            extract_clip("input.mp4", 0.0, 8.0, out, show_timer=True, height=1080)
            assert (open(os.path.join(tmp, "clip_0000_timer0.txt")).read()) == "0:08"
            assert (open(os.path.join(tmp, "clip_0000_timer7.txt")).read()) == "0:01"
    cmd = _captured_cmd(mock_run)
    vf_val = cmd[cmd.index("-vf") + 1]
    assert vf_val.count("drawtext=") == 8          # ceil(8.0) windows
    assert "enable=between(t\\," in vf_val          # commas escaped
    assert "0:08" not in vf_val                     # M:SS lives in the file, not the filter
    assert "%{eif" not in vf_val                    # no dynamic expression (would break this ffmpeg)


def test_extract_clip_fade_in_and_out():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = type("R", (), {"returncode": 0})()
        extract_clip("input.mp4", 0.0, 8.0, "out.mp4", fade_out_secs=0.4, fade_in_secs=0.4)
    cmd = _captured_cmd(mock_run)
    vf_val = cmd[cmd.index("-vf") + 1]
    assert "fade=t=in:st=0:d=0.4" in vf_val
    assert "fade=t=out" in vf_val
    af_val = cmd[cmd.index("-af") + 1]
    assert "afade=t=in:st=0:d=0.4" in af_val
    assert "afade=t=out" in af_val


def test_stitch_clips_to_pipe_returns_popen_with_pipe_stdout():
    """stitch_clips_to_pipe must return a Popen with stdout=PIPE and the right ffmpeg flags."""
    from video_editor import stitch_clips_to_pipe

    with patch("video_editor.subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        result = stitch_clips_to_pipe(["/tmp/a.mp4", "/tmp/b.mp4"])

    assert result is mock_proc
    call_args = mock_popen.call_args
    cmd = call_args[0][0]
    kwargs = call_args[1]

    assert kwargs.get("stdout") == subprocess.PIPE
    assert kwargs.get("stderr") == subprocess.PIPE
    assert "pipe:1" in cmd
    assert "-movflags" in cmd
    movflags_val = cmd[cmd.index("-movflags") + 1]
    assert "frag_keyframe" in movflags_val
    assert "empty_moov" in movflags_val
    # Must be a concat command
    assert "concat" in cmd
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"


def test_stitch_clips_to_pipe_concat_list_contains_paths(tmp_path):
    """stitch_clips_to_pipe must write clip paths into the concat list file."""
    from video_editor import stitch_clips_to_pipe

    captured_cmd = []

    def fake_popen(cmd, **kwargs):
        captured_cmd.extend(cmd)
        m = MagicMock()
        m._concat_list_path = cmd[cmd.index("-i") + 1]
        return m

    with patch("video_editor.subprocess.Popen", side_effect=fake_popen):
        proc = stitch_clips_to_pipe(["/tmp/clip_0.mp4", "/tmp/clip_1.mp4"])

    list_path = proc._concat_list_path
    content = Path(list_path).read_text()
    assert "/tmp/clip_0.mp4" in content
    assert "/tmp/clip_1.mp4" in content
    # Cleanup
    Path(list_path).unlink(missing_ok=True)
