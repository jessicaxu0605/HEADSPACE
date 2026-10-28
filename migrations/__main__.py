"""Run the repository's PostgreSQL migrations."""

from migrations.postgres import apply_migrations


if __name__ == "__main__":
    apply_migrations()
