from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.persistence.postgres.connection import get_database_url


class DatabaseConfigurationTests(unittest.TestCase):
    def test_reads_neon_url_from_local_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env.local"
            env_path.write_text(
                "NEON_DATABASE_URL=postgresql://neon.example/headspace\n",
                encoding="utf-8",
            )
            with patch.dict("os.environ", {}, clear=True), patch(
                "app.persistence.postgres.connection.DEFAULT_LOCAL_ENV_PATH", env_path
            ):
                self.assertEqual(
                    get_database_url(),
                    "postgresql://neon.example/headspace",
                )

    def test_process_environment_takes_precedence(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "postgresql://process.example/headspace",
                "NEON_DATABASE_URL": "postgresql://neon.example/headspace",
            },
            clear=True,
        ):
            self.assertEqual(
                get_database_url(),
                "postgresql://process.example/headspace",
            )


if __name__ == "__main__":
    unittest.main()
