from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from backend.app.integrations.datasource_runtime import DatasourcePayloadError, iter_remote_pages
from backend.sync_data import _evaluate_quality, _sync_one_datasource


def _datasource() -> dict:
    return {
        "source_key": "test_source",
        "request_config": {
            "pagination": {
                "page_size": 10,
                "max_pages": 2,
                "code_key": "code",
                "success_codes": [0],
                "data_key": "data",
                "total_key": "total",
                "has_next_key": "has_next",
            }
        },
    }


class PayloadValidationTests(unittest.TestCase):
    @patch("backend.app.integrations.datasource_runtime.perform_datasource_fetch")
    def test_missing_data_field_is_rejected(self, fetch) -> None:
        fetch.return_value = (200, {"code": 0, "total": 0})
        with self.assertRaisesRegex(DatasourcePayloadError, "missing configured data field"):
            list(iter_remote_pages(_datasource()))

    @patch("backend.app.integrations.datasource_runtime.perform_datasource_fetch")
    def test_non_array_data_field_is_rejected(self, fetch) -> None:
        fetch.return_value = (200, {"code": 0, "data": {"id": 1}})
        with self.assertRaisesRegex(DatasourcePayloadError, "must be an array"):
            list(iter_remote_pages(_datasource()))

    @patch("backend.app.integrations.datasource_runtime.perform_datasource_fetch")
    def test_non_object_rows_are_rejected_instead_of_filtered(self, fetch) -> None:
        fetch.return_value = (200, {"code": 0, "data": [{"id": 1}, "bad-row"]})
        with self.assertRaisesRegex(DatasourcePayloadError, "non-object row"):
            list(iter_remote_pages(_datasource()))


class QualityGateTests(unittest.TestCase):
    def test_first_empty_snapshot_is_rejected_by_default(self) -> None:
        status, report, _, publish = _evaluate_quality(0, 0, {})
        self.assertEqual(status, "rejected")
        self.assertFalse(publish)
        self.assertFalse(report["published"])

    def test_empty_publish_requires_literal_true(self) -> None:
        for unsafe_value in (1, "true", "1"):
            status, _, _, publish = _evaluate_quality(0, 100, {"allow_empty_publish": unsafe_value})
            self.assertEqual(status, "rejected")
            self.assertFalse(publish)

        status, report, _, publish = _evaluate_quality(0, 100, {"allow_empty_publish": True})
        self.assertEqual(status, "empty")
        self.assertTrue(publish)
        self.assertTrue(report["published"])

    def test_significant_drop_is_rejected(self) -> None:
        status, report, _, publish = _evaluate_quality(
            79,
            100,
            {"row_count_change": {"reject_ratio": 0.8}},
        )
        self.assertEqual(status, "rejected")
        self.assertEqual(report["reason_code"], "row_count_drop")
        self.assertFalse(publish)

    def test_old_warn_ratio_is_a_backward_compatible_publish_gate(self) -> None:
        status, _, _, publish = _evaluate_quality(
            49,
            100,
            {"row_count_change": {"warn_ratio": 0.5}},
        )
        self.assertEqual(status, "rejected")
        self.assertFalse(publish)

    def test_quality_evaluation_precedes_current_switch(self) -> None:
        source = inspect.getsource(_sync_one_datasource)
        self.assertLess(source.index("_evaluate_quality("), source.index("finalize_ods_staging_rows("))
        self.assertIn("if publish_candidate:", source)

    def test_exception_path_discards_committed_candidate_table(self) -> None:
        source = inspect.getsource(_sync_one_datasource)
        exception_path = source.split("except Exception as exc:", 1)[1]
        self.assertIn("if staging_name:", exception_path)
        self.assertIn("discard_ods_staging_rows(conn, staging_name)", exception_path)


if __name__ == "__main__":
    unittest.main()
