# 泰华日志系统适配说明

## 1. 适配范围

目标系统为公司内网的泰华日志系统：

```text
http://10.10.50.101
```

本适配属于 AgentBridge 第二系统一期。它复用现有远程 MCP、可信交互、
多用户身份路由、加密会话状态、幂等操作账本和受控保活基础设施，不引入客户端
浏览器扩展，也不要求 OpenClaw 用户安装系统专用 CLI。

## 2. 技术路线

泰华系统采用 API 优先路线，不为日常能力调用启动浏览器：

1. 用户只在短期可信认证卡中输入用户名和密码；
2. Credential Broker 直接调用 `/api/authenticates/basic`；
3. 中央服务仅保存访问令牌、刷新令牌及有效期，并通过既有
   `SessionStateStore` 加密；
4. 访问令牌失效时调用 `/api/authenticates/refresh`，刷新失败才要求重新登录；
5. 每次真实读取或保活通过 `/api/users/principal` 核验会话与实际用户；
6. HTTP worker 只允许访问配置中的精确 origin，并拒绝自动重定向。

OA 仍使用 `SeeyonCentralAdapter + CentralBrowserWorker`。泰华使用
`TaihuaCentralAdapter + CentralHttpWorker`。中心服务按 `system_id` 选择运行时，
同一用户的 `oa` 和 `taihua` 会话互不覆盖。

## 3. 一期能力

### 3.1 读取

| 业务能力 | Capability | MCP 工具 |
|---|---|---|
| 查询本人工作日志 | `taihua.work_log.my.list` | `taihua_work_log_my_list` |
| 查询权限范围内团队日志 | `taihua.work_log.team.list` | `taihua_work_log_team_list` |
| 搜索可用项目 | `taihua.project.search` | `taihua_project_search` |

真实页面只读探索确认了以下接口：

- `GET /api/work-logs/range`
- `GET /api/work-logs/team`
- `GET /api/work-logs/team/dept-options`
- `GET /api/work-logs/team/member-options`
- `GET /api/projects`

团队日志页码从 `1` 开始；默认视角为 `submittedAt`。读取结果只暴露稳定业务字段，
不暴露访问令牌、内部认证状态或页面实现细节。

### 3.2 写入

一期选择“正式创建个人工作日志”作为完整写入样板：

| 阶段 | Capability | MCP 工具 |
|---|---|---|
| 字段收集与计划冻结 | `taihua.work_log.create.prepare` | `taihua_work_log_create_prepare` |
| 授权后提交与回读 | `taihua.work_log.create` | `taihua_work_log_create` |

流程固定为：

```text
用户原始描述
  -> 预填可信字段卡
  -> 实时检查同日日志和项目匹配
  -> 冻结精确写入计划
  -> 独立执行授权卡
  -> POST /api/work-logs
  -> GET /api/work-logs/range 权威回读
```

字段包括日志日期、工时、可选项目和日志内容。项目名称或编码必须唯一匹配。系统允许
同一日期存在多条日志，因此只在日期、工时、项目和内容四项完全相同时停止重复创建。后台业务拒绝以
`TAIHUA_BUSINESS_RULE_REJECTED` 返回具体、经清洗的原因。提交后无法完成权威回读时
返回 `RESULT_UNKNOWN`，且不会自动重试。

截至 2026-07-26，写入链路已完成模拟 HTTP 契约、字段卡、权限、提交边界和回读测试，
尚未对真实泰华系统执行工作日志写入。真实写入仍需用户针对具体日志确认字段卡与授权卡。

## 4. 权限

泰华权限与 OA 权限独立：

- `taihua:read`
- `taihua:write:worklog`

MCP Token 能连接服务不代表能调用任意系统。每个工具单独校验 scope：

- `oa:read` 不能读取泰华；
- `taihua:read` 不能创建日志；
- `taihua:write:worklog` 不授予任何 OA 写权限。

通过 CLI 签发 Token 时，只要请求了任一泰华写权限，就会自动补齐
`taihua:read`，用于登录、状态检查和字段选项查询；只包含泰华 scope 的
Token 只登记泰华会话，不会隐式附加 `oa:read` 或创建 OA 会话。

部署新能力不会自动扩大现有 Token 权限。

## 5. 配置

在 AgentBridge 状态目录登记系统：

```bash
python -m bscli.cli.main --home /home/guomao/agentbridge/data \
  system add taihua \
  --name "泰华日志系统" \
  --url http://10.10.50.101
```

中心服务会自动从 `system taihua` 配置读取 URL，也可以显式传入：

```bash
python -m bscli.cli.main --home /home/guomao/agentbridge/data \
  mcp central-serve \
  --taihua-base-url http://10.10.50.101 \
  ...
```

登录与状态工具：

```text
taihua_session_login
taihua_session_status
```

OpenClaw 插件使用原有多用户 `identityBindings`。不同微信或 Telegram 发送者继续绑定
不同 MCP Token；中央会话键为 `(user_subject, system_id)`，因此不同用户、不同系统的
登录状态和刷新令牌均隔离。

## 6. OpenClaw 续办

泰华登录卡沿用 `agentbridge.interaction.v1`。若“我的日志”“团队日志”或“项目查询”
首次因未登录被阻塞，OpenClaw 在登录成功后会通过同一用户、同一通道和同一 MCP Token
重放一次原读取请求，并直接把日志或项目结果发送回原会话。

仅三类泰华读取工具允许自动重放。日志创建、字段提交、授权和正式写入不会自动重放。
