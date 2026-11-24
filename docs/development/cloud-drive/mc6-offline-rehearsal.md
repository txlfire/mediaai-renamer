# MC6 离线副本演练

类型：实施与迁移准备。更新：2026-11-22。目标契约见[多连接演进计划](multi-connection-evolution-plan.md)。开发版多连接未发布，运行库未迁移；合成库旧版回退证据见下文，不能代替现场演练。

## 1. 工具范围

入口：[rehearse-cloud-migration.py](../../../scripts/rehearse-cloud-migration.py)。只接受显式提供、已停止写入的schema55单配置独立数据库文件；不读取默认配置、不导入`app.main`、不初始化数据库、不访问CD2、不启动或停止服务。

工具保留源文件，新建目录中的`baseline.sqlite3`、`candidate.sqlite3`、`restored.sqlite3`及白名单`report.json`。只有candidate调用已有离线迁移服务；第二次调用验证幂等。restored仅证明基线副本字节一致、完整性及外键检查通过，不等于旧二进制回退演练。

输入必须是停服取得并完成一致性核对的离线副本，不能把正在运行的主数据库文件直接当源。非空WAL/回滚日志拒绝；操作者确认和文件哈希只能发现部分变化，不能证明其他进程已退出。已存在多连接扩展的源拒绝，不借此工具降级或升级扩展1/2。

## 2. 命令

前提：确认备份操作已获授权、源实例及worker均已退出、无未决RPC和原生保护；将一致性副本放在受控位置。输出父目录须已存在并限制访问；Windows上`chmod`不等于ACL，工具不宣称已核验ACL，也不自动修改系统权限。副本包含账户及加密凭据材料，不上传Git或聊天，不与发行包混放。

命令示例，只能替换为自己的离线副本路径及已有、启用且具备`settings:write`的操作人ID：

```powershell
& .\.venv\Scripts\python.exe .\scripts\rehearse-cloud-migration.py `
  --source-db D:\MediaAI-Offline\source\mediaai.sqlite3 `
  --output-dir D:\MediaAI-Offline\runs\mc6-unique-run `
  --actor-id 1 --confirm-offline-copy
