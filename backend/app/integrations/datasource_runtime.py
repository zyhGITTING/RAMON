from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

import requests
from fastapi import HTTPException

from backend.app.services.datasource_service import (
    DEFAULT_SYNC_PAGE_SIZE,
    apply_env_auth_to_request_config,
    normalize_pagination_config,
    normalize_success_codes,
    parse_optional_positive_int,
    parse_positive_int,
)


def perform_datasource_fetch(datasource: dict[str, Any], page: int, page_size: int) -> tuple[int, Any]:
    request_config = apply_env_auth_to_request_config(datasource["source_key"], datasource.get("request_config", {}))
    headers = request_config.get("headers") if isinstance(request_config.get("headers"), dict) else {}
    payload_template = request_config.get("payload_template") if isinstance(request_config.get("payload_template"), dict) else {}
    replacements = {
        "$page": page,
        "$page_size": page_size,
        "$today": datetime.now().strftime("%Y-%m-%d"),
        "$month_start": datetime.now().replace(day=1).strftime("%Y-%m-%d"),
    }
    payload = {key: replacements.get(value, value) for key, value in payload_template.items()}
    if not datasource.get("api_url"):
        raise HTTPException(status_code=400, detail="Datasource API URL is empty")
    verify_tls = bool(datasource.get("verify_tls", True))
    if (datasource.get("http_method") or "GET").upper() == "GET":
        response = requests.get(datasource["api_url"], headers=headers, params=payload, timeout=30, verify=verify_tls)
    else:
        response = requests.post(datasource["api_url"], headers=headers, json=payload, timeout=30, verify=verify_tls)
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {}


def extract_path(data: Any, path: str) -> Any:
    current = data
    for part in (path or "").split("."):
        if not part:
            continue
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def list_remote_rows(
    datasource: dict[str, Any],
    row_limit: int | None = None,
    *,
    progress_callback: Any | None = None,
    should_stop: Any | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    request_config = datasource.get("request_config") if isinstance(datasource.get("request_config"), dict) else {}
    pagination = normalize_pagination_config(request_config.get("pagination"))
    page_size = parse_positive_int(pagination.get("page_size"), DEFAULT_SYNC_PAGE_SIZE)
    max_rows_raw = row_limit if row_limit is not None else pagination.get("max_rows")
    max_rows = parse_optional_positive_int(max_rows_raw)
    max_pages = parse_positive_int(pagination.get("max_pages"), 100)
    code_key = str(pagination.get("code_key") or "code")
    success_codes = normalize_success_codes(pagination.get("success_codes"))
    data_key = str(pagination.get("data_key") or "data")
    total_key = str(pagination.get("total_key") or "total")
    has_next_key = str(pagination.get("has_next_key") or "has_next")
    concurrent_workers = min(3, max(1, int(pagination.get("concurrent_workers", 3))))

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
        status_code, body = perform_datasource_fetch(datasource, page, page_size)
        if status_code >= 400:
            raise HTTPException(status_code=400, detail=f"Upstream returned HTTP {status_code}")
        code_value = extract_path(body, code_key) if code_key else status_code
        if success_codes and code_value not in success_codes:
            raise HTTPException(status_code=400, detail=f"Upstream returned business code {code_value}")
        batch = extract_path(body, data_key)
        if batch is None:
            batch = []
        if isinstance(batch, dict):
            batch = [batch]
        if not isinstance(batch, list):
            batch = []
        total_value = extract_path(body, total_key) if total_key else None
        has_next_value = extract_path(body, has_next_key) if has_next_key else None
        has_next = _parse_has_next(has_next_value)
        return page, [item for item in batch if isinstance(item, dict)], total_value, has_next

    # 先拉第 1 页，获取 total 和 has_next
    page_results: dict[int, tuple[list[dict[str, Any]], Any, bool | None]] = {}
    p1, batch1, total_value, has_next_1 = _fetch_page(1)
    page_results[p1] = (batch1, total_value, has_next_1)

    reported_total: int | None = None
    if total_value not in (None, ""):
        try:
            reported_total = int(total_value)
        except (TypeError, ValueError):
            reported_total = None

    # 计算还需要拉取的页数
    if has_next_1 is False:
        remaining_pages: list[int] = []
    elif has_next_1 is None and len(batch1) < page_size:
        remaining_pages = []
    else:
        if max_rows is not None:
            target = min(max_rows, reported_total or max_rows)
        else:
            target = reported_total
        if target:
            needed_pages = math.ceil(target / page_size)
        else:
            needed_pages = max_pages
        remaining_pages = list(range(2, min(needed_pages + 1, max_pages + 1)))

    # 并发拉取剩余页，默认 3 路并发
    if remaining_pages:
        with ThreadPoolExecutor(max_workers=concurrent_workers, thread_name_prefix="datamid-fetch") as executor:
            futures = {executor.submit(_fetch_page, p): p for p in remaining_pages}
            for future in as_completed(futures):
                page, batch, total_value, has_next = future.result()
                page_results[page] = (batch, total_value, has_next)

    # 按页顺序合并并回调进度
    rows: list[dict[str, Any]] = []
    final_reported_total: int | None = None
    for page in sorted(page_results.keys()):
        batch, total_value, has_next = page_results[page]
        rows.extend(batch)
        if final_reported_total is None and total_value not in (None, ""):
            try:
                final_reported_total = int(total_value)
            except (TypeError, ValueError):
                pass
        if callable(progress_callback):
            progress_callback(
                page=page,
                batch_size=len(batch),
                fetched=len(rows),
                reported_total=final_reported_total,
                has_next=has_next,
            )
        if max_rows is not None and len(rows) >= max_rows:
            rows = rows[:max_rows]
            break

    return rows, final_reported_total if final_reported_total is not None else reported_total
