import anthropic

_client = anthropic.Anthropic()

_SYSTEM_PROMPT = """You are a transcript analyst. Given a timestamped video transcript and a topic prompt, identify every timestamp range where the speaker addresses or meaningfully mentions the prompt topic.

Return ONLY the timestamp ranges in the format: M:SS-M:SS
If multiple segments, separate with commas: M:SS-M:SS, M:SS-M:SS
If no relevant segments exist, return exactly: none

Rules:
- Scan the entire transcript. Return every range where the topic is addressed, not just the most prominent one.
- Start each range as late as possible — at the first word that speaks to the topic — and end it as early as possible, at the last word that directly contributes. Do not include surrounding context or lead-in sentences unless they are needed to make the statement intelligible.
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
