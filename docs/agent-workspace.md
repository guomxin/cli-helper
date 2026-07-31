# Agent Workspace 网页端

> 状态：二期网页端及多端执行授权一期已实现
>
> 更新日期：2026-07-31
>
> 适用版本：AgentBridge 当前主线、OpenClaw 2026.7.1、
> `agentbridge-interactions` 0.4.7

## 1. 定位

Agent Workspace 是普通用户使用智能体的独立网页客户端，与 Telegram、微信处于
同一层。它不是 AgentBridge 管理控制台，也不提供 Token 签发、用户主体重绑、全局
写暂停或他人会话检查。

当前二期提供：

- 网页账号首次绑定、登录、退出和持久会话；
- 通过 OpenClaw Gateway 使用智能体对话；
- 查看当前用户的 AgentBridge 任务、事件时间线和关联端点；
- 打开当前任务的可信登录、字段或授权页面；
- 网页发起只读 AgentBridge 能力；
- 网页发起 OA 请假、出差两个正式提交的受治理填单流程；
- 最终执行授权同时显示在网页并主动投递到已绑定 Telegram、微信。

当前二期不提供：

- 除请假、出差正式提交准备之外的网页端业务写工具；
- 登录卡、字段卡的多端共同填写；
- 跨端继续对话和 OBO 执行宿主切换；
- 企业 OIDC、找回密码和自助解绑。

## 2. 身份绑定

首次绑定不要求用户在网页输入 MCP Token，也不允许用户手工填写
`userSubject`。

```text
网页生成 8 位一次性配对码
  -> 用户在已可信的 Telegram 或微信私聊发送
     /agentbridge link 配对码
  -> OpenClaw 插件使用该聊天身份的独立 MCP Token
  -> AgentBridge 从 Bearer Token 推导 userSubject
  -> 网页设置本地用户名和密码
  -> 创建永久关联的网页 ClientEndpoint
```

配对码有效期 10 分钟，只能确认一次。网页账号与 `userSubject` 建立永久关联后，
普通退出只吊销当前浏览器会话，不删除账号、端点或身份关联。用户下次直接使用网页
用户名和密码登录，不需要重新配对。

浏览器会话默认：

- 绝对有效期 30 天；
- 空闲有效期 7 天；
- 每次有效访问只延长空闲期限，不突破 30 天绝对期限；
- Session Token 使用 `HttpOnly`、`Secure`、`SameSite=Strict` Cookie；
- 修改请求还必须携带独立 CSRF Cookie/请求头。

## 3. OpenClaw 接入

浏览器不直接连接 OpenClaw，也不持有 Gateway Token。Agent Workspace BFF 在每次
发送消息前：

1. 为网页账号生成 90 秒、一次性 Gateway 绑定凭证；
2. 调用插件私有 RPC `agentbridge.workspace.bind`；
3. 插件依次使用已配置的独立 MCP 身份兑换凭证；
4. 只有与网页账号 `userSubject` 一致的身份能够兑换；
5. 插件把该网页 OpenClaw Session 固定到对应身份；
6. BFF 再调用 `chat.send`。

进程内绑定只作为缓存。若 OpenClaw 在 Gateway RPC 与 Agent Runtime 工具创建之间
重建插件实例或清空缓存，插件会使用当前候选 Bearer Token 调用宿主私有只读工具
`agentbridge_host_workspace_session_resolve`。中央服务只在该 Token 的
`userSubject` 确实拥有目标网页 Session、且网页端点仍为 active 时恢复绑定。浏览器、
模型参数和聊天文本均不能指定或覆盖身份。

网页 Session Key 使用：

```text
agent:main:agentbridge-workspace:direct:<workspace-account-id>
```

网页会话注册 MCP 目录中 `readOnlyHint=true` 的 AgentBridge 工具，并额外允许
`oa_business_trip_submit_prepare`、`oa_leave_submit_prepare` 两个受治理入口。
这两个入口先产生字段卡和冻结计划，最终提交仍必须经过执行授权卡；网页模型看不到
对应 commit 工具，确认后的 commit/verify 由原任务协调器续办。Telegram 和微信的
既有权限与行为不受此限制。

OpenClaw 核心源码没有修改。接入只使用正式插件 API、Gateway WebSocket 协议和
自定义 Gateway Method，因此 OpenClaw 升级后只需执行插件兼容测试。

网页发送消息时使用一个流式 HTTPS POST。BFF 在同一个 OpenClaw Gateway
WebSocket 上依次完成一次性身份绑定、`chat.send` 和 `agent` / `chat` 事件接收，
再把脱敏结果编码为 SSE 返回浏览器：

- Gateway 握手声明 OpenClaw 官方 `tool-events` 客户端能力，使发起本次运行的同一
  连接能够接收工具开始、进度和结束事件；
