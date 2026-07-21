# Datamid 文件上传自动建表方案（可直接交给开发 Agent）

## 1. 结论

在现有 FastAPI + PostgreSQL 服务内直接实现一个**管理员同步导入接口**，不引入 Celery、Redis、Pandas、消息队列或新的微服务。

推荐链路：

```text
浏览器上传 -> 文件流写入临时文件 -> 逐行解析并转成标准 CSV 临时流
          -> PostgreSQL 单事务建表 + COPY 批量写入 + 注册数据源
          -> 提交事务后返回成功
```

这是当前项目中代码最少、速度最快、结果最可靠的做法：

- `COPY FROM STDIN` 比逐条 `INSERT` 快很多，也比当前 `execute_values` 更适合整文件导入。
- 解析在事务外完成，避免长时间占用数据库事务。
- 建业务表、写数据、登记 `sys_datasource`、写字段元数据和审计日志放在同一个事务中；任一步失败全部回滚，不留下半张表或半批数据。
- 接口只有在数据库提交完成后才返回成功，前端不会出现“显示成功但后台其实失败”。

## 2. 第一版范围（必须按此实现，不自行扩大）

### 支持格式

| 格式 | 第一版规则 |
|---|---|
| `.csv` | 第一行作为表头；自动识别逗号、分号、Tab；编码依次尝试 UTF-8 BOM、UTF-8、GB18030 |
| `.xlsx` | 导入全部可见且非空工作表；每个 Sheet 单独创建一个数据源和一张表；每个 Sheet 的第一行非空行作为表头；使用 `openpyxl` 只读模式，公式只读取缓存值，不执行公式 |
| `.md` | 只支持 GitHub 风格 Markdown 表格；导入文档中的第一张合法表格；普通文章、代码块和无表格 Markdown 明确报错 |

`.md` 不是天然的结构化数据。第一版必须限定为“Markdown 表格”，不要尝试用大模型把任意文章自动转换成数据表，否则结果不可预测、响应慢且会引入外部服务依赖。

### 限制

- 单文件最大 20 MB，可通过环境变量 `DATAMID_UPLOAD_MAX_BYTES` 调整。
- 最大 100,000 行、最大 200 列；超限返回明确错误。
- 只允许管理员上传，复用 `require_admin`。
- 一次请求只上传一个文件。CSV/Markdown 创建一个数据源和一张表；XLSX 每个可见非空 Sheet 分别创建一个数据源和一张表。
- XLSX 最多导入 20 个有效 Sheet；单个 Sheet 最大 100,000 行、200 列，整个工作簿累计最大 300,000 行。
- 第一版不支持追加、覆盖、跨 Sheet 合并、多文件 ZIP；所有新表名均由后端生成。
- 空行跳过；短行补空字符串；长于表头的行直接报错并指出行号。
- 所有业务字段在数据库中使用 `TEXT`。不要自动推断数字、日期、布尔类型；这能最大限度避免脏数据导致整批失败。展示层仍可使用现有字段元数据的类型推断。

## 3. 接口定义

新增路由文件：`backend/app/api/routers/admin_file_import.py`。

### `POST /api/admin/file-import`

请求类型：`multipart/form-data`

字段：

- `file`：必填，仅允许 `.csv`、`.xlsx`、`.md`。
- `source_name`：可选，默认使用去扩展名后的文件名。
- `platform_id`：可选，沿用现有平台归属。

`source_key` 和 `table_name` 必须由后端生成，不接受客户端直接传 SQL 标识符。CSV/Markdown 使用：

- `source_key = file_<UTC时间戳>_<8位随机十六进制>`
- `table_name = ods_file_<UTC时间戳>_<8位随机十六进制>`

XLSX 在同一批次基础名称后按工作簿顺序增加 Sheet 编号：

- `source_key = file_<UTC时间戳>_<8位随机十六进制>_s01`
- `table_name = ods_file_<UTC时间戳>_<8位随机十六进制>_s01`
- 后续 Sheet 使用 `_s02`、`_s03`，不要把 Sheet 原名拼进 SQL 标识符。

成功响应（HTTP 201）：

```json
{
  "message": "Excel 导入成功，共创建 2 个数据源",
  "datasource_count": 2,
  "total_row_count": 91600,
  "duration_ms": 4260,
  "items": [
    {
      "datasource_id": 123,
      "source_key": "file_20260721_123000_a1b2c3d4_s01",
      "source_name": "7月销售明细 - 华东区",
      "table_name": "ods_file_20260721_123000_a1b2c3d4_s01",
      "sheet_name": "华东区",
      "row_count": 48000,
      "column_count": 18
    },
    {
      "datasource_id": 124,
      "source_key": "file_20260721_123000_a1b2c3d4_s02",
      "source_name": "7月销售明细 - 华南区",
      "table_name": "ods_file_20260721_123000_a1b2c3d4_s02",
      "sheet_name": "华南区",
      "row_count": 43600,
      "column_count": 18
    }
  ]
}
```

CSV 和 Markdown 也使用相同响应结构，只是 `datasource_count=1`、`items` 只有一项、`sheet_name` 为空字符串。这样前端只需要处理一种响应格式。

错误响应统一使用 FastAPI `HTTPException`：

