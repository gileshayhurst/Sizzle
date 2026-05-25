from pathlib import Path
from unittest.mock import patch
from create_test_data import write_transcript, generate_video, create_test_data


def test_write_transcript_creates_file(tmp_path):
    path = tmp_path / "test.txt"
    write_transcript(str(path), "[0:00] Speaker: Hello.")
    assert path.exists()


def test_write_transcript_writes_correct_content(tmp_path):
    content = "[0:00] Speaker: Hello.\n[0:05] Speaker: World."
    path = tmp_path / "test.txt"
    write_transcript(str(path), content)
    assert path.read_text(encoding="utf-8") == content


def test_write_transcript_creates_parent_dirs(tmp_path):
    path = tmp_path / "subfolder" / "nested" / "test.txt"
    write_transcript(str(path), "content")
    assert path.exists()


def test_generate_video_calls_ffmpeg(tmp_path):
    output = str(tmp_path / "test.mp4")
    with patch("create_test_data.subprocess.run") as mock_run:
        generate_video(output, duration=30, color="0x3a7d44")
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert output in cmd
        assert "30" in cmd


def test_generate_video_includes_color_in_ffmpeg_args(tmp_path):
    output = str(tmp_path / "test.mp4")
    with patch("create_test_data.subprocess.run") as mock_run:
        generate_video(output, duration=10, color="0xc0392b")
        cmd = mock_run.call_args[0][0]
        assert any("0xc0392b" in arg for arg in cmd)


def test_generate_video_skips_existing_file(tmp_path):
    output = tmp_path / "test.mp4"
    output.touch()
    with patch("create_test_data.subprocess.run") as mock_run:
        generate_video(str(output), duration=30, color="0x3a7d44")
        mock_run.assert_not_called()


def test_create_test_data_creates_all_folders(tmp_path):
    with patch("create_test_data.generate_video"):
        create_test_data(str(tmp_path))
    expected = [
        "riverside_grocery", "bella_vista_restaurant", "iron_fitness_gym",
        "lakeview_hotel", "morning_grounds_cafe",
    ]
    for folder in expected:
        assert (tmp_path / folder).is_dir()


def test_create_test_data_creates_transcript_files(tmp_path):
    with patch("create_test_data.generate_video"):
        create_test_data(str(tmp_path))
    assert (tmp_path / "riverside_grocery" / "sarah_k.txt").exists()
    assert (tmp_path / "bella_vista_restaurant" / "carlos_m.txt").exists()
    assert (tmp_path / "iron_fitness_gym" / "alex_j.txt").exists()
    assert (tmp_path / "lakeview_hotel" / "mark_s.txt").exists()
    assert (tmp_path / "morning_grounds_cafe" / "sophia_r.txt").exists()


def test_create_test_data_transcript_content_nonempty(tmp_path):
    with patch("create_test_data.generate_video"):
        create_test_data(str(tmp_path))
    content = (tmp_path / "riverside_grocery" / "sarah_k.txt").read_text(encoding="utf-8")
    assert "[0:00]" in content
    assert "Speaker:" in content


def test_create_test_data_calls_generate_video_for_each_respondent(tmp_path):
    with patch("create_test_data.generate_video") as mock_gen:
        create_test_data(str(tmp_path))
    assert mock_gen.call_count == 23
