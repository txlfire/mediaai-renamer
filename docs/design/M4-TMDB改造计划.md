# M4-TMDB V4 令牌接口兼容改造方案

## 1. 需求目标

1. 在原有 TMDB 配置基础上，新增 TMDB V4 只读访问令牌配置项。
2. 调用优先级：优先使用 V4 令牌调用新版接口；令牌为空自动降级为 V3 API 密钥旧接口。
3. 前端引导用户优先填写 V4 令牌，保留 V3 密钥作为备用降级方案。
4. 连接测试和业务调用时，前端明确展示当前使用的是 V4 还是 V3，以及各自的成功/失败状态。
5. 存量配置完全兼容，原有刮削业务逻辑无改动。

## 2. 前端页面改造

### 2.1 配置项顺序调整

1. 首行配置项：**TMDB V4 只读访问令牌（推荐优先配置）**
   - 增加灰色小字备注：优先配置令牌，调用 V4 新版接口，连接更稳定。
   - 仅调整输入框宽度，左右内边距收缩，标签与单位文字垂直位置保持不变，不上下位移。
2. 第二行保留原有配置项：TMDB V3 API 密钥（备用降级方案）
3. 移除所有输入框末尾多余的「默认值」文字。

### 2.2 文案说明

新增全局提示文字：

> 调用优先级：系统优先使用 V4 令牌；令牌未填写时，自动降级使用 V3 API 密钥。

### 2.3 表单控件约束

1. V4 令牌输入框：单行长文本输入，不做字符格式校验（JWT 格式允许字母、数字、下划线、连字符和点号）。
2. V3 密钥：保留原有字符格式校验逻辑不变。
3. 所有原有下拉框、数字输入框位置、垂直排版保持原样，只修改新增项。

### 2.4 连接测试结果展示

1. 点击「测试连接」按钮后，后端分别测试 V4 和 V3 两个通道。
2. 前端以列表或分行的形式展示每个通道的测试结果：
   - **V4 连接：** 成功 ✓  /  失败 ✗（附原因）
   - **V3 连接：** 成功 ✓  /  失败 ✗（附原因）
3. 明确告诉用户当前最终生效的是哪个通道（「当前生效：V4」或「当前生效：V3」）。
4. 测试结果不采用 ElMessage 单行提示，改用对话框、Alert 或行内结果区域展示，以便同时呈现两条结果。

### 2.5 业务调用状态展示

在命名预览页面的元数据状态列或提示气泡中，展示此次匹配使用的通道：

- "TMDB V4" — 走 V4 令牌成功匹配
- "TMDB V3" — V4 失败降级后走 V3 密钥成功匹配
- "TMDB V3（仅密钥）" — 用户未配置 V4 令牌，直接走 V3
- "TMDB 失败" — 两个通道都失败

## 3. 后端数据库改造

### 3.1 存储方式

`system_settings` 表已是 key-value 结构，无需新增列或字段。新增一行：

| key | value | category | description | sensitive |
| --- | --- | --- | --- | --- |
| `tmdb.v4_token` | 用户填写的令牌 | tmdb | TMDB V4 Access Token | 是 (1) |

与原有 `tmdb.api_key` 相互独立存储，互不覆盖。

### 3.2 配置生效规则

1. 配置支持热保存，修改后即时生效，无需重启服务。
2. 配置持久化写入数据库。

### 3.3 令牌解析校验

V4 token 是 JWT 格式（含 `.` 和 `-` 等字符），不能复用 V3 API key 的 `_parse_secret`（含正则 `[^\w.\-]` 过滤）。在 `settings_service.py` 中新增独立的 `_parse_token` 函数：

```python
def _parse_token(value: object) -> str:
    """宽松校验，仅做长度和空值检查，不限制字符格式。"""
    parsed = str(value).strip()
    if parsed and len(parsed) > 1024:
        raise ValueError("令牌长度超出限制")
    return parsed
```

`SETTING_DEFINITIONS` 中 `tmdb.v4_token` 的 `value_type` 设为 `"token"`，在 `_parse_value` 中增加 `"token"` 分支映射到 `_parse_token`。

