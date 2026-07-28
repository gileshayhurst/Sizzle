import io
from unittest.mock import patch

import pytest

from encoder.service import create_app

TRANSCRIPT = "[00:13] Participant: He's a Corgi mix."
WORDS = [
    {"w": "He's", "s": 13.4, "e": 13.7},
    {"w": "a", "s": 13.7, "e": 13.9},
    {"w": "Corgi", "s": 13.9, "e": 14.4},
    {"w": "mix.", "s": 14.4, "e": 15.2},
]


@pytest.fixture
def client():
    return create_app(testing=True).test_client()


def test_health(client):
    assert client.get("/health").get_json() == {"ok": True}


def test_encode_words_returns_rich_and_stats(client):
    response = client.post("/encode/words", json={"transcript": TRANSCRIPT, "words": WORDS})
    assert response.status_code == 200
    body = response.get_json()
    assert body["rich"] == "[0:13-0:16] Participant: He's a Corgi mix."
    assert body["stats"]["exact"] == 1


def test_encode_words_rejects_missing_transcript(client):
    response = client.post("/encode/words", json={"words": WORDS})
    assert response.status_code == 400
    assert "transcript" in response.get_json()["error"]


def test_encode_words_rejects_non_list_words(client):
    response = client.post("/encode/words", json={"transcript": TRANSCRIPT, "words": "nope"})
    assert response.status_code == 400
    assert "words" in response.get_json()["error"]


def test_encode_words_rejects_malformed_word_entries(client):
    response = client.post("/encode/words",
                           json={"transcript": TRANSCRIPT, "words": [{"w": "hi"}]})
    assert response.status_code == 400


def test_encode_audio_runs_asr_then_the_same_core(client):
    with patch("encoder.service.words", return_value=WORDS) as asr:
        response = client.post(
            "/encode",
            data={"transcript": TRANSCRIPT,
                  "audio": (io.BytesIO(b"fake audio"), "align.opus")},
            content_type="multipart/form-data",
        )
    assert response.status_code == 200
    assert response.get_json()["rich"] == "[0:13-0:16] Participant: He's a Corgi mix."
    asr.assert_called_once()


def test_encode_audio_rejects_missing_audio(client):
    response = client.post("/encode", data={"transcript": TRANSCRIPT},
                           content_type="multipart/form-data")
    assert response.status_code == 400
    assert "audio" in response.get_json()["error"]
