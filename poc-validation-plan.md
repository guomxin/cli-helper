# AgentBridge 轻量 PoC 验证方案

> 状态：执行草案 v0.11
> 更新日期：2026-07-29
> 目标：用中心端零客户端连接器架构接入 3 个不同类型的 B/S 系统，验证浏览器会话、Token 会话和交互式登录三类适配路线。

## 0. 当前执行进度（2026-07-29）

中心端零客户端连接器 PoC 已覆盖三个真实系统和两个真实 OA 用户；第三系统语雀一期已完成部署、交互式登录和真实只读验收：

| 验证面 | 当前状态 | 证据摘要 |
| --- | --- | --- |
| 中心能力内核 | 已完成 PoC | CLI、HTTPS MCP 和 OpenClaw 共用能力注册表、会话锁、幂等键、错误语义和 SQLite 操作账本 |
| 加密会话 | 已完成单机 PoC | Windows 使用 DPAPI；Linux 使用外部 32 字节密钥和 AES-256-GCM，拒绝错密钥、篡改、符号链接和过宽权限 |
| Seeyon OA 读取 | 已完成真实验证 | 模板、待办、已发、已办、跟踪、详情和意见均通过中心 HTTP 或中心浏览器会话读取 |
| Seeyon OA 写入 | 已完成多条真实闭环 | 出差和请假正式提交与撤销、普通协同、周报知会、会议创建等已经过字段卡、授权、commit 和权威回读 |
| 泰华日志系统 | 已完成适配一期 | 登录与刷新、个人/团队日志筛选、项目查询及受控日志创建均已真实验证 |
| 部门信息库（语雀） | 已完成真实验收 | 公共区知识库、目录、搜索和正文读取已固化；用户通过隔离 noVNC 卡完成真实滑块登录，账号核验、加密 Cookie 保存和临时进程清理均通过；搜索摘要不返回，正文执行凭据和 Token 脱敏 |
| 可信交互 | 已完成真实验证 | 认证、业务字段和执行授权统一为 `agentbridge.interaction.v1`，支持轮询、恢复、后续卡片直投和最终状态反馈 |
| 远程 MCP | 已完成内网 PoC | `10.10.50.213` 通过内网 IP、HTTPS、内部 CA 和 Bearer 向 OpenClaw 提供 Streamable HTTP MCP |
| 双用户身份隔离 | 已完成同服务 PoC | Telegram/辛国茂与微信/李世玉使用独立 Token、会话、Profile、权限、账本和回复通道；真实只读结果未串用 |
| 移动端 | 部分完成 | Android Chrome 已信任内部 CA；微信链路完成真实登录和读取，Telegram Android WebView 对内部 CA 仍存在空白页兼容问题 |
| 受控保活 | 已完成一期 | 按用户和系统记录活动时间，OA Cookie 会话与泰华 Token 会话分别探测、保活和刷新；真实失效仍要求重新登录 |
| 生产隔离 | 待实现 | 每用户独立 OS/容器 Worker、企业 OAuth/OIDC、正式 PKI、限流、集中审计和 Vault/KMS 尚未完成 |

PoC 已经证明中心端、低客户端安装、多系统和多聊天身份接入可行。下一阶段重点不是
恢复通用浏览器控制，而是继续扩充高价值工作流，并把同服务账户内的逻辑隔离升级为
每用户独立安全主体。详细时间线保留在后续增量章节和
[当前部署记录](./docs/current-deployment-plan.md)。
### 0.1 2026-07-24 双用户增量证据

- 已接入两个真实 OA 身份：Telegram 对应 `guomao` / 辛国茂，微信对应
  `lishiyu` / 李世玉；两者使用独立 MCP Token、预期下游身份、会话 ID、Profile
  和操作账本分区；
- 两个会话均由实时 OA 探测确认 active，同一时段分别读取到 2 条和 9 条待办；微信
  验收只产生李世玉的只读操作，没有处理第二用户待办；
- 中心 Worker 已强制把新旧 Profile 目录收敛为 `0700`，线上两个真实目录均通过
  权限复核；权限无法收敛时 Worker 失败关闭；
- 辛国茂通过 Telegram 完成一条普通协同的字段卡、独立授权、提交和待办消失回读，
  操作 `2310b95b-5a8b-48e3-bdaa-3fc47360614a` 明确返回成功；
