"""Text helpers and the embedder port used by projection rebuilds."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol


class DocumentEmbedder(Protocol):
    model: str

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...


def article(word: str) -> str:
    return "an" if word[:1] in "aeiou" else "a"


def as_sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else f"{text}."


def join_list(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def humanize(value: str) -> str:
    return value.replace("_", " ").strip()


def clean(value: str) -> str:
    return " ".join(value.split())


def truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    cut = value[:limit].rsplit(" ", 1)[0]
    return f"{cut}…"


def dedupe(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value:
            continue
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def render_datetime(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    rendered = f"{parsed:%B} {parsed.day}, {parsed.year}"
    if (parsed.hour, parsed.minute) != (0, 0):
        rendered = f"{rendered} {parsed:%H:%M}"
    return rendered
