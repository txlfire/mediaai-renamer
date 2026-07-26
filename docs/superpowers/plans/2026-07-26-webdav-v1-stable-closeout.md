# WebDAV v1.0.0 稳定版收口实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 完成 WebDAV 远程操作前端恢复入口、真实协议集成验证、M11 文档收口，并将项目发布为仅承诺已实现能力的 `v1.0.0` 稳定版。

**架构：** 保留现有 `RemoteMediaProvider`、远程操作锁和恢复状态机，在服务层新增脱敏分页查询；任务治理页通过独立 Tab 调用分页与恢复 API；容器化 WsgiDAV 只用于 HTTPS Basic 认证集成测试；发布阶段统一执行测试、构建、编码、打包、GitHub Release 和 GHCR 验证。

**技术栈：** Python 3.13、FastAPI、SQLite、Vue 3、TypeScript、Pinia、Element Plus、Vitest、Docker Compose、WsgiDAV、GitHub Actions。

---

## 文件结构

### 修改文件

- `backend/app/api/remote_operations.py`：增加远程操作分页查询接口和参数校验。
- `backend/app/service/remote_operation_service.py`：增加分页 DTO、筛选 SQL 和媒体源名称关联。
- `backend/app/service/shared_protocols/webdav.py`：清理开发阶段过时能力提示。
- `backend/tests/test_remote_operation_recovery.py`：补充列表 API、权限和恢复后刷新行为测试。
- `backend/tests/test_remote_operation_service.py`：补充分页、筛选、排序和脱敏服务测试。
- `backend/tests/test_shared_protocols.py`：校验 WebDAV 稳定版能力提示。
- `frontend/src/api/client.ts`：增加远程操作分页和恢复类型及调用方法。
- `frontend/src/api/client.test.ts`：覆盖查询参数、响应映射和恢复请求。
- `frontend/src/components/TablePagination.vue`：仅在发现服务端分页边界缺陷时做兼容修正。
- `frontend/src/stores/pagination.ts`：增加 `webdav-recovery` 独立分页键。
- `frontend/src/views/TaskGovernanceView.vue`：增加任务列表/WebDAV 恢复 Tab、筛选、表格、确认和行级加载。
- `frontend/src/locales/zh-CN.ts`：增加恢复页文案并修正 WebDAV 已支持能力说明。
- `.gitignore`：忽略集成测试临时证书、测试数据和日志。
- `package.json`：增加 WebDAV 集成测试命令，最终升级到 `1.0.0`。
- `frontend/package.json`：最终升级到 `1.0.0`。
- `backend/app/core/config.py`：最终升级到 `1.0.0`。
- `config/config.example.toml`：最终升级示例配置版本。
- `.github/workflows/docker-ghcr.yml`：确认稳定标签和 `latest` 镜像发布规则，必要时补充发布依赖。
- `README.md`：改为 `v1.0.0` 稳定版能力和维护路线。
- `docs/development/m11/M11-远程协议扩展开发计划.md`：改为 WebDAV-only 完成状态。
- `docs/design/project-design.md`：同步稳定版能力边界。
- `docs/design/MediaAI-Renamer-总设计文档.md`：同步产品完成状态和维护路线。
- `docs/manuals/MediaAI-Renamer-用户手册.md`：补充 WebDAV 配置、恢复和限制。

### 新增文件

