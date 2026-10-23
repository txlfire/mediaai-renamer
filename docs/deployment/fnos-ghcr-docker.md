# fnOS Docker 镜像部署与更新

类型：部署与维护手册。当前稳定版v1.4.0、schema55，固定制品和来源见[发布说明](../releases/v1.4.0.md)，验收边界见[项目状态](../development/project-status.md)。旧v1.3.0为schema39。

当前fnOS已于2026-10-23另获授权切换到固定正式版，并核对unless-stopped与一次仅新容器正常重启，见[部署记录](../work-logs/progress-2026-10-23-fnos-stable.md)。本手册“不会随发布自动切换”仍成立，本次是另行批准的部署；没有实际重启NAS。证书按[人工维护规程](mediaai-certificate-maintenance.md)处理，不宣称已启用自动续期。

v1.4.0包含CD2数据库配置及受控目录整理，schema55。升级前必须核对任务/未知保护、worker退出、匹配备份及副本迁移，见[CD2接入与升级准备第6节](cd2-readonly-preflight.md#6-正式升级前核对与回退)；不能将新库交给旧版本回退。运行实例不随公开发布自动切换，真实媒体动作仍需单独确认。

## 1. 运行形态

目标镜像 `ghcr.io/txlfire/mediaai-renamer:v1.4.0`，CI默认平台为 `linux/amd64`，以标签CI及实际镜像核验为准。正式Compose只有一个mediaai服务和一个mediaai-renamer容器，FastAPI同时提供Vue页面及API，长任务由隔离worker执行，不需要独立Nginx或在NAS上编译前端。

| 入口 | 地址 |
| --- | --- |
| Web主入口 | `http://<NAS_IP>:8971` |
| 同源健康检查 | `http://<NAS_IP>:8971/api/health` |
| 兼容入口 | `http://<NAS_IP>:8970` |

两个端口映射同一容器8970。主入口HTTP与WebDAV媒体源仅允许HTTPS是不同层次，不能据此使用HTTP WebDAV。

当前固定镜像已验证可匿名读取。拉取出现denied/unauthorized时先核对镜像名、标签、网络和客户端已有认证；自建私有包才需 `docker login ghcr.io`。不为排障擅自改变包可见性或在文档里填写令牌。

## 2. 目录与配置

选择自己的部署目录，下面路径是占位示例，不是项目要求的固定位置：

```bash
mkdir -p /path/to/docker/mediaai-renamer
cd /path/to/docker/mediaai-renamer
mkdir -p data logs config media
```

将稳定标签的 `docker-compose.ghcr.yml` 放到该目录，将 `config/config.example.toml` 放到其 `config/` 下。首次部署才复制，不覆盖已有配置：

```bash
if [ ! -f config/config.toml ]; then cp config/config.example.toml config/config.toml; fi
```

示例配置的 `/app/data` 和 `/app/logs` 是容器路径。最终目录为：

```text
mediaai-renamer/
  .env
  docker-compose.ghcr.yml
  data/
  logs/
  media/
  config/
    config.example.toml
    config.toml
```

在同级 `.env` 设置固定版本，保留已有其他变量；以下是文件内容，不是shell命令：

```dotenv
MEDIAAI_IMAGE_TAG=v1.4.0
```

数据库和凭据密钥需持久化。默认密钥文件为 `data/secrets/credentials.key`；也可在已配置的独立挂载保存，环境秘密 `MEDIAAI_CREDENTIAL_KEY` 优先。更换路径或环境值不是密钥轮换，恢复必须使用匹配的原密钥。

## 3. 媒体目录映射

Compose默认将部署目录的 `media/` 映射到 `/app/media`。先创建小型专用测试目录：

```bash
mkdir -p media/Test
```

正式媒体目录在 `services.mediaai.volumes` 中增加映射，例如先以只读方式扫描：

```yaml
services:
  mediaai:
    volumes:
      - /path/to/movies:/app/media/Movies:ro
```

系统媒体源填写 `/app/media/Movies`，不是宿主机路径。真实重命名/回滚需要改为读写挂载及相应UID/GID权限；不要给全部目录放宽权限。目录可写不替代用户权限、dry-run和确认。NFS应先由宿主完成挂载；WebDAV证书、认证及写能力见[专项说明](webdav.md)。

## 4. 首次启动与验收

```bash
docker compose -f docker-compose.ghcr.yml config --quiet
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
docker compose -f docker-compose.ghcr.yml ps
docker compose -f docker-compose.ghcr.yml logs --tail=100 mediaai
```

预期：一个mediaai-renamer容器，状态running；两个健康入口均返回200、`status=ok`、`version=1.4.0`。仅容器running不等于应用健康，还需打开Web登录页。CD2的Token交接另须可信HTTPS；普通HTTP入口不能绕过该门禁。

首次无用户时从登录页初始化管理员。示例默认关闭自动创建；已有实例使用已保存账号，不保证admin/123456仍有效。不要删除数据库恢复密码，应按[用户手册](../manuals/MediaAI-Renamer-用户手册.md)的应急入口处理。

先测试媒体源连接、扫描专用目录、生成预览和dry-run。仅明确授权的测试文件才执行真实改名与回滚；WebDAV必须另外通过当前端点条件MOVE验证。核对审计、操作日志与恢复列表，测试后验证还原，再接入正式媒体。

## 5. 旧双容器迁移

仅对确实使用旧前后端双容器的实例执行。核对原Compose项目、容器归属、挂载和密钥，按第9节停服/备份。使用**原Compose文件**正常停止该项目；若原文件仍名为docker-compose.ghcr.yml：

```bash
docker compose -f docker-compose.ghcr.yml down
```

不加 `--volumes`，不凭容器名字强制删除，不执行全局prune。确认备份后替换新版Compose，沿用原data/logs/config及匹配密钥，设置固定版本，再按第4节启动和验证。自定义named volume须明确迁移映射，不能默认旧数据就在同级目录。

## 6. 更新版本

1. 核对升级来源、目标标签及第9节限制，停止应用与全部worker，确认没有仍在执行的文件动作。
2. 备份完整data/logs/config及独立密钥，记录旧镜像tag/digest与挂载；不要使用运行中的数据库文件简单复制代替一致性备份。
3. 修改 `.env` 的固定版本，执行pull/up并核对健康版本、登录、配置、任务与文件事实。下方命令不负责备份或数据库降级。

```bash
docker compose -f docker-compose.ghcr.yml down
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
docker compose -f docker-compose.ghcr.yml ps
docker compose -f docker-compose.ghcr.yml logs --tail=100 mediaai
```

由v1.2.0升级至v1.3.0仍为schema39；从更早版本会执行迁移，不因此省略备份。共享目录/NFS阻塞时，停止请求或超时不等于worker实际退出，先检查进程与挂载，不强清执行槽。

## 7. 常见错误

- 页面可达而API失败：核对同源健康、反向代理和配置，不额外部署旧Nginx容器。
- 媒体目录不可见：检查容器映射、容器内路径、UID/GID和宿主挂载，不把宿主路径直接填入容器。
- 密钥缺失或解密失败：恢复对应原密钥和配置，保留原库，不生成替代密钥、不清空凭据绕过启动。
- 镜像下载慢：核对NAS网络和可信代理；代理用于daemon拉取，不把凭据写进镜像或公开配置。
- 未知文件/外发结果：先查看意图、任务和供应商状态；不能以重复执行作为恢复方式。

## 8. 从源码构建

只在需要本地源码镜像且具备构建环境时使用，不与同名正式实例同时运行：

```bash
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.yml up -d --build
docker compose -f docker-compose.yml ps
```

生成 `mediaai-renamer:local` 单容器，继续持久化data/logs/config。基础镜像或依赖下载401应修复daemon镜像源/认证；构建代理不得留在最终镜像。可只读检查镜像环境，但输出可能含自定义秘密，分享前须脱敏：

```bash
docker image inspect mediaai-renamer:local --format '{{json .Config.Env}}'
```

## 9. 历史版本升级与回退边界

- v1.4.0为schema55（v1.3.0为39）。独立v3凭据自schema21引入，旧v1/v2密文依赖原database_path字符串，首次迁移前不能随意改路径；先在受控副本中复现原配置完成迁移验证。
- schema22起的安全快照使旧dry-run需要重新生成；schema23～25增加本地意图/跨盘身份/回滚快照，schema27引入文件动作审计意图。迁移不会猜测历史请求是否成功。
- schema31/32/34分别增加重命名、回滚和待处理迁移的后台执行信息；真实文件写共用FIFO。旧checked/running记录不自动放进新队列，扫描使用独立槽。遗留槽先通过只读恢复工具核对，实际释放需要停服确认及已知PID退出。
- schema33引入跨进程HTTP预算：默认同数据库全局4实际请求、每origin1并发/2次每秒；等待计入超时。不自动重定向或重试未知请求，不将SQLite共享到多主机作为分布式限流。
- schema39持久化后台探测/批量匹配任务及逐项结果；普通结果默认7天、单任务4MiB、总逻辑64MiB，清理不删除幂等UUID或重发任务。接口与维护见[后台任务手册](../development/background-jobs/api-and-maintenance.md)。
- 本轮新增CD2预算/盘点、改名/恢复/隔离计划、路径水位与schema55数据库配置对象。旧TOML不自动导入，三项执行默认关闭；未知RPC和保护槽不因迁移、清理观察或配置保存被抹除。迁移前检查及回退顺序见[CD2部署核对](cd2-readonly-preflight.md)。
- 迁移失败时停止并保留原库/密钥，不删库、不清凭据。升级后重新核对账户权限、凭据读取、分页、dry-run、旧任务与审计意图，不指向正式媒体试写。
- 回退用旧镜像＋升级前数据库/配置/匹配密钥整套恢复，没有通用数据库降级工具。新版本已改名/MOVE/迁移时先核对文件意图与真实身份，不能只恢复旧库掩盖文件事实。
- 历史脱敏、意图核对等维护默认只读或要求显式确认，不由升级自动执行清理；未知/pending意图不为腾空间删除，安全审计不套用普通日志保留期，不在线VACUUM。

恢复/维护命令见[完整手册](../manuals/MediaAI-Renamer-用户手册.md)及[WebDAV说明](webdav.md)。此前v1.3.0隔离包已验新装、schema20→39及旧备份恢复、标签CI门禁；本轮1.4.0/schema55的独立结果见[发布记录](../work-logs/progress-2026-10-22-release-1.4.0.md)，不能沿用旧CI结果。它们不代表用户正式实例、真实NFS硬阻塞或每个NAS写端点已通过验收。
