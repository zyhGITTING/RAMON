from __future__ import annotations

import json
import os
import re
from typing import Any

from backend.app.db.repositories.config import now_text
from backend.app.services.permission_service import (
    has_source_permission,
    resolve_field_access,
    resolve_permission_origin,
)

RAMON_AUTH_ENV_VAR = "DATAMID_RAMON_AUTH"
DEFAULT_RAMON_AUTH = os.getenv(RAMON_AUTH_ENV_VAR, "").strip()
OFFICIAL_SOURCE_KEYS = {"erp_buy", "stock", "erp_safe_stock", "srm_purchase"}
DEFAULT_SYNC_PAGE_SIZE = max(1, int(os.getenv("DATAMID_SYNC_PAGE_SIZE", os.getenv("DATAMID_SYNC_ROW_LIMIT", "300"))))
ODS_VERSION_RETENTION_COUNT = 20

STANDARD_FIELD_CATALOG: dict[str, dict[str, str]] = {
    "purchase_request_code": {"standard_field_name": "请购单号", "business_domain": "procurement", "entity_code": "purchase_request", "entity_role": "identifier", "metric_unit": "", "definition": "跨系统统一的请购单主编号"},
    "purchase_order_code": {"standard_field_name": "采购单号", "business_domain": "procurement", "entity_code": "purchase_order", "entity_role": "identifier", "metric_unit": "", "definition": "跨系统统一的采购订单主编号"},
    "material_code": {"standard_field_name": "物料编码", "business_domain": "master_data", "entity_code": "material", "entity_role": "identifier", "metric_unit": "", "definition": "跨系统统一的物料主编码"},
    "material_name": {"standard_field_name": "物料名称", "business_domain": "master_data", "entity_code": "material", "entity_role": "name", "metric_unit": "", "definition": "跨系统统一的物料名称"},
    "material_model": {"standard_field_name": "规格型号", "business_domain": "master_data", "entity_code": "material", "entity_role": "attribute", "metric_unit": "", "definition": "物料的规格型号或产品型号"},
    "material_short_name": {"standard_field_name": "产品简称", "business_domain": "master_data", "entity_code": "material", "entity_role": "attribute", "metric_unit": "", "definition": "物料或产品的简称"},
    "supplier_name": {"standard_field_name": "供应商", "business_domain": "supplier", "entity_code": "supplier", "entity_role": "name", "metric_unit": "", "definition": "供应商显示名称"},
    "request_department": {"standard_field_name": "需求部门", "business_domain": "organization", "entity_code": "department", "entity_role": "dimension", "metric_unit": "", "definition": "提出采购或请购需求的部门"},
    "creator_name": {"standard_field_name": "创建人", "business_domain": "organization", "entity_code": "employee", "entity_role": "dimension", "metric_unit": "", "definition": "单据创建人姓名"},
    "requester_employee_no": {"standard_field_name": "申请人工号", "business_domain": "organization", "entity_code": "employee", "entity_role": "identifier", "metric_unit": "", "definition": "申请人的员工编号"},
    "purchaser_name": {"standard_field_name": "采购员", "business_domain": "organization", "entity_code": "employee", "entity_role": "dimension", "metric_unit": "", "definition": "采购经办人姓名"},
    "unit_name": {"standard_field_name": "单位", "business_domain": "common", "entity_code": "unit", "entity_role": "dimension", "metric_unit": "", "definition": "业务数量对应的计量单位"},
    "brand_name": {"standard_field_name": "品牌", "business_domain": "master_data", "entity_code": "brand", "entity_role": "dimension", "metric_unit": "", "definition": "物料或商品品牌"},
    "summary_text": {"standard_field_name": "摘要", "business_domain": "common", "entity_code": "document", "entity_role": "attribute", "metric_unit": "", "definition": "单据摘要或备注"},
    "usage_purpose": {"standard_field_name": "用途", "business_domain": "common", "entity_code": "document", "entity_role": "attribute", "metric_unit": "", "definition": "物料或采购用途说明"},
    "purchase_quantity": {"standard_field_name": "采购数量", "business_domain": "procurement", "entity_code": "purchase_order", "entity_role": "measure", "metric_unit": "qty", "definition": "采购订单中的采购数量"},
    "demand_quantity": {"standard_field_name": "需求数量", "business_domain": "procurement", "entity_code": "purchase_request", "entity_role": "measure", "metric_unit": "qty", "definition": "业务需求提出的数量"},
    "remaining_quantity": {"standard_field_name": "剩余数量", "business_domain": "procurement", "entity_code": "purchase_order", "entity_role": "measure", "metric_unit": "qty", "definition": "订单或需求尚未完成的剩余数量"},
    "inventory_on_hand": {"standard_field_name": "现有库存", "business_domain": "inventory", "entity_code": "inventory_item", "entity_role": "measure", "metric_unit": "qty", "definition": "当前可用现有库存数量"},
    "safety_stock_quantity": {"standard_field_name": "安全库存", "business_domain": "inventory", "entity_code": "inventory_item", "entity_role": "measure", "metric_unit": "qty", "definition": "库存安全库存下限"},
    "shortage_quantity": {"standard_field_name": "缺口量", "business_domain": "inventory", "entity_code": "inventory_item", "entity_role": "measure", "metric_unit": "qty", "definition": "满足需求仍存在的库存缺口数量"},
    "in_transit_quantity": {"standard_field_name": "在途数量", "business_domain": "inventory", "entity_code": "inventory_item", "entity_role": "measure", "metric_unit": "qty", "definition": "在运输或待入库的数量"},
    "in_production_quantity": {"standard_field_name": "在制数量", "business_domain": "inventory", "entity_code": "inventory_item", "entity_role": "measure", "metric_unit": "qty", "definition": "生产在制中的数量"},
    "coverage_days": {"standard_field_name": "安全天数", "business_domain": "inventory", "entity_code": "inventory_item", "entity_role": "measure", "metric_unit": "day", "definition": "库存覆盖可支撑的预计天数"},
    "inventory_category": {"standard_field_name": "库存类别", "business_domain": "inventory", "entity_code": "inventory_item", "entity_role": "dimension", "metric_unit": "", "definition": "库存分析的类别分组"},
    "abc_class": {"standard_field_name": "ABC分类", "business_domain": "inventory", "entity_code": "inventory_item", "entity_role": "dimension", "metric_unit": "", "definition": "库存 ABC 分类标签"},
    "warehouse_name": {"standard_field_name": "默认仓库", "business_domain": "inventory", "entity_code": "warehouse", "entity_role": "dimension", "metric_unit": "", "definition": "物料默认所属仓库"},
    "project_name": {"standard_field_name": "项目名称", "business_domain": "project", "entity_code": "project", "entity_role": "name", "metric_unit": "", "definition": "采购或需求关联的项目名称"},
    "contract_code": {"standard_field_name": "合同号", "business_domain": "contract", "entity_code": "contract", "entity_role": "identifier", "metric_unit": "", "definition": "采购对应的合同编号"},
    "document_status": {"standard_field_name": "状态", "business_domain": "common", "entity_code": "document", "entity_role": "status", "metric_unit": "", "definition": "单据当前状态"},
    "purchase_request_date": {"standard_field_name": "请购日期", "business_domain": "procurement", "entity_code": "purchase_request", "entity_role": "date", "metric_unit": "", "definition": "请购单据的申请日期"},
    "required_arrival_date": {"standard_field_name": "要求到货日期", "business_domain": "procurement", "entity_code": "purchase_order", "entity_role": "date", "metric_unit": "", "definition": "需求或订单要求到货日期"},
    "purchase_order_date": {"standard_field_name": "采购日期", "business_domain": "procurement", "entity_code": "purchase_order", "entity_role": "date", "metric_unit": "", "definition": "采购订单日期"},
    "snapshot_date": {"standard_field_name": "统计日期", "business_domain": "inventory", "entity_code": "inventory_snapshot", "entity_role": "date", "metric_unit": "", "definition": "库存快照统计日期"},
}
COMMON_STANDARD_FIELD_MAP: dict[str, str] = {
    "prd_no": "material_code",
    "material_code": "material_code",
    "prd_name": "material_name",
    "material_name": "material_name",
    "prd_spc": "material_model",
    "model": "material_model",
    "create_name": "creator_name",
    "chuangjianren_mc": "creator_name",
    "status": "document_status",
    "contract_code": "contract_code",
    "supplier_name": "supplier_name",
}
SOURCE_STANDARD_FIELD_MAP: dict[str, dict[str, str]] = {
    "erp_buy": {
        "sq_no": "purchase_request_code",
        "qinggou_sl": "demand_quantity",
        "prd_ut": "unit_name",
        "dep": "request_department",
        "sq_dd": "purchase_request_date",
        "qinggou_yujiaoqi": "required_arrival_date",
        "zhaiyao": "summary_text",
        "yongtu": "usage_purpose",
        "mark_name": "brand_name",
        "sal_no": "requester_employee_no",
    },
    "stock": {
        "prd_snm": "material_short_name",
        "indx_name": "inventory_category",
        "abc": "abc_class",
        "moren_cangku_mc": "warehouse_name",
        "xianyou_kucun": "inventory_on_hand",
        "qty_min": "safety_stock_quantity",
        "kucu_queliang": "shortage_quantity",
        "sum_qty_on_way": "in_transit_quantity",
        "sum_qty_on_prc": "in_production_quantity",
        "need_days": "coverage_days",
        "createdate": "snapshot_date",
    },
    "erp_safe_stock": {
        "prd_no": "material_code",
        "prd_name": "material_name",
        "prd_spc": "material_model",
        "prd_snm": "material_short_name",
        "indx_name": "inventory_category",
        "abc": "abc_class",
        "moren_cangku_mc": "warehouse_name",
        "xianyou_kucun": "inventory_on_hand",
        "qty_min": "safety_stock_quantity",
        "kucu_queliang": "shortage_quantity",
        "sum_qty_on_way": "in_transit_quantity",
        "sum_qty_on_prc": "in_production_quantity",
        "need_days": "coverage_days",
    },
    "srm_purchase": {
        "po_code": "purchase_order_code",
        "erp_request_no": "purchase_request_code",
        "quantity": "purchase_quantity",
        "required_quantity": "demand_quantity",
        "remaining_quantity": "remaining_quantity",
        "required_arrival_date": "required_arrival_date",
        "po_date": "purchase_order_date",
        "purchaser": "purchaser_name",
        "create_name": "creator_name",
        "product_name": "project_name",
    },
}


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _row_get(datasource: Any, key: str) -> Any:
    if isinstance(datasource, dict):
        return datasource.get(key)
    return datasource[key]