- `backend/tests/integration/test_webdav_e2e.py`：真实 HTTPS WebDAV 协议端到端测试。
- `tests/integration/webdav/Dockerfile`：受控 WsgiDAV 测试镜像。
- `tests/integration/webdav/entrypoint.sh`：生成临时 CA/服务端证书并启动 HTTPS WebDAV。
- `tests/integration/webdav/docker-compose.yml`：隔离测试服务、端口、证书和媒体卷。
- `scripts/test-webdav-integration.ps1`：Windows 一键启动、等待、测试和清理脚本。
- `scripts/test-webdav-integration.sh`：Linux/CI 一键启动、等待、测试和清理脚本。
- `.github/workflows/webdav-integration.yml`：受控环境协议集成验证工作流。
- `docs/design/M11-WebDAV设计手册.md`：稳定版 WebDAV 架构和安全边界。
- `docs/development/m11/M11-WebDAV验收清单.md`：自动化与人工验收项。
- `docs/development/m11/M11-WebDAV验收报告.md`：记录实际执行结果和证据。
- `docs/manuals/M11-WebDAV用户手册.md`：用户配置、扫描、重命名、回滚和恢复说明。
- `docs/deployment/webdav.md`：证书、反向代理、权限和兼容性部署说明。
- `docs/releases/v1.0.0.md`：正式发布说明。
- `docs/work-logs/progress-2026-07-26-v1.0.0-release.md`：稳定版收口和发布证据。

## 任务 1：远程操作分页服务

**文件：**

- 修改：`backend/app/service/remote_operation_service.py`
- 修改：`backend/tests/test_remote_operation_service.py`

- [x] **步骤 1：先写分页和筛选失败测试**

新增三个测试：

```python
def test_list_remote_operation_items_returns_newest_first_with_source_name(self):
    ...
    page = list_remote_operation_items(settings, page=1, page_size=2)
    self.assertEqual(3, page.total)
    self.assertEqual([third.id, second.id], [item.id for item in page.items])
    self.assertEqual("media", page.items[0].media_source_name)

def test_list_remote_operation_items_filters_source_type_and_status(self):
    ...
    page = list_remote_operation_items(
        settings,
        media_source_id=source_id,
        operation_type="rollback",
        status="failed",
    )
    self.assertEqual(1, page.total)

def test_list_remote_operation_items_clamps_page_size(self):
    page = list_remote_operation_items(settings, page=0, page_size=1000)
    self.assertEqual(1, page.page)
    self.assertEqual(100, page.page_size)
```

- [x] **步骤 2：运行测试，确认失败**

运行：

```powershell
$env:PYTHONPATH="backend"
.\.venv\Scripts\python.exe -m unittest backend.tests.test_remote_operation_service -v
```

预期：因 `list_remote_operation_items` 和分页 DTO 尚不存在而失败。

- [x] **步骤 3：实现服务层分页 DTO 和参数化 SQL**

增加只读返回类型：

```python
@dataclass(frozen=True)
class RemoteOperationListItem(RemoteOperationItem):
    media_source_name: str | None


@dataclass(frozen=True)
class RemoteOperationPage:
    items: list[RemoteOperationListItem]
    total: int
    page: int
    page_size: int
```

实现要求：

- 页码最小为 1，页大小限制为 1 至 100。
- 使用参数化 `WHERE` 条件，不拼接用户输入值。
- `LEFT JOIN media_sources` 只读取媒体源名称。
- 固定按 `remote_operation_items.updated_at DESC, id DESC` 排序。
- 返回字段不得包含认证信息、密钥、密码或连接上下文。

- [x] **步骤 4：运行目标测试**

```powershell
$env:PYTHONPATH="backend"
.\.venv\Scripts\python.exe -m unittest backend.tests.test_remote_operation_service -v
```

预期：全部通过。

- [x] **步骤 5：提交**

```powershell
git add backend/app/service/remote_operation_service.py backend/tests/test_remote_operation_service.py
git commit -m "feat(m11): 增加远程操作分页查询服务"
```

## 任务 2：远程操作分页 API

**文件：**

- 修改：`backend/app/api/remote_operations.py`
- 修改：`backend/tests/test_remote_operation_recovery.py`

- [x] **步骤 1：先写 API 失败测试**

覆盖：

- `GET /api/remote-operations?page=1&page_size=10` 返回分页结构。
- `media_source_id`、`operation_type`、`status` 筛选透传。
- `page_size=101` 返回 `422`。
- 未认证请求按现有鉴权模式被拒绝。
- 列表响应不出现 `password`、`token`、`encrypted_secret`。

