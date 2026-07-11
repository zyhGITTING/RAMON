from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests
from fastapi import HTTPException

from backend.app.db.repositories.config import get_config, set_config
from backend.app.services.datasource_service import (
    normalize_field_label_map,
    normalize_identifier,
    normalize_pagination_config,
    normalize_searchable_fields,
)

LLM_SERVICES_CONFIG_KEY = "llm_services"
SEARCH_HINTS = ("code", "name", "no", "po", "erp", "supplier", "material", "model", "dept", "dep")


def build_llm_service_endpoint(base_url: str) -> str:
    raw = str(base_url or "").strip().rstrip("/")
    if not raw:
        return ""
    if raw.lower().endswith("/chat/completions"):
        return raw
    return raw + "/chat/completions"


def parse_json_array(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def normalize_llm_service(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    base_url = str(item.get("base_url") or "").strip()
    return {
        "id": int(item.get("id") or 0),
        "name": str(item.get("name") or "").strip(),
        "base_url": base_url.rstrip("/"),
        "endpoint": build_llm_service_endpoint(base_url),
        "api_key": str(item.get("api_key") or "").strip(),
        "model": str(item.get("model") or "").strip(),
        "enabled": bool(item.get("enabled", True)),
        "is_default": bool(item.get("is_default", False)),
        "verify_tls": bool(item.get("verify_tls", True)),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
    }


def mask_secret(value: str) -> str:
    secret = str(value or "").strip()
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}{'*' * (len(secret) - 8)}{secret[-4:]}"


def load_llm_services(conn) -> list[dict[str, Any]]:
    raw_items = parse_json_array(get_config(conn, LLM_SERVICES_CONFIG_KEY, "[]"))
    items: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for raw in raw_items:
        item = normalize_llm_service(raw)
        if not item or item["id"] <= 0 or item["id"] in seen_ids:
            continue
        seen_ids.add(item["id"])
        items.append(item)
    if items and not any(item.get("is_default") for item in items):
        items[0]["is_default"] = True
    return items


def save_llm_services(conn, items: list[dict[str, Any]]) -> None:
    payload = []
    for item in items:
        normalized = normalize_llm_service(item)
        if not normalized or normalized["id"] <= 0:
            continue
        payload.append(
            {
                "id": normalized["id"],
                "name": normalized["name"],
                "base_url": normalized["base_url"],
                "api_key": normalized["api_key"],
                "model": normalized["model"],
                "enabled": normalized["enabled"],
                "is_default": normalized["is_default"],
                "verify_tls": normalized["verify_tls"],
                "created_at": normalized["created_at"],
                "updated_at": normalized["updated_at"],
            }
        )
    set_config(conn, LLM_SERVICES_CONFIG_KEY, json.dumps(payload, ensure_ascii=False))


def list_llm_services(conn, include_secret: bool = False) -> list[dict[str, Any]]:
    items = load_llm_services(conn)
    result: list[dict[str, Any]] = []
    for item in items:
        payload = {
            "id": item["id"],
            "name": item["name"],
            "base_url": item["base_url"],
            "endpoint": item["endpoint"],
            "model": item["model"],
            "enabled": item["enabled"],
            "is_default": item["is_default"],
            "verify_tls": item["verify_tls"],
            "created_at": item["created_at"],
            "updated_at": item["updated_at"],
            "has_api_key": bool(item["api_key"]),
            "api_key_masked": mask_secret(item["api_key"]),
        }
        if include_secret:
            payload["api_key"] = item["api_key"]
        result.append(payload)
    return result


def get_llm_service_by_id(conn, service_id: int, include_secret: bool = False) -> dict[str, Any] | None:
    for item in list_llm_services(conn, include_secret=include_secret):
        if int(item["id"]) == int(service_id):
            return item
    return None


def extract_chat_completion_text(body: dict[str, Any]) -> str:
    if isinstance(body.get("output_text"), str) and body.get("output_text"):
        return str(body.get("output_text"))
    choices = body.get("choices") if isinstance(body.get("choices"), list) else []
    if not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else {}
    content = message.get("content") if isinstance(message, dict) else ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def extract_json_object_from_text(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        raise HTTPException(status_code=502, detail="LLM did not return content")
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start : end + 1])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    raise HTTPException(status_code=502, detail="LLM did not return valid JSON")


def normalize_parameter_docs(items: Any) -> list[dict[str, Any]]:
    values = items if isinstance(items, list) else []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        name = normalize_identifier(str(item.get("name") or item.get("key") or item.get("field") or ""))
        if not name or name in seen:
            continue
        seen.add(name)
        fixed_values = item.get("fixed_values") if isinstance(item.get("fixed_values"), list) else []
        result.append(
            {
                "name": name,
                "label": str(item.get("label") or item.get("title") or name).strip(),
                "description": str(item.get("description") or "").strip(),
                "required": bool(item.get("required", False)),
                "example": str(item.get("example") or "").strip(),
                "notes": str(item.get("notes") or "").strip(),
                "fixed_values": [str(value) for value in fixed_values],
            }
        )
    return result


