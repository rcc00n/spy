import re


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def build_match_preview(text: str, phrase: str, radius: int = 120) -> str:
    normalized = normalize_text(text)
    lowered = normalized.lower()
    phrase_lower = phrase.lower()
    index = lowered.find(phrase_lower)
    if index == -1:
        return normalized[: radius * 2]

    start = max(index - radius, 0)
    end = min(index + len(phrase) + radius, len(normalized))
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(normalized) else ""
    return f"{prefix}{normalized[start:end]}{suffix}"


def keyword_matches(text: str, keywords) -> list[tuple[object, str]]:
    normalized = normalize_text(text)
    lowered = normalized.lower()
    matches = []
    for keyword in keywords:
        phrase = normalize_text(keyword.phrase)
        if phrase and phrase.lower() in lowered:
            matches.append((keyword, build_match_preview(normalized, phrase)))
    return matches
