# M8 命名规则高级配置二期设计手册

## 1. 阶段定位

M8 在 M2 命名预览、M4 TMDB 回填、M6 AI 回填和 M7 标题识别模式基础上，继续增强命名规则配置能力。阶段目标不是改变重命名主流程，而是让命名模板可测试、可导入导出、可版本化，并让历史预览能感知模板版本变化。

## 2. 当前基线

截至 2026-07-08 设计核对，代码中已存在以下基础能力，后续仍需按 M8 验收清单统一验证：

- 系统设置中已有电影和剧集命名模板配置。
- 命名模板配置已包含版本号和更新时间字段。
- 后端已有命名模板导出、导入、测试和差异预览接口。
- 前端命名规则区域已有规则测试、差异预览、导入、导出和版本展示入口。
- 命名预览表已记录生成时使用的模板版本快照，历史预览可展示当前、旧模板或版本未知状态。

## 3. 范围

本阶段包含：

- 命名模板版本管理。
- 模板导入导出。
- 规则测试和差异预览。
- 双语命名表达。
- 命名预览与模板版本联动。

本阶段不包含：

- 多用户模板协作。
- 模板审批流。
- 按媒体源绑定独立模板版本。
- 完整历史回滚体系。
- 新增外部元数据站点。

## 4. 数据设计

### 4.1 命名模板配置

命名模板继续存储在 `system_settings` 中，核心配置项：

- `naming.movie_template`
- `naming.movie_template_version`
- `naming.movie_template_updated_at`
- `naming.episode_template`
- `naming.episode_template_version`
- `naming.episode_template_updated_at`

旧配置缺少版本字段时按 `v1` 兼容处理。

### 4.2 命名预览模板版本快照

`rename_previews` 已扩展模板版本快照字段：

- `naming_template_type TEXT`：生成时使用的模板类型，取值 `movie` 或 `episode`。
- `naming_template_version INTEGER`：生成时使用的模板版本号。
- `naming_template_updated_at TEXT`：生成时模板配置更新时间。

迁移要求：

- 旧库字段缺失时自动补齐。
- 历史预览默认字段为空，页面展示为版本未知。
- 不批量重算旧预览，不覆盖用户编辑的目标文件名。

## 5. 后端接口设计

### 5.1 模板导出

```http
GET /api/settings/naming/export
```

返回当前电影和剧集模板、版本号、更新时间和结构版本。

### 5.2 模板导入

```http
POST /api/settings/naming/import
Content-Type: application/json

{
  "raw_text": "{\"schema_version\":1}"
}
```

导入时必须校验 JSON 结构、模板字段和媒体类型。非法模板不得覆盖当前配置。

### 5.3 规则测试

```http
POST /api/settings/naming/test
Content-Type: application/json

{
  "media_type": "movie",
  "template": "[{\"key\":\"title\",\"label\":\"标题\",\"variable\":\"title\"}]",
  "separator": ".",
  "keep_year": true,
  "sample": {
    "title": "黑客帝国",
    "year": 1999,
    "extension": ".mkv"
  }
}
```

返回生成结果、字段命中信息、格式告警和当前模板版本。

### 5.4 差异预览

```http
POST /api/settings/naming/diff
Content-Type: application/json
```

请求结构与规则测试一致。返回当前模板输出、候选模板输出、差异字段和模板版本。

### 5.5 命名预览列表扩展

`GET /api/rename-previews` 返回模板版本状态字段：

- `naming_template_type`
- `naming_template_version`
- `naming_template_updated_at`
- `current_naming_template_version`
- `is_naming_template_outdated`
- `naming_template_status`

推荐状态：

- `current`：预览基于当前模板生成。
- `outdated`：预览基于旧模板生成。
- `unknown`：历史记录未保存模板版本。

## 6. 服务逻辑

生成命名预览时：

1. 解析媒体类型。
2. 根据媒体类型读取当前模板和版本信息。
3. 使用现有命名构建器生成目标文件名。
4. 写入 `rename_previews` 时同时写入模板版本快照。

重新生成命名预览时：

1. 保持现有手动编辑保护。
2. 未确认覆盖前，不改写 `edited_name`。
3. 覆盖生成时写入新的模板版本快照。

候选回填时：

1. TMDB、IMDb、AI 和人工候选仍走现有回填逻辑。
2. 如果候选确认导致目标名重新构建，应更新或保留模板版本状态，不能让状态变为空。
3. 外部匹配失败不影响模板版本状态。

## 7. 前端交互

系统设置页：

- 命名规则区域展示模板版本、更新时间、导入、导出、规则测试和差异预览。
- 测试和差异预览只影响弹窗结果，不写正式预览表。

重命名预览页：

- 当前模板生成的记录不额外提示。
- 旧模板生成的记录展示短标签“旧模板生成”，完整说明放 tooltip。
- 未记录模板版本的历史记录展示“版本未知”。
- 状态标签不得挤压操作列，150% 浏览器缩放下仍应可操作。

## 8. 测试要求

后端：

- 数据库迁移测试：旧库补齐模板版本快照字段。
- 预览生成测试：新预览写入模板版本。
- 历史兼容测试：旧预览缺少版本时仍能列表展示。
- 候选回填测试：TMDB、AI 或人工候选回填后模板版本状态不丢失。

前端：

- API 类型和调用测试。
- 规则测试结果展示字段命中和格式告警。
- 旧模板、当前模板、未知版本三种展示状态。
- 150% 缩放下操作列和状态标签不互相遮挡。

验证命令：

```powershell
npm.cmd run backend:test
npm.cmd run frontend:test
npm.cmd run frontend:build
npm.cmd run check:encoding
git diff --check
```