- 400：文件内容、表头、Markdown 表格或行列结构不合法。
- 401/403：未登录或非管理员。
- 409：重复文件或命名冲突（若实现文件哈希去重）。
- 413：文件过大、行数或列数超限。
- 500：数据库异常；返回通用中文信息，详细异常只写服务日志。

不要把数据库 SQL、服务器路径或原始堆栈返回给前端。

## 4. 数据表结构与现有系统兼容

业务表继续遵循现有 ODS 结构，避免破坏目录查询、MCP 查询、字段管理和删除数据源功能：

```sql
CREATE TABLE "动态生成的表名" (
    id BIGSERIAL PRIMARY KEY,
    sync_batch_id TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    sync_version TEXT,
    is_current INTEGER NOT NULL DEFAULT 1,
    -- 后面全部是本次文件的业务字段，类型统一 TEXT
    "业务字段1" TEXT,
    "业务字段2" TEXT
);
```

建表后调用现有 `ensure_ods_indexes`，并登记：

1. `sys_datasource`
   - `http_method='GET'`
   - `api_url=''`
   - `enabled=1`
   - `last_status='success'`
   - `last_sync_at=NOW()`
   - `last_message='文件上传导入成功，共 N 行'`
   - 每个 Sheet 对应的 `extra_config` 分别写入 `source_type=file_upload`、原始文件名、扩展名、Sheet 名、Sheet 顺序、同一文件 SHA-256、本 Sheet 行列数和原始表头映射。
2. `sys_field_meta`：调用现有 `sync_datasource_field_meta` 后，用原始表头覆盖 `field_label`。
3. `sys_sync_log`：写一条 `status='success'`、`triggered_by='file_upload:<username>'` 的记录。
4. `sys_sync_version`：写第一版版本记录并设置 `is_current=1`，以兼容现有版本展示。
5. `sys_audit_log`：记录 `import_file_datasource`。注意现有 `record_audit_log` 会自行新建连接并提交，不能在本次事务里调用。第一版直接使用当前 `conn.execute(...)` 插入审计表的必填字段，确保审计记录和导入结果一起提交或一起回滚。

### 字段名规则

原始表头保留在 `sys_field_meta.field_label` 和 `extra_config.header_map` 中。物理列名必须确定性生成：

- 英文字母/数字/下划线组成的表头：转小写 snake_case，开头是数字则加 `c_`。
- 中文、符号、空表头或规范化后为空：使用 `col_001`、`col_002`。
- 重名依次加 `_2`、`_3`。
- 截断到 PostgreSQL 允许的 63 字节以内；截断后仍需再次去重。
- 所有动态表名、字段名一律经现有 `quote_identifier` 或 psycopg2 SQL Identifier 安全引用，禁止字符串裸拼接用户输入。

示例：

| 原始表头 | 物理字段名 |
|---|---|
| `Order No` | `order_no` |
| `2026金额` | `col_002` |
| `客户名称` | `col_003` |
| 第二个 `Order No` | `order_no_2` |

## 5. 最稳定的事务边界

必须按以下顺序：

1. 边接收边写入 `tempfile.SpooledTemporaryFile`，同时计算 SHA-256 和累计字节数；超限立刻停止。
2. 在数据库事务外完整解析文件。CSV/Markdown 生成一个标准 CSV 临时流；XLSX 为每个可见非空 Sheet 各生成一个标准 CSV 临时流，并得到每项的表头、行数、Sheet 名和少量示例值。
3. 完整解析成功后才打开数据库连接并开始事务。
4. 检查 `platform_id` 是否存在，检查生成的 `source_key/table_name` 无冲突。
5. 按解析结果顺序循环：为每个结果建业务表并创建索引。
6. 对每个结果使用原生 psycopg2 游标 `copy_expert` 将对应标准 CSV 临时流一次导入；COPY 列包含同步元字段及所有业务列，但不包含自增 `id`。
7. 分别验证每张表的 `SELECT COUNT(*)` 等于对应 Sheet 的解析行数。
8. 为每张表分别写入 `sys_datasource`、`sys_field_meta`、`sys_sync_log`、`sys_sync_version` 和审计记录；所有循环共用当前连接和同一个事务。
9. `commit`。
10. 任意一个 Sheet 的任一步异常都执行一次 `rollback`；PostgreSQL 的事务性 DDL 会同时撤销本工作簿已经创建的所有表和元数据。
11. 在 `finally` 中关闭数据库连接、上传临时文件以及每个解析结果的 COPY 临时流。

不要先提交业务表再登记数据源，也不要捕获异常后继续提交。

## 6. 第一版代码放置（只新增一个后端业务文件）

为了让能力较弱的编码 Agent 不漏接模块，第一版不要拆 service 和 parser。所有上传、解析、建表和 COPY 逻辑集中放在一个新文件：

```text
backend/app/api/routers/admin_file_import.py   # HTTP、权限、响应和错误映射
```

测试文件单独新增：

```text
backend/tests/test_file_import.py
```

修改文件：

```text
backend/app/main.py             # 注册新 router
backend/requirements.txt        # 增加 python-multipart、openpyxl
frontend/app.js                 # 数据源管理页增加上传区和进度/结果提示
.env.example                    # 增加上传大小、最大行列配置
```

第一版不做哈希去重表，所以不要修改 `backend/init_pg.sql`。SHA-256 只记录在 `sys_datasource.extra_config` 中。

建议依赖仅增加：

```text
python-multipart>=0.0.32
openpyxl>=3.1.5
```

