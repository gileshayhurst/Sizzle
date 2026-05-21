import pytest
from pathlib import Path
from loader import load_transcripts


def test_returns_dict_of_filename_to_text(tmp_path):
    (tmp_path / "video1.txt").write_text("[0:10] Speaker 1: Hello", encoding="utf-8")
    (tmp_path / "video2.txt").write_text("[0:05] Speaker 2: Hi", encoding="utf-8")
    result = load_transcripts(str(tmp_path))
    assert set(result.keys()) == {"video1.txt", "video2.txt"}
    assert "Hello" in result["video1.txt"]
    assert "Hi" in result["video2.txt"]


def test_raises_file_not_found_on_missing_folder():
    with pytest.raises(FileNotFoundError, match="Folder not found"):
        load_transcripts("/nonexistent/path/that/does/not/exist")


def test_raises_value_error_on_no_txt_files(tmp_path):
    (tmp_path / "notes.md").write_text("some notes")
    with pytest.raises(ValueError, match="No .txt files found"):
        load_transcripts(str(tmp_path))


def test_ignores_non_txt_files(tmp_path):
    (tmp_path / "video1.txt").write_text("[0:10] Speaker 1: Hello", encoding="utf-8")
    (tmp_path / "notes.md").write_text("some notes")
    result = load_transcripts(str(tmp_path))
    assert list(result.keys()) == ["video1.txt"]


def test_files_sorted_alphabetically(tmp_path):
    (tmp_path / "b_video.txt").write_text("b content", encoding="utf-8")
    (tmp_path / "a_video.txt").write_text("a content", encoding="utf-8")
    result = load_transcripts(str(tmp_path))
    assert list(result.keys()) == ["a_video.txt", "b_video.txt"]
