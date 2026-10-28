"""Build ``node_search_projection`` text and vectors for canonical nodes.

Design V1 ("Build projections") gives each node two projections:

- ``identity`` answers "what thing is this?": title, node type, identifiers,
  external IDs in abstracted form, stable type-specific identity fields, and the
  strongest identity edges (``is_a``, ``part_of``, ``located_in``).
- ``profile`` answers "what is this thing associated with?": summary, important
  edges as readable sentences, and changeable type-specific facts.

Each projection has a ``search_text`` (compact keywords for lexical search) and
an ``embedding_text`` (prose for the embedding model). Text building is pure so
it can be tested without a database or Ollama.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol
from uuid import UUID

from app.graph.identity_description import render_identity_description
from app.projection_contract import NODE_PROJECTION_VERSION, ProjectionType
from app.workflows.rebuild_projections.common import (
    DocumentEmbedder,
    article,
    as_sentence,
    clean,
    dedupe,
    humanize,
    join_list,
    render_datetime,
    truncate,
)


MAX_IDENTIFIERS = 10
MAX_IDENTITY_EDGES = 3
MAX_PROFILE_EDGES = 15
MAX_DETAILS_CHARS = 160
MAX_SUMMARY_CHARS = 600

IDENTITY_RELATIONS = ("is_a", "part_of", "located_in")

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

# Person contact external IDs. Their raw values go into search_text only;
# instagram also appears as an @handle alias.
_CONTACT_PROVIDERS = {"email", "phone", "instagram"}


@dataclass(frozen=True, slots=True)
class ProjectionIdentifier:
    text: str
    scope: str = "global"
    confidence: float = 0.5


@dataclass(frozen=True, slots=True)
class ProjectionExternalId:
    provider: str
    external_id: str
    confidence: float = 0.5


@dataclass(frozen=True, slots=True)
class ProjectionEdge:
    """A non-deleted edge touching the node, seen from the node's side."""

    edge_id: UUID
    relation_type: str
    # Tense-appropriate phrase placed between canonical endpoints: A {connector} B.
    connector: str
    # True when the projected node is the canonical ``from`` endpoint.
    outgoing: bool
    neighbor_name: str
    neighbor_type: str
    strength: float
    confidence: float
    details: str | None = None


@dataclass(frozen=True, slots=True)
class NodeProjectionSource:
    """Everything the builder reads for one canonical node."""

    node_id: UUID
    node_type: str
    name: str
    summary: str | None = None
    type_specific: dict[str, Any] | None = None
    identifiers: list[ProjectionIdentifier] = field(default_factory=list)
    external_ids: list[ProjectionExternalId] = field(default_factory=list)
    edges: list[ProjectionEdge] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class NodeProjectionText:
    projection_type: ProjectionType
    search_text: str
    embedding_text: str


@dataclass(frozen=True, slots=True)
class NodeSearchProjection:
    node_id: UUID
    projection_type: ProjectionType
    embedding_model: str
    projection_version: int
    search_text: str
    embedding_text: str
    embedding: list[float]
    source_hash: str


class NodeProjectionStore(Protocol):
    async def load_node_projection_source(
        self, *, workspace_id: UUID, node_id: UUID
    ) -> NodeProjectionSource | None: ...

    async def get_projection_hashes(
        self,
        *,
        workspace_id: UUID,
        node_id: UUID,
        embedding_model: str,
        projection_version: int,
    ) -> dict[str, str]: ...

    async def save_node_projections(
        self, *, workspace_id: UUID, projections: Sequence[NodeSearchProjection]
    ) -> None: ...

    async def delete_node_projections(
        self, *, workspace_id: UUID, node_id: UUID
    ) -> None: ...