CSV 使用 Python 标准库 `csv`，Markdown 表格写一个小型严格解析器即可。不要引入 Pandas，它会增加镜像体积、内存占用和启动/解析成本。

## 7. 前端最简方案

在“数据源管理”页“新增数据源”区域上方增加一个卡片：

- 文件选择框：`accept=".xlsx,.csv,.md"`
- 可选数据源名称。
- 可选所属平台。
- “上传并导入”按钮。
- 上传过程中按钮禁用，显示“正在上传并写入，请勿重复提交”。
- 成功后显示表名、行数、列数、耗时，并刷新数据源列表和导航。
- 失败展示后端的可读错误。

第一版使用普通 `fetch + FormData`。不做分片上传、不做断点续传、不做 WebSocket；20 MB 限制下没有必要。

注意：现有 `apiFetch` 默认可能按 JSON 请求封装。上传时不要手工设置 `Content-Type`，让浏览器自动生成 multipart boundary；如 `apiFetch` 会强制 JSON，需要为 FormData 增加分支。

## 8. 性能与稳定性要求

- XLSX 必须 `load_workbook(..., read_only=True, data_only=True)`，禁止普通模式加载整个工作簿。
- 全流程不要构造 `list[dict]` 保存全部数据；使用迭代器和临时流。
- CSV 写入临时流时使用 Python `csv.writer`，不要自己拼逗号和引号。
- PostgreSQL 必须使用 `COPY`，禁止逐行 `INSERT`。
- 不自动为所有业务列建索引；只保留现有 `is_current/id` 与 `sync_version/id` 索引。用户配置业务时间字段后再使用现有逻辑建时间索引。
- 文件名只作展示，不作为服务器路径或 SQL 名称。
- 限制 XLSX 解压后可处理的单元格总数（最大行数 × 最大列数），防止压缩炸弹和内存攻击。
- 单元格统一转字符串；`None` 写为空，日期转 ISO 文本，布尔转 `true/false`，禁止执行公式或宏。
- 日志记录耗时、文件大小、行列数、操作者、source_key；不记录完整数据行。

建议验收目标（同机 PostgreSQL、普通办公文件）：

- 10 MB CSV / 100,000 行：导入完成时间目标小于 5 秒。
- 10 MB XLSX：目标小于 15 秒，实际受压缩与单元格数量影响。
- 任意解析错误或数据库错误后：不存在新业务表、不存在新数据源登记、不存在部分数据。

## 9. 必测用例

1. UTF-8、UTF-8 BOM、GB18030 CSV。
2. CSV 中含逗号、双引号、换行、多字节中文。
3. XLSX 含多个 Sheet 时导入全部可见非空 Sheet；隐藏 Sheet 和空 Sheet 跳过。
4. XLSX 含公式、日期、空单元格、合并单元格。
5. Markdown 合法表格、转义竖线、无表格文档。
6. 空表头、中文表头、超长表头、重复表头、SQL 保留字表头。
7. 空文件、只有表头、列数不一致、超 20 MB、超 100,000 行、超 200 列。
8. 数据库 COPY 中途异常，确认表和所有元数据均回滚。
9. 非管理员请求被拒绝。
10. 导入成功后能在数据源列表出现，能浏览数据，MCP 查询可读，删除数据源能同时删除业务表。

## 10. 开发 Agent 执行指令

可将下面内容原样交给 DeepSeek、Claude、Codex 或其他开发 Agent：

> 请阅读项目中的 `FILE_UPLOAD_IMPORT_PLAN.md`，严格按第一版范围实现文件上传自动建表功能。先检查现有 `backend/app/services/datasource_service.py`、权限依赖、数据库适配器和前端 `apiFetch`，最大限度复用现有函数。实现 `.csv`、`.xlsx`、`.md`（Markdown 仅第一张表格）；CSV/Markdown 各建一个数据源，XLSX 的每个可见非空 Sheet 分别建一个数据源和一张表，整个工作簿必须在同一数据库事务内全成或全退。使用流式解析、全字段 TEXT 和 PostgreSQL COPY。不要引入 Pandas、Celery、Redis、LLM 解析或异步任务。补齐单元测试与接口测试，运行现有测试集，报告改动文件、测试结果和仍存在的限制。不要修改无关功能，不要覆盖用户已有改动。

## 11. 后续版本（第一版不要做）

只有实际出现 20 MB 以上文件或导入超过反向代理超时时间时，再升级为“持久化任务表 + 独立 worker + 状态轮询”的异步导入。不要使用 FastAPI `BackgroundTasks` 承担关键导入，因为服务重启时任务会丢失。第一版同步事务模式更简单，也更容易保证“成功响应就是已经写入成功”。

---

## 12. DeepSeek 必须严格执行的施工顺序

不要同时改所有文件。必须按下面顺序逐步完成，每一步先保证代码能导入，再继续下一步：

1. 修改 `backend/requirements.txt`，增加两个依赖。
2. 新建 `backend/app/api/routers/admin_file_import.py`，先完成解析辅助函数和单元测试。
3. 在同一个新文件中完成数据库事务和 `COPY`。
4. 修改 `backend/app/main.py` 注册路由，确认应用可以启动。
5. 修改 `frontend/app.js` 的 `apiFetch`，使其支持 `FormData`。
6. 在 `renderAdminDatasources()` 中加入上传卡片及事件监听。
7. 修改 `.env.example` 写入限制配置示例。
8. 新增 `backend/tests/test_file_import.py` 并运行全部测试。
9. 使用真实 CSV、XLSX、Markdown 各手工导入一次，检查页面、数据库和删除功能。

