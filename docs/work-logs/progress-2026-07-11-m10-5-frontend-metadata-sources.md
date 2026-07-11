# 2026-07-11 M10-5 多源元数据前端入口

## 范围

- 系统设置“刮削设置”新增补充元数据源配置卡片。
- 支持 Bangumi、TVDB、豆瓣代理启用、优先级、Base URL、API Key、超时、重试、保存和测试。
- 重命名页新增多源匹配入口，支持选中行和当前列表批量匹配。
- 多源匹配前增加外部提交和敏感词保护确认说明。
- 元数据候选弹窗增加来源列和展开行字段来源展示。

## 版本

- 开发版本提升到 `0.10.6`。

## 验证

- 需执行：
  - `npm.cmd run frontend:test`
  - `npm.cmd run frontend:build`
  - `npm.cmd run backend:test`
  - `npm.cmd run check:encoding`
  - `git diff --check`
