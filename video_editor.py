import os
import subprocess
import tempfile


def check_ffmpeg() -> None:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True)
    except FileNotFoundError:
        raise RuntimeError(
            "ffmpeg not found. Install it with:\n"
            "  Windows: winget install ffmpeg\n"
            "  Mac: brew install ffmpeg"
        )


def parse_timestamp_to_seconds(ts: str) -> float:
    parts = ts.split(":")
    return float(int(parts[0]) * 60 + int(parts[1]))


def extract_clip(video_path: str, start_sec: float, end_sec: float, output_path: str) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_path,
            "-ss", str(start_sec),
            "-to", str(end_sec),
            "-c", "copy",
            output_path,
        ],
        check=True,
        capture_output=True,
    )


def stitch_clips(clip_paths: list[str], output_path: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        concat_list_path = f.name
        for path in clip_paths:
            f.write(f"file '{path}'\n")
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list_path,
                "-c", "copy",
                output_path,
            ],
            check=True,
            capture_output=True,
        )
    finally:
        os.unlink(concat_list_path)
