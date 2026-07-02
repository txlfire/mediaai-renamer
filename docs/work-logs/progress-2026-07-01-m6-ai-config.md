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