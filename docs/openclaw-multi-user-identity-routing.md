# OpenClaw 多用户身份路由方案

## 1. 目标与当前状态

目标是在同一个 OpenClaw Gateway 中，让 Telegram、微信等不同私聊身份使用各自的
AgentBridge 身份、OA 登录态和权限，不共享 MCP Bearer Token，也不共享浏览器
Profile。

当前代码已经完成一期改造：

- OpenClaw 插件通过运行时可信字段 `messageChannel`、`requesterSenderId` 和
  `agentAccountId` 识别请求者，模型参数中不允许传入用户身份；
- 每个身份映射到一个环境变量名，Bearer Token 只从 Gateway 进程环境读取；
- 插件把当前 37 个 AgentBridge MCP 工具注册为 OpenClaw 原生代理工具；
- 同一会话一旦绑定身份便不可切换，发生身份变化时按冲突拒绝；
- 卡片轮询、交互恢复和自动续办固定使用最初触发操作的用户客户端；
- 未配置身份的用户只能看到身份状态工具，不能看到或调用 OA 工具；
- 已用两个虚拟消息用户完成 Token 隔离、并发请求、未知用户拒绝和
  会话串号测试。

第二个真实 OA 用户和微信私聊通道已经具备，真实双用户登录、会话保活和 OA
并发验收将在完成 Token 签发与身份绑定后进行。

## 2. 身份链路

```text
Telegram 用户 A
  -> OpenClaw 可信 requesterSenderId=A
  -> identityBindings 命中 TOKEN_A 环境变量
  -> AgentBridge MCP Token A
  -> user_subject A
  -> OA Session A
  -> Chromium Profile/Cookie A

微信用户 B
  -> OpenClaw 可信 channel=openclaw-weixin, requesterSenderId=B
  -> identityBindings 命中 TOKEN_B 环境变量
  -> AgentBridge MCP Token B
  -> user_subject B
  -> OA Session B
  -> Chromium Profile/Cookie B
```

身份映射不使用聊天文本、模型生成参数或用户昵称。昵称可以修改，也可能重复，
只允许使用宿主提供的稳定 `channel + senderId`；多机器人场景再加 `accountId`。

## 3. AgentBridge 侧开户

每个 OA 用户分别签发 Token，`user-subject` 和 `expected-principal` 必须对应真实人员。
权限按用户最小化授予，示例：

```bash
python -m bscli.cli.main --home /home/guomao/agentbridge/.bscli mcp token issue \
  --user-subject <agentbridge-user-id> \
  --expected-principal <OA显示姓名> \
  --label openclaw-telegram-<telegram-user-id> \
  --scope oa:write:draft \
  --ttl-hours 720
```

`oa:read` 是 MCP Token 的基础权限。写权限
`oa:write:draft`、`oa:write:approval`、`oa:write:meeting`、`oa:write:submit` 和
`oa:write:revoke` 互相独立，只按实际需要增加。

AgentBridge 在首次登录完成后校验实际 OA 用户是否等于 `expected-principal`。同一个
OA principal 不能同时绑定给两个不同的活动 AgentBridge 用户。

## 4. OpenClaw 侧配置

### 4.1 Token 环境变量

每个用户使用不同变量，变量值写入 OpenClaw 托管 Gateway 会读取的
`%USERPROFILE%\.openclaw\.env`，不要写入插件 JSON、聊天、仓库或日志：

```dotenv
AGENTBRIDGE_MCP_TOKEN_USER_1001=abmcp_...
AGENTBRIDGE_MCP_TOKEN_WECHAT_USER=abmcp_...
```

变量名可以自定义，但必须满足 `^[A-Za-z_][A-Za-z0-9_]*$`。

### 4.2 插件配置

```json
{
  "tools": {
    "profile": "coding",
    "alsoAllow": [
      "agentbridge-interactions"
    ]
  },
  "plugins": {
    "entries": {
      "agentbridge-interactions": {
        "enabled": true,
        "config": {
          "allowedCardOrigins": [
            "https://10.10.50.213:8780"
          ],
          "mcpUrl": "https://10.10.50.213:8790/mcp",
          "mcpTimeoutSeconds": 150,
          "identityBindings": [
            {
              "channel": "telegram",
              "senderId": "1001",
              "tokenEnv": "AGENTBRIDGE_MCP_TOKEN_USER_1001",
              "label": "用户A"
            },
            {
              "channel": "openclaw-weixin",
              "senderId": "wechat-user-1002@im.wechat",
              "accountId": "wechat-bot-account",
              "tokenEnv": "AGENTBRIDGE_MCP_TOKEN_WECHAT_USER",
              "label": "用户B"
            }
          ]
        }
      }
    }
  }
}
```