如果任何一步失败，先修复当前步骤，不要跳过，不要用“临时注释掉”绕过。

## 13. 后端新文件的固定结构

`backend/app/api/routers/admin_file_import.py` 必须按这个顺序组织，不要自由重构：

```python
from __future__ import annotations

# 1. Python 标准库 import
# 2. openpyxl / FastAPI import
# 3. 本项目 import
# 4. 常量
# 5. ParsedImport 数据类
# 6. 通用辅助函数
# 7. CSV 解析函数
# 8. XLSX 解析函数
# 9. Markdown 解析函数
# 10. PostgreSQL 建表与 COPY 函数
# 11. POST /api/admin/file-import 路由
```

必须使用的项目 import：

```python
from backend.app.api.deps import require_admin
from backend.app.db.connection import get_connection
from backend.app.db.repositories.config import now_text
from backend.app.services.datasource_service import (
    build_datasource_extra_config,
    ensure_ods_indexes,
    sync_datasource_field_meta,
)
from backend.app.services.sync_service import quote_identifier
```

建议的数据结构和函数签名如下。函数名可以直接采用，不要合并成一个超长函数：

```python
@dataclass
class ParsedImport:
    physical_columns: list[str]
    original_headers: list[str]
    copy_stream: TextIO
    row_count: int
    sheet_name: str
    sheet_index: int


def _normalize_headers(headers: list[object]) -> tuple[list[str], list[str]]: ...
def _cell_to_text(value: object) -> str: ...
def _decode_text_file(raw: bytes) -> str: ...
def _parse_csv(raw: bytes, batch_id: str, version: str, synced_at: str) -> ParsedImport: ...
def _parse_xlsx(stream: BinaryIO, batch_id: str, version: str, synced_at: str) -> list[ParsedImport]: ...
def _split_markdown_row(line: str) -> list[str]: ...
def _parse_markdown(raw: bytes, batch_id: str, version: str, synced_at: str) -> ParsedImport: ...
def _parse_to_copy_stream(stream: BinaryIO, extension: str, batch_id: str, version: str, synced_at: str) -> list[ParsedImport]: ...
def _create_and_copy(conn, table_name: str, parsed: ParsedImport) -> None: ...
def _insert_import_metadata(conn, ...) -> int: ...
```

配置常量全部写在新文件顶部，从环境变量读取，并提供安全默认值：

```python
UPLOAD_MAX_BYTES = int(os.getenv("DATAMID_UPLOAD_MAX_BYTES", str(20 * 1024 * 1024)))
UPLOAD_MAX_ROWS = int(os.getenv("DATAMID_UPLOAD_MAX_ROWS", "100000"))
UPLOAD_MAX_COLUMNS = int(os.getenv("DATAMID_UPLOAD_MAX_COLUMNS", "200"))
UPLOAD_MAX_SHEETS = int(os.getenv("DATAMID_UPLOAD_MAX_SHEETS", "20"))
UPLOAD_MAX_WORKBOOK_ROWS = int(os.getenv("DATAMID_UPLOAD_MAX_WORKBOOK_ROWS", "300000"))
UPLOAD_CHUNK_BYTES = 1024 * 1024
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".md"}
SYSTEM_COLUMNS = {"id", "sync_batch_id", "synced_at", "sync_version", "is_current"}
```

不得读取客户端传来的 Content-Type 来决定解析器，因为它可以伪造。必须使用文件扩展名选择解析器，再由解析器验证内容是否合法。

## 14. 文件接收逻辑（必须这样做）

路由必须是 `async def`，因为需要分块读取 `UploadFile`：

```python
router = APIRouter()


@router.post("/api/admin/file-import", status_code=201)
async def admin_file_import(
    file: UploadFile = File(...),
    source_name: str = Form(""),
    platform_id: int | None = Form(None),
    admin=Depends(require_admin),
):
    ...
```

接收步骤必须为：

1. `filename = Path(file.filename or "").name`，只取 basename，禁止使用客户端路径。
2. `extension = Path(filename).suffix.lower()`，不在允许集合中立即返回 400。
3. 创建 `tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")`。
4. 循环执行 `chunk = await file.read(UPLOAD_CHUNK_BYTES)`。
5. 每读一块同时更新 `total_bytes` 和 `hashlib.sha256()`。
6. 一旦 `total_bytes > UPLOAD_MAX_BYTES`，立即关闭临时文件并返回 413。
7. 文件为 0 字节返回 400。
8. 写完后 `seek(0)`，不要再读取原始 `UploadFile`。
9. 在最外层 `finally` 中执行 `await file.close()` 并关闭所有自建临时流。

`source_name` 处理规则：去掉首尾空格；为空时使用文件名去掉扩展名；最终仍为空则使用“文件导入数据源”；最多保留 200 个字符。

生成标识符：

```python
unique_suffix = uuid.uuid4().hex[:8]
time_part = datetime.now().strftime("%Y%m%d_%H%M%S")
base_source_key = f"file_{time_part}_{unique_suffix}"
base_table_name = f"ods_file_{time_part}_{unique_suffix}"
batch_id = uuid.uuid4().hex
sync_version = datetime.now().strftime("%Y%m%d%H%M%S") + "_" + unique_suffix
synced_at = now_text()
```

