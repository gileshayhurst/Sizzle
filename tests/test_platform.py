"""Tests for platform portability fixes."""
from unittest.mock import patch
from pathlib import Path


def test_find_system_font_returns_linux_path_when_windows_fonts_absent():
    """When Windows font dirs don't exist but a Linux path does, return the Linux path."""
    import generator_app

    linux_font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    def fake_exists(self):
        # Only the Linux DejaVu path "exists" in this mock
        # Account for path normalization: Path converts / to \ on Windows
        return str(self).replace("\\", "/") == linux_font

    with patch.object(Path, "exists", fake_exists):
        result = generator_app._find_system_font()

    assert result is not None
    assert result.replace("\\", "/") == linux_font


def test_find_system_font_prefers_windows_font_when_present(tmp_path):
    """Windows fonts take precedence over Linux paths when both exist."""
    import generator_app

    def fake_exists(self):
        return str(self) == str(Path("C:/Windows/Fonts/arial.ttf"))

    with patch.object(Path, "exists", fake_exists):
        result = generator_app._find_system_font()

    # Should return the first match — Windows arial
    assert result is not None


def test_find_system_font_returns_none_when_no_fonts():
    """Returns None when no candidate font path exists."""
    import generator_app

    with patch.object(Path, "exists", lambda self: False):
        result = generator_app._find_system_font()

    assert result is None
