# 单容器与统一发布包改造记录

日期：2026-07-30

## 完成内容

- FastAPI 增加前端静态资源托管和 Vue Router 回退，API 路由保持优先。
- Docker 改为多阶段单镜像构建，运行时仅保留 `mediaai` 一个服务和一个容器。
- GHCR 工作流改为发布单一镜像 `ghcr.io/txlfire/mediaai-renamer`。
- Windows、Linux 发布脚本改为生成前后端统一 ZIP：
  `mediaai-renamer-v1.0.0.zip`。
- 更新 README、fnOS 部署文档、开发指南和常用命令说明。

## 提交记录

- `4534a18 feat: 由 FastAPI 托管前端页面`
- `e3ade01 build: 合并前后端 Docker 镜像`
- `cab8ceb ci: 发布单一 Docker 镜像`
- `e4407b2 docs: 更新单容器部署说明`
- `18f6893 build: 合并前后端发布包`
- `41b1e8e docs: 补充统一发布包说明`

## 验证结果

- 后端测试：325 项通过，10 项跳过。
- 前端测试：18 个测试文件、82 项测试全部通过。
- 前端构建：通过，仅保留既有的大分块提示。
- 编码检查：通过。
- 单进程源码冒烟：根页面、SPA 路由、健康检查和缺失静态资源行为符合预期。
- 统一 ZIP：
  - 文件：`releases/mediaai-renamer-v1.0.0.zip`
  - 大小：640641 字节
  - SHA-256：`F225A9DB3503749645337FFB2A4F627B8FD12BA34F7AD7C8269A1BB181700D12`
  - 解压后由 Uvicorn 单进程启动成功，首页和 `/api/health` 均返回 200。
- Compose 文件已通过 YAML 结构解析，两个文件均只包含 `mediaai` 服务。

## 尚待外部环境验证

本机未安装 Docker Engine，fnOS `192.168.50.43` 在验证期间无法连接，
因此尚未执行实际 Docker 镜像构建、容器启动和 NAS 浏览器访问测试。
设备恢复后需补跑：

```bash
docker compose build
docker compose up -d
docker compose ps
curl http://127.0.0.1:8970/api/health
```

该项只影响 Docker 动态验收，不影响已通过的统一 ZIP 和单进程运行验证。
