from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).parents[2]


class BackupSafetyTests(unittest.TestCase):
    def test_physical_backup_uses_shared_local_socket(self) -> None:
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("PGHOST: /var/run/postgresql", compose)
        self.assertGreaterEqual(compose.count("pgsocket:/var/run/postgresql"), 2)

    def test_failed_base_backup_cannot_be_reported_as_verified(self) -> None:
        script = (ROOT / "backend" / "scripts" / "postgres_backup_loop.sh").read_text(encoding="utf-8")
        self.assertIn("if ! pg_basebackup", script)
        self.assertIn("if ! pg_verifybackup", script)
        self.assertIn("Removing incomplete PITR base backup", script)


if __name__ == "__main__":
    unittest.main()
