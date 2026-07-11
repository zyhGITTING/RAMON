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
    page            INTEGER DEFAULT NULL,
    page_size       INTEGER DEFAULT NULL,
    row_count       INTEGER DEFAULT NULL,
    total_count     INTEGER DEFAULT NULL,
    search_fields   TEXT DEFAULT '',
    accessed_fields TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON sys_audit_log(created_at DESC);

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
    config_json_http  TEXT NOT NULL DEFAULT ''
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
    user_seen       INTEGER NOT NULL DEFAULT 0
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
    ('erp_buy',      'ERP采购请购',   'ods_erp_buy',       'POST', 1),
    ('stock',        '库存预警',      'ods_stock',         'POST', 1),
    ('srm_purchase', 'SRM采购需求',   'ods_srm_purchase',  'POST', 1)
ON CONFLICT (source_key) DO NOTHING;
