"""Helpers to consume model output safely."""
import json
from typing import Any

from app.ai import errors as E


def parse_json(text: str, provider: str = "") -> Any:
    """Parse a JSON answer that may be wrapped in markdown fences or surrounded by prose.
    Raises AIError(INVALID_RESPONSE) (no model text in the message) when nothing parses."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except (TypeError, ValueError):
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = cleaned.find(opener), cleaned.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except ValueError:
                continue
    raise E.AIError(E.INVALID_RESPONSE, "answer is not valid JSON", provider=provider or None)
