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


def allows_endpoints(
    definition: RelationDefinition,
    from_type: str,
    to_type: str,
) -> bool:
    """Return whether the canonical direction accepts these endpoint types."""

    return (
        not definition.allowed_from_types or from_type in definition.allowed_from_types
    ) and (not definition.allowed_to_types or to_type in definition.allowed_to_types)


def has_endpoint_restrictions(definition: RelationDefinition) -> bool:
    return bool(definition.allowed_from_types or definition.allowed_to_types)


def valid_relation_labels(
    definitions: list[RelationDefinition] | tuple[RelationDefinition, ...],
    from_type: str,
    to_type: str,
) -> tuple[str, ...]:
    """Return every extractable label (canonical or inverse) valid for from -> to."""

    labels: list[str] = []
    for item in definitions:
        if not item.is_extractable:
            continue
        if allows_endpoints(item, from_type, to_type):
            labels.append(item.name)
        if not item.is_symmetric and allows_endpoints(item, to_type, from_type):
            labels.append(item.inverse_name)
    return tuple(dict.fromkeys(labels))


def repair_relation_endpoints(
    relation: RelationDefinition,
    definitions: list[RelationDefinition] | tuple[RelationDefinition, ...],
    from_type: str,
    to_type: str,
) -> tuple[RelationDefinition, bool] | None:
    """Find a replacement for a relation whose endpoints do not fit.

    Returns the relation to use and whether endpoints must be reversed. Tries the
    same relation reversed first, then the single type-restricted relation that
    fits these endpoint types. Unrestricted relations fit everything, so they are
    not counted; more than one restricted fit is too ambiguous to pick from.
    """

    if not relation.is_symmetric and allows_endpoints(relation, to_type, from_type):
        return relation, True

    fits: list[tuple[RelationDefinition, bool]] = []
    for item in definitions:
        if not item.is_extractable or not has_endpoint_restrictions(item):
            continue
        if allows_endpoints(item, from_type, to_type):
            fits.append((item, False))
        elif not item.is_symmetric and allows_endpoints(item, to_type, from_type):
            fits.append((item, True))
    return fits[0] if len(fits) == 1 else None


def _endpoint_label(types: tuple[NodeType, ...]) -> str:
    return "|".join(types) if types else "any"


def relation_catalog_prompt(
    definitions: list[RelationDefinition] | tuple[RelationDefinition, ...],
) -> str:
    lines = [
        "Allowed relation types, as canonical (from -> to) / inverse (from -> to). "
        "A relation type is the edge's structured identity; put finer meaning in "
        "the edge's details. A label may only connect the node types shown for "
        "it; \"any\" accepts every node type:"
    ]
    for item in definitions:
        if not item.is_extractable:
            continue
        from_label = _endpoint_label(item.allowed_from_types)
        to_label = _endpoint_label(item.allowed_to_types)
        if item.is_symmetric:
            signature = f"{item.name} ({from_label} <-> {to_label})"
        else:
            signature = (
                f"{item.name} ({from_label} -> {to_label}) / "
                f"{item.inverse_name} ({to_label} -> {from_label})"
            )
        lines.append(f"- {signature}: {item.description}")
    return "\n".join(lines)
