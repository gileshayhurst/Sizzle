import whisper


def _seconds_to_timestamp(seconds: float) -> str:
    total = int(seconds)
    m = total // 60
    s = total % 60
    return f"{m}:{s:02d}"


def _split_into_sentences(segment: dict) -> list[tuple[float, str]]:
    """Split a Whisper segment with word timestamps into sentence-level (start, text) pairs.

    Each sentence gets the start time of its first word, giving Claude sub-segment
    precision when choosing clip start points. Falls back to segment-level if no
    word timestamps are present.
    """
    words = segment.get("words", [])
    if not words:
        return [(segment["start"], segment["text"].strip())]

    sentences: list[tuple[float, str]] = []
    sentence_start = words[0]["start"]
    sentence_words: list[str] = []

    for i, word in enumerate(words):
        word_text = word["word"]
        sentence_words.append(word_text)
        if word_text.rstrip().endswith((".", "!", "?")):
            sentence = "".join(sentence_words).strip()
            if sentence:
                sentences.append((sentence_start, sentence))
            sentence_start = words[i + 1]["start"] if i + 1 < len(words) else sentence_start
            sentence_words = []

    # Flush any remaining words that didn't end with terminal punctuation
    if sentence_words:
        sentence = "".join(sentence_words).strip()
        if sentence:
            sentences.append((sentence_start, sentence))

    return sentences


def transcribe_video(video_path: str, model=None) -> str:
    if model is None:
        model = whisper.load_model("base")
    result = model.transcribe(video_path, word_timestamps=True)
    lines = []
    for segment in result["segments"]:
        for start, text in _split_into_sentences(segment):
            ts = _seconds_to_timestamp(start)
            lines.append(f"[{ts}] Speaker: {text}")
    return "\n".join(lines)
