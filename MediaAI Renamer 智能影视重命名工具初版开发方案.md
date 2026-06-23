# MediaAI Renamer 智能影视重命名工具初版开发方案

## 文档说明

本文档定义 MediaAI Renamer 初版的可落地开发方案。项目名使用 `MediaAI Renamer`，仓库/包/目录名使用 `mediaai-renamer`，中文名为 `MediaAI 智能影视重命名工具`。本工具的主要目的不是替代 Emby、Jellyfin、Plex、影视墙或 NAS 媒体库系统，而是把下载得到的、命名混乱的视频文件整理成有序且符合主流刮削标准的文件名和目录结构，方便各类 NAS 或类 NAS 系统进行刮削、挂载和媒体库识别。

初版目标是交付一个可部署在 fnOS、NAS、Linux 服务器和本地电脑上的 Web 服务，支持媒体目录识别、共享目录接入、全量/增量扫描、文件名本地解析、按需 AI 解析、TMDB 匹配、预览确认和安全批量重命名。

初版采用前后端分离架构：前端使用 Vue 3 + Vite + Element Plus，后端使用 Python 3.11 + FastAPI。AI 接入默认使用 DeepSeek，并通过 OpenAI-compatible 适配层保留 GPT 等常用模型接口。

## 一、初版产品目标

1. 将混乱下载文件名规范化为主流刮削器容易识别的命名格式。
2. 支持电影和剧集两类核心命名目标，生成有序、可预览、可批量确认的新文件名。
3. 支持通过浏览器访问 Web 管理界面，适配 fnOS、群晖 NAS、Linux 服务器、Windows、macOS。
4. 支持识别和保存媒体目录，后续扫描时可复用已识别目录。
5. 支持本地目录、Docker 挂载目录、Windows 共享、NAS SMB/CIFS 共享、Linux NFS 共享。
6. 支持识别 Windows、NAS、Linux 三类常见共享来源，并在页面展示共享类型、访问路径和挂载状态。
7. 支持全量扫描和增量扫描，创建扫描任务时由用户选择。
8. 对已处理文件写入操作标记和扫描索引，下次扫描同一目录时提示用户选择全量或增量。
9. 支持常见和较老的视频格式，包括 `.mp4`、`.mkv`、`.mov`、`.avi`、`.wmv`、`.flv`、`.ts`、`.m2ts`、`.mpeg`、`.mpg`、`.m4v`、`.3gp`、`.vob`、`.dat`、`.rm`、`.rmvb`、`.asf`、`.divx`、`.xvid`、`.ogm`、`.ogv`、`.webm`、`.iso`、`.strm`。
10. 支持中文、英文、中英混合文件名本地解析，优先使用本地规则解析和广告关键词过滤。
11. 程序先自行解析，无法解析或低置信度的条目汇总到页面，由用户决定是否调用 AI。
12. 调用 AI 前展示待解析条目数量、预估输入 token、预估输出 token 和预估总 token，用户确认后才发起请求。
13. 主流程优先使用 TMDB 搜索匹配电影和剧集，保留既有 `tmdb_match.py` 主匹配逻辑不变。
14. TMDB 匹配失败或低置信度时按类型分流：动漫调用 Bangumi，国产影视/港台剧调用豆瓣代理接口，纯海外美剧调用 TVDB 补充分季分集信息。
15. 增加文件名多语言识别接口，初版识别中文、英文、中英混合，保留后续扩展日文、韩文、罗马音等多语言能力。
16. 支持重命名前预览新旧文件名，显示冲突、非法字符、路径过长、低置信度等风险。
17. 支持用户逐条勾选、批量选择、手动修正标题、年份、季集信息后再执行重命名。
18. 初版对 `.strm` 文件只做重命名保护：可识别并改名，但不生成新的 `.strm` 文件。
19. 支持 SQLite 保存媒体源、已识别目录、扫描索引、任务、预览结果和重命名日志。
20. 日志可在页面查看，支持导出 TXT、CSV、JSON。

## 二、初版范围边界

### 初版必须实现

- Web UI：设置页、媒体源页、已识别目录页、扫描页、AI 解析确认页、重命名预览页、日志页。
- 后端 API：配置管理、媒体源管理、共享识别、目录持久化、扫描任务、AI 解析、TMDB 匹配、预览结果、执行重命名、日志导出。
- 网络共享：支持 SMB/CIFS 和 NFS，共享来源覆盖 Windows 共享、NAS 共享、Linux NFS 共享。
- 目录复用：已识别的媒体目录必须保存到数据库，后续扫描、增量判断、路径白名单都基于已保存目录。
- 多源匹配：TMDB 为主匹配源，Bangumi、豆瓣代理、TVDB 仅在 TMDB 失败或低置信度时作为补充源。
- 多语言识别：文件名识别必须通过统一接口输出语言、地区、媒体类型、题材类型和置信度，后续可扩展更多语言。
- 扫描策略：支持全量扫描和基于扫描索引的增量扫描。
- AI 成本控制：本地解析优先，AI 调用必须由用户二次确认，并在调用前显示 token 预估。
- 命名标准化：输出命名必须服务于主流媒体刮削器识别，避免生成只有本工具能理解的私有格式。
- 部署方式：Docker Compose 优先，支持 fnOS/NAS 通过浏览器访问。

### 初版暂不实现

- 本地 GGUF 离线模型。
- 多用户账号权限系统。
- 定时任务和后台自动刮削。
- 手机端专门适配。
- 完整桌面客户端打包。
- 新建 `.strm` 文件。初版只保护和重命名现有 `.strm`，后续版本保留生成 `.strm` 文件的能力。

