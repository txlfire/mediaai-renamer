# M5 共享协议接口实现计划

> 面向 AI 代理的工作者：实现此计划前先读取 `docs/development/m5/M5-NAS-SMB共享目录开发计划.md` 和 `docs/development/m5/M5-NAS-SMB共享目录验收清单.md`。步骤使用复选框语法跟进进度。

**目标：** 为 M5 建立统一共享协议接口，让本地路径、Windows UNC / SMB、Linux / NAS / Docker / NFS 已挂载路径通过接口接入，并为 WebDAV、FTP / SFTP、S3 保留后续候选协议能力说明。

**架构：** 后端新增 `shared_protocols` 服务包，扫描、目录浏览、连接测试和重命名前校验都通过协议注册表调用具体实现。前端从后端协议能力接口读取展示说明，媒体源页面显示连接协议和对应能力边界。

**技术栈：** FastAPI、SQLite schema migration、dataclass、Vue 3、Pinia、Element Plus、Vitest、unittest。

---

## 1. 计划文件结构

后端新增：

- `backend/app/service/shared_protocols/base.py`：协议接口、能力描述、连接测试结果、目录条目模型。
- `backend/app/service/shared_protocols/errors.py`：协议错误类型和中文提示映射。
- `backend/app/service/shared_protocols/local.py`：本地路径实现。
- `backend/app/service/shared_protocols/smb.py`：SMB / UNC 协议实现。
- `backend/app/service/shared_protocols/mounted_nfs.py`：Linux / NAS / Docker / NFS 已挂载路径实现。
- `backend/app/service/shared_protocols/registry.py`：根据 `path_type` 获取协议实现。
- `backend/app/service/media_source_secret.py`：媒体源自身 SMB 凭据加密、解密、脱敏状态；不建立全局凭据管理模块。
- `backend/app/api/shared_protocols.py`：协议能力查询接口。

后端修改：

- `backend/app/core/database.py`：媒体源字段迁移和 schema version 升级。
- `backend/app/schema/media.py`：媒体源共享字段、协议类型、能力模型。
- `backend/app/api/media_sources.py`：媒体源创建、编辑、连接测试、目录浏览接口接入协议注册表。
- `backend/app/service/media_source_service.py`：媒体源校验改走协议接口。
- `backend/app/service/scan_service.py`：扫描前校验和共享异常状态写入。
- `backend/app/service/rename_operation_service.py`：dry-run 和执行前共享路径校验。
- `backend/app/service/settings_service.py`：共享目录配置和协议说明配置项。

前端修改：

- `frontend/src/api/client.ts`：新增协议能力、连接测试、目录浏览请求。
- `frontend/src/locales/zh-CN.ts`：协议展示名、能力边界提示、后续候选提示。
- `frontend/src/views/MediaSourcesView.vue`：媒体源连接协议展示、动态表单、能力说明。
- `frontend/src/views/SettingsView.vue`：共享目录配置加入协议说明。
- `frontend/src/stores/mediaSources.ts`：保存协议字段和连接测试状态。

测试新增或修改：

- `backend/tests/test_shared_protocols.py`
- `backend/tests/test_media_source_secrets.py`
- `backend/tests/test_media_sources.py`
- `backend/tests/test_database_migrations.py`
- `backend/tests/test_scan_service.py`
- `backend/tests/test_rename_operations.py`
- `frontend/src/api/client.test.ts`
- `frontend/src/stores/mediaSources.test.ts`

## 2. 后端接口模型

### 2.1 协议能力模型

在 `backend/app/service/shared_protocols/base.py` 中定义：

```python
@dataclass(frozen=True)
class ProtocolCapabilities:
    protocol: str
    display_name: str
    supports_credentials: bool
    supports_directory_browse: bool
    supports_scan: bool
    supports_rename: bool
    requires_system_mount: bool
    can_verify_filesystem_type: bool
    future_candidate: bool
    user_notice: str
```

### 2.2 协议接口

```python
class SharedProtocol(Protocol):
    def capabilities(self) -> ProtocolCapabilities:
        ...

    def validate_config(self, source: MediaSourceDraft) -> None:
        ...

    def test_connection(self, source: MediaSourceDraft) -> ConnectionTestResult:
        ...

    def list_directories(self, source: MediaSourceDraft, path: str | None) -> DirectoryListing:
        ...

    def check_scan_ready(self, source: MediaSourceDraft) -> None:
        ...

    def check_rename_ready(self, source: MediaSourceDraft, source_path: str, target_path: str) -> None:
        ...

    def normalize_path(self, source: MediaSourceDraft) -> str:
        ...
```

