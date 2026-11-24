# CD2 多连接详细技术契约

类型：详细设计。更新：2026-10-25。本文描述目标结构和接口，实际实施/验证进度见演进计划，不表示现有v1.4.0已支持端到端多连接，不是运行库迁移脚本；不指定新schema号。范围依据[需求D1至D8](multi-connection-requirements.md)，迁移步骤另见[演进计划](multi-connection-evolution-plan.md)。

## 1. 固定边界

- 配置、授权、偏好及任务上下文存SQLite，不回写config.toml。主账号密码、CD2 Token和私钥均不保存。
- 新任务必须显式选择connectionId、directoryProfileId；历史动作仅由原记录解析，默认选择不参与解析。
- 连接、目录、用户授权、原动作权限取交集；没有角色，没有管理员自动代执行。
- 全实例单云RPC、原worker和写/恢复互斥、暂停、冷却、账本及普通文件任务栅栏保留。风险组首版固定一个，不按连接拆池。
- 同连接未归档目录配置之间禁止相等/祖孙批准根，包括停用配置；归档记录不再作为可使用的批准根，但历史版本和任务不删除。重新启用或解除归档时重新检查冲突。跨连接同目录允许，已知别名提示授权并集，不产生deny ACL。
- 修改根、映射标识或连接归属不在原目录配置上重绑，必须新建配置并重新授权/盘点。归档原配置释放的是配置占用，不是释放未知保护或使旧任务变新。
- 当前批准范围内选择更深目录仍允许；不得将字符串前缀匹配代替目录段比较。

## 2. 数据结构

### 2.1 公共约定

UUID由服务端生成并校验规范形式；requestId由客户端为一次意图生成。SQLite列名沿用snake_case，HTTP和Python服务参数采用camelCase。修订为0至2^63-2整数，拒绝bool；时间为UTC Unix秒。所有连接启用foreign_keys，每次写使用BEGIN IMMEDIATE，审计和业务变更同事务提交。

下列DDL适用于已有users、user_sessions、cloud_drive_read_sessions的空设计副本，只创建新模型，不ALTER既有表。生产迁移还须完成演进计划的停写、副本、旧记录隔离及回退门禁。SQLite没有ORM，本项目继续采用标准库sqlite3；下文仓储契约是对应实现边界，不新增ORM依赖。