## 三、运行环境

| 环境项 | 推荐方案 |
| --- | --- |
| 后端语言 | Python 3.11+ |
| 前端框架 | Vue 3 + Vite + Element Plus |
| 后端框架 | FastAPI + Uvicorn |
| 数据库 | SQLite |
| 部署方式 | Docker Compose 优先，venv 本地运行备选 |
| 目标平台 | fnOS、群晖 NAS、Linux 服务器、Windows、macOS |
| 访问方式 | 浏览器访问 `http://NAS-IP:端口` |

## 四、整体技术栈

| 功能模块 | 技术选型 | 说明 |
| --- | --- | --- |
| 前端 UI | Vue 3、Vite、Element Plus、Pinia、Vue Router | 构建正式 Web 管理界面 |
| 后端 API | FastAPI、Uvicorn、Pydantic | 提供配置、任务、预览、重命名等接口 |
| 任务执行 | asyncio、ThreadPoolExecutor | 扫描和网络请求限流执行 |
| 配置管理 | TOML、tomllib、tomlkit | 读取和保存模型、路径、模板配置 |
| 数据存储 | SQLite、SQLAlchemy 或 SQLModel | 保存媒体源、目录、扫描索引、任务、日志 |
| 网络共享 | SMB/CIFS、NFS、subprocess、pathlib | 支持共享识别、连通性检测和可选挂载 |
| AI 接入 | OpenAI-compatible 适配层、httpx | 默认 DeepSeek，兼容 GPT 等模型 |
| TMDB 匹配 | httpx、RapidFuzz | 直接封装 TMDB API，做候选相似度匹配 |
| Bangumi 匹配 | httpx、RapidFuzz | TMDB 失败后，为动漫文件提供二次匹配 |
| 豆瓣代理匹配 | httpx、RapidFuzz | TMDB 失败后，为国产影视、港台剧提供兜底匹配；代理地址必须可配置 |
| TVDB 匹配 | httpx | TMDB 失败后，为纯海外美剧补充分季、分集和剧集信息 |
| 多语言识别 | 规则识别接口、可选 AI 增强 | 输出语言、地区、媒体类型、题材类型和置信度 |
| 文本清洗与本地解析 | re、unicodedata、RapidFuzz | 标准化文件名、支持中文标题解析、过滤广告文本 |
| Token 预估 | 本地估算器 | 调用 AI 前估算输入、输出和总 token |
| 文件处理 | pathlib、os、shutil | 扫描、路径校验、安全重命名 |
| 日志 | logging、TXT/CSV/JSON 导出 | 页面查看日志，记录操作、失败原因和回滚依据 |
| 插件机制 | Python 接口协议、插件注册表 | 大功能扩展通过插件接口接入，避免核心代码膨胀 |
| 部署 | Docker、Docker Compose、Nginx 可选 | 适配 fnOS/NAS 常驻服务 |

网络共享说明：SMB/CIFS 覆盖 Windows 共享、NAS SMB 共享和常见局域网共享；Linux 的基础共享协议按 NFS 处理。初版需要同时支持“宿主机已挂载后映射到容器”和“程序内调用系统命令挂载”两种模式。Docker 环境中程序内挂载可能需要额外容器权限，默认推荐宿主机先挂载共享目录，再映射给容器。

## 五、推荐目录结构

```text
mediaai-renamer/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── config.py            # 配置读写接口
│   │   │   ├── sources.py           # 媒体源、SMB/NFS 配置和检测接口
│   │   │   ├── directories.py       # 已识别目录管理接口
│   │   │   ├── tasks.py             # 扫描任务和任务状态接口
│   │   │   ├── preview.py           # 预览结果查询和修改接口
│   │   │   └── logs.py              # 日志查询和导出接口
│   │   ├── core/
│   │   │   ├── settings.py
│   │   │   ├── database.py
│   │   │   └── logging.py
│   │   ├── plugins/
│   │   │   ├── base.py              # 插件接口协议
│   │   │   ├── registry.py          # 插件注册表
│   │   │   └── manifests.py         # 插件元数据定义
│   │   ├── models/
│   │   │   ├── media_source.py      # 本地/SMB/NFS 媒体源模型
│   │   │   ├── media_directory.py   # 已识别目录模型
│   │   │   ├── scan_index.py        # 扫描索引和增量扫描标记模型
│   │   │   ├── task.py
│   │   │   ├── media_item.py
│   │   │   └── rename_log.py
│   │   ├── services/
│   │   │   ├── share_detector.py    # Windows/NAS/Linux 共享识别
│   │   │   ├── share_mount.py       # SMB/CIFS、NFS 共享检测和挂载
│   │   │   ├── directory_registry.py # 已识别目录保存和复用
│   │   │   ├── scanner.py
│   │   │   ├── scan_indexer.py
│   │   │   ├── text_filter.py
│   │   │   ├── local_parser.py
│   │   │   ├── token_estimator.py
│   │   │   ├── llm_provider.py
│   │   │   ├── llm_parser.py
│   │   │   ├── language_detector.py # 文件名多语言识别接口
│   │   │   ├── media_classifier.py  # 动漫/国产港台/海外剧分流判断
│   │   │   ├── match_orchestrator.py # TMDB 优先、多源失败分流编排
│   │   │   ├── tmdb_match.py        # 现有 TMDB 主匹配逻辑，初版保持主流程不变
│   │   │   ├── bangumi_client.py    # Bangumi 动漫匹配适配器
│   │   │   ├── douban_proxy_client.py # 豆瓣代理兜底适配器
│   │   │   ├── tvdb_client.py       # TVDB 美剧分集信息适配器
│   │   │   ├── preview_builder.py
│   │   │   └── rename_executor.py
│   │   └── utils/
│   │       ├── filename.py
│   │       └── path_guard.py
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   ├── router/
│   │   ├── stores/
│   │   ├── views/
│   │   │   ├── SettingsView.vue
│   │   │   ├── SourcesView.vue
│   │   │   ├── DirectoriesView.vue
│   │   │   ├── ScanView.vue
│   │   │   ├── AiConfirmView.vue
│   │   │   ├── PreviewView.vue
│   │   │   └── LogsView.vue
│   │   └── main.ts
├── config/
│   └── config.example.toml
├── data/
│   └── mediaai.sqlite3
├── docker/
│   ├── Dockerfile.backend
│   ├── Dockerfile.frontend
│   └── nginx.conf
├── docker-compose.yml
└── README.md
```

