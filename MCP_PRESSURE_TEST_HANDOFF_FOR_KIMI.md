# 数据中台 MCP 多实例、多次调用压测任务书

> 本文档是给执行压测的 Kimi/自动化代理使用的完整任务说明。请先通读全文，再开始操作。

## 1. 任务目标

对当前数据中台项目进行 MCP 压力测试，重点验证：

1. 多个 MCP Token 同时调用多个数据源；
2. 每个 MCP 连续、多次调用 `tools/call`；
3. 不同查询类型和分页大小下的吞吐与延迟；
4. 高并发、突发流量和长时间运行时的稳定性；
5. MCP 查询与后台全量同步同时运行时是否互相影响；
6. 鉴权、数据源权限、字段权限、审计日志是否正确；
7. SSE 长连接在多会话下是否稳定。

本次压测必须输出可复现的脚本、原始数据和总结报告，不能只给口头结论。

## 2. 项目位置和技术背景

项目根目录：

```text
C:\Users\1202605003\Desktop\新建文件夹\7.17
```

主要组件：

- FastAPI 后端；
- PostgreSQL 数据库；
- Docker Compose 部署；
- MCP Streamable HTTP/普通 HTTP 入口；
- MCP SSE 长连接入口；
- 当前后端使用单个 Uvicorn worker；
- MCP 查询、数据同步和审计写入共用后端与数据库。

关键源码：

```text
backend/app/api/routers/public_data.py
backend/app/services/mcp_service.py
backend/app/services/datasource_query_service.py
backend/app/db/repositories/audit.py
backend/Dockerfile
docker-compose.yml
```

开始前请阅读上述文件中的 MCP 路由、查询逻辑和异常检测规则，但不要修改业务代码。

## 3. 安全和执行边界

1. 优先在独立压测环境执行，不要直接压生产环境。
2. 如果只能使用生产环境，必须先得到明确授权，并从极低并发开始。
3. 不得在脚本、日志、报告或 Git 中保存真实 MCP Token。
4. Token 必须通过环境变量或本机专用配置文件传入。
5. 输出日志时只显示 Token 的序号或末尾四位，不显示完整值。
6. 不得删除业务数据、同步版本、用户、Token或审计记录。
7. 不得执行破坏性 SQL。
8. 不得为了提高成绩而跳过响应校验。
9. 压测完成后清理压测进程，但保留脚本、CSV、HTML和报告。

建议创建一个不提交到 Git 的配置文件：

```text
loadtest/.env.loadtest
```

内容示例：

```dotenv
MCP_BASE_URL=https://测试环境域名/data_center
MCP_TARGETS_JSON=[{"source_key":"source_a","token":"dmc_xxx"},{"source_key":"source_b","token":"dmc_yyy"}]
```

真实 Token 由项目负责人单独提供。如果没有有效 Token，停止实际压测，只完成脚本和静态检查，并在报告中说明阻塞原因。

## 4. 项目现有 MCP 入口

### 4.1 Streamable HTTP/JSON-RPC

```text
POST /api/mcp/data/{source_key}
```

请求头：

```http
Authorization: Bearer <MCP_TOKEN>
Content-Type: application/json
Accept: application/json
```

初始化请求：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {
      "name": "datamid-loadtest",
      "version": "1.0"
    }
  }
}
```

工具列表请求：

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/list",
  "params": {}
}
```

工具调用请求：

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "query_<source_key>",
    "arguments": {
      "page": 1,
      "page_size": 20,
      "keyword": "",
      "filters": {}
    }
  }
}
```

工具名必须是：

```text
query_<source_key>
```

### 4.2 SSE

建立连接：

```text
GET /api/mcp/sse/{source_key}
```

请求头同样使用：

```http
Authorization: Bearer <MCP_TOKEN>
Accept: text/event-stream
```

服务端首先返回 `endpoint` 事件。客户端从事件中取得包含 `session_id` 的地址，然后向该地址 POST JSON-RPC 请求，并从原 SSE 连接读取 `message` 事件。

SSE 测试必须检查：

- endpoint 事件是否成功返回；
- 心跳是否正常；
- 消息是否返回到正确会话；
- 不同 Token、不同数据源之间是否串话；
- 连接断开后会话是否被清理；
- 连接保持超过5分钟时的表现。

## 5. 压测工具与产物要求

主测试建议使用 Python + Locust。原因是需要轮换多个 Token、多个数据源、解析 JSON-RPC 业务错误，并可能扩展 SSE 测试。

请在项目中创建：

```text
loadtest/
  locustfile.py
  requirements.txt
  README.md
  config.example.json
  results/
