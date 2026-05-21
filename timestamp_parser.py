import re


def parse_timestamps(response: str) -> list[str] | None:
    response = response.strip()
    if response.lower() == "none":
        return None
    pattern = r'\d+:\d{2}-\d+:\d{2}'
    matches = re.findall(pattern, response)
    return matches if matches else None
