# CD2 数据库配置设计

类型：配置、接口与数据设计。已确认范围：系统设置维护，SQLite 为唯一持久来源；不保存云密码、临时 Token 或私钥。

## 存储与接口

- `cloud_drive_configuration` 为单例表（`id=1`），字段 `revision`、`values_json`、`updated_at`，schema55新增。字段白名单与范围由配置服务统一校验。
- GET `/api/settings/cloud-drive`：要求 `settings:write`，返回 `values`、`revision`、`updatedAt`、`changeBlocked`、`reason`、`caSha256`、`source=database`、`networkAccess=false`。
- PUT 同路径：请求 `{ "expectedRevision": 0, "values": { ... } }`，完整替换配置；缺失字段使用关闭/空的安全默认值，不接收任意键。修订/任务保护冲突409，字段非法400，权限不足403，存储不可用503。
- 前端通过 `fetchCloudDriveSettings()` / `saveCloudDriveSettings(values, expectedRevision)` 调用。旧会话响应不得覆盖当前用户状态；保存等待确认期间撤权或退出，不得继续发送。

SQL 与访问方式：本项目使用标准库 `sqlite3`，没有 ORM，不新增 ORM 依赖。配置服务执行以下参数化 SQL，并使用 `CloudDriveSettings` 不可变运行快照承载校验结果：

```sql
CREATE TABLE IF NOT EXISTS cloud_drive_configuration (
    id INTEGER PRIMARY KEY CHECK(id=1),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
    values_json TEXT NOT NULL DEFAULT '{}',
    updated_at REAL NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO cloud_drive_configuration(id) VALUES(1);
-- BEGIN IMMEDIATE 内先读取并核对 expectedRevision，再更新并写审计。
UPDATE cloud_drive_configuration SET revision=?,values_json=?,updated_at=? WHERE id=1;
```

## 事务与运行关系

保存事务顺序：取得 SQLite 写锁 → 操作者权限二次复核 → 修订 CAS → 云及普通任务保护检查 → 配置更新 → 全局路径世代失效 → 预算政策更新但保留暂停/冷却 → 审计 → 提交。审计失败必须整体回滚，无网络副作用。

API 每请求获取一个数据库快照，runner 每次明确启动重新获取快照并交给本次线程；匿名 stdin 向独立 worker 仅传配置修订和临时内存秘密，worker 从同库读取并核对修订后才可使用配置。业务写事务还核对快照修订，拒绝旧快照。

保护检查涵盖只读 RPC 租约、探测租约、待启动/运行盘点、目录执行/恢复、隔离槽及未决 RPC，以及普通扫描、文件写入/回滚/迁移、后台任务、本地动作和远端动作锁。保留令牌的终态不视为已回收，不按时间自动解除未知。

公开 PEM 证书链上限16KiB，只允许证书，不接受私钥、任意附带文本、证书文件路径。空链采用系统信任；格式校验与真正 TLS 验证分离，不允许关闭证书校验。审计仅记变更字段名、修订、路径世代和公开 CA 摘要，不记证书正文或端点详情。

## 边界

保存配置不进行连接测试，不生成 Token，不扫描、不读正文、不改云文件。三个功能开关独立且默认关闭；修改配置不代表版本、连接或媒体操作授权已获通过。旧文件配置退出及升级操作另见[操作手册](../manuals/cd2-settings.md#5-旧实例升级)，不在目标设计中隐含自动导入路径。
