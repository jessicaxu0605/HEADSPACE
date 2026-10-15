"""Apply the repository's versioned PostgreSQL migrations."""

from __future__ import annotations

from pathlib import Path

from app.persistence.postgres.connection import get_connection


MIGRATIONS_ROOT = Path(__file__).resolve().parents[1] / "migrations"


def apply_migrations() -> None:
    with get_connection() as conn:
        with conn.transaction():
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migration (
                    version text PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )

        for migration_path in sorted(MIGRATIONS_ROOT.glob("*.sql")):
            with conn.transaction():
                already_applied = conn.execute(
                    "SELECT 1 FROM schema_migration WHERE version = %s",
                    (migration_path.name,),
                ).fetchone()
                if already_applied:
                    continue
                conn.execute(migration_path.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT INTO schema_migration (version) VALUES (%s)",
                    (migration_path.name,),
                )


if __name__ == "__main__":
    apply_migrations()
