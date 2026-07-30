from unittest.mock import patch

import pytest

from encoder.job import encode_one, find_pairs, run

SESSION = "sessions/2f8a1c40-1111-2222-3333-444455556666"
PLAIN = "[00:13] Participant: He's a Corgi mix."
RICH = "[0:13-0:16] Participant: He's a Corgi mix."
WORDS = [
    {"w": "He's", "s": 13.4, "e": 13.7},
    {"w": "a", "s": 13.7, "e": 13.9},
    {"w": "Corgi", "s": 13.9, "e": 14.4},
    {"w": "mix.", "s": 14.4, "e": 15.2},
]


# ── find_pairs ────────────────────────────────────────────────────────────────

def test_find_pairs_matches_video_to_same_stem_transcript():
    keys = [f"{SESSION}/a.mp4", f"{SESSION}/a.txt", f"{SESSION}/b.webm", f"{SESSION}/b.txt"]
    assert find_pairs(keys) == [
        (f"{SESSION}/a.mp4", f"{SESSION}/a.txt"),
        (f"{SESSION}/b.webm", f"{SESSION}/b.txt"),
    ]


def test_find_pairs_skips_video_with_no_transcript():
    assert find_pairs([f"{SESSION}/orphan.mp4"]) == []


def test_find_pairs_ignores_non_video_keys():
    keys = [f"{SESSION}/notes.txt", f"{SESSION}/reel.json"]
    assert find_pairs(keys) == []


def test_find_pairs_does_not_pair_a_forven_sidecar_as_a_video():
    """<stem>.forven.txt must never be mistaken for a transcript of a video."""
    keys = [f"{SESSION}/a.mp4", f"{SESSION}/a.txt", f"{SESSION}/a.forven.txt"]
    assert find_pairs(keys) == [(f"{SESSION}/a.mp4", f"{SESSION}/a.txt")]


# ── encode_one ────────────────────────────────────────────────────────────────

def test_encode_one_writes_rich_and_preserves_the_original():
    uploads = {}
    with patch("encoder.job.r2.read_text", return_value=PLAIN), \
         patch("encoder.job.r2.download"), \
         patch("encoder.job.r2.upload_text", side_effect=lambda k, t: uploads.__setitem__(k, t)), \
         patch("encoder.job.words", return_value=WORDS), \
         patch("encoder.job._model"):
        stats = encode_one(f"{SESSION}/a.mp4", f"{SESSION}/a.txt", "tiny", log=lambda m: None)

    assert stats["sentences"] == 1
    assert uploads[f"{SESSION}/a.txt"].startswith(RICH)
    assert uploads[f"{SESSION}/a.forven.txt"] == PLAIN


def test_encode_one_skips_an_already_rich_transcript():
    with patch("encoder.job.r2.read_text", return_value=RICH), \
         patch("encoder.job.r2.download") as dl, \
         patch("encoder.job.r2.upload_text") as up, \
         patch("encoder.job.words") as asr:
        assert encode_one(f"{SESSION}/a.mp4", f"{SESSION}/a.txt", "tiny", log=lambda m: None) is None
    dl.assert_not_called()
    asr.assert_not_called()
    up.assert_not_called()


def test_encode_one_deletes_its_temp_file_even_when_asr_fails():
    seen = {}
    with patch("encoder.job.r2.read_text", return_value=PLAIN), \
         patch("encoder.job.r2.download", side_effect=lambda k, p: seen.__setitem__("path", p)), \
         patch("encoder.job.r2.upload_text"), \
         patch("encoder.job.words", side_effect=RuntimeError("boom")), \
         patch("encoder.job._model"):
        with pytest.raises(RuntimeError):
            encode_one(f"{SESSION}/a.mp4", f"{SESSION}/a.txt", "tiny", log=lambda m: None)

    import os
    assert not os.path.exists(seen["path"]), "temp video must not be left behind"


# ── run ───────────────────────────────────────────────────────────────────────

def test_run_rejects_a_suspicious_session_key():
    """The key reaches a command line, so it is validated, not trusted."""
    for bad in ["sessions/../../etc", "sessions/x; rm -rf /", "library/foo", "sessions/"]:
        with pytest.raises(ValueError):
            run(bad)


def test_run_encodes_every_pair_in_the_session():
    keys = [f"{SESSION}/a.mp4", f"{SESSION}/a.txt", f"{SESSION}/b.mp4", f"{SESSION}/b.txt"]
    with patch("encoder.job.r2.list_keys", return_value=keys), \
         patch("encoder.job.encode_one", return_value={"sentences": 1}) as enc:
        results = run(SESSION, workers=2, log=lambda m: None)
    assert len(results) == 2
    assert enc.call_count == 2


def test_run_survives_one_failing_interview():
    """A bad interview must not cost the rest of the folder."""
    keys = [f"{SESSION}/a.mp4", f"{SESSION}/a.txt", f"{SESSION}/b.mp4", f"{SESSION}/b.txt"]

    def flaky(video_key, *args, **kwargs):
        if video_key.endswith("a.mp4"):
            raise RuntimeError("corrupt")
        return {"sentences": 7}

    with patch("encoder.job.r2.list_keys", return_value=keys), \
         patch("encoder.job.encode_one", side_effect=flaky):
        results = run(SESSION, workers=2, log=lambda m: None)

    assert len(results) == 1
    assert results[0]["stats"]["sentences"] == 7


def test_run_with_no_interviews_does_nothing():
    with patch("encoder.job.r2.list_keys", return_value=[]), \
         patch("encoder.job.encode_one") as enc:
        assert run(SESSION, log=lambda m: None) == []
    enc.assert_not_called()
