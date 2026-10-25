"""Pure calculation helpers for edges. Edge values only: no I/O, no nodes.

Shared by extraction, ingest apply, and node merges, which all fold a repeated
relationship into one edge.
"""

from __future__ import annotations

from math import prod


def combine_probabilities(values: list[float]) -> float:
    """Combine independent supporting signals with diminishing returns.

    Used for edge confidence and strength as evidence accumulates.
    """

    return 1.0 - prod(1.0 - value for value in values)


def combine_edge_details(existing: str | None, incoming: str | None) -> str | None:
    """Accumulate edge details across mentions without repeating known facts.

    Details never affect edge identity, so a repeated relationship keeps one edge
    whose details grow as new sources qualify it.
    """

    existing = " ".join(existing.split()) if existing else None
    incoming = " ".join(incoming.split()) if incoming else None
    if not incoming:
        return existing or None
    if not existing:
        return incoming
    if _normalize(incoming) in _normalize(existing):
        return existing
    if _normalize(existing) in _normalize(incoming):
        return incoming
    return f"{existing}; {incoming}"


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())
