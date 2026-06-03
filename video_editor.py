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


def extract_clip(video_path: str, start_sec: float, end_sec: float, output_path: str) -> None:
    # Re-encode (never stream-copy) so every clip starts on an I-frame.
    # -ss before -i: fast input seek. -t duration (not -to) is relative to the
    # seek point. -avoid_negative_ts make_zero zeroes each clip's timestamps so
    # the concat demuxer sees clean zero-based PTS on every clip — prevents AV drift.
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", str(start_sec),
            "-i", video_path,
            "-t", str(end_sec - start_sec),
            "-avoid_negative_ts", "make_zero",
            "-c:v", "libx264",
            "-preset", "fast",
            "-c:a", "aac",
            output_path,
        ],
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
