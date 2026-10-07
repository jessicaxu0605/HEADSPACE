"""Source adapters and read-strategy preparation for extraction workflows."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterable, Callable
from html import unescape
from typing import Any

from app.storage import read_storage_object
from app.workflows.types import (
    MAX_LOAD_ALL_BYTES,
    ChunkLocation,
    ExtractionSourceContext,
    JsonObject,
    MemorySourceRecord,
    PreparedSource,
    SourceAdapter,
    SourceChunk,
    SourceReadStrategy,
)


class UnsupportedSourceStrategyError(ValueError):
    """Raised when a source cannot be prepared with the requested strategy."""


class PayloadTooLargeForLoadAllError(UnsupportedSourceStrategyError):
    """Raised when a load_all source exceeds the hard byte limit."""


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


class LoadAllSourceAdapter(ABC):
    preferred_strategy: SourceReadStrategy = "load_all"

    def build_extraction_metadata(self, source_metadata: JsonObject) -> JsonObject:
        return dict(source_metadata)

    @abstractmethod
    def extract_text(self, payload: Any, source: MemorySourceRecord) -> str:
        ...

    async def create_chunk_stream(
        self,
        payload: Any,
        source: MemorySourceRecord,
    ) -> AsyncIterable[SourceChunk]:
        payload_size = _payload_size_bytes(payload)
        if payload_size > MAX_LOAD_ALL_BYTES:
            raise PayloadTooLargeForLoadAllError(
                f"source_type={source.source_type!r} payload is {payload_size} bytes; "
                f"load_all supports at most {MAX_LOAD_ALL_BYTES} bytes."
            )

        text = self.extract_text(payload, source)
        if not text:
            return

        yield SourceChunk(
            chunk_id=f"{source.id}:all",
            source_id=source.id,
            text=text,
            chunk_location=ChunkLocation(start_offset=0, end_offset=len(text)),
        )


class ManualEntryAdapter(LoadAllSourceAdapter):
    def build_extraction_metadata(self, source_metadata: JsonObject) -> JsonObject:
        allowed_fields = ("submitted_at", "author", "locale", "timezone")
        return {
            field: source_metadata[field]
            for field in allowed_fields
            if field in source_metadata
        }

    def extract_text(self, payload: Any, source: MemorySourceRecord) -> str:
        return _payload_to_text(payload).strip()


class EmailAdapter(LoadAllSourceAdapter):
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

    def extract_text(self, payload: Any, source: MemorySourceRecord) -> str:
        data = _payload_to_json_object(payload)
        if data is None:
            return _strip_html(_payload_to_text(payload))

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

        return "\n".join(parts).strip()


class CalendarEventAdapter(LoadAllSourceAdapter):
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

    def extract_text(self, payload: Any, source: MemorySourceRecord) -> str:
        data = _payload_to_json_object(payload)
        if data is None:
            return _payload_to_text(payload).strip()

        parts = _string_fields(
            data,
            ("title", "summary", "start", "end", "location", "description", "notes"),
        )
        attendees = data.get("attendees")
        if isinstance(attendees, list):
            attendee_text = ", ".join(str(attendee) for attendee in attendees)
            if attendee_text:
                parts.append(f"Attendees: {attendee_text}")

        return "\n".join(parts).strip()


SOURCE_ADAPTERS: dict[str, SourceAdapter] = {
    "manual_entry": ManualEntryAdapter(),
    "email": EmailAdapter(),
    "calendar_event": CalendarEventAdapter(),
}


def get_source_adapter(source_type: str) -> SourceAdapter:
    try:
        return SOURCE_ADAPTERS[source_type]
    except KeyError as exc:
        raise UnsupportedSourceStrategyError(
            f"No source adapter is available for source_type={source_type!r}."
        ) from exc


def prepare_source(
    source: MemorySourceRecord,
    read_object: Callable[[str], bytes] = read_storage_object,
) -> PreparedSource:
    if source.storage_object_key is None:
        raise UnsupportedSourceStrategyError(
            f"source_type={source.source_type!r} has no storage_object_key."
        )

    adapter = get_source_adapter(source.source_type)
    payload = read_object(source.storage_object_key)
    source_context = ExtractionSourceContext(
        source_id=source.id,
        source_type=source.source_type,
        extraction_metadata=adapter.build_extraction_metadata(source.source_metadata),
    )
    return PreparedSource(
        source_context=source_context,
        chunks=adapter.create_chunk_stream(payload, source),
    )
