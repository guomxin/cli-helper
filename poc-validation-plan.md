# AgentBridge 轻量 PoC 验证方案

> 状态：执行草案 v0.6
> 更新日期：2026-07-13
> 目标：用中心端零客户端连接器架构接入 2 个不同类型的 B/S 遗留系统；时间允许时再增加第 3 个系统，快速判断面向智能体非侵入适配是否值得继续投入。

## 0. 执行进度（2026-07-13）

Seeyon OA 的首个 R0 纵切已经通过单用户真实环境验收：

| 项目 | 状态 | 证据 |
|---|---|---|
| 能力目录与 Schema | 已完成首版 | 已注册 6 个中心只读能力和 2 个 `oa.business_trip.*@0.1.0` 草稿写能力，可通过 CLI 列出和描述 |
| 中心能力服务 | 已完成首版 | CLI 与 MCP 共用 `CentralCapabilityService`、会话锁、错误语义、幂等键和操作账本，不复制 OA 编排 |
| SQLite 操作账本 | 已完成首版 | 先落账再执行；同幂等键复用操作，不同输入返回冲突 |
| 中央会话注册表 | 已完成首版 | `userSubject + systemId` 绑定独立 Profile；支持 `new/awaiting_login/active/expired/quarantined` |
| 中央 Browser Worker | 已完成首版 | Playwright 持久 Profile、origin 白名单、同 Profile 单租约、正常关闭回收 |
| 加密会话状态 | 已完成 Windows PoC | 进程级 Session Cookie 经 DPAPI 加密落盘；新进程只在内存中恢复；普通日志和账本无 Cookie；不同 Windows 用户解密会失败关闭，Broker 与 Worker 必须使用同一服务安全主体 |
| OA 模板与事项列表 | 已完成真实验证 | 模板直接复用浏览器上下文 HTTP 会话；事项列表从首页动态发现当前用户栏目参数后调用后台接口；不调用扩展或 localhost Daemon |
| OA 详情与意见 | 已完成真实验证 | 详情在同一中心会话中渲染并合并同源 iframe；真实样本提取 8 个业务字段和 1 条结构化意见，公开结果不含内部 URL、HTML、Cookie、动作端点和写提示 |
| 未登录真实诊断 | 已验证 | 模板接口真实返回 `401 application/json`，会话保持未激活并返回 `LOGIN_REQUIRED` |
| 已登录真实读取 | 已验证 | 新 headless CLI 进程恢复加密会话并读取 118 个模板、3 条待办、9 条已办、9 条跟踪以及一个详情/意见样本；全部 `browser_bridge_used=false` |
| 操作幂等复用 | 已验证 | 重复使用同一幂等键返回同一 `operationId` 和保存结果，`reused=true` |
| 可信认证卡片/凭据代理 | 已完成单用户真实验证 | 一次性挑战、固定字段、CSRF、TTL、来源校验、内存凭据、真实 iframe 登录、原生提交和下游身份核验均已验证 |
| 卡片后跨进程会话恢复 | 已验证 | 停止认证服务后，两个新 CLI 进程各自恢复 `/seeyon` Session Cookie 并读取 118 个模板；均未调用扩展 |
| 卡片响应式界面 | 已完成视口验证 | 1280×800 和 390×844 无溢出或遮挡；尚未从真实手机跨网络提交凭据 |
| Streamable HTTP MCP | 已完成单用户真实纵切 | 官方 MCP SDK 无状态 JSON 传输并发布 10 个工具；独立服务进程和官方客户端完成握手与工具调用；真实调用 `oa_session_login` 完成卡片认证后，通过 `oa_workflow_pending_list` 读取 3 条待办，返回 `central_http_session`、`browserBridgeUsed=false` 和持久化 `operationId`；CLI/MCP 同幂等键返回同一操作 |
| MCP 调用身份 | 已完成 PoC 绑定 | 预签发短期 Bearer 令牌仅保存摘要并绑定 `userSubject + expectedPrincipalRef + scope + TTL`；工具不接受用户身份参数；无令牌和吊销令牌均返回 401 |
| MCP 与认证卡同进程 | 已完成真实验证 | 一个中心运行时同时托管 MCP 与可信认证卡，Broker/Worker 使用同一 OS 安全主体和每用户会话锁；真实验证完成后一次性令牌自动吊销、两个监听端口关闭；初始化失败也会关闭认证服务 |
| 可信写授权 | 已完成代码与自动化测试 | 写计划以哈希冻结并绑定用户、系统、OA 会话、能力版本和准备操作；独立可信操作卡片使用 CSRF、TTL 和一次性批准，授权只在适配器提交边界消费；旧计划会被新计划废止 |
| 出差申请草稿 W1 | 已完成单用户真实验证 | `prepare` 校验模板和 CAP4 字段但不填写；可信卡批准后授权只消费一次；真实 `save_draft` 返回待发草稿和稳定 ID，服务端重载后 7 个业务字段一致，`browser_bridge_used=false`、`workflow_submitted=false`、`submitted_count=0`；原生确认字段回归和隐藏附言前置拒绝已补测试 |
| 多用户安全主体 | 待真实验证 | 当前无第二个 OA 用户；代码级绑定与单用户身份核验已完成，OS/容器级双用户不可读性尚未证明 |
| 生产远程 MCP | 未开始 | 预签发 Bearer 只用于 PoC；OAuth/OIDC、生产证书、反向代理信任、限流、真实手机网络和第二用户仍待验证 |
| 更多中心写动作 | 未开始 | 当前只开放出差申请保存草稿；发送、提交和其他流程按后续里程碑逐个提升 |

