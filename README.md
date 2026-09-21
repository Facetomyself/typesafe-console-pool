# TypeSafe Console Pool

本仓库提供 [TypeSafe Console](https://console.typesafe.ai/) 的 FastAPI 号池服务：导入 Outlook 四参数邮箱并写入 SQLite 维护、经 Cliproxy sticky 代理注册账号、按本地风控参数控制节奏与保号，并管理号池的查询、租出、归还与 JSON 导出。

仓库：<https://github.com/Facetomyself/typesafe-console-pool>

浏览器自动化单次脚本在 [typesafe-console](https://github.com/Facetomyself/typesafe-console)；HTTP 协议还原在 [typesafe-console-protocol](https://github.com/Facetomyself/typesafe-console-protocol)。本仓把注册做成可排队的控制面，默认走 automation 后端。

## 范围

| 项 | 说明 |
|----|------|
| 发布面 | FastAPI 号池（邮箱导入入库、健康检查、Cliproxy lease、注册/保号队列、账号 checkout） |
| 默认注册后端 | `automation`：ruyipage Firefox 151，启动时注入 Cliproxy sticky URL |
| 保号 | 同一账号 profile + 同一地区 sticky 再打开 console；到期由 `keepalive_due_at` 入队 |
| 风控账本 | 本地参数（间隔、日上限、邮箱冷却、sticky）；分数是本机记账，不是挑战绕过声明 |
| 协议后端 | `protocol`：当前固定 `register_blocked` / keepalive blocked，等协议仓冻请求形状后再接 |
| 登录方式 | 邮箱一次性验证码（`Email me a code instead`） |
| 邮件确认 | 最小 IMAP XOAUTH2 轮询（INBOX，必要时 Junk） |
| 注册结果 | 写入本地 JSON 文件，不写 `.env` |
| 代理 | Cliproxy sticky：显式两位地区码 + 每任务 SID；拒绝 `Rand` |

本仓库不覆盖 TypeSafe SDK 集成。HTTP API 调用以官方文档为准：<https://docs.typesafe.ai/api.md>。

## 邮箱文件

导入接口读取 Outlook txt。每行四个字段，以 `----` 分隔：

```text
email----password----client_id----refresh_token
```

| 字段 | 说明 |
|------|------|
| `email` | Outlook 邮箱地址，例如 `name@outlook.com` |
| `password` | 占位字段，注册流程不使用 |
| `client_id` | Microsoft Entra 公共客户端 ID |
| `refresh_token` | 已授权的 refresh token，用于换取 IMAP 访问令牌 |

空行、`#` 注释行以及首行 `卡密导出` 会被跳过。可一次导入多行。`client_id` 与 `refresh_token` 写入 gitignored 的 `data/secrets.json`，列表接口只返回邮箱与维护字段。

文件导入后按 `email` 在 SQLite 里 upsert：新行 `ready` / `health=unknown`；已存在且非 `in_use` 的行更新 `source` 与 secrets，冷却与失败计数清零。已绑定账号的邮箱保持 `bound`。同一邮箱不会因再次导入变成另一条记录。

## Cliproxy

注册流量走 Cliproxy sticky lease。凭证从本机 `D:\reverse_ENV\storage\proxy-usage\.env` 读取（`CLIPROXY_ACCOUNT` 或 `CLIPROXY_USER` / `CLIPROXY_PASS`），不进本仓库。

约束：

- 地区必须是显式两位字母，例如 `US`。`Rand` / `RANDOM` 直接拒绝。
- SID 只允许 `[A-Za-z0-9_]`。
- `sticky_minutes` 范围 1–120。
- 入队时生成 SID 并落库；worker 按已存 SID **重建**代理 URL，不会另开一条 sticky。

可选 `CLIPROXY_PRE_PROXY` 由配置读取，当前注册后端把 sticky URL 直接交给 Firefox `quick_start(proxy=...)`。

## 风控参数与保号

无感通过是环境、出口 IP 与行为节奏的合分，本仓只记账这三维，不把「IP 好坏」当成单一归因。

默认策略写在 `risk_policy` 表（`GET/PATCH /v1/risk/policy`）：

| 参数 | 默认 | 作用 |
|------|------|------|
| `register_min_interval_seconds` | 180 | 两次注册入队最短间隔 |
| `register_daily_cap` | 40 | 自然日注册入队上限 |
| `mailbox_fail_cooldown_seconds` | 900 | IMAP/token 失败后冷却 |
| `mailbox_max_fail` | 3 | 超限后 `health=dead`，邮箱 `disabled` |
| `keepalive_interval_hours` | 24 | 注册成功后下次保号到期 |
| `keepalive_jitter_minutes` | 90 | 到期时间右偏偏移 |
| `sticky_minutes` | 30 | 任务 sticky 时长 |
| `checkout_requires_healthy_mailbox` | true | 租出要求绑定邮箱不是 cooling/dead |
| `reject_rand` / `region_sticky` | true | 拒绝 Rand，同账号复用注册地区 |

保号方式：

1. 注册成功后邮箱变为 `bound`，账号记下 `region` 与 `keepalive_due_at`。
2. `POST /v1/jobs/keepalive` 对该账号开任务；worker 用 `account-{id}` profile 打开 console，已登录则结束，否则走同一套邮箱 OTP。
3. `POST /v1/jobs/keepalive/due` 扫描到期账号批量入队。
4. 失败把邮箱打成 `cooling`（已绑定则仍记 `bound` + `health=cooling`），token 失效记 `dead`。

`risk_events` 记录导入、检查、入队、成功与失败。这些分数只描述本机节奏是否按策略执行。

## 运行

依赖本机 `reverse_ENV` 的 Python venv。服务默认监听 `127.0.0.1:8091`。

```powershell
$py = "D:\reverse_ENV\.venv\Scripts\python.exe"
$proj = "D:\reverse_ENV\workspace\typesafe-console-pool"

& $py -m pip install -e "$proj[dev]"
& $py -m uvicorn app.main:app --app-dir $proj --host 127.0.0.1 --port 8091
```

OpenAPI：`http://127.0.0.1:8091/docs`。

运行前确认：

- Cliproxy `.env` 已配置且地区不是 Rand。
- Firefox 路径为 `D:\reverse_ENV\tools\ruyipage\runtimes\151-proxy\firefox\firefox.exe`。
- 本机已有四参数 `mailbox.txt`（已忽略，不进 Git）。

首期 `queue_workers=1`。本机 `data/config.json` 可改监听端口、默认地区、sticky 时长与后端名，该文件已忽略。

## HTTP API

所有业务响应为 `{ "ok", "data", "error" }`。请求体 `extra=forbid`。未知字段返回 `422` / `validation_error`。

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/health` | 进程存活 |
| GET | `/ready` | Cliproxy 已配置且 worker 在跑 |
| GET | `/v1/meta` | 版本、默认后端、地区、sticky、worker 数 |
| GET | `/v1/risk/policy` | 当前风控参数 |
| PATCH | `/v1/risk/policy` | 改间隔、日上限、冷却、保号周期 |
| GET | `/v1/risk/events` | 风控事件，可选 `kind` / mailbox / account |
| POST | `/v1/mailboxes/import` | JSON：`path` 或 `text` 二选一，可选 `source` |
| GET | `/v1/mailboxes` | 列表，可选 `status`、`health` |
| GET | `/v1/mailboxes/stats` | 邮箱池计数 |
| POST | `/v1/mailboxes/check` | 批量 IMAP 探活，`limit` 1–100 |
| GET | `/v1/mailboxes/{id}` | 单条 |
| PATCH | `/v1/mailboxes/{id}` | `ready` / `disabled`、`notes`、`clear_cooldown` |
| POST | `/v1/mailboxes/{id}/check` | 单条探活 |
| GET | `/v1/proxy/status` | 脱敏代理快照 |
| POST | `/v1/proxy/leases` | 预开 sticky lease |
| GET | `/v1/proxy/leases` | 列表，可选 `state` |
| POST | `/v1/proxy/leases/{id}/release` | 释放 |
| POST | `/v1/proxy/leases/{id}/rotate` | 换新 SID |
| POST | `/v1/jobs/register` | 入队注册（可指定 mailbox / region / backend） |
| POST | `/v1/jobs/register/batch` | 批量入队，`count` 1–20 |
| POST | `/v1/jobs/keepalive` | 对已注册账号入队保号 |
| POST | `/v1/jobs/keepalive/due` | 扫描到期账号入队 |
| GET | `/v1/jobs` | 列表，可选 `status`、`backend`、`type` |
| GET | `/v1/jobs/{id}` | 单条（含 lease） |
| POST | `/v1/jobs/{id}/cancel` | 取消 queued/running |
| POST | `/v1/jobs/{id}/retry` | 对 failed/blocked/cancelled 重新入队 |
| GET | `/v1/accounts` | 号池列表 |
| GET | `/v1/accounts/stats` | 计数 |
| GET | `/v1/accounts/export` | 写出 `data/exports/account-*.json` |
| GET | `/v1/accounts/{id}` | 单条 |
| POST | `/v1/accounts/checkout` | 租出 `registered` 账号 |
| POST | `/v1/accounts/{id}/release` | 归还为 `registered` |
| POST | `/v1/accounts/{id}/disable` | 停用 |
| POST | `/v1/controls/pause` | 暂停新注册 |
| POST | `/v1/controls/resume` | 恢复 |

账号状态：`registered` ↔ `leased`；另有 `disabled`、`retired`。邮箱状态：`ready` / `in_use` / `bound` / `cooling` / `disabled`。邮箱健康：`unknown` / `ok` / `cooling` / `dead`。任务类型：`register` / `keepalive`。任务状态：`queued` / `running` / `succeeded` / `failed` / `cancelled` / `blocked`。

导入示例：

```powershell
Invoke-RestMethod http://127.0.0.1:8091/v1/mailboxes/import -Method POST -ContentType "application/json" -Body '{"path":"D:\\path\\to\\mailbox.txt"}'
Invoke-RestMethod http://127.0.0.1:8091/v1/jobs/register -Method POST -ContentType "application/json" -Body '{"region":"US","backend":"automation"}'
```

导出文件形状：

```json
{
  "email": "name@outlook.com",
  "status": "registered",
  "registered_at": "2026-09-21T00:00:00Z",
  "console": "https://console.typesafe.ai/",
  "region": "US",
  "last_keepalive_at": null,
  "keepalive_due_at": "2026-09-22T01:30:00Z"
}
```

## 仓库结构

```text
app/main.py              FastAPI 入口
app/api/v1.py            HTTP 路由
app/store.py             号池、邮箱库、风控账本
app/services/risk.py     默认策略与冷却计算
app/services/cliproxy.py sticky lease / rebuild
app/services/mailbox.py  四参数 txt
app/services/otp.py      IMAP XOAUTH2 与探活
app/backends/            automation / protocol / keepalive
tests/                   解析、代理约束、API
LICENSE                  MIT
```

Public 仓只跟踪说明、源码、测试、许可证和忽略规则。邮箱 txt、Cliproxy 凭证、refresh token、验证码、SQLite、secrets 和浏览器 profile 留在本机。

## 测试

```powershell
$py = "D:\reverse_ENV\.venv\Scripts\python.exe"
& $py -m pytest "D:\reverse_ENV\workspace\typesafe-console-pool\tests" -q
```

## 许可

MIT License。见 [LICENSE](./LICENSE)。
