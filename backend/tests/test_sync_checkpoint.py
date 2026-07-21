from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import requests
import urllib3
from fastapi import HTTPException

from backend.app.integrations.datasource_runtime import (
    DatasourcePayloadError,
    _is_retriable_error,
    fetch_page_with_retry,
    iter_remote_pages,
)


def _datasource() -> dict:
    return {
        "source_key": "test_source",
        "api_url": "http://example.com/api",
        "http_method": "POST",
        "verify_tls": True,
        "request_config": {
            "payload_template": {
                "page": "$page",
                "page_size": "$page_size",
            },
            "pagination": {
                "page_size": 2,
                "max_pages": 10,
                "code_key": "code",
                "success_codes": [0],
                "data_key": "data",
                "total_key": "total",
                "has_next_key": "has_next",
            },
        },
    }


class RetriableErrorTests(unittest.TestCase):
    def test_request_exception_is_retriable(self) -> None:
        self.assertTrue(_is_retriable_error(requests.ConnectionError("timeout")))

    def test_urllib3_error_is_retriable(self) -> None:
        self.assertTrue(_is_retriable_error(urllib3.exceptions.HTTPError("reset")))

    def test_server_error_http_exception_is_retriable(self) -> None:
        self.assertTrue(_is_retriable_error(HTTPException(status_code=503), status_code=503))

    def test_timeout_http_exception_is_retriable(self) -> None:
        self.assertTrue(_is_retriable_error(HTTPException(status_code=504), status_code=504))

    def test_rate_limit_is_retriable(self) -> None:
        self.assertTrue(_is_retriable_error(HTTPException(status_code=429), status_code=429))

    def test_client_error_is_not_retriable(self) -> None:
        self.assertFalse(_is_retriable_error(HTTPException(status_code=400), status_code=400))

    def test_payload_error_is_not_retriable(self) -> None:
        self.assertFalse(_is_retriable_error(DatasourcePayloadError("invalid_json", "bad")))


class PageRetryTests(unittest.TestCase):
    @patch("backend.app.integrations.datasource_runtime.perform_datasource_fetch")
    def test_retry_succeeds_on_second_attempt(self, fetch) -> None:
        fetch.side_effect = [
            requests.ConnectionError("transient"),
            (200, {"code": 0, "data": [{"id": 1}], "total": 1, "has_next": False}),
        ]
        status, body = fetch_page_with_retry(_datasource(), 1, 2)
        self.assertEqual(status, 200)
        self.assertEqual(body["total"], 1)
        self.assertEqual(fetch.call_count, 2)

    @patch("backend.app.integrations.datasource_runtime.perform_datasource_fetch")
    def test_retry_gives_up_after_three_attempts(self, fetch) -> None:
        fetch.side_effect = requests.ConnectionError("transient")
        with self.assertRaisesRegex(HTTPException, "failed after 3 attempts"):
            fetch_page_with_retry(_datasource(), 1, 2, max_attempts=3)
        self.assertEqual(fetch.call_count, 3)

    @patch("backend.app.integrations.datasource_runtime.perform_datasource_fetch")
    def test_non_retriable_error_not_retried(self, fetch) -> None:
        fetch.side_effect = DatasourcePayloadError("invalid_json", "bad payload")
        with self.assertRaisesRegex(DatasourcePayloadError, "bad payload"):
            fetch_page_with_retry(_datasource(), 1, 2)
        self.assertEqual(fetch.call_count, 1)

    @patch("backend.app.integrations.datasource_runtime.perform_datasource_fetch")
    def test_http_400_not_retried(self, fetch) -> None:
        fetch.side_effect = HTTPException(status_code=400, detail="bad request")
        with self.assertRaisesRegex(HTTPException, "bad request"):
            fetch_page_with_retry(_datasource(), 1, 2)
        self.assertEqual(fetch.call_count, 1)


