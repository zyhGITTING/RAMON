from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).parents[2]


class DatabaseRolePermissionTests(unittest.TestCase):
    def test_runtime_role_can_create_sync_staging_tables(self) -> None:
        script = (ROOT / "backend" / "scripts" / "configure_db_roles.sh").read_text(encoding="utf-8")
        revoke_index = script.index("REVOKE ALL ON DATABASE %I FROM PUBLIC")
        grant_index = script.index("GRANT TEMPORARY ON DATABASE %I TO datamid_app")

        self.assertGreater(grant_index, revoke_index)
        self.assertIn(
            "has_database_privilege('datamid_app', current_database(), 'TEMPORARY')",
            script,
        )


if __name__ == "__main__":
    unittest.main()
