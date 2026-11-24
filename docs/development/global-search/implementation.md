# 本地全局搜索实现与验收

类型：实施与验证记录。核对日期：2026-10-30。开发版已接入，未发布、未更新NAS，不包含CD2历史搜索扩展。

## 契约

`POST /api/search/query`，必须Bearer登录。请求体：

```json
{"query":"中文电影","kind":"file","limit":10,"beforeId":null}
```

`kind`为`all/source/scan/file/preview/background`；limit 1–50，页面固定10。beforeId为正SQLite整数或null，仅单类可传。返回：

```json
{"groups":[{"kind":"file","items":[{"kind":"file","id":21,"title":"中文电影.mkv","subtitle":"电影","status":"indexed","sourceId":1,"scanJobId":3,"requestId":null}],"hasMore":true,"nextBeforeId":21}],"networkAccess":false}
```

`GET /api/search/records/{kind}/{recordId}`：重读相同投影，返回`{"item":{...},"networkAccess":false}`。不存在、已清理或无权读均404。不接受服务端跳转URL；前端从分类与ID生成固定站内列表路径。后台任务重读并核对当前会话后打开当前页抽屉，原地址不变；后台任务组件仍兼容已有的 `background_request_id` 链接。

输入先边缘去空白、NFC归一化，2–128字符；拒绝Unicode控制/格式/代理等C类字符。百分号、下划线、反斜线作为普通字符转义。`#正整数`转为主键精确查询。中文子串和SQLite默认ASCII大小写口径，不支持任意FTS语法。

400无效参数、401无会话、403返回前会话权限变化、404详情不可用、422请求类型/长度错误、503预算或存储暂不可用。错误不含搜索词、SQLite原异常、路径、秘密或命中内容。访问日志只有固定POST路径；不主动记录搜索词。

## 数据与授权

服务为`backend/app/service/global_search_service.py`，路由为`backend/app/api/global_search.py`，组件为`frontend/src/components/GlobalSearch.vue`，复用apiClient与sessionGeneration。

媒体源/扫描/结果/预览沿用现有已登录可读规则；本任务不借写权限反推读取权限。后台任务额外限制actor_id为本人，并按任务kind校验现有动作权限，不借task:manage读其他人。返回前重新解析原Bearer及其当前权限。CD2范围需固定来源及授权链，尚不接入。

无新表、索引、ORM或数据库版本调整。项目实际使用sqlite3，以下是参数化SQL与服务调用，不引入ORM示例冒充现有实现：

```sql
SELECT s.id, s.file_name AS title, m.name AS subtitle
FROM media_files s JOIN media_sources m ON m.id=s.media_source_id
WHERE s.file_name LIKE ? ESCAPE '\' AND s.id < ?
ORDER BY s.id DESC LIMIT ?;
```

```python
from app.service.global_search_service import searchLocal
result = searchLocal(settings, authenticatedUser, "中文电影", kind="file", limit=10, beforeId=22)
```

前端调用示例，真实Bearer由现有拦截器提供：

```ts
import { searchLocal } from "./api/globalSearch";
const controller = new AbortController();
const groups = await searchLocal("中文电影", "file", null, controller.signal);
```

各分类ID倒序，无全库COUNT；LIMIT只约束结果数量，不作为扫描代价上限。只读事务用于单次一致视图，跨页不冻结快照。后台任务的input/result/error、媒体源凭据、配置及完整路径均不选择；标题/摘要/状态输出各最多512字符。

## 容量与取消

- 只读mode=ro、query_only事务；锁等待150ms；每进程同时最多2个搜索，槽满立即503。不是跨进程或全实例限流。
- 所有分类共享600ms或300万SQLite VM步预算，每1000步检查；不是硬实时截止，也不等于每条磁盘操作都能中断。超限整次503，无部分结果/计数泄漏。
- 客户端300ms防抖，超时2500ms，取消旧请求并用代次/会话/用户权限防回填。取消HTTP不证明SQLite立即停止，仍靠服务端预算和槽约束。关闭/隐藏清理查询；不自动重试，不保留跨用户搜索缓存。
- 无索引子串命中可能全表读取。20万合成文件、24,338,432字节数据库：最近命中4.00ms、无命中71.09ms、五类`#1`精确查询3.43ms。本机单轮，不是容量SLA或百万级并发保证。
- EXPLAIN为`SCAN s`、`SEARCH m USING INTEGER PRIMARY KEY (rowid=?)`；预算控制而非索引证明有界。若真实库频繁503，再独立设计可迁移的本地索引与中文检索，不自动放宽预算或触达云端。

## 验证

- 后端7项定向测试：认证、投影、字面转义、中文/NFC、注入、后台拥有者及动作权限、游标、详情消失、预算/并发503、返回前停用用户。临时库与禁止联网桩，不操作运行库。
- 前端搜索API/组件、App与后台任务重开共11项相关测试，完整76文件650项通过；类型/构建通过，既有500kB产物提示保留。会话中途变化、隐藏、撤权、迟到回包及失效详情有针对性验证；同一路由原编号也能显式重开后台任务。
- 浏览器使用临时库的真实FastAPI检索接口，其他页面接口合成；7个场景覆盖搜索、分类/游标、详情、列表跳转、字面特殊字符、空态、ESC和关闭。CSS视口1536×764、1920×1080、1366×768、1024×400。亮暗校正后结果以最终日志为准，旧失败不能冒称最终证据。
- 原始证据位于`.codex/test-artifacts/search-*20261029*`（本机私有，不打包/推送合成会话资料）。此前1538项后端基线不覆盖本次新增模块；为遵循日常精简验证，没有重复运行30分钟整仓后端。

未验证：真实超大本地库、高并发/多进程、实体级CD2检索、非ASCII大小写折叠、读屏全流程与用户实机缩放；无发布或NAS更新。