## 3. 任务拆分

### 任务 1：协议能力接口和注册表

**文件：**

- 创建：`backend/app/service/shared_protocols/base.py`
- 创建：`backend/app/service/shared_protocols/registry.py`
- 创建：`backend/app/service/shared_protocols/errors.py`
- 测试：`backend/tests/test_shared_protocols.py`

- [x] 编写测试：注册表返回 `local`、`unc`、`mounted_nfs` 实现。
- [x] 编写测试：WebDAV、FTP / SFTP、S3 作为 future candidate 返回能力说明，但不返回可执行协议实现。
- [x] 实现 `ProtocolCapabilities`、结果模型和错误类型。
- [x] 实现协议注册表。
- [x] 运行后端协议测试。

### 任务 2：本地和已挂载路径协议实现

**文件：**

- 创建：`backend/app/service/shared_protocols/local.py`
- 创建：`backend/app/service/shared_protocols/mounted_nfs.py`
- 测试：`backend/tests/test_shared_protocols.py`

- [x] 编写测试：本地路径存在、可读、可写时连接测试成功。
- [x] 编写测试：路径不存在、不是目录、无权限时返回中文错误。
- [x] 编写测试：目录浏览只返回目录。
- [ ] 实现 `local` 和 `mounted_nfs` 协议。
- [ ] 运行后端协议测试。

### 任务 3：媒体源自身 SMB 凭据加密

**文件：**

- 创建：`backend/app/service/media_source_secret.py`
- 修改：`backend/app/core/config.py`
- 测试：`backend/tests/test_media_source_secrets.py`

- [x] 编写测试：明文凭据保存后数据库值不是明文。
- [x] 编写测试：API 脱敏状态不返回明文。
- [ ] 编写测试：覆写凭据后旧值不可读取。
- [x] 编写测试：SMB 账号密码仅写入媒体源自身加密字段，不写入全局配置或独立凭据表。
- [x] 实现媒体源自身加密字段的加密、解密、脱敏状态。
- [x] 确认不创建全局独立凭据管理入口或跨媒体源共享凭据库。
- [ ] 增加生产部署必须配置加密密钥的文档提示。
- [ ] 运行凭据测试。

### 任务 4：SMB 协议实现

**文件：**

- 创建：`backend/app/service/shared_protocols/smb.py`
- 测试：`backend/tests/test_shared_protocols.py`

- [ ] 编写测试：UNC 路径格式校验。
- [ ] 编写测试：凭据缺失时返回中文提示。
- [ ] 编写测试：连接测试不会在日志中输出明文凭据。
- [ ] 实现 SMB 协议能力说明、配置校验和路径可访问性检测。
- [ ] 运行 SMB 协议测试。

### 任务 5：mounted_nfs 与 NFS 可选信息实现

**文件：**

- 创建：`backend/app/service/shared_protocols/mounted_nfs.py`
- 测试：`backend/tests/test_shared_protocols.py`

- [x] 编写测试：`mounted_nfs` 类型不要求用户名密码。
- [ ] 编写测试：`mounted_nfs` 表单和接口不接受用户名、密码、域、令牌等敏感凭据。
- [ ] 编写测试：NFS Kerberos keytab、principal、realm 等配置不被支持。
- [x] 编写测试：`mounted_nfs` 媒体源保存后无敏感凭据字段，日志不输出密钥类信息。
- [x] 编写测试：本地挂载点不存在时返回中文提示。
- [ ] 编写测试：无法确认文件系统类型时降级提示，不在路径可访问时强制失败。
- [ ] 编写测试：Linux `/proc/mounts` 中识别 `nfs` / `nfs4`。
- [ ] 实现 `mounted_nfs` 配置校验、挂载点检测、NFS 可选信息保存和异常映射。
- [ ] 运行 NFS 协议测试。

### 任务 6：媒体源模型和数据库迁移

**文件：**

