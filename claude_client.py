import anthropic

_client = anthropic.Anthropic()

_SYSTEM_PROMPT = """You are a transcript analyst. Given a timestamped video transcript and a topic prompt, identify the timestamp ranges where the speaker most directly and fully addresses the prompt topic.

Return ONLY the timestamp ranges in the format: M:SS-M:SS
If multiple segments, separate with commas: M:SS-M:SS, M:SS-M:SS
If no relevant segments exist, return exactly: none

Rules:
- Scan the entire transcript for all relevant mentions. Then return only the 2–4 most substantive and clearly relevant segments — the moments where the speaker most directly and fully addresses the topic. Do not return every passing mention.
- Start each range as late as possible — at the first word that speaks to the topic — and end it as early as possible, at the last word that directly contributes. Do not include surrounding context or lead-in sentences unless they are needed to make the statement intelligible.
- If the prompt asks for positive opinions, only return segments where the speaker's reaction is clearly positive or enthusiastic. Skip neutral mentions, passing references, and negative opinions even if the topic word appears.
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
