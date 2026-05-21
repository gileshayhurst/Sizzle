import anthropic

_client = anthropic.Anthropic()

_SYSTEM_PROMPT = """You are a transcript analyst. Given a timestamped video transcript and a topic prompt, identify the timestamp ranges where the speaker directly addresses the prompt topic.

Return ONLY the timestamp ranges in the format: M:SS-M:SS
If multiple segments, separate with commas: M:SS-M:SS, M:SS-M:SS
If no relevant segments exist, return exactly: none

Rules:
- Only include segments that directly address the prompt topic, not tangential mentions
- Only use timestamps that appear verbatim in the transcript
- Do not fabricate or infer timestamps
- Do not include any explanation, preamble, or punctuation — just the timestamps or the word none"""


def query_claude(transcript: str, prompt: str) -> str:
    message = _client.messages.create(
        model="claude-opus-4-7",
        max_tokens=256,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Transcript:\n{transcript}\n\nPrompt: {prompt}"
            }
        ]
    )
    return message.content[0].text
