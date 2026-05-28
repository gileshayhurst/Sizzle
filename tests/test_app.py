import os
from unittest.mock import patch, MagicMock
from pathlib import Path

import pytest
from app import create_app


@pytest.fixture
def client():
    app = create_app(testing=True)
    with app.test_client() as c:
        yield c


def test_index_returns_200(client):
    resp = client.get("/")
    assert resp.status_code == 200


def test_index_returns_html(client):
    resp = client.get("/")
    assert b"<!DOCTYPE html>" in resp.data or b"<html" in resp.data


def test_load_folder_returns_video_list(client, tmp_path):
    (tmp_path / "video1.mp4").touch()
    (tmp_path / "video2.mp4").touch()
    (tmp_path / "notes.txt").write_text("[0:01] Speaker: hi", encoding="utf-8")
    resp = client.post("/load-folder", json={"folder": str(tmp_path)})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "job_id" in data
    assert set(data["files"]) == {"video1.mp4", "video2.mp4"}


def test_load_folder_missing_folder_returns_404(client):
    resp = client.post("/load-folder", json={"folder": "/nonexistent/folder/xyz"})
    assert resp.status_code == 404


def test_load_folder_no_videos_returns_422(client, tmp_path):
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    resp = client.post("/load-folder", json={"folder": str(tmp_path)})
    assert resp.status_code == 422