def build_default_datasources() -> list[dict[str, Any]]:
    erp_compno = os.getenv("DATAMID_ERP_COMPNO", "ERP_GT").strip() or "ERP_GT"

    def request_cfg(payload_template: dict[str, Any]) -> dict[str, Any]:
        return {
            "headers": {"Accept": "application/json"},
            "requires_auth_env": RAMON_AUTH_ENV_VAR,
            "payload_template": payload_template,
            "pagination": {
                "page_size": DEFAULT_SYNC_PAGE_SIZE,
                "max_pages": 100,
                "code_key": "code",
                "success_codes": [0, 200],
                "data_key": "data",
                "total_key": "total",
            },
        }

    return [
        {
            "source_key": "erp_buy",
            "source_name": "ERP采购请购",
            "table_name": "ods_erp_buy",
            "platform_name": "ERP",
            "http_method": "POST",
            "api_url": os.getenv("DATAMID_ERP_BUY_URL", "").strip(),
            "description": "ERP 请购明细与交期跟踪",
            "chart_field": "dep",
            "field_labels": {
                "sq_no": "请购单号",
                "prd_no": "物料编码",
                "prd_name": "物料名称",
                "prd_spc": "规格型号",
                "qinggou_sl": "请购数量",
                "prd_ut": "单位",
                "dep": "部门",
                "chuangjianren_mc": "创建人",
                "sq_dd": "请购日期",
                "qinggou_yujiaoqi": "预计交期",
                "zhaiyao": "摘要",
                "yongtu": "用途",
                "mark_name": "品牌",
                "sal_no": "申请人编号",
            },
            "searchable_fields": ["sq_no", "prd_no", "prd_name", "prd_spc", "dep", "chuangjianren_mc"],
            "quality_rules": {"row_count_change": {"warn_ratio": 0.8}},
            "request": request_cfg({"compno": erp_compno, "page": "$page", "page_size": "$page_size"}),
            "response": {},
        },
        {
            "source_key": "stock",
            "source_name": "库存预警",
            "table_name": "ods_stock",
            "platform_name": "库存",
            "http_method": "POST",
            "api_url": os.getenv("DATAMID_STOCK_URL", "").strip(),
            "description": "库存预警、安全库存和缺口分析",
            "chart_field": "indx_name",
            "field_labels": {
                "prd_no": "物料编码",
                "prd_name": "物料名称",
                "prd_spc": "规格型号",
                "prd_snm": "产品简称",
                "indx_name": "类别",
                "abc": "ABC分类",
                "moren_cangku_mc": "默认仓库",
                "xianyou_kucun": "现有库存",
                "qty_min": "安全库存",
                "kucu_queliang": "缺口量",
                "sum_qty_on_way": "在途数量",
                "sum_qty_on_prc": "在制数量",
                "need_days": "安全天数",
                "createdate": "统计日期",
            },
            "searchable_fields": ["prd_no", "prd_name", "prd_spc", "prd_snm", "indx_name"],
            "quality_rules": {"row_count_change": {"warn_ratio": 0.8}},
            "request": request_cfg({"compno": erp_compno, "page": "$page", "page_size": "$page_size"}),
            "response": {},
        },
        {
            "source_key": "erp_safe_stock",
            "source_name": "物料安全库存不足预警",
            "table_name": "ods_erp_safe_stock",
            "platform_name": "ERP",
            "http_method": "POST",
            "api_url": os.getenv("DATAMID_ERP_SAFE_STOCK_URL", "").strip(),
            "description": "ERP 物料安全库存、现有库存与缺口预警",
            "chart_field": "indx_name",
            "field_labels": {
                "id": "主键ID",
                "prd_no": "品号",
                "prd_name": "品名",
                "prd_snm": "简称",
                "prd_spc": "型号",
                "abc": "ABC分类",
                "knd": "大类",
                "idx1": "中类代码",
                "indx_name": "中类名称",
                "need_days": "前置天数",
                "zuixiao_caigouliang": "最小采购量",
                "cgdl": "采购大类",
                "cgxl": "采购小类",
                "sum_qty_on_way": "在途量",
                "sum_qty_on_prc": "在制量",
                "sum_qty_on_rsv": "未发预占量",
                "ck_cishu": "盘点周转次数",
                "qty_min": "安全库存",
                "moren_cangku_bh": "默认仓库编码",
                "moren_cangku_mc": "默认仓库名称",
                "anquankucun_lx": "安全库存类型",
                "xianyou_kucun": "现有库存",
                "kucu_queliang": "库存缺量",
            },
            "searchable_fields": ["prd_no", "prd_name", "prd_spc", "prd_snm", "indx_name"],
            "quality_rules": {"row_count_change": {"warn_ratio": 0.8}},
            "request": request_cfg({"page": "$page", "page_size": "$page_size"}),
            "response": {},
        },
        {
            "source_key": "srm_purchase",
            "source_name": "SRM采购需求",
            "table_name": "ods_srm_purchase",
            "platform_name": "SRM",
            "http_method": "POST",
            "api_url": os.getenv("DATAMID_SRM_PURCHASE_URL", "").strip(),
            "description": "SRM 采购需求、供应商和到货进度",
            "chart_field": "status",
            "field_labels": {
                "po_code": "采购单号",
                "erp_request_no": "ERP请购单号",
                "supplier_name": "供应商",
                "material_code": "物料编码",
                "material_name": "物料名称",
                "model": "型号",
                "quantity": "采购数量",
                "required_quantity": "需求数量",
                "remaining_quantity": "剩余数量",
                "required_arrival_date": "要求到货日期",
                "po_date": "采购日期",
                "status": "状态",
                "purchaser": "采购员",
                "create_name": "创建人",
                "product_name": "项目名称",
                "contract_code": "合同号",
            },
            "searchable_fields": ["po_code", "erp_request_no", "supplier_name", "material_code", "material_name", "model"],
            "quality_rules": {"row_count_change": {"warn_ratio": 0.8}},
            "request": request_cfg({"page": "$page", "page_size": "$page_size"}),
            "response": {},
        },
    ]