```sql
CREATE TABLE cloud_risk_groups (
    id INTEGER PRIMARY KEY CHECK(id=1),
    name TEXT NOT NULL CHECK(name='global-conservative')
);
INSERT INTO cloud_risk_groups VALUES(1,'global-conservative');

CREATE TABLE cloud_global_policy (
    id INTEGER PRIMARY KEY CHECK(id=1),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
    min_interval REAL NOT NULL CHECK(min_interval BETWEEN 2 AND 300),
    failure_cooldown INTEGER NOT NULL CHECK(failure_cooldown BETWEEN 60 AND 86400),
    updated_at REAL NOT NULL
);

CREATE TABLE cloud_configuration_requests (
    request_id TEXT PRIMARY KEY NOT NULL,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    resource_kind TEXT NOT NULL CHECK(resource_kind IN ('connection','profile')),
    resource_id TEXT NOT NULL,
    intent_hash TEXT NOT NULL CHECK(length(intent_hash)=64),
    created_at REAL NOT NULL,
    UNIQUE(resource_kind,resource_id)
);

CREATE TABLE cloud_source_namespaces (
    id TEXT PRIMARY KEY NOT NULL,
    relation_state TEXT NOT NULL CHECK(relation_state IN ('unverified','operator_confirmed')),
    evidence_ref TEXT,
    path_epoch INTEGER NOT NULL DEFAULT 0 CHECK(path_epoch>=0),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
    risk_group_id INTEGER NOT NULL DEFAULT 1 REFERENCES cloud_risk_groups(id) CHECK(risk_group_id=1),
    created_at REAL NOT NULL,
    CHECK(relation_state='unverified' OR evidence_ref IS NOT NULL)
);

CREATE TABLE cloud_connections (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
    name_key TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'disabled' CHECK(state IN ('enabled','disabled','archived')),
    metadata_revision INTEGER NOT NULL DEFAULT 0 CHECK(metadata_revision>=0),
    state_revision INTEGER NOT NULL DEFAULT 0 CHECK(state_revision>=0),
    security_revision INTEGER NOT NULL CHECK(security_revision>=1),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(name_key),
    FOREIGN KEY(id,security_revision) REFERENCES cloud_connection_versions(connection_id,revision)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE cloud_connection_versions (
    connection_id TEXT NOT NULL REFERENCES cloud_connections(id),
    revision INTEGER NOT NULL CHECK(revision>=1),
    endpoint TEXT NOT NULL,
    target_key TEXT NOT NULL,
    ca_pem TEXT NOT NULL CHECK(length(ca_pem)<=16384),
    trust_hash TEXT NOT NULL CHECK(length(trust_hash)=64),
    tls_policy TEXT NOT NULL CHECK(tls_policy='strict'),
    created_at REAL NOT NULL,
    PRIMARY KEY(connection_id,revision)
);
CREATE INDEX idx_cloud_connection_target ON cloud_connection_versions(target_key,connection_id,revision);

CREATE TABLE cloud_directory_profiles (
    id TEXT PRIMARY KEY NOT NULL,
    connection_id TEXT NOT NULL REFERENCES cloud_connections(id),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
    name_key TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'disabled' CHECK(state IN ('enabled','disabled','archived')),
    metadata_revision INTEGER NOT NULL DEFAULT 0 CHECK(metadata_revision>=0),
    state_revision INTEGER NOT NULL DEFAULT 0 CHECK(state_revision>=0),
    security_revision INTEGER NOT NULL CHECK(security_revision>=1),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(connection_id,name_key),
    UNIQUE(connection_id,id),
    FOREIGN KEY(id,security_revision) REFERENCES cloud_directory_profile_versions(profile_id,revision)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE cloud_directory_profile_versions (
    profile_id TEXT NOT NULL REFERENCES cloud_directory_profiles(id),
    revision INTEGER NOT NULL CHECK(revision>=1),
    approved_root TEXT NOT NULL CHECK(length(approved_root) BETWEEN 2 AND 4096),
    mapping_key TEXT NOT NULL CHECK(length(mapping_key) BETWEEN 1 AND 64),
    source_namespace_id TEXT NOT NULL REFERENCES cloud_source_namespaces(id),
    inventory_enabled INTEGER NOT NULL DEFAULT 0 CHECK(inventory_enabled IN (0,1)),
    rename_enabled INTEGER NOT NULL DEFAULT 0 CHECK(rename_enabled IN (0,1)),
    quarantine_enabled INTEGER NOT NULL DEFAULT 0 CHECK(quarantine_enabled IN (0,1)),
    quarantine_name TEXT NOT NULL CHECK(length(CAST(quarantine_name AS BLOB)) BETWEEN 1 AND 240),
    created_at REAL NOT NULL,
    PRIMARY KEY(profile_id,revision)
);
CREATE INDEX idx_cloud_profile_parent ON cloud_directory_profiles(connection_id,state,id);
CREATE INDEX idx_cloud_profile_source ON cloud_directory_profile_versions(source_namespace_id,profile_id,revision);

CREATE TABLE cloud_connection_user_grants (
    connection_id TEXT NOT NULL REFERENCES cloud_connections(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    allowed INTEGER NOT NULL DEFAULT 0 CHECK(allowed IN (0,1)),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
    updated_by INTEGER NOT NULL REFERENCES users(id),
    updated_at REAL NOT NULL,
    PRIMARY KEY(connection_id,user_id)
);
CREATE TABLE cloud_directory_user_grants (
    connection_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    profile_id TEXT NOT NULL,
    allowed INTEGER NOT NULL DEFAULT 0 CHECK(allowed IN (0,1)),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
    updated_by INTEGER NOT NULL REFERENCES users(id),
    updated_at REAL NOT NULL,
    PRIMARY KEY(connection_id,user_id,profile_id),
    FOREIGN KEY(connection_id,user_id) REFERENCES cloud_connection_user_grants(connection_id,user_id),
    FOREIGN KEY(connection_id,profile_id) REFERENCES cloud_directory_profiles(connection_id,id)
);
CREATE INDEX idx_cloud_grant_user ON cloud_connection_user_grants(user_id,allowed,connection_id);
CREATE INDEX idx_cloud_directory_grant_user ON cloud_directory_user_grants(user_id,allowed,connection_id,profile_id);

CREATE TABLE cloud_user_preferences (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    connection_id TEXT,
    profile_id TEXT,
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
    updated_at REAL NOT NULL,
    FOREIGN KEY(connection_id) REFERENCES cloud_connections(id),
    FOREIGN KEY(connection_id,profile_id) REFERENCES cloud_directory_profiles(connection_id,id),
    CHECK(profile_id IS NULL OR connection_id IS NOT NULL)
);

CREATE TABLE cloud_task_contexts (
    id TEXT PRIMARY KEY NOT NULL,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    auth_session_id INTEGER NOT NULL CHECK(auth_session_id>0),
    action TEXT NOT NULL CHECK(action IN ('inventory','diagnostic','directory_plan','directory_execute',
        'directory_observe','directory_reverse','ad_review','quarantine_plan','quarantine_move',
        'quarantine_observe','quarantine_reverse')),
    connection_id TEXT NOT NULL,
    connection_security_revision INTEGER NOT NULL,
    connection_state_revision INTEGER NOT NULL,
    profile_id TEXT NOT NULL,
    profile_security_revision INTEGER NOT NULL,
    profile_state_revision INTEGER NOT NULL,
    connection_grant_revision INTEGER NOT NULL,
    directory_grant_revision INTEGER NOT NULL,
    source_namespace_id TEXT NOT NULL REFERENCES cloud_source_namespaces(id),
    source_epoch INTEGER NOT NULL CHECK(source_epoch>=0),
    source_revision INTEGER NOT NULL CHECK(source_revision>=0),
    selected_path TEXT NOT NULL,
    policy_hash TEXT NOT NULL CHECK(length(policy_hash)=64),
    recovery_identity_hash TEXT NOT NULL CHECK(length(recovery_identity_hash)=64),
    connection_name_snapshot TEXT NOT NULL,
    profile_name_snapshot TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(connection_id,connection_security_revision)
        REFERENCES cloud_connection_versions(connection_id,revision),
    FOREIGN KEY(profile_id,profile_security_revision)
        REFERENCES cloud_directory_profile_versions(profile_id,revision),
    FOREIGN KEY(connection_id,profile_id) REFERENCES cloud_directory_profiles(connection_id,id),
    FOREIGN KEY(connection_id,actor_id) REFERENCES cloud_connection_user_grants(connection_id,user_id),
    FOREIGN KEY(connection_id,actor_id,profile_id)
        REFERENCES cloud_directory_user_grants(connection_id,user_id,profile_id)
);
CREATE INDEX idx_cloud_context_owner ON cloud_task_contexts(actor_id,connection_id,profile_id,created_at DESC,id);

CREATE TABLE cloud_request_registry (
    request_id TEXT PRIMARY KEY NOT NULL,
    context_id TEXT NOT NULL REFERENCES cloud_task_contexts(id),
    parent_context_id TEXT REFERENCES cloud_task_contexts(id),
    actor_id INTEGER NOT NULL REFERENCES users(id),
    resource_kind TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    intent_hash TEXT NOT NULL CHECK(length(intent_hash)=64),
    created_at REAL NOT NULL,
    UNIQUE(resource_kind,resource_id)
);
CREATE INDEX idx_cloud_request_context ON cloud_request_registry(context_id);

CREATE TABLE cloud_connection_diagnostics (
    id TEXT PRIMARY KEY NOT NULL,
    request_id TEXT NOT NULL UNIQUE REFERENCES cloud_request_registry(request_id) DEFERRABLE INITIALLY DEFERRED,
    context_id TEXT NOT NULL REFERENCES cloud_task_contexts(id),
    session_id TEXT NOT NULL UNIQUE REFERENCES cloud_drive_read_sessions(id),
    status TEXT NOT NULL CHECK(status IN ('accepted','running','completed','interrupted','cancelled','review')),
    tls_state TEXT NOT NULL DEFAULT 'not_run' CHECK(tls_state IN ('not_run','passed','failed')),
    token_state TEXT NOT NULL DEFAULT 'not_run' CHECK(token_state IN ('not_run','passed','failed','not_provided')),
    runtime_state TEXT NOT NULL DEFAULT 'not_run' CHECK(runtime_state IN ('not_run','passed','failed','permission_missing','unsupported')),
    core_build TEXT,
    cloud_api_build TEXT,
    reason_code TEXT,
    used_rpc INTEGER NOT NULL DEFAULT 0 CHECK(used_rpc BETWEEN 0 AND 2),
    received_bytes INTEGER NOT NULL DEFAULT 0 CHECK(received_bytes BETWEEN 0 AND 65536),
    worker_token TEXT,
    worker_pid INTEGER,
    owner_pid INTEGER CHECK(owner_pid IS NULL OR owner_pid BETWEEN 1 AND 2147483647),
    owner_host TEXT CHECK(owner_host IS NULL OR length(owner_host)=64),
    owner_instance TEXT,
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    CHECK(expires_at>created_at)
);
CREATE UNIQUE INDEX idx_cloud_diagnostic_worker ON cloud_connection_diagnostics((1))
    WHERE worker_token IS NOT NULL OR worker_pid IS NOT NULL OR status IN ('accepted','running');
CREATE INDEX idx_cloud_diagnostic_context ON cloud_connection_diagnostics(context_id,created_at DESC,id);

CREATE TRIGGER cloud_connection_version_no_update BEFORE UPDATE ON cloud_connection_versions
BEGIN SELECT RAISE(ABORT,'immutable_connection_version'); END;
CREATE TRIGGER cloud_connection_version_no_delete BEFORE DELETE ON cloud_connection_versions
BEGIN SELECT RAISE(ABORT,'referenced_history_retained'); END;
CREATE TRIGGER cloud_profile_version_no_update BEFORE UPDATE ON cloud_directory_profile_versions
BEGIN SELECT RAISE(ABORT,'immutable_profile_version'); END;
CREATE TRIGGER cloud_profile_version_no_delete BEFORE DELETE ON cloud_directory_profile_versions
BEGIN SELECT RAISE(ABORT,'referenced_history_retained'); END;
CREATE TRIGGER cloud_context_no_update BEFORE UPDATE ON cloud_task_contexts
BEGIN SELECT RAISE(ABORT,'immutable_task_context'); END;
CREATE TRIGGER cloud_context_no_delete BEFORE DELETE ON cloud_task_contexts
BEGIN SELECT RAISE(ABORT,'referenced_history_retained'); END;
CREATE TRIGGER cloud_request_no_update BEFORE UPDATE ON cloud_request_registry
BEGIN SELECT RAISE(ABORT,'immutable_request_identity'); END;
CREATE TRIGGER cloud_request_no_delete BEFORE DELETE ON cloud_request_registry
BEGIN SELECT RAISE(ABORT,'request_identity_retained'); END;
CREATE TRIGGER cloud_configuration_request_no_update BEFORE UPDATE ON cloud_configuration_requests
BEGIN SELECT RAISE(ABORT,'immutable_configuration_request'); END;
CREATE TRIGGER cloud_configuration_request_no_delete BEFORE DELETE ON cloud_configuration_requests
BEGIN SELECT RAISE(ABORT,'configuration_request_retained'); END;
```

