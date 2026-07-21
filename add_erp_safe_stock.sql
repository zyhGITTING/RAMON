-- 添加 ERP 物料安全库存不足预警数据源
-- 执行方式：docker exec -i datamid-pg psql -U datamid_app -d datamid < add_erp_safe_stock.sql

-- 1. 确保 ERP 平台存在
INSERT INTO sys_platform (name, description, created_at, sort_order)
VALUES ('ERP', 'ERP 平台', NOW(), 0)
ON CONFLICT (name) DO NOTHING;

-- 2. 插入/更新数据源
INSERT INTO sys_datasource (
    source_key,
    source_name,
    table_name,
    http_method,
    api_url,
    extra_config,
    enabled,
    platform_id,
    created_at,
    updated_at
) VALUES (
    'erp_safe_stock',
    '物料安全库存不足预警',
    'ods_erp_safe_stock',
    'POST',
    'https://api.example.com/ramon-api/aiagent/getMaterialSafeStockData',
    '{
        "description": "物料安全库存不足预警数据",
        "chart_field": "INDX_NAME",
        "field_labels": {
            "ID": "主键ID",
            "PRD_NO": "品号",
            "PRD_NAME": "品名",
            "PRD_SNM": "简称",
            "PRD_SPC": "型号",
            "ABC": "ABC分类",
            "KND": "大类",
            "IDX1": "中类代号",
            "INDX_NAME": "中类名称",
            "NEED_DAYS": "前置天数",
            "ZUIXIAO_CAIGOULIANG": "最小采购量",
            "CGDL": "采购大类",
            "CGXL": "采购小类",
            "SUM_QTY_ON_WAY": "在途量",
            "SUM_QTY_ON_PRC": "在制量",
            "SUM_QTY_ON_RSV": "未发预占量",
            "CK_CISHU": "盘点周转次数",
            "QTY_MIN": "安全库存",
            "MOREN_CANGKU_BH": "默认仓库编码",
            "MOREN_CANGKU_MC": "默认仓库名称",
            "ANQUANKUCUN_LX": "安全库存类型",
            "XIANYOU_KUCUN": "现有库存",
            "KUCU_QUELIANG": "库存缺量"
        },
        "searchable_fields": ["PRD_NO", "PRD_NAME", "PRD_SPC"],
        "request": {
            "headers": {
                "Accept": "application/json",
                "Authorization": "Basic REPLACE_WITH_BASE64_CREDENTIAL"
            },
            "payload_template": {
                "PRD_NO": "",
                "PRD_NAME": "",
                "PRD_SPC": "",
                "NEED_DAYS_MIN": "",
                "CK_CISHU_MIN": "",
                "KUCU_QUELIANG_MIN": "",
                "page": "$page",
                "page_size": "$page_size"
            },
            "pagination": {
                "page_size": 100,
                "max_pages": 100,
                "code_key": "code",
                "success_codes": [200],
                "data_key": "data",
                "total_key": "total",
                "has_next_key": "has_next"
            }
        },
        "response": {
            "code_key": "code",
            "data_key": "data",
            "total_key": "total",
            "success_codes": [200]
        },
        "quality_rules": {},
        "verify_tls": true
    }'::jsonb,
    1,
    (SELECT id FROM sys_platform WHERE name = 'ERP' LIMIT 1),
    NOW(),
    NOW()
)
ON CONFLICT (source_key) DO UPDATE SET
    source_name = EXCLUDED.source_name,
    table_name = EXCLUDED.table_name,
    http_method = EXCLUDED.http_method,
    api_url = EXCLUDED.api_url,
    extra_config = EXCLUDED.extra_config,
    platform_id = EXCLUDED.platform_id,
    updated_at = NOW();
