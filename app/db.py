"""Database connection helpers."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not database_url:
        raise RuntimeError("Missing DATABASE_URL or NEON_DATABASE_URL.")
    return database_url


@contextmanager
def get_connection() -> Iterator[psycopg.Connection]:
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:
        yield conn