解析完成后再为每项生成最终名称：CSV/Markdown 直接使用基础名称；XLSX 使用 `f"{base_source_key}_s{index:02d}"` 和 `f"{base_table_name}_s{index:02d}"`。`index` 按有效 Sheet 的工作簿顺序从 1 开始，不因跳过隐藏或空 Sheet 留空号。

不要允许前端传 `source_key` 或 `table_name`。

## 15. 三种解析器的精确行为

### 15.1 公共表头规范化

第一行有效行是表头。表头单元格先调用 `_cell_to_text`，但不得自动修改展示名称。物理字段名按以下伪代码生成：

```python
original = unicodedata.normalize("NFKC", label).strip()
candidate = original.lower()
candidate = re.sub(r"[^a-z0-9_]+", "_", candidate)
candidate = re.sub(r"_+", "_", candidate).strip("_")

if not candidate or not re.match(r"^[a-z_]", candidate):
    candidate = f"col_{position:03d}"
if candidate in SYSTEM_COLUMNS:
    candidate = f"src_{candidate}"
candidate = candidate[:55]
# 已存在则追加 _2、_3……，最终长度不得超过 63 个 ASCII 字符
```

这里故意只让 ASCII 英文表头变成物理字段名。中文表头用 `col_001` 等安全名称，原中文完整保存在 `field_label`，这样不需要拼音依赖，也不会出现 PostgreSQL 多字节标识符截断问题。

表头要求：

- 不允许所有表头都为空。
- 最大 200 列。
- 即使两个原始表头相同也允许，但物理字段必须去重。
- 表头映射必须保持原始列顺序，不允许排序。

### 15.2 单元格转文本

`_cell_to_text` 必须遵守：

- `None -> ""`
- `bool -> "true" / "false"`
- `datetime -> ISO 格式，日期和时间之间用空格`
- `date`、`time -> isoformat()`
- 其他值 `str(value)`
- 不对普通字符串调用 `.strip()`，避免偷偷改变用户数据。
- 单个单元格文本超过 1,000,000 字符时返回 400。

### 15.3 标准 COPY 临时流

三个解析器最后都必须生成相同格式的 `SpooledTemporaryFile(mode="w+", encoding="utf-8", newline="")`，并用 `csv.writer(..., lineterminator="\n")` 写入。不要自己拼接 CSV。

每一行写入顺序固定为：

```text
sync_batch_id, synced_at, sync_version, 1, 业务列1, 业务列2, ...
```

标准流不写表头。写完必须 `flush()` 和 `seek(0)`。

### 15.4 CSV

1. 原始文件最大只有 20 MB，可以从上传临时文件读取成 `bytes`，但不得把解析后的所有行再保存为 list。
2. 编码依次尝试 `utf-8-sig`、`utf-8`、`gb18030`，全部失败返回“无法识别 CSV 编码”。
3. 用前 8192 个字符执行 `csv.Sniffer().sniff(sample, delimiters=",;\t")`；失败时回退 `csv.excel`（逗号）。
4. 使用 `csv.reader(io.StringIO(text, newline=""), dialect)` 逐行读取。
5. 第一行完全非空的行作为表头；此前空行跳过。
6. 数据空行跳过。短行右侧补空字符串；长行返回“第 N 行列数超过表头”。
7. 没有数据行（只有表头）返回 400。
8. 读取到第 100,001 条数据时立即返回 413。

### 15.5 XLSX（多 Sheet 全部导入）

必须这样打开：

```python
workbook = openpyxl.load_workbook(stream, read_only=True, data_only=True)
```

然后：

1. 按工作簿原顺序遍历所有工作表，`sheet_state != "visible"` 的隐藏 Sheet 直接跳过。
2. 对每个可见 Sheet 使用 `worksheet.iter_rows(values_only=True)`，禁止把整个 Sheet 转成 list。
3. 完全没有任何非空单元格的 Sheet 视为空 Sheet并跳过，不创建数据源。
4. 每个有效 Sheet 的第一行非空行作为该 Sheet 表头，后续数据只写入该 Sheet 自己的 COPY 临时流。
5. 处理规则与 CSV 相同：数据空行跳过、短行补空、长行报错。
6. Sheet 只有表头而没有数据行时不是“空 Sheet”，应返回 400，并在错误信息中包含 Sheet 名称。
7. 每个 Sheet 最大 100,000 行和 200 列；累计数据行最大 300,000 行；第 21 个有效 Sheet 或第 300,001 条累计数据立即返回 413。
8. `_parse_xlsx` 返回 `list[ParsedImport]`，顺序必须与有效 Sheet 在工作簿中的顺序一致；每项拥有独立的 `copy_stream`、表头和行数。
9. `data_only=True` 只读取公式缓存值；缓存不存在时为空。绝对不要计算或执行公式。
10. 如果后面的 Sheet 解析失败，必须关闭前面已经创建的所有 COPY 临时流后再抛异常。
11. 在 `finally` 中执行 `workbook.close()`。
12. 工作簿没有任何可导入的 Sheet 时返回 400。
13. 计数达到任一限制立即停止，防止 XLSX 解压膨胀攻击。

数据源展示名称规则：