- 辛国茂通过 Telegram 完成一条周报“知会”事项，能力
  `oa.weekly_report.acknowledge` 明确返回 `workflow_acknowledged=true`、
  `workflow_approved=null`，操作 `9a2f7967-9ae0-4fde-824a-c4e32761be6d`
  由待办消失回读确认；字段卡、授权卡和成功回执均留在原 Telegram 通道；
- 本次可声明“同一中心服务账户内的真实身份、Token、会话、Profile、账本和通道路由
  隔离已通过 PoC 验收”。两个 Profile 仍由同一 Linux 服务账户管理，因此每用户独立
  OS/容器 Worker，以及 Cookie、下载、截图和日志的跨安全主体不可读性仍未完成，
  不宣称达到完整生产隔离。

### 0.3 2026-07-29 第三系统语雀增量

- 已识别单组织公共区、知识库、目录、全文搜索和单文档读取接口，并将页面探索结果固化为四个只读业务能力；
- 当前测试账号不具备 Personal Access Token 权限，因此一期使用按用户隔离的中心浏览器 Cookie 会话，不把网页 Cookie 或私有接口暴露给智能体；
- 登录采用 `interactive_browser_login`：可信卡片嵌入按挑战隔离的 noVNC 浏览器，用户亲自完成滑块和短信验证码；原生输入、一次性 VNC 密码、Cookie、浏览器端点和控制令牌不进入模型；
- 搜索结果主动删除服务端摘要，避免命中内容中的账号、密码、SSH 信息或 Token 被批量带入模型；只有用户明确选择文档后才读取正文，并对疑似凭据、Token、URL 内嵌口令和私钥进行脱敏；
- 已发布 `yuque:read` 独立 scope 和六个 MCP 工具（四个读取工具、会话状态、会话登录）；没有 `yuque:write:*`，也不开放任意 HTTP、DOM 或浏览器控制；
- 适配器、远程浏览器 Broker、可信卡、中央服务、MCP 和 OpenClaw 工具目录已完成全量回归测试。2026-07-29 在 `10.10.50.213` 完成真实登录：会话激活并核验为“辛国茂”，加密 Cookie 保存成功，挑战结束后临时 Profile、路由和浏览器进程全部清理。语雀只读 PoC 已通过。

## 1. PoC 要回答的问题

1. 不修改遗留系统源码，能否稳定完成查询、填写、下载及安全的低风险写入？
2. 能否把页面操作封装成智能体容易理解和调用的业务能力，而不是暴露点击和选择器？
3. 能否让至少两个真实用户分别使用自己的遗留账号和会话，且数据和 Cookie 不串用？
4. 页面变化、登录过期、弹窗和网络异常时，能否给出明确错误并安全停止？
5. 一个新系统从分析到形成可运行连接器需要多少工作量，哪些页面类型最难适配？
6. 能否在最终用户设备不安装 Chrome 扩展、本地 Daemon 或系统连接器的前提下，通过中心 HTTP Session/Browser Worker 完成同样任务？
7. 表单登录能否通过可信认证卡片完成，使账号密码和验证码绕过模型并安全注入中心浏览器？

PoC 不以生产高可用、完整企业身份治理或正式移动产品为目标；当前内网远程 MCP 和 Telegram 宿主联调只作为部署可行性证据。

## 2. 首期范围

### 2.1 系统选择

基线选择 2 个差异明显的系统，系统 C 为 stretch goal：

| 系统 | 建议特征 | 主要验证点 |
|---|---|---|
| 系统 A | 普通 HTML/SPA、表格和表单 | DOM/ARIA 定位、查询、详情读取 |
| 系统 B | iframe、弹窗、分页、下载或上传；当前以 Seeyon OA 为代表 | 复杂页面状态、文件处理、中心登录与会话恢复 |
| 系统 C（已选语雀部门信息库） | 稳定内部读取接口，但登录包含滑块和短信验证 | 可信交互式浏览器登录、中心 Cookie 会话、只读检索与敏感信息脱敏 |

每个系统优先选择 1—2 个高价值、低风险、人工操作路径清楚的业务流程。至少准备两个权限或数据范围不同的测试用户；系统 A/B 中至少一个必须包含可撤销、可回读验证的 W1 流程，否则不满足基线系统选择条件。

