import subprocess
from pathlib import Path


def write_transcript(path: str, content: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def generate_video(output_path: str, duration: int, color: str) -> None:
    if Path(output_path).exists():
        return
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:size=640x480:rate=24",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            "-t", str(duration),
            output_path,
        ],
        check=True,
        capture_output=True,
    )


def create_test_data(output_dir: str) -> None:
    pass


if __name__ == "__main__":
    create_test_data("test_videos")
