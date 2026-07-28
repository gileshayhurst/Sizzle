from unittest.mock import MagicMock

from encoder.asr.local import words


class FakeWord:
    def __init__(self, word, start, end):
        self.word, self.start, self.end = word, start, end


class FakeSegment:
    def __init__(self, fake_words):
        self.words = fake_words


def test_words_flattens_segments_to_a_word_stream():
    model = MagicMock()
    model.transcribe.return_value = (
        [FakeSegment([FakeWord(" He's", 1.0, 1.2), FakeWord(" a", 1.2, 1.35)]),
         FakeSegment([FakeWord(" Corgi", 1.35, 1.9)])],
        None,
    )
    assert words("video.webm", model=model) == [
        {"w": " He's", "s": 1.0, "e": 1.2},
        {"w": " a", "s": 1.2, "e": 1.35},
        {"w": " Corgi", "s": 1.35, "e": 1.9},
    ]


def test_words_requests_word_timestamps():
    model = MagicMock()
    model.transcribe.return_value = ([], None)
    words("video.webm", model=model)
    assert model.transcribe.call_args.kwargs["word_timestamps"] is True


def test_words_tolerates_a_segment_with_no_words():
    model = MagicMock()
    model.transcribe.return_value = ([FakeSegment(None)], None)
    assert words("video.webm", model=model) == []
