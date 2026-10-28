"""Source adapters and read-strategy preparation for extraction workflows."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterable
from html import unescape
from typing import Any

from app.integrations.aws.s3 import read_storage_object
from app.workflows.ingest.models import (
    MAX_LOAD_ALL_BYTES,
    ChunkLocation,
    ExtractionSourceContext,
    JsonObject,
    MemorySourceRecord,
    PreparedSource,
    SourceChunk,
    SourceReadStrategy as SourceReadStrategyName,
)


# =============================================================================
# Errors
# =============================================================================

class UnsupportedSourceStrategyError(ValueError):
    """Raised when a source cannot be prepared with the requested strategy."""


class PayloadTooLargeForLoadAllError(UnsupportedSourceStrategyError):
    """Raised when a load_all source exceeds the hard byte limit."""


# =============================================================================
# Read Strategies
# =============================================================================

class ReadStrategy(ABC):
    @property
    @abstractmethod
    def strategy_name(self) -> SourceReadStrategyName:
        ...

    @abstractmethod
    async def read_payload(self, source: MemorySourceRecord) -> Any:
        ...

class LoadAllReadStrategy(ReadStrategy):
    @property
    def strategy_name(self) -> SourceReadStrategyName:
        return "load_all"

    async def read_payload(self, source: MemorySourceRecord) -> bytes:
        """Read the whole stored object into memory for source-aware chunking."""
        if source.storage_object_key is None:
            raise UnsupportedSourceStrategyError(
                f"source_type={source.source_type!r} has no storage_object_key."
            )

        payload = read_storage_object(source.storage_object_key)
        payload_size = _payload_size_bytes(payload)
        if payload_size > MAX_LOAD_ALL_BYTES:
            raise PayloadTooLargeForLoadAllError(
                f"source_type={source.source_type!r} payload is {payload_size} bytes; "
                f"load_all supports at most {MAX_LOAD_ALL_BYTES} bytes."
            )

        return payload


# =============================================================================
# Payload Helpers
# =============================================================================

def _payload_size_bytes(payload: Any) -> int:
    if isinstance(payload, bytes):
        return len(payload)

    return len(_payload_to_text(payload).encode("utf-8"))


def _payload_to_json_object(payload: Any) -> JsonObject | None:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _payload_to_text(payload: Any) -> str:
    if isinstance(payload, bytes):
        return payload.decode("utf-8")
    if isinstance(payload, str):
        return payload

    try:
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    except TypeError as exc:
        raise UnsupportedSourceStrategyError(
            f"Unsupported JSON payload value for load_all: {type(payload).__name__}."
        ) from exc


def _strip_html(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unescape(without_tags)).strip()


def _string_fields(data: JsonObject, field_names: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for field_name in field_names:
        value = data.get(field_name)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return values


# =============================================================================
# Source Type Adapters
# =============================================================================

class SourceTypeAdapter(ABC):
    read_strategies: tuple[ReadStrategy, ...]

    def build_extraction_metadata(self, source_metadata: JsonObject) -> JsonObject:
        """
        Filters source_metadata from MemorySourceRecordto only include fields relevant for extraction
        Relevant fields are based on the source type.
        """
        return dict(source_metadata)

    @abstractmethod
    def _create_chunks_from_payload(
        self,
        payload: Any,
        source: MemorySourceRecord,
    ) -> AsyncIterable[SourceChunk]:
        """
        Convert a strategy-read payload into extraction-ready source chunks.
        Read payloads may be streamed.
        """
        ...

    async def _create_single_text_chunk(
        self,
        text: str,
        source: MemorySourceRecord,
    ) -> AsyncIterable[SourceChunk]:
        """
        The most basic create_chunks_from_payload implementation: yield a single chunk containing all text.
        """
        if not text:
            return

        yield SourceChunk(
            chunk_id=f"{source.id}:all",
            source_id=source.id,
            text=text,
            chunk_location=ChunkLocation(start_offset=0, end_offset=len(text)),
        )

    async def create_chunk_stream(
        self,
        source: MemorySourceRecord,
    ) -> AsyncIterable[SourceChunk]:
        """
        Each concrete SourceTypeAdapter provides an list of read strategies in order of preference.
        
        From most to least preferred read strategy, attempt to create a chunk stream for the given source.
        If a read strategy is not supported for the source, it will be skipped and the next strategy will be attempted. 
        If no read strategies are supported, an UnsupportedSourceStrategyError will be raised
        """
        last_error: UnsupportedSourceStrategyError | None = None
        for read_strategy in self.read_strategies:
            try:
                payload = await read_strategy.read_payload(source)
                async for chunk in self._create_chunks_from_payload(payload, source):
                    yield chunk
                return
            except UnsupportedSourceStrategyError as exc:
                last_error = exc

        if last_error is not None:
            raise last_error

        raise UnsupportedSourceStrategyError(
            f"No read strategies are configured for source_type={source.source_type!r}."
        )


class ManualEntryAdapter(SourceTypeAdapter):
    read_strategies = (LoadAllReadStrategy(),)

    def build_extraction_metadata(self, source_metadata: JsonObject) -> JsonObject:
        allowed_fields = ("submitted_at", "author", "locale", "timezone")
        return {
            field: source_metadata[field]
            for field in allowed_fields
            if field in source_metadata
        }

    def _create_chunks_from_payload(
        self,
        payload: Any,
        source: MemorySourceRecord,
    ) -> AsyncIterable[SourceChunk]:
        return self._create_single_text_chunk(_payload_to_text(payload).strip(), source)


class EmailAdapter(SourceTypeAdapter):
    read_strategies = (LoadAllReadStrategy(),)

    def build_extraction_metadata(self, source_metadata: JsonObject) -> JsonObject:
        allowed_fields = (
            "provider",
            "external_id",
            "thread_id",
            "subject",
            "from",
            "to",
            "cc",
            "sent_at",
            "received_at",
        )
        return {
            field: source_metadata[field]
            for field in allowed_fields
            if field in source_metadata
        }

    def _create_chunks_from_payload(
        self,
        payload: Any,
        source: MemorySourceRecord,
    ) -> AsyncIterable[SourceChunk]:
        data = _payload_to_json_object(payload)
        if data is None:
            return self._create_single_text_chunk(
                _strip_html(_payload_to_text(payload)),
                source,
            )

        parts = _string_fields(
            data,
            ("subject", "from", "to", "cc", "date", "sent_at", "received_at"),
        )
        body = "\n\n".join(_string_fields(data, ("text", "body", "body_text", "snippet")))
        html_body = "\n\n".join(_string_fields(data, ("html", "body_html")))
        if body:
            parts.append(body)
        elif html_body:
            parts.append(_strip_html(html_body))

        return self._create_single_text_chunk("\n".join(parts).strip(), source)


class CalendarEventAdapter(SourceTypeAdapter):
    read_strategies = (LoadAllReadStrategy(),)

    def build_extraction_metadata(self, source_metadata: JsonObject) -> JsonObject:
        allowed_fields = (
            "provider",
            "external_id",
            "calendar_id",
            "organizer",
            "attendees",
            "start",
            "end",
            "timezone",
        )
        return {
            field: source_metadata[field]
            for field in allowed_fields
            if field in source_metadata
        }

    def _create_chunks_from_payload(
        self,
        payload: Any,
        source: MemorySourceRecord,
    ) -> AsyncIterable[SourceChunk]:
        data = _payload_to_json_object(payload)
        if data is None:
            return self._create_single_text_chunk(_payload_to_text(payload).strip(), source)

        parts = _string_fields(
            data,
            ("title", "summary", "start", "end", "location", "description", "notes"),
        )
        attendees = data.get("attendees")
        if isinstance(attendees, list):
            attendee_text = ", ".join(str(attendee) for attendee in attendees)
            if attendee_text:
                parts.append(f"Attendees: {attendee_text}")

        return self._create_single_text_chunk("\n".join(parts).strip(), source)


# =============================================================================
# Exports
# =============================================================================

SOURCE_ADAPTERS: dict[str, SourceTypeAdapter] = {
    "manual_entry": ManualEntryAdapter(),
    "email": EmailAdapter(),
    "calendar_event": CalendarEventAdapter(),
}


def prepare_source(
    source: MemorySourceRecord,
) -> PreparedSource:
    try:
        adapter = SOURCE_ADAPTERS[source.source_type]
    except KeyError as exc:
        raise UnsupportedSourceStrategyError(
            f"No source adapter is available for source_type={source.source_type!r}."
        ) from exc

    source_context = ExtractionSourceContext(
        source_id=source.id,
        source_type=source.source_type,
        extraction_metadata=adapter.build_extraction_metadata(source.source_metadata),
    )
    return PreparedSource(
        source_context=source_context,
        chunks=adapter.create_chunk_stream(source),
    )
