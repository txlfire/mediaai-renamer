# 2026-07-08 M8-3 命名预览模板版本联动

## 范围

实现 M8-3 预览链路联动，让命名预览记录保存生成时使用的命名模板版本，并在模板更新后提示历史预览是否基于旧模板生成。

## 变更

- 数据库 schema 升级到 13。
- `rename_previews` 新增模板版本快照字段：
  - `naming_template_type`
  - `naming_template_version`
  - `naming_template_updated_at`
- 新生成或覆盖生成命名预览时写入模板类型、版本号和更新时间。
- 命名预览列表返回当前模板版本、旧模板状态和未知版本状态。
- TMDB、IMDb、AI 或人工候选回填目标名时刷新模板版本快照，避免状态丢失。
- 前端重命名预览列表在目标文件名后展示“旧模板生成”或“版本未知”短标签，当前模板不额外显示。
- 详情弹窗补充命名模板状态说明。

## 验证

- `npm.cmd run backend:test`：通过，195 个测试。
- `npm.cmd run frontend:test`：通过，13 个测试文件、66 个测试。
- `npm.cmd run frontend:build`：通过，保留既有 Vite chunk size 警告。

## 后续

- M8-0 到 M8-2 已有代码基线仍需按验收清单逐项复核。
- M8-4 需要在阶段收尾时同步用户手册、README 和 release 说明。
