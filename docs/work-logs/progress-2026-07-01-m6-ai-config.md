# 2026-07-01 M6-1 AI 热配置启动记录

## 本次变更

- 系统设置分类名称调整：
  - `TMDB 元数据刮削` 改为 `刮削设置`。
  - `外部提交保护` 改为 `敏感词设置`。
  - `系统运维配置` 改为 `通用设置`，并固定在系统设置分类最下方。
- 启动 M6-1 AI 热配置：
  - 后端新增 `ai.enabled`、`ai.provider`、`ai.model`、`ai.api_key`、`ai.base_url`、`ai.timeout_ms`、`ai.max_retries` 热配置。
  - `ai.api_key` 按敏感字段脱敏展示，只支持覆写，不回显明文。
  - 前端新增 `AI 智能解析` 设置分类，位于 `通用设置` 上方。

## 验证

- `npm.cmd run backend:test` 通过，131 个测试。
- `npm.cmd run frontend:test` 通过，11 个测试文件 / 55 个测试。
- `npm.cmd run frontend:build` 通过，仅保留既有 chunk 体积提示。
- `npm.cmd run check:encoding` 通过。

## 后续

- M6-1 后续继续实现 AI 连接测试。
- M6-2 开始前需要补充统一 AI Provider 接口与 DeepSeek Provider mock 测试。

## M6 元数据回填修正

- 修正 TMDB 候选选择回填逻辑：回填前读取当前生效命名构建器模板，按模板变量合并候选字段和本地解析字段。
- 电影回填同样使用模板变量合并逻辑；当电影模板包含年份且候选缺少年份时，保留本地解析出的年份。
- 当电视剧候选只返回剧名和年份、不返回季号/集号时，如果当前剧集模板需要季集字段，保留本地解析出的季号和集号，避免 `SxxExx` 被清空。
- 回填后的目标文件名统一通过当前命名构建器重新生成，保持与系统设置中的命名结构一致。
- 元数据状态和匹配度增加原因展示，低置信结果提示需要人工确认。
- 重命名预览默认排序调整为稳定 ID 顺序，避免刷新后因 `updated_at` 变化导致手动编辑或回填记录跳转到其他页。

## 本轮验证

- `npm.cmd run backend:test` 通过，132 个测试通过。
- `npm.cmd run frontend:test` 通过，11 个测试文件、55 个测试通过。
- `npm.cmd run frontend:build` 通过，保留既有 Vite chunk 体积提示。
- `npm.cmd run check:encoding` 通过。

## 2026-07-02 v0.5.4 小阶段收口

- 完成 M6-1 AI 连接测试闭环：
  - 新增 `/api/settings/ai/test` 与 `/api/settings/ai/test-result`。
  - AI 测试结果复用 `page_test_results` 持久化。
  - AI API Key 测试快照只记录配置状态和指纹，不回显明文。
  - 前端 AI 设置页增加测试按钮、动态状态栏、历史详情弹窗。
- 启动 M6-2 Provider 基础：
  - 新增统一 `AiProvider` 抽象。
  - 新增 `DeepSeekProvider`，使用 OpenAI-compatible `/chat/completions` 测试连接。
  - 新增 DeepSeek Provider mock 测试，覆盖成功和鉴权失败。
- 设置页连接状态栏时间统一为完整格式 `yyyy-mm-dd hh:mm:ss`。
- 版本号升级到 `0.5.4`，作为 M6 小阶段开发预览版本。

### 验证

- `npm.cmd run backend:test` 通过，143 个测试通过。
- `npm.cmd run frontend:test` 通过，11 个测试文件、55 个测试通过。
- `npm.cmd run frontend:build` 通过，保留既有 Vite chunk 体积提示。
- `npm.cmd run check:encoding` 通过。

### 后续

- M6-0 收尾：补齐外部提交拦截记录 API、前端列表和用户决策动作。
- M6-2 后续：实现 AI 结构化解析服务和 schema 校验。

## 2026-07-02 M6 开发版本切换

M5 正式版已按 `v0.5.4` 发布，`develop` 合入发布基线后继续 M6 开发。M6 开发版本号从 `0.5.4` 切换为 `0.6.0`，后续 M6 小阶段按 `0.6.x` 递增。

