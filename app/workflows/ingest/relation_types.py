"""Relation catalog DTOs and transformations; PostgreSQL owns the catalog data."""

from __future__ import annotations

from dataclasses import dataclass

from app.workflows.ingest.types import NodeType


@dataclass(frozen=True, slots=True)
class RelationDefinition:
    name: str
    inverse_name: str
    description: str
    connector: str = ""
    past_connector: str = ""
    future_connector: str = ""
    is_symmetric: bool = False
    is_exclusive: bool = False
    can_change_over_time: bool = False
    allowed_from_types: tuple[NodeType, ...] = ()
    allowed_to_types: tuple[NodeType, ...] = ()
    is_extractable: bool = True
    parent_name: str | None = None


def validate_relation_definitions(
    definitions: list[RelationDefinition] | tuple[RelationDefinition, ...],
) -> None:
    """Fail fast when database catalog rows cannot define one edge identity."""

    names = [item.name for item in definitions]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        raise ValueError(f"Duplicate relation names: {sorted(duplicates)}")

    primary_names = set(names)
    for item in definitions:
        if item.is_symmetric and item.inverse_name != item.name:
            raise ValueError(
                f"{item.name!r} is symmetric but has inverse {item.inverse_name!r}."
            )
        if not item.is_symmetric and item.inverse_name == item.name:
            raise ValueError(
                f"{item.name!r} is asymmetric but uses itself as its inverse."
            )
        if not item.is_symmetric and item.inverse_name in primary_names:
            raise ValueError(
                f"Inverse {item.inverse_name!r} is also a canonical relation name."
            )
        if item.parent_name == item.name:
            raise ValueError(f"Relation {item.name!r} cannot be its own parent.")
        if item.parent_name is not None and item.parent_name not in primary_names:
            raise ValueError(
                f"Parent {item.parent_name!r} of relation {item.name!r} "
                "is not in the active catalog."
            )

    definitions_by_name = {item.name: item for item in definitions}
    for item in definitions:
        path: list[str] = []
        current: RelationDefinition | None = item
        while current is not None:
            if current.name in path:
                cycle = " -> ".join([*path, current.name])
                raise ValueError(f"Relation hierarchy contains a cycle: {cycle}.")
            path.append(current.name)
            current = (
                definitions_by_name[current.parent_name]
                if current.parent_name is not None
                else None
            )


def relation_ancestors(
    name: str,
    definitions_by_name: dict[str, RelationDefinition],
) -> tuple[str, ...]:
    """Return every broader relation of name, nearest parent first."""

    ancestors: list[str] = []
    current = definitions_by_name.get(name)
    while current is not None and current.parent_name is not None:
        if current.parent_name in ancestors or current.parent_name == name:
            break
        ancestors.append(current.parent_name)
        current = definitions_by_name.get(current.parent_name)
    return tuple(ancestors)


def is_more_specific_relation(
    *,
    proposed_name: str,
    existing_name: str,
    definitions: list[RelationDefinition] | tuple[RelationDefinition, ...],
) -> bool:
    """Return whether proposed_name is a strict descendant of existing_name."""

    definitions_by_name = {item.name: item for item in definitions}
    return existing_name in relation_ancestors(proposed_name, definitions_by_name)


def extractable_relation_types(
    definitions: list[RelationDefinition] | tuple[RelationDefinition, ...],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            label
            for item in definitions
            if item.is_extractable
            for label in (item.name, item.inverse_name)
        )
    )


def canonicalize_relation_type(
    relation_type: str,
    definitions: list[RelationDefinition] | tuple[RelationDefinition, ...],
) -> tuple[RelationDefinition, bool] | None:
    """Return the canonical definition and whether endpoints must be reversed."""

    for item in definitions:
        if relation_type == item.name:
            return item, False
        if relation_type == item.inverse_name:
            return item, not item.is_symmetric
    return None


def relation_catalog_prompt(
    definitions: list[RelationDefinition] | tuple[RelationDefinition, ...],
) -> str:
    lines = [
        "Allowed relation types (canonical / inverse). A relation type is the "
        "edge's structured identity; put finer meaning in the edge's details:"
    ]
    lines.extend(
        f"- {item.name} / {item.inverse_name}: {item.description}"
        for item in definitions
        if item.is_extractable
    )
    return "\n".join(lines)
