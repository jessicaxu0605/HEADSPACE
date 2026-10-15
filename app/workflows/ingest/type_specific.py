"""Per-node-type detail fields stored in ``memory_node.type_specific``.

Design V1 describes these as ``person_profile``, ``location_detail``,
``date_detail``, and ``task_detail``. Until those tables exist, the fields live in
the ``type_specific`` jsonb column and are validated here. Node types without an
entry in ``TYPE_SPECIFIC_FIELDS`` keep whatever extraction produced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Literal, TypeAlias

from app.workflows.ingest.types import JsonObject, ProposedExternalId, ProposedNode


FieldKind: TypeAlias = Literal[
    "text",
    "email",
    "phone",
    "handle",
    "date",
    "datetime",
    "number",
    "enum",
]


@dataclass(frozen=True, slots=True)
class TypeSpecificField:
    name: str
    kind: FieldKind
    description: str
    choices: tuple[str, ...] = ()
    # False for fields that need a lookup service rather than the source text.
    extractable: bool = True
    # Shown on the node page. False when the value only restates the node's
    # title (a date node's date) or is internal (coordinates, place IDs).
    display: bool = False
    label: str = ""
    # Set for fields that identify the node. They are extracted here but stored
    # only as external IDs, never in type_specific, so a node can have several.
    external_id_provider: str | None = None


# Names are not type-specific fields: the title and node identifiers hold them.
TYPE_SPECIFIC_FIELDS: dict[str, tuple[TypeSpecificField, ...]] = {
    "person": (
        TypeSpecificField(
            "email", "email", "Email address.", external_id_provider="email"
        ),
        TypeSpecificField(
            "phone_number",
            "phone",
            "Phone number, digits with optional leading +.",
            external_id_provider="phone",
        ),
        TypeSpecificField(
            "instagram",
            "handle",
            "Instagram handle without the @.",
            external_id_provider="instagram",
        ),
        TypeSpecificField(
            "pronouns",
            "text",
            "Pronouns, such as she/her.",
            display=True,
            label="pronouns",
        ),
        TypeSpecificField(
            "relationship_context",
            "text",
            "How the author knows this person, in a few words.",
            display=True,
            label="how you know them",
        ),
    ),
    "location": (
        TypeSpecificField(
            "address",
            "text",
            "Street address as stated.",
            display=True,
            label="address",
        ),
        TypeSpecificField("latitude", "number", "Latitude.", extractable=False),
        TypeSpecificField("longitude", "number", "Longitude.", extractable=False),
        TypeSpecificField(
            "google_place_id", "text", "Google Place ID.", extractable=False
        ),
    ),
    "date": (
        TypeSpecificField(
            "date_value",
            "date",
            "YYYY-MM-DD. For month or year precision use the first day of the "
            "month or year.",
        ),
        TypeSpecificField(
            "precision",
            "enum",
            "How precise the date is.",
            choices=("day", "month", "year", "datetime", "range"),
        ),
        TypeSpecificField(
            "start_at",
            "datetime",
            "ISO 8601 start, for datetime or range precision.",
        ),
        TypeSpecificField("end_at", "datetime", "ISO 8601 end, for range precision."),
    ),
    "task": (
        TypeSpecificField(
            "due_at", "datetime", "ISO 8601 deadline.", display=True, label="due"
        ),
        TypeSpecificField(
            "completed_at",
            "datetime",
            "ISO 8601 completion time.",
            display=True,
            label="completed",
        ),
        TypeSpecificField(
            "urgency",
            "enum",
            "Stated urgency.",
            choices=("low", "medium", "high"),
            display=True,
            label="urgency",
        ),
        TypeSpecificField(
            "task_status",
            "enum",
            "Current status.",
            choices=("todo", "in_progress", "blocked", "done", "archived"),
            display=True,
            label="status",
        ),
    ),
}

_EMAIL_PATTERN = re.compile(
    r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}$",
    re.IGNORECASE,
)
_HANDLE_PATTERN = re.compile(r"^[a-z0-9._]{1,30}$")


def is_email(value: str) -> bool:
    return _EMAIL_PATTERN.fullmatch(value.strip()) is not None


def has_type_specific_fields(node_type: str) -> bool:
    return node_type in TYPE_SPECIFIC_FIELDS


def normalize_type_specific(
    node_type: str,
    values: JsonObject | None,
) -> JsonObject | None:
    """Keep known stored fields with valid values, normalized for comparison.

    Identity fields stored as external IDs are dropped. Returns None when nothing valid remains. Node types without a field catalog
    are returned unchanged.
    """

    if values is None or not has_type_specific_fields(node_type):
        return values
    normalized: JsonObject = {}
    for spec in TYPE_SPECIFIC_FIELDS[node_type]:
        if spec.external_id_provider is not None:
            continue
        value = _normalize_value(spec, values.get(spec.name))
        if value is not None:
            normalized[spec.name] = value
    return normalized or None


def fill_type_specific(
    existing: JsonObject | None,
    incoming: JsonObject | None,
) -> tuple[JsonObject, dict[str, dict[str, Any]]]:
    """Return incoming fields missing from existing, and conflicting fields.

    Existing canonical values are never overwritten; a different incoming value
    is reported as a conflict for review instead.
    """

    existing = existing or {}
    patch: JsonObject = {}
    conflicts: dict[str, dict[str, Any]] = {}
    for key, value in (incoming or {}).items():
        if value is None:
            continue
        if existing.get(key) is None:
            patch[key] = value
        elif existing[key] != value:
            conflicts[key] = {"existing": existing[key], "incoming": value}
    return patch, conflicts


def type_specific_identity_key(
    node_type: str,
    values: JsonObject | None,
) -> str | None:
    """Return a key that two nodes of this type share only if they are the same.

    Matching keys identify the same node; different keys block resolution even
    when names look alike (``March 3`` and ``March 4``).
    """

    values = normalize_type_specific(node_type, values) or {}
    if node_type == "date":
        precision = values.get("precision") or "day"
        if precision == "range":
            if values.get("start_at") and values.get("end_at"):
                return f"range:{values['start_at']}/{values['end_at']}"
            return None
        if precision == "datetime" and values.get("start_at"):
            return f"datetime:{values['start_at']}"
        date_value = values.get("date_value")
        if not date_value:
            return None
        width = {"year": 4, "month": 7}.get(precision, 10)
        return f"{precision}:{date_value[:width]}"
    if node_type == "location" and values.get("google_place_id"):
        return f"google_place:{values['google_place_id']}"
    return None


def type_specific_external_ids(
    node_type: str,
    values: JsonObject | None,
) -> list[ProposedExternalId]:
    """Return the identity fields in values (email, phone...) as external IDs."""

    external_ids: list[ProposedExternalId] = []
    for spec in TYPE_SPECIFIC_FIELDS.get(node_type, ()):
        if spec.external_id_provider is None or values is None:
            continue
        value = _normalize_value(spec, values.get(spec.name))
        if value is not None:
            external_ids.append(ProposedExternalId(spec.external_id_provider, value))
    return external_ids


def with_type_specific_identity(node: ProposedNode) -> ProposedNode:
    """Move identity fields into external IDs and normalize the rest."""

    external_ids = list(node.external_ids)
    known = {
        (item.provider.casefold(), item.external_id.casefold())
        for item in external_ids
    }
    for item in type_specific_external_ids(node.node_type, node.type_specific):
        if (item.provider, item.external_id.casefold()) not in known:
            external_ids.append(item)
            known.add((item.provider, item.external_id.casefold()))
    return replace(
        node,
        type_specific=normalize_type_specific(node.node_type, node.type_specific),
        external_ids=external_ids,
    )


def displayable_type_specific(
    node_type: str,
    values: JsonObject | None,
) -> list[tuple[TypeSpecificField, Any]]:
    """Return the fields worth showing on the node page, in catalog order."""

    values = normalize_type_specific(node_type, values) or {}
    return [
        (spec, values[spec.name])
        for spec in TYPE_SPECIFIC_FIELDS.get(node_type, ())
        if spec.display and spec.name in values
    ]


def _normalize_value(spec: TypeSpecificField, value: Any) -> Any:
    if value is None:
        return None
    if spec.kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    if spec.kind == "text":
        return text
    if spec.kind == "email":
        return text.casefold() if is_email(text) else None
    if spec.kind == "phone":
        digits = re.sub(r"\D", "", text)
        if len(digits) < 7:
            return None
        return f"+{digits}" if text.startswith("+") else digits
    if spec.kind == "handle":
        handle = text.removeprefix("@").casefold()
        return handle if _HANDLE_PATTERN.fullmatch(handle) else None
    if spec.kind == "enum":
        choice = text.casefold().replace(" ", "_")
        return choice if choice in spec.choices else None
    if spec.kind == "date":
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            return None
    if spec.kind == "datetime":
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return None
    raise AssertionError(f"Unhandled field kind {spec.kind!r}")
