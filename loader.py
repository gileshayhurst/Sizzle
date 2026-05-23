from pathlib import Path

_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def load_transcripts(folder_path: str) -> dict[str, str]:
    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")
    files = sorted(folder.glob("*.txt"))
    if not files:
        raise ValueError(f"No .txt files found in: {folder_path}")
    return {f.name: f.read_text(encoding="utf-8") for f in files}


def scan_videos(folder_path: str) -> list[Path]:
    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")
    files = sorted(f for f in folder.iterdir() if f.suffix.lower() in _VIDEO_EXTENSIONS)
    if not files:
        raise ValueError(f"No video files found in: {folder_path}")
    return files
