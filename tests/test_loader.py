import pytest
from pathlib import Path
from loader import load_transcripts, scan_videos


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
    with pytest.raises(ValueError, match=r"No \.txt files found"):
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


def test_scan_videos_returns_sorted_path_list(tmp_path):
    (tmp_path / "b.mp4").touch()
    (tmp_path / "a.mp4").touch()
    result = scan_videos(str(tmp_path))
    assert [p.name for p in result] == ["a.mp4", "b.mp4"]


def test_scan_videos_supports_all_extensions(tmp_path):
    (tmp_path / "clip.mp4").touch()
    (tmp_path / "clip.mov").touch()
    (tmp_path / "clip.avi").touch()
    (tmp_path / "clip.mkv").touch()
    result = scan_videos(str(tmp_path))
    assert len(result) == 4


def test_scan_videos_ignores_non_video_files(tmp_path):
    (tmp_path / "video.mp4").touch()
    (tmp_path / "notes.txt").write_text("notes")
    (tmp_path / "doc.pdf").touch()
    result = scan_videos(str(tmp_path))
    assert len(result) == 1
    assert result[0].name == "video.mp4"


def test_scan_videos_raises_file_not_found_on_missing_folder():
    with pytest.raises(FileNotFoundError, match="Folder not found"):
        scan_videos("/nonexistent/path/that/does/not/exist")


def test_scan_videos_raises_value_error_on_no_video_files(tmp_path):
    (tmp_path / "notes.txt").write_text("notes")
    with pytest.raises(ValueError, match="No video files found"):
        scan_videos(str(tmp_path))


def test_scan_videos_returns_path_objects(tmp_path):
    (tmp_path / "video.mp4").touch()
    result = scan_videos(str(tmp_path))
    assert all(isinstance(p, Path) for p in result)
