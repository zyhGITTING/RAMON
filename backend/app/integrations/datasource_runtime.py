from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

import requests
import urllib3
from fastapi import HTTPException

from backend.app.core.outbound_http import OutboundRequestBlocked, safe_outbound_request
from backend.app.services.datasource_service import (
    DEFAULT_SYNC_MAX_PAGES,
    DEFAULT_SYNC_PAGE_SIZE,
    apply_env_auth_to_request_config,
    normalize_pagination_config,
    normalize_success_codes,
    parse_optional_positive_int,
    parse_positive_int,
)


class DatasourcePayloadError(HTTPException):
    """The upstream answered, but its payload cannot be trusted as a snapshot."""

    def __init__(self, reason_code: str, detail: str, *, page: int | None = None) -> None:
        self.reason_code = reason_code
        self.page = page
        super().__init__(status_code=422, detail=detail)


DEFAULT_PAGE_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = [1, 2, 4]


def _is_retriable_error(exc: BaseException, status_code: int | None = None) -> bool:
    """Return True for transient errors that are safe to retry page-by-page."""
    if isinstance(exc, (requests.RequestException, urllib3.exceptions.HTTPError, OSError)):
        return True
    if isinstance(exc, DatasourcePayloadError):
        # Data envelope errors indicate a bad upstream response, not a transient blip.
        return False
    if isinstance(exc, OutboundRequestBlocked):
        return False
    if isinstance(exc, HTTPException):
        code = int(exc.status_code) if status_code is None else int(status_code)
        # Retry timeouts, rate limits, and server errors. Do not retry client errors.
        if code in {429, 502, 503, 504} or code >= 599:
            return True
        return False
    return False


