from encoder.core import encode


def test_encode_end_to_end_splits_a_turn_into_rich_sentences():
    transcript = "[00:13] Participant: He's a Corgi mix. We got him as a rescue."
    words = [
        {"w": "He's", "s": 13.4, "e": 13.7},
        {"w": "a", "s": 13.7, "e": 13.9},
        {"w": "Corgi", "s": 13.9, "e": 14.4},
        {"w": "mix.", "s": 14.4, "e": 15.2},
        {"w": "We", "s": 16.0, "e": 16.2},
        {"w": "got", "s": 16.2, "e": 16.5},
        {"w": "him", "s": 16.5, "e": 16.7},
        {"w": "as", "s": 16.7, "e": 16.9},
        {"w": "a", "s": 16.9, "e": 17.0},
        {"w": "rescue.", "s": 17.0, "e": 17.8},
    ]
    result = encode(transcript, words)
    assert result["rich"].splitlines() == [
        "[0:13-0:16] Participant: He's a Corgi mix.",
        "[0:16-0:18] Participant: We got him as a rescue.",
    ]
    assert result["stats"]["sentences"] == 2
    assert result["stats"]["exact"] == 2
    assert result["stats"]["match_rate"] == 1.0


def test_encode_preserves_interviewer_role_for_downstream_exclusion():
    """shared.is_interviewer_label keys on this label, so it must survive."""
    result = encode("[00:00] Interviewer: Hello there.",
                    [{"w": "Hello", "s": 0.0, "e": 0.4}, {"w": "there.", "s": 0.4, "e": 0.9}])
    assert result["rich"] == "[0:00-0:01] Interviewer: Hello there."


def test_encode_empty_transcript():
    result = encode("", [])
    assert result["rich"] == ""
    assert result["stats"]["sentences"] == 0


def test_encode_drops_a_sentence_the_asr_never_matched():
    """An unanchored sentence has an interpolated span — it must not be emitted."""
    transcript = "[00:13] Participant: He's a Corgi mix. Totally inaudible mumbling."
    words = [
        {"w": "He's", "s": 13.4, "e": 13.7},
        {"w": "a", "s": 13.7, "e": 13.9},
        {"w": "Corgi", "s": 13.9, "e": 14.4},
        {"w": "mix.", "s": 14.4, "e": 15.2},
    ]
    result = encode(transcript, words)
    assert result["rich"] == "[0:13-0:16] Participant: He's a Corgi mix."
    assert result["stats"]["sentences"] == 2
    assert result["stats"]["emitted"] == 1
    assert result["stats"]["dropped"] == 1


def test_encode_emits_nothing_when_nothing_anchors():
    """Callers check emitted == 0 rather than overwrite a working transcript."""
    result = encode("[00:13] Participant: Nothing here matches.",
                    [{"w": "unrelated", "s": 1.0, "e": 1.5}])
    assert result["rich"] == ""
    assert result["stats"]["emitted"] == 0


def test_encode_repairs_glued_punctuation_in_output():
    transcript = "[00:13] Participant: Um,he's lazy."
    words = [
        {"w": "Um,", "s": 13.2, "e": 13.5},
        {"w": "he's", "s": 13.5, "e": 13.8},
        {"w": "lazy.", "s": 13.8, "e": 14.6},
    ]
    assert encode(transcript, words)["rich"] == "[0:13-0:15] Participant: Um, he's lazy."