## 六、核心数据结构

### 媒体源

```json
{
  "id": 1,
  "name": "客厅NAS影视库",
  "source_type": "smb",
  "share_os_type": "nas",
  "uri": "//192.168.1.10/media",
  "mount_path": "/mnt/mediaai/source-1",
  "container_path": "/media/source-1",
  "credentials_ref": "source_1",
  "mount_mode": "host_mounted",
  "enabled": true,
  "last_scan_mode": "incremental",
  "last_scan_at": "2026-06-23T20:00:00+08:00",
  "has_operation_marks": true
}
```

`source_type` 可取值：`local`、`docker_volume`、`smb`、`nfs`。`share_os_type` 可取值：`windows`、`nas`、`linux`、`unknown`。`mount_mode` 可取值：`host_mounted`、`app_mount`。敏感凭据不得明文返回前端，数据库中只保存加密后凭据或环境变量引用。

### 已识别目录

```json
{
  "id": 10,
  "source_id": 1,
  "path": "/media/source-1/电影",
  "display_name": "电影",
  "directory_type": "movie_library",
  "detected_by": "scan",
  "last_seen_at": "2026-06-23T20:00:00+08:00",
  "last_scan_task_id": 1001,
  "total_media_files": 350,
  "enabled": true
}
```

已识别目录必须持久化保存，后续扫描、增量扫描、目录选择、路径白名单校验都优先使用该表。`directory_type` 可取值：`movie_library`、`series_library`、`mixed_library`、`unknown`。

### 扫描任务

```json
{
  "id": 1001,
  "source_id": 1,
  "directory_id": 10,
  "scan_mode": "incremental",
  "suggested_scan_mode": "incremental",
  "reason": "source has previous operation marks",
  "status": "running",
  "total_files": 1200,
  "changed_files": 80,
  "skipped_unchanged_files": 1120
}
```

`scan_mode` 可取值：`full`、`incremental`。如果媒体源或目录已有扫描索引、已识别目录或操作标记，页面必须提示“检测到上次操作记录，可选择增量扫描；如目录有大量外部变更，建议全量扫描”。

### 扫描索引

```json
{
  "source_id": 1,
  "directory_id": 10,
  "path": "/media/source-1/电影/盗梦空间.2010.mkv",
  "size": 1234567890,
  "mtime": 1782200000,
  "fingerprint": "size:1234567890|mtime:1782200000",
  "last_seen_task_id": 1001,
  "last_operation": "renamed",
  "last_operation_at": "2026-06-23T20:30:00+08:00",
  "operation_mark": "mediaai-processed"
}
```

增量扫描以 `source_id + directory_id + path + size + mtime` 为基础判断文件是否变化。初版不计算完整文件哈希，避免大文件 IO 过重。

### 多语言识别结果

```json
{
  "filename": "孤独的美食家.S01E01.JPN.2012.mkv",
  "primary_language": "ja",
  "secondary_languages": ["zh"],
  "region": "jp",
  "media_type": "series",
  "content_type": "live_action",
  "is_anime": false,
  "is_domestic_cn_hk_tw": false,
  "is_overseas_us_series": false,
  "confidence": 0.78
}
```

`primary_language` 使用 ISO 639 风格的短代码，例如 `zh`、`en`、`ja`、`ko`、`unknown`。`region` 可取值：`cn`、`hk`、`tw`、`us`、`jp`、`kr`、`unknown`。初版先实现中文、英文、中英混合识别，但接口字段必须为后续日文、韩文、多语言混合识别预留。

### 多源匹配结果

```json
{
  "primary_source": "tmdb",
  "final_source": "bangumi",
  "tmdb_status": "failed",
  "fallback_reason": "anime_detected_after_tmdb_failed",
  "external_id": "bangumi:123456",
  "title": "葬送的芙莉莲",
  "original_title": "葬送のフリーレン",
  "year": 2023,
  "season": 1,
  "episode": 1,
  "match_confidence": 0.9,
  "candidates": []
}
```

`primary_source` 固定优先为 `tmdb`。`final_source` 可取值：`tmdb`、`bangumi`、`douban_proxy`、`tvdb`、`manual`、`none`。所有补充源只能在 TMDB 失败或低置信度时使用，不允许绕过 TMDB 主流程。

## 七、本地解析与 AI 调用策略

