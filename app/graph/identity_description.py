"""Readable identity descriptions shared by indexing and resolution.

This is canonical graph language rather than projection workflow behavior: the
rebuild worker renders stored identity documents with it, while ingestion uses
the same description for symmetric identity comparisons.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any


NODE_TYPE_LABELS = {
    "person": "Person",
    "organization": "Organization",
    "location": "Location",
    "event": "Event",
    "document": "Document",
    "date": "Date",
    "task": "Task",
    "concept": "Concept",
}

PROVIDER_LABELS = {
    "google_contacts": "Google Contacts",
    "gmail": "Gmail",
    "slack": "Slack",
    "calendar": "Calendar",
    "manual_import": "a manual import",
}

_CONTACT_PROVIDERS = {"email", "phone", "instagram"}


def render_identity_description(
    *,
    name: str,
    node_type: str,
    aliases: Sequence[str] = (),
    external_providers: Sequence[str] = (),
    type_specific: dict[str, Any] | None = None,
    graph_facts: Sequence[str] = (),
) -> str:
    """Render the embedding document used for identity comparisons."""

    name = _clean(name)
    aliases = [
        alias
        for alias in _dedupe(_clean(value) for value in aliases)
        if alias.casefold() != name.casefold()
    ][:10]

    sentences = [f"{name}."]
    if aliases:
        sentences.append(f"Also referred to as {', '.join(aliases)}.")

    label = NODE_TYPE_LABELS.get(node_type, _humanize(node_type))
    descriptor = label
    identity_facts = _identity_facts(node_type, type_specific or {})
    if identity_facts:
        descriptor = f"{descriptor} {identity_facts}"

    providers = _dedupe(
        PROVIDER_LABELS.get(provider.casefold(), _humanize(provider))
        for provider in external_providers
        if provider.casefold() not in _CONTACT_PROVIDERS
    )
    if providers:
        descriptor = f"{descriptor} from {_join_list(providers)}"
    sentences.append(f"{descriptor}.")
    sentences.extend(f"{fact.rstrip('.')}." for fact in graph_facts if fact.strip())
    return " ".join(sentences)


def _identity_facts(node_type: str, details: dict[str, Any]) -> str:
    if node_type == "location" and details.get("address"):
        return f"at {details['address']}"
    if node_type == "date":
        rendered = _render_date_detail(details)
        return f"for {rendered}" if rendered else ""
    if node_type == "task" and details.get("due_at"):
        rendered = _render_datetime(details["due_at"])
        return f"due {rendered}" if rendered else ""
    return ""


def _render_date_detail(details: dict[str, Any]) -> str:
    precision = details.get("precision") or "day"
    if precision == "range":
        start = _render_datetime(details.get("start_at"))
        end = _render_datetime(details.get("end_at"))
        if start and end:
            return f"{start} to {end}"
        return start or end
    if precision == "datetime" and details.get("start_at"):
        return _render_datetime(details["start_at"])
    value = details.get("date_value")
    if not isinstance(value, str):
        return ""
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        return ""
    if precision == "year":
        return str(parsed.year)
    if precision == "month":
        return f"{parsed:%B} {parsed.year}"
    return f"{parsed:%B} {parsed.day}, {parsed.year}"


def _render_datetime(value: Any) -> str:
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


def _clean(value: str) -> str:
    return " ".join(value.split())


def _humanize(value: str) -> str:
    return value.replace("_", " ").strip()


def _dedupe(values: Iterable[str]) -> list[str]:
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


def _join_list(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"
