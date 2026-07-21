# 数据中台部署说明

这是数据中台的部署包。当前版本后端运行入口是 `backend/app/main.py`，旧版根目录大文件已经移出运行链路；生产数据以 PostgreSQL 为准。

## 目录结构

- `backend/`：FastAPI 后端、同步任务、PostgreSQL 初始化脚本
- `frontend/`：前端静态页面和资源
- `docker-compose.yml`：启动 PostgreSQL 和后端服务
- `server-nginx-data-center.conf`：宿主机 nginx 反向代理配置片段
- `.env.example`：环境变量样例，部署前复制为 `.env`

## 部署前准备

服务器需要安装：

- Docker
- Docker Compose v2
- 宿主机 nginx，负责对外提供 `/data_center/` 入口并代理到后端

创建 `.env`：

```bash
cp .env.example .env
```

至少确认这些变量：

| 变量 | 说明 |
|---|---|
| `DATAMID_DB_ADMIN_PASSWORD` | PostgreSQL 启动/运维账号 `datamid_owner` 的密码，不会传给后端；必须与运行密码不同 |
| `DATAMID_DB_PASSWORD` | PostgreSQL 运行账号 `datamid_app` 的密码，生产环境必须改成强密码 |
| `DATAMID_TOKEN_SECRET` | 登录和 MCP token 签名密钥，至少 32 位随机字符串 |
| `DATAMID_RAMON_AUTH` | 连接 ERP/SRM/库存等官方接口所需认证值 |
| `DATAMID_ERP_SUBCONTRACT_URL` | ERP 托工明细表接口地址，留空使用默认地址 |
| `DATAMID_PUBLIC_URL` | 外部访问地址，例如 `http://your-domain/data_center` |
| `DATAMID_INIT_ADMIN_PASSWORD` | 首次部署自动创建管理员账号用，至少 8 位 |
| `DATAMID_ALLOW_SELF_REGISTER` | 留空表示关闭自助注册 |
| `DATAMID_OUTBOUND_ALLOW_HOSTS` | 可信内网/HTTP 出站接口的精确主机名或 IP，逗号分隔；通常留空 |

首次管理员账号：

- 用户名：`admin`
- 工号：`10001`
- 密码：`.env` 里的 `DATAMID_INIT_ADMIN_PASSWORD`

首次登录后建议立即修改密码，并清理 `.env` 中的初始明文密码。

## 启动服务

```bash
docker compose up -d --build
docker compose ps
docker compose logs backend --tail=100
```

当前 compose 只启动：

- `postgres`
- `db-role-init`（一次性数据库角色初始化/升级任务，成功后退出）
- `backend`

后端只绑定本机端口：

```text
127.0.0.1:8128
```

PostgreSQL 只绑定本机端口：

```text
127.0.0.1:5430
```

不要把 8128 或 5430 直接暴露到公网。

## PostgreSQL 最小权限与旧版本升级

数据库账号分为两类：

- `datamid_owner`：PostgreSQL 启动/运维账号，仅用于初始化、角色迁移、备份和恢复。
- `datamid_app`：后端运行账号，不具备 `SUPERUSER`、`CREATEDB`、`CREATEROLE`、复制或绕过行级安全权限；权限仅授予 `datamid` 数据库的应用对象。

`db-role-init` 会在后端启动前幂等执行。对于旧版本已经存在的 volume，它会自动用原 `datamid` 凭据接入，创建新角色、迁移对象所有权，然后清除旧角色密码并禁止旧角色登录。因此升级时：

1. 保留原来的 `DATAMID_DB_PASSWORD`，它会成为 `datamid_app` 的运行密码。
2. 在 `.env` 新增一个不同的强密码 `DATAMID_DB_ADMIN_PASSWORD`。
3. 在维护窗口执行 `docker compose up -d --build`；后端只会在角色任务成功后启动。
4. 检查 `docker compose logs db-role-init`，应看到 `owner=datamid_owner runtime=datamid_app`。

角色初始化任务可安全重跑：

```bash
docker compose run --rm db-role-init
```

如果两个数据库密码相同、两套新旧凭据都无法认证，任务会失败并阻止后端启动，不会回退为超级用户运行。

## 出站接口安全策略

数据源和大模型请求默认只允许 HTTPS，且域名解析出的所有地址都必须是公网地址；回环、私网、链路本地、保留地址和云元数据地址会被拒绝。HTTP 重定向和环境代理不会被跟随。

确实需要访问可信内网或 HTTP 服务时，在 `.env` 中逐个填写精确主机名或 IP：

```dotenv
DATAMID_OUTBOUND_ALLOW_HOSTS=erp.internal.example,llm.internal.example,10.20.0.15
```

该配置不接受 `*`、`.example.com`、CIDR、完整 URL 或端口，避免一次配置放开整个网段。关闭 TLS 证书校验也只允许用于这里明确列出的主机。

## nginx 配置

把 `server-nginx-data-center.conf` 的内容合并到宿主机已有 nginx 的 `server {}` 块里，并把里面的路径改成真实部署路径，例如：

```nginx
root /opt/datamid/frontend;
```

然后执行：

```bash
nginx -t
systemctl reload nginx
```

访问地址示例：

```text
http://YOUR_SERVER/data_center/
```

API 健康检查：

```text
http://YOUR_SERVER/data_center/api/health
```

## 日常运维命令

```bash
docker compose ps
docker compose logs -f backend
docker compose logs -f postgres
docker compose restart backend
docker compose up -d --build
docker compose down
```

## 数据备份

主数据在 PostgreSQL 中。备份：

```bash
docker compose up -d postgres-backup
docker compose logs -f postgres-backup
```

恢复：

```bash
docker compose exec postgres-backup \
  sh /opt/datamid/postgres_restore_verify.sh \
  /backups/logical/datamid-YYYYMMDDTHHMMSSZ.dump
```

自动备份服务会校验逻辑备份、定期恢复到隔离验证库、生成并验证 PITR 基础备份，同时持续检查 WAL 归档。生产环境必须把 `DATAMID_BACKUP_DIR` 指向独立磁盘、NFS 或会异地同步的目录。完整操作见 `BACKUP_AND_PITR.md`。

建议配置每日自动备份，并把备份文件同步到另一台服务器或对象存储。

## 重要说明

- `.env`、数据库文件、备份文件、`__pycache__` 已通过 `.gitignore` 排除，不会上传 GitHub。
- 当前生产路径使用 PostgreSQL。SQLite 只作为显式开发模式兼容，不参与 Docker 生产部署。
- MCP token 可以被驾驶舱或 agent 调用数据接口使用，生产环境应控制授权范围并定期审计。
