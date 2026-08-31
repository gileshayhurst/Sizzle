"""Cut a reel from an aligned interview, locally, with ffmpeg.

Selects participant moments from the aligned transcript, extracts each as its
own clip, and concatenates them. Extraction RE-ENCODES: the tempting `-c copy`
produces clips that start mid-GOP and freeze on playback.

    python cut_local.py sessions/<uuid> <REF> [n_clips]

Prints timings and durations only - never transcript content.
"""

import re
import subprocess
import sys
from pathlib import Path

LINE = re.compile(r"^\[(?P<start>[\d:]+)-(?P<end>[\d:]+)\]\s*(?P<role>[^:]{1,24}):\s*(?P<text>.*)$")
MIN_CLIP_SECONDS = 3.0


def to_seconds(stamp: str) -> float:
    parts = [int(p) for p in stamp.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    hours, minutes, seconds = parts[-3:]
    return float(hours * 3600 + minutes * 60 + seconds)


def parse(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = LINE.match(line.strip())
        if not match:
            continue
        start = to_seconds(match.group("start"))
        end = to_seconds(match.group("end"))
        rows.append({"start": start, "end": end, "role": match.group("role").strip(),
                     "text": match.group("text")})
    return rows


def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr[-800:])
        raise SystemExit(f"ffmpeg failed: {' '.join(cmd[:6])}...")


def main(argv=None):
    argv = argv or sys.argv[1:]
    if len(argv) < 2:
        print(__doc__)
        return 2

    session_key, ref = argv[0], argv[1]
    want = int(argv[2]) if len(argv) > 2 else 3

    root = Path(__file__).parent / session_key
    video = root / f"{ref}.mp4"
    aligned = root / f"{ref}.txt"

    rows = parse(aligned)
    candidates = [r for r in rows
                  if r["role"].lower().startswith("participant")
                  and (r["end"] - r["start"]) >= MIN_CLIP_SECONDS]
    if not candidates:
        print("no participant lines long enough to cut")
        return 1

    # Longest answers first, then back into transcript order so the reel reads
    # in sequence rather than by length.
    chosen = sorted(candidates, key=lambda r: r["end"] - r["start"], reverse=True)[:want]
    chosen.sort(key=lambda r: r["start"])

    print(f"{len(rows)} aligned lines, {len(candidates)} usable participant answers")
    print(f"selected {len(chosen)} clip(s):")
    work = root / "cut"
    work.mkdir(exist_ok=True)
    parts = []
    total = 0.0

    for index, row in enumerate(chosen):
        duration = row["end"] - row["start"]
        total += duration
        print(f"   clip {index}: {row['start']:.0f}s -> {row['end']:.0f}s  ({duration:.0f}s)")
        out = work / f"clip{index}.mp4"
        run(["ffmpeg", "-y", "-ss", str(row["start"]), "-i", str(video),
             "-t", str(duration),
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-ar", "44100",
             "-avoid_negative_ts", "make_zero", str(out)])
        parts.append(out)

    listing = work / "parts.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")

    reel = root / f"{ref}_reel.mp4"
    # The parts are already uniform (same codec/params), so concat may copy.
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c", "copy", str(reel)])

    print(f"\nreel: {reel.name}  {reel.stat().st_size / 1_000_000:.1f} MB  ~{total:.0f}s")
    print(f"source_interview_refs must be exactly: ['{ref}']")
    return 0


if __name__ == "__main__":
    sys.exit(main())