### 3.4 返回模型

后端新增连接测试的响应模型：

```python
class TmdbConnectionTestResult(BaseModel):
    v4: dict[str, object]  # {"status": "success" | "skipped" | "failed", "message": str}
    v3: dict[str, object]  # {"status": "success" | "skipped" | "failed", "message": str}
    effective: str          # "v4" | "v3" | "none"
```

## 4. 接口调用逻辑改造

### 4.1 优先级策略（固定顺序）

1. 若 `tmdb.v4_token` 不为空 → 启用 V4 接口模式
2. 若 `tmdb.v4_token` 为空 → 自动切换 V3 旧接口模式

### 4.2 V4 请求规则

- 请求头自动携带鉴权：`Authorization: Bearer {tmdb_v4_token}`
- URL 不再拼接 `api_key` 参数，完全使用新版鉴权方式。

### 4.3 TmdbClient 改造示例

当前 `_get_json` 把 `api_key` 拼入 URL query。改造后根据 `v4_token` 是否为空选择鉴权路径：

```python
class TmdbClient:
    def __init__(self, api_key: str = "", v4_token: str = "",
                 language: str = "zh-CN", region: str = "CN",
                 timeout_ms: int = 10000):
        self.api_key = api_key
        self.v4_token = v4_token
        self.language = language
        self.region = region
        self.timeout_seconds = max(1, timeout_ms / 1000)

    def _get_json(self, path: str, params: dict[str, object]) -> dict[str, Any]:
        base_params = {"language": self.language, "region": self.region}
        base_params.update(
            {k: v for k, v in params.items() if v is not None and v != ""}
        )
        headers = {"Accept": "application/json",
                   "User-Agent": "MediaAI-Renamer/0.3"}

        if self.v4_token:
            headers["Authorization"] = f"Bearer {self.v4_token}"
            query = urlencode(base_params)
        else:
            base_params["api_key"] = self.api_key
            query = urlencode(base_params)

        request = Request(f"{TMDB_API_BASE_URL}{path}?{query}", headers=headers)
        # 异常处理逻辑不变...
```

### 4.4 降级逻辑位置（metadata_service.py）

降级逻辑放在 `match_metadata_candidates` 中，而非 TmdbClient 内部。先尝试 V4，失败后静默重试 V3：

```python
def match_metadata_candidates(settings, parsed, provider=None):
    effective = get_effective_settings(settings)
    if not effective.get("tmdb.enabled"):
        return _failed("TMDB 未启用")

    v4_token = str(effective.get("tmdb.v4_token") or "").strip()
    api_key = str(effective.get("tmdb.api_key") or "").strip()
    used_channel = None  # 记录实际使用的通道

    if v4_token:
        client = TmdbClient(v4_token=v4_token, ...)
        try:
            candidates = client.search(parsed)
            used_channel = "v4"
        except Exception as exc:
            logger.warning("V4 请求失败，降级至 V3: %s", exc)
            if not api_key:
                return _failed(f"TMDB 搜索失败 (V4: {exc})，无 V3 密钥可降级")
            client = TmdbClient(api_key=api_key, ...)
            try:
                candidates = client.search(parsed)
                used_channel = "v3"
            except Exception as exc2:
                return _failed(f"TMDB 搜索失败 (V4+V3): {exc2}")
    elif api_key:
        client = TmdbClient(api_key=api_key, ...)
        try:
            candidates = client.search(parsed)
            used_channel = "v3_only"
        except Exception as exc:
            return _failed(f"TMDB 搜索失败: {exc}")
    else:
        return _failed("未配置 TMDB V4 令牌且无 V3 API 密钥")

    matches = sorted(...)
    summary = MetadataMatchSummary(status=matches[0].status, message=None, matches=matches)
    # 记录通道信息供前端展示
    summary.metadata_source = used_channel
    return summary
```

### 4.5 异常容错策略

1. V4 出现 401 鉴权失败、接口超时、网络异常、限流报错时，静默自动重试 V3 接口。
2. 降级过程不弹出前端报错，不中断当前刮削任务。
3. 仅在后台日志记录降级行为，不影响业务流程。
4. 两个模式都失败时才返回错误。