user_sessions是既有认证表，auth_session_id保存原不可变编号快照，不保存会话Token，也不设外键：现有cleanupExpiredSessions会物理删除过期会话，不能因历史任务阻止该清理。该编号来自AUTOINCREMENT主键，不复用；上下文受理时事务验证归属，每次发送重新验证存在/归属/未撤销/未过期，缺行一律拒绝。历史不依赖原会话行保留，恢复须新会话重新确认。

DDL不代替业务校验：name_key取去首尾空白后的NFC字符串，不忽略大小写；路径维持现有canonicalCloudPath/approvedCloudRoot语义，不做Unicode归一或大小写折叠。mapping_key沿用1至64字符的ASCII字母/数字/下划线/点/连字符，隔离名称沿用CloudDriveSettings的安全单层目录规则和240字节上限。端点使用canonicalCd2Target的host:port键；CA沿用严格公开PEM校验，空CA为系统信任，trust_hash包含该模式标识，不将证书指纹当永久实例身份。目标唯一性、根重叠、版本连续、授权修订、JSON白名单和配额在同一写事务校验。

同一规范端点最多一个未归档连接，包括停用连接；历史版本不参与当前端点占用。归档后新建同端点连接不会继承授权/验证或认领历史；旧连接解除归档时重新检查。连接/目录首版仅软归档，不提供硬删除。引用用户不能被级联删除，账号禁用沿用现有机制；认证会话则沿原有界清理，编号快照不保活过期凭据。

### 2.2 既有表绑定清单

| 根记录 | 新列与绑定规则 |
| --- | --- |
| cloud_drive_read_sessions | context_id可空FK，旧记录为NULL；新云会话必须非空且与拥有者、范围、端点一致 |
| cloud_drive_inventory_jobs | context_id、binding_state；新任务bound，旧记录legacy_unbound；与session同上下文 |
| cloud_directory_plans / cloud_quarantine_plans | context_id、binding_state；从原job/review派生，并显式保存原上下文关系 |
| cloud_directory_executions / cloud_directory_recoveries / cloud_quarantine_runs | context_id、binding_state；运行使用新动作上下文，但连接/目录及身份固定为原链；恢复有parent_context_id追溯 |
| cloud_ad_reviews | context_id、binding_state；保持原job绑定，不采纳当前选择器 |
| 子条目、RPC、outcomes、路径修订、日志 | 通过已有不可变父ID连接根上下文；不复制可覆盖端点；日志查询复核同一授权 |
| cloud_drive_rpc_lease、probe lease、全局保护 | 不改变全局唯一键；准入能从session/执行根追溯context；非任务probe加显式上下文引用 |

binding_state取bound/legacy_unbound，CHECK约束保证bound必须context_id非空、legacy_unbound必须为空。新插入不得选legacy_unbound，仅迁移器允许。每根表增加(context_id,created_at,主键)索引；既有owner索引保留。历史列表通过context联接复合owner/connection/profile索引，实际EXPLAIN和分页规模须MC2验证，不以DDL检查冒充性能验收。

request_registry覆盖新版本全部新建意图，不跨不同动作复用requestId。旧各表request_id唯一性保留；受理先检查旧表的固定注册清单，防止新UUID撞旧请求而生成第二意图。不能依靠每张表自己的UNIQUE实现跨动作幂等。

诊断阶段的持久依据使用服务端固定resource_kind：diagnostic_tls或diagnostic_token_runtime；诊断记录与登记的resource_id/id、context_id必须一致。不可只从最新浏览器请求或tls_state等可变结果猜最初批准的阶段。客户端仍只提交stage，不能任意指定resource_kind；未知种类或同context多重登记均失败关闭。这样不新增可覆盖原意图的stage字段，也不修改旧请求登记。

## 3. sqlite3仓储及事务契约

| 方法 | 输入与事务内约束 | 输出及副作用 |
| --- | --- | --- |
| createCloudConnection | settings:write二次核对、名称/目标唯一、总配额；父行与安全版本同事务插入 | disabled、动作无关；审计，不联网 |
| updateCloudConnectionMetadata | expectedMetadataRevision；相同name无变化 | 名称CAS，不推进安全/路径/预算 |
| publishCloudConnectionVersion | expectedSecurityRevision；端点规范值须与当前连接一致；实际 CA 更新时，任何活跃 worker/probe/未知/本地动作保护存在均拒绝 | CA 更新追加不可变版本，CAS 当前指针，原执行及诊断依据失效；保留直接授权，不推进真实路径世代 |
| createCloudDirectoryProfile | 固定connectionId；批准根/映射；未归档根重叠检查及配额 | 新source命名空间默认unverified，三个动作false，disabled，无授权 |
| publishCloudDirectoryPolicy | expectedSecurityRevision；只改动作开关/隔离名称；根/映射/source归属不可重绑 | 新版本；启用需全局无保护，关闭总可记录并阻止后续准入，不清未知 |
| replaceCloudUserGrant | expectedGrantRevision和显式目录ID；所有ID属于连接，未知ID不忽略 | 原子替换当前白名单；撤销行保留allowed=0并递增revision；连接修订及变化目录修订推进 |
| setCloudLifecycle | expectedStateRevision；停用立即停止后续；归档须无绑定活跃/未知；解除归档复查唯一/重叠/配额 | 不删除历史、不放松全局保护；授权不因重新启用恢复旧执行依据 |
| prepareCloudIntent | requestId、显式来源、期望安全/state/grant修订、确认、scope和policyHash | 单事务固定context、全局UUID注册、session/业务根和审计；无Token/无网络 |
| resolveCloudTaskContext | 只接受原recordId、当前操作者和mode，不允许endpoint覆盖 | 不可变连接/目录版本＋当前授权/状态校验后的内存快照 |
| admitCloudRpc | worker所有权、原context/session、当前权限/修订、全局保护、时间/次数/字节 | 沿既有lease/intent占槽并记账；未知不自动重试 |
| saveCloudPreference | 当前用户、expectedRevision、明确ID或清空；复核可用授权 | 只写本人偏好，不影响任何上下文 |

全局频率政策唯一持久入口为cloud_global_policy；迁移保留原有效值，pool保存实际准入状态及匹配政策修订，不读某连接的局部值。更新时同时CAS政策和pool，暂停、冷却、已消费账本不重置。物理有效时间只能延后，不因提高间隔或冷却清除原cooldown_until。

所有方法使用参数化SQL及受控表名注册，不从请求拼SQL。正常写CAS示例（非完整迁移）：

```sql
UPDATE cloud_connections
SET name=:name, name_key=:nameKey,
    metadata_revision=metadata_revision+1, updated_at=:now
WHERE id=:connectionId AND metadata_revision=:expectedMetadataRevision;
```

