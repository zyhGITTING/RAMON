INSERT INTO sys_datasource (source_key, source_name, table_name, http_method, api_url, extra_config, enabled, created_at, updated_at, platform_id)
VALUES (
    'erp_subcontract',
    'ERP托工明细表',
    'ods_erp_subcontract',
    'POST',
    'https://www.ramon.net.cn/api/ramon-api/aiagent/getERPSubcontractDetail',
    '{
        "description": "ERP 托工明细、外协进度与验收情况",
        "business_time_field": "tw_dd",
        "chart_field": "cus_name",
        "field_labels": {
            "qty_mrp": "制造数量",
            "usr_name": "制单人",
            "tw_dd": "托工日期",
            "tw_no": "外协单号",
            "cus_name": "托工供应商",
            "mrp_name": "托外货名称",
            "unit_mrp": "单位",
            "est_dd": "预交日",
            "up": "外协单价",
            "amtn": "加工未税",
            "qty_rtn": "已缴库数量",
            "qty_lost": "不合格量",
            "qty_chk": "验收合格量"
        },
        "searchable_fields": ["tw_no", "cus_name", "mrp_name", "usr_name"],
        "quality_rules": {"allow_empty_publish": false, "row_count_change": {"reject_ratio": 0.8}},
        "request": {
            "headers": {"Accept": "application/json"},
            "requires_auth_env": "DATAMID_RAMON_AUTH",
            "payload_template": {"erp_account": "衡阳钢铁", "page": "$page", "page_size": "$page_size"},
            "pagination": {
                "page_size": 100,
                "max_pages": 10000,
                "code_key": "code",
                "success_codes": [0, 200],
                "data_key": "data",
                "total_key": "total"
            }
        },
        "response": {},
        "verify_tls": true,
        "incremental": {"enabled": false, "strategy": "full", "merge_strategy": "append", "field": "", "format": "string", "lookback_seconds": 86400, "initial_value": ""}
    }'::jsonb,
    1,
    NOW(),
    NOW(),
    1
)
ON CONFLICT (source_key) DO UPDATE SET
    source_name = EXCLUDED.source_name,
    table_name = EXCLUDED.table_name,
    http_method = EXCLUDED.http_method,
    api_url = EXCLUDED.api_url,
    extra_config = EXCLUDED.extra_config,
    enabled = EXCLUDED.enabled,
    platform_id = EXCLUDED.platform_id,
    updated_at = NOW();