```

要求：

1. `locustfile.py` 从环境变量读取目标地址和 Token；
2. 支持多个 `{source_key, token}` 目标轮询或随机选择；
3. 每个虚拟用户固定使用一个目标，避免单次会话中频繁切换身份；
4. 每个虚拟用户启动时执行一次 `initialize` 和 `tools/list`；
5. 运行期间重复执行 `tools/call`；
6. HTTP 200 但 JSON-RPC 含 `error` 时必须记为失败；
7. 响应解析失败、超时、401、403、500必须分别统计；
8. 统计维度中必须包含数据源和查询类型，但不能包含完整 Token；
9. 支持命令行无界面运行并输出 CSV、HTML 报告；
10. 对返回数据执行正确性断言。

建议依赖：

```text
locust
requests
python-dotenv
```

## 6. 响应正确性校验

每次 `tools/call` 至少检查：

1. HTTP 状态码为200；
2. 响应是合法 JSON；
3. 不包含 JSON-RPC `error`；
4. `result.content` 是非空数组；
5. `content[0].text` 能再次解析为 JSON；
6. 返回行数不超过请求的 `page_size`；
7. `total` 不小于当前返回行数；
8. `page`、`page_size` 与请求相符；
9. 不返回未授权字段；
10. 不同 Token 不能访问未授权数据源。

权限负向测试必须单独统计，预期结果包括：

- 缺少 Token：401；
- 无效 Token：401；
- 已撤销 Token：403；
- Token 无数据源权限：403或JSON-RPC权限错误；
- 错误工具名：JSON-RPC错误；
- 非法分页参数：受控的400或JSON-RPC错误，不能500。

## 7. 查询流量模型

`tools/call` 按以下比例随机执行：

| 类型 | 比例 | 参数建议 |
|---|---:|---|
| 首页、小分页 | 25% | `page=1,page_size=20` |
| 首页、最大分页 | 20% | `page=1,page_size=200` |
| 精确字段过滤 | 25% | 使用真实存在但不敏感的业务字段 |
| 日期区间过滤 | 15% | 选择有结果的短日期区间 |
| 模糊关键词 | 10% | 使用有结果的常见关键词 |
| 深分页 | 5% | `page=50`或`page=100` |

禁止所有请求都使用空条件。必须同时覆盖轻查询和重查询。

过滤字段和关键词应先通过 `tools/list`、字段元数据或小流量探测确认，不能凭空编造。

## 8. 测试阶段

### 阶段A：冒烟与正确性

目标：确认脚本、Token、数据源和断言正确。

```text
并发用户：1
持续时间：3分钟
调用次数：至少50次
```

通过后执行：

```text
10个Token × 5个数据源 × 每个10次调用
```

如果实际 Token 或数据源不足，使用现有数量，但必须在报告中记录。

### 阶段B：单用户基准

```text
并发用户：1
持续时间：10分钟
```

分别记录不同查询类型的 P50、P95、P99和响应大小。

### 阶段C：阶梯并发

每个阶段持续10分钟，阶段之间不重启服务：

```text
5 → 10 → 20 → 30 → 50 并发用户
```

每个用户两次调用之间等待1～3秒。

如果错误率连续2分钟超过10%，或者数据库/主机出现危险状态，立即停止升压并记录拐点。

### 阶段D：突发流量

```text
5并发运行2分钟
在5秒内升到50并发
保持5分钟
降回5并发并观察5分钟
```

检查服务能否恢复到突发前的延迟水平。

### 阶段E：稳定性

```text
并发用户：20
持续时间：2小时，条件允许时延长到4小时
```

检查内存、连接、审计表和延迟是否随时间持续增长。

### 阶段F：同步竞争

保持20个MCP并发用户运行，同时触发一个约8万条或更大的全量数据同步。

分别记录：

- 同步前10分钟；
- 同步进行期间；
- 同步完成后10分钟。

比较三个时间段的 MCP P95/P99、错误率、数据库连接和同步耗时。

### 阶段G：SSE

SSE 与普通 HTTP 分开执行：

```text
10 → 50 → 100条并发SSE连接
每条连接每30秒发送一次tools/call
每个阶段15分钟
```

如果测试环境连接能力不足，至少完成50条连接。

## 9. MCP异常策略注意事项

项目默认异常阈值大致为：

```text
单Token每分钟30次
单Token每小时返回5,000行
单Token每小时最多5个不同IP
```

压测会很容易触发这些规则。

执行原则：

1. 独立压测环境可将异常动作设为 `alert_only`；
2. 容量测试可临时提高阈值，但必须记录修改前后的值；
3. 必须另做一轮保持默认阈值的异常策略测试；
4. 不要在生产环境关闭安全策略；
5. 如果动作为自动撤销，必须使用可废弃的专用压测 Token。

## 10. 需要采集的指标

### Locust侧

- 总请求数和RPS；
- P50、P90、P95、P99、最大耗时；
- HTTP错误率；
- JSON-RPC业务错误率；
- 超时率；
- 分数据源、分查询类型指标；
- 响应体大小；
- 每个阶段的稳定吞吐上限。

### 后端侧

- 容器CPU、内存；
- 后端线程数；
- 401、403、422、500数量；
- MCP tools/call异常日志；
- SSE当前连接数和异常断开数。

建议采集：

```powershell
docker stats --no-stream
docker compose logs --since 10m backend
```

长时间测试需要周期采集，不要只在结束时采一次。

### PostgreSQL侧

- 当前连接数、活动连接数、等待连接数；
- 慢SQL和锁等待；
- 数据库CPU、内存、磁盘IO；
- 审计表增长量；
- WAL增长量；
- `COUNT`、模糊查询和深分页SQL耗时；
- 是否出现连接耗尽或长事务。

只执行只读监控SQL，不得终止业务连接。

## 11. 初始验收标准

以下是首轮建议标准，最终可根据单用户基线修订：

| 指标 | 建议标准 |
|---|---:|
| HTTP及JSON-RPC综合错误率 | < 1% |
| 精确查询P95 | < 2秒 |
| `page_size=200`普通查询P95 | < 3秒 |
| 模糊查询和深分页P95 | < 5秒 |
| 全部查询P99 | < 10秒 |
| SSE建连成功率 | ≥ 99% |
| SSE异常断开率 | < 1% |
| 数据越权、字段越权、会话串话 | 0 |
| 数据库连接耗尽 | 0 |
| 压测期间同步失败 | 0 |

除延迟外，任何数据错误、越权或会话串话都视为阻断性问题。

## 12. 停止条件

出现任一情况立即停止继续升压：

- HTTP/JSON-RPC错误率连续2分钟超过10%；
- P99连续5分钟超过30秒；
- 后端或数据库CPU持续5分钟超过90%；
- 容器内存持续增长并接近上限；
- PostgreSQL连接耗尽；
- 同步任务失败或当前数据版本异常；
- 出现数据越权、字段越权、Token串用或SSE串话；
- 服务无法在降压后10分钟内恢复。

停止后保留现场指标、日志和时间点，不要立即重启并丢失证据。

## 13. 建议执行命令

具体命令可根据环境调整。示例：

```powershell
cd "C:\Users\1202605003\Desktop\新建文件夹\7.17"
python -m venv loadtest\.venv
loadtest\.venv\Scripts\python.exe -m pip install -r loadtest\requirements.txt
```

无界面运行示例：

```powershell
loadtest\.venv\Scripts\locust.exe `
  -f loadtest\locustfile.py `
  --headless `
  --host "https://测试环境域名/data_center" `
  -u 20 `
  -r 2 `
  -t 10m `
  --csv loadtest\results\stage_c_20users `
  --html loadtest\results\stage_c_20users.html