sqlite3实现必须检查cursor.rowcount==1；0行按授权过滤后区分404/409，不写成功审计。CAS、版本INSERT、审计任一步失败整体rollback。无变化保存返回原修订。写锁timeout沿用0.2秒，不循环重试用户写意图；每个写事务内不做网络、证书联网校验或等待间隔。创建父行与版本的循环FK采用DEFERRABLE并在commit验证，不能关闭foreign_keys规避。

重叠检测从该连接未归档profile的当前版本读取根，复用isWithinCloudRoot双向比较；不使用LIKE，因为云路径可能含%和_。写事务串行保证同时新增父/子根不会双通过。归档后新建配置不复用旧ID或用户白名单。

## 4. 修订、幂等与会话

| 维度 | 比较与失效 |
| --- | --- |
| metadataRevision | 仅名称CAS；历史使用创建时名称快照，列表可另显当前名称；不失效观察 |
| securityRevision | 当前指针需匹配新动作期望；旧版本只读不可改；关闭动作可阻止后续，不影响原请求事实 |
| stateRevision | 停用/归档/恢复均推进；旧已受理动作不能因重新启用自动复活 |
| grantRevision | 连接用户为授权集合聚合修订；目录行独立修订；撤销再授予不恢复旧发送资格 |
| sourceEpoch/sourceRevision | 远端真实路径变化/来源关系修订；已知别名联动，未知关联时保守失效全部云观察并保留普通文件全局水位 |
| taskRevision | 原执行及日志状态CAS；不复用配置修订代替 |

intent_hash对规范JSON做SHA256：actorId、原authSessionId、action、连接/目录ID及安全/state/grant修订、source世代/修订、本次根、policyHash、父记录ID及本次业务字段；不含Token。新会话可查询本人原requestId但不能用同UUID改变意图；已受理start不重复交接Token。原requestId同指纹返回原资源，异指纹409；他人ID按404，不回显冲突拥有者。

摘要序列化固定为UTF-8、JSON键排序、无额外空白、ensure_ascii=False、拒绝NaN/Infinity，顶层包含contractVersion=1和各自用途标识，不能用无分隔字符串拼接。policyHash包括固定预算、全局政策修订、动作及连接/目录安全与状态修订，还包括原有cloud_directory_path_epoch的全局修订；在来源独立性无法证明时，不能只检查新source_namespace.path_epoch而遗漏旧全局路径变化。recovery_identity_hash按下述身份白名单另算，不含可关闭开关。摘要格式变化必须新contractVersion，不能在发布中无声改变旧指纹算法。

当前会话在父进程、子进程和每次准入复核：查user_sessions归属、未撤销/未过期及users.enabled和动作权限。会话到期只停止后续发送，不把正在发生的远端请求当取消成功；结果仍可由原worker按原lease记事实，不能因撤权丢失响应证据。新会话需要独立恢复上下文、新requestId和确认，不重放旧写。

HTTP层从现有MediaAI Bearer在事务内查得本地会话编号，不接受浏览器提交auth_session_id作为凭证。Worker仅持原编号并复核原会话有效性，不接收或保存MediaAI Bearer。发送准入与原响应事实入账是两条校验路径：前者检查当前权限及会话，后者以原worker能力、PID、原intent和context归属核对，不能因撤权而丢掉已发送请求的真实响应。事实入账入口不对外提供绕权读取。

只读历史使用当前登录会话及当前内容授权，不要求创建时会话仍有效，也不要求旧security/state/grant修订等于当前值；旧依据过期作为状态显示，不能因此把历史误送新目标。执行/派生/恢复分别走专门mode校验，不能将read_history解析结果直接交给发送器。撤权只返回第7节脱敏保护摘要，legacy_unbound另按原拥有者及原动作权限只读规则处理。

recovery_identity_hash仅包含原规范端点、严格信任模式/公开CA摘要、批准根、映射标识、source命名空间/修订、本次范围及原规则身份；排除名称、偏好、生命周期和可关闭的动作开关。恢复比较原身份不变，并重新检查当前用户的动作/连接/目录授权、维护窗口和预算；不要求重新打开写开关。

派生新计划与继续旧执行分别处理：已有执行/已受理start必须匹配原安全/state/grant修订，不能自动复活；从本人仍可访问、未清理且路径世代有效的观察显式创建新计划时，可采用当前授权与动作政策生成新context，但原连接/目录ID及recovery_identity_hash必须相同。仅开关变化不强迫重新读取云目录；端点/CA/根/映射或身份规则变化则拒绝复用原观察。新计划在原request_registry中记录parent_context_id，不能修改旧context或将legacy_unbound历史变bound。先前撤权后重新授予，只允许用户重新确认的新动作，不恢复旧worker发送能力。

## 5. 调用链逐项改造清单

本表是实现影响清单，不表示源码已修改。证据以当前真实模块为准。

| 现有入口/模块 | 必须改造或保持的契约 |
| --- | --- |
| [settings.py](../../../backend/app/api/settings.py)、[configuration_service](../../../backend/app/service/cloud_drive_configuration_service.py) | 替代新动作单例解析，新增管理/使用分离API；旧兼容入口不可写 |
| [cloud_drive.py](../../../backend/app/api/cloud_drive.py)的_cloudSettings | 新意图按明确ID，原任务按根ID；不得request级缓存一份可变全局当前来源 |
| [inventory_web_service](../../../backend/app/service/cloud_inventory_web_service.py) | policy/prepare/start、Runner.start分别核对固定上下文；prepare改为一个事务，不能session与job分步部分提交 |
| [inventory_service](../../../backend/app/service/cloud_inventory_service.py)、[lifecycle_service](../../../backend/app/service/cloud_inventory_lifecycle_service.py)、[advice_service](../../../backend/app/service/cloud_inventory_advice_service.py) | 创建、分页、保留、清理、本地建议按原上下文和授权；清理不删上下文/账本 |
| [inventory_worker_service](../../../backend/app/service/cloud_inventory_worker_service.py)、[inventory_worker](../../../backend/app/cloud_inventory_worker.py) | stdin改contextId及原记录/worker能力；子进程从同库不可变版本读取，不按单例修订重解 |
| [budget_service](../../../backend/app/service/cloud_drive_budget_service.py)、[directory_guard](../../../backend/app/service/cloud_directory_guard.py)、[probe_service](../../../backend/app/service/cloud_directory_probe_service.py) | session与probe引用context，全局唯一/未知/冷却保持；TLS诊断也不能旁路全局网络准入 |
| [directory_plan_service](../../../backend/app/service/cloud_directory_plan_service.py)、[reverse_service](../../../backend/app/service/cloud_directory_reverse_service.py) | 原观察/条目同来源；反向新动作/新上下文，不更新旧context |
| [directory_web_service](../../../backend/app/service/cloud_directory_web_service.py)、[admission_service](../../../backend/app/service/cloud_directory_admission_service.py)、[item_service](../../../backend/app/service/cloud_directory_item_service.py) | 受理、发送前、身份核对和结果提交分别复核；关闭写开关不丢响应 |
| [directory_worker_service](../../../backend/app/service/cloud_directory_worker_service.py)、[directory_worker](../../../backend/app/cloud_directory_worker.py) | 不接收endpoint覆盖；子进程/父进程退出必须由原worker能力和PID核对 |
| [directory_recovery_service](../../../backend/app/service/cloud_directory_recovery_service.py)、[recovery_worker_service](../../../backend/app/service/cloud_directory_recovery_worker_service.py)、[recovery_worker](../../../backend/app/cloud_directory_recovery_worker.py) | 原身份只读核对、独立恢复context及预算；关闭/停用允许受限核对，不允许改目标或越权 |
| [path_service](../../../backend/app/service/cloud_directory_path_service.py) | 配置失效与真实路径提交分开；本地提交保留恢复交接保护、原revision和别名/普通文件水位 |
| [ad_review_service](../../../backend/app/service/cloud_ad_review_service.py) | 复核只读本地缓存；导出不因拥有者相同绕过撤权 |
| [quarantine_plan_service](../../../backend/app/service/cloud_quarantine_plan_service.py)、[execution_service](../../../backend/app/service/cloud_quarantine_execution_service.py)、[item_service](../../../backend/app/service/cloud_quarantine_item_service.py) | C3计划/发送/冲突/提交同来源，不接受跨profile目标 |
| [quarantine_web_service](../../../backend/app/service/cloud_quarantine_web_service.py)、[worker_service](../../../backend/app/service/cloud_quarantine_worker_service.py)、[quarantine_worker](../../../backend/app/cloud_quarantine_worker.py) | 同C2上下文及worker能力传播；不保留Token队列 |
| [quarantine_recovery_service](../../../backend/app/service/cloud_quarantine_recovery_service.py) | 位置核对、commit和reverse沿原来源，新动作新requestId；旧未知事实不回填 |
| [directory_log_service](../../../backend/app/service/cloud_directory_log_service.py)、[quarantine_log_service](../../../backend/app/service/cloud_quarantine_log_service.py) | 内容授权与最小保护摘要分开，日志大小/过期提示沿原设置 |
| [recover_cloud_drive.py](../../../backend/app/recover_cloud_drive.py)及其调用者 | CLI也需原记录/context选择，不允许省略来源回退单例 |
| [cloudInventory.ts](../../../frontend/src/api/cloudInventory.ts)及其页面调用者 | 显式来源、原UUID、会话/选择代次；仅定义交互约束，MC5再做UI实现 |

