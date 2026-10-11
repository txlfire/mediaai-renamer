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
Web: http://<NAS_IP>:8971
API: http://<NAS_IP>:8970/api/health
Web 同源 API: http://<NAS_IP>:8971/api/health
```

`8971` 是页面主入口；`8970` 作为旧 API 地址兼容入口，两个端口访问的是同一个容器。

## 2. fnOS 目录准备

在 fnOS SSH 终端中执行：

```bash
mkdir -p /path/to/docker/mediaai-renamer
cd /path/to/docker/mediaai-renamer
mkdir -p data logs config media
```

复制项目中的 `docker-compose.ghcr.yml` 和 `config/config.example.toml` 到该目录。首次部署时创建正式配置：

```bash
cp config.example.toml config/config.toml
```

建议目录结构：

```text
/path/to/docker/mediaai-renamer/
  .env
  docker-compose.ghcr.yml
  data/
  logs/
  media/
  config/
    config.toml
```

## 3. 媒体目录映射

Compose 默认将同级 `media/` 挂载到容器内 `/app/media`。可以先创建测试子目录：

```bash
mkdir -p media/Test
```

重新启动容器后，“本地路径 -> 选择目录”的初始位置应为 `/app/media`，并能看到 `Test`。

如果需要挂载正式媒体目录，在 `docker-compose.ghcr.yml` 的 `mediaai.volumes` 中增加：

```yaml
- /path/to/movies:/app/media/Movies
```

系统媒体源路径填写容器内路径：

```text
/app/media/Movies
```

不要填写宿主机路径，容器内只能访问已经映射的容器路径。

## 4. 首次启动

在 Compose 同级创建 `.env` 固定版本：

```bash
MEDIAAI_IMAGE_TAG=v1.0.1
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
cd /path/to/docker/mediaai-renamer
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

先停服并备份数据、日志、配置及对应凭据密钥，再更换版本。下方命令不会自动备份，也不会做数据库降级；未发布审查分支的升级边界见第9节。

修改 `.env` 中的 `MEDIAAI_IMAGE_TAG`，然后执行：

```bash
cd /path/to/docker/mediaai-renamer
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
- 浏览器打开 `http://<NAS_IP>:8971`。
- `http://<NAS_IP>:8971/api/health` 返回版本号和 `status: ok`。
- 兼容地址 `http://<NAS_IP>:8970/api/health` 返回相同结果。
- 新增媒体源路径 `/app/media/Movies` 并测试连接。
- 扫描任务能识别视频文件。
- 生成命名预览正常。
- 真实重命名前先执行 dry-run。

GHCR 在部分 NAS 网络环境中下载较慢时，可以为 Docker daemon 配置代理，或在可信客户端通过代理下载并校验镜像后再导入 fnOS。

## 8. 从源码构建单容器

将项目源码放到部署目录后执行：

```bash
cd /path/to/docker/mediaai-renamer
docker compose -f docker-compose.yml build
docker compose -f docker-compose.yml up -d
docker compose -f docker-compose.yml ps
```

源码 Compose 生成 `mediaai-renamer:local` 镜像，并只启动
`mediaai-renamer` 一个容器。`data/`、`logs/` 和 `config/` 继续使用宿主机目录。

若 fnOS 的默认镜像站返回 `401 Unauthorized`，需先修复 Docker daemon
镜像源，或为 daemon 配置可访问 Docker Hub 的 HTTP/HTTPS 代理。代理只用于
下载基础镜像和构建依赖，不应写入最终镜像环境。构建后可用以下命令检查：

```bash
docker inspect mediaai-renamer:local --format '{{json .Config.Env}}'
```

## 9. 审查修复分支升级与回退边界

本节适用于尚未发布的审查修复分支，不表示稳定镜像已包含新schema或正式实例已验收。