```

预期：退出0，报告`sourceUnchanged`、`idempotent`、`integrityChecked`和`restoredCopyMatchesBaseline`为true，`networkAccess`、`runtimeReplaced`、`oldBinaryRollbackTested`为false。迁入连接/目录禁用、三个开关关闭且无用户授权；不会修改正式入口或发云请求。

失败：退出1，仅输出有限原因码，不输出路径、配置、用户名或原始异常。已产生的基线和失败候选保留供人工核对，不自动删除、重跑或替换源；再次演练必须使用新的输出目录。

| 原因 | 处理 |
| --- | --- |
| offline_copy_confirmation_required | 确认源是静止的一致性副本，明确操作人，不能通过反复加参数替代停服核对 |
| source_has_live_sidecar | 源仍有非空WAL/回滚日志；先完成受控一致性导出，禁止删除旁文件强行通过 |
| new_output_directory_required | 选择新的受限输出目录，不覆盖原证据 |
| source_changed_during_copy | 停止，核对源是否仍有写者；不继续迁移不一致副本 |
| operator_not_allowed | 核对副本中原操作人启用状态及settings:write，不创建临时超级用户 |
| already_multi_connection_source | 本工具只演练单配置schema55；扩展升级需要单独流程 |
| global_protection_held | 先完成原请求核对，禁止清槽或删预算绕过 |
| copy_rehearsal_failed | 只在受控本机排查文件、空间与SQLite问题，不向界面输出任意异常正文 |

## 3. 实际数据接口

实现沿用sqlite3，没有ORM层，不提供虚构ORM写法。主要只读核对SQL如下，均在离线副本执行；操作人ID使用参数绑定：

```sql
SELECT value FROM app_meta WHERE key = 'schema_version';
SELECT value FROM app_meta WHERE key = 'cloud_multi_storage_version';
SELECT username, enabled, permissions_json FROM users WHERE id = ?;
PRAGMA quick_check;
PRAGMA foreign_key_check;
```

迁移入口为`migrateCloudConnectionStorage(settings, confirmOffline=True, auditContext=context)`，内部由`connectionDb(..., write=True, migration=True)`管理事务。DDL和预算/旧历史策略复用现有迁移器，不复制第二套SQL迁移。候选验证检查连接、目录、版本开关、直接授权表；原历史仍按legacy_unbound处理，不从当前地址认领。

## 4. 本轮证据与出口

- 首轮24项通过：工具6项及误引入的原迁移测试18项，13.868秒；随后移除测试模块的无用TestCase导入，避免重复发现。
- 最终工具7项通过，4.648秒，包含真实子进程CLI、空配置、完整配置、安全默认值、幂等、基线还原、权限、WAL拒绝、复制中变化与失败候选保留。全部使用临时合成库；源哈希/旧库对象未变。
- 旧351文件后端1538项证据仍属于原后台快照，不冒称覆盖新工具。新增脚本及测试不改后端运行服务或schema，按用户精简策略未再次运行30分钟全量。发布前仍需整合制品门禁。
- 尚待批准与实际制品：运行实例一致性备份/停服、匹配的多连接发行制品试运行、目标部署切换与现场回退、真实多连接小范围云验收及发布。禁止将本工具输出直接覆盖运行库。

## 5. v1.4.1 发布包回退演练

2026-11-22 使用已公开的 v1.4.1 ZIP，校验其 SHA256 为 `838996828686d9456680eea2bec685555dcdca552196fe7f6d9d848b3f3338fa`，在隔离目录通过该包的应用创建合成用户和单连接配置。数据库关闭写入后，使用本页 CLI 的同一入口迁移候选，schema 保持 55，增加多连接扩展 3；连接和目录禁用，三个开关关闭，无连接和目录授权，重复迁移不重做。

- **正向**：当前开发源码打开迁移候选的独立副本，原合成用户可登录，设置可查看禁用连接与目录，未获授权时任务选源为空。旧配置 GET 返回升级指引，旧配置 PUT 返回 `409 multi_connection_required`。这不是待发布制品验收。
- **回退**：另解压一份 v1.4.1，使用 `restored.sqlite3` 的副本运行。首页和健康正常，原合成用户 ID、配置字段及修订号一致，无需重新初始化账号，主 schema 55 且不存在多连接扩展。没有让旧版直接打开已迁移库。
- **不改原件**：演练前后源、基线、恢复快照的哈希一致；开发版与旧版各自只消费测试副本，候选快照不变。单独的演练报告记录旧版运行通过；迁移工具自带 `report.json` 仍只记录它能证明的范围，`oldBinaryRollbackTested` 仍为 false。
- **未验**：现网配置和凭据密钥的配套恢复、真实任务与受保护记录、部署替换、NAS/CD2 联调。实际回退须使用匹配的旧版、同次备份的库、配置和密钥；出现新写入先保全新库，不直接降级。过程见[本轮工作记录](../../work-logs/progress-2026-11-22-mc6-rehearsal.md)。

## 6. 整合候选 SQL 清单

| 位置 | 作用及对象 | 执行与验证 |
| --- | --- | --- |
| [cloud_connection_schema.py](../../../backend/app/core/cloud_connection_schema.py) | SQLite 多连接扩展 3；连接与修订、目录与修订、用户授权、偏好、来源命名空间、任务上下文、诊断、请求登记、配置请求及全局风控策略。 | 只随显式离线迁移执行；启动不执行。schema 55 的合成单配置库已验证，主 schema 不变。 |
| [cloud_connection_migration_service.py](../../../backend/app/service/cloud_connection_migration_service.py) 的 `_extendLegacyBindings`、`_upgradeStorage` | 盘点、目录计划/执行/恢复、隔离计划/运行、广告复核及只读会话增加固定来源关联；增量索引和触发器保护历史绑定。兼容迁入扩展 1/2：补诊断所有者字段（扩展 1）及按任务上下文查找的索引，更新扩展标记。 | 仅允许授权管理员操作静止副本。迁移前先核对 schema、完整性和全局未决保护；连接和目录禁用，无默认授权，原任务保留未绑定；不判断历史来源。回退同次旧库、配置和密钥，不手工回删字段。 |
| [cloud_connection_store.py](../../../backend/app/service/cloud_connection_store.py) 及同组运行服务 | 连接、目录、授权、诊断、任务固定来源、审计和预检的运行时 SQLite 操作。 | 随程序调用生效，无另行 SQL 脚本。定向回归用隔离数据验证。 |
| [global_search_service.py](../../../backend/app/service/global_search_service.py) | 五类本地记录的参数化只读检索和鉴权详情，不建表或索引。 | 随程序查询生效，无迁移步骤；搜索后端定向回归验证。 |

个人项目当前只做本地开发与隔离验收。真实 NAS/CD2 检查属于后续部署选项，不是本次送审的现场工作；本记录不授权停服、迁移运行库或启用云执行。
