"""Tests for POST /upload and config injection in app.py."""
import pytest
from unittest.mock import patch


@pytest.fixture
def client():
    from app import create_app
    app = create_app(testing=True)
    with app.test_client() as c:
        yield c


def test_index_injects_generator_url(client, monkeypatch):
    """GET / should include window.__CONFIG__ with the configured generator URL."""
    monkeypatch.setenv("GENERATOR_URL", "https://my-generator.onrender.com")
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "window.__CONFIG__" in html
    assert "https://my-generator.onrender.com" in html


def test_index_injects_default_generator_url_when_env_absent(client, monkeypatch):
    """When GENERATOR_URL is not set, the default localhost:5001 is injected."""
    monkeypatch.delenv("GENERATOR_URL", raising=False)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "localhost:5001" in html


def test_index_injects_app_mode(client, monkeypatch):
    """GET / should inject the APP_MODE into window.__CONFIG__."""
    monkeypatch.setenv("APP_MODE", "cloud")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "cloud" in resp.data.decode()
