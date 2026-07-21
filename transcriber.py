import math


def _seconds_to_timestamp(seconds: float) -> str:
    total = int(seconds)
    m = total // 60
    s = total % 60
    return f"{m}:{s:02d}"


def _seconds_to_timestamp_ceil(seconds: float) -> str:
    """Round UP to the next whole second.

    Used for END timestamps only. Truncating an end moves it earlier, which
    clips the speaker's final word — the defect this format exists to remove.
    Starts still truncate (earlier is safe lead-in).
    """
    total = int(math.ceil(seconds - 1e-9))
    return f"{total // 60}:{total % 60:02d}"


def _split_into_sentences(segment: dict) -> list[tuple[float, float | None, str]]:
    """Split a Whisper segment with word timestamps into sentence-level (start, end, text) triples.

    Each sentence carries the start time of its first word and the end time of
    its last word, giving Claude sub-segment precision when choosing clip
    boundaries. Falls back to segment-level start/end if no word timestamps
    are present.
    """
    words = segment.get("words", [])
    if not words:
        return [(segment["start"], segment.get("end"), segment["text"].strip())]

    sentences: list[tuple[float, float, str]] = []
    sentence_start = words[0]["start"]
    sentence_words: list[str] = []

    for i, word in enumerate(words):
        word_text = word["word"]
        sentence_words.append(word_text)
        if word_text.rstrip().endswith((".", "!", "?")):
            sentence = "".join(sentence_words).strip()
            if sentence:
                sentences.append((sentence_start, word["end"], sentence))
            sentence_start = words[i + 1]["start"] if i + 1 < len(words) else sentence_start
            sentence_words = []

    # Flush any remaining words that didn't end with terminal punctuation
    if sentence_words:
        sentence = "".join(sentence_words).strip()
        if sentence:
            sentences.append((sentence_start, words[-1]["end"], sentence))

    return sentences


def _segment_to_dict(segment) -> dict:
    """Adapt a faster-whisper Segment object to the dict shape _split_into_sentences expects."""
    words = []
    if segment.words:
        words = [{"word": w.word, "start": w.start, "end": w.end} for w in segment.words]
    return {"start": segment.start, "end": segment.end, "text": segment.text, "words": words}


def transcribe_video(video_path: str, model=None) -> str:
    if model is None:
        from faster_whisper import WhisperModel
        model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(video_path, word_timestamps=True)
    lines = []
    for segment in segments:
        seg_dict = _segment_to_dict(segment)
        for start, end, text in _split_into_sentences(seg_dict):
            ts = _seconds_to_timestamp(start)
            if end is None:
                lines.append(f"[{ts}] Speaker: {text}")
            else:
                lines.append(f"[{ts}-{_seconds_to_timestamp_ceil(end)}] Speaker: {text}")
    return "\n".join(lines)
