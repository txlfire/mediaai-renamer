# 2026-07-12 M10-6 文档和验收收尾

## 范围

- M10 设计手册同步到 `0.10.7`。
- M10 验收清单补齐外部提交覆盖强确认项。
- 新增 M10 验收报告。
- 系统设计、总设计、README 和用户手册同步 M10 当前状态。
- 版本号提升到 `0.10.7`。

## 说明

- 本阶段不改业务代码。
- `.codegraph/`、`.reasonix/`、`docs/development/m5/backups/` 是本地分析或备份目录，不纳入提交。

## 验证

- 需执行：
  - `npm.cmd run backend:test`
  - `npm.cmd run frontend:test`
  - `npm.cmd run frontend:build`
  - `npm.cmd run check:encoding`
  - `git diff --check`