所有底层cd2客户端只接收经过resolve后的内存快照，不能自己查询默认连接。stdin上限保持65536字节，不再传CA正文/可覆盖端点；apiToken只在匿名管道内，workerToken是本地任务能力，不是CD2 Token。新增诊断worker也必须纳入统一监督/回收，不能在请求线程无追踪地发网络。

## 6. 接口定义

### 6.1 管理与偏好

以下为目标API。JSON字段白名单、extra=forbid；UUID/修订不合法返回400，认证由既有依赖处理。所有返回带networkAccess:false，除显式诊断/执行；保存无网络。

| 方法与路径 | 请求字段 | 响应/权限 |
| --- | --- | --- |
| GET /api/settings/cloud-connections | page、pageSize、state、search | settings:write；items/total/page/pageSize，管理可见不代表可使用 |
| POST /api/settings/cloud-connections | requestId、name、endpoint、caCertificatePem | 201；id、state=disabled、metadataRevision/stateRevision=0、securityRevision=1 |
| GET /api/settings/cloud-connections/{id} | 无 | 名称/状态/公开PEM/端点及各修订；不含任何Token |
| PUT /api/settings/cloud-connections/{id}/metadata | name、expectedMetadataRevision | 当前展示快照；无变化不递增 |
| PUT /api/settings/cloud-connections/{id}/security | 原 endpoint、caCertificatePem、expectedSecurityRevision | 新安全修订或原修订；TLS 严格不可关闭。不同规范端点返回 409 `connection_endpoint_immutable`，须走新增连接并重新授权。 |
| PUT /api/settings/cloud-connections/{id}/state | state、expectedStateRevision | 状态CAS；archived使用此入口，无DELETE |
| GET/POST /api/settings/cloud-connections/{id}/directories | GET分页；POST requestId/name/approvedRoot/mappingKey/quarantineFolderName | 管理权限；新目录disabled，全部开关false |
| PUT /api/settings/cloud-connections/{id}/directories/{profileId}/metadata | name、expectedMetadataRevision | 固定parent，不原地变根或归属 |
| PUT /api/settings/cloud-connections/{id}/directories/{profileId}/policy | webExecutionEnabled/directoryRenameEnabled/quarantineEnabled/quarantineFolderName、expectedSecurityRevision | 动作政策CAS；禁止夹带approvedRoot/mappingKey |
| PUT /api/settings/cloud-connections/{id}/directories/{profileId}/state | state、expectedStateRevision | 目录生命周期CAS |
| GET/PUT /api/settings/cloud-connections/{id}/user-grants/{userId} | PUT allowed、directoryProfileIds、expectedGrantRevision | 一次原子替换；不存在授权聚合按revision=0，首写为1；allowed=false必须空目录集 |
| GET /api/cloud-drive/connection-choices | search、page、pageSize；可选connectionId取其目录 | 当前用户可使用的ID/名称/修订/状态/批准根；先授权过滤再分页/计数 |
| GET/PUT /api/cloud-drive/preferences | PUT connectionId、directoryProfileId、expectedRevision；允许null清空 | 仅本人，过期偏好返回unavailable不自动选其他连接 |
| GET/PUT /api/settings/cloud-drive-policy | PUT minIntervalSeconds/failureCooldownSeconds、expectedRevision | 原全局政策；settings:write；占用/未知期间只许暂停或提高保护，不放松 |

创建配置requestId也需幂等，但不要求媒体使用权或虚构task context：与创建和审计同事务写cloud_configuration_requests，不挪用cloud_request_registry绕过context外键。该登记属于配置命名空间，云业务命名空间仍跨盘点/诊断/C2/C3统一唯一；配置登记只返回原创建对象ID，不能绕过当前settings:write或读取秘密。

### 6.2 盘点与原任务

GET /api/cloud-drive/inventory-policy要求connectionId、directoryProfileId、selectedPath，返回固定id、revisions对象（含六个安全/状态/授权修订）、policyHash、limits、available/reasonCode、networkAccess:false。目录批准根本身可作为selectedPath，不能从请求默认补连接。

POST /api/cloud-drive/inventory-jobs请求保留现有requestId/selectedPath/policyHash/confirmRead，增加connectionId、directoryProfileId及expectedConnectionSecurityRevision/expectedDirectorySecurityRevision/expectedConnectionStateRevision/expectedDirectoryStateRevision/expectedGrantRevision/expectedDirectoryGrantRevision。201返回jobId、contextId、requestId、revision、source快照、limits、status和networkAccess:false；重复同意图200及相同ID。

POST /api/cloud-drive/inventory-jobs/{jobId}/start只接受expectedRevision、policyHash、apiToken、confirmRead。202不等于已完成或能重放；不接受新端点、来源ID或contextId覆盖。query/cancel/advice/keep/clear-cache及C2/C3旧动作路径保留，新增动作从原父记录派生新context。只有新建意图需要明确来源字段，原ID动作不改变对外路径。

响应中的source包含connectionId、directoryProfileId、创建时名称、当前名称、bound/legacy_unbound及授权允许显示的范围；撤权后的保护摘要不包含source路径/名称。

调用示例：以下完整函数注入项目已有apiClient（baseURL=/api、Bearer及会话代次拦截器），只描述目标接口，部署MC3后才能对真实服务调用；Token另在start确认中交接，不写localStorage。