`coding` 等限制型 profile 不会默认开放原生第三方插件工具。这里用
`alsoAllow` 只放行 AgentBridge，不使用会同时开放其他插件的
`group:plugins`。若已有其他 `alsoAllow` 项，应合并保留。仅看到插件状态
`loaded` 还不够，真实验收必须在绑定的私聊会话中成功调用
`agentbridge_identity_status`。

同一个 Gateway 连接多个机器人账号时，可在绑定中增加 `accountId`，形成
`channel + accountId + senderId` 精确匹配；没有 `accountId` 的配置作为该渠道、
该发送者的通用匹配。

### 4.3 退出全局 MCP Token 模式

启用 `identityBindings` 后，应删除原来的 `mcp.servers.agentbridge` 全局配置，避免
OpenClaw 同时展示一套使用共享 Token 的远程 MCP 工具。插件还会阻止名称形如
`agentbridge__...` 的旧全局工具调用，作为迁移期的第二道保护。

新的 `mcpUrl` 只提供地址，认证由插件按当前消息身份动态选择。配置了
`identityBindings` 却没有 `mcpUrl` 时，插件会拒绝启动该模式。

## 5. 并发和隔离语义

- 不同用户：不同 Token、`user_subject`、`session_id`、浏览器 Profile 和锁，可以并行；
- 同一用户的多个请求：共用该用户的 OA 会话，并按 Session 锁串行访问浏览器；
- 同一消息身份的多个 OpenClaw 新会话：仍映射到同一 AgentBridge 用户和
  OA Session；
- 同一 OpenClaw `sessionKey` 中身份发生变化：立即进入冲突状态，不自动换 Token；
- 未开户用户：只能得到 `identity_not_provisioned`，不会回退到管理员或默认 Token；
- Token 缺失、过期或撤销：该用户失败，不影响其他用户。

OA 自身如果限制“同一账号只能一处登录”，AgentBridge 不绕过该限制。正确模型是
一名自然人使用一个 OA 账号和一条 AgentBridge 身份通道，而不是多人共享 OA 账号。

## 6. 工具目录同步

OpenClaw 原生代理工具目录由 Python MCP 服务自动导出：

```powershell
python tools\export_openclaw_agentbridge_catalog.py
python tools\export_openclaw_agentbridge_catalog.py --check
```

当前目录包含 37 个工具。Python 端新增或修改 MCP 工具后，CI/发布检查应先运行
`--check`；失败时重新导出目录并审查差异，防止 OpenClaw 能力面悄悄落后。

## 7. 真实双用户验收

2026-07-24 已使用两个真实 OA 身份完成一期验收：

1. Telegram `7052061588` 绑定 `guomao` / 辛国茂，微信私聊绑定
   `lishiyu` / 李世玉；两条绑定使用不同环境变量中的 MCP Token；
2. 两个 Token 分别绑定预期 OA 姓名和独立 scope。李世玉 Token 不含会议创建权限，
   没有回退使用辛国茂 Token；
3. 两个中央会话均由实时 OA 探测确认 active，下游姓名分别为辛国茂和李世玉，
   会话 ID 和浏览器 Profile 均不同；
4. 同一时段读取到辛国茂 2 条待办、李世玉 9 条待办，操作账本分别落在
   `guomao` 和 `lishiyu` 分区；
5. 微信只读验收只产生李世玉的 `oa.workflow.pending.list`，并把结果发回原微信；
6. 辛国茂通过 Telegram 对“关于征集济南市大数据产业专家入库工作的通知”完成
   字段卡、独立授权和普通协同提交。操作
   `2310b95b-5a8b-48e3-bdaa-3fc47360614a` 返回
   `workflow_approved=true`，并以目标待办消失确认；最终成功消息只发往原 Telegram；
7. Worker 启动现在会把新旧 Profile 目录强制收敛为 `0700`。线上两个真实目录均已
   验证为 `0700`，权限无法收敛时 Worker 会失败关闭。

本次通过的是同一中心服务账户内的身份绑定、Token、会话、Profile、操作账本和
通道路由隔离。两个 Profile 当前仍由同一 Linux `agentbridge` 服务账户持有；每用户
独立 OS/容器 Worker、下载和日志的跨安全主体不可读性仍是生产化验收项，不能据此
宣称已经达到完整生产隔离。