早期 Seeyon OA 原型曾依赖浏览器桥接，但后续验证已经证明 OA 能力无需依赖员工浏览器。PoC 迁移时按三类处理：

- 首页栏目、模板中心、会议 AJAX 等稳定后台接口，在中心浏览器建立用户会话后优先迁移到每用户 HTTP Session；
- 流程详情、意见和附件等页面，验证能否通过带会话的 HTTP 获取 HTML 后结构化解析；
- `ContinueSubmit`、保存草稿、CAP4 业务表单和动态隐藏字段等状态性流程，暂留中心 Browser Worker 并逐流程固化状态机。

抓包结果中的 `ownerId`、`spaceId`、时间戳和其他用户相关值不得作为跨用户常量；必须从各用户自己的会话中解析或通过稳定接口获取。PoC 的目标是减少每次调用对浏览器的依赖，而不是在尚未证明安全时强行彻底去浏览器。

### 2.2 能力范围

首期只实现：

- R0 查询、搜索、详情读取、报表或附件下载；
- 至少一个 W1 草稿、标签或临时备注等可撤销动作；
- 至少一个普通表单登录流程通过可信认证卡片建立中心会话；
- W2 和简单跨系统流程均为加分项，不影响基线 PoC 通过；
- 如果做跨系统验证，只允许“从系统 A 读取结果，在系统 B 创建草稿”，不自动正式提交。

### 2.3 暂不实现

- 生产级远程 MCP Gateway 和完整手机产品；当前固定内网 IP HTTPS、内部 CA 与 OpenClaw/Telegram 仅作为 PoC 宿主纵切，不等同于企业级移动发布；
- 分布式队列、集群调度、多区域灾备；
- 完整 OAuth/OIDC、OBO、DelegationGrant 和多租户；
- 复杂策略引擎、事件总线和跨系统 Saga；
- W3 高价值或不可逆操作；
- 完整模型数据治理、企业 DLP、WORM 审计和监管报表；
- 通用可视化控制台。

这些内容保存在 [目标架构](./agent-oriented-legacy-bs-adaptation-design.md) 和 [后续增强事项](./deferred-considerations.md) 中。

## 3. 最小架构

```text
本地测试智能体
  → Skill
  → agentbridge CLI（JSON 输入输出）
  → 中心 AgentBridge 单机服务
  → 中心能力内核
  → 简单 SQLite 操作账本
  → 中心会话注册表与凭据代理
  → System Adapter / Worker
       ├─ 每用户 HTTP Session
       ├─ 每用户 Playwright 浏览器状态机
       └─ 必要时使用 CDP 辅助观测
  → 遗留系统

手机或桌面浏览器
  → 一次性可信认证卡片 → 中心凭据代理（登录秘密绕过模型）
  → 一次性可信字段卡片 → FieldSubmission（业务字段绕过模型）
  → 一次性可信授权卡片 → 冻结计划授权（决定绕过模型）
```

首期采用一台位于目标系统可达网络区域的中心主机，不建设 Daemon 集群。能力内核、SQLite 账本、凭据代理、HTTP Session、浏览器 Worker 和 Profile 全部在该主机运行；最终用户设备不安装 Chrome 扩展、本地 Daemon 或连接器。旧 BSCLI Chrome 扩展、localhost Daemon 和代理型命令已彻底退役；迁移线索由 Git 基线和 [退役记录](./docs/legacy-bridge-retirement.md) 保存，不再保留可运行旧路径。

## 4. 最小组件

### 4.1 CLI

只需提供：

```text
agentbridge capabilities list --json
agentbridge capabilities describe <name> --json
agentbridge session login --system <system>
agentbridge invoke <capability> --input <file> --json
agentbridge prepare <write-capability> --input <file> --json
agentbridge operations get <operation-id> --json
agentbridge-trusted authorize <operation-id>
agentbridge-trusted commit <operation-id> --json
```