DEFAULT_DATASOURCES = build_default_datasources()
DATASOURCE_MAP = {item["source_key"]: item for item in DEFAULT_DATASOURCES}


def get_datasource_detail(conn, source_key: str, include_disabled: bool = False):
    sql = "SELECT * FROM sys_datasource WHERE source_key = ?" + ("" if include_disabled else " AND enabled = 1")
    return conn.execute(sql + " LIMIT 1", (source_key,)).fetchone()


def get_datasource_map() -> dict[str, Any]:
    return DATASOURCE_MAP


def build_datasource_extra_config(
    description: str,
    chart_field: str,
    field_labels: dict[str, Any],
    request_config: dict[str, Any],
    response_config: dict[str, Any],
    searchable_fields: list[str],
    quality_rules: dict[str, Any],
    verify_tls: bool,
) -> dict[str, Any]:
    return {
        "description": description,
        "chart_field": chart_field,
        "field_labels": field_labels or {},
        "request": request_config or {},
        "response": response_config or {},
        "searchable_fields": searchable_fields or [],
        "quality_rules": quality_rules or {},
        "verify_tls": verify_tls,
    }


def parse_json_object(raw: str | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def parse_positive_int(value: Any, default: int) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, (int, float)):
            parsed = int(value)
        else:
            text = str(value or "").strip()
            if not text or text.startswith("$"):
                raise ValueError
            parsed = int(text)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def parse_optional_positive_int(value: Any) -> int | None:
    try:
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, (int, float)):
            parsed = int(value)
        else:
            text = str(value or "").strip()
            if not text or text in {"0", "$page", "$page_size"} or text.startswith("$"):
                return None
            parsed = int(text)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def normalize_success_codes(value: Any) -> list[Any]:
    raw_items = value if isinstance(value, list) else ([value] if value not in (None, "") else [])
    items: list[Any] = []
    for item in raw_items:
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            items.append(int(item))
            continue
        text = str(item or "").strip()
        if not text:
            continue
        try:
            items.append(int(text))
        except ValueError:
            items.append(text)
    return items or [0, 200]