首个 R0 纵切和可信认证卡片单用户门槛已经满足。下一验收门槛是两个不同真实用户在独立 Worker 安全主体中完成同一 R0 能力，并证明 Profile、Cookie、下载和日志不能跨用户读取；在此之前不宣称完成多用户隔离。

## 1. PoC 要回答的问题

1. 不修改遗留系统源码，能否稳定完成查询、填写、下载及安全的低风险写入？
2. 能否把页面操作封装成智能体容易理解和调用的业务能力，而不是暴露点击和选择器？
3. 能否让至少两个真实用户分别使用自己的遗留账号和会话，且数据和 Cookie 不串用？
4. 页面变化、登录过期、弹窗和网络异常时，能否给出明确错误并安全停止？
5. 一个新系统从分析到形成可运行连接器需要多少工作量，哪些页面类型最难适配？
6. 能否在最终用户设备不安装 Chrome 扩展、本地 Daemon 或系统连接器的前提下，通过中心 HTTP Session/Browser Worker 完成同样任务？
7. 表单登录能否通过可信认证卡片完成，使账号密码和验证码绕过模型并安全注入中心浏览器？

PoC 不以生产高可用、远程移动接入或完整企业治理为目标。

## 2. 首期范围

### 2.1 系统选择

基线选择 2 个差异明显的系统，系统 C 为 stretch goal：

| 系统 | 建议特征 | 主要验证点 |
|---|---|---|
| 系统 A | 普通 HTML/SPA、表格和表单 | DOM/ARIA 定位、查询、详情读取 |
| 系统 B | iframe、弹窗、分页、下载或上传；当前以 Seeyon OA 为代表 | 复杂页面状态、文件处理、中心登录与会话恢复 |
| 系统 C（加分项） | 有稳定 XHR/内部接口，或存在边界清楚的受控写流程 | HTTP/页面混合适配、W2或简单联动 |

每个系统优先选择 1—2 个高价值、低风险、人工操作路径清楚的业务流程。至少准备两个权限或数据范围不同的测试用户；系统 A/B 中至少一个必须包含可撤销、可回读验证的 W1 流程，否则不满足基线系统选择条件。

当前 Seeyon OA 原型已经证明“现有 BSCLI 实现依赖浏览器桥接”不等于“OA 每项能力天然必须经过浏览器”。PoC 迁移时按三类处理：

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

- 完整远程 MCP Gateway 和手机端业务能力调用；PoC 只验证可由手机或桌面浏览器打开的最小可信认证卡片，不建设完整移动产品；
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
  → 一次性可信认证卡片
  → 中心凭据代理（秘密绕过模型）
```

首期采用一台位于目标系统可达网络区域的中心主机，不建设 Daemon 集群。能力内核、SQLite 账本、凭据代理、HTTP Session、浏览器 Worker 和 Profile 全部在该主机运行；最终用户设备不安装 Chrome 扩展、本地 Daemon 或连接器。现有 BSCLI Chrome 扩展只可用于迁移期接口发现和结果对照，不作为 PoC 验收路径。

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

要求：stdout 只输出 JSON，stderr 输出诊断；禁止 Shell 拼接；返回稳定 `status/error.code`。`session login` 只生成非敏感 `AuthChallenge`，不得在 CLI 或 MCP 参数中接收密码。普通 `agentbridge` 是智能体协议面，不包含认证秘密提交、`authorize` 或 `commit`；这些能力只存在于独立可信入口，并且不得注册到模型工具集合。`invoke` 只允许执行 `effect: read`；W1/W2 通过 `invoke` 直接调用时返回 `WRITE_REQUIRES_PREPARE`。可信确认组件显示冻结计划并取得用户确认后，才可调用授权和提交。

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
- 登录过期时返回 `LOGIN_REQUIRED` 和一次性 `AuthChallenge`，由用户完成认证卡片后再继续原 `operationId`。

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
  → 秘密字段通过独立 TLS 通道提交给凭据代理
  → Browser Worker 填写真实登录页面
  → 核验实际登录账号
  → 会话变为 active
```

- `AuthChallenge` 绑定 `challengeId + userSubject + systemId + sessionId + origin + pageFingerprint + nonce + TTL`，且只能使用一次；
- 卡片字段由已注册登录 Adapter 定义，模型和网页内容不能新增密码字段或修改提交地址；
- 模型只看到挑战 ID、状态和非敏感提示，不得看到账号密码、验证码或 MFA 值；
- 密码、验证码不进入 CLI/MCP 参数、聊天、Trace、截图、HAR、剪贴板、普通日志或分析埋点；
- Browser Worker 使用秘密填写真实页面，让页面自身完成前端加密、动态盐、CSRF 和跳转；
- 首期支持用户名密码和可选的第二阶段验证码；滑块、二维码同机扫码、USB Key、ActiveX 和客户端证书返回 `UNSUPPORTED_AUTH_METHOD` 或转桌面接管，不实现手机远程桌面常态操作。

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

PoC 中所有 W1/W2 写入统一采用：

```text
prepare → trusted authorize → commit → verify
```

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
- 不开放付款、删除、正式审批、归档等高风险动作；
- 发现页面身份、当前用户或目标业务对象不明确时立即停止。

上述条款属于首期运行时约束，不得因为生产治理功能暂缓而省略。

## 9. PoC 输出物

- 可运行的 CLI 和最小能力内核；
- 2 个系统 Adapter，第 3 个为加分项；
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
