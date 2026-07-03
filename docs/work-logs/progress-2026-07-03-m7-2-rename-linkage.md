# M7-2 重命名链路后端闭环

日期：2026-07-03

版本：0.7.3

## 完成内容

- 数据库 schema 升级到 12，为 `rename_previews` 增加标题来源追踪字段：
  - `title_source`
  - `parent_folder_title`
  - `recognition_mode`
  - `title_conflict_message`
- 重命名预览支持上级文件夹标题兜底：
  - 文件名已识别出标题时保持原解析结果。
  - 文件名缺少标题但含季集信息时，使用上级文件夹名称补全标题，并保留季号、集号。
  - API 返回标题来源字段，供后续前端展示。
- 新增手动排除预览项后端能力：
  - `POST /api/rename-previews/{preview_id}/exclude`
  - 写入 `pending_files`，预览项标记为 `excluded`。
  - 默认预览列表隐藏 `excluded`，显式按状态查询仍可查看。
- 真实重命名成功后同步增量扫描索引：
  - 源路径索引标记为 `renamed`。
  - 目标路径索引写入为 `active`。
  - 索引同步失败不会回滚已完成的文件重命名，仅记录操作项提示。

## 验证

- `npm.cmd run backend:test`：164 项通过。

## 后续

- 前端接入手动排除按钮。
- 设置页接入上级文件夹识别模式选择。
- 批量 AI 匹配和 TMDB 到 AI 的二次匹配串联仍在 M7 后续阶段。