def normalize_pagination_config(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    payload = dict(raw)
    payload["page_size"] = parse_positive_int(raw.get("page_size"), DEFAULT_SYNC_PAGE_SIZE)
    payload["max_pages"] = parse_positive_int(raw.get("max_pages"), 100)
    max_rows = parse_optional_positive_int(raw.get("max_rows"))
    if max_rows is None:
        payload.pop("max_rows", None)
    else:
        payload["max_rows"] = max_rows
    payload["code_key"] = str(raw.get("code_key") or "code").strip() or "code"
    payload["data_key"] = str(raw.get("data_key") or "data").strip() or "data"
    payload["total_key"] = str(raw.get("total_key") or "total").strip() or "total"
    payload["has_next_key"] = str(raw.get("has_next_key") or "has_next").strip() or "has_next"
    payload["success_codes"] = normalize_success_codes(raw.get("success_codes"))
    return payload


def sanitize_request_config(source_key: str, request_config: dict[str, Any], token: str = "") -> dict[str, Any]:
    payload = json.loads(json.dumps(request_config or {}, ensure_ascii=False))
    headers = payload.get("headers") if isinstance(payload.get("headers"), dict) else {}
    existing_auth = headers.get("Authorization")
    headers.pop("Authorization", None)
    clean_token = token.strip()
    if source_key in OFFICIAL_SOURCE_KEYS:
        payload["requires_auth_env"] = RAMON_AUTH_ENV_VAR
    elif clean_token:
        # 兼容用户只填写纯 token 的情况，自动补 Bearer 前缀
        lower = clean_token.lower()
        if lower.startswith("bearer ") or lower.startswith("basic "):
            headers["Authorization"] = clean_token
        else:
            headers["Authorization"] = f"Bearer {clean_token}"
    elif existing_auth:
        headers["Authorization"] = existing_auth
    payload["headers"] = headers
    payload["pagination"] = normalize_pagination_config(payload.get("pagination"))
    return payload


def redact_request_config_for_response(request_config: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(request_config or {}, ensure_ascii=False))
    headers = payload.get("headers") if isinstance(payload.get("headers"), dict) else {}
    redacted_headers = {str(key): value for key, value in headers.items() if str(key).lower() != "authorization"}
    if redacted_headers:
        payload["headers"] = redacted_headers
    else:
        payload.pop("headers", None)
    payload.pop("requires_auth_env", None)
    return payload


def apply_env_auth_to_request_config(source_key: str, request_config: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(request_config or {}, ensure_ascii=False))
    headers = payload.get("headers") if isinstance(payload.get("headers"), dict) else {}
    if (payload.get("requires_auth_env") == RAMON_AUTH_ENV_VAR or source_key in OFFICIAL_SOURCE_KEYS) and DEFAULT_RAMON_AUTH and not headers.get("Authorization"):
        headers["Authorization"] = DEFAULT_RAMON_AUTH
    payload["headers"] = headers
    return payload


def normalize_identifier(name: str) -> str:
    value = re.sub(r"[^0-9a-zA-Z_]+", "_", (name or "").strip().lower()).strip("_") or "field"
    if value[0].isdigit():
        value = f"f_{value}"
    if value in {"id", "sync_batch_id", "synced_at"}:
        value = f"src_{value}"
    return value


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in (record or {}).items():
        column = normalize_identifier(str(key))
        if isinstance(value, (dict, list)):
            payload[column] = json.dumps(value, ensure_ascii=False)
        elif value is None:
            payload[column] = ""
        else:
            payload[column] = str(value)
    return payload


def normalize_field_label_map(field_labels: Any) -> dict[str, str]:
    items: list[tuple[str, str]] = []
    if isinstance(field_labels, dict):
        items = [(str(key), str(value)) for key, value in field_labels.items()]
    elif isinstance(field_labels, list):
        for item in field_labels:
            if not isinstance(item, dict):
                continue
            items.append((str(item.get("name") or item.get("field") or item.get("key") or ""), str(item.get("label") or item.get("value") or "")))
    payload: dict[str, str] = {}
    for raw_name, raw_label in items:
        field_name = normalize_identifier(raw_name)
        field_label = str(raw_label or "").strip()
        if not field_name or not field_label:
            continue
        payload[field_name] = field_label
    return payload


def normalize_searchable_fields(items: Any) -> list[str]:
    values = items if isinstance(items, list) else []
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        field_name = normalize_identifier(str(item or ""))
        if not field_name or field_name in seen:
            continue
        seen.add(field_name)
        result.append(field_name)
    return result


def infer_field_data_type(values: list[str]) -> str:
    clean = [str(value).strip() for value in values if str(value).strip()]
    if not clean:
        return "text"
    sample = clean[:10]
    if all(re.fullmatch(r"-?\d+", value) for value in sample):
        return "integer"
    if all(re.fullmatch(r"-?\d+(?:\.\d+)?", value) for value in sample):
        return "number"
    if all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) for value in sample):
        return "date"
    if all(re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", value) for value in sample):
        return "datetime"
    if all((value.startswith("{") and value.endswith("}")) or (value.startswith("[") and value.endswith("]")) for value in sample):
        return "json"
    if all(value.lower() in {"true", "false", "0", "1", "yes", "no"} for value in sample):
        return "boolean"
    return "text"


def collect_column_samples(conn, table_name: str, columns: list[str], limit: int = 5) -> dict[str, list[str]]:
    samples: dict[str, list[str]] = {column: [] for column in columns}
    if not columns or not table_exists(conn, table_name):
        return samples
    select_columns = ", ".join(_quote_identifier(column) for column in columns)
    where_sql = " WHERE is_current = 1" if has_table_column(conn, table_name, "is_current") else ""
    rows = conn.execute(
        f"SELECT {select_columns} FROM {_quote_identifier(table_name)}{where_sql} ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    for row in rows:
        for column in columns:
            value = row[column]
            if value in (None, ""):
                continue
            if len(samples[column]) >= limit:
                continue
            samples[column].append(str(value))
    return samples


def sync_datasource_field_meta(conn, datasource) -> None:
    source_key = str(_row_get(datasource, "source_key") or "")
    table_name = str(_row_get(datasource, "table_name") or "")
    if not source_key or not table_name:
        return
    config = parse_datasource_config(datasource)
    default_labels = get_config_field_labels(datasource)
    response_config = config.get("response") if isinstance(config.get("response"), dict) else {}
    field_paths = response_config.get("field_paths") if isinstance(response_config.get("field_paths"), dict) else {}
    configured_searchable = config.get("searchable_fields") if isinstance(config.get("searchable_fields"), list) else []
    columns: list[str] = []
    if table_exists(conn, table_name):
        columns = [column for column in get_table_columns(conn, table_name) if column not in {"id", "sync_batch_id", "synced_at", "sync_version", "is_current"}]
    elif default_labels:
        columns = list(default_labels.keys())
    if not columns:
        return
    samples = collect_column_samples(conn, table_name, columns)
    existing_rows = conn.execute("SELECT * FROM sys_field_meta WHERE source_key = ?", (source_key,)).fetchall()
    existing_map = {row["field_name"]: row for row in existing_rows}
    now = now_text()
    for column in columns:
        row = existing_map.get(column)
        example_value = samples.get(column, [""])[0] if samples.get(column) else ""
        data_type = infer_field_data_type(samples.get(column, []))
        field_label = default_labels.get(column, column)
        source_path = str(field_paths.get(column) or column)
        is_searchable = 1 if column in configured_searchable else 0
        standard_defaults = build_standard_field_defaults(source_key, column, field_label)
        if row:
            conn.execute(
                """
                UPDATE sys_field_meta
                SET table_name = ?,
                    field_label = CASE WHEN COALESCE(field_label, '') = '' THEN ? ELSE field_label END,
                    source_path = CASE WHEN COALESCE(source_path, '') = '' THEN ? ELSE source_path END,
                    standard_field_code = CASE WHEN COALESCE(standard_field_code, '') = '' THEN ? ELSE standard_field_code END,
                    standard_field_name = CASE WHEN COALESCE(standard_field_name, '') = '' THEN ? ELSE standard_field_name END,
                    business_domain = CASE WHEN COALESCE(business_domain, '') = '' THEN ? ELSE business_domain END,
                    entity_code = CASE WHEN COALESCE(entity_code, '') = '' THEN ? ELSE entity_code END,
                    entity_role = CASE WHEN COALESCE(entity_role, '') = '' THEN ? ELSE entity_role END,
                    data_type = CASE WHEN COALESCE(data_type, '') IN ('', 'text') AND ? != 'text' THEN ? ELSE data_type END,
                    metric_unit = CASE WHEN COALESCE(metric_unit, '') = '' THEN ? ELSE metric_unit END,
                    value_dictionary = CASE WHEN COALESCE(value_dictionary, '') = '' THEN ? ELSE value_dictionary END,
                    definition = CASE WHEN COALESCE(definition, '') = '' THEN ? ELSE definition END,
                    example_value = CASE WHEN ? != '' THEN ? ELSE example_value END,
                    is_searchable = ?,
                    sensitivity_level = CASE WHEN COALESCE(sensitivity_level, '') IN ('', 'normal') THEN ? ELSE sensitivity_level END,
                    is_active = 1,
                    updated_at = ?
                WHERE source_key = ? AND field_name = ?
                """,
                (
                    table_name,
                    field_label,
                    source_path,
                    standard_defaults["standard_field_code"],
                    standard_defaults["standard_field_name"],
                    standard_defaults["business_domain"],
                    standard_defaults["entity_code"],
                    standard_defaults["entity_role"],
                    data_type,
                    data_type,
                    standard_defaults["metric_unit"],
                    standard_defaults["value_dictionary"],
                    standard_defaults["definition"],
                    example_value,
                    example_value,
                    is_searchable,
                    standard_defaults["sensitivity_level"],
                    now,
                    source_key,
                    column,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO sys_field_meta (
                    source_key, table_name, field_name, field_label, source_path, data_type,
                    standard_field_code, standard_field_name, business_domain, entity_code, entity_role,
                    description, example_value, is_searchable, is_filterable, is_displayed,
                    sensitivity_level, mask_rule, permission_scope, metric_unit, value_dictionary, definition, is_active,
                    created_at, updated_at, updated_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, 0, 1, ?, '', '', ?, ?, ?, 1, ?, ?, 'system')
                """,
                (
                    source_key,
                    table_name,
                    column,
                    field_label,
                    source_path,
                    data_type,
                    standard_defaults["standard_field_code"],
                    standard_defaults["standard_field_name"],
                    standard_defaults["business_domain"],
                    standard_defaults["entity_code"],
                    standard_defaults["entity_role"],
                    example_value,
                    is_searchable,
                    standard_defaults["sensitivity_level"],
                    standard_defaults["metric_unit"],
                    standard_defaults["value_dictionary"],
                    standard_defaults["definition"],
                    now,
                    now,
                ),
            )
    placeholders = ", ".join(["?"] * len(columns))
    conn.execute(
        f"UPDATE sys_field_meta SET is_active = 0, updated_at = ? WHERE source_key = ? AND field_name NOT IN ({placeholders})",
        [now, source_key, *columns],
    )


def parse_datasource_config(datasource: Any) -> dict[str, Any]:
    raw = _row_get(datasource, "extra_config")
    stored = parse_json_object(raw)
    source_key = str(_row_get(datasource, "source_key") or "")
    defaults = DATASOURCE_MAP.get(source_key.strip(), {})
    default_request = sanitize_request_config(source_key.strip(), defaults.get("request", {})) if defaults else {}
    default_response = defaults.get("response", {}) if isinstance(defaults.get("response", {}), dict) else {}
    default_labels = defaults.get("field_labels", {}) if isinstance(defaults.get("field_labels", {}), dict) else {}
    merged = build_datasource_extra_config(
        description=str(stored.get("description", defaults.get("description", "")) or ""),
        chart_field=str(stored.get("chart_field", defaults.get("chart_field", "")) or ""),
        field_labels={**default_labels, **(stored.get("field_labels") if isinstance(stored.get("field_labels"), dict) else {})},
        request_config=stored.get("request") if isinstance(stored.get("request"), dict) else default_request,
        response_config={**default_response, **(stored.get("response") if isinstance(stored.get("response"), dict) else {})},
        searchable_fields=stored.get("searchable_fields") if isinstance(stored.get("searchable_fields"), list) else defaults.get("searchable_fields", []),
        quality_rules=stored.get("quality_rules") if isinstance(stored.get("quality_rules"), dict) else defaults.get("quality_rules", {}),
        verify_tls=bool(stored.get("verify_tls", defaults.get("verify_tls", True) if defaults else True)),
    )
    return merged


def get_config_field_labels(datasource: Any) -> dict[str, str]:
    config = parse_datasource_config(datasource)
    field_labels = config.get("field_labels") if isinstance(config.get("field_labels"), dict) else {}
    return {str(key).lower(): str(value) for key, value in field_labels.items() if str(key).strip()}


def build_standard_field_defaults(source_key: str, field_name: str, field_label: str) -> dict[str, str]:
    standard_code = SOURCE_STANDARD_FIELD_MAP.get(source_key, {}).get(field_name) or COMMON_STANDARD_FIELD_MAP.get(field_name, "")
    catalog = STANDARD_FIELD_CATALOG.get(standard_code, {}) if standard_code else {}
    sensitivity_level = "internal" if any(token in field_name.lower() for token in ("employee", "sal_no", "create_by", "creator", "purchaser")) else "normal"
    return {
        "standard_field_code": standard_code or "",
        "standard_field_name": catalog.get("standard_field_name", field_label or field_name),
        "business_domain": catalog.get("business_domain", ""),
        "entity_code": catalog.get("entity_code", ""),
        "entity_role": catalog.get("entity_role", ""),
        "metric_unit": catalog.get("metric_unit", ""),
        "definition": catalog.get("definition", ""),
        "value_dictionary": "",
        "sensitivity_level": catalog.get("sensitivity_level", sensitivity_level),
    }


def table_exists(conn, table_name: str) -> bool:
    row = conn.execute("SELECT to_regclass(?) AS table_name", (table_name,)).fetchone()
    return bool(row) and row["table_name"] is not None


def get_table_columns(conn, table_name: str) -> list[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position",
        (table_name,),
    ).fetchall()
    return [row["column_name"] for row in rows]


def has_table_column(conn, table_name: str, column_name: str) -> bool:
    return column_name in get_table_columns(conn, table_name)


def get_table_row_count(conn, table_name: str, current_only: bool = True, sync_version: str | None = None) -> int:
    if not table_exists(conn, table_name):
        return 0
    where_sql = ""
    params: list[Any] = []
    if sync_version and has_table_column(conn, table_name, "sync_version"):
        where_sql = " WHERE sync_version = ?"
        params.append(sync_version)
    elif current_only and has_table_column(conn, table_name, "is_current"):
        where_sql = " WHERE is_current = 1"
    return int(conn.execute(f"SELECT COUNT(*) AS total FROM {_quote_identifier(table_name)}{where_sql}", params).fetchone()["total"])


def get_business_columns(conn, datasource: Any) -> list[str]:
    table_name = _row_get(datasource, "table_name")
    if table_exists(conn, table_name):
        return [column for column in get_table_columns(conn, table_name) if column not in {"id", "sync_batch_id", "synced_at", "sync_version", "is_current"}]
    meta_rows = conn.execute(
        "SELECT field_name FROM sys_field_meta WHERE source_key = ? AND is_active = 1 ORDER BY id",
        (_row_get(datasource, "source_key"),),
    ).fetchall()
    if meta_rows:
        return [str(row["field_name"]) for row in meta_rows]
    return list(get_config_field_labels(datasource).keys())


def visible_columns(field_access: dict[str, str], columns: list[str]) -> list[str]:
    """去掉 hide 列，保留 plain 与 mask（保持原顺序）。"""
    return [column for column in columns if field_access.get(column, "plain") != "hide"]


def list_field_meta(conn, datasource: Any, columns: list[str] | None = None) -> list[dict[str, Any]]:
    source_key = str(_row_get(datasource, "source_key") or "")
    table_name = str(_row_get(datasource, "table_name") or "")
    config = parse_datasource_config(datasource)
    default_labels = get_config_field_labels(datasource)
    configured_searchable = config.get("searchable_fields") if isinstance(config.get("searchable_fields"), list) else []
    if columns is None:
        columns = get_business_columns(conn, datasource)
    rows = conn.execute(
        "SELECT * FROM sys_field_meta WHERE source_key = ? AND is_active = 1 ORDER BY id",
        (source_key,),
    ).fetchall()
    row_map = {row["field_name"]: row for row in rows}
    items: list[dict[str, Any]] = []
    for column in columns:
        row = row_map.get(column)
        defaults = build_standard_field_defaults(source_key, column, default_labels.get(column, column))
        items.append(
            {
                "source_key": source_key,
                "table_name": table_name,
                "field_name": column,
                "field_label": (row["field_label"] if row and row["field_label"] else default_labels.get(column, column)),
                "source_path": (row["source_path"] if row and row["source_path"] else column),
                "standard_field_code": row["standard_field_code"] if row and row["standard_field_code"] else defaults["standard_field_code"],
                "standard_field_name": row["standard_field_name"] if row and row["standard_field_name"] else defaults["standard_field_name"],
                "business_domain": row["business_domain"] if row and row["business_domain"] else defaults["business_domain"],
                "entity_code": row["entity_code"] if row and row["entity_code"] else defaults["entity_code"],
                "entity_role": row["entity_role"] if row and row["entity_role"] else defaults["entity_role"],
                "data_type": (row["data_type"] if row and row["data_type"] else "text"),
                "description": row["description"] if row else "",
                "example_value": row["example_value"] if row else "",
                "is_searchable": bool(row["is_searchable"]) if row else column in configured_searchable,
                "is_filterable": bool(row["is_filterable"]) if row else False,
                "is_displayed": bool(row["is_displayed"]) if row else True,
                "sensitivity_level": row["sensitivity_level"] if row and row["sensitivity_level"] else defaults["sensitivity_level"],
                "mask_rule": row["mask_rule"] if row else "",
                "permission_scope": row["permission_scope"] if row else "",
                "is_restricted": bool(row["is_restricted"]) if row else False,
                "restricted_access": (row["restricted_access"] if row and row["restricted_access"] else "hide"),
                "metric_unit": row["metric_unit"] if row and row["metric_unit"] else defaults["metric_unit"],
                "value_dictionary": row["value_dictionary"] if row and row["value_dictionary"] else defaults["value_dictionary"],
                "definition": row["definition"] if row and row["definition"] else defaults["definition"],
                "is_active": bool(row["is_active"]) if row else True,
                "updated_at": row["updated_at"] if row else "",
                "updated_by": row["updated_by"] if row else "system",
            }
        )
    return items


def build_field_labels_from_meta(field_meta: list[dict[str, Any]]) -> dict[str, str]:
    return {item["field_name"]: item["field_label"] for item in field_meta}


def serialize_datasource(conn, datasource, user) -> dict[str, Any]:
    config = parse_datasource_config(datasource)
    request_config = config.get("request") if isinstance(config.get("request"), dict) else {}
    request_config = sanitize_request_config(datasource["source_key"], request_config)
    is_admin = user is not None and user["role"] == "admin"
    has_token = bool(DEFAULT_RAMON_AUTH) if datasource["source_key"] in OFFICIAL_SOURCE_KEYS else bool((request_config.get("headers") or {}).get("Authorization"))
    response_request_config = request_config if is_admin else redact_request_config_for_response(request_config)
    platform_name = None
    if datasource["platform_id"]:
        platform = conn.execute("SELECT name FROM sys_platform WHERE id = ? LIMIT 1", (datasource["platform_id"],)).fetchone()
        platform_name = platform["name"] if platform else None
    all_columns = get_business_columns(conn, datasource)
    field_access = resolve_field_access(conn, user, datasource["source_key"], all_columns)
    columns = visible_columns(field_access, all_columns)
    field_meta = list_field_meta(conn, datasource, columns)
    for meta_item in field_meta:
        meta_item["masked"] = field_access.get(str(meta_item.get("field_name", ""))) == "mask"
    field_labels = build_field_labels_from_meta(field_meta)
    has_perm = has_source_permission(conn, user, datasource["source_key"])
    current_version_row = conn.execute(
        """
        SELECT sync_version, finished_at
        FROM sys_sync_version
        WHERE source_key = ? AND is_current = 1
        ORDER BY finished_at DESC, id DESC
        LIMIT 1
        """,
        (datasource["source_key"],),
    ).fetchone()
    version_count = int(
        conn.execute("SELECT COUNT(*) AS total FROM sys_sync_version WHERE source_key = ?", (datasource["source_key"],)).fetchone()["total"]
    )
    return {
        "id": datasource["id"],
        "source_key": datasource["source_key"],
        "source_name": datasource["source_name"],
        "table_name": datasource["table_name"],
        "http_method": datasource["http_method"],
        "api_url": (datasource["api_url"] or "") if is_admin else "",
        "platform_id": datasource["platform_id"],
        "platform_name": platform_name,
        "description": config.get("description", ""),
        "field_labels": field_labels,
        "field_meta": field_meta,
        "field_count": len(columns),
        "row_count": get_table_row_count(conn, datasource["table_name"]) if has_perm else None,
        "last_sync_at": datasource["last_sync_at"],
        "current_sync_version": current_version_row["sync_version"] if current_version_row else "",
        "version_count": version_count,
        "last_status": datasource["last_status"] or "",
        "last_message": datasource["last_message"] or "",
        "last_quality_status": datasource["last_quality_status"] or "",
        "last_quality_report": datasource["last_quality_report"] or "",
        "searchable_fields": config.get("searchable_fields", []),
        "quality_rules": config.get("quality_rules", {}),
        "request_config": response_request_config,
        "response_config": config.get("response", {}),
        "verify_tls": bool(config.get("verify_tls", True)),
        "has_permission": has_perm,
        "permission_origin": resolve_permission_origin(conn, user, datasource["source_key"]),
        "has_token": has_token,
        "enabled": int(datasource["enabled"]),
        "technical": {"table_name": datasource["table_name"], "http_method": datasource["http_method"]},
    }


def list_catalog_items(conn, user, keyword: str = "") -> list[dict[str, Any]]:
    items = [
        serialize_datasource(conn, row, user)
        for row in conn.execute("SELECT * FROM sys_datasource WHERE enabled = 1 ORDER BY COALESCE(platform_id, 9999), id").fetchall()
    ]
    kw = keyword.strip().lower()
    if kw:
        items = [
            item
            for item in items
            if kw in (item["source_name"] or "").lower()
            or kw in (item["source_key"] or "").lower()
            or kw in (item["platform_name"] or "").lower()
            or kw in (item["description"] or "").lower()
        ]
    return items


def list_platform_catalog(conn, user) -> list[dict[str, Any]]:
    items = []
    all_ds = conn.execute("SELECT * FROM sys_datasource WHERE enabled = 1 ORDER BY id").fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for ds in all_ds:
        grouped.setdefault(ds["platform_id"] or 0, []).append(serialize_datasource(conn, ds, user))
    for platform in conn.execute("SELECT * FROM sys_platform ORDER BY sort_order, id").fetchall():
        items.append(
            {
                "id": platform["id"],
                "name": platform["name"],
                "description": platform["description"] or "",
                "datasources": grouped.get(platform["id"], []),
            }
        )
    return items


def ensure_ods_table(conn, table_name: str, rows: list[dict[str, Any]]) -> list[str]:
    normalized_rows = [normalize_record(row) for row in rows]
    business_columns = sorted({column for row in normalized_rows for column in row.keys()})
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_quote_identifier(table_name)} (
            id SERIAL PRIMARY KEY,
            sync_batch_id TEXT NOT NULL,
            synced_at TEXT NOT NULL,
            sync_version TEXT,
            is_current INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    existing = set(get_table_columns(conn, table_name))
    for column in business_columns:
        if column not in existing:
            conn.execute(f"ALTER TABLE {_quote_identifier(table_name)} ADD COLUMN {_quote_identifier(column)} TEXT")
    if "sync_version" not in existing:
        conn.execute(f"ALTER TABLE {_quote_identifier(table_name)} ADD COLUMN sync_version TEXT")
    if "is_current" not in existing:
        conn.execute(f"ALTER TABLE {_quote_identifier(table_name)} ADD COLUMN is_current INTEGER NOT NULL DEFAULT 1")
    return business_columns


def replace_ods_table_rows(conn, table_name: str, rows: list[dict[str, Any]], sync_batch_id: str, sync_version: str) -> None:
    business_columns = ensure_ods_table(conn, table_name, rows)
    conn.execute(f"UPDATE {_quote_identifier(table_name)} SET is_current = 0 WHERE is_current = 1")
    if not rows:
        return
    normalized_rows = [normalize_record(row) for row in rows]
    columns = ["sync_batch_id", "synced_at", "sync_version", "is_current", *business_columns]
    placeholders = ", ".join(["?"] * len(columns))
    sql = f"INSERT INTO {_quote_identifier(table_name)} ({', '.join(_quote_identifier(c) for c in columns)}) VALUES ({placeholders})"
    synced_at = now_text()
    conn.executemany(sql, [[sync_batch_id, synced_at, sync_version, 1, *[row.get(column, "") for column in business_columns]] for row in normalized_rows])


def prune_ods_table_versions(conn, source_key: str, table_name: str, keep_versions: int = ODS_VERSION_RETENTION_COUNT) -> int:
    if keep_versions <= 0 or not table_exists(conn, table_name) or not has_table_column(conn, table_name, "sync_version"):
        return 0
    keep_rows = conn.execute(
        """
        SELECT sync_version
        FROM sys_sync_version
        WHERE source_key = ?
          AND status IN ('success', 'warning', 'empty')
          AND COALESCE(sync_version, '') != ''
        ORDER BY CASE WHEN is_current = 1 THEN 0 ELSE 1 END, finished_at DESC, id DESC
        LIMIT ?
        """,
        (source_key, keep_versions),
    ).fetchall()
    keep_set = {str(row["sync_version"]) for row in keep_rows if str(row["sync_version"] or "").strip()}
    if not keep_set:
        return 0
    old_rows = conn.execute(
        """
        SELECT sync_version
        FROM sys_sync_version
        WHERE source_key = ?
          AND status IN ('success', 'warning', 'empty')
          AND COALESCE(sync_version, '') != ''
        """,
        (source_key,),
    ).fetchall()
    old_versions = sorted({str(row["sync_version"]) for row in old_rows if str(row["sync_version"] or "").strip() and str(row["sync_version"]) not in keep_set})
    if not old_versions:
        return 0
    placeholders = ", ".join(["?"] * len(old_versions))
    conn.execute(f"DELETE FROM {_quote_identifier(table_name)} WHERE sync_version IN ({placeholders})", old_versions)
    conn.execute(
        f"DELETE FROM sys_sync_version WHERE source_key = ? AND sync_version IN ({placeholders})",
        [source_key, *old_versions],
    )
    return len(old_versions)