要求：stdout 只输出 JSON，stderr 输出诊断；禁止 Shell 拼接；返回稳定 `status/error.code`。`session login` 是幂等的会话确保操作：有效会话经过真实探测后直接复用，只有 OA 明确失效才生成非敏感 `AuthChallenge`，不得在 CLI 或 MCP 参数中接收密码；探测暂时不可用或运行身份不匹配均不得触发重新认证。普通 `agentbridge` 是智能体协议面，不包含认证秘密提交、`authorize` 或 `commit`；这些能力只存在于独立可信入口，并且不得注册到模型工具集合。`invoke` 只允许执行 `effect: read`；W1/W2 通过 `invoke` 直接调用时返回 `WRITE_REQUIRES_PREPARE`。可信确认组件显示冻结计划并取得用户确认后，才可调用授权和提交。

### 4.2 能力定义

首期 CapabilitySpec 只保留：

```yaml
name: system.object.action
version: 0.1.0
description: 面向智能体的业务描述
inputSchema: {}
outputSchema: {}
effect: read | reversible_write | controlled_write
adapter: system-a
workflow: flow-name
```

### 4.3 用户会话

- 中心测试框架为每个最终用户启动固定、独立的 HTTP Cookie Jar 和浏览器 Profile/Context；
- Profile 与受信任的 PoC `userSubject`、目标系统和非敏感下游账号说明绑定，智能体运行中不能通过业务参数切换用户；
- 表单登录由可信认证卡片收集一次性账号、密码或验证码，秘密直达中心凭据代理，再由 Browser Worker 填写真实登录页；PoC 默认不持久保存密码；
- Cookie、下载目录、截图和日志不跨用户共享，也不提交到代码仓库；
- 同一用户会话串行执行写操作，不同用户会话可以并行；
- 同一遗留账号被标记为单会话时，手机和桌面调用共享同一中心会话，禁止分别重复登录；
- 活跃会话先用真实服务端接口探测并刷新 Cookie 状态；仅在 OA 明确登录过期时返回 `LOGIN_REQUIRED` 和一次性 `AuthChallenge`，由用户完成认证卡片后再继续原 `operationId`；暂时网络失败返回 `SESSION_CHECK_UNAVAILABLE`，运行身份不匹配返回 `SESSION_RUNTIME_MISMATCH`，两者都保留会话且不索要凭据。

双用户验证固定使用中心端两个独立、受限的 Worker OS 身份、容器或虚拟机，并分别持有浏览器和数据目录。首期不接受在同一 Worker 安全主体下仅依赖目录命名模拟两个用户；若 PoC 暂时使用同一主机，必须通过进程身份和 ACL 证明用户 A 的 Worker 无法读取用户 B 的 Profile、下载、截图、Cookie 和日志。

首期可以使用简单本地配置记录：

```text
userSubject → Worker安全身份 → systemId → profilePath → 非敏感账号说明
```

不开发完整 IdentityBinding 服务，但必须证明两个用户不会串会话，并在登录后从可信页面状态或后端接口核验实际遗留账号与预期说明一致。验收时还要验证用户 A 的 Worker 无法读取用户 B 的 Profile、下载、截图、Cookie 和日志目录；仅仅在文件名中使用不同用户名称不算隔离。

### 4.4 可信认证卡片

PoC 只实现普通表单登录的最小挑战响应协议：

```text
LOGIN_REQUIRED
  → 服务端生成 AuthChallenge
  → 可信宿主按服务端 Schema 渲染认证卡片
  → 秘密字段通过独立可信通道提交给凭据代理
  → Browser Worker 填写真实登录页面
  → 核验实际登录账号
  → 会话变为 active
```

生产通道必须使用 TLS。为先验证“用户电脑运行 OpenClaw、另一台内网机器运行
AgentBridge”的部署拓扑，允许通过显式 `--allow-insecure-private-http` 开关使用
固定私网 IP 的 HTTP；该模式必须限定在受控公司内网且不得做公网映射。主机防火墙或上游 ACL 属于推荐加固项，但不作为首次 PoC 前置条件；该模式不能作为生产验收项。

- `AuthChallenge` 绑定 `challengeId + userSubject + systemId + sessionId + origin + pageFingerprint + nonce + TTL`，且只能使用一次；
- 卡片字段由已注册登录 Adapter 定义，模型和网页内容不能新增密码字段或修改提交地址；
- 模型只看到挑战 ID、状态和非敏感提示，不得看到账号密码、验证码或 MFA 值；
- 密码、验证码不进入 CLI/MCP 参数、聊天、Trace、截图、HAR、剪贴板、普通日志或分析埋点；
- Browser Worker 使用秘密填写真实页面，让页面自身完成前端加密、动态盐、CSRF 和跳转；
- 首期支持用户名密码、短信验证码以及登录页内的滑块交互。滑块不被自动破解：可信卡只转发指定登录页画面和用户输入，由用户亲自完成验证；二维码同机扫码、USB Key、ActiveX、客户端证书和电子签章仍返回 `UNSUPPORTED_AUTH_METHOD` 或转受控桌面接管。