- [x] **步骤 2：运行测试，确认失败**

```powershell
$env:PYTHONPATH="backend"
.\.venv\Scripts\python.exe -m unittest backend.tests.test_remote_operation_recovery -v
```

预期：`GET /api/remote-operations` 当前被 `/{item_id}` 路由处理或不存在，测试失败。

- [x] **步骤 3：实现静态列表路由**

在 `/{item_id}` 之前注册：

```python
@router.get("")
def list_remote_operations_api(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    media_source_id: int | None = Query(None, ge=1),
    operation_type: Literal["rename", "rollback"] | None = None,
    status: Literal["pending", "recovering", "completed", "failed", "recovery_required"] | None = None,
    _current_user=Depends(require_authenticated_user()),
):
    return list_remote_operation_items(...)
```

- [x] **步骤 4：运行目标测试和全量后端测试**

```powershell
$env:PYTHONPATH="backend"
.\.venv\Scripts\python.exe -m unittest backend.tests.test_remote_operation_recovery -v
npm.cmd run backend:test
```

预期：目标测试和全量后端测试通过。

- [x] **步骤 5：提交**

```powershell
git add backend/app/api/remote_operations.py backend/tests/test_remote_operation_recovery.py
git commit -m "feat(m11): 暴露远程操作分页查询接口"
```

## 任务 3：前端 API、分页状态和纯逻辑

**文件：**

- 修改：`frontend/src/api/client.ts`
- 修改：`frontend/src/api/client.test.ts`
- 修改：`frontend/src/stores/pagination.ts`

- [x] **步骤 1：先写客户端失败测试**

测试查询调用：

```typescript
await fetchRemoteOperations(
  { page: 2, page_size: 50, status: "failed" },
  fakeHttpClient,
);
expect(requestUrl).toContain("/remote-operations?");
expect(requestUrl).toContain("page=2");
expect(requestUrl).toContain("status=failed");
```

测试恢复调用：

```typescript
await recoverRemoteOperation(9, fakeHttpClient);
expect(postUrl).toBe("/remote-operations/9/recover");
```

- [x] **步骤 2：运行测试，确认失败**

```powershell
npm.cmd run frontend:test -- --run frontend/src/api/client.test.ts
```

预期：新类型和函数尚不存在而失败。

- [x] **步骤 3：实现类型和 API**

新增：

```typescript
export type RemoteOperationStatus =
  | "pending"
  | "recovering"
  | "completed"
  | "failed"
  | "recovery_required";

export type RemoteOperationPage = {
  items: RemoteOperationItem[];
  total: number;
  page: number;
  page_size: number;
};
```

`fetchRemoteOperations` 过滤 `undefined` 和空字符串后使用 `URLSearchParams`；`recoverRemoteOperation` 复用现有错误映射。

在 `PaginationKey` 中加入 `"webdav-recovery"`，保证任务列表和恢复列表页码互不影响。

- [x] **步骤 4：运行前端目标测试**

```powershell
npm.cmd run frontend:test -- --run frontend/src/api/client.test.ts frontend/src/stores/pagination.test.ts
```

预期：全部通过。

- [x] **步骤 5：提交**

```powershell
git add frontend/src/api/client.ts frontend/src/api/client.test.ts frontend/src/stores/pagination.ts
git commit -m "feat(m11): 增加 WebDAV 恢复前端接口"
```

## 任务 4：任务治理 WebDAV 恢复 Tab

**文件：**

- 修改：`frontend/src/views/TaskGovernanceView.vue`
- 修改：`frontend/src/locales/zh-CN.ts`
- 条件修改：`frontend/src/components/TablePagination.vue`

- [x] **步骤 1：增加独立状态和加载函数**

实现：

- `activeTab` 默认 `tasks`。
- `remoteOperations`、`remoteTotal`、`remoteLoading`。
- `remoteFilters`：媒体源、操作类型、状态。
- `recoveringIds` 使用 `Set<number>` 的响应式副本，形成行级操作锁。
- 切换到 WebDAV Tab 时首次加载，返回任务 Tab 不重复刷新任务表。