初版必须坚持“先本地解析，后用户确认 AI”的原则，避免扫描大量文件时直接产生不可控的模型调用成本。

本地解析必须支持中文、英文和中英混合文件名，至少覆盖以下格式：

```text
庆余年.S02E01.2024.mkv
庆余年 第二季 第01集.mp4
长安三万里.2023.2160p.mkv
Inception.2010.BluRay.mkv
The.Last.of.Us.S01E03.2023.mkv
[公众号] 繁花 第01集 1080P.mp4
老友记.S01E01.rmvb
电影名.CD1.dat
VIDEO_TS/VTS_01_1.VOB
```

扫描任务结束后，页面必须单独汇总本地解析失败、低置信度、TMDB 无匹配或多候选差异过小的条目。用户选择全部、部分或不调用 AI。只有用户确认后，后端才允许调用 DeepSeek 或其他模型。

调用 AI 前后端必须返回 token 预估信息，供前端弹窗确认：

```json
{
  "items_count": 42,
  "estimated_input_tokens": 3200,
  "estimated_output_tokens": 2100,
  "estimated_total_tokens": 5300,
  "model": "deepseek-v4-flash"
}
```

初版 token 预估可采用保守本地算法：中文按每字约 1 token 估算，英文按每 4 个字符约 1 token 估算，并额外加入系统提示词和 JSON 输出模板开销。

## 八、配置示例

```toml
[server]
host = "0.0.0.0"
port = 8000

[paths]
data_dir = "/app/data"
mount_root = "/mnt/mediaai"

[[media_sources]]
name = "本地影视目录"
source_type = "local"
path = "/media"

[[media_sources]]
name = "Windows共享"
source_type = "smb"
share_os_type = "windows"
uri = "//192.168.1.50/Videos"
mount_mode = "host_mounted"
container_path = "/media/windows-videos"
username_env = "SMB_USERNAME"
password_env = "SMB_PASSWORD"

[[media_sources]]
name = "NAS影视共享"
source_type = "smb"
share_os_type = "nas"
uri = "//192.168.1.10/media"
mount_mode = "host_mounted"
container_path = "/media/nas-media"
username_env = "NAS_SMB_USERNAME"
password_env = "NAS_SMB_PASSWORD"

[[media_sources]]
name = "Linux NFS影视共享"
source_type = "nfs"
share_os_type = "linux"
uri = "192.168.1.20:/srv/media"
mount_mode = "host_mounted"
container_path = "/media/linux-nfs"

[llm]
provider = "deepseek"
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"
temperature = 0.1
timeout_seconds = 60

[llm.providers.openai]
model = "gpt-4o-mini"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"

[tmdb]
api_key_env = "TMDB_API_KEY"
language = "zh-CN"
region = "CN"

[metadata_sources]
primary = "tmdb"
tmdb_low_confidence_threshold = 0.72
enable_bangumi = true
enable_douban_proxy = true
enable_tvdb = true

[metadata_sources.bangumi]
base_url = "https://api.bgm.tv"
access_token_env = "BANGUMI_ACCESS_TOKEN"

[metadata_sources.douban_proxy]
base_url = ""
api_key_env = "DOUBAN_PROXY_API_KEY"
enabled_regions = ["cn", "hk", "tw"]

[metadata_sources.tvdb]
base_url = "https://api4.thetvdb.com/v4"
api_key_env = "TVDB_API_KEY"
enabled_regions = ["us"]

[language_detection]
enabled = true
default_language = "zh"
supported_languages = ["zh", "en"]
reserved_languages = ["ja", "ko"]

[rename]
movie_template = "{title}.{year}"
movie_full_template = "{title}.{original_title}.{year}.{quality}"
series_template = "{title}.{year}.S{season:02d}E{episode:02d}"
series_full_template = "{title}.{year}.S{season:02d}E{episode:02d}.{episode_title}"
daily_show_template = "{title}.{air_date}"
separator = "."
dry_run_required = true
keep_quality_suffix = true
keep_codec_suffix = true
multi_disc_pattern = "CD{disc_index}"

[scan]
max_workers = 4
default_mode = "incremental"
video_extensions = [
  ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv",
  ".ts", ".m2ts", ".mpeg", ".mpg", ".m4v", ".3gp",
  ".vob", ".dat", ".rm", ".rmvb", ".asf", ".divx",
  ".xvid", ".ogm", ".ogv", ".webm", ".iso", ".strm"
]

[filter]
ad_blacklist = [
  "公众号",
  "字幕组",
  "www\\..+\\.com",
  "磁力链接",
  "网盘分享",
  "高清资源"
]
```

API Key 和共享密码优先从环境变量读取，不建议明文写入配置文件。模型名必须保持可配置，不允许在业务代码中写死。

## 九、识别匹配主流程

初版识别匹配必须遵循固定顺序：

1. 文件名清洗和本地解析。
2. 多语言识别接口输出语言、地区、媒体类型、题材类型和置信度。
3. 调用现有 TMDB 主匹配逻辑，`tmdb_match.py` 保持主流程不变。
4. 如果 TMDB 匹配成功且置信度大于等于配置阈值，采用 TMDB 结果。
5. 如果 TMDB 匹配失败或低置信度，进入分流判断：
   - 动漫文件：调用 Bangumi 接口二次匹配。
   - 国产影视、港台剧：调用豆瓣代理接口兜底。
   - 纯海外美剧：调用 TVDB 补充分季、分集和剧集信息。
6. 如果补充源仍失败，标记为 `manual_required`，由用户在预览页手动处理。

