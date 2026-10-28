"""Versioned PostgreSQL migrations and their runner."""

from migrations.postgres import apply_migrations

__all__ = ["apply_migrations"]