def build_payload_template_from_parameter_docs(parameter_docs: list[dict[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for item in parameter_docs:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if name in {"page", "pageindex", "page_index", "current_page"}:
            payload[name] = "$page"
        elif name in {"page_size", "pagesize", "limit", "rows"}:
            payload[name] = "$page_size"
        elif item.get("example") not in {None, ""}:
            payload[name] = item["example"]
        elif item.get("fixed_values"):
            payload[name] = item["fixed_values"][0]
        else:
            payload[name] = ""
    return payload


def normalize_doc_parse_result(result: dict[str, Any], filename: str = "") -> dict[str, Any]:
    filename_stem = Path(filename).stem if filename else ""
    source_name = str(result.get("source_name") or filename_stem or "数据源").strip() or "数据源"
    source_key = normalize_identifier(str(result.get("source_key") or source_name or filename_stem or "datasource"))
    raw_table_name = str(result.get("table_name") or f"ods_{source_key}").strip() or f"ods_{source_key}"
    table_name = normalize_identifier(raw_table_name)
    if not table_name.startswith("ods_"):
        table_name = f"ods_{table_name}"
    http_method = str(result.get("http_method") or "POST").strip().upper()
    if http_method not in {"GET", "POST"}:
        http_method = "POST"
    field_labels = normalize_field_label_map(result.get("field_labels"))
    searchable_fields = normalize_searchable_fields(result.get("searchable_fields"))
    if not searchable_fields and field_labels:
        for field_name in field_labels.keys():
            if any(token in field_name.lower() for token in SEARCH_HINTS):
                searchable_fields.append(field_name)
        searchable_fields = searchable_fields[:6]
    parameter_docs = normalize_parameter_docs(result.get("parameter_docs"))
    request_config = result.get("request_config") if isinstance(result.get("request_config"), dict) else {}
    headers = request_config.get("headers") if isinstance(request_config.get("headers"), dict) else {}
    headers.setdefault("Accept", "application/json")
    request_config["headers"] = headers
    payload_template = request_config.get("payload_template") if isinstance(request_config.get("payload_template"), dict) else {}
    if not payload_template and parameter_docs:
        payload_template = build_payload_template_from_parameter_docs(parameter_docs)
    request_config["payload_template"] = payload_template
    request_config["pagination"] = normalize_pagination_config(request_config.get("pagination"))
    if parameter_docs:
        request_config["parameter_docs"] = parameter_docs
    response_config = result.get("response_config") if isinstance(result.get("response_config"), dict) else {}
    quality_rules = result.get("quality_rules") if isinstance(result.get("quality_rules"), dict) else {}
    warnings_raw = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    warnings = [str(item).strip() for item in warnings_raw if str(item).strip()]
    return {
        "source_key": source_key,
        "source_name": source_name,
        "table_name": table_name,
        "http_method": http_method,
        "api_url": str(result.get("api_url") or "").strip(),
        "description": str(result.get("description") or source_name).strip(),
        "verify_tls": bool(result.get("verify_tls", True)),
        "searchable_fields": searchable_fields,
        "field_labels": field_labels,
        "parameter_docs": parameter_docs,
        "request_config": request_config,
        "response_config": response_config,
        "quality_rules": quality_rules,
        "warnings": warnings,
    }


def request_llm_doc_parse(service: dict[str, Any], document_text: str, filename: str = "") -> dict[str, Any]:
    doc_text = str(document_text or "").strip()
    if not doc_text:
        raise HTTPException(status_code=400, detail="Document text is required")
    warnings: list[str] = []
    if len(doc_text) > 30000:
        doc_text = doc_text[:30000]
        warnings.append("Document text was truncated to 30000 characters.")
    system_prompt = (
        "You analyze API/interface documents for a Chinese data middleware. "
        "Return one valid JSON object only. Do not use markdown fences."
    )
    user_prompt = f"""
请从下面接口文档中提取数据源配置 JSON，字段包括：
source_key, source_name, table_name, http_method, api_url, description, verify_tls,
searchable_fields, field_labels, parameter_docs, request_config, response_config, quality_rules, warnings。

要求：
1. source_key 使用 snake_case。
2. table_name 以 ods_ 开头。
3. http_method 只能是 GET 或 POST，无法判断时用 POST。
4. request_config.headers 至少包含 Accept: application/json。
5. request_config.payload_template 中分页参数用 "$page" 和 "$page_size"。
6. response_config 描述接口响应里的数据列表、总数、状态码等路径。
7. 只返回 JSON 对象，不要 markdown。

文件名：{filename or ""}
文档内容：
{doc_text}
""".strip()
    endpoint = service.get("endpoint") or build_llm_service_endpoint(service.get("base_url", ""))
    if not endpoint:
        raise HTTPException(status_code=400, detail="LLM endpoint is not configured")
    headers = {
        "Authorization": f"Bearer {service.get('api_key', '')}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": service.get("model", ""),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    verify_tls = bool(service.get("verify_tls", True))
    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=60, verify=verify_tls)
        if response.status_code >= 400:
            fallback_payload = dict(payload)
            fallback_payload.pop("response_format", None)
            response = requests.post(endpoint, headers=headers, json=fallback_payload, timeout=60, verify=verify_tls)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"LLM request failed: {exc}") from exc
    if response.status_code >= 400:
        try:
            error_payload = response.json()
            error_text = extract_chat_completion_text(error_payload) or json.dumps(error_payload, ensure_ascii=False)
        except ValueError:
            error_text = response.text
        raise HTTPException(status_code=502, detail=f"LLM request failed: HTTP {response.status_code} {error_text[:300]}")
    try:
        body = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="LLM response is not JSON") from exc
    parsed = normalize_doc_parse_result(extract_json_object_from_text(extract_chat_completion_text(body)), filename)
    if warnings:
        parsed["warnings"] = warnings + parsed.get("warnings", [])
    return parsed