分流判断由 `media_classifier.py` 完成，依据包括文件名语言识别、目录类型、标题关键词、地区线索、TMDB 候选结果和用户手动目录类型。豆瓣代理接口必须通过配置注入 `base_url`，不得在代码中写死代理服务。

## 十、命名输出标准

初版重命名输出必须面向主流媒体刮削器，优先保证 TMDB、Plex、Kodi、Emby、Jellyfin、Infuse、VidHub、影视墙类 NAS 应用可读。默认模板采用点号分隔，保持简单、通用、可配置。

### 前置统一规则

1. 统一分隔符：工具自动将空格、`【】`、`[]`、`()`、`_` 等分隔符替换为英文点号 `.`，不影响刮削解析。
2. 年份必须携带，用于区分同名作品。
3. 剧集季集标准标识使用 `SxxEyy`，两位数字；`S00` 固定表示特辑、OVA、花絮。
4. 文件名禁止包含 `: / \ * ? " < > |` 等系统非法字符。
5. 分辨率、编码、来源信息放在文件名末尾，不干扰刮削匹配。
6. 多碟影片后缀使用 `CD1`、`CD2`。
7. 分隔符和格式控制符一律使用英文字符，包括 `.`, `-`, `()`, `CD1`, `S01E01`, `Season 01`；标准化输出文件名禁止使用中文标点作为结构分隔符。

### 1. 电影 Movie

基础通用模板：

```text
中文片名.英文原名.年份.分辨率.编码.ext
```

最简模板，初版优先推荐：

```text
中文片名.年份.ext
```

多碟 / 分卷电影：

```text
中文片名.年份.CD1.1080p.mkv
```

电影花絮 / 特辑：

```text
中文片名.年份.S00E01.花絮.mkv
```

示例：

```text
流浪地球.2019.2160p.WEB-DL.mkv
奥本海默.Oppenheimer.2023.1080p.mkv
指环王护戒使者.2001.CD2.4K.mkv
```

### 2. 电视剧 TV Series

标准模板：

```text
剧集名.年份.SxxExx.集标题.分辨率.ext
```

文件夹规范：

```text
TV/狂飙(2023)/Season 01/
    狂飙.2023.S01E01.序幕.1080p.mkv
```

正片剧集：

```text
狂飙.2023.S01E08.mkv
```

特别篇 / 花絮 / OVA：

```text
狂飙.2023.S00E01.幕后花絮.mkv
```

日期型综艺 / 纪实，无季，按播出日期：

```text
十三邀.2025-06-20.mkv
```

多集合并文件：

```text
老友记.1994.S01E01-E03.合集.mkv
```

### 3. 动漫 / 番剧 Anime

常规季番模板：

```text
番名.播出年份.SxxExx.标题.720p.mkv
```

示例：

```text
进击的巨人.2013.S01E01.mkv
鬼灭之刃.2019.S00E02.无限列车OVA.mkv
孤独摇滚.2022.S01E01.mkv
```

剧场版归类为电影格式，不使用 `SxxEyy`：

```text
鬼灭之刃无限列车篇.2020.1080p.mkv
```

### 4. 综艺 Variety / Talk Show

季播综艺：

```text
综艺名.年份.SxxExx.ext
乘风破浪.2024.S02E05.mkv
```

日播 / 访谈：

```text
综艺名.YYYY-MM-DD.ext
圆桌派.2025-06-22.mkv
```

综艺特辑 / 衍生：

```text
奔跑吧.2025.S00E01.春节特辑.mkv
```

### 5. 纪录片 Documentary

单部纪录片按电影类命名：

```text
纪录片名.年份.4K.mkv
地球脉动.2006.mkv
```

多季分集纪录片按剧集格式命名：

```text
纪录片名.年份.SxxExx.mkv
人生一串.2018.S01E02.mkv
河西走廊.2015.S01E03.mkv
```

### 6. 迷你剧 / 限定短剧 Mini Series

统一按剧集 `S01` 处理：

```text
漫长的季节.2023.S01E04.mkv
```

### 7. 剧场版 / 影视短片

归类电影，不使用 `SxxEyy`：

```text
隐入尘烟.2022.1080p.mkv
```

### 8. 标准化模板

```toml
movie_template = "{title}.{year}"
movie_full_template = "{title}.{original_title}.{year}.{quality}"
series_template = "{title}.{year}.S{season:02d}E{episode:02d}"
series_full_template = "{title}.{year}.S{season:02d}E{episode:02d}.{episode_title}"
daily_show_template = "{title}.{air_date}"
```

### 9. 禁止和不推荐命名

1. 只写集数无 `SxxEyy` 标识：`狂飙01`。
2. 年份缺失：`流浪地球.mkv`。
3. 大量特殊符号：`【狂飙】第01集(2023)`。
4. 混用多种分隔符：`狂飙 S01_E01`。
5. 花絮 / OVA 使用 `S01` 而非 `S00`。

### 10. 文件夹标准目录结构

```text
MediaRoot/
├── Movies/
│   ├── 流浪地球(2019)/
│   │   └── 流浪地球.2019.2160p.mkv
├── TV/
│   ├── 狂飙(2023)/
│   │   ├── Season 00/
│   │   └── Season 01/
├── Anime/
│   └── 进击的巨人(2013)/
```

初版可以先只执行文件重命名，不强制移动目录；但预览数据结构必须同时给出 `target_name` 和可选 `target_relative_path`，为后续整理目录结构预留能力。文件夹名也应尽量符合标准。

