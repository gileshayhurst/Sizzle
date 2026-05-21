from unittest.mock import MagicMock, patch
from claude_client import query_claude


def _make_mock_response(text: str) -> MagicMock:
    mock_content = MagicMock()
    mock_content.text = text
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    return mock_response


def test_returns_string_from_claude():
    with patch("claude_client.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = _make_mock_response("0:23-1:05")
        result = query_claude("[0:23] Speaker: Hello", "hospitality")
    assert result == "0:23-1:05"


def test_sends_transcript_in_user_message():
    with patch("claude_client.anthropic.Anthropic") as mock_anthropic:
        mock_client = mock_anthropic.return_value
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("my transcript text", "my prompt")
        call_kwargs = mock_client.messages.create.call_args[1]
        user_content = call_kwargs["messages"][0]["content"]
    assert "my transcript text" in user_content


def test_sends_prompt_in_user_message():
    with patch("claude_client.anthropic.Anthropic") as mock_anthropic:
        mock_client = mock_anthropic.return_value
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("some transcript", "hospitality of waiters")
        call_kwargs = mock_client.messages.create.call_args[1]
        user_content = call_kwargs["messages"][0]["content"]
    assert "hospitality of waiters" in user_content


def test_returns_none_string_when_no_match():
    with patch("claude_client.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = _make_mock_response("none")
        result = query_claude("[0:05] Speaker: Parking is hard to find.", "hospitality")
    assert result == "none"