```python
item_source_name = f"{source_name} - {parsed.sheet_name}"[:200]
```

Sheet 原名只用于展示名称和 `extra_config.file_import.sheet_name`，绝对不能进入物理表名或物理字段名。

### 15.6 Markdown

只识别代码块外的第一张 GFM 表格：

```markdown
| 姓名 | 金额 |
| --- | ---: |
| 张三 | 12.5 |
```

判定规则：候选表头行的下一行，每个单元格必须匹配 `^:?-{3,}:?$`。`_split_markdown_row` 需要支持 `\|` 转义；切分后移除行首和行尾的外层竖线，并把转义竖线还原。处在三反引号或三波浪号代码块中的内容必须忽略。

找到第一张合法表格后一直读取到空行或不含表格分隔符的行。列数规则、行数限制与 CSV 相同。未找到合法表格或没有数据行返回 400。不要调用现有 LLM 文档解析接口。

## 16. 数据库写入代码必须遵循的细节

### 16.1 建表

对 `parsed_items` 循环处理，每个结果建立一张业务表。业务表创建 SQL 结构固定。动态名称只能来自后端生成的安全表名和 `_normalize_headers` 生成的 ASCII 字段名，仍然必须使用 `quote_identifier()`：

```python
business_sql = ", ".join(
    f"{quote_identifier(column)} TEXT" for column in parsed.physical_columns
)
conn.execute(
    f"""
    CREATE TABLE {quote_identifier(table_name)} (
        id BIGSERIAL PRIMARY KEY,
        sync_batch_id TEXT NOT NULL,
        synced_at TEXT NOT NULL,
        sync_version TEXT,
        is_current INTEGER NOT NULL DEFAULT 1,
        {business_sql}
    )
    """
)
ensure_ods_indexes(conn, table_name)
```

表头至少有一列，所以 `business_sql` 不会为空。禁止使用用户原始表头直接拼 SQL。

### 16.2 COPY

当前项目的包装连接可以通过 `conn.cursor()` 获取原生 psycopg2 cursor。对每个 `ParsedImport` 分别执行一次 COPY；COPY 列顺序必须和该结果的标准临时流完全一致：

```python
copy_columns = [
    "sync_batch_id", "synced_at", "sync_version", "is_current",
    *parsed.physical_columns,
]
column_sql = ", ".join(quote_identifier(name) for name in copy_columns)
copy_sql = (
    f"COPY {quote_identifier(table_name)} ({column_sql}) "
    "FROM STDIN WITH (FORMAT CSV, NULL '\\N')"
)
parsed.copy_stream.seek(0)
with conn.cursor() as cursor:
    cursor.copy_expert(copy_sql, parsed.copy_stream)
```

标准流中真正的空字符串保持为空字符串；`\\N` 才代表数据库 NULL。禁止逐条 `INSERT`，禁止用现有 `append_ods_staging_rows` 分批插入。

COPY 后立即执行：

```sql
SELECT COUNT(*) AS total FROM "目标表" WHERE is_current = 1
```

每张表的结果不等于对应 `parsed.row_count` 时抛异常；外层捕获后回滚整个工作簿，而不是只删除当前 Sheet。

### 16.3 `extra_config`

先调用现有 `build_datasource_extra_config(...)` 生成兼容结构：

```python
field_labels = dict(zip(parsed.physical_columns, parsed.original_headers))
extra = build_datasource_extra_config(
    description="通过管理员上传文件创建",
    business_time_field="",
    chart_field="",
    field_labels=field_labels,
    request_config={},
    response_config={},
    searchable_fields=[],
    quality_rules={},
    verify_tls=True,
    incremental_config={},
)
extra["source_type"] = "file_upload"
extra["file_import"] = {
    "original_filename": filename,
    "extension": extension,
    "sheet_name": parsed.sheet_name,
    "sheet_index": parsed.sheet_index,
    "sha256": file_sha256,
    "file_size": total_bytes,
    "row_count": parsed.row_count,
    "column_count": len(parsed.physical_columns),
    "header_map": field_labels,
}
```

使用 `json.dumps(extra, ensure_ascii=False)` 写入 JSONB。不要只写自定义结构，否则现有 `parse_datasource_config()` 会缺少默认配置键。

### 16.4 元数据写入顺序

同一事务中对每个 `ParsedImport` 依次执行：

1. 插入 `sys_datasource`，使用 `RETURNING *` 取得完整行。
2. 调用 `sync_datasource_field_meta(conn, datasource_row)`。
3. 再执行一次 `UPDATE sys_field_meta SET field_label = ? ...`，确保原始中文表头准确保存。
4. 插入 `sys_sync_log`。
5. 插入 `sys_sync_version`，`is_current=1`。
6. 直接通过当前连接插入 `sys_audit_log` 必填字段，`action='import_file_datasource'`。
7. 把该项成功信息追加到响应 `items`。

所有项全部完成后，循环外只调用一次 `conn.commit()`。严禁在每个 Sheet 循环内部提交。

`sys_datasource.last_status` 固定为 `success`，`last_quality_status` 可为空。`sys_sync_log` 和 `sys_sync_version` 的状态也固定为 `success`。

### 16.5 异常和关闭

数据库代码模板：