```javascript
async function prepareCloudInventory(apiClient, connectionId, directoryProfileId, selectedPath) {
  const query = new URLSearchParams({ connectionId, directoryProfileId, selectedPath });
  const policy = (await apiClient.get(`/cloud-drive/inventory-policy?${query}`)).data;
  if (!policy.available) throw new Error(policy.reasonCode);
  const revisions = policy.revisions;
  const requestId = crypto.randomUUID();
  try {
    const response = await apiClient.post('/cloud-drive/inventory-jobs', {
      requestId, connectionId, directoryProfileId, selectedPath,
      expectedConnectionSecurityRevision: revisions.connectionSecurityRevision,
      expectedDirectorySecurityRevision: revisions.directorySecurityRevision,
      expectedConnectionStateRevision: revisions.connectionStateRevision,
      expectedDirectoryStateRevision: revisions.directoryStateRevision,
      expectedGrantRevision: revisions.grantRevision,
      expectedDirectoryGrantRevision: revisions.directoryGrantRevision,
      policyHash: policy.policyHash, confirmRead: true,
    });
    return { requestId, job: response.data };
  } catch (error) {
    const failure = new Error('prepare_result_unconfirmed');
    failure.requestId = requestId;
    failure.httpStatus = error?.response?.status ?? null;
    failure.sessionChanged = error?.name === 'SessionChangedError';
    throw failure;
  }
}
```

现有认证通过apiClient注入MediaAI Bearer，并非仅凭浏览器cookie；不能将apiClient改成不带认证的原生fetch。用户确认在调用前完成；网络异常保留requestId先查原请求，不自动再次调用本函数生成新意图。页面对sessionChanged不得再展示旧来源结果，按既有会话代次工具清理；异常不附原Axios对象，避免泄漏请求载荷。

### 6.3 显式诊断

GET /api/cloud-drive/connection-diagnostic-policy要求connectionId、directoryProfileId、selectedPath、stage=tls或token-runtime；只在本地返回固定来源修订、policyHash、阶段预算及available/reasonCode，不联网。该入口使用诊断政策，允许已授权的disabled来源；不能复用只允许enabled来源的inventory-policy，否则关闭盘点后无法进行明确诊断。

POST /api/cloud-drive/connection-diagnostics接受requestId、connectionId、directoryProfileId、selectedPath、与盘点相同的期望修订、上述policyHash、stage=tls或token-runtime、confirmNetwork=true；token-runtime另接apiToken。TLS模式拒绝apiToken字段，Token模式不接受主账号凭据或任意方法。权限使用scan:run加连接/目录授权，不能仅凭settings:write调用。显式诊断允许已授权的disabled连接/目录，不要求打开盘点或写开关；archived拒绝。诊断模式不能被用于盘点或执行，保护未决时也不例外。

诊断同时创建context、read session、请求登记和diagnostic记录，交给独立受监督worker；202返回id/requestId/contextId/revision及同源查询URL。GET /api/cloud-drive/connection-diagnostics/{id}及GET /api/cloud-drive/diagnostic-requests/{requestId}只读本地结果，POST /{id}/cancel只记停止后续。运行中页面默认1.5秒轮询，页面隐藏停止轮询并清未交接Token；不做100毫秒高频刷新。

| 预算/阶段 | 冻结值与含义 |
| --- | --- |
| 整轮 | 总30秒含等待、TLS及调用；应用RPC最多2；累计响应protobuf原始字节64KiB；每消息32KiB；单次发送16KiB；无重试，gRPC显式禁止内建重试 |
| TLS | 严格验证原端点、名称、有效期与链；占全局网络准入/worker槽但不伪记应用RPC；超时记录安全分类；无Token可完成本层 |
| Token | 仅GetApiTokenInfo；公共方法不附Bearer头，Token仅请求体；必须list，允许可选runtime，拒绝其他及未知权限；root须精确selectedPath |
| 精确版本 | 仅GetRuntimeInfo，携带本轮受限Token；无runtime权限标permission_missing，不补权限、不自动重试；同时核对Core及CLOUDAPI完整Build组合 |
| 管理调用 | 产品诊断为0；不登录/创建/撤销Token、不查询传输任务、不列目录；Token生命周期由用户在CD2管理 |
| 异常 | 明确响应的鉴权/格式失败与已发送结果未知分别记录；未决沿全局保护，不因只读或超时TTL自动删除 |

字节上限针对应用protobuf，不冒称包含TLS/HTTP2开销的网卡流量上限。TLS握手失败且可证明没有发出应用RPC时只记录网络失败；监督进程确认socket/worker回收后释放本地握手占用，不伪造一个云端未知RPC。发送前已经持久化sending而无法证明未发出的情形仍按未知保护，不通过异常名称推断未发送。

现有元数据inspectConnection只提取productVersion，不足以证明精确双版本；MC3必须复用目录客户端已验证的组合判断并补测试，不能把当前inspectConnection结果直接标为写能力已通过。诊断通过不是目录存在证明，不是正式媒体写授权。

### 6.4 C2/C3显式策略与固定来源动作

| 接口 | 必需输入 | 本地结果与边界 |
| --- | --- | --- |
| GET /api/cloud-drive/directory-policy | connectionId、directoryProfileId、selectedPath | 当前登录及目录计划权限、连接/目录授权；available、reason/reasonCode、policyHash、limits、固定来源ID和六项revisions；动作权限或开关不满足时显示不可执行 |
| GET /api/cloud-drive/quarantine/policy | 同上 | scan:run加cloud-quarantine:plan及来源授权；planAvailable、executionEnabled/canExecute/executionAvailable/executionReason、policyHash、targetName、limits与固定来源；没有执行权不阻断本人有权的本地计划政策读取 |
| POST /api/cloud-drive/quarantine-plans/{planId}/execute | requestId、expectedRevision、policyHash、confirmMaintenance、confirmGlobalFence、apiToken | 可信HTTPS；从原计划派生quarantine_move上下文和新run_id；首次202不表示远端成功，同意图200不再交接Token或启动worker |
| GET /api/cloud-drive/quarantine-runs/{runId}、GET /api/cloud-drive/quarantine-run-requests/{requestId} | 原ID或原UUID | 当前有效登录、原所有者、原动作读取权限和当前来源授权；仅读本地状态、原来源、预算、位置及日志可用性，不自动轮询CD2 |
| POST /api/cloud-drive/quarantine-runs/{runId}/cancel | expectedRevision | 当前读取动作与来源授权；仅记停止后续，不撤销远端请求、不释放未知保护 |
| POST /api/cloud-drive/quarantine-runs/{runId}/observe | 新requestId、原执行expectedRevision、confirmMaintenance、confirmRemoteIdle、apiToken | 可信HTTPS；新quarantine_observe上下文、新会话及只读预算；从原move派生，不接收来源覆盖；首次202、同意图查询200，不重放旧写 |
| POST /api/cloud-drive/quarantine-runs/{runId}/commit-local | expectedRevision、confirmLocalOnly | 当前登录及执行/来源授权；move须完整验证且无恢复交接，observe须最新有效当前位置证据。CAS、身份、路径/来源世代、证据新鲜度与审计同事务核对；仅推进本地水位 |
| POST /api/cloud-drive/quarantine-plans/{planId}/reverse | 新requestId、原计划expectedRevision | 从原move已提交位置派生quarantine_reverse上下文、新计划；仅反转已确认在目标处的条目，不自动执行；新执行仍需新UUID、确认及最小Token |
| POST /api/cloud-drive/quarantine-runs/{runId}/review-worker | expectedRevision、confirmClientsStopped、confirmRemoteIdle | 原所有者兼具settings:write、原动作和当前来源授权；核对原主机/父子进程，只归还PID，不清未知或原move冻结 |
| GET /api/operation-logs、GET /api/operation-logs/export | task_type=cloud_quarantine_run、task_id；读取可带after_id/limit | audit:read加原动作读取与当前来源授权，在同一只读事务内核权、读取/导出；已清理返回log_available=false及提示，不删除独立预算、请求、位置或审计 |

