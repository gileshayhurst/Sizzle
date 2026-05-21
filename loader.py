from pathlib import Path


def load_transcripts(folder_path: str) -> dict[str, str]:
    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")
    files = sorted(folder.glob("*.txt"))
    if not files:
        raise ValueError(f"No .txt files found in: {folder_path}")
    return {f.name: f.read_text(encoding="utf-8") for f in files}