```python
conn = get_connection()
try:
    # 验证 platform_id
    # 建表、COPY、校验、写全部元数据
    conn.commit()
except HTTPException:
    conn.rollback()
    raise
except Exception:
    conn.rollback()
    logger.exception("file import failed")
    raise HTTPException(status_code=500, detail="文件写入数据库失败，未保存任何数据")
finally:
    conn.close()
```

解析异常必须在连接数据库之前发现。HTTP 201 只能在所有 Sheet 处理完成且唯一一次 `commit()` 成功后返回。任何 Sheet 失败，响应不得包含部分成功项。

## 17. `main.py` 的精确修改

在其他 router import 附近增加：

```python
from backend.app.api.routers.admin_file_import import router as admin_file_import_router
```

在 `app.mount("/", ...)` 之前、其他 `app.include_router(...)` 附近增加：

```python
app.include_router(admin_file_import_router)
```

绝对不能把 router 注册写在 `app.mount("/", StaticFiles(...))` 后面，否则静态挂载可能先匹配请求。

## 18. 前端必须逐项修改

前端只改 `frontend/app.js`；第一版不要求修改 `index.html`，样式直接复用现有 `admin-panel`、`dm-input`、`dm-btn`。

### 18.1 修复 `apiFetch` 对 FormData 的处理

找到 `async function apiFetch(path, opts)`，把当前“只要有 method 就设置 JSON Content-Type”的代码替换为：

```javascript
const isFormData = !!(opts && opts.body instanceof FormData);
if (opts && opts.method && !isFormData) {
  headers['Content-Type'] = headers['Content-Type'] || 'application/json';
}
if (isFormData && headers['Content-Type']) {
  delete headers['Content-Type'];
}
```

Authorization 逻辑保持原样。上传 FormData 时绝对不能手工设置 `multipart/form-data`，浏览器必须自己添加 boundary。

### 18.2 在数据源管理页添加上传卡片

找到 `async function renderAdminDatasources()`，在页面标题之后、平台管理卡片之前插入下面的 HTML。平台下拉选项复用该函数已经生成的 `platformOpts`：

```javascript
<div class="admin-panel">
  <h3>文件导入数据源</h3>
  <p style="margin:-4px 0 12px;color:#64748b;font-size:12px;">
    支持 XLSX、CSV 和含表格的 Markdown，上传成功后自动创建数据表和数据源。
  </p>
  <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;">
    <input class="dm-input" id="dmFileImportName" placeholder="数据源名称（默认使用文件名）">
    <select class="dm-input" id="dmFileImportPlatform">
      <option value="">— 未分配平台 —</option>
      ${platformOpts}
    </select>
    <input class="dm-input" id="dmFileImportFile" type="file"
           accept=".xlsx,.csv,.md" style="grid-column:1/-1;">
  </div>
  <div style="display:flex;align-items:center;gap:10px;margin-top:10px;flex-wrap:wrap;">
    <button class="dm-btn primary" id="dmFileImportSubmit">上传并创建数据源</button>
    <span id="dmFileImportStatus" style="font-size:12px;color:#64748b;">
      单文件最大 20 MB；XLSX 会把每个可见非空 Sheet 分别创建为数据源。
    </span>
  </div>
</div>
```

这个区域只能放在 `renderAdminDatasources()` 中。现有 `renderAdminView()` 已经判断 `state.user.role === 'admin'`，普通用户不会渲染它；不要把上传卡片放到公共数据浏览页面。

### 18.3 文件选择事件

在 `$('mainContent').innerHTML = html;` 之后绑定事件。选择文件时，如果名称输入框为空，自动用文件名去掉最后一个扩展名：

```javascript
$('dmFileImportFile').addEventListener('change', () => {
  const file = $('dmFileImportFile').files[0];
  if (!file || $('dmFileImportName').value.trim()) return;
  $('dmFileImportName').value = file.name.replace(/\.[^.]+$/, '');
});
```

### 18.4 上传按钮事件

严格执行以下步骤：

1. 再次检查 `state.user && state.user.role === 'admin'`。
2. 必须选文件；扩展名必须是 xlsx/csv/md。
3. 前端检查 `file.size <= 20 * 1024 * 1024`。这只是用户体验，后端仍必须独立检查。
4. 创建 `FormData`，字段名必须与后端完全一致：`file`、`source_name`、`platform_id`。
5. 上传过程中禁用按钮和文件输入，按钮文字改为“正在导入…”。
6. 调用 `apiFetch('/api/admin/file-import', {method:'POST', body:formData})`，不要传 headers。
7. 成功后先保存本次 `result` 到局部变量。CSV/Markdown 汇总为“导入成功：1 个数据源、N 行”；XLSX 汇总为“导入成功：M 个数据源、共 N 行”。
8. 执行 `await loadDynamicPlatforms()`，然后仅当 `currentAdminView === 'datasources'` 时执行 `await renderAdminDatasources()`；重新渲染完成后，再取得新的 `dmFileImportStatus` 元素，把汇总以及 `result.items` 中每个 `Sheet 名、行数、列数` 写进去。所有 Sheet 名必须经过 `escapeHtml()`。这样既能刷新列表，也不会让成功明细被重新渲染清掉。
9. `catch` 使用 `showToast(e.message, true)`，并把错误写入状态文本。
10. `finally` 中恢复按钮和文件输入；但要先检查元素仍存在，因为成功后页面可能已经重新渲染。

事件代码必须防重复提交，可给按钮设置 `dataset.loading = '1'`，进入时发现已经为 1 就直接 return，finally 删除该标记。

