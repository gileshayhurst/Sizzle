from encoder.core.reconcile import align, normalize


def words(*triples):
    return [{"w": w, "s": s, "e": e} for w, s, e in triples]


def test_normalize_strips_punctuation_and_case():
    assert normalize(" Corgi,") == "corgi"
    assert normalize("don't") == "don't"
    assert normalize("...") == ""


def test_align_exact_match_takes_first_and_last_word_times():
    sentences = [{"role": "Participant", "text": "He's a Corgi mix."}]
    stream = words(("He's", 1.0, 1.2), ("a", 1.2, 1.3), ("Corgi", 1.3, 1.8), ("mix.", 1.8, 2.4))
    aligned, match_rate = align(sentences, stream)
    assert aligned[0]["start"] == 1.0
    assert aligned[0]["end"] == 2.4
    assert aligned[0]["anchor"] == "exact"
    assert aligned[0]["confidence"] == 1.0
    assert match_rate == 1.0


def test_align_tolerates_asr_errors_and_still_anchors():
    """One mis-transcribed word must not cost the sentence its boundaries."""
    sentences = [{"role": "Participant", "text": "He's a Corgi mix."}]
    stream = words(("He's", 1.0, 1.2), ("a", 1.2, 1.3), ("Corky", 1.3, 1.8), ("mix.", 1.8, 2.4))
    aligned, match_rate = align(sentences, stream)
    assert aligned[0]["start"] == 1.0
    assert aligned[0]["end"] == 2.4
    assert aligned[0]["anchor"] == "exact"
    assert aligned[0]["confidence"] == 0.75
    assert match_rate == 0.75


def test_align_two_sentences_get_separate_spans():
    sentences = [
        {"role": "Participant", "text": "Yeah."},
        {"role": "Participant", "text": "He sleeps a lot."},
    ]
    stream = words(
        ("Yeah.", 0.5, 1.0),
        ("He", 2.0, 2.2), ("sleeps", 2.2, 2.6), ("a", 2.6, 2.7), ("lot.", 2.7, 3.1),
    )
    aligned, _ = align(sentences, stream)
    assert (aligned[0]["start"], aligned[0]["end"]) == (0.5, 1.0)
    assert (aligned[1]["start"], aligned[1]["end"]) == (2.0, 3.1)


def test_align_unanchored_sentence_falls_back_after_previous_end():
    sentences = [
        {"role": "Participant", "text": "Yeah."},
        {"role": "Participant", "text": "Totally inaudible mumbling here."},
    ]
    stream = words(("Yeah.", 0.5, 1.0))
    aligned, _ = align(sentences, stream)
    assert aligned[1]["anchor"] == "none"
    assert aligned[1]["start"] == 1.0
    assert aligned[1]["end"] > aligned[1]["start"]
    assert aligned[1]["confidence"] == 0.0


def test_align_role_and_text_pass_through_untouched():
    sentences = [{"role": "Interviewer", "text": "Hello there."}]
    aligned, _ = align(sentences, words(("Hello", 0.0, 0.4)))
    assert aligned[0]["role"] == "Interviewer"
    assert aligned[0]["text"] == "Hello there."


def test_align_empty_inputs():
    aligned, match_rate = align([], [])
    assert aligned == []
    assert match_rate == 0.0


def test_align_sentence_with_no_usable_tokens():
    aligned, _ = align([{"role": "Participant", "text": "..."}], words(("Hi", 0.0, 0.5)))
    assert aligned[0]["anchor"] == "none"
