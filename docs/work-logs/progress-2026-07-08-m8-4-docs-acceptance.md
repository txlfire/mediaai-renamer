# 2026-07-08 M8-4 文档与验收收尾

## 本次范围

- 补齐规则测试结果的字段命中和格式告警。
- 同步 M8 开发计划、验收清单、设计文档、README 和用户手册。
- 新增 M8 用户手册。

## 实现核对

- `/api/settings/naming/test` 返回 `field_hits` 和 `warnings`。
- 系统设置命名规则测试结果展示字段命中和格式告警。
- 命名预览记录已保存模板类型、模板版本和模板更新时间快照。
- 历史预览可区分当前模板、旧模板和版本未知。

## 已执行验证

- `npm.cmd run backend:test`：通过，196 个测试。
- `npm.cmd run frontend:test`：通过，13 个测试文件、66 个测试。
- `npm.cmd run frontend:build`：通过，仅保留 Vite chunk-size 已知警告。

## 收尾验证

- `npm.cmd run check:encoding`：通过。
- `git diff --check`：通过。
