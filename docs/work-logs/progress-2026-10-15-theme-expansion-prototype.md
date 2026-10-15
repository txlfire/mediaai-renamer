# 六主题隔离原型开发记录

类型：实施与验证记录。日期：2026-10-15。当前阶段：TE-P1原型与限定验证完成，待用户视觉确认；未进入产品推广。

## 范围与检查点

用户明确“启动开发，不用评估了”，直接按已确认扩展计划执行，不重复启动评估。开发基线develop/5b38aa3、版本1.2.0，有前两轮未提交改动；产品src、入口和包元数据已取SHA256快照，不能还原既有修改。

- [原型已完成，待视觉确认] 六主题十二态隔离原型，复用真实工作台与合成Axios数据；所有未知/写入请求拒绝。
- [完成限定验证] 新外观预览单选、独立模式、长列表滚动、低高度回退及键盘。
- [完成] 实际浏览器截图、颜色/可达性、独立文件无网络与产品未改校验。
- [待确认] 用户看具体原型后确认，再进入TE-P2/TE-P3产品实施；不把开发启动当作具体原型已经获批。

入口为[扩展计划](../development/ui-refresh/theme-expansion-plan.md)；上一轮收尾与日间规划分别保留，不覆盖历史结论。使用mediaai-frontend-ui及mediaai-test-release；UI路由器只读尝试仍未安装，不新增依赖。标准脚本status显示5173/8970均stopped，已调用项目start恢复，不依据旧PID起停活跃服务。

## 第一批检查点

- 原型放在.codex/test-artifacts/theme-expansion，构建为无外部资源的theme-preview.html；comparison.html以十二张实际工作台截图内嵌生成。复用真实App/重命名/设置等组件，Axios适配器拒绝所有未知读取及写入。没有改正式store三ID、产品CSS或启动脚本。
- 既有主题色值由PostCSS读取当前产品声明，叠加本地候选；同一生成声明同时用于根与主题示意，不在卡片里复制HEX。构建时排除模式别名，仅提取基础值和操作色，防止生成块把暗态别名覆盖成亮态。新三主题覆盖完整配对角色，原三套只微调分区，contrast保留原值。
- 琥珀亮态为保证主操作/链接可读，强调色推导为较深蜂蜜色，明亮金黄用于选择层；暗态主按钮采用金黄配深色字。来源原色不是必须原样用于正文或按钮，具体调整留待用户看原型确认。
- 十二态首轮及收拢组件后的复跑均12/12通过，0页面异常/0HTTP请求/0未配置读取；对8类操作正常/hover×3行背景×12态，共576颜色对至少4.589:1，正文/说明/按钮/选择至少4.5:1，输入边界至少3:1。这是原型取样，不是产品/WCAG全覆盖。
- 类型检查首次因预览缺vite/client样式声明和两个隐式any失败；补本地env声明和显式事件类型后，vue-tsc预览工程检查通过。没有放松strict或改产品类型配置。
- 第一轮交互33项仅8通过：12个模式点击失败是脚本点到隐藏原生input而非可见标签；长列表关闭/窄宽问题是原型布局，原生fieldset内部尺寸导致内容撑开，改为有名称radio组配可收缩div，Popover/Dialog共享ThemeChoices；不复制两套内容。刷新写拒绝对照需要先明确点击模拟确认框，首轮漏确认不当作出口失效。失败日志保留，修正后继续复跑。
- 第二轮25/33：列表与关闭已可达；390/340窗口仍因原型focusSelected的scrollIntoView滚动全部祖先，实际body横移-335px，不能把列表自身宽度相等当作页面通过。改为只计算列表内scrollTop、focus preventScroll及Popover fixed策略/防溢出，不靠隐藏溢出达标。诊断390窗口打开后body x=0、浮层x=12/宽366，根scrollWidth375<=390；标准产品源码未改。
- 跟随系统的失败属于隔离壳与真实App旧三主题监听竞争：本地候选根属性被旧store回写default。原型在下一绘制前补候选值；正式接入注册表后必须去掉这段原型覆盖/rAF协调，不把临时兼容带入产品。写拒绝对照补模拟确认，仍不放行任何真实请求。

## 终态与交接

- 最新交互33/33通过，页面异常0、HTTP请求0；覆盖十二态键盘即时切换、入口相邻Shift+Tab、Enter/Space/方向键、hover不切换、ESC焦点返回，18个视口/列表长度组合、system变化、外部点击不抢焦点、全屏ESC层级和刷新恢复。最后一轮失败仅脚本把确认框写成alertdialog，依据本地组件实际role=dialog改正后通过；没有修改业务确认流程。
- 写拒绝正向对照实际点击“生成预览”及模拟“确定”，适配器捕获POST /api/rename-previews/generate并拒绝，0HTTP请求；不以完全不点按钮的零请求替代出口拒绝验证。该错误属于预期隔离行为，不是正式服务失败。
- 18个尺寸/长度组合为1920×980、1366×668、1280×620、390×844、340×640、800×360分别配6/12/24项。常规Popover的列表clientWidth等于scrollWidth，24项scrollHeight1436/clientHeight384；340窄宽单列scrollHeight2876/client384；低高度Dialog的列表clientHeight170，顶部关闭始终命中。现有窄屏工作台自身的隐藏/裁切不由此宣称完整移动端验收；本批窄宽结论只针对新选择器及根级不新增横滚。
- 选择器6/12/24项分别约102/180/336个后代元素，低高度无重复标题时96/174/330；没有渲染多套真实表格。只记录DOM量，不声明长任务/全部帧率已量测或TE13完整通过。
- 最新十二态12/12复跑通过、576操作颜色对通过、0页面异常/0HTTP；截图取浮层入场结束后，移除选择器模式按钮的颜色过渡，避免切换主题时短暂混色。实际查看桌面海盐青、琥珀暗态、390长列表及800×360对话框截图。
- 最新命令：Node24.15.0运行build-preview.mjs、verify-prototype.mjs、verify-interactions.mjs、build-comparison.mjs和verify-delivery.mjs；vue-tsc --noEmit --project .codex/test-artifacts/theme-expansion/tsconfig.json通过。对应日志theme-expansion-build-verified、theme-expansion-twelve-state-verified、theme-expansion-interactions-verified、theme-expansion-types-verified、theme-expansion-delivery，均在.codex/run-logs中以2026-10-15.log结尾；失败日志不删除。
- 对照页12张图片全部加载、六主题段落存在、链接能打开正确海盐青交互页并打开选择器，0异常/0HTTP；delivery-results.json及delivery-picker.png为实际验证证据。theme-preview.html和comparison.html是可直接本机打开的独立文件，无需原型服务器，不依赖Color Hunt在线资源。
- 产品src、入口和三份包元数据SHA256快照与开工前一致。未改正式主题/白名单、Skill、依赖、版本、数据库、API或实际凭据，没有提交/推送/发布；上一轮未提交修改完整保留。产品未变，本批没有机械运行前端/后端全量，不拿之前251等计数冒充本轮结果。
- 标准服务已按脚本恢复且保持5173/8970运行；后续不依据旧PID起停。只等具体视觉确认后进入TE-P2注册表/正式选择器与TE-P3配色；产品1008场景、实机4K组合缩放和TE-P4回归仍未执行。
- 本批文档收尾：当前8份变更/新增Markdown共36处本地引用检查通过，编码检查及git diff --check通过；这些总数包含前两轮文档。计划、实施入口与验收分别注明“原型完成，产品未接入”，没有把具体视觉确认写成已获批准。标准后端及前端代理健康1.2.0/ok，任务所需测试进程均已结束，服务继续运行。