- `agent` 事件只显示模型提供的用户可读进度、脱敏后的业务能力名称和执行阶段；
- 工具参数、工具结果、Bearer Token、`userSubject` 和其他会话事件不会进入浏览器；
- `chat` 增量实时更新当前回答，最终事件到达后再读取一次正式聊天历史进行对账；
- 最终事件到达、超时或浏览器断开时，对应 Gateway 子进程立即结束；
- 每条流在 Node 侧同时按网页账号专属 Session Key 和本次 Run ID 过滤，避免其他
  用户或同一用户的并发任务事件进入 Python；
- 发送与监听不能拆成两个 Gateway 连接；OpenClaw 会把完整工具流和回答流优先投递
  给发起 `chat.send` 的连接。

## 4. 网络与部署

当前内网 PoC 地址：

| 组件 | 地址 |
| --- | --- |
| Agent Workspace | `https://10.10.50.213:8783` |
| AgentBridge MCP | `https://10.10.50.213:8790/mcp` |
| 可信卡片 | `https://10.10.50.213:8780` |
| OpenClaw Gateway | `ws://10.90.20.210:18789` |

OpenClaw Gateway 当前使用 `gateway.bind=lan`，这样本机
`127.0.0.1:18789` 和内网 `10.90.20.210:18789` 同时可用。不要改成当前版本下会
破坏回环访问的自定义单地址绑定。非回环连接必须保留 Gateway Token 认证和设备配对。

AgentBridge systemd 参数包括：

```text
--workspace-host 10.10.50.213
--workspace-port 8783
--workspace-public-base-url https://10.10.50.213:8783
--workspace-tls-cert /home/guomao/agentbridge/config/tls/server.crt
--workspace-tls-key /home/guomao/agentbridge/config/tls/server.key
--workspace-gateway-url ws://10.90.20.210:18789
--workspace-gateway-token-file /home/guomao/agentbridge/config/openclaw-gateway.token
```

Gateway Token 文件必须：

- 由部署管理员通过安全管道写入，不进入命令行、仓库、日志或数据库；
- 属于 AgentBridge 服务账号；
- 文件权限为 `0600`，父目录不可被普通用户写入；
- Token 轮换后立即替换服务器文件并重启 AgentBridge。

AgentBridge 第一次从 Linux 连接 OpenClaw 会产生设备配对请求。管理员在 OpenClaw
工作站核对设备名 `AgentBridge Workspace` 后执行：

```powershell
openclaw devices list --json
openclaw devices approve <requestId> --json
```

设备私钥只保存在服务器
`data/workspace-gateway/device-identity.json`，权限为 `0600`。正常服务重启不需要
重复配对；删除该文件、清除 OpenClaw 已配对设备或轮换相应设备 Token 后才需要重新
批准。

当前 PoC 已验证服务器可连接工作站 `10.90.20.210:18789`。生产化前仍应把 Windows
入站规则限制到 AgentBridge 服务器源地址，并评估 TLS 或受控隧道，不能把该端口
暴露到公司外网。

## 5. 用户操作

首次使用：

1. 打开 `https://10.10.50.213:8783`；
2. 选择“首次绑定”，生成配对码；
3. 在已经绑定 AgentBridge 的 Telegram 或微信私聊发送页面显示的命令；
4. 网页显示“身份已确认”后设置用户名和至少 12 位密码；
5. 进入工作台。

日常使用：

1. 使用网页用户名和密码登录；
2. 在“对话”中发起读取任务，或请假、出差正式提交任务；
3. 在“任务”中查看 Task Hub 状态、时间线和待处理可信交互；
4. 在“端点”中核对网页、Telegram、微信端点；
5. 退出只影响当前网页会话。

## 6. 安全边界

- 浏览器永远拿不到 MCP Token、Gateway Token、`userSubject` 或下游 Cookie；
- 网页账号不能自行选择或更换 `userSubject`；
- 两个用户的任务、事件、端点和 OpenClaw Session 均按 `userSubject` 隔离；
- Gateway 绑定凭证只能由对应身份兑换一次，错误身份和重放均失败；
- 网页聊天历史只返回用户和助手文本，不返回系统提示或工具内部消息；
- 可信卡片继续使用独立 HTTPS 页面，敏感字段不进入聊天；
- 同一执行授权为每个 Endpoint 生成独立 URL 和页面会话；
- 任一可信端可确认，但中心事务只接受第一个有效决定并只执行一次；
- 旁端只有确认权，不继承原 OpenClaw 会话的写 scope 或执行上下文；
- CSP 禁止第三方脚本、跨源连接和 iframe 嵌入；
- 登录失败按来源地址限流，密码使用 scrypt 哈希存储。

## 7. 验收基线

自动化覆盖：

- 首次配对、账号创建、登录、退出和超时；
- 双用户任务、事件和端点隔离；
- 错误身份兑换、一次性凭证重放和 CSRF 拒绝；
- Gateway Token 不进入子进程命令行或请求 JSON；
- 网页会话只暴露读取工具及两个明确允许的受治理提交准备工具；
- 插件 Gateway Method 使用 `operator.write` 并固定正确身份；
- 桌面 `1440x900` 与移动端 `390x844` 的登录、对话、任务列表、详情和返回；
- 浏览器控制台 0 错误。