- [x] **步骤 2：增加 Tab、筛选和表格**

WebDAV 表格列严格按设计：

- 操作 ID。
- 媒体源。
- 操作类型。
- 源路径。
- 目标路径。
- 状态。
- 失败原因。
- 更新时间。
- 操作。

路径和错误信息统一使用 `TextCell`，保持单行省略和 Tooltip。恢复列表使用：

```vue
<TablePagination
  pagination-key="webdav-recovery"
  :total="remoteTotal"
  :pager-count="3"
  server
  @page-change="loadRemoteOperations"
  @page-size-change="loadRemoteOperations"
/>
```

- [x] **步骤 3：实现确认、恢复和结果反馈**

确认文案必须明确：

> 系统将检查 WebDAV 源路径和目标路径，并在状态明确时重试 MOVE 或补齐数据库。状态冲突时不会自动修改远端文件。是否继续？

成功反馈区分：

- `retried`：WebDAV MOVE 已安全重试并完成恢复。
- `reconciled`：远端文件已移动，数据库状态已补齐。
- `already_completed`：该操作已完成，无需重复恢复。

`409` 显示后端锁冲突或人工处理原因。无论成功或可恢复失败，完成后保持当前筛选和页码刷新。

- [x] **步骤 4：修正稳定版 WebDAV 文案**

把“暂不支持真实重命名”改为“支持连接测试、目录浏览、递归扫描、命名预览、真实重命名、回滚和失败恢复”；不宣传未实现认证方式或协议。

- [x] **步骤 5：运行测试和构建**

```powershell
npm.cmd run frontend:test
npm.cmd run frontend:build
```

预期：测试和类型构建全部通过。

- [ ] **步骤 6：手动验证**

在开发服务中验证：

- 任务列表原有筛选、归档、恢复、日志入口不变。
- WebDAV Tab 初次进入无额外页面滚动条。
- 两个 Tab 分页状态独立。
- 恢复确认、行加载、成功提示和 `409` 提示正确。
- 页面缩放 150% 时表格可横向滚动，分页完整显示。

- [x] **步骤 7：提交**

```powershell
git add frontend/src/views/TaskGovernanceView.vue frontend/src/locales/zh-CN.ts frontend/src/components/TablePagination.vue
git commit -m "feat(m11): 增加任务治理 WebDAV 恢复入口"
```

仅在 `TablePagination.vue` 实际修改时暂存该文件。

## 任务 5：WebDAV 能力说明回归

**文件：**

- 修改：`backend/app/service/shared_protocols/webdav.py`
- 修改：`backend/tests/test_shared_protocols.py`

- [x] **步骤 1：写失败测试**

断言 WebDAV 能力说明包含扫描、真实重命名、回滚和失败恢复，不包含“暂不支持真实重命名”。

- [x] **步骤 2：运行并确认失败**

```powershell
$env:PYTHONPATH="backend"
.\.venv\Scripts\python.exe -m unittest backend.tests.test_shared_protocols -v
```

- [x] **步骤 3：修正文案并运行测试**

```powershell
$env:PYTHONPATH="backend"
.\.venv\Scripts\python.exe -m unittest backend.tests.test_shared_protocols -v
npm.cmd run backend:test
```

- [x] **步骤 4：提交**

```powershell
git add backend/app/service/shared_protocols/webdav.py backend/tests/test_shared_protocols.py
git commit -m "fix(m11): 同步 WebDAV 稳定版能力说明"
```

## 任务 6：容器化 WebDAV 集成环境

**文件：**

- 新增：`tests/integration/webdav/Dockerfile`
- 新增：`tests/integration/webdav/entrypoint.sh`
- 新增：`tests/integration/webdav/docker-compose.yml`
- 新增：`backend/tests/integration/test_webdav_e2e.py`
- 新增：`scripts/test-webdav-integration.ps1`
- 新增：`scripts/test-webdav-integration.sh`
- 修改：`.gitignore`
- 修改：`package.json`

