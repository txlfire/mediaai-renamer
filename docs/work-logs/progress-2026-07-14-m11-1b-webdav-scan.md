# M11-1B WebDAV 递归扫描工作日志

日期：2026-07-14

## 背景

M11-1A 已完成 WebDAV HTTPS 地址校验、连接测试和目录浏览，但扫描服务仍只支持本地、UNC / SMB 和已挂载 NFS 路径。本阶段将 WebDAV 先接入只读扫描链路，不开放远程写操作。

## 已完成

- WebDAV Provider 支持递归 `PROPFIND Depth: 1` 文件枚举。
- 读取 WebDAV 文件的大小、修改时间和 ETag。
- WebDAV 能力声明从不可扫描调整为可扫描，真实重命名仍保持关闭。
- 扫描服务新增通用扫描条目，WebDAV URL 文件和本地 Path 文件共用媒体文件入库、最小文件阈值、待处理列表、扫描索引和增量跳过逻辑。
- WebDAV ETag 纳入现有 `scan_file_index.fingerprint`，不新增数据库字段。
- 前端媒体源提示、README、用户手册和 M11 计划同步为“WebDAV 支持扫描但不支持真实重命名”。
- 版本号提升至 `0.11.7`。

## 边界

- 本阶段不执行 WebDAV MOVE、真实重命名、远程写操作锁接入和失败恢复。
- 本阶段不引入新数据库迁移；ETag 只作为 fingerprint 的一部分参与增量判断。
- FTP、SFTP、S3 仍保持候选或未开放状态。

## 验证

已完成完整验证：

```text
backend.tests.test_shared_protocols -> 11 tests OK
backend.tests.test_scan_service -> 17 tests OK
npm.cmd run backend:test -> 284 tests OK
npm.cmd run frontend:test -> 78 tests OK
npm.cmd run frontend:build -> OK，保留已知 chunk warning
npm.cmd run check:encoding -> OK
git diff --check -> OK
```
