"""Build ``edge_search_projection`` text and vectors for canonical edges.

Design V1 gives each edge one projection that reads like the fact it records:

    Sarah likes Matcha. This is a personal preference related to drinks,
    cafés, lattes, and gift ideas. Detail: especially strawberry matcha lattes.

The text is built from the edge's readable sentence (endpoints joined by the
tense-appropriate catalog connector), the relation type's meaning, the edge's
free-text ``details``, its validity window, and its strongest evidence with
annotated mentions resolved to node names.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.search.common import (
    DocumentEmbedder,
    article,
    as_sentence,
    clean,
    dedupe,
    humanize,
    render_datetime,
    truncate,
)


# Bump when the text format changes so stored projections are rebuilt.
EDGE_PROJECTION_VERSION = 1

MAX_EVIDENCE = 3
MAX_EVIDENCE_CHARS = 280
MAX_DETAILS_CHARS = 400

# Relations whose catalog description is guidance, not meaning. Their details
# carry what the connection is.
_GENERIC_RELATIONS = {"related_to", "possibly_same_as"}


@dataclass(frozen=True, slots=True)
class ProjectionAnnotation:
    """A span of evidence text that refers to a node without naming it."""

    start: int
    length: int
    node_name: str


@dataclass(frozen=True, slots=True)
class ProjectionEvidence:
    raw_content: str
    confidence: float = 0.5
    is_verbatim: bool = True
    created_at: datetime | None = None
    annotations: list[ProjectionAnnotation] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class EdgeProjectionSource:
    """Everything the builder reads for one canonical edge."""

    edge_id: UUID
    relation_type: str
    # Tense-appropriate phrase placed between canonical endpoints: A {connector} B.
    connector: str
    from_name: str
    from_type: str
    to_name: str
    to_type: str
    relation_description: str | None = None
    parent_relation: str | None = None
    details: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    evidence: list[ProjectionEvidence] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class EdgeProjectionText:
    search_text: str
    embedding_text: str


@dataclass(frozen=True, slots=True)
class EdgeSearchProjection:
    edge_id: UUID
    embedding_model: str
    projection_version: int
    search_text: str
    embedding_text: str
    embedding: list[float]
    source_hash: str


class EdgeProjectionStore(Protocol):
    async def load_edge_projection_source(
        self, *, workspace_id: UUID, edge_id: UUID
    ) -> EdgeProjectionSource | None: ...

    async def get_edge_projection_hash(
        self,
        *,
        workspace_id: UUID,
        edge_id: UUID,
        embedding_model: str,
        projection_version: int,
    ) -> str | None: ...

    async def save_edge_projection(
        self, *, workspace_id: UUID, projection: EdgeSearchProjection
    ) -> None: ...

    async def delete_edge_projections(
        self, *, workspace_id: UUID, edge_id: UUID
    ) -> None: ...


class EdgeProjectionEngine:
    """Rebuild an edge's search projection."""

    def __init__(self, *, store: EdgeProjectionStore, embedder: DocumentEmbedder):
        self.store = store
        self.embedder = embedder

    async def rebuild_edge(
        self, *, workspace_id: UUID, edge_id: UUID
    ) -> EdgeSearchProjection | None:
        """Rebuild the projection if its text changed; return what was written.

        Deleted edges, and edges whose endpoints are deleted or merged, have no
        source and their projections are removed.
        """

        source = await self.store.load_edge_projection_source(
            workspace_id=workspace_id, edge_id=edge_id
        )
        if source is None:
            await self.store.delete_edge_projections(
                workspace_id=workspace_id, edge_id=edge_id
            )
            return None

        text = build_edge_projection_text(source)
        source_hash = edge_projection_source_hash(
            text, embedding_model=self.embedder.model
        )
        existing_hash = await self.store.get_edge_projection_hash(
            workspace_id=workspace_id,
            edge_id=edge_id,
            embedding_model=self.embedder.model,
            projection_version=EDGE_PROJECTION_VERSION,
        )
        if existing_hash == source_hash:
            return None

        [vector] = await self.embedder.embed_documents([text.embedding_text])
        projection = EdgeSearchProjection(
            edge_id=edge_id,
            embedding_model=self.embedder.model,
            projection_version=EDGE_PROJECTION_VERSION,
            search_text=text.search_text,
            embedding_text=text.embedding_text,
            embedding=vector,
            source_hash=source_hash,
        )
        await self.store.save_edge_projection(
            workspace_id=workspace_id, projection=projection
        )
        return projection