- [ ] **步骤 1：创建隔离 HTTPS WebDAV 服务**

容器要求：

- 基于固定版本 Python Alpine 镜像。
- 固定 WsgiDAV 和 Cheroot 版本。
- 启动时生成临时测试 CA 和带 `localhost`、`127.0.0.1` SAN 的服务端证书。
- 仅使用测试账号和临时卷，不读取项目 `config.toml`、数据库或真实媒体目录。
- 对外暴露 `9443`，仅用于测试。

- [ ] **步骤 2：先写真实协议测试**

`test_webdav_e2e.py` 由 `MEDIAAI_WEBDAV_INTEGRATION=1` 显式启用，覆盖：

1. HTTPS Basic 正确凭据连接成功。
2. 错误密码失败。
3. 目录浏览。
4. 递归扫描与 ETag。
5. MOVE dry-run 目标冲突。
6. 真实 MOVE 且禁止覆盖。
7. 反向 MOVE 回滚。
8. 远端已移动时恢复只补数据库。
9. 源目标同时存在时进入 `recovery_required`。
10. 活跃写锁返回冲突。

- [ ] **步骤 3：实现 Windows/Linux 编排脚本**

脚本必须：

- 检查 `docker` 和 `docker compose`。
- 清理上一次临时目录。
- `docker compose up -d --build`。
- 等待 HTTPS 服务可用。
- 将临时 CA 路径传给测试进程，保持证书校验开启。
- 执行目标测试。
- 在 `finally`/`trap` 中 `docker compose down -v`。
- 不打印密码、Token 或私钥内容。

- [ ] **步骤 4：增加 npm 命令和忽略项**

```json
"webdav:test": "powershell -ExecutionPolicy Bypass -File scripts/test-webdav-integration.ps1",
"webdav:test:linux": "bash scripts/test-webdav-integration.sh"
```

忽略 `tests/integration/webdav/.tmp/`。

- [ ] **步骤 5：本地静态验证**

本机无 Docker 时运行：

```powershell
docker --version
git diff --check
npm.cmd run check:encoding
```

预期：明确记录 Docker 不可用；编码和差异检查通过。不得把静态检查表述为真实协议测试通过。

- [ ] **步骤 6：提交**

```powershell
git add .gitignore package.json backend/tests/integration/test_webdav_e2e.py tests/integration/webdav scripts/test-webdav-integration.ps1 scripts/test-webdav-integration.sh
git commit -m "test(m11): 增加 WebDAV HTTPS 集成环境"
```

## 任务 7：GitHub Actions 集成验证

**文件：**

- 新增：`.github/workflows/webdav-integration.yml`
- 条件修改：`.github/workflows/docker-ghcr.yml`

- [ ] **步骤 1：新增可重复执行工作流**

触发条件：

- `pull_request` 修改 WebDAV、远程操作、集成脚本或工作流文件。
- `push` 到 `develop` 和 `main`。
- `workflow_dispatch`。

工作流步骤：

```yaml
- uses: actions/checkout@v4
- uses: actions/setup-python@v5
  with:
    python-version: "3.13"
- run: python -m pip install -r backend/requirements.txt
- run: bash scripts/test-webdav-integration.sh
```

失败时上传 Compose 日志，但脚本必须先脱敏，日志中不得出现测试密码或证书私钥。

- [ ] **步骤 2：检查 GHCR 稳定标签规则**

确认 `v1.0.0` 标签产生：

- `ghcr.io/txlfire/mediaai-renamer:1.0.0`
- `ghcr.io/txlfire/mediaai-renamer:1.0`
- `ghcr.io/txlfire/mediaai-renamer:1`
- `ghcr.io/txlfire/mediaai-renamer:latest`

若现有 `docker/metadata-action` 已满足，不做无关修改。

- [ ] **步骤 3：静态检查工作流**

