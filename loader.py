from pathlib import Path

_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def scan_videos(folder_path: str) -> list[Path]:
    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")
    files = sorted(f for f in folder.iterdir() if f.suffix.lower() in _VIDEO_EXTENSIONS)
    if not files:
        raise ValueError(f"No video files found in: {folder_path}")
    return files