### 18.5 管理员权限的最终保障

前端隐藏只用于界面体验，不能作为权限控制。真正权限来自后端路由的：

```python
admin=Depends(require_admin)
```

不要新增前端角色参数，不要相信客户端传来的 `role`，也不要在请求体中传管理员用户名。

## 19. `.env.example` 和部署说明

在 `.env.example` 末尾增加：

```dotenv
# 管理员文件导入限制
DATAMID_UPLOAD_MAX_BYTES=20971520
DATAMID_UPLOAD_MAX_ROWS=100000
DATAMID_UPLOAD_MAX_COLUMNS=200
DATAMID_UPLOAD_MAX_SHEETS=20
DATAMID_UPLOAD_MAX_WORKBOOK_ROWS=300000
```

由于增加了 Python 依赖，部署时不能只重启旧容器，必须重新构建 backend 镜像。不要修改 `docker-compose.yml`，也不需要增加 volume 或新服务。

## 20. 测试文件必须覆盖的断言

`backend/tests/test_file_import.py` 至少写以下测试函数，函数名可直接使用：

```text
test_normalize_headers_handles_chinese_duplicates_and_system_names
test_csv_utf8_with_quotes_commas_and_newlines
test_csv_gb18030
test_csv_rejects_too_many_columns
test_csv_rejects_long_row
test_xlsx_returns_all_visible_non_empty_sheets_in_order
test_xlsx_skips_hidden_and_fully_empty_sheets
test_xlsx_rejects_header_only_sheet
test_xlsx_rejects_more_than_twenty_effective_sheets
test_xlsx_rejects_more_than_total_workbook_row_limit
test_xlsx_formula_is_not_executed
test_markdown_imports_first_table
test_markdown_ignores_fenced_code_block
test_markdown_rejects_document_without_table
test_import_rejects_non_admin
test_import_rejects_unsupported_extension
test_database_failure_rolls_back_table_and_metadata
test_second_sheet_failure_rolls_back_first_sheet_too
test_success_registers_datasource_fields_versions_log_and_audit
```

数据库回滚测试必须主动让元数据插入失败，然后断言：

- `to_regclass(table_name)` 是 NULL。
- `sys_datasource` 没有对应 source_key。
- `sys_field_meta`、`sys_sync_log`、`sys_sync_version`、`sys_audit_log` 都没有对应记录。

多 Sheet 回滚测试必须让第二个或最后一个 Sheet 写入失败，并断言此前 Sheet 已创建的表及元数据也全部不存在，从而证明整个工作簿只有一次事务提交。

完成后运行：

```text
pytest -q backend/tests/test_file_import.py
pytest -q backend/tests
```

如果测试依赖真实 PostgreSQL而当前环境没有数据库，必须清楚报告“哪些测试未运行及原因”，不能把未运行写成通过。

## 21. 禁止事项清单

DeepSeek 实现时以下行为一律视为不合格：

- 新建第二个 FastAPI 应用、第二个端口或第二个 Docker 服务。
- 使用 Pandas、Celery、Redis、RabbitMQ、Kafka或 LLM。
- 使用 FastAPI `BackgroundTasks` 导入关键数据。
- 逐行执行 SQL `INSERT`。
- 客户端决定数据库表名或字段名。
- 根据文件名拼服务器保存路径。
- 解析完成前开启数据库事务。
- 中途 commit 多次。
- 把用户原始表头直接拼进 SQL。
- 只做前端管理员判断，不加 `require_admin`。
- FormData 请求手工设置 `Content-Type`。
- 捕获异常后仍然返回成功。
- 上传失败后保留空表、部分数据或孤立元数据。
- 在每个 Sheet 循环内部单独 commit，造成部分 Sheet 成功。
- 把多个 Sheet 强行合并到同一张表，或让一个 `sys_datasource` 指向多张表。
- 修改现有数据源同步、MCP、登录或权限逻辑来迁就上传功能。
- 顺手格式化整个 `frontend/app.js` 或修改无关中文文本。

## 22. 最终交付检查表

开发 Agent 最终回复前必须逐项确认：

- [ ] 后端仍是原来的单个 FastAPI 服务和 8128 端口。
- [ ] 只新增一个后端业务文件 `admin_file_import.py`。
- [ ] `.csv`、`.xlsx`、`.md` 三种格式均有成功测试。
- [ ] XLSX 每个可见非空 Sheet 分别创建数据源和表，隐藏/空 Sheet 被跳过。
- [ ] XLSX 任一 Sheet 失败时整个工作簿全部回滚。
- [ ] Markdown 只解析第一张合法表格。
- [ ] 路由使用 `require_admin`。
- [ ] 普通用户既看不到入口，也无法直接调用接口。
- [ ] 写入使用 PostgreSQL COPY。
- [ ] 建表、数据和所有元数据处于同一事务。
- [ ] 业务字段统一为 TEXT。
- [ ] 成功后数据源出现在现有管理列表和目录中。
- [ ] 成功后可以通过现有数据查询接口读取。
- [ ] 删除该数据源时业务表也被删除。
- [ ] 失败不会遗留表或元数据。
- [ ] FormData 上传没有手工设置 Content-Type。
- [ ] 新依赖已进入 backend 镜像。
- [ ] 新测试和全部现有测试均已执行并报告结果。