def _build_base_replacements(
    page: int,
    page_size: int,
    extra_replacements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now()
    replacements: dict[str, Any] = {
        "$page": page,
        "$page_size": page_size,
        "$today": now.strftime("%Y-%m-%d"),
        "$month_start": now.replace(day=1).strftime("%Y-%m-%d"),
    }
    if extra_replacements:
        replacements.update(extra_replacements)
    return replacements


def perform_datasource_fetch(
    datasource: dict[str, Any],
    page: int,
    page_size: int,
    timeout: int = 120,
    *,
    extra_replacements: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    request_config = apply_env_auth_to_request_config(datasource["source_key"], datasource.get("request_config", {}))
    headers = request_config.get("headers") if isinstance(request_config.get("headers"), dict) else {}
    payload_template = request_config.get("payload_template") if isinstance(request_config.get("payload_template"), dict) else {}
    replacements = _build_base_replacements(page, page_size, extra_replacements)
    payload = {key: replacements.get(value, value) for key, value in payload_template.items()}
    if not datasource.get("api_url"):
        raise HTTPException(status_code=400, detail="Datasource API URL is empty")
    verify_tls = bool(datasource.get("verify_tls", True))
    try:
        if (datasource.get("http_method") or "GET").upper() == "GET":
            response = safe_outbound_request(
                "GET",
                datasource["api_url"],
                headers=headers,
                params=payload,
                timeout=timeout,
                verify=verify_tls,
            )
        else:
            response = safe_outbound_request(
                "POST",
                datasource["api_url"],
                headers=headers,
                json=payload,
                timeout=timeout,
                verify=verify_tls,
            )
    except OutboundRequestBlocked as exc:
        raise HTTPException(status_code=400, detail=f"Datasource outbound request blocked: {exc}") from exc
    try:
        return response.status_code, response.json()
    except ValueError as exc:
        # Keep transport failures distinct from a successful HTTP response whose
        # body is malformed. The latter must never be interpreted as an empty set.
        if response.status_code >= 400:
            return response.status_code, None
        raise DatasourcePayloadError(
            "invalid_json",
            f"Upstream page {page} response is not valid JSON",
            page=page,
        ) from exc


def fetch_page_with_retry(
    datasource: dict[str, Any],
    page: int,
    page_size: int,
    timeout: int = 120,
    max_attempts: int = DEFAULT_PAGE_RETRY_ATTEMPTS,
    extra_replacements: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """Fetch one page, retrying transient failures up to max_attempts times."""
    last_error: BaseException | None = None
    last_status: int | None = None
    attempts = max(1, min(max_attempts, 10))
    for attempt in range(1, attempts + 1):
        try:
            return perform_datasource_fetch(
                datasource,
                page,
                page_size,
                timeout=timeout,
                extra_replacements=extra_replacements,
            )
        except (DatasourcePayloadError, OutboundRequestBlocked) as exc:
            # Never retry payload or policy errors.
            raise
        except Exception as exc:
            last_error = exc
            last_status = getattr(exc, "status_code", None)
            if not _is_retriable_error(exc, status_code=last_status):
                raise
            if attempt < attempts:
                sleep_seconds = _RETRY_BACKOFF_SECONDS[min(attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)]
                time.sleep(sleep_seconds)
    detail = f"Upstream page {page} failed after {attempts} attempts"
    if last_error is not None:
        detail += f": {last_error}"
    raise HTTPException(
        status_code=last_status or 504,
        detail=detail,
    ) from last_error


def extract_path(data: Any, path: str) -> Any:
    current = data
    for part in (path or "").split("."):
        if not part:
            continue
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _compute_extra_replacements(incremental_config: dict[str, Any] | None) -> dict[str, Any]:
    """Prepare incremental template variables for a sync run.

    If the caller has already precomputed a time window (from sync_data.py),
    emit the full set of placeholder formats. Otherwise fall back to the
    legacy per-strategy defaults.
    """
    if not incremental_config or not incremental_config.get("enabled"):
        return {}
    extras: dict[str, Any] = {}

    # Precomputed time window takes precedence.
    window_keys = {"start_date", "end_date", "start_datetime", "end_datetime", "start_timestamp_ms", "end_timestamp_ms"}
    if window_keys.issubset(incremental_config):
        extras["$start_date"] = incremental_config["start_date"]
        extras["$end_date"] = incremental_config["end_date"]
        extras["$start_datetime"] = incremental_config["start_datetime"]
        extras["$end_datetime"] = incremental_config["end_datetime"]
        extras["$start_timestamp_ms"] = incremental_config["start_timestamp_ms"]
        extras["$end_timestamp_ms"] = incremental_config["end_timestamp_ms"]
        return extras

    strategy = str(incremental_config.get("strategy") or "full")
    field = str(incremental_config.get("field") or "").strip()
    fmt = str(incremental_config.get("format") or "string").strip()
    watermark = str(incremental_config.get("watermark_value") or incremental_config.get("initial_value") or "").strip()
    cursor = str(incremental_config.get("cursor_value") or "").strip()
    business_id = str(incremental_config.get("business_id_value") or "").strip()

    if strategy == "watermark" and watermark:
        extras["$watermark"] = watermark
        if fmt == "datetime" and " " not in watermark and len(watermark) == 10:
            extras["$watermark"] = f"{watermark} 00:00:00"
    if strategy == "cursor" and cursor:
        extras["$cursor"] = cursor
    if strategy == "business_id" and business_id:
        extras["$business_id"] = business_id
    if strategy == "date_range":
        lookback_days = max(1, int(incremental_config.get("lookback_days", 7) or 7))
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=lookback_days)
        extras["$start_date"] = start_dt.strftime("%Y-%m-%d")
        extras["$end_date"] = end_dt.strftime("%Y-%m-%d")
    return extras


def iter_remote_pages(
    datasource: dict[str, Any],
    row_limit: int | None = None,
    *,
    should_stop: Any | None = None,
    start_page: int = 1,
    sequential: bool = False,
    checkpoint_callback: Any | None = None,
) -> Any:
    request_config = datasource.get("request_config") if isinstance(datasource.get("request_config"), dict) else {}
    pagination = normalize_pagination_config(request_config.get("pagination"))
    page_size = parse_positive_int(pagination.get("page_size"), DEFAULT_SYNC_PAGE_SIZE)
    max_rows_raw = row_limit if row_limit is not None else pagination.get("max_rows")
    max_rows = parse_optional_positive_int(max_rows_raw)
    max_pages = parse_positive_int(pagination.get("max_pages"), DEFAULT_SYNC_MAX_PAGES)
    code_key = str(pagination.get("code_key") or "code")
    success_codes = normalize_success_codes(pagination.get("success_codes"))
    data_key = str(pagination.get("data_key") or "data")
    total_key = str(pagination.get("total_key") or "total")
    has_next_key = str(pagination.get("has_next_key") or "has_next")
    concurrent_workers = min(5, max(1, int(pagination.get("concurrent_workers", 5))))
    request_timeout = parse_positive_int(pagination.get("timeout"), 120)
    incremental_config = datasource.get("incremental_config") if isinstance(datasource.get("incremental_config"), dict) else None
    extra_replacements = _compute_extra_replacements(incremental_config)

    start_page = max(1, int(start_page or 1))
    # When resuming we must fetch sequentially so checkpoint page numbers are monotonic.
    use_sequential = sequential or start_page > 1

    def _parse_has_next(has_next_value: Any) -> bool | None:
        if isinstance(has_next_value, bool):
            return has_next_value
        if isinstance(has_next_value, (int, float)):
            return bool(has_next_value)
        if isinstance(has_next_value, str) and has_next_value.strip():
            lowered = has_next_value.strip().lower()
            if lowered in {"true", "1", "yes", "y"}:
                return True
            elif lowered in {"false", "0", "no", "n"}:
                return False
        return None

    def _fetch_page(page: int) -> tuple[int, list[dict[str, Any]], Any, bool | None]:
        if callable(should_stop) and should_stop():
            raise RuntimeError("Sync cancelled")
        status_code, body = fetch_page_with_retry(
            datasource,
            page,
            page_size,
            timeout=request_timeout,
            extra_replacements=extra_replacements,
        )
        if status_code >= 400:
            raise HTTPException(status_code=400, detail=f"Upstream returned HTTP {status_code}")
        if not isinstance(body, dict):
            raise DatasourcePayloadError(
                "invalid_envelope",
                f"Upstream page {page} JSON root must be an object",
                page=page,
            )
        code_value = extract_path(body, code_key) if code_key else status_code
        if success_codes and code_value not in success_codes:
            raise HTTPException(status_code=400, detail=f"Upstream returned business code {code_value}")
        batch = extract_path(body, data_key)
        if batch is None:
            raise DatasourcePayloadError(
                "missing_data_field",
                f"Upstream page {page} is missing configured data field '{data_key}'",
                page=page,
            )
        if not isinstance(batch, list):
            raise DatasourcePayloadError(
                "non_array_data_field",
                f"Upstream page {page} data field '{data_key}' must be an array",
                page=page,
            )
        invalid_row_count = sum(1 for item in batch if not isinstance(item, dict))
        if invalid_row_count:
            raise DatasourcePayloadError(
                "non_object_rows",
                f"Upstream page {page} contains {invalid_row_count} non-object row(s)",
                page=page,
            )
        total_value = extract_path(body, total_key) if total_key else None
        has_next_value = extract_path(body, has_next_key) if has_next_key else None
        has_next = _parse_has_next(has_next_value)
        return page, batch, total_value, has_next

    _, batch1, total_value, has_next_1 = _fetch_page(start_page)
    reported_total: int | None = None
    if total_value not in (None, ""):
        try:
            reported_total = int(total_value)
        except (TypeError, ValueError):
            reported_total = None

    target_rows = min(max_rows, reported_total) if max_rows is not None and reported_total is not None else (max_rows or reported_total)
    needed_pages = math.ceil(target_rows / page_size) if target_rows else None
    if needed_pages is not None and needed_pages > max_pages:
        raise HTTPException(
            status_code=400,
            detail=f"Datasource requires {needed_pages} pages, exceeding max_pages={max_pages}",
        )

    fetched = 0

    def _limited(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if max_rows is None:
            return batch
        return batch[: max(0, max_rows - fetched)]

    def _invoke_checkpoint(page: int, batch: list[dict[str, Any]]) -> None:
        if callable(checkpoint_callback):
            checkpoint_callback(page=page, batch=batch)

    first_batch = _limited(batch1)
    fetched += len(first_batch)
    _invoke_checkpoint(start_page, first_batch)
    yield start_page, first_batch, reported_total, has_next_1
    if (max_rows is not None and fetched >= max_rows) or has_next_1 is False or (has_next_1 is None and len(batch1) < page_size):
        return

    if needed_pages is not None and not use_sequential:
        last_page = needed_pages
        if last_page <= start_page:
            return
        with ThreadPoolExecutor(max_workers=concurrent_workers, thread_name_prefix="datamid-fetch") as executor:
            pending: dict[int, Any] = {}
            next_submit = start_page + 1
            for _ in range(concurrent_workers):
                if next_submit > last_page:
                    break
                pending[next_submit] = executor.submit(_fetch_page, next_submit)
                next_submit += 1
            next_yield = start_page + 1
            while next_yield <= last_page:
                _, batch, total_value, has_next = pending.pop(next_yield).result()
                limited_batch = _limited(batch)
                fetched += len(limited_batch)
                _invoke_checkpoint(next_yield, limited_batch)
                yield next_yield, limited_batch, reported_total, has_next
                if max_rows is not None and fetched >= max_rows:
                    return
                if next_submit <= last_page:
                    pending[next_submit] = executor.submit(_fetch_page, next_submit)
                    next_submit += 1
                next_yield += 1
        return

    last_has_next = has_next_1
    last_batch_size = len(batch1)
    for page in range(start_page + 1, max_pages + 1):
        _, batch, total_value, has_next = _fetch_page(page)
        limited_batch = _limited(batch)
        fetched += len(limited_batch)
        _invoke_checkpoint(page, limited_batch)
        yield page, limited_batch, reported_total, has_next
        last_has_next = has_next
        last_batch_size = len(batch)
        if (max_rows is not None and fetched >= max_rows) or has_next is False or (has_next is None and len(batch) < page_size):
            return
    if last_has_next is True or (last_has_next is None and last_batch_size >= page_size):
        raise HTTPException(status_code=400, detail=f"Datasource pagination reached max_pages={max_pages} before completion")


def list_remote_rows(
    datasource: dict[str, Any],
    row_limit: int | None = None,
    *,
    progress_callback: Any | None = None,
    should_stop: Any | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    rows: list[dict[str, Any]] = []
    reported_total: int | None = None
    for page, batch, page_total, has_next in iter_remote_pages(datasource, row_limit=row_limit, should_stop=should_stop):
        rows.extend(batch)
        if page_total is not None:
            reported_total = page_total
        if callable(progress_callback):
            progress_callback(
                page=page,
                batch_size=len(batch),
                fetched=len(rows),
                reported_total=reported_total,
                has_next=has_next,
            )
    return rows, reported_total