class IterRemotePagesTests(unittest.TestCase):
    @patch("backend.app.integrations.datasource_runtime.perform_datasource_fetch")
    def test_resumes_from_start_page(self, fetch) -> None:
        fetch.return_value = (200, {"code": 0, "data": [{"id": 3}, {"id": 4}], "total": 4, "has_next": False})
        pages = list(iter_remote_pages(_datasource(), start_page=2, sequential=True))
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0][0], 2)
        self.assertEqual([r["id"] for r in pages[0][1]], [3, 4])
        fetch.assert_called_once()
        # Verify the requested page is 2 (second positional argument).
        call_args = fetch.call_args
        self.assertEqual(call_args.args[1], 2)

    @patch("backend.app.integrations.datasource_runtime.perform_datasource_fetch")
    def test_checkpoint_callback_invoked_per_page(self, fetch) -> None:
        fetch.side_effect = [
            (200, {"code": 0, "data": [{"id": 1}], "total": 2, "has_next": True}),
            (200, {"code": 0, "data": [{"id": 2}], "total": 2, "has_next": False}),
        ]
        callback = MagicMock()
        pages = list(iter_remote_pages(_datasource(), sequential=True, checkpoint_callback=callback))
        self.assertEqual(len(pages), 2)
        self.assertEqual(callback.call_count, 2)
        self.assertEqual(callback.call_args_list[0].kwargs["page"], 1)
        self.assertEqual(callback.call_args_list[1].kwargs["page"], 2)

    @patch("backend.app.integrations.datasource_runtime.perform_datasource_fetch")
    def test_sequential_mode_no_concurrency(self, fetch) -> None:
        fetch.side_effect = [
            (200, {"code": 0, "data": [{"id": 1}], "total": 2, "has_next": True}),
            (200, {"code": 0, "data": [{"id": 2}], "total": 2, "has_next": False}),
        ]
        list(iter_remote_pages(_datasource(), sequential=True))
        self.assertEqual(fetch.call_count, 2)


class CheckpointRepositorySmokeTests(unittest.TestCase):
    """Lightweight smoke tests using a mocked connection."""

    def _mock_conn(self, rows=None) -> MagicMock:
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = rows[0] if rows else None
        cursor.fetchall.return_value = rows or []
        conn.execute.return_value = cursor
        return conn

    @patch("backend.app.db.repositories.sync_checkpoint._utc_now")
    def test_create_checkpoint_upserts(self, mock_now) -> None:
        from backend.app.db.repositories.sync_checkpoint import create_checkpoint
        from datetime import datetime, timezone

        mock_now.return_value = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conn = self._mock_conn()
        create_checkpoint(conn, "erp_buy", "batch1", "version1")
        calls = [call.args[0] for call in conn.execute.call_args_list]
        self.assertTrue(
            any("INSERT INTO sys_sync_checkpoint" in sql for sql in calls),
            f"Expected an INSERT call, got: {calls}",
        )
        self.assertTrue(any("ON CONFLICT" in sql for sql in calls))

    @patch("backend.app.db.repositories.sync_checkpoint._utc_now")
    def test_is_checkpoint_stale_handles_various_types(self, mock_now) -> None:
        from backend.app.db.repositories.sync_checkpoint import is_checkpoint_stale
        from datetime import datetime, timezone, timedelta

        mock_now.return_value = datetime(2026, 1, 2, tzinfo=timezone.utc)
        self.assertTrue(is_checkpoint_stale(None))
        self.assertTrue(is_checkpoint_stale({}))
        self.assertFalse(is_checkpoint_stale({"updated_at": datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)}, hours=24))
        self.assertTrue(is_checkpoint_stale({"updated_at": datetime(2025, 12, 30, 0, 0, 0, tzinfo=timezone.utc)}, hours=24))
        self.assertFalse(is_checkpoint_stale({"updated_at": (datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)).isoformat()}, hours=24))


if __name__ == "__main__":
    unittest.main()
