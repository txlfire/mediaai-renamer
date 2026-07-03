# M7-1 增量扫描后端实现记录

日期：2026-07-03

## 本阶段范围

- `run_full_scan` 增加 `scan_mode` 参数，兼容 `full` 和 `incremental`。
- 全量扫描写入 `scan_file_index`，保留历史文件指纹。
- 增量扫描对未变化文件计入 `skipped_count`，不重复写入 `media_files`。
- 增量扫描可识别新增、修改和缺失文件，并写入任务统计。
- 低于最小扫描阈值的视频继续进入待处理列表，并在索引中标记为 `ignored`。
- `/api/scan-jobs` 创建任务请求增加 `scan_mode`，未传时默认 `full`。

## 版本

- 开发版本由 `0.7.1` 升级为 `0.7.2`。

## 验证

- `npm.cmd run backend:test`：通过，160 项测试。

## 后续

- M7-2 继续处理重命名成功后的索引转移、上级文件夹标题识别和手动排除到待处理列表。