```powershell
git diff --check
npm.cmd run check:encoding
```

- [ ] **步骤 4：提交并推送 develop**

```powershell
git add .github/workflows/webdav-integration.yml .github/workflows/docker-ghcr.yml
git commit -m "ci(m11): 增加 WebDAV 协议集成验证"
git push origin develop
```

仅在 GHCR 工作流实际修改时暂存该文件。

- [ ] **步骤 5：等待并核对 CI**

```powershell
gh run list --workflow webdav-integration.yml --branch develop --limit 3
gh run watch <run-id> --exit-status
```

预期：真实 WebDAV 十个场景全部通过。失败时修复后重新执行，未通过不得进入稳定版发布。

## 任务 8：M11 和用户文档收口

**文件：**

- 修改：`README.md`
- 修改：`docs/development/m11/M11-远程协议扩展开发计划.md`
- 修改：`docs/design/project-design.md`
- 修改：`docs/design/MediaAI-Renamer-总设计文档.md`
- 修改：`docs/manuals/MediaAI-Renamer-用户手册.md`
- 新增：`docs/design/M11-WebDAV设计手册.md`
- 新增：`docs/development/m11/M11-WebDAV验收清单.md`
- 新增：`docs/development/m11/M11-WebDAV验收报告.md`
- 新增：`docs/manuals/M11-WebDAV用户手册.md`
- 新增：`docs/deployment/webdav.md`

- [ ] **步骤 1：改写 M11 范围**

明确：

- 已支持本地、UNC/SMB、已挂载 NFS、HTTPS WebDAV。
- WebDAV 支持无认证、Basic、Bearer。
- WebDAV 支持连接、浏览、扫描、预览、真实重命名、回滚和失败恢复。
- 不支持 HTTP、跳过 TLS 校验、Digest 和不受信任证书。
- FTP/FTPS/SFTP/S3/MinIO 仅是未来候选能力，无承诺排期。

- [ ] **步骤 2：补齐设计、部署和用户手册**

部署说明必须包含：

- URL 和根路径填写规则。
- Basic/Bearer 最小权限建议。
- CA 信任链配置。
- 反向代理 `PROPFIND`、`MOVE`、`Destination`、`Depth`、`If-Match` 头要求。
- 目标覆盖禁止策略。
- 恢复状态和人工处理边界。

- [ ] **步骤 3：填写验收清单和报告**

报告区分：

- 本机已验证。
- GitHub Actions 已验证。
- fnOS/NAS 人工验证。
- 未验证或不适用。

每项附命令、时间、结果和证据链接；不得用“计划通过”代替实际结果。

- [ ] **步骤 4：文档一致性检查**

```powershell
rg -n "M11-2|M11-3|下一阶段.*SFTP|下一阶段.*S3|暂不支持真实重命名|Digest" README.md docs
npm.cmd run check:encoding
git diff --check
```

预期：只在历史归档或“未来候选能力/明确不支持”上下文中出现未实现协议。

- [ ] **步骤 5：提交**

```powershell
git add README.md docs
git commit -m "docs(m11): 完成 WebDAV 稳定版文档收口"
```

## 任务 9：升级 `1.0.0` 并生成发布材料

**文件：**

- 修改：`package.json`
- 修改：`frontend/package.json`
- 修改：`backend/app/core/config.py`
- 修改：`config/config.example.toml`
- 新增：`docs/releases/v1.0.0.md`
- 新增：`docs/work-logs/progress-2026-07-26-v1.0.0-release.md`
- 修改：`README.md`

- [ ] **步骤 1：统一版本号**

将运行时、前端、根包和示例配置版本统一为 `1.0.0`，并搜索遗漏：

```powershell
rg -n "0\.11\.11|0\.10\.7" package.json frontend/package.json backend/app/core/config.py config README.md docs
```

- [ ] **步骤 2：编写发布说明**

发布说明必须包含：