```

不要把真实 Token 写进命令行，因为命令行可能被历史记录或进程列表采集。

## 14. 最终交付物

压测完成后必须交付：

```text
loadtest/locustfile.py
loadtest/requirements.txt
loadtest/README.md
loadtest/config.example.json
loadtest/results/*.csv
loadtest/results/*.html
loadtest/results/system_metrics.*
loadtest/PRESSURE_TEST_REPORT.md
```

最终报告至少包含：

1. 测试时间、环境、版本和数据规模；
2. Token数量、数据源数量、查询类型；
3. 每个阶段的并发、RPS、P50/P95/P99、错误率；
4. 最大稳定并发和最大稳定RPS；
5. 首个明显性能拐点；
6. 后端和PostgreSQL资源曲线；
7. 同步前、同步中、同步后的性能对比；
8. SSE连接测试结果；
9. 所有错误的分类、数量和典型日志；
10. 正确性、权限和审计校验结果；
11. 瓶颈定位及证据；
12. 按P0/P1/P2分级的改进建议；
13. 明确说明本轮是否达到验收标准。

## 15. 预期重点关注的瓶颈

不要预设结论，但需要重点验证：

1. 单 Uvicorn worker 是否成为并发瓶颈；
2. 每次调用新建数据库连接是否导致连接压力；
3. `COUNT(*)`、模糊匹配和深分页是否导致查询变慢；
4. 每次调用更新Token并写审计是否放大数据库写压力；
5. 全量同步是否与MCP查询争抢CPU、IO和数据库连接；
6. SSE内存会话和无界队列是否随连接数增长；
7. 多worker或多副本情况下SSE内存会话是否失效；
8. 异常检测本身查询审计表是否在高调用量下变慢。

## 16. 给执行者的最终要求

先完成脚本和低并发验证，再逐级升压。每个结论必须附带指标、时间点、日志或SQL证据。不要只说“很慢”“正常”或“扛不住”，必须给出具体并发、RPS、P95/P99、错误率和资源占用。

如果因为缺少Token、测试地址、权限、监控权限或生产授权而无法继续，立即停止相关步骤，在报告中列明缺失项，并完成所有不依赖该权限的准备工作。

## 17. 已知风险的规避与整改方向

以下措施供压测后的整改使用。短期措施用于降低风险，不等于彻底解决；长期措施需要经过设计、开发和回归测试。

### 17.1 单 Uvicorn worker

短期规避：

- 限制入口并发和单请求超时；
- 将同步安排在低峰期；
- 限制重查询、深分页和最大分页的调用频率；
- 设置明确的过载保护，达到阈值时快速失败，不让请求无限排队。

长期整改：

- 将 API、同步worker和调度器拆成独立进程或服务；
- API可以多worker/多副本扩容；
- 调度状态、同步任务和SSE会话迁移到Redis、数据库或消息队列；
- 在完成状态外置前，不要直接增加worker，否则会产生重复调度和SSE会话不一致。

### 17.2 每次调用新建数据库连接

短期规避：

- 使用 PgBouncer transaction pooling；
- 限制后端最大并发；
- 配置数据库连接、语句、锁和空闲事务超时；
- 监控活动连接、等待连接和连接建立速率。

长期整改：

- 在应用中引入有上限的 psycopg 连接池；
- 查询、同步和审计使用不同的连接池或资源配额；
- 为连接池设置获取超时，连接不足时快速返回受控错误。

### 17.3 `COUNT(*)`、模糊查询和深分页

短期规避：

- MCP调用必须优先使用精确 `filters` 和日期范围；
- 限制最大 `page_size` 和最大可访问页码；
- 对高频过滤字段建立合适索引；
- 对模糊查询设置最小关键词长度；
- 对超大结果集返回“请缩小条件”，不要允许无限翻页。

长期整改：

- 使用游标/keyset分页替代深层 `OFFSET`；
- 对搜索字段使用 PostgreSQL `pg_trgm`、全文索引或专用搜索服务；
- 对非关键场景使用估算总数、缓存总数或取消每页精确 `COUNT(*)`；
- 根据真实慢SQL使用 `EXPLAIN (ANALYZE, BUFFERS)` 优化索引和SQL。

### 17.4 每次调用同步更新Token并写审计

短期规避：

- 审计表按时间建立索引并定期归档；
- `last_used_at` 降低更新频率，例如同一Token一分钟最多更新一次；
- 将异常检测阈值查询所需字段建立组合索引；
- 压测环境单独存放或定期清理压测审计数据。

长期整改：

- 使用 outbox、队列或批量异步写入审计；
- 审计表按日期分区；
- Token最近使用信息在内存/Redis合并后周期落库；
- 异常检测使用Redis滑动窗口或专用计数器，不在每次请求时扫描审计表。

审计不能直接关闭；即使异步写，也必须保证失败可追踪和最终落库。

### 17.5 全量同步与MCP查询争抢资源

短期规避：

- 全量同步安排在业务低峰；
- 限制同步数据源并发数和单源分页并发；
- 为同步设置单独的数据库连接上限；
- 当MCP延迟或数据库负载超过阈值时暂停新同步任务；
- 保留 last-known-good 快照，失败时不能覆盖当前版本。

长期整改：

- 将同步服务从API服务中拆出；
- 由全量同步升级为按更新时间、递增ID、游标或CDC增量同步；
- 使用持久任务队列、checkpoint、重试和幂等job-id；
- 需要更强隔离时使用只读副本承载MCP查询。

### 17.6 SSE会话保存在进程内

短期规避：

- SSE入口保持单worker；
- Nginx对同一会话保持粘性路由；
- 设置连接数上限、会话TTL、心跳和单会话队列长度上限；
- 慢消费者达到队列上限后主动断开，避免内存无限增长。

长期整改：

- 将会话路由和消息队列迁移到Redis Streams/PubSub或专用消息系统；
- API实例无状态化后再进行多副本扩容；
- 使用持久会话标识、租约和跨实例消息路由。

### 17.7 多worker/多副本导致调度和内存状态分裂

短期规避：

- 在现有架构未改造前保持单worker、单后端实例；
- 不要仅通过修改 Uvicorn `--workers` 扩容；
- API前设置并发限制和排队上限。

长期整改：

- 调度器独立部署，并通过数据库租约或 leader election 保证单实例执行；
- 同步任务、取消状态、进度、SSE会话全部外置；
- 所有任务携带幂等job-id，避免重复发布数据版本。

### 17.8 MCP异常检测扫描审计表

短期规避：

- 为 `jti + action + created_at` 建立组合索引；
- 为按Token统计行数和IP的查询补充合适索引；
- 对审计表进行按期归档，控制热数据规模；
- 容量压测与安全规则验证分开执行。

长期整改：

- 使用Redis或内存限流器维护分钟/小时窗口；
- 数据库审计作为最终证据，不再承担每请求实时计数；
- 限流器失败时采用明确的 fail-open 或 fail-closed 策略，并按安全要求记录。

### 17.9 推荐整改顺序

建议结合压测证据按以下顺序实施：

1. P0：正确性、权限、Token串用、SSE串话、数据版本覆盖问题；
2. P0：连接耗尽、无超时、无过载保护；
3. P1：连接池/PgBouncer、必要索引、慢SQL治理；
4. P1：审计异步化和异常检测计数器；
5. P1：同步worker、调度器和API服务拆分；
6. P1：SSE状态外置后进行多副本扩容；
7. P2：增量同步、只读副本和更长期的数据架构优化。

每项整改后必须重复相同压测场景，使用同一数据集和并发模型对比前后结果。