README 保持正式版口径，只展示 M5 `v0.5.4` 已发布能力，不写入 M6 开发中能力。
## 2026-07-02 v0.6.1 M6-0A 拦截记录处理入口

完成 M6 外部提交保护的记录处理基础能力：

- 后端新增 `GET /api/external-submission-blocks`，支持按状态、目标服务查询拦截记录，并返回总数。
- 后端新增 `PATCH /api/external-submission-blocks/{id}`，支持标记已改名、忽略、确认风险继续提交和归档。
- 覆盖提交必须填写风险确认原因，只记录当前拦截记录的用户决策，不全局关闭敏感词规则。
- 系统设置“敏感词设置”页新增外部提交拦截记录列表，展示文件名、目标服务、命中摘要、状态、拦截时间和处理记录。
- 前端提供“已改名”“忽略”“继续提交”“归档”处理入口。

验证结果：

- `npm.cmd run backend:test`：通过，147 个测试通过。
- `npm.cmd run frontend:test`：通过，11 个测试文件、55 个测试通过。
- `npm.cmd run frontend:build`：通过，仅保留既有 Vite chunk 体积提示。

后续继续：将统一外部提交保护网关接入 TMDB、IMDb 和 AI 实际调用链路，确保命中敏感词时不会发出外部请求。

## 2026-07-02 v0.6.2 M6-0B TMDB 外部提交拦截接入

完成 M6 外部提交保护在重命名 TMDB 匹配链路的首个实际接入点：

- 重命名预览单条 TMDB 匹配前，先按当前配置检查文件名、路径和待提交标题是否命中敏感词规则。
- 命中规则时不调用 TMDB Provider，不发出外部请求，记录外部提交拦截记录，并将该预览行标记为 `needs_review` / `blocked`。
- 候选结果列表接口同样接入拦截逻辑，命中规则时直接返回空候选，避免弹窗或批量入口绕过保护。
- 新增后端测试覆盖敏感词命中时 Provider 不被调用、预览状态被标记、拦截记录写入数据库的行为。
- 开发版本号升级到 `0.6.2`。

验证结果：

- `npm.cmd run backend:test`：通过，148 个测试通过。

## 2026-07-02 v0.6.3 M6-2A AI 结构化解析服务骨架

完成 M6 AI 结构化解析的后端服务骨架：

- 新增 `AiParseCandidate` 与 `AiParseResult` 结构化结果模型。
- 扩展 `AiProvider` 抽象，新增非流式 `complete_chat` 能力。
- `DeepSeekProvider` 新增 OpenAI-compatible `/chat/completions` 实际解析调用封装，返回内容、耗时和 token usage。
- 新增 `parse_media_with_ai` 服务，解析前读取 AI 热配置并执行外部提交保护。
- 命中敏感词时不调用 AI Provider，写入 `target_service=ai` 的拦截记录。
- AI 返回内容必须是 JSON 对象，服务校验 `title`、`media_type`、`year`、`season`、`episode`、`confidence`、`reason` 字段；非法输出不回填，只返回失败状态。
- 新增后端测试覆盖合法结构化响应、非法 JSON 降级、敏感词阻断 AI 调用。
- 开发版本号升级到 `0.6.3`。

验证结果：

- `npm.cmd run backend:test`：通过，151 个测试通过。

## 2026-07-02 v0.6.4 M6-2B 重命名预览 AI 解析 API

完成单条重命名预览的 AI 结构化解析后端入口：

- 新增 `parse_rename_preview_with_ai` 服务包装，基于重命名预览记录组装文件名、路径和本地解析字段。
- 新增 `POST /api/rename-previews/{preview_id}/ai-parse`，返回结构化 AI 解析候选。
- API 只返回候选结果，不修改当前预览、不回填目标文件名，后续由前端候选确认流程接入。
- API 复用 M6-2A 的 AI 热配置、敏感词外部提交保护和 JSON schema 校验能力。
- 新增 API 测试覆盖结构化候选返回，并确认响应不泄露 AI API Key。
- 开发版本号升级到 `0.6.4`。

验证结果：

- `npm.cmd run backend:test`：通过，152 个测试通过。