- 稳定版核心功能。
- WebDAV 支持边界。
- 从 `v0.10.7` 升级方式。
- 数据库自动迁移和备份建议。
- 已知限制。
- 后续 `1.0.x` 维护策略。

- [ ] **步骤 3：提交版本升级**

```powershell
git add package.json frontend/package.json backend/app/core/config.py config/config.example.toml README.md docs/releases/v1.0.0.md docs/work-logs/progress-2026-07-26-v1.0.0-release.md
git commit -m "release: 准备 v1.0.0 稳定版"
```

## 任务 10：全量验证、打包和正式发布

**文件：**

- 更新：`docs/development/m11/M11-WebDAV验收报告.md`
- 更新：`docs/work-logs/progress-2026-07-26-v1.0.0-release.md`

- [ ] **步骤 1：运行本机发布门槛**

```powershell
npm.cmd run backend:test
npm.cmd run frontend:test
npm.cmd run frontend:build
npm.cmd run check:encoding
git diff --check
npm.cmd run release:package
```

预期：全部退出码为 0。

- [ ] **步骤 2：检查发布包内容**

确认：

- 包含 `config/config.example.toml`。
- 不包含 `config.toml`。
- 不包含 SQLite 数据库、日志、临时证书、集成测试密码或真实媒体文件。
- 记录文件名、大小和 SHA-256。

- [ ] **步骤 3：验证 Compose**

在有 Docker 的 GitHub Actions 或 fnOS 环境执行：

```bash
docker compose config
docker compose -f docker-compose.ghcr.yml config
```

预期：两份配置解析成功。

- [ ] **步骤 4：补录验收证据并提交**

```powershell
git add docs/development/m11/M11-WebDAV验收报告.md docs/work-logs/progress-2026-07-26-v1.0.0-release.md
git commit -m "docs: 记录 v1.0.0 验收结果"
git push origin develop
```

- [ ] **步骤 5：合并主线并打标签**

先确认 `develop` CI 全绿，再执行：

```powershell
git switch main
git pull --ff-only origin main
git merge --no-ff develop -m "release: 发布 v1.0.0"
git tag -a v1.0.0 -m "MediaAI Renamer v1.0.0"
git push origin main
git push origin v1.0.0
```

- [ ] **步骤 6：创建 GitHub Release**

```powershell
gh release create v1.0.0 releases/mediaai-renamer-frontend-v1.0.0.zip --title "MediaAI Renamer v1.0.0" --notes-file docs/releases/v1.0.0.md
```

- [ ] **步骤 7：等待镜像发布并核对**

```powershell
gh run list --workflow docker-ghcr.yml --branch v1.0.0 --limit 3
gh run watch <run-id> --exit-status
gh release view v1.0.0
```

核对 GitHub Release 资产和 GHCR 标签均存在。任何工作流失败必须修复并重新发布，不得把排队或运行中状态记录为成功。

- [ ] **步骤 8：切回维护分支**

```powershell
git switch develop
git pull --ff-only origin develop
```

后续版本策略：

- `1.0.x`：问题修复、安全和兼容性。
- `1.1.x`：体验、性能和可观测性优化。
- FTP/FTPS/SFTP/S3/MinIO：仅保留候选说明，不建立实现里程碑。

## 计划自检

- [ ] 与已确认设计一致，只实现 WebDAV，不实现 FTP、FTPS、SFTP、S3 或 MinIO。
- [ ] 不支持 HTTP WebDAV、TLS 绕过、Digest 或不受信任证书。
- [ ] 远程恢复入口与任务归档语义完全分离。
- [ ] 所有写操作继续经过权限、确认、远程写锁、幂等和审计。
- [ ] API 和前端不返回或显示密码、Token、密文和私钥。
- [ ] 集成测试不接触用户媒体目录和生产凭据。
- [ ] 发布包只携带示例配置，不携带正式配置和运行数据。
- [ ] 只有真实 WebDAV CI、全量测试、构建、编码、打包和发布检查全部通过后，才宣告 `v1.0.0` 完成。