class NodeProjectionEngine:
    """Rebuild a node's identity and profile projections."""

    def __init__(self, *, store: NodeProjectionStore, embedder: DocumentEmbedder):
        self.store = store
        self.embedder = embedder

    async def rebuild_node(
        self, *, workspace_id: UUID, node_id: UUID
    ) -> list[NodeSearchProjection]:
        """Rebuild changed projections and return the ones that were written.

        Deleted or merged nodes have no source and their projections are removed.
        Projections whose text hash is unchanged are not re-embedded.
        """

        source = await self.store.load_node_projection_source(
            workspace_id=workspace_id, node_id=node_id
        )
        if source is None:
            await self.store.delete_node_projections(
                workspace_id=workspace_id, node_id=node_id
            )
            return []

        texts = build_node_projection_texts(source)
        existing_hashes = await self.store.get_projection_hashes(
            workspace_id=workspace_id,
            node_id=node_id,
            embedding_model=self.embedder.model,
            projection_version=NODE_PROJECTION_VERSION,
        )
        hashes = {
            text.projection_type: projection_source_hash(
                text, embedding_model=self.embedder.model
            )
            for text in texts
        }
        stale = [
            text
            for text in texts
            if existing_hashes.get(text.projection_type)
            != hashes[text.projection_type]
        ]
        if not stale:
            return []

        vectors = await self.embedder.embed_documents(
            [text.embedding_text for text in stale]
        )
        projections = [
            NodeSearchProjection(
                node_id=node_id,
                projection_type=text.projection_type,
                embedding_model=self.embedder.model,
                projection_version=NODE_PROJECTION_VERSION,
                search_text=text.search_text,
                embedding_text=text.embedding_text,
                embedding=vector,
                source_hash=hashes[text.projection_type],
            )
            for text, vector in zip(stale, vectors, strict=True)
        ]
        await self.store.save_node_projections(
            workspace_id=workspace_id, projections=projections
        )
        return projections


def build_node_projection_texts(
    source: NodeProjectionSource,
) -> list[NodeProjectionText]:
    return [build_identity_projection(source), build_profile_projection(source)]


def build_identity_projection(source: NodeProjectionSource) -> NodeProjectionText:
    name = clean(source.name)
    details = source.type_specific or {}

    aliases = _aliases(source, name)
    identity_edges = _ranked_edges(
        [edge for edge in source.edges if edge.relation_type in IDENTITY_RELATIONS],
        MAX_IDENTITY_EDGES,
    )
    embedding_text = render_identity_description(
        name=name,
        node_type=source.node_type,
        aliases=aliases,
        external_providers=[
            item.provider
            for item in sorted(source.external_ids, key=lambda item: -item.confidence)
        ],
        type_specific=details,
        graph_facts=[
            _edge_sentence(name, edge, with_details=False) for edge in identity_edges
        ],
    )

    search_terms = [name, *aliases, *_identity_search_values(source)]
    return NodeProjectionText(
        projection_type="identity",
        search_text=" | ".join(dedupe(search_terms)),
        embedding_text=embedding_text,
    )


def build_profile_projection(source: NodeProjectionSource) -> NodeProjectionText:
    name = clean(source.name)
    type_label = NODE_TYPE_LABELS.get(source.node_type, humanize(source.node_type))
    details = source.type_specific or {}

    sentences = [f"{name} is {article(type_label.lower())} {type_label.lower()}."]
    summary = truncate(clean(source.summary or ""), MAX_SUMMARY_CHARS)
    if summary:
        sentences.append(as_sentence(summary))
    sentences.extend(_profile_facts(source.node_type, name, details))

    edges = _ranked_edges(source.edges, MAX_PROFILE_EDGES)
    sentences.extend(
        as_sentence(_edge_sentence(name, edge, with_details=True)) for edge in edges
    )
    neighbors = dedupe(clean(edge.neighbor_name) for edge in edges)
    if neighbors:
        sentences.append(f"{name} is connected to {join_list(neighbors)}.")

    search_terms = [name, summary, *neighbors]
    return NodeProjectionText(
        projection_type="profile",
        search_text=" | ".join(dedupe(term for term in search_terms if term)),
        embedding_text=" ".join(sentences),
    )


