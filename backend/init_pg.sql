-- =============================================================
-- 鏁版嵁涓彴 PostgreSQL 鍒濆鍖栬剼鏈?
-- 瀵瑰簲 server.py 鐨?bootstrap_database()
-- 棣栨 docker-compose up 鑷姩鎵ц
-- =============================================================

-- 绯荤粺鐢ㄦ埛琛?
CREATE TABLE IF NOT EXISTS sys_user (
    id              SERIAL PRIMARY KEY,
    employee_no     TEXT NOT NULL UNIQUE,
    username        TEXT NOT NULL UNIQUE,
    full_name       TEXT NOT NULL,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    department      TEXT NOT NULL DEFAULT '',
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sys_platform (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sort_order      INTEGER DEFAULT 0
);

-- 鏁版嵁婧愯〃锛坋xtra_config 鐢?JSONB锛孭G 鍘熺敓鏌ヨ锛?
CREATE TABLE IF NOT EXISTS sys_datasource (
    id              SERIAL PRIMARY KEY,
    source_key      TEXT NOT NULL UNIQUE,
    source_name     TEXT NOT NULL,
    table_name      TEXT NOT NULL UNIQUE,
    http_method     TEXT NOT NULL DEFAULT 'GET',
    api_url         TEXT,
    extra_config    JSONB,              -- 鈫?PG 浼樺娍锛欽SONB 鍙缓绱㈠紩銆佸彲鏌ュ唴閮ㄥ瓧娈?
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_sync_at    TIMESTAMPTZ,
    last_status     TEXT,
    last_message    TEXT,
    platform_id     INTEGER,
    sync_interval_minutes INTEGER DEFAULT NULL,
    sync_interval_seconds INTEGER DEFAULT NULL,
    last_quality_status TEXT DEFAULT '',
    last_quality_report JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 鍚屾鏃ュ織
CREATE TABLE IF NOT EXISTS sys_sync_log (
    id              SERIAL PRIMARY KEY,
    source_key      TEXT NOT NULL,
    source_name     TEXT NOT NULL,
    table_name      TEXT NOT NULL,
    sync_version    TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL,
    message         TEXT,
    row_count       INTEGER NOT NULL DEFAULT 0,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ NOT NULL,
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    triggered_by    TEXT NOT NULL DEFAULT '',
    quality_status  TEXT DEFAULT '',
    quality_report  JSONB
);
CREATE INDEX IF NOT EXISTS idx_sync_started_at ON sys_sync_log(started_at DESC);

-- 鍚屾蹇収鐗堟湰
CREATE TABLE IF NOT EXISTS sys_sync_version (
    id              SERIAL PRIMARY KEY,
    source_key      TEXT NOT NULL,
    source_name     TEXT NOT NULL,
    table_name      TEXT NOT NULL,
    sync_version    TEXT NOT NULL,
    sync_batch_id   TEXT NOT NULL,
    status          TEXT NOT NULL,
    message         TEXT,
    row_count       INTEGER NOT NULL DEFAULT 0,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ NOT NULL,
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    triggered_by    TEXT NOT NULL DEFAULT '',
    quality_status  TEXT DEFAULT '',
    quality_report  JSONB,
    is_current      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sync_version_source_time ON sys_sync_version(source_key, finished_at DESC);
CREATE INDEX IF NOT EXISTS idx_sync_version_current ON sys_sync_version(source_key, is_current);

-- 瀛楁绾у厓鏁版嵁
CREATE TABLE IF NOT EXISTS sys_field_meta (
    id                SERIAL PRIMARY KEY,
    source_key        TEXT NOT NULL,
    table_name        TEXT NOT NULL,
    field_name        TEXT NOT NULL,
    field_label       TEXT NOT NULL DEFAULT '',
    source_path       TEXT NOT NULL DEFAULT '',
    standard_field_code TEXT NOT NULL DEFAULT '',
    standard_field_name TEXT NOT NULL DEFAULT '',
    business_domain   TEXT NOT NULL DEFAULT '',
    entity_code       TEXT NOT NULL DEFAULT '',
    entity_role       TEXT NOT NULL DEFAULT '',
    data_type         TEXT NOT NULL DEFAULT 'text',
    description       TEXT NOT NULL DEFAULT '',
    example_value     TEXT NOT NULL DEFAULT '',
    is_searchable     INTEGER NOT NULL DEFAULT 0,
    is_filterable     INTEGER NOT NULL DEFAULT 0,
    is_displayed      INTEGER NOT NULL DEFAULT 1,
    sensitivity_level TEXT NOT NULL DEFAULT 'normal',
    mask_rule         TEXT NOT NULL DEFAULT '',
    permission_scope  TEXT NOT NULL DEFAULT '',
    is_restricted     INTEGER NOT NULL DEFAULT 0,
    restricted_access TEXT NOT NULL DEFAULT 'hide',
    metric_unit       TEXT NOT NULL DEFAULT '',
    value_dictionary  TEXT NOT NULL DEFAULT '',
    definition        TEXT NOT NULL DEFAULT '',
    is_active         INTEGER NOT NULL DEFAULT 1,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by        TEXT NOT NULL DEFAULT 'system',
    UNIQUE(source_key, field_name)
);
CREATE INDEX IF NOT EXISTS idx_field_meta_source ON sys_field_meta(source_key, is_active);

-- 审计日志
CREATE TABLE IF NOT EXISTS sys_audit_log (
    id              SERIAL PRIMARY KEY,
    username        TEXT NOT NULL,
    role            TEXT NOT NULL,
    action          TEXT NOT NULL,
    target          TEXT NOT NULL,
    detail          TEXT,
    ip              TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id         INTEGER DEFAULT NULL,
    employee_no     TEXT DEFAULT '',
    department      TEXT DEFAULT '',
    token_id        INTEGER DEFAULT NULL,
    jti             TEXT DEFAULT '',
    source_name     TEXT DEFAULT '',
    keyword         TEXT DEFAULT '',
    as_of           TEXT DEFAULT '',
    start_time      TEXT DEFAULT '',
    end_time        TEXT DEFAULT '',
    business_time_field TEXT DEFAULT '',
    page            INTEGER DEFAULT NULL,
    page_size       INTEGER DEFAULT NULL,
    row_count       INTEGER DEFAULT NULL,
    total_count     INTEGER DEFAULT NULL,
    search_fields   TEXT DEFAULT '',
    accessed_fields TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON sys_audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_jti_created ON sys_audit_log(jti, created_at DESC);

CREATE TABLE IF NOT EXISTS sys_config (
    id              SERIAL PRIMARY KEY,
    key             TEXT NOT NULL UNIQUE,
    value           TEXT NOT NULL DEFAULT '',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 鐢ㄦ埛鏉冮檺琛?
CREATE TABLE IF NOT EXISTS sys_user_permission (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL,
    source_key      TEXT NOT NULL,
    granted_by      TEXT NOT NULL,
    granted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, source_key)
);

-- 閮ㄩ棬-鏁版嵁婧愭潈闄愭槧灏勶紙鐐庨粍瀵规帴棰勭暀锛?
CREATE TABLE IF NOT EXISTS sys_department_permission (
    id              SERIAL PRIMARY KEY,
    department      TEXT NOT NULL,
    source_key      TEXT NOT NULL,
    granted_by      TEXT,
    granted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(department, source_key)
);

-- MCP 令牌 / 导出申请 / 字段级权限（今天新迁移的原生 Postgres 代码需要用到这几张表）
CREATE TABLE IF NOT EXISTS sys_user_field_permission (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL,
    source_key      TEXT NOT NULL,
    field_name      TEXT NOT NULL,
    granted_by      TEXT NOT NULL DEFAULT '',
    granted_at      TEXT NOT NULL DEFAULT '',
    UNIQUE(user_id, source_key, field_name)
);

CREATE TABLE IF NOT EXISTS sys_department_field_permission (
    id              SERIAL PRIMARY KEY,
    department      TEXT NOT NULL,
    source_key      TEXT NOT NULL,
    field_name      TEXT NOT NULL,
    granted_by      TEXT NOT NULL DEFAULT '',
    granted_at      TEXT NOT NULL DEFAULT '',
    UNIQUE(department, source_key, field_name)
);

CREATE TABLE IF NOT EXISTS sys_mcp_token (
    id                SERIAL PRIMARY KEY,
    jti               TEXT NOT NULL UNIQUE,
    user_id           INTEGER NOT NULL,
    username          TEXT NOT NULL,
    employee_no       TEXT NOT NULL DEFAULT '',
    department        TEXT NOT NULL DEFAULT '',
    source_keys_json  TEXT NOT NULL DEFAULT '[]',
    bind_ip           INTEGER NOT NULL DEFAULT 0,
    ip                TEXT NOT NULL DEFAULT '',
    token_hash        TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'active',
    created_at        TEXT NOT NULL,
    expires_at        TEXT NOT NULL,
    last_used_at      TEXT NOT NULL DEFAULT '',
    last_used_ip      TEXT NOT NULL DEFAULT '',
    revoked_at        TEXT NOT NULL DEFAULT '',
    revoked_by        TEXT NOT NULL DEFAULT '',
    revoked_reason    TEXT NOT NULL DEFAULT '',
    user_deleted      INTEGER NOT NULL DEFAULT 0,
    deleted_at        TEXT NOT NULL DEFAULT '',
    deleted_by        TEXT NOT NULL DEFAULT '',
    deleted_reason    TEXT NOT NULL DEFAULT '',
    config_json       TEXT NOT NULL DEFAULT '',
    config_json_http  TEXT NOT NULL DEFAULT '',
    validity_period   TEXT NOT NULL DEFAULT '3m'
);
CREATE INDEX IF NOT EXISTS idx_sys_mcp_token_status ON sys_mcp_token(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sys_mcp_token_user ON sys_mcp_token(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS sys_mcp_export_request (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL,
    username        TEXT NOT NULL DEFAULT '',
    employee_no     TEXT NOT NULL DEFAULT '',
    department      TEXT NOT NULL DEFAULT '',
    source_key      TEXT NOT NULL,
    source_name     TEXT NOT NULL DEFAULT '',
    reason          TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT NOT NULL,
    handled_at      TEXT NOT NULL DEFAULT '',
    handled_by      TEXT NOT NULL DEFAULT '',
    admin_comment   TEXT NOT NULL DEFAULT '',
    user_seen       INTEGER NOT NULL DEFAULT 0,
    validity_period TEXT NOT NULL DEFAULT '3m'
);
CREATE INDEX IF NOT EXISTS idx_sys_mcp_export_request_status ON sys_mcp_export_request(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sys_mcp_export_request_user ON sys_mcp_export_request(user_id, source_key, created_at DESC);


-- ==================== 榛樿鏁版嵁 ====================

-- 鏈湴璐﹀彿涓嶅啀鑷姩娉ㄥ叆寮遍粯璁ゅ彛浠ゃ€?
-- 濡傞渶棣栦釜绠＄悊鍛橈紝璇峰湪搴旂敤鍚姩鍓嶈缃互涓嬬幆澧冨彉閲忥紝鐢卞簲鐢ㄤ晶寮曞鍒涘缓锛?
-- DATAMID_INIT_ADMIN_USERNAME
-- DATAMID_INIT_ADMIN_PASSWORD
-- 鍙€夛細DATAMID_INIT_ADMIN_EMPLOYEE_NO / DATAMID_INIT_ADMIN_FULL_NAME / DATAMID_INIT_ADMIN_DEPARTMENT


-- 涓変釜榛樿鏁版嵁婧?
INSERT INTO sys_datasource (source_key, source_name, table_name, http_method, enabled)
VALUES
    ('erp_buy',        'ERP采购请购',      'ods_erp_buy',         'POST', 1),
    ('stock',          '库存预警',         'ods_stock',           'POST', 1),
    ('srm_purchase',   'SRM采购需求',      'ods_srm_purchase',    'POST', 1),
    ('erp_subcontract','ERP托工明细表',    'ods_erp_subcontract', 'POST', 1)
ON CONFLICT (source_key) DO NOTHING;

INSERT INTO sys_datasource (source_key, source_name, table_name, http_method, enabled)
VALUES ('erp_safe_stock', 'ERP safe stock', 'ods_erp_safe_stock', 'POST', 1)
ON CONFLICT (source_key) DO NOTHING;

-- ==================== MCP 人均采购额改造：补充 5 个数据源的查询配置 ====================
-- 这些源已存在但 extra_config 中 business_time_field 为空，需要补齐日期、排序、字段标签等配置

UPDATE sys_datasource
SET extra_config = COALESCE(extra_config, '{}'::jsonb) || '{
    "business_time_field": "",
    "chart_field": "fbm",
    "field_labels": {
        "yuangong_bh": "工号",
        "fbm": "部门",
        "fxm": "姓名",
        "fzt": "员工状态",
        "frzrq": "入职日期",
        "fzxrq": "离职日期"
    },
    "searchable_fields": ["yuangong_bh", "fxm", "fbm"]
}'::jsonb,
    updated_at = NOW()
WHERE source_key = 'new_employee_info';

UPDATE sys_datasource
SET extra_config = COALESCE(extra_config, '{}'::jsonb) || '{
    "business_time_field": "os_dd",
    "date_format": "timestamp_ms",
    "stable_sort_fields": ["os_dd", "os_no", "itm"],
    "chart_field": "cus_name",
    "field_labels": {
        "os_dd": "采购日期",
        "sal_name": "采购员",
        "qty": "数量",
        "up": "单价",
        "amt": "金额",
        "amtn": "未税金额",
        "os_no": "采购单号",
        "itm": "行号",
        "prd_no": "物料编码",
        "prd_name": "物料名称",
        "cus_name": "供应商"
    },
    "searchable_fields": ["os_no", "prd_no", "prd_name", "cus_name", "sal_name"]
}'::jsonb,
    updated_at = NOW()
WHERE source_key = 'erp_purchase_order_detail';

UPDATE sys_datasource
SET extra_config = COALESCE(extra_config, '{}'::jsonb) || '{
    "business_time_field": "os_dd",
    "date_format": "timestamp_ms",
    "stable_sort_fields": ["os_dd", "os_no", "itm"],
    "chart_field": "sal_no_pona",
    "field_labels": {
        "os_dd": "采购日期",
        "fx_name": "固定资产名称",
        "qty": "数量",
        "up": "单价",
        "amt": "金额",
        "amtn_net": "未税金额",
        "os_no": "采购单号",
        "itm": "行号",
        "sal_no_pona": "业务员",
        "sal_no_usena": "使用人",
        "cus_name": "供应商"
    },
    "searchable_fields": ["os_no", "fx_name", "cus_name", "sal_no_pona"]
}'::jsonb,
    updated_at = NOW()
WHERE source_key = 'erp_asset_purchase_detail';

UPDATE sys_datasource
SET extra_config = COALESCE(extra_config, '{}'::jsonb) || '{
    "business_time_field": "tw_dd",
    "date_format": "date_string",
    "stable_sort_fields": ["tw_dd", "tw_no", "itm"],
    "chart_field": "cus_name",
    "field_labels": {
        "tw_dd": "托工日期",
        "usr": "制单人",
        "qty_mrp": "制造数量",
        "up": "外协单价",
        "amtn": "加工金额",
        "tw_no": "外协单号",
        "itm": "行号",
        "cus_name": "托工供应商",
        "mrp_name": "托外货名称"
    },
    "searchable_fields": ["tw_no", "cus_name", "mrp_name", "usr"]
}'::jsonb,
    updated_at = NOW()
WHERE source_key = 'erp_subcontract_detail';

UPDATE sys_datasource
SET extra_config = COALESCE(extra_config, '{}'::jsonb) || '{
    "business_time_field": "ep_dd",
    "date_format": "timestamp_ms",
    "stable_sort_fields": ["ep_dd", "ep_no", "itm"],
    "chart_field": "usr_name",
    "field_labels": {
        "ep_dd": "费用日期",
        "ep_no": "费用单号",
        "itm": "行号",
        "usr_name": "报销人",
        "amt": "金额",
        "amtn": "未税金额",
        "amtn_net": "净额",
        "cus_name": "往来单位"
    },
    "searchable_fields": ["ep_no", "cus_name", "usr_name"]
}'::jsonb,
    updated_at = NOW()
WHERE source_key = 'erp_other_expense_detail';

-- 人均采购额相关表索引：日期/单号/行号稳定排序，人员表部门+工号
CREATE INDEX IF NOT EXISTS idx_ods_erp_purchase_order_detail_date_doc ON ods_erp_purchase_order_detail ((os_dd::bigint), os_no, itm) WHERE is_current = 1;
CREATE INDEX IF NOT EXISTS idx_ods_erp_asset_purchase_detail_date_doc ON ods_erp_asset_purchase_detail ((os_dd::bigint), os_no, itm) WHERE is_current = 1;
CREATE INDEX IF NOT EXISTS idx_ods_erp_subcontract_detail_date_doc ON ods_erp_subcontract_detail (tw_dd, tw_no, itm) WHERE is_current = 1;
CREATE INDEX IF NOT EXISTS idx_ods_other_expense_detail_date_doc ON ods_other_expense_detail ((ep_dd::bigint), ep_no, itm) WHERE is_current = 1;
CREATE INDEX IF NOT EXISTS idx_ods_employee_info_dept_no ON ods_employee_info (fbm, yuangong_bh);

-- ==================== 同步断点续传与增量同步扩展 ====================

-- 每个数据源的同步断点/续传状态，失败或进程重启后从 last_fetched_page+1 继续
CREATE TABLE IF NOT EXISTS sys_sync_checkpoint (
    source_key             TEXT PRIMARY KEY,
    sync_batch_id          TEXT NOT NULL DEFAULT '',
    sync_version           TEXT NOT NULL DEFAULT '',
    strategy               TEXT NOT NULL DEFAULT 'full',
    status                 TEXT NOT NULL DEFAULT 'completed',
    watermark_value        TEXT NOT NULL DEFAULT '',
    cursor_value           TEXT NOT NULL DEFAULT '',
    last_fetched_page      INTEGER NOT NULL DEFAULT 0,
    last_fetched_row_count INTEGER NOT NULL DEFAULT 0,
    failed_attempts        INTEGER NOT NULL DEFAULT 0,
    last_error             TEXT NOT NULL DEFAULT '',
    start_date             TEXT NOT NULL DEFAULT '',
    end_date               TEXT NOT NULL DEFAULT '',
    started_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sync_checkpoint_status ON sys_sync_checkpoint(source_key, status);

-- 记录每次同步使用的策略/水位，便于后续切换增量策略时追溯
ALTER TABLE sys_sync_version
    ADD COLUMN IF NOT EXISTS strategy TEXT NOT NULL DEFAULT 'full',
    ADD COLUMN IF NOT EXISTS watermark_value TEXT NOT NULL DEFAULT '';
