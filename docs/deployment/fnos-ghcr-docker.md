# fnOS Docker 镜像部署与更新

## 1. 方案说明

MediaAI Renamer 正式 Docker 版本使用一个镜像和一个容器：

```text
ghcr.io/txlfire/mediaai-renamer:<tag>
```

镜像内由 FastAPI 同时提供 Vue 页面和 `/api` 接口。fnOS 只需下载一个镜像，不需要在 NAS 上编译前后端，也不需要单独运行 Nginx 容器。

如果拉取时报 `denied` 或 `unauthorized`，请在 GitHub Packages 中把镜像设为 Public，或先执行：

```bash
docker login ghcr.io
```

默认访问地址：

```text
Web: http://192.168.50.43:8971
API: http://192.168.50.43:8970/api/health
Web 同源 API: http://192.168.50.43:8971/api/health
```

`8971` 是页面主入口；`8970` 作为旧 API 地址兼容入口，两个端口访问的是同一个容器。

## 2. fnOS 目录准备

在 fnOS SSH 终端中执行：

```bash
mkdir -p /vol1/1000/docker/mediaai-renamer
cd /vol1/1000/docker/mediaai-renamer
mkdir -p data logs config
```

复制项目中的 `docker-compose.ghcr.yml` 和 `config/config.example.toml` 到该目录。首次部署时创建正式配置：

```bash
cp config.example.toml config/config.toml
```

建议目录结构：

```text
/vol1/1000/docker/mediaai-renamer/
  .env
  docker-compose.ghcr.yml
  data/
  logs/
  config/
    config.toml
```

## 3. 媒体目录映射

如果 fnOS 媒体目录是 `/vol1/1000/Movies`，在 `docker-compose.ghcr.yml` 的 `mediaai.volumes` 中增加：

```yaml
- /vol1/1000/Movies:/app/media/Movies
```

系统媒体源路径填写容器内路径：

```text
/app/media/Movies
```

不要填写宿主机路径 `/vol1/1000/Movies`，容器内默认无法直接访问该路径。

## 4. 首次启动

在 Compose 同级创建 `.env` 固定版本：

```bash
MEDIAAI_IMAGE_TAG=v1.0.0
```

启动并检查：

```bash
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
docker compose -f docker-compose.ghcr.yml ps
docker compose -f docker-compose.ghcr.yml logs --tail=100 mediaai
```

`docker compose ps` 应只显示 `mediaai-renamer` 一个运行中的容器。

## 5. 从旧双容器版本迁移

新 Compose 会把后端和前端合并为一个服务。升级前先停止旧项目并移除孤立容器：

```bash
cd /vol1/1000/docker/mediaai-renamer
docker compose -f docker-compose.ghcr.yml down --remove-orphans
docker rm -f mediaai-renamer-backend mediaai-renamer-frontend 2>/dev/null || true
```

以上命令只移除旧容器，不删除以下持久化目录：

```text
data/
logs/
config/
```

替换为新版 `docker-compose.ghcr.yml` 后执行：

```bash
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
```

## 6. 更新版本

修改 `.env` 中的 `MEDIAAI_IMAGE_TAG`，然后执行：

```bash
cd /vol1/1000/docker/mediaai-renamer
docker compose -f docker-compose.ghcr.yml down --remove-orphans
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
docker compose -f docker-compose.ghcr.yml ps
```

查看日志：

```bash
docker compose -f docker-compose.ghcr.yml logs --tail=100 mediaai
```

## 7. 验收

- `docker compose ps` 只显示一个 `mediaai-renamer` 容器且状态为 running。
- 浏览器打开 `http://192.168.50.43:8971`。
- `http://192.168.50.43:8971/api/health` 返回版本号和 `status: ok`。
- 兼容地址 `http://192.168.50.43:8970/api/health` 返回相同结果。
- 新增媒体源路径 `/app/media/Movies` 并测试连接。
- 扫描任务能识别视频文件。
- 生成命名预览正常。
- 真实重命名前先执行 dry-run。

GHCR 在部分 NAS 网络环境中下载较慢时，可以为 Docker daemon 配置代理，或在可信客户端通过代理下载并校验镜像后再导入 fnOS。