def projection_source_hash(text: NodeProjectionText, *, embedding_model: str) -> str:
    digest = hashlib.sha256()
    for part in (
        embedding_model,
        str(NODE_PROJECTION_VERSION),
        text.projection_type,
        text.search_text,
        text.embedding_text,
    ):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _aliases(source: NodeProjectionSource, name: str) -> list[str]:
    # Handles are how people are referred to, so they read as aliases.
    candidates = [
        f"@{item.external_id.removeprefix('@')}"
        for item in source.external_ids
        if item.provider.casefold() == "instagram"
    ]
    identifiers = sorted(
        source.identifiers, key=lambda item: (-item.confidence, item.text.casefold())
    )
    candidates.extend(item.text for item in identifiers)
    aliases = [
        alias
        for alias in dedupe(clean(candidate) for candidate in candidates)
        if alias.casefold() != name.casefold()
    ]
    return aliases[:MAX_IDENTIFIERS]


def _identity_search_values(source: NodeProjectionSource) -> list[str]:
    """Raw exact-match values that are noise for embeddings but useful lexically."""

    details = source.type_specific or {}
    values = [
        item.external_id
        for item in source.external_ids
        if item.provider.casefold() in _CONTACT_PROVIDERS
    ]
    if source.node_type == "location" and details.get("address"):
        values.append(str(details["address"]))
    if source.node_type == "date":
        rendered = _render_date_detail(details)
        if rendered:
            values.append(rendered)
        if details.get("date_value"):
            values.append(str(details["date_value"]))
    return values


def _profile_facts(node_type: str, name: str, details: dict[str, Any]) -> list[str]:
    """Changeable or contextual type-specific facts."""

    facts: list[str] = []
    if node_type == "person":
        if details.get("birthday"):
            facts.append(f"Birthday: {clean(details['birthday'])}.")
        if details.get("gender"):
            facts.append(f"Gender: {clean(details['gender'])}.")
        if details.get("relationship_context"):
            facts.append(
                as_sentence(f"Relationship: {clean(details['relationship_context'])}")
            )
        if details.get("pronouns"):
            facts.append(f"Pronouns: {clean(details['pronouns'])}.")
    elif node_type == "task":
        status = details.get("task_status")
        if status:
            facts.append(f"Status: {humanize(status)}.")
        if details.get("urgency"):
            facts.append(f"{humanize(details['urgency']).capitalize()} urgency.")
        if details.get("due_at"):
            rendered = render_datetime(details["due_at"])
            if rendered:
                facts.append(f"Due {rendered}.")
        if details.get("completed_at"):
            rendered = render_datetime(details["completed_at"])
            if rendered:
                facts.append(f"Completed {rendered}.")
    elif node_type == "location" and details.get("address"):
        facts.append(f"Address: {clean(details['address'])}.")
    elif node_type == "date":
        rendered = _render_date_detail(details)
        if rendered and rendered.casefold() != name.casefold():
            facts.append(f"Date: {rendered}.")
    return facts


def _ranked_edges(
    edges: Sequence[ProjectionEdge], limit: int
) -> list[ProjectionEdge]:
    ranked = sorted(
        edges,
        key=lambda edge: (
            -(edge.strength * edge.confidence),
            -edge.confidence,
            edge.neighbor_name.casefold(),
            str(edge.edge_id),
        ),
    )
    return ranked[:limit]


def _edge_sentence(name: str, edge: ProjectionEdge, *, with_details: bool) -> str:
    connector = clean(edge.connector) or humanize(edge.relation_type)
    neighbor = clean(edge.neighbor_name)
    if edge.outgoing:
        sentence = f"{name} {connector} {neighbor}"
    else:
        sentence = f"{neighbor} {connector} {name}"
    details = truncate(clean(edge.details or ""), MAX_DETAILS_CHARS)
    if with_details and details:
        sentence = f"{sentence} ({details.rstrip('.')})"
    return sentence


def _render_date_detail(details: dict[str, Any]) -> str:
    precision = details.get("precision") or "day"
    if precision == "range":
        start = render_datetime(details.get("start_at"))
        end = render_datetime(details.get("end_at"))
        if start and end:
            return f"{start} to {end}"
        return start or end
    if precision == "datetime" and details.get("start_at"):
        return render_datetime(details["start_at"])
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
