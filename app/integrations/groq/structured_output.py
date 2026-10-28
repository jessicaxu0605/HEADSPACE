"""Provider-generic helpers for Groq structured-output adapters."""

from __future__ import annotations

import os
from dataclasses import fields, is_dataclass
from typing import Any
from uuid import UUID

from groq import BadRequestError


DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_GROQ_TEMPERATURE = 0.5


def groq_error_detail(exc: BadRequestError) -> dict[str, Any]:
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error", body)
    return error if isinstance(error, dict) else {}


def groq_error_code(exc: BadRequestError) -> str | None:
    return groq_error_detail(exc).get("code")


def groq_error_message(exc: BadRequestError) -> str:
    """Return a bounded provider error without echoing failed generation text."""

    return str(groq_error_detail(exc).get("message") or exc)[:1_000]


def resolve_api_key(explicit_api_key: str | None) -> str | None:
    return explicit_api_key or os.getenv("GROQ_API_KEY") or None


def resolve_temperature() -> float:
    configured = os.getenv("GROQ_TEMPERATURE")
    temperature = float(configured) if configured else DEFAULT_GROQ_TEMPERATURE
    if not 0 <= temperature <= 2:
        raise ValueError("GROQ_TEMPERATURE must be between 0 and 2")
    return temperature


def response_content(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError) as exc:
        raise ValueError("Groq response did not contain a completion choice") from exc
    if not isinstance(content, str) or not content:
        raise ValueError("Groq response contained no message content")
    return content


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_jsonable(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    raise TypeError(f"Cannot serialize {type(value).__name__} for Groq")


def required(value: dict[str, Any], key: str, path: str) -> Any:
    if key not in value:
        raise KeyError(f"{path}.{key} is required")
    return value[key]


def mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{path} must be an object")
    return value


def list_value(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{path} must be an array")
    return value


def string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{path} must be a string")
    return value


def object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
