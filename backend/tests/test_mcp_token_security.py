from __future__ import annotations

import json
import os
from pathlib import Path
import unittest

os.environ.setdefault("DATAMID_TOKEN_SECRET", "test-only-token-secret-" + "x" * 48)

from backend.app.api.routers.admin_mcp_token import (  # noqa: E402
    SSE_ENDPOINT_TEMPLATE,
    _build_mcp_config,
)
from backend.app.api.routers.public_data import _build_api_doc_markdown  # noqa: E402
from backend.app.services.mcp_service import (  # noqa: E402
    MCP_TOKEN_PLACEHOLDER,
    sanitize_mcp_config,
    update_mcp_token_configs,
)


class _RecordingConnection:
    def __init__(self) -> None:
        self.statement = ""
        self.params: tuple[object, ...] = ()

    def execute(self, statement: str, params: tuple[object, ...]) -> None:
        self.statement = statement
        self.params = params


class McpTokenSecurityTests(unittest.TestCase):
    def test_startup_migration_binds_secret_markers(self) -> None:
        main_source = (Path(__file__).parents[1] / "app" / "main.py").read_text(encoding="utf-8")
        migration_source = main_source.split("def _migrate_mcp_token_config_columns()", 1)[1].split(
            "def _migrate_sync_interval_to_seconds()", 1
        )[0]
        self.assertNotIn("LIKE '%mcp_token=%'", migration_source)
        self.assertNotIn("LIKE '%dmc_%'", migration_source)
        self.assertIn("POSITION(?", migration_source)
        self.assertIn("(now_text(), *legacy_secret_markers)", migration_source)

    def test_export_config_uses_header_not_query_string(self) -> None:
        token = "sensitive-token-value"
        raw = _build_mcp_config(token, ["orders"], SSE_ENDPOINT_TEMPLATE, "https://data.example")
        payload = json.loads(raw)
        server = payload["mcpServers"]["ramon-datamid"]
        self.assertEqual(server["url"], "https://data.example/api/mcp/sse/orders")
        self.assertEqual(server["headers"]["Authorization"], f"Bearer {token}")
        self.assertNotIn("mcp_token=", raw)

    def test_persisted_config_is_redacted(self) -> None:
        token = "header-secret-value"
        config = json.dumps(
            {
                "url": f"https://data.example/api/mcp/sse/orders?mcp_token={token}",
                "headers": {"Authorization": f"Bearer {token}"},
            }
        )
        conn = _RecordingConnection()
        update_mcp_token_configs(conn, 7, config, config)
        self.assertIn("UPDATE sys_mcp_token", conn.statement)
        self.assertNotIn(token, str(conn.params))
        self.assertIn(MCP_TOKEN_PLACEHOLDER, str(conn.params))
        self.assertNotIn("mcp_token=", str(conn.params))

    def test_sanitizer_removes_legacy_query_credential(self) -> None:
        token = "legacy-secret-value"
        clean = sanitize_mcp_config(
            json.dumps({"url": f"https://data.example/mcp?mcp_token={token}&mode=sse"})
        )
        self.assertNotIn(token, clean)
        self.assertNotIn("mcp_token=", clean)
        self.assertIn("mode=sse", clean)

    def test_documentation_contains_template_but_no_issued_token(self) -> None:
        markdown = _build_api_doc_markdown(
            "orders",
            {
                "source_name": "Orders",
                "description": "",
                "last_sync_at": "",
                "current_sync_version": "",
                "business_time_field": "",
                "api_url": "",
                "searchable_fields": [],
            },
            [],
            public_url="https://data.example",
        )
        self.assertIn("Authorization: Bearer <your_mcp_token>", markdown)
        self.assertNotIn("mcp_token=", markdown)
        self.assertIn("不会自动签发或显示令牌", markdown)


if __name__ == "__main__":
    unittest.main()