## 十一、后端 API 设计

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/config` | 读取当前配置 |
| PUT | `/api/config` | 保存配置 |
| GET | `/api/health` | 服务健康检查 |
| GET | `/api/sources` | 读取媒体源列表 |
| POST | `/api/sources` | 新增本地、SMB/CIFS、NFS 媒体源 |
| PUT | `/api/sources/{source_id}` | 修改媒体源配置 |
| POST | `/api/sources/{source_id}/detect` | 识别 Windows、NAS、Linux 共享类型 |
| POST | `/api/sources/{source_id}/test` | 测试媒体源连通性和权限 |
| POST | `/api/sources/{source_id}/mount` | 可选程序内挂载 SMB/CIFS 或 NFS |
| POST | `/api/sources/{source_id}/unmount` | 可选卸载程序内挂载的共享 |
| GET | `/api/directories` | 读取已识别目录列表 |
| POST | `/api/directories/scan` | 扫描并保存可识别媒体目录 |
| PUT | `/api/directories/{directory_id}` | 修改目录类型、启用状态和显示名 |
| GET | `/api/sources/{source_id}/scan-suggestion` | 根据历史标记返回建议扫描模式 |
| POST | `/api/tasks/scan` | 创建扫描任务，必须包含 `source_id`、`directory_id`、`scan_mode` |
| GET | `/api/tasks/{task_id}` | 任务状态和进度 |
| GET | `/api/tasks/{task_id}/preview` | 读取预览结果 |
| GET | `/api/tasks/{task_id}/language-detection` | 查看文件名语言、地区和类型识别结果 |
| GET | `/api/tasks/{task_id}/match-results` | 查看 TMDB 和补充源匹配结果 |
| GET | `/api/tasks/{task_id}/ai-candidates` | 读取需要 AI 确认解析的条目 |
| POST | `/api/tasks/{task_id}/ai-estimate` | 估算选中条目的 AI token 用量 |
| POST | `/api/tasks/{task_id}/ai-parse` | 用户确认后调用 AI 解析选中条目 |
| POST | `/api/tasks/{task_id}/rename` | 执行已选项重命名 |
| GET | `/api/tasks/{task_id}/logs` | 读取任务日志 |
| GET | `/api/tasks/{task_id}/export.txt` | 导出 TXT 日志 |
| GET | `/api/tasks/{task_id}/export.csv` | 导出 CSV 日志 |
| GET | `/api/tasks/{task_id}/export.json` | 导出 JSON 日志 |

## 十二、插件机制设计

后续新增较大的功能必须优先通过插件接口实现，只有稳定、通用、必须常驻的能力才合并进核心。插件机制的目标是让元数据源、文件名识别、命名模板、STRM 生成、通知、导出格式等能力可插拔扩展。

### 插件类型

```text
metadata_provider    # 元数据源插件，例如 Bangumi、TVDB、豆瓣代理
filename_parser      # 文件名解析插件，例如日文、韩文、罗马音识别
rename_template      # 命名模板插件，例如特殊媒体库模板
share_provider       # 共享协议插件，例如 WebDAV、Alist、SFTP
strm_generator       # STRM 生成插件，二期预留
exporter             # 导出插件，例如 TXT、CSV、JSON、Excel
notifier             # 通知插件，例如 Webhook、企业微信、Telegram
```

### 插件接口原则

1. 插件只能通过公开上下文对象访问任务、配置、日志和数据库，不允许直接绕过路径白名单操作文件。
2. 插件必须声明 `plugin_id`、`name`、`version`、`plugin_type`、`capabilities`。
3. 插件配置必须存储在独立命名空间，例如 `[plugins.bangumi]`。
4. 插件失败不得导致主任务崩溃，必须返回结构化错误。
5. 插件输出必须经过 Pydantic 校验后才能进入预览和重命名流程。
6. 对外网络插件必须支持超时、重试、限流和禁用开关。

### 插件接口草案

```python
class MetadataProviderPlugin:
    plugin_id: str
    plugin_type: str = "metadata_provider"

    async def match(self, query, context):
        """Return normalized metadata candidates."""

class FilenameParserPlugin:
    plugin_id: str
    plugin_type: str = "filename_parser"

    async def parse(self, filename, context):
        """Return parsed media info with confidence."""

class StrmGeneratorPlugin:
    plugin_id: str
    plugin_type: str = "strm_generator"

    async def generate(self, items, context):
        """Reserved for future STRM generation."""
