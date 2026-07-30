"""python -m encoder <folder> -- encode every video that has a plain transcript.

Local testing and backfill entrypoint. Deliberately writes a `.rich.txt`
sidecar by default: the `.txt` beside a video is client data, and this tool
does not overwrite it without being asked.
"""
import argparse
import re
from pathlib import Path

from .asr.local import DEFAULT_MODEL_SIZE, words
from .core import encode

VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

_RICH_LINE_RE = re.compile(r"^\[\d+:\d{2}-\d+:\d{2}\]", re.MULTILINE)


def is_rich(text: str) -> bool:
    """True if the transcript already carries end timestamps."""
    return bool(_RICH_LINE_RE.search(text))


def encode_folder(folder, in_place: bool = False, size: str = DEFAULT_MODEL_SIZE,
                  log=lambda message: None) -> list[dict]:
    """Encode every video in `folder` that has a plain transcript beside it.

    Returns one {"video", "output", "stats"} per encoded video.
    """
    folder = Path(folder)
    results = []
    for video in sorted(p for p in folder.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES):
        plain = video.with_suffix(".txt")
        if not plain.exists():
            continue
        text = plain.read_text(encoding="utf-8-sig")
        if is_rich(text):
            log(f"skip {video.name}: already rich")
            continue

        log(f"encoding {video.name}")
        result = encode(text, words(video, size=size))

        # Nothing anchored: writing an empty transcript would be worse than
        # leaving the working plain one alone.
        if result["stats"]["emitted"] == 0:
            log(f"  no sentences anchored — leaving {plain.name} untouched")
            continue

        if in_place:
            plain.replace(video.with_suffix(".forven.txt"))
            output = plain
        else:
            output = video.with_suffix(".rich.txt")
        output.write_text(result["rich"] + "\n", encoding="utf-8")

        stats = result["stats"]
        dropped = f", {stats['dropped']} dropped" if stats["dropped"] else ""
        log(f"  {stats['emitted']} of {stats['sentences']} sentences{dropped}, "
            f"{stats['match_rate']:.1%} word match -> {output.name}")
        results.append({"video": video, "output": output, "stats": stats})
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m encoder", description=__doc__)
    parser.add_argument("folder", help="folder of videos with plain .txt transcripts")
    parser.add_argument("--in-place", action="store_true",
                        help="overwrite <video>.txt, preserving the original as <video>.forven.txt")
    parser.add_argument("--model", default=DEFAULT_MODEL_SIZE,
                        help=f"whisper model size (default: {DEFAULT_MODEL_SIZE})")
    args = parser.parse_args(argv)

    results = encode_folder(args.folder, in_place=args.in_place, size=args.model,
                            log=lambda message: print(message, flush=True))
    print(f"\nencoded {len(results)} video(s)")
    return 0
