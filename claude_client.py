import anthropic

_client = anthropic.Anthropic()

_SYSTEM_PROMPT = """You are a transcript analyst. Given a timestamped video transcript and a topic prompt, identify the most compelling short moments where the speaker directly and substantively addresses the prompt topic, and rate how compelling each one is.

Return ONLY one segment per line in the format: M:SS-M:SS|N
where N is an integer from 1 to 10 rating how compelling the evidence is.
If no relevant segments exist, return exactly: none

Score rubric:
- 9-10: direct, vivid, quotable evidence — the strongest possible moment on the topic.
- 7-8: clearly on-topic and substantive.
- 5-6: relevant but ordinary.
- 1-4: passing mention.

Rules:
- Scan the entire transcript and return EVERY genuinely relevant segment, each with its score. Do not limit the count. Do not return passing mentions dressed up as strong evidence — score them low instead.
- The subject of each segment must be the primary item named in the prompt — not something served alongside it, contextually adjacent to it, or containing it as a minor ingredient. For example, if the prompt is about fish, exclude miso soup segments even at a sushi restaurant, even if the broth contains fish stock. Before selecting a segment ask: "Is the speaker directly evaluating the exact subject the prompt names?" If the answer is no, skip it.
- Each range must be a single, tight, self-contained statement — the "money quote" — not a whole on-topic stretch. Aim for roughly 5–12 seconds. When a speaker stays on-topic for a long span, do NOT return the entire span; return only the most compelling sentence or two within it. Prefer several short, punchy ranges over one long one.
- Start each range as late as possible — at the first word that speaks to the topic — and end it as early as possible, at the last word that directly contributes. Do not include surrounding context or lead-in sentences unless they are needed to make the statement intelligible.
- If the prompt asks for positive opinions, only return segments where the speaker's reaction is clearly positive or enthusiastic. Skip neutral mentions, passing references, and negative opinions even if the topic word appears.
- Only use timestamps that appear verbatim in the transcript
- The transcript may label speakers (e.g. "Interviewer:", "Agent:", "Participant:"). Only return ranges spoken by the respondent/participant. Never return a range where the interviewer, agent, or moderator is speaking, even if the topic word appears in their question.
- Do not fabricate or infer timestamps
- Do not include any explanation, preamble, or extra punctuation — just the scored segments, one per line, or the word none"""


def query_claude(transcript: str, prompt: str) -> str:
    message = _client.messages.create(
        model="claude-opus-4-8",
        max_tokens=256,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        # Stable prefix: cached across repeated analyzes of the
                        # same folder (additive analyze re-sends this verbatim).
                        "type": "text",
                        "text": f"Transcript:\n{transcript}",
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        # Varying suffix: must stay after the breakpoint.
                        "type": "text",
                        "text": f"\n\nPrompt: {prompt}",
                    },
                ],
            }
        ]
    )
    return message.content[0].text