两个策略接口的policyHash是对应C2/C3业务政策摘要，不是盘点prepare使用的上下文政策摘要，不能互换。策略读取不探测TLS、版本或目录存在性，不生成Token、不消耗RPC；显示可用不替代执行时的预算、任务互斥和逐RPC核验。多连接模式缺少明确来源即拒绝；未迁移的单配置实例不能忽略传入的来源参数后使用旧默认值。

原生受理回执和完成结果使用不同的前端类型：回执含contextId、resourceId、resourceKind、原requestId、status/revision、replayed及固定source；首次交接后networkAccess可为null，不代表远端请求尚未发出或已成功。同意图返回原状态，不再次启动worker；页面丢失回执只能查原UUID，不能生成新UUID重发。前端[原生动作适配](../../../frontend/src/api/cloudBoundActions.ts)以原计划/动作source核对返回值，来源只用于客户端比对，不作为覆盖字段发送。

提交适配仅接受可信HTTPS、严格确认及合法临时Token；无论成功、失败或旧会话异常，均清空其交接载荷引用，不保存到偏好、URL、日志或请求队列。调用组件还须在提交时清空自己持有的表单和交接引用，并在隐藏/关闭/来源或会话变化时废止在途票据。清空JavaScript引用不是承诺运行时内存物理清零，也不能据此撤销已发请求。

C3移动预算为3+9N RPC、总180秒、1MiB；只读核对为6+2N RPC、总90秒、1MiB，N为1至5。核对Token仅允许list/runtime，不允许move及额外未知权限；Token经线程内存及匿名stdin交接，不进入任务队列、数据库、日志或响应。关闭写开关仍可在原端点/CA/根/映射身份未变时另行批准只读核对，不给旧worker恢复发送资格。

只读核对最多5轮。原动作及同一原动作下已退出、同主机、无槽的先前只读核对，其未知事实可由新一轮独立只读预算继续核对；须重新确认相关远端静止、满足冷却，并创建新上下文和UUID。旧未知不提前改写或释放，非只读方法、其他来源未知、活worker/租约和手动暂停仍阻断；不能把这一通道用于新诊断或新写。最终仅依据最新完整位置证据明确提交，关联原未知及先前只读未知，不声称已证明旧调用因果。

上下文/请求登记普通意图上限49000，directory_observe及quarantine_observe可使用总额50000内的恢复预留；预留耗尽仍拒绝，不删除证据腾位。反向计划不是只读恢复预算，不占用恢复预留；亲缘核对只读取固定记录和有界直接来源，不沿任意长反向链递归扫描。

调用示例使用已有apiClient，仅查询本地政策，不触发执行：

```javascript
async function getQuarantinePolicy(apiClient, connectionId, directoryProfileId, selectedPath) {
  const params = { connectionId, directoryProfileId, selectedPath };
  const response = await apiClient.get('/cloud-drive/quarantine/policy', { params });
  return response.data;
}
```

## 7. 错误与最小恢复

统一错误体为detail对象：reasonCode、requestId（已提供时）、networkAccess、operationState、queryUrl（仅本人有权）、retryAutomatically:false。reasonCode来自稳定白名单；不保存远端details、Token哈希/前后缀、请求体、异常repr或全量响应。

| HTTP | reasonCode | 行为 |
| --- | --- | --- |
| 400 | invalid_fields / invalid_scope / invalid_revision / parent_mismatch | 字段/根/父子不合法；无网络 |
| 401 | authentication_required | 当前认证失败，无任务内容 |
| 403 | action_permission_denied | 已知本人动作但权限不足；不暴露未授权来源 |
| 404 | resource_not_found | 他人、未授权连接/目录/任务或不存在统一口径 |
| 409 | revision_conflict / request_context_conflict / root_overlap / endpoint_duplicate / name_duplicate | 保留requestId；重新加载或核对原意图，无自动重试 |
| 409 | context_stale / source_disabled / legacy_unbound / worker_busy / global_protection_held / trust_repair_required | 不改目标、不清保护；给本人脱敏原因及允许的后续动作 |
| 429 | capacity_reached | 逻辑配额满，不设自动重试，不用新连接重置配额 |
| 503 | storage_unavailable / audit_write_failed | 未确认写成功；先查原requestId，不能猜失败后重放 |

已受理诊断及云动作的远端失败在本地资源状态中呈现，不伪造配置保存失败。token_root_mismatch、unknown_token_permissions、tls_name_mismatch、tls_expired、tls_untrusted、deadline_exceeded、runtime_unsupported分别展示，不只返回“连接失败”。

| 恢复状态 | 允许行为 | 禁止行为 |
| --- | --- | --- |
| active/sending | 原worker记录响应或未知；管理员暂停/用户请求停止后续 | 改地址/根/信任、硬删、假定取消已成功 |
| unknown_frozen | 本人查看脱敏状态；管理者核对旧PID/host/槽；保留原未知证据 | 新诊断/新写绕过未知，按TTL清理 |
| stopped_read_review | 确认旧进程退出、远端静止、冷却结束；受限撤销手动暂停意图后，另开原身份只读恢复预算 | 自动重试旧写、普通resume清未知槽、隐式扩大授权 |
| trust_blocked | 展示原信任材料摘要/有效期分类及证书维修提示；仅允许本地核对和取消后续 | 给旧写换目标/换CA、跳过TLS、把新连接诊断当旧请求已恢复 |
| positions_ready | 新只读观察与原条目ID/范围相符，原revision/路径世代仍匹配后明确本地提交 | 覆盖旧RPC为成功，未经提交直接释放全部保护 |
| committed | 记录新路径世代/核对关系，原请求保持事实；如需反向操作另建新计划 | 自动反向或沿用旧Token |

首版信任维修的明确限制：可在CD2服务端续签到原CA仍可验证、原端点仍匹配的有效证书，再由原拥有者单独批准只读核对；应用不据此断言云账号未变，仍按维护窗口和对象身份核对。原CA已失效、需要替换CA/地址、原用户无法重新获得授权或原身份缺证据时，保持trust_blocked，首版不提供跨用户代执行或信任覆盖。人工在外部确认状态不自动改变应用账本；将来增加强证据维修需独立设计/批准，不以“临时解锁”绕过。

管理者settings:write可看到保护类型、任务不透明ID、状态、安全错误码、冷却和进程核对结果；不含他人媒体路径/条目、端点正文、日志正文或导出。原拥有者失去媒体授权后仅可查本人requestId的这类摘要并请求停止后续；远端恢复前须重新授予原连接/目录及动作权限，新建恢复上下文，不能管理员冒用原用户。

### 7.1 最小任务保护与只读进程核对接口

最小查询、列表和停止覆盖inventory、diagnostic_tls、diagnostic_token_runtime，以及directory_execute、directory_observe、quarantine_move、quarantine_observe。有效会话仍必需；这些入口不恢复内容授权，不读取媒体计划/路径/端点/原生worker能力，不发送云请求。

