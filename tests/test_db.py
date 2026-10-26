from __future__ import annotations

import unittest
from unittest.mock import patch

from app.persistence.postgres.connection import get_database_url


class DatabaseConfigurationTests(unittest.TestCase):
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
