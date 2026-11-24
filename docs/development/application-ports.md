# 应用端口设置

类型：设计与验收。2026-11-23 确认：Web 系统设置可修改本地开发前后端端口，校验后手动重启生效；Docker 只显示访问入口及部署管理说明，不修改宿主映射。

## 1. 配置、生效和回退

| 配置 | 范围 | 缺省 |
| --- | --- | --- |
| 本地前端端口 | 整数 TCP 1024–65535，不和后端相同。供 Vite 监听。 | 5173，兼容现有开发入口。 |
| 本地后端端口 | 整数 TCP 1024–65535，不和前端相同。供 FastAPI 监听，Vite 的 `/api` 代理随之更新。 | 8970，兼容现有开发入口。 |

使用已有 SQLite `system_settings`，一个实例级键 `server.local_dev_ports` 保存两端口及修订号；不改表结构。不能沿用热更新入口：监听进程须在启动前读取同一份设置，Web 只写待生效值，不在服务进程内重启。没有已保存值时表单显示当前启动值，允许原样保存；保存之前不会改变下次启动的缺省。保存前拒绝新端口不可绑定、非法或重复；端口可用性可能在重启前变化，启动失败应保留配置并报告，不强杀占用目标端口的其他程序。

Windows 与 Linux 项目启动入口共用同一读取逻辑。优先级：单次显式脚本参数或 Linux 显式环境变量 → 数据库中本地待生效端口 → 原有缺省值。显式覆盖不写数据库。数据库文件位置仍按既有应用配置确定；首次启动库或设置表不存在回到原缺省值，已保存值损坏或无法读取时不静默回退，必要时显式传两项端口恢复入口。配置库外的 PID/监听记录只用于核对已启动实例，不参与决定下次端口。

Docker、反代及直接运行不受开发启动器管理的实例均为部署模式：不保存本地待应用端口；对外地址已由容器端口映射或反向代理提供。HTTPS 代理和证书不由这个入口修改。设置页显示当前浏览器访问地址，不推断镜像宿主映射或 TLS 配置。

启动前的绑定校验与开发启动范围一致。单次开发启动可设置 `MEDIAAI_DEV_LOOPBACK_ONLY=1` 仅监听 127.0.0.1，用于本地隔离验证；未设置仍兼容已有局域网开发模式。该启动环境开关不写库、不由 Web 编辑。

## 2. 接口

`GET /api/settings/service-ports` 与 `PUT /api/settings/service-ports` 需 `settings:write`。开发模式返回 `active` 当前对、`desired` 待生效对、`revision`、`restartRequired`；部署模式仅返回模式。PUT 只接收两个端口与 GET 返回的修订号。发生修订冲突先重新读取；写入与审计同事务，不自动重试，不触发远程请求。

```ts
const { data: current } = await apiClient.get("/settings/service-ports");
if (current.mode === "local-dev") {
  await apiClient.put("/settings/service-ports", {
    frontendPort: 6061, backendPort: 9070, expectedRevision: current.revision,
  });
}
```

存储直接使用 SQLite 参数绑定，无 ORM 层。[dev_port_service.py](../../backend/app/service/dev_port_service.py) 读取使用 `SELECT value FROM system_settings WHERE key = ?`，经过修订检查后按同一个键更新 `system_settings`，并借用同一事务记录 `audit_events`。仅随应用设置操作生效，无 SQL 升级脚本；恢复时通过设置页保存先前的端口，或在启动时显式临时覆盖。

## 3. 验收与排除

- 端口未配置、已保存、部分/完整覆盖、无权限、配置被并发更改、Docker 拒写、目标占用及旧入口回退均用本地合成配置验证。
- Web 验证显示当前和待生效、保存不重启、更改后新启动读取配置且 API 代理健康；开发流程不要求重启现有用户实例。
- 不修改 Docker/Compose、NAS、防火墙、证书或云执行配置。生产访问地址变更须走原部署管理。