### 4.5 操作账本

使用单机 SQLite 记录：

- `operationId`、能力、版本和用户测试身份；
- 脱敏输入摘要；
- `running/succeeded/failed/unknown`；
- 开始、结束时间和错误码；
- 写操作的幂等键及验证结果；
- 必要的截图或证据文件引用。

不实现分布式事务，但写操作超时或状态不确定时必须进入 `unknown`，禁止自动重试。

### 4.6 Adapter

每个连接器实现四个最小接口：

```text
check_session(context)
prepare(context, input)
execute(context, input)
verify(context, result)
```

页面定位优先使用 role、label、可访问名称和稳定业务文本；CSS/XPath 只封装在 Adapter 内。每个写流程必须有独立 `verify`，不能只判断成功 Toast。

### 4.7 最小写入确认协议

PoC 中需要用户提供业务字段的 W1/W2 写入统一采用：

```text
trusted collect → prepare → trusted authorize → commit → verify
```

- `collect` 生成绑定用户、OA 会话、能力版本和字段 Schema 哈希的一次性字段卡；模型只获得不透明 `input_submission_id`；
- `prepare` 生成结构化计划、目标对象、参数摘要、`operationId` 和不可变 `planHash`，不得产生业务副作用；
- 可信确认组件向用户显示计划，并在受 OS ACL 保护的中心 SQLite 账本中写入短期、一次性授权记录；授权绑定 `planHash + userSubject + capability/version + target + TTL`；
- 模型不能在业务输入 JSON 中自报“用户已确认”，也不能直接调用 `commit`；
- `commit` 重新确认当前用户、能力版本、目标对象和页面状态，校验 `planHash` 后原子消费一次性授权再执行；
- `verify` 回读结果。超时或无法确认时进入 `unknown`，禁止自动重新提交。

## 5. 三轮验证

### 第 1 轮：系统 A，只读打通

- 建立第一个 Adapter、CapabilitySpec 和 Skill；
- 两个用户分别登录并查询各自数据；
- 完成查询、列表、详情中的至少两个能力；
- 连续重复运行并记录失败原因；
- 验证用户 A 不能看到或复用用户 B 的会话数据。
- 验证所有执行发生在中心端，断开现有 Chrome 扩展后能力仍可运行。

### 第 2 轮：系统 B，复杂交互

- 验证 iframe、弹窗、分页、下载/上传或多页签中的至少两类；
- 验证中心会话过期、可信认证卡片重新登录和登录后账号核验；
- 验证两个用户的浏览器实例并行运行；
- 保存失败时的脱敏截图、DOM 摘要和步骤日志。
- 使用手机和桌面浏览器各完成一次认证卡片流程；这只验证登录交互，不代表完整移动 MCP 已上线。

### 第 3 轮（加分项）：系统 C、W2或简单联动

- 在基线 W1 已完成后，可再实现一个边界清楚的 W2 能力；
- 执行前展示结构化计划并由用户人工确认；
- 提交后回读目标状态；
- 模拟超时、重复请求和提交后断连；
- 可选验证“系统 A 读取 → 系统 B 创建草稿”的简单联动。

## 6. 建议时间盒

| 周期 | 工作 |
|---|---|
| 第 1 周 | CLI、能力接口、SQLite 账本、中心 Worker/Profile 隔离和认证卡片骨架 |
| 第 2 周 | 系统 A 只读连接器及双用户验证 |
| 第 3 周 | 系统 B 复杂页面与会话恢复 |
| 第 4 周 | 系统 C/W2/简单联动加分项，或用于修复前两轮问题和总结 |

如果只有两个合适系统，可压缩为 2—3 周。时间盒结束后先评估结果，不继续无限扩展功能。

## 7. 验收指标

PoC 通过条件：

