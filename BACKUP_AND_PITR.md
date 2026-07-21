# PostgreSQL 自动备份与 PITR

部署包中的 `postgres-backup` 服务用于执行三层校验：

1. 每日生成 `pg_dump -Fc` 逻辑备份并运行 `pg_restore --list`。
2. 默认每 7 天恢复到隔离数据库 `datamid_restore_verify`，执行基本完整性查询后删除验证库。
3. 默认每 7 天执行 `pg_basebackup`，使用 `pg_verifybackup` 验证，并配合 PostgreSQL WAL 归档支持 PITR。

## 必须配置

`.env` 至少设置：

```dotenv
DATAMID_DB_ADMIN_PASSWORD=<独立的数据库 owner 强密码>
DATAMID_DB_PASSWORD=<datamid_app 运行账户强密码>
DATAMID_BACKUP_DIR=/srv/datamid-backups
```

`DATAMID_BACKUP_DIR` 应放在另一块磁盘、NFS 或已同步到对象存储的目录。不要把备份目录放在 PostgreSQL 数据卷中。备份包含敏感数据，宿主机目录应限制为仅运维账户可读，并使用磁盘或文件系统加密。

默认策略可通过 `.env` 调整：

```dotenv
DATAMID_BACKUP_INTERVAL_SECONDS=86400
DATAMID_LOGICAL_BACKUP_RETENTION_DAYS=14
DATAMID_PITR_BASEBACKUP_INTERVAL_SECONDS=604800
DATAMID_PITR_BASEBACKUP_RETENTION_DAYS=14
DATAMID_PITR_WAL_RETENTION_DAYS=21
DATAMID_RESTORE_VERIFY_INTERVAL_SECONDS=604800
```

WAL 保留天数必须大于基础备份保留天数，否则备份服务会拒绝启动，避免清理仍用于恢复链路的 WAL。

## 查看备份状态

```bash
docker compose ps postgres-backup
docker compose logs --tail=200 postgres-backup
find "${DATAMID_BACKUP_DIR}" -maxdepth 2 -type f -print
```

只有同时看到以下日志才算备份成功：

- `Logical backup verified`
- `Restore verification passed`
- `PITR base backup verified`

## 手工验证某个逻辑备份

该命令只恢复到隔离数据库，不会覆盖 `datamid`：

```bash
docker compose exec postgres-backup \
  sh /opt/datamid/postgres_restore_verify.sh \
  /backups/logical/datamid-YYYYMMDDTHHMMSSZ.dump
```

## PITR 演练

PITR 必须在隔离主机或隔离容器中演练，不能直接覆盖生产 `PGDATA`：

1. 停止隔离 PostgreSQL 实例。
2. 将最近一个已通过 `pg_verifybackup` 的 `base-*` 目录复制到隔离实例的空 `PGDATA`。
3. 将 `${DATAMID_BACKUP_DIR}/wal` 以只读方式挂载到隔离实例的 `/wal_archive`，并在隔离实例 `postgresql.auto.conf` 中配置：

   ```conf
   restore_command = 'cp /wal_archive/%f %p'
   recovery_target_time = '2026-07-14 10:30:00+08'
   recovery_target_action = 'promote'
   ```

4. 创建空文件 `recovery.signal`，以只读方式挂载对应 WAL archive，然后启动隔离实例。
5. 验证业务表、用户数、数据源数及目标时间点数据，记录实际 RPO/RTO。

建议至少每季度做一次完整 PITR 演练，并把日志、目标时间、恢复耗时和验收人保存在变更系统中。
