# 2026-06-28 编码与国际化治理记录

## 背景

开发过程中多次出现终端中文显示为乱码、文档内容疑似乱码、前端页面文案重复抽取的问题。本次将问题拆成两类处理：

- 终端显示乱码：文件本身是 UTF-8，但 PowerShell 或命令输出按错误代码页显示。
- 文件内容乱码：文件内容已经被错误编码写入，需要按 UTF-8 重写或修复。

## 完成内容

- 新增 `.editorconfig`，统一源码、配置和 Markdown 文档使用 UTF-8 no BOM。
- 更新 `.gitattributes`，统一文本文件 LF 换行，保留 `.bat`、`.cmd` 使用 CRLF。
- 新增 `scripts/check-encoding.ps1`，检查 BOM、混合换行、常见中文 mojibake 标记，以及前端非语言文件中的硬编码中文。
- 新增 `npm run check:encoding`，作为后续提交前的固定检查命令。
- 修复 README 和总用户手册中的真实内容乱码。
- 将系统设置相关错误提示和状态文案补充到 `frontend/src/locales/zh-CN.ts`。
- 修复 `SettingsView.vue` 和 `settings.ts` 中残留的硬编码中文展示文本。
- 更新 `docs/development/development-guide.md`，补充编码和前端国际化规范。

## 国际化检查结果

- 当前前端文案源：`frontend/src/locales/zh-CN.ts`。
- 当前自动检查范围：`frontend/src` 下的 `.vue`、`.ts` 文件，排除 `locales`、测试文件和注释。
- 检查结果：未发现新增页面、Store、API 中的硬编码中文展示文本。
- 后续新增功能时，应先检查 `zh-CN.ts` 是否已有可复用 key，再补充新文案。

## 验证记录

- `npm.cmd run check:encoding`：通过。
- `git diff --check`：通过。
- `npm.cmd run frontend:test`：通过，10 项测试通过。
- `npm.cmd run backend:test`：通过，96 项测试通过。
- `npm.cmd run frontend:build`：通过，仅保留 Vite chunk 体积提示。

## 后续约束

- 不再使用 PowerShell 5 的 `Set-Content -Encoding UTF8` 写源码或文档，避免写入 BOM。
- 中文内容检查优先使用 `npm run check:encoding` 或 Node UTF-8 读取，不以 PowerShell 控制台显示作为文件是否乱码的判断依据。
- 新增前端用户可见文案必须进入 `frontend/src/locales/zh-CN.ts`。
