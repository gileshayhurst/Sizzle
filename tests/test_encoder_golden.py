"""Golden test against a real Forven interview.

Pins the result measured during the design spike: 48 sentences, 47 with both
boundaries taken from matched ASR words. Asserts on shape and counts rather
than exact text so a wording change does not break the suite, while a real
regression in splitting, alignment, or rounding does.
"""
import json
import re
from pathlib import Path

import pytest

from encoder.core import encode

FIXTURES = Path(__file__).parent / "fixtures"
RICH_LINE_RE = re.compile(r"^\[(\d+):(\d{2})-(\d+):(\d{2})\] (\w[\w ]*): .+$")


@pytest.fixture
def result():
    transcript = (FIXTURES / "encoder_forven_interview.txt").read_text(encoding="utf-8-sig")
    words = json.loads((FIXTURES / "encoder_whisper_words.json").read_text(encoding="utf-8"))
    return encode(transcript, words)


def _seconds(minutes, secs):
    return int(minutes) * 60 + int(secs)


def test_golden_sentence_and_anchor_counts(result):
    assert result["stats"]["sentences"] == 48
    assert result["stats"]["exact"] >= 47
    assert result["stats"]["match_rate"] >= 0.90


def test_golden_every_line_is_valid_rich_format(result):
    lines = result["rich"].splitlines()
    assert len(lines) == 48
    for line in lines:
        assert RICH_LINE_RE.match(line), line


def test_golden_every_line_has_positive_duration(result):
    for line in result["rich"].splitlines():
        m = RICH_LINE_RE.match(line)
        assert _seconds(m.group(3), m.group(4)) > _seconds(m.group(1), m.group(2)), line


def test_golden_starts_are_non_decreasing(result):
    starts = [
        _seconds(*RICH_LINE_RE.match(line).group(1, 2))
        for line in result["rich"].splitlines()
    ]
    assert starts == sorted(starts)


def test_golden_both_roles_survive(result):
    roles = {RICH_LINE_RE.match(line).group(5) for line in result["rich"].splitlines()}
    assert roles == {"Interviewer", "Participant"}


def test_golden_output_is_rich_tier_to_the_consuming_app(result):
    """The whole point: shared.py must classify this as rich."""
    from shared import parse_transcript_lines, transcript_tier
    assert transcript_tier(parse_transcript_lines(result["rich"])) == "rich"