def build_edge_projection_text(source: EdgeProjectionSource) -> EdgeProjectionText:
    from_name = clean(source.from_name)
    to_name = clean(source.to_name)
    connector = clean(source.connector) or humanize(source.relation_type)
    fact = f"{from_name} {connector} {to_name}"

    sentences = [as_sentence(fact)]
    sentences.append(_relation_sentence(source))
    details = truncate(clean(source.details or ""), MAX_DETAILS_CHARS)
    if details:
        sentences.append(as_sentence(f"Detail: {details}"))
    validity = _validity_sentence(source.valid_from, source.valid_to)
    if validity:
        sentences.append(validity)
    quotes = _evidence_quotes(source.evidence)
    if quotes:
        sentences.append("Evidence: " + " ".join(f'"{quote}"' for quote in quotes))

    search_terms = [from_name, to_name, humanize(source.relation_type), details, *quotes]
    return EdgeProjectionText(
        search_text=" | ".join(dedupe(term for term in search_terms if term)),
        embedding_text=" ".join(sentences),
    )


def render_evidence(evidence: ProjectionEvidence) -> str:
    """Replace annotated mentions ("she") with the node names they refer to."""

    text = evidence.raw_content
    pieces: list[str] = []
    cursor = 0
    for annotation in sorted(evidence.annotations, key=lambda item: item.start):
        end = annotation.start + annotation.length
        # Skip overlapping or out-of-range spans rather than garbling the text.
        if annotation.start < cursor or end > len(text):
            continue
        pieces.append(text[cursor : annotation.start])
        pieces.append(clean(annotation.node_name))
        cursor = end
    pieces.append(text[cursor:])
    return clean("".join(pieces))


def edge_projection_source_hash(
    text: EdgeProjectionText, *, embedding_model: str
) -> str:
    digest = hashlib.sha256()
    for part in (
        embedding_model,
        str(EDGE_PROJECTION_VERSION),
        text.search_text,
        text.embedding_text,
    ):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _relation_sentence(source: EdgeProjectionSource) -> str:
    relation = humanize(source.relation_type)
    from_type = humanize(source.from_type)
    to_type = humanize(source.to_type)
    sentence = (
        f"A {relation} relation between {article(from_type)} {from_type} "
        f"and {article(to_type)} {to_type}"
    )
    if source.parent_relation and source.parent_relation not in _GENERIC_RELATIONS:
        sentence = f"{sentence}, a kind of {humanize(source.parent_relation)}"
    meaning = _first_sentence(source.relation_description or "")
    if meaning and source.relation_type not in _GENERIC_RELATIONS:
        return f"{sentence}: {as_sentence(meaning)}"
    return as_sentence(sentence)


def _first_sentence(text: str) -> str:
    text = clean(text)
    end = text.find(". ")
    return text[: end + 1] if end >= 0 else text


def _validity_sentence(valid_from: datetime | None, valid_to: datetime | None) -> str:
    start = render_datetime(valid_from.isoformat()) if valid_from else ""
    end = render_datetime(valid_to.isoformat()) if valid_to else ""
    if start and end:
        return f"From {start} until {end}."
    if start:
        return f"Since {start}."
    if end:
        return f"Until {end}."
    return ""


def _evidence_quotes(evidence: Sequence[ProjectionEvidence]) -> list[str]:
    ranked = sorted(
        evidence,
        key=lambda item: (
            -item.confidence,
            not item.is_verbatim,
            -(item.created_at.timestamp() if item.created_at else 0.0),
        ),
    )
    quotes = dedupe(
        truncate(render_evidence(item), MAX_EVIDENCE_CHARS) for item in ranked
    )
    return quotes[:MAX_EVIDENCE]
