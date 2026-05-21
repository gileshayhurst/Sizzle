import argparse
import sys

from loader import load_transcripts
from claude_client import query_claude
from timestamp_parser import parse_timestamps


def main():
    parser = argparse.ArgumentParser(
        description="Generate a sizzle reel by finding relevant segments across video transcripts."
    )
    parser.add_argument("folder", help="Path to folder containing transcript .txt files")
    parser.add_argument("prompt", help="Topic to search for in the transcripts")
    args = parser.parse_args()

    try:
        transcripts = load_transcripts(args.folder)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    for filename, text in transcripts.items():
        try:
            response = query_claude(text, args.prompt)
            segments = parse_timestamps(response)
        except Exception as e:
            print(f"{filename}:  [warning: API error — {e}]", file=sys.stderr)
            continue

        if segments:
            print(f"{filename}:  {', '.join(segments)}")
        else:
            print(f"{filename}:  no relevant segments found")


if __name__ == "__main__":
    main()