- 修改：`backend/app/core/database.py`
- 修改：`backend/app/schema/media.py`
- 修改：`backend/app/service/media_source_service.py`
- 测试：`backend/tests/test_database_migrations.py`
- 测试：`backend/tests/test_media_sources.py`

- [x] 编写迁移测试：旧 `media_sources` 表升级后默认 `path_type = local`。
- [x] 编写媒体源测试：SMB、`mounted_nfs` 字段保存和展示正常。
- [x] 编写测试：`mounted_nfs` 类型不保存 `username`、`encrypted_secret` 等敏感凭据字段。
- [x] 实现 schema migration。
- [ ] 媒体源保存校验改走协议接口。
- [ ] 运行媒体源和迁移测试。

### 任务 7：连接测试和目录浏览 API

**文件：**

- 修改：`backend/app/api/media_sources.py`
- 创建：`backend/app/api/shared_protocols.py`
- 测试：`backend/tests/test_m1_api.py`
- 测试：`backend/tests/test_shared_protocols.py`

- [x] 编写测试：协议能力接口返回支持协议和后续候选协议说明。
- [x] 编写测试：媒体源连接测试按 `path_type` 调用协议实现。
- [x] 编写测试：目录浏览只返回目录和权限状态。
- [x] 实现 API。
- [x] 运行 API 测试。

### 任务 8：扫描和重命名前校验接入

**文件：**

- 修改：`backend/app/service/scan_service.py`
- 修改：`backend/app/service/rename_operation_service.py`
- 测试：`backend/tests/test_scan_service.py`
- 测试：`backend/tests/test_rename_operations.py`

- [ ] 编写测试：扫描前协议校验失败时任务失败并保存中文原因。
- [ ] 编写测试：单文件权限失败时任务可进入 `partial_completed`。
- [ ] 编写测试：共享断开时任务进入 `connection_lost`。
- [ ] 编写测试：重命名前协议校验失败时 dry-run 阻断。
- [ ] 实现扫描和重命名前校验接入。
- [ ] 运行扫描和重命名测试。

### 任务 9：前端协议能力展示和动态表单

**文件：**

- 修改：`frontend/src/api/client.ts`
- 修改：`frontend/src/locales/zh-CN.ts`
- 修改：`frontend/src/views/MediaSourcesView.vue`
- 修改：`frontend/src/stores/mediaSources.ts`
- 测试：`frontend/src/api/client.test.ts`
- 测试：`frontend/src/stores/mediaSources.test.ts`

- [ ] 编写测试：协议能力接口请求和解析。
- [x] 编写测试：媒体源保存包含 `path_type` 和协议字段。
- [x] 编写测试：选择 `mounted_nfs` 类型时隐藏用户名、密码输入项。
- [x] 编写测试：仅选择 Windows UNC / SMB 类型时展示账号、密码输入框。
- [x] 实现协议类型展示列。
- [x] 实现动态表单和能力说明。
- [ ] 实现 WebDAV / FTP / SFTP / S3 后续候选提示。
- [ ] 运行前端测试。

### 任务 10：系统设置共享协议说明

**文件：**

- 修改：`backend/app/service/settings_service.py`
- 修改：`frontend/src/views/SettingsView.vue`
- 修改：`frontend/src/locales/zh-CN.ts`
- 测试：`backend/tests/test_settings_service.py`
- 测试：`frontend/src/api/client.test.ts`

- [ ] 编写测试：共享目录配置包含默认路径类型、连接测试超时、NFS 配置。
- [ ] 编写测试：系统设置页面展示协议能力说明。
- [ ] 实现后端配置项。
- [ ] 实现前端设置页展示。
- [ ] 运行设置测试。

### 任务 11：文档和回归验证

**文件：**

- 修改：`README.md`
- 修改：`docs/manuals/MediaAI-Renamer-用户手册.md`
- 创建：`docs/manuals/M5-user-manual-cn.md`
- 创建：`docs/design/M5-design-manual.md`
- 修改：`docs/work-logs/progress-<date>-m5.md`

- [ ] 更新 README 当前能力。
- [ ] 编写 M5 设计手册。
- [ ] 编写 M5 用户手册。
- [ ] 汇总当前完整用户手册。
- [ ] 记录 M5 工作日志和设计偏差检查。
- [ ] 运行后端测试、前端测试、前端构建和 `git diff --check`。


