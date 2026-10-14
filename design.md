# Design — MediaAI Renamer

文档类型：界面设计执行入口。目标确认日期：2026-10-13；原型视觉方向确认日期：2026-10-14。

品类为数据密集型媒体整理工作台，不是营销页。详细目标以[完整主题设计](docs/design/workbench-theme-design.md)为准；系统与业务边界见[系统设计](docs/design/project-design.md)。本文件约束目标设计，不证明源码已经实现；当前差异、兼容与进度见[实施计划](docs/development/ui-refresh/implementation-plan.md)，测试与实际缩放见[验收清单](docs/development/ui-refresh/acceptance-checklist.md)。

## System

- 工作台保持现有导航、路由、权限、查询和安全文件操作流程。
- 三套完整主题：default清爽专业、candy柔和彩色、contrast精密高对比；各自支持亮暗。
- 界面差异覆盖画布、侧栏、表头、输入和选择状态，不只替换按钮强调色。
- 共享Vue 3、Element Plus、Pinia与公共CSS，不替换组件库或引入营销页结构。
- 具体颜色以获确认的原型及运行时CSS为准，不在本文件复制第二份真值。

## Theme：显示模式 × 完整主题

| 轴 | 状态 | 职责 |
| --- | --- | --- |
| 显示模式 | system / light / dark | system解析为light或dark，选择对应语义别名及浏览器color-scheme |
| 视觉主题 | default / candy / contrast | 提供每套亮暗的背景、面板、侧栏、文本、边框、表格、输入、按钮、语义及材质基础值 |

两个选择独立，主题取值与显示模式共同决定最终颜色。主题可以改变中性色，不再限制为只换强调色；六种视觉组合共享一套组件规则。

- 根节点沿用data-scheme与data-palette，不增加第三个选择轴。
- 外观沿用mediaai-theme-mode与mediaai-theme-palette，两项独立保存在当前浏览器。
- system只改变显示模式，切换主题不改变业务状态、筛选、权限或会话。
- 未知主题回退default；theme切换不发送外部请求，不触发文件操作。

## Token Ownership

- frontend/src/styles.css为唯一运行时取值源；不另建tokens.css复制全部变量。
- 主题声明亮暗基础值，模式解析语义别名；组件只使用语义变量。
- 复用--app-bg、--panel-bg、--sidebar-bg、--text-main、--text-muted、--border-color、--table-*、--input-bg、--brand-accent、--brand-accent-ink、--semantic-*与--action-*。
- 品牌与语义的现有light/dark变量名继续复用；新增基础值按语义命名，不按颜色命名。
- 禁止按主题复制页面CSS，禁止[data-palette="..."] .el-*组件分支；主题差异必须落到共享变量。
- Element Plus背景、文字、边框、填充、禁用、遮罩、浮层及固定列与公共变量保持一致。
- hover/active派生色也需要验证，不能假设混色天然达标。
- 默认值提供完整回退，但正式主题必须声明完整角色，不能依赖上一主题残留。

## Layout And Typography

- 页面标题与说明采用紧凑无框头部；查询、命令和数据分区有清楚层级，不把页面区块层层包成卡片。
- 表格、弹窗和确需框定的工具可保留边界；列表改造复用ListPageLayout，不新增第二套布局。
- 字号固定阶梯：页面标题20px、区块标题16px、正文/控件14px、说明13px；不按viewport连续缩放。
- 中文系统无衬线继续使用，不为避免模板感强行换字体，不新增在线字体依赖。
- 间距采用4px阶梯，优先8/12/16/24px；几何尺寸不随主题、hover或loading跳动。
- 普通控件与重复项圆角不超过8px，6px为基线；独立状态点允许圆形。
- 数字列使用等宽数字。长内容可重排、换行或截断提示，不通过全站缩字规避缩放问题。

## Controls And Feedback

- 图标使用@element-plus/icons-vue，不用表情符号代替功能图标。
- 表格操作为纯图标、无边框背景、居中、有语义颜色及可访问名称；保留可见键盘焦点。
- 查询、局部操作、批量操作与危险操作范围清楚；重命名拆分下拉及解析标题选择保留原行为。
- AI未就绪就地说明原因，不恢复突兀整页告警；权限不足和外部提交保护不得绕过。
- 加载、空、错误、失败和未知结果分别表达，不仅靠颜色，不将未知受理当成功。
- 同一反馈不在字段、就地结果、toast与弹窗重复出现。
- 长表格只在内部横滚，操作列可见；Provider当前和操作两列固定右侧。
- 弹窗按视口约束与内容用途确定，不强制所有窗口统一尺寸；内部滚动时必要关闭、确认动作可达。

## Motion And Readability

- 克制使用短时transform/opacity反馈，不做逐行入场、装饰性弹跳、大面积模糊或持续背景动画。
- 焦点即时出现，不添加动画；支持prefers-reduced-motion。
- 六种组合的正文、状态文字、品牌文字和表格动作分别满足项目4.5:1门禁；焦点和必要边界目标至少3:1。
- 检查实际背景上的正常、hover、active及选择状态；禁用项保持可识别并提供必要原因。
- 实际主屏门禁为3840×2160、Windows200%、浏览器125%；CSS视口模拟不得代替实机结果。
- 页面根级不得横向溢出，工作区纵向可见；不能只检查横向坐标就判断按钮可用。

## Tool Use

- 优先现有项目Skill和共享实现；已安装不等于当前启用。
- improve-ui仅诊断有证据的设计偏差，产出本地design-plans提案；新目标不能伪装成旧契约违规。
- 可访问性由专项检查与实际键盘/颜色验证负责，不能由普通UI报告无发现推导合规。
- 外部风格配方只作参考；不得覆盖本工作台字号、字体、布局、图标和业务约束。
- Hallmark营销Hero、页脚、截图导览和差异化页面风格规则不适用；不以纯白、纯黑、系统字体判模板缺陷。
- 三套主题共享系统，不鼓励每页另选设计。无必要收益不安装MCP、Bun、UI或动画依赖。

## Design And Evidence

- 目标设计：[完整主题设计](docs/design/workbench-theme-design.md)。
- 实施、兼容、工具与停止条件：[实施计划](docs/development/ui-refresh/implementation-plan.md)。
- 六态、视口、状态和交付门禁：[验收清单](docs/development/ui-refresh/acceptance-checklist.md)。
- 旧强调配色的历史证据：[原审核与前端收尾](docs/work-logs/progress-2026-10-13-review-closeout.md)。该证据不证明新的完整主题已验收。
- 获批准的三套方向由styles.css登记实际色值；验收记录取样与比值，README和手册区分已落地源码与已发布制品。
