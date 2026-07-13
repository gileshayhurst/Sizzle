import pytest
from pathlib import Path
from loader import scan_videos


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