| 接口 | 权限及行为 |
| --- | --- |
| GET /api/cloud-drive/intents/{requestId}/protection | 当前有效登录的原拥有者；无需恢复scan或来源授权，仅返回不透明请求ID、种类、状态、修订及保护槽是否占用，不返回目录内容 |
| POST /api/cloud-drive/intents/{requestId}/stop | 同上；停止后续幂等，保留原UUID、消费及未决，不代表已发请求已撤回 |
| GET /api/settings/cloud-drive-protections | settings:write；按页返回最小保护元数据和原进程证据，不授予他人内容访问 |
| POST /api/settings/cloud-drive-protections/{requestId}/stop | settings:write；可停止已禁用拥有者的后续任务，不代执行恢复或重放 |
| POST /api/settings/cloud-drive-protections/{requestId}/review-worker | settings:write；严格接收expectedRevision、confirmClientsStopped=true、confirmRemoteIdle=true；仅盘点/诊断可用，四类原生动作固定invalid_scope；不能将原生槽交给只读worker回收 |
| POST /api/cloud-drive/budget/review-read-lease | settings:write；严格接收leaseId、expectedRevision及上述两项确认；单独核对原只读发送者与池修订、冷却，不能清理原生写ledger |

孤儿worker核对以原owner_pid、owner_host、owner_instance及已交接child PID为证据。必须是原主机且父子进程均已退出；缺失、异地主机、PID仍存活或读取期间修订改变均拒绝，不能根据当前机器PID猜测原发送者。核对通过只清除worker能力/PID，保留原请求槽及未知，诊断进入review而不是成功；旧worker能力立即失效。未知只读RPC须另走独立租约核对，保留原方法、状态和消耗审计，池进入暂停/冷却，不自动恢复或重放。普通resume拒绝尚有worker、队列或未决的情形。

两个核对接口都只修改本地账本，无远端探测。确认远端静止属于运维前提，不以用户点击代替原进程退出证据；审计失败整事务回滚。请求载荷不得覆盖原PID/host/实例、端点、来源或Token。

停止POST只允许无正文或空对象，拒绝额外字段；原生停止仅写cancel_requested、修订和同事务审计，不清slot_held、未决RPC、消费或路径世代。重复停止不追加审计，事件为cloud_drive.protection，目标为原cloud_task_context。原生历史记录保留worker_token不等于活worker，摘要仅按实际worker_pid报告进程占用；安全原因不在白名单内时统一review_required。

```typescript
// apiClient已使用当前MediaAI会话；此调用不提交CD2 Token或连接覆盖。
const summary = await apiClient.get(`/cloud-drive/intents/${requestId}/protection`);
await apiClient.post(`/cloud-drive/intents/${requestId}/stop`, {});
```

### 7.2 历史只读未知的后续核对关系

目录只读核对的历史sending/unknown可在以下条件同时成立后不再计为未决保护：旧轮为review、无槽且无worker；方法仅GetApiTokenInfo、GetRuntimeInfo或FindFileByPath；同一原plan、actor、host和policy的较新轮已由用户明确committed，未取消且无槽/worker；原执行为review/positions_reconciled且无槽/worker。配置门禁和最小摘要共用同一关系判定。

该判定只确认新的位置证据已提交，不把旧RPC改成responded或成功，不撤销预算消耗、不推断旧远端调用的因果结果。ready、TTL、进程退出、换连接或新增授权均不能单独满足；其他计划、主机、所有者、非只读方法及无关全局未知仍阻断。新核对继续需要独立确认和原身份/当前权限校验，不自动发送或重试。

## 8. 容量与生命周期

| 项目 | 首版技术上限 |
| --- | --- |
| 连接 | 全实例64条含归档；每页默认20，最大100；名称1至80字符 |
| 目录 | 每连接128、全实例1024含归档；路径UTF-8最多4096字节；映射1至64个受限ASCII字符 |
| 版本 | 连接版本4096条、目录版本8192条全局上限；公开CA每版本16KiB；配置与版本逻辑载荷合计32MiB |
| 配置创建登记 | 最多1088条全局，与64连接/1024目录终身ID上限一致；与配置一并保留，不含Token |
| 授权 | 每用户连接最多64、目录最多1024；整实例授权行最多65536；一次替换一个用户一个连接，最多128目录 |
| 新上下文/请求登记 | 分别50000条全局上限；有引用/未知证据不清除；达到上限拒绝新意图，仍允许查询/取消和受限恢复 |
| 诊断 | 历史最多2000条全局；业务上下文预留1000个名额给受限恢复，普通新意图达到49000停止；诊断不挤占该预留 |
| 全局请求政策 | 间隔2至300秒、冷却60至86400秒，具体已保存值保持；不因多连接重设为最小值 |
| 既有观察/人工复核/日志 | 继续原全局条数/字节和设置保留策略，不按连接数倍增；日志已清理显示原有不可用状态 |

这些是防止无界增长的设计上限，不是2PB索引能力或吞吐实测。50000请求登记也为恢复保留1000个名额；恢复耗尽预留仍不能删未知记录腾位，应明确capacity_reached并进入人工维护。物理磁盘满/审计写失败时不得报告取消或设置已持久化成功。

首版不自动删除版本、上下文、请求登记及未知证据。已完成且无worker/未决的观察缓存仍按原显式清理流程；诊断历史达到配额先停止新诊断，后续若实现删除需独立生命周期设计，不以日志保留天数删除执行身份。用户禁用保留FK引用，不能物理删用户破坏原归属。

## 9. 源码事实与设计验证边界

当前源码事实：users/user_sessions表及直接权限在[database.py](../../../backend/app/core/database.py)和[auth_service](../../../backend/app/service/auth_service.py)；[paths](../../../backend/app/utils/cloud_drive_paths.py)按目录段校验；[元数据协议](../../../backend/app/service/cd2_metadata_protocol.py)包含RuntimeInfo双版本字段；[元数据客户端](../../../backend/app/service/cd2_metadata_client.py)的Token自检不附Bearer。未发送真实诊断请求，不新增远端协议能力保证。

本契约的DDL须在合成库核对：FK、循环插入事务、不可变版本、跨父目录授权拒绝、唯一请求、偏好父子关系、过期会话可清理而上下文编号保持、幂等/CAS及并发根检查。检查通过只证明文档结构/模型，不证明服务、worker、迁移或浏览器已实现；MC2以后逐阶段运行实际测试和全量门禁。

## 10. MC1实现前复核清单

- 配置创建登记使用cloud_configuration_requests，云意图使用cloud_request_registry；两个命名空间不能混用或以一方查询代替另一方授权。
- 旧/api/settings/cloud-drive GET仅返回source=legacy、migrationRequired=true、networkAccess=false及新入口，不再返回可执行配置；PUT固定409/multi_connection_required，无隐式单连接回退。全局预算的管理路径保持，先按本地全局政策解析，不要求选某个连接才能暂停。
- 用户没有选择目录配置时，不允许用该连接下“只有一个目录”推断；已有任务原ID查询不受新页面筛选改变。分页先授权过滤，choices不可用的旧偏好仅返回unavailable及不透明ID，不带未授权名称/根。
- schema号在实际实施时分配；旧表ALTER、legacy_unbound和旧UUID扫描必须通过离线副本迁移脚本验证，不能直接执行本文DDL覆盖正式库。
- 既有CLI、普通扫描/改名/回滚/待移动及probe调用者必须纳入运行回归，前述清单不是仅查字符串后即可勾选完成。
- 真实云试验、新部署、正式媒体写和发布需要各自范围授权，不沿用此前已消费的一次门禁。
