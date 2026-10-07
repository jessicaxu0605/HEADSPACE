"""File upload and download helpers for local or object storage."""

from __future__ import annotations

import os
from pathlib import Path


def get_storage_root() -> Path:
    return Path(os.getenv("MEMORY_OBJECT_STORAGE_ROOT", ".storage")).resolve()


def read_storage_object(storage_object_key: str) -> bytes:
    storage_root = get_storage_root()
    object_path = (storage_root / storage_object_key).resolve()
    if not object_path.is_relative_to(storage_root):
        raise ValueError(f"Invalid storage object key: {storage_object_key!r}.")

    return object_path.read_bytes()
