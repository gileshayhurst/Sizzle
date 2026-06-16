import os
import subprocess
import tempfile
from pathlib import Path


def check_ffmpeg() -> None:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        raise RuntimeError(
            "ffmpeg not found. Install it with:\n"
            "  Windows: winget install ffmpeg\n"
            "  Mac: brew install ffmpeg"
        )


def parse_timestamp_to_seconds(ts: str) -> float:
    parts = ts.split(":")
    return float(int(parts[0]) * 60 + int(parts[1]))


def extract_clip(video_path: str, start_sec: float, end_sec: float, output_path: str, fade_out_secs: float = 0.0) -> None:
    # Re-encode (never stream-copy) so every clip starts on an I-frame.
    # -ss before -i: fast input seek. -t duration (not -to) is relative to the
    # seek point. -avoid_negative_ts make_zero zeroes each clip's timestamps so
    # the concat demuxer sees clean zero-based PTS on every clip — prevents AV drift.
    duration = end_sec - start_sec
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-i", video_path,
        "-t", str(duration),
        "-avoid_negative_ts", "make_zero",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-r", "30",       # normalise to 30 fps — must match title cards so the
        "-c:a", "aac",    # concat demuxer sees a single consistent video timebase
        "-ar", "48000",
        "-ac", "2",
    ]
    if fade_out_secs > 0.0:
        fade_start = max(0.0, duration - fade_out_secs)
        cmd += [
            "-vf", f"fade=t=out:st={fade_start}:d={fade_out_secs}",
            "-af", f"afade=t=out:st={fade_start}:d={fade_out_secs}",
        ]
    cmd.append(output_path)
    subprocess.run(
        cmd,
        check=True,
        capture_output=True,
    )


def stitch_clips(clip_paths: list[str], output_path: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        concat_list_path = f.name
        for path in clip_paths:
            f.write(f"file '{Path(path).as_posix()}'\n")
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list_path,
                "-c", "copy",
                output_path,
            ],
            capture_output=True,
        )
        if result.returncode != 0:
            print(result.stderr.decode(errors="replace"), file=__import__("sys").stderr)
            result.check_returncode()
    finally:
        os.unlink(concat_list_path)


def stitch_clips_to_pipe(clip_paths: list[str]) -> subprocess.Popen:
    """Like stitch_clips but streams fragmented MP4 to stdout instead of writing a file.

    Returns a Popen object. Caller must:
    - Read proc.stdout (to consume the stream and avoid pipe buffer deadlock)
    - Drain proc.stderr in a separate thread (to prevent ffmpeg blocking on a full pipe)
    - Call proc.wait() after stdout is exhausted
    - Delete proc._concat_list_path (the temp concat list file) after proc.wait()
    """
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    concat_list_path = f.name
    for path in clip_paths:
        f.write(f"file '{Path(path).as_posix()}'\n")
    f.close()

    try:
        proc = subprocess.Popen(
            [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list_path,
                "-c", "copy",
                "-movflags", "frag_keyframe+empty_moov",
                "-f", "mp4",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except Exception:
        os.unlink(concat_list_path)
        raise
    proc._concat_list_path = concat_list_path
    return proc
