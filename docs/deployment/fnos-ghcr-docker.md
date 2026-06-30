# fnOS Docker 镜像部署与更新

## 1. 方案说明

推荐部署方式：

- GitHub Actions 在推送 `main` 或版本标签 `v*` 时自动构建 Docker 镜像。
- 镜像发布到 GitHub Container Registry。
- fnOS 使用 `docker-compose.ghcr.yml` 拉取镜像运行，不在 NAS 上本地构建。

默认镜像：

```text
ghcr.io/txlfire/mediaai-renamer-backend:v0.5.3
ghcr.io/txlfire/mediaai-renamer-frontend:v0.5.3
```

如果 fnOS 拉取镜像时报 `denied` 或 `unauthorized`，需要在 GitHub Packages 中把两个镜像设置为 Public，或在 fnOS 上执行 `docker login ghcr.io` 后再拉取。

默认访问地址：

```text
Web: http://192.168.50.43:8971
API: http://192.168.50.43:8970/api/health
```

## 2. fnOS 目录准备

在 fnOS SSH 终端中执行：

```bash
mkdir -p /vol1/1000/docker/mediaai-renamer
cd /vol1/1000/docker/mediaai-renamer
mkdir -p data logs config
```

复制项目中的 `docker-compose.ghcr.yml` 和 `config/config.example.toml` 到该目录。

首次部署时创建正式配置：

```bash
cp config.example.toml config/config.toml
```

目录结构建议：

```text
/vol1/1000/docker/mediaai-renamer/
  docker-compose.ghcr.yml
  data/
  logs/
  config/
    config.toml
```

## 3. 媒体目录映射

如果 fnOS 媒体目录是：

```text
/vol1/1000/Movies
```

在 `docker-compose.ghcr.yml` 的 `backend.volumes` 中启用并修改示例映射：

```yaml
- /vol1/1000/Movies:/app/media/Movies
```

系统媒体源路径填写容器内路径：

```text
/app/media/Movies
```

不要填写宿主机路径 `/vol1/1000/Movies`，容器内默认看不到该路径。

## 4. 启动

如果镜像已设为 Public，可直接拉取。若镜像仍为私有，先登录 GHCR：

```bash
docker login ghcr.io
```

固定版本启动：

```bash
export MEDIAAI_IMAGE_TAG=v0.5.3
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
```

检查状态：

```bash
docker compose -f docker-compose.ghcr.yml ps
docker compose -f docker-compose.ghcr.yml logs --tail=100 backend
docker compose -f docker-compose.ghcr.yml logs --tail=100 frontend
```

## 5. 更新

更新到新版本，例如 `v0.5.4`：

```bash
cd /vol1/1000/docker/mediaai-renamer
export MEDIAAI_IMAGE_TAG=v0.5.4
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
```

如需长期固定版本，建议把 `.env` 文件放在 compose 同级目录：

```bash
MEDIAAI_IMAGE_TAG=v0.5.4
```

之后更新只需：

```bash
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
```

## 6. 验收

- `docker compose ps` 中 backend 和 frontend 均为 running。
- 浏览器打开 `http://192.168.50.43:8971`。
- 健康检查 `http://192.168.50.43:8970/api/health` 返回版本号。
- 新增媒体源路径 `/app/media/Movies`。
- 测试连接成功。
- 扫描任务能识别视频文件。
- 生成命名预览正常。
- 真实重命名前先执行 dry-run。

## 7. 后续自动更新

当前推荐先使用手动更新：

```bash
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
```

如果后续确认镜像发布和 fnOS 运行稳定，再评估 Watchtower 或 GitHub Actions SSH 自动部署。
