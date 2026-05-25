import os
import subprocess
from pathlib import Path


def write_transcript(path: str, content: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def generate_video(output_path: str, duration: int, color: str) -> None:
    pass


def create_test_data(output_dir: str) -> None:
    pass


if __name__ == "__main__":
    create_test_data("test_videos")