```

初版内置的 Bangumi、豆瓣代理、TVDB 适配器应按 `metadata_provider` 插件形态设计，即使代码暂时放在核心仓库，也要遵守同一接口，便于后续外置。

## 十三、前端页面设计

### 1. 设置页

- DeepSeek 默认模型配置。
- OpenAI-compatible 自定义配置。
- TMDB API Key 状态检查。
- 电影和剧集重命名模板配置。
- 广告关键词配置。

### 2. 媒体源页

- 新增本地目录、Docker 挂载目录、Windows SMB 共享、NAS SMB 共享、Linux NFS 共享。
- 显示共享类型、共享地址、容器路径、挂载方式、连通性状态。
- 支持共享类型自动识别和手动修正。
- 支持读写权限测试。

### 3. 已识别目录页

- 展示程序已识别并保存的媒体目录。
- 支持按媒体源筛选目录。
- 支持设置目录类型：电影库、剧集库、混合库、未知。
- 支持启用、停用、重新识别目录。
- 后续扫描优先从已保存目录中选择。

### 4. 扫描页

- 选择已保存媒体源和已识别目录。
- 根据历史扫描索引和操作标记，提示用户选择全量扫描或增量扫描。
- 全量扫描说明：重新遍历当前目录，刷新扫描索引。
- 增量扫描说明：只处理新增、修改或未处理文件，跳过未变化文件。
- 展示扫描进度、文件数量、本地解析成功数、需 AI 确认数、失败数、跳过数量、变化文件数量。

### 5. AI 解析确认页

- 展示本地无法解析或低置信度文件列表。
- 支持逐条勾选、全选、跳过。
- 点击“估算 token”后展示选中条目的 token 预估。
- 用户确认后才调用 DeepSeek 或其他已配置模型。

### 6. 预览页

- 使用 Element Plus 表格展示预览项。
- 支持勾选、全选、反选、只选 ready 项。
- 显示原文件名、新文件名、媒体类型、语言/地区识别、最终匹配源、TMDB 匹配状态、置信度、风险状态。
- 对 Bangumi、豆瓣代理、TVDB 兜底匹配结果显示来源标识，避免用户误以为全部来自 TMDB。
- 支持手动编辑标题、年份、季、集、新文件名。
- 冲突和低置信度项高亮显示。

### 7. 日志页

- 页面内展示扫描日志、AI 调用日志、TMDB 匹配日志、重命名执行日志。
- 展示重命名成功、失败、跳过原因和错误详情。
- 支持按任务、状态、关键词筛选日志。
- 支持导出 TXT、CSV 和 JSON。

## 十四、完整业务流程

1. 用户在 fnOS/NAS 中启动 Docker 服务。
2. 浏览器访问 Web 界面。
3. 用户配置 DeepSeek API Key、TMDB API Key。
4. 用户新增本地、Windows SMB、NAS SMB 或 Linux NFS 媒体源。
5. 后端识别共享来源类型，执行连通性和权限测试。
6. 用户扫描媒体源中的目录，程序保存已识别目录。
7. 用户从已识别目录中选择一个目录创建扫描任务。
8. 页面根据历史索引和操作标记提示选择全量扫描或增量扫描。
9. 后端扫描媒体文件，识别新旧视频格式和 `.strm` 文件。
10. 后端执行文本清洗和本地解析，优先解析中文、英文和中英混合文件名。
11. 本地解析失败或低置信度条目进入 `needs_ai_confirm` 列表。
12. 用户在页面查看待 AI 解析列表，决定是否调用 AI。
13. 用户选择条目后，后端估算 token 并返回页面展示。
14. 用户确认后，后端调用 DeepSeek 或其他已配置模型进行结构化解析。
15. 后端调用多语言识别接口，输出语言、地区、媒体类型、题材类型和置信度。
16. 后端优先调用现有 TMDB 主匹配逻辑，`tmdb_match.py` 的主流程保持不变。
17. 如果 TMDB 匹配成功且置信度达标，直接采用 TMDB 结果。
18. 如果 TMDB 匹配失败或低置信度，按分流规则调用补充源：动漫走 Bangumi，国产影视/港台剧走豆瓣代理，纯海外美剧走 TVDB。
19. 后端生成预览项并保存到 SQLite，记录 `primary_source`、`final_source` 和兜底原因。
20. 用户在预览页检查、勾选、修正。
21. 用户确认执行重命名。
22. 后端再次检查冲突和路径合法性，然后逐条重命名。
23. 重命名成功后，后端更新扫描索引、已识别目录统计和操作标记。
24. 用户下次扫描同一目录时，页面提示选择全量或增量扫描。
25. 用户在日志页查看日志，并可导出 TXT、CSV、JSON。

## 十五、安全与兼容规范

1. 预览阶段不得修改任何文件。
2. 执行重命名前必须基于最新文件系统状态重新检查目标路径。
3. 同名冲突必须阻止执行，不允许覆盖文件。
4. Windows 保留名、非法字符、尾随空格和尾随点必须清理。
5. Linux/macOS 路径长度和权限错误必须记录。
6. 大小写重命名需要使用临时文件名中转，避免大小写不敏感文件系统失败。
7. `.strm` 文件只允许重命名文件本身，不读取、不写入文件内容。
8. `.iso`、`.vob`、`.dat` 等老格式只按文件名处理，不解析媒体内容。
9. 单条失败不得中断整个批次。
10. 每条操作必须记录原路径、目标路径、执行时间、结果和错误信息。
11. 媒体源和已识别目录必须作为路径白名单，禁止通过 API 操作白名单外文件。
12. SMB/CIFS 和 NFS 凭据不得明文返回前端，日志不得输出密码。
13. 程序内挂载共享时，挂载点必须限制在配置的挂载根目录下。
14. 增量扫描不得仅依赖文件名，必须同时比较路径、大小和修改时间。
15. 重命名成功后必须更新扫描索引，记录旧路径、新路径和操作标记。
16. 下次扫描同一媒体源或目录时，如存在历史扫描或操作标记，必须提示用户选择全量扫描或增量扫描。
17. AI 调用必须由用户主动确认，禁止扫描任务自动批量调用 AI。
18. AI 调用前必须展示 token 预估，用户取消时不得发起模型请求。
19. TMDB 必须作为主匹配源，Bangumi、豆瓣代理、TVDB 只能作为失败或低置信度后的补充源。
20. 豆瓣代理接口不得写死具体第三方地址，必须由用户配置并自行承担可用性。
21. 多语言识别接口输出必须可审计，预览页需展示识别语言、地区和最终匹配源。
22. 重命名输出不得生成只有本工具可识别的私有命名格式，默认模板必须兼容主流刮削器。
23. 初版不得新建 `.strm` 文件；只允许识别、保护和重命名已有 `.strm` 文件。
24. 插件不得绕过路径白名单、用户确认、AI token 预估和 dry-run 预览流程。
25. 标准化输出中的结构分隔符必须使用英文字符，禁止使用中文标点作为命名结构。

## 十六、Docker 部署方案

```yaml
services:
  mediaai-backend:
    build:
      context: .
      dockerfile: docker/Dockerfile.backend
    environment:
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - TMDB_API_KEY=${TMDB_API_KEY}
      - SMB_USERNAME=${SMB_USERNAME}
      - SMB_PASSWORD=${SMB_PASSWORD}
    volumes:
      - ./config:/app/config
      - ./data:/app/data
      - /path/to/media:/media
      # 如果使用宿主机挂载的 SMB/NFS 目录，将宿主机挂载路径映射到容器即可
      # - /mnt/smb-media:/media/smb-media
      # - /mnt/nfs-media:/media/nfs-media
    ports:
      - "8000:8000"
    restart: unless-stopped

  mediaai-frontend:
    build:
      context: .
      dockerfile: docker/Dockerfile.frontend
    ports:
      - "8080:80"
    depends_on:
      - mediaai-backend
    restart: unless-stopped
