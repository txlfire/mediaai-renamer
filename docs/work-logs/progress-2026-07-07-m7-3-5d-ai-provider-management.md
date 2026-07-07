# 2026-07-07 M7-3.5D AI Provider 管理与批量入口门禁

## 本次范围

- 后端 AI Provider 抽象扩展为支持 `deepseek`、`openai_compatible` 和 `custom` 三类 OpenAI-compatible 配置。
- 系统设置新增 AI Provider 配置列表与当前激活配置 ID，支持保存多套 Provider / 模型配置并切换当前生效项。
- Provider 列表接口继续脱敏返回 API Key；更新 Provider 列表时会保留已有密钥，不会把前端脱敏占位符写回数据库。
- AI 连接测试结果新增当前激活 profile、模型和 Base URL 信息，供设置页和命名预览页复用。
- 命名预览页批量 AI 与 TMDB→AI 模式增加执行门禁：当前激活 Provider 未完成有效测试时不可直接执行。
- 批量 AI / TMDB→AI 确认提示补充当前 Provider、模型和 Base URL 摘要。
- 用户手册与 M7 验收清单同步更新。

## 验证

- `npm.cmd run backend:test`
- `npm.cmd run frontend:test`
- `npm.cmd run frontend:build`
- `npm.cmd run check:encoding`

## 结果

- 后端测试：172 个测试全部通过。
- 前端测试：11 个测试文件、57 个测试全部通过。
- 前端构建：通过；仍保留既有 Vite chunk size 警告，无新增构建错误。
- 编码检查：通过。

## 说明

- `scan.minimum_file_size` 相关验收项已结合既有测试回填到 M7 验收清单。
