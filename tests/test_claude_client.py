from unittest.mock import MagicMock, patch
import claude_client
from claude_client import query_claude


def _make_mock_response(text: str) -> MagicMock:
    mock_content = MagicMock()
    mock_content.text = text
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    return mock_response


def test_returns_string_from_claude():
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("0:23-1:05")
        result = query_claude("[0:23] Speaker: Hello", "hospitality")
    assert result == "0:23-1:05"


def _user_blocks(mock_client) -> list:
    call_kwargs = mock_client.messages.create.call_args.kwargs
    return call_kwargs["messages"][0]["content"]


def test_sends_transcript_in_user_message():
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("my transcript text", "my prompt")
        blocks = _user_blocks(mock_client)
    joined = "".join(b["text"] for b in blocks)
    assert "my transcript text" in joined


def test_sends_prompt_in_user_message():
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("some transcript", "hospitality of waiters")
        blocks = _user_blocks(mock_client)
    joined = "".join(b["text"] for b in blocks)
    assert "hospitality of waiters" in joined


def test_returns_none_string_when_no_match():
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        result = query_claude("[0:05] Speaker: Parking is hard to find.", "hospitality")
    assert result == "none"


def test_sends_system_prompt():
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("t", "p")
        call_kwargs = mock_client.messages.create.call_args.kwargs
    assert "system" in call_kwargs
    assert "transcript analyst" in call_kwargs["system"]


def test_system_prompt_instructs_respondent_only():
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("t", "p")
        system = mock_client.messages.create.call_args.kwargs["system"]
    assert "interviewer" in system.lower()


def test_transcript_block_is_cached_and_prompt_block_is_not():
    """Transcript block carries the cache breakpoint; the prompt block must
    come after it (varying content after the breakpoint) and carry none."""
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("long transcript", "topic prompt")
        blocks = _user_blocks(mock_client)
    assert len(blocks) == 2
    assert "long transcript" in blocks[0]["text"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "topic prompt" in blocks[1]["text"]
    assert "cache_control" not in blocks[1]


def test_rich_tier_appends_rich_clause_to_system_prompt():
    """query_claude with tier='rich' sends a system prompt that mentions end timestamps."""
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("transcript", "prompt", tier="rich")
        system = mock_client.messages.create.call_args.kwargs["system"]
    assert "end timestamp" in system.lower() or "start and end" in system.lower()


def test_plain_tier_system_prompt_unchanged():
    """query_claude with default tier='plain' sends the same system prompt as before."""
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("transcript", "prompt")
        system_plain = mock_client.messages.create.call_args.kwargs["system"]
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("transcript", "prompt", tier="plain")
        system_explicit_plain = mock_client.messages.create.call_args.kwargs["system"]
    assert system_plain == system_explicit_plain


def test_rich_system_prompt_is_superset_of_plain():
    """Rich system prompt contains all of the plain prompt's instructions."""
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("t", "p")
        plain_system = mock_client.messages.create.call_args.kwargs["system"]
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("t", "p", tier="rich")
        rich_system = mock_client.messages.create.call_args.kwargs["system"]
    assert plain_system in rich_system
