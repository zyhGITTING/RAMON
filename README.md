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
| `DATAMID_DB_PASSWORD` | PostgreSQL 密码，生产环境必须改成强密码 |
| `DATAMID_TOKEN_SECRET` | 登录和 MCP token 签名密钥，至少 32 位随机字符串 |
| `DATAMID_RAMON_AUTH` | 连接 ERP/SRM/库存等官方接口所需认证值 |
| `DATAMID_PUBLIC_URL` | 外部访问地址，例如 `http://your-domain/data_center` |
| `DATAMID_INIT_ADMIN_PASSWORD` | 首次部署自动创建管理员账号用，至少 8 位 |
| `DATAMID_ALLOW_SELF_REGISTER` | 留空表示关闭自助注册 |

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
docker exec datamid-pg pg_dump -U datamid datamid > backup-datamid-$(date +%Y%m%d-%H%M%S).sql
```

恢复：

```bash
cat backup-datamid-20260101-030000.sql | docker exec -i datamid-pg psql -U datamid -d datamid
```

建议配置每日自动备份，并把备份文件同步到另一台服务器或对象存储。

## 重要说明

- `.env`、数据库文件、备份文件、`__pycache__` 已通过 `.gitignore` 排除，不会上传 GitHub。
- 当前生产路径使用 PostgreSQL。SQLite 只作为显式开发模式兼容，不参与 Docker 生产部署。
- MCP token 可以被驾驶舱或 agent 调用数据接口使用，生产环境应控制授权范围并定期审计。