2026-07-30 已完成的真实部署验收：

1. 发布 `caaeac5e2857` 已部署，8783 健康端点和首页均返回 200；
2. Linux 设备 `AgentBridge Workspace` 已完成 OpenClaw 配对；
3. 服务器持久设备身份已成功调用只读 `system.info`；
4. OpenClaw 插件 `0.4.3`、`agentbridge.workspace.bind` 和持久身份回源已加载；
5. Telegram、微信通道在 Gateway 重启后均保持运行。

真实用户验收已完成：

1. 使用现有可信 Telegram 身份完成网页账号绑定；
2. 网页成功发起只读 OA 与泰华查询；
3. 网页任务、原聊天端和下游 Session 按同一 `userSubject` 关联；
4. 插件 `0.4.3` 已验证 Gateway 身份绑定可被 Agent Runtime 复用并在缓存丢失后回源恢复；
5. 成功结束的网页任务显示为“已完成”，不再滞留为“进行中”。

2026-07-31 流式输出真实验收已完成：

1. 未预先调用网页绑定 RPC 的新 Workspace 会话能够从中央服务恢复正确身份，并以
   辛国茂身份调用只读 `oa_session_status`，不再返回 `identity_not_provisioned`；
2. 网页发送、身份绑定、`chat.send` 和事件接收共用同一个 Gateway WebSocket；
3. Gateway 客户端声明官方 `tool-events` 能力后，真实事件流依次出现智能体开始、
   “正在检查 OA 登录状态”、工具结果、回答增量、智能体结束和最终回答；
4. 事件清洗层未向浏览器输出工具参数、工具结果、Token、`userSubject` 或其他会话；
5. Linux Release `c3c675e6e4f0` 已部署，62 个 MCP 工具完整，OA 会话保持 active。

2026-07-31 多端执行授权一期部署验收：

1. Workspace、Telegram、微信分别获得绑定自身 Endpoint 的授权 URL；
2. 多页面使用独立 CSRF Card Session，不再因后打开页面覆盖先打开页面；
3. 并发确认只有一个原子决定成功，其他端显示已在另一可信端处理；
4. Outbox 主动投递执行授权和终态，30 秒 Lease、最多 5 次失败重试；
5. Workspace SSE 在等待确认时刷新任务详情并提示可在网页处理；
6. 原任务会话继续执行 commit/verify，旁端仅展示和确认。
7. 提交 `420d890` 已推送，Linux Release `420d8902f534` 已部署；65 个 MCP 工具
   发布冒烟、OpenClaw Gateway 深度 RPC 和插件 `0.4.5` 运行时检查均通过。

## 多端任务同步一期

自 `agentbridge-interactions` 0.4.5 起，Workspace、Telegram 和微信不再共享
同一个客户端端点记录。MCP Token 只决定用户身份和调用权限，客户端端点决定任务
来源、会话地址和通知路由。网页发起任务不会再覆盖 Telegram 或微信的会话资料。

任务建立后，已绑定端会订阅同一任务的关键事件，包括任务建立、执行中、等待用户、
完成、失败、取消和结果未知。具备 `trusted_interaction` 能力的端收到可操作卡片；
只具备状态能力的端只收到状态消息。普通聊天和敏感字段值不做跨端复制。

业务字段填写卡和最终执行授权卡都按端点生成独立展示链接及 CSRF 会话。任意一端
先完成后，其他端再次打开会看到已处理状态，底层业务动作仍只会提交一次。

## 网页应用卡与通知降噪

Agent Workspace 的对话区会把同一 `taskId` 的跨端进展合并为一张持久应用卡。
卡片展示任务状态、当前可信交互、最新事件和对应操作入口；字段卡或授权卡在另一端
完成后，网页卡片通过 Task Hub SSE 自动切换到下一阶段或最终状态。刷新网页时，
进行中任务和最近六小时内的终态任务会从 Task Hub 重建，不依赖 OpenClaw 再生成
一条聊天回复。

SSE 首次连接从当前最新事件建立游标，后续重连显式携带最后事件 ID，不重放历史
事件。普通任务进度只更新应用卡，不再显示临时提示；只有失败和结果未知保留提示，
同一异常会合并且同时最多显示两条。

Telegram、微信等推送端只主动接收可操作的可信卡片、取消、过期以及最终成功、
失败或结果未知。任务创建、操作关联、执行中和交互完成等高频中间事件仍写入
Task Hub，但不再逐条发送聊天消息。Agent Workspace 是拉取式端点，插件直接确认
其 Outbox 投递，由网页事件流负责呈现，避免无效的 `webchat` 直投和重复告警。

网页收到 OpenClaw 模型连接错误时会在原消息位置显示失败原因。只有事件流明确表明
尚未调用任何业务工具时才提供“重新发送”；已经出现工具调用或仅发生传输超时的
请求不提供安全重试，以免对写操作造成重复执行。
