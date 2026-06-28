# 2026-06-28 项目 Skill 拆分记录

## 目标

将 MediaAI Renamer 的开发过程拆解为可复用的项目本地 Codex skills，先服务本项目，后续根据使用效果迁移到全局 skills 复用到类似项目。

## 完成内容

- 新增 `.codex/skills/mediaai-docs-lifecycle/`：
  - 覆盖需求分析、需求设计、需求拆解、需求补充、设计文档、用户手册、验收清单、验收报告、README、工作日志和阶段归档。
- 新增 `.codex/skills/mediaai-frontend-ui/`：
  - 覆盖 Vue 前端页面、Element Plus 控件、Pinia、前端 API、表格、弹窗、主题、侧边栏、国际化和 UI 优化。
- 新增 `.codex/skills/mediaai-backend-api/`：
  - 覆盖 FastAPI 接口、service 逻辑、SQLite migration、共享协议、扫描、重命名、TMDB、系统设置、加密、校验和后端测试。
- 新增 `.codex/skills/mediaai-test-release/`：
  - 覆盖测试验证、构建、打包、版本号、Git 流程、GitHub Release、发布说明和阶段发布。
- 每个 skill 均补充 `references/`，记录项目路径、关键文件、命令和工作边界。
- 更新 `docs/development/development-guide.md`，新增项目本地 skill 使用规范。

## 验证记录

- 已执行 `quick_validate.py` 校验四个 skill，结果均为 `Skill is valid!`。

## 后续

- 后续实际开发中发现流程缺口时，直接更新对应 skill。
- 稳定后可迁移到 `~/.codex/skills/`，并根据类似项目抽象掉 MediaAI 专属路径。
