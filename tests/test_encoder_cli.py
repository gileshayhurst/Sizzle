from unittest.mock import patch

from encoder.cli import encode_folder, is_rich

WORDS = [
    {"w": "He's", "s": 13.4, "e": 13.7},
    {"w": "a", "s": 13.7, "e": 13.9},
    {"w": "Corgi", "s": 13.9, "e": 14.4},
    {"w": "mix.", "s": 14.4, "e": 15.2},
]
TRANSCRIPT = "[00:13] Participant: He's a Corgi mix."


def _folder(tmp_path):
    (tmp_path / "interview.mp4").write_bytes(b"fake")
    (tmp_path / "interview.txt").write_text(TRANSCRIPT, encoding="utf-8")
    return tmp_path


def test_is_rich_detects_end_timestamps():
    assert is_rich("[0:13-0:15] Participant: Hello.")
    assert not is_rich("[0:13] Participant: Hello.")


def test_encode_folder_writes_a_rich_sidecar(tmp_path):
    folder = _folder(tmp_path)
    with patch("encoder.cli.words", return_value=WORDS):
        results = encode_folder(folder)
    assert (folder / "interview.rich.txt").read_text(encoding="utf-8").startswith(
        "[0:13-0:16] Participant:"
    )
    assert results[0]["stats"]["sentences"] == 1


def test_encode_folder_leaves_the_original_untouched(tmp_path):
    """The .txt on disk is client data -- never rewritten without --in-place."""
    folder = _folder(tmp_path)
    with patch("encoder.cli.words", return_value=WORDS):
        encode_folder(folder)
    assert (folder / "interview.txt").read_text(encoding="utf-8") == TRANSCRIPT


def test_encode_folder_in_place_preserves_the_original_as_forven_txt(tmp_path):
    folder = _folder(tmp_path)
    with patch("encoder.cli.words", return_value=WORDS):
        encode_folder(folder, in_place=True)
    assert (folder / "interview.forven.txt").read_text(encoding="utf-8") == TRANSCRIPT
    assert is_rich((folder / "interview.txt").read_text(encoding="utf-8"))


def test_encode_folder_leaves_the_original_when_nothing_anchors(tmp_path):
    """A failed encode must not replace a working transcript with an empty file."""
    folder = _folder(tmp_path)
    with patch("encoder.cli.words", return_value=[{"w": "unrelated", "s": 1.0, "e": 2.0}]):
        results = encode_folder(folder, in_place=True)
    assert results == []
    assert (folder / "interview.txt").read_text(encoding="utf-8") == TRANSCRIPT
    assert not (folder / "interview.forven.txt").exists()


def test_encode_folder_skips_videos_with_no_transcript(tmp_path):
    (tmp_path / "orphan.mp4").write_bytes(b"fake")
    with patch("encoder.cli.words", return_value=WORDS) as asr:
        results = encode_folder(tmp_path)
    assert results == []
    asr.assert_not_called()


def test_encode_folder_skips_already_rich_transcripts(tmp_path):
    (tmp_path / "done.mp4").write_bytes(b"fake")
    (tmp_path / "done.txt").write_text("[0:13-0:15] Participant: Hi.", encoding="utf-8")
    with patch("encoder.cli.words", return_value=WORDS) as asr:
        results = encode_folder(tmp_path)
    assert results == []
    asr.assert_not_called()