1. 停止应用和所有worker，确认没有仍在执行或不可中断的文件动作；NFS阻塞不能仅凭应用超时认定退出。备份完整data/logs/config目录及当前镜像标签/digest，数据库与密钥分别保管，不把备份或环境秘密上传。
2. 新v3凭据优先使用MEDIAAI_CREDENTIAL_KEY，否则使用配置credential_key_file或data/secrets/credentials.key；恢复必须带对应原密钥。旧v1/v2凭据仍绑定原数据库路径，不能在迁移前随意改路径或复制到新路径猜密钥；先在受控副本中复现原配置路径完成迁移验证。
3. 新代码启动执行幂等数据库迁移。先在隔离副本验证登录/权限、凭据解密、分页、dry-run、遗留任务及审计意图，不指向正式媒体做试写。迁移失败停止并保留原库/密钥，不清空凭据、不生成替代密钥绕过失败。
4. 回退采用旧镜像加升级前数据库/配置/对应密钥的整套恢复，不将迁移后数据库直接交给旧程序；当前没有数据库降级工具。若新版本已真实改名、迁移或MOVE，不能仅恢复旧数据库掩盖文件事实，须先核对持久化文件意图与真实源/目标身份。
5. 已消费外部授权且结果未知时，先本机只读核对，不能自动重发网络请求。历史审计脱敏、文件意图核对和未知结果人工观察工具默认只读或需显式维护确认，不由升级过程自动执行正式清理。
6. schema31为重命名批次增添可空执行上下文/租约字段；迁移不把旧dry-run或旧running批次自动放进新队列。服务停止须等待扫描及重命名子进程实际退出；保留槽位不等于文件动作失败，可先只读运行 `python -m app.recover_renames --config <明确配置>` 核对，不凭过期时间自动释放或重试。
7. schema32为回滚计划增添可空执行上下文/租约和真实成功、失败计数，历史计数从已存在逐项终态回填，不把旧checked计划自动排队。重命名与回滚共用持久化写任务槽，旧同步入口也遵守队列占用；schema34加入待处理迁移，扫描仍不在该槽。停服须等待回滚子进程实际退出，遗留槽先用 `python -m app.recover_rollbacks --config <明确配置>` 只读核对；有界游标、停服确认及已知退出PID要求与重命名核对一致，不自动重放文件动作。执行模块命令需在后端目录或明确PYTHONPATH=backend的项目根目录进行。

8. schema33增添空的external_request_sites/external_request_leases及索引，不由历史记录生成请求。静态external_requests默认每站点2次/秒、单站点1实际请求、同机同数据库全局4实际请求；AI/元数据及Provider覆盖见示例配置和手册24.2。修改需重启服务及所有worker；等待计入请求超时，不自动重定向。存活/未知PID槽保留，确认进程实际退出才回收，不推断供应商结果或重发。不将SQLite共享到其他主机作为分布式请求预算。

9. schema34增添空的pending_move_tasks/items及队首/令牌/来源/待处理索引，保留已有pending_files，不由历史记录生成迁移或预约。新迁移202链和旧同步迁移共同遵守重命名/回滚FIFO槽；预约期间来源改配/退役及记录移除、AI解析被拒绝。受理不访问媒体目录，worker才验证文件身份，跨盘复制后撤权不删除源。停服须等待迁移worker实际退出；遗留槽先只读 `python -m app.recover_pending_moves --config <明确配置>`，实际释放要求 `--task-id <ID> --apply --confirm-service-stopped` 及已知PID退出。不自动重放未知动作，操作细节见手册24.3。schema33到34的临时库幂等测试不是正式实例升级/回退证明。

预期结果：配置/密钥/数据库一致，旧会话策略、实际文件与任务证据可解释，失败保留可回退材料。常见错误：缺原密钥、旧路径改变、直接回退旧镜像读新库、遗留worker未退出、将未知MOVE或AI请求重复执行。

目前证据为Windows隔离迁移与Python3.11/3.12回归、Linux目标wheel哈希安装及本地测试ZIP。正式数据库升级/回退、真实Linux/NFS、Docker/CI与NAS写入仍未执行，不能将本节操作清单当验收报告。
