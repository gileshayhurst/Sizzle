import argparse
import os
import sys
import tempfile
from pathlib import Path

import whisper

from claude_client import query_claude
from loader import scan_videos
from timestamp_parser import parse_timestamps
from transcriber import transcribe_video
from video_editor import check_ffmpeg, extract_clip, parse_timestamp_to_seconds, stitch_clips


def main():
    parser = argparse.ArgumentParser(
        description="Generate a sizzle reel from relevant segments across video files."
    )
    parser.add_argument("folder", help="Path to folder containing video files")
    parser.add_argument("--prompt", nargs="+", required=True, help="Topic to search for in the videos")
    parser.add_argument(
        "--output", default=None,
        help="Output file path (default: sizzle_reel.<source_extension>)"
    )
    args = parser.parse_args()
    args.prompt = " ".join(args.prompt)

    try:
        check_ffmpeg()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        video_paths = scan_videos(args.folder)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.output is None:
        args.output = "sizzle_reel" + video_paths[0].suffix

    print("Loading Whisper model...", file=sys.stderr)
    whisper_model = whisper.load_model("base")

    video_segments: list[tuple[Path, list[str]]] = []

    for video_path in video_paths:
        transcript_path = video_path.with_suffix(".txt")

        if transcript_path.exists() and transcript_path.stat().st_size > 0:
            transcript = transcript_path.read_text(encoding="utf-8")
        else:
            print(f"Transcribing {video_path.name}...", file=sys.stderr)
            try:
                transcript = transcribe_video(str(video_path), model=whisper_model)
            except Exception as e:
                print(f"{video_path.name}: [warning: transcription failed — {e}]", file=sys.stderr)
                continue
            transcript_path.write_text(transcript, encoding="utf-8")

        try:
            response = query_claude(transcript, args.prompt)
            segments = parse_timestamps(response)
        except Exception as e:
            print(f"{video_path.name}: [warning: API error — {e}]", file=sys.stderr)
            continue

        if segments:
            print(f"{video_path.name}:  {', '.join(segments)}")
            video_segments.append((video_path, segments))
        else:
            print(f"{video_path.name}:  no relevant segments found")

    if not video_segments:
        print("No relevant segments found in any video. No output created.", file=sys.stderr)
        sys.exit(0)

    with tempfile.TemporaryDirectory() as tmp_dir:
        clip_paths = []
        clip_index = 0
        for video_path, segments in video_segments:
            for segment in segments:
                start_str, end_str = segment.split("-")
                start_sec = parse_timestamp_to_seconds(start_str)
                end_sec = parse_timestamp_to_seconds(end_str)
                clip_path = os.path.join(tmp_dir, f"clip_{clip_index:04d}{video_path.suffix}")
                try:
                    extract_clip(str(video_path), start_sec, end_sec, clip_path)
                    clip_paths.append(clip_path)
                    clip_index += 1
                except Exception as e:
                    print(
                        f"{video_path.name} [{segment}]: [warning: clip extraction failed — {e}]",
                        file=sys.stderr,
                    )

        if not clip_paths:
            print("No clips could be extracted. No output created.", file=sys.stderr)
            sys.exit(1)

        try:
            stitch_clips(clip_paths, args.output)
        except Exception as e:
            print(f"Error: stitching failed — {e}", file=sys.stderr)
            sys.exit(1)

    print(f"Sizzle reel saved to {args.output}")


if __name__ == "__main__":
    main()