```

fnOS/NAS 部署时，推荐优先在宿主机系统或 NAS 管理界面挂载 SMB/NFS，再把宿主机挂载路径映射到容器 `/media/...`。如果需要容器内程序主动挂载 SMB/NFS，需要额外授予容器挂载权限，部署复杂度更高。Web 访问地址为 `http://NAS-IP:8080`。

## 十七、初版依赖建议

### backend/requirements.txt

```txt
fastapi
uvicorn[standard]
pydantic
pydantic-settings
httpx
sqlalchemy
tomlkit
rapidfuzz
python-multipart
```

Python 3.11+ 内置 `tomllib` 可用于读取 TOML；由于初版需要在 Web 页面保存配置，因此同时使用 `tomlkit` 写入配置文件。

如果启用容器内程序主动挂载 SMB/CIFS 或 NFS，共享挂载能力依赖系统工具，不是 Python 包。Linux 镜像或宿主机需要安装 `cifs-utils`、`nfs-common`，并根据部署方式授予容器必要的挂载权限。默认推荐宿主机先挂载共享目录，再通过 Docker volume 映射给应用。

## 十八、推荐开发顺序

1. 创建后端 FastAPI 骨架和健康检查接口。
2. 创建 SQLite 数据模型：媒体源、已识别目录、扫描索引、任务、媒体预览项、重命名日志。
3. 实现插件基础接口、插件注册表和插件配置命名空间。
4. 实现配置加载和环境变量覆盖。
5. 实现媒体源配置、Windows/NAS/Linux 共享识别、SMB/CIFS 和 NFS 连通性测试。
6. 实现可选共享挂载服务和挂载状态检测。
7. 实现已识别目录扫描、保存、复用和路径白名单校验。
8. 实现新旧视频格式扫描服务。
9. 实现全量扫描和增量扫描索引服务。
10. 实现重命名后的操作标记更新。
11. 实现中文、英文、中英混合文件名本地解析。
12. 实现文件名多语言识别接口，初版支持中文、英文、中英混合。
13. 实现媒体类型分流判断：动漫、国产影视/港台剧、纯海外美剧。
14. 保留现有 TMDB 主匹配逻辑，封装 `match_orchestrator.py` 做 TMDB 优先编排。
15. 按插件接口实现 Bangumi 动漫匹配适配器。
16. 按插件接口实现豆瓣代理兜底适配器，代理地址配置化。
17. 按插件接口实现 TVDB 美剧分季分集信息适配器。
18. 实现主流刮削器友好的电影/剧集命名模板。
19. 实现文件名清洗、模板渲染、非法字符处理。
20. 实现待 AI 解析列表和 token 预估服务。
21. 实现 OpenAI-compatible LLM 适配层，默认 DeepSeek。
22. 实现用户确认后的 AI 解析服务和 Pydantic 校验。
23. 实现预览生成服务，展示语言、地区、主匹配源、最终匹配源和兜底原因。
24. 实现安全重命名执行器和日志记录。
25. 实现 TXT、CSV、JSON 日志导出。
26. 实现前端设置页。
27. 实现前端媒体源页和已识别目录页。
28. 实现前端扫描页，包含全量/增量扫描选择提示。
29. 实现前端 AI 解析确认页。
30. 实现前端预览页。
31. 实现前端日志页和导出功能。
32. 编写 Dockerfile 和 docker-compose.yml。
33. 在本地 Docker 环境验证端到端流程。

## 十九、二期扩展方向

- 本地 GGUF 离线模型。
- 通过 `strm_generator` 插件生成 `.strm` 文件，并支持从网盘直链、挂载路径或外部链接批量生成。
- 定时任务和后台自动刮削。
- 多用户登录和权限控制。
- 独立桌面客户端。
- 更丰富的影视元数据管理。
- 前端移动端适配。
- 扫描任务队列和断点续跑。
- WebDAV、Alist、SFTP 等新共享协议通过 `share_provider` 插件实现。
- 新元数据源、新语言识别、新导出格式优先通过插件实现。