- 成功接入两个不同 B/S 系统；第三个系统为加分项；
- 每个系统至少形成 1—2 个业务能力，而不是原子页面操作；
- 至少两个用户使用各自遗留账号和独立会话完成同一只读能力；
- 不出现跨用户 Cookie、下载、结果或截图串用；
- 用户 A 的 Worker 不能读取用户 B 的 Profile、Cookie、下载、截图或日志目录；
- 每个只读流程重复执行至少 20 次，成功率达到 90% 以上；W1 在同一受控测试对象或可清理草稿上完成重复请求、回读和清理验证，不批量制造业务对象；失败均能定位到明确步骤，且没有错误业务副作用；
- 断开或卸载客户端 Chrome 扩展后，PoC 验收能力不受影响；
- 至少一个用户通过可信认证卡片建立中心会话，秘密未进入模型、CLI/MCP参数、Trace、截图或普通日志，且登录后账号核验正确；
- 至少一个写流程通过可信字段卡收集业务输入，字段值未进入模型、CLI/MCP 参数或普通操作回执，并且字段提交与执行授权严格分离；
- 一个 W1 写流程通过可信人工确认、提交、回读验证和重复请求测试；
- 写操作重复副作用数为 0，结果不确定时能够进入 `unknown`；
- 模型无法获得账号密码、Cookie、Token、任意 Shell/HTTP/JavaScript 或底层页面选择器；
- 能统计每个新系统的分析时间、开发时间、失败类型和维护难点。

90% 是 PoC 技术验证线，不是生产 SLO。

## 8. 安全边界

- 默认只使用合成数据或明确批准的低敏测试数据。涉及真实生产数据、高敏只读或批量导出前，必须先补充对应的数据分级、字段最小化以及模型、日志和证据处理规则，否则停止验证；
- 优先使用测试环境或低风险测试账号；
- 生产验证前取得系统所有者许可，并限定时间、账号、IP和操作范围；
- 账号密码、Cookie 和 Token 不进入模型上下文、普通日志或代码仓库；
- 认证卡片必须由服务端登录 Adapter 生成并显示可验证的系统名称；秘密通过独立通道直达凭据代理，默认仅在内存中短暂使用；
- 智能体只能调用白名单业务能力，不得获得任意 Shell、HTTP、JavaScript、Cookie 或页面选择器；
- 不使用共享管理员账号，不直接写数据库；
- 不绕过验证码、MFA、USB Key 或电子签章；
- 不把敏感业务数据发送到未批准的云端模型；
- 付款、删除、归档等高风险动作默认不开放；正式审批或工作流提交只有在具备流程专用映射、独立 scope、可信授权和权威回读后才可逐项开放，不能借用通用提交入口；
- 发现页面身份、当前用户或目标业务对象不明确时立即停止。

上述条款属于首期运行时约束，不得因为生产治理功能暂缓而省略。

## 9. PoC 输出物

- 可运行的 CLI 和最小能力内核；
- 3 个系统 Adapter：Seeyon OA、泰华日志和语雀部门信息库；
- 每个系统的 CapabilitySpec、Skill 和页面契约；
- 中心会话注册表、最小可信认证卡片和登录 Adapter；
- 双用户会话隔离验证记录；
- 运行成功率、失败分类和性能数据；
- 低风险写入的幂等与回读验证记录；
- 每个系统的适配难度、预计维护成本和是否值得继续；
- 下一阶段 Go / Conditional Go / Stop 建议。

## 10. 阶段决策

### Go

- 至少两个系统达到验收线；
- 双用户身份和会话隔离可靠；
- 页面操作能够稳定封装成业务能力；
- 新连接器工作量在可接受范围内。

### Conditional Go

- 只读能力可行，但写入或会话恢复仍需改进；
- 某类系统需要独立 VM、受控桌面接管或特殊认证方案；
- 仅部分系统值得继续投入。

### Stop / 改变路线

- 无法可靠确认当前用户或目标业务对象；
- 无法隔离不同用户会话；
- 页面变化导致核心流程长期不可维护；
- 目标系统许可、安全政策或风控明确禁止自动化；
- 维护成本明显高于推动正式 API 或替换系统。

PoC 通过后，再优先实现远程 MCP 和手机端只读访问；随后根据 [后续增强事项](./deferred-considerations.md) 分阶段补齐生产能力。