### 4.6 连接测试（test_tmdb_connection）

`settings_service.py` 中 `test_tmdb_connection` 明确返回两个通道的测试结果：

```python
def test_tmdb_connection(settings) -> dict:
    effective = get_effective_settings(settings)
    v4_token = str(effective.get("tmdb.v4_token") or "").strip()
    api_key = str(effective.get("tmdb.api_key") or "").strip()

    v4_result = {"status": "skipped", "message": "未配置 V4 令牌"}
    v3_result = {"status": "skipped", "message": "未配置 V3 API 密钥"}
    effective_channel = "none"

    # 测试 V4
    if v4_token:
        client = TmdbClient(v4_token=v4_token, ...)
        try:
            client.test_connection()
            v4_result = {"status": "success", "message": "V4 连接成功"}
            effective_channel = "v4"
        except Exception as exc:
            v4_result = {"status": "failed", "message": f"V4 连接失败: {exc}"}

    # 测试 V3
    if api_key:
        client = TmdbClient(api_key=api_key, ...)
        try:
            client.test_connection()
            v3_result = {"status": "success", "message": "V3 连接成功"}
            if effective_channel == "none":
                effective_channel = "v3"
        except Exception as exc:
            v3_result = {"status": "failed", "message": f"V3 连接失败: {exc}"}

    # 如果 V4 失败但 V3 成功，当前生效通道修正为 V3
    if v4_result["status"] == "failed" and v3_result["status"] == "success":
        effective_channel = "v3"

    return {
        "v4": v4_result,
        "v3": v3_result,
        "effective": effective_channel,
    }
```

### 4.7 前端连接测试回调

`testTmdbConnection` 收到后端返回后，用对话框或页面内 Alert 展示：

```
V4 连接： 成功 ✓
V3 连接： 未配置
当前生效： V4
```

或

```
V4 连接： 失败 ✗（HTTP 401）
V3 连接： 成功 ✓
当前生效： V3
```

## 5. 存量兼容性保障

1. 历史用户已保存的 V3 API 密钥完整保留，不会被清空覆盖。
2. 未配置 V4 令牌的旧环境，程序继续执行原有 V3 逻辑，实现无缝升级。
3. 双配置相互独立，支持同时保存令牌+密钥两套内容。

## 6. 原有功能保留项

1. 多站点调度、匹配度打分算法保持不变。
2. 文件过滤、双列表分流、待处理文件运维功能无修改。
3. 扫描任务、命名预览、表单宽度优化等已完成 UI 项不受本次迭代影响。

## 7. 测试覆盖要求

1. 单元测试：`test_tmdb_metadata_service.py` 新增 V4 token 搜索成功用例。
2. 单元测试：V4 token 无效（401/超时）时自动降级 V3 并搜索成功的用例。
3. 单元测试：V4+V3 都失败时返回 failed 状态的用例。
4. 单元测试：`_parse_token` 对含 `.`、`-` 字符的 JWT 不做非法字符拦截。
5. 验收测试：页面排版中 V4 令牌输入框排在首位、提示文案可见、V3 密钥在第二行。
6. 验收测试：连接测试结果分别展示 V4 和 V3 两个通道的状态，并指明当前生效通道。
7. 验收测试：保存后调用优先级为 V4 → V3 降级，日志记录降级行为。
8. 回归测试：存量 V3 用户配置不受影响，走原逻辑不变。

## 8. 验收标准

1. 页面排版：V4 令牌输入框排在首位，标注推荐配置，V3 密钥放在第二行。
2. 存储：令牌与密钥分别独立入库，配置热更新生效。
3. 调用顺序：优先走 Bearer 令牌 V4；鉴权失败自动静默降级 V3。
4. 连接测试：返回 V4 和 V3 两个通道各自的成功/失败状态，并指明当前生效通道。
5. 业务调用：命名预览页面展示此次匹配使用的通道来源。
6. 存量数据无丢失，原有刮削流程正常运行无中断。
7. 页面仅调整新增输入框宽度，其他控件垂直位置不发生偏移。
8. 测试用例全部通过。
