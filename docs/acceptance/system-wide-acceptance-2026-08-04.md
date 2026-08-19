# AgentBridge 整体系统验收报告

> 验收日期：2026-08-04
>
> 范围：Agent Workspace、Telegram、微信、OpenClaw、AgentBridge、Task Hub、可信交互、双用户会话与 OA 真实业务闭环

## 1. 验收结论

本轮整体系统验收通过。系统已同时证明：

- 辛国茂和李世玉两个真实身份、四个客户端端点及下游 OA Session 全程隔离；
- 网页端只读结果可以通过自然语言在 Telegram 或微信继续，不需要用户记忆任务号；
- OA 待办、已发、已办、跟踪事项保持独立集合语义；
- 字段填写、执行授权、正式提交、权威回读、撤销和撤销回读形成完整闭环；
- 同一个写计划只提交一次，授权只能消费一次，最终任务能够收敛为终态；
- Outbox 当前无积压、无重复键，当前版本部署后的消息投递没有新增失败；
- 4 个下游 Session 均为 `active`，连续保活没有过期、延迟或循环失败。

## 2. 本轮修复

### 2.1 OpenClaw 启动期并发守卫

Gateway 启动后 90 秒内，若当前 run 仍真实活动，只报告排队进度，不再提前中止或重放。
缺失 run 仍保留 15 秒快速恢复；超过宽限期的活动 run 才进入兜底恢复。这样避免机器刚重启、
模型首轮较慢时把正常请求误判为卡死，同时保留真正失联后的自愈能力。

### 2.2 OA 跟踪事项兼容

OA 某些账号的跟踪列表接口只返回 affair ID，缺少标题和流程元数据。适配器现在先读取
跟踪页的权威 ID，再与该账号的已发、已办结果精确关联；无法关联时显式失败，不把已发、
已办或跟踪事项混为一张列表。

真实回读结果：辛国茂待办 3、已发 215、已办 1028、跟踪 28；李世玉待办 33、已发 39、
已办 39、跟踪 5。

### 2.3 无任务号自然接续

OpenClaw 插件升级为 `0.4.22`。当用户明确使用“刚才、刚刚、最近、上一个、上次”等相对
指代时，服务端从同一 `userSubject + agentHost` 的候选中选择最近任务，记录原因为
`latest_relative_reference`。候选不唯一且用户没有相对指代时，仍要求按人类可读标题和
时间澄清，不默认猜测，也不跨用户选择。

真实链路：

- 辛国茂从网页读取 OA 待办，在 Telegram 说“继续刚才网页里的待办任务，查看第 1 条详情”；
- 李世玉从网页读取 OA 待办，在微信发出同样的自然接续表达；
- 两条链路均复用原 Task，只新增一个 `oa.workflow.detail.get`，没有再次读取待办列表；
- 接续原因均为 `latest_relative_reference`，执行模式均为 `follow_up`。

## 3. 双用户与多端隔离

只读隔离验收覆盖辛国茂网页 + Telegram、李世玉网页 + 微信。运行诊断结果为：

- 用户 2，活动 Endpoint 4，活动任务 0，待投递 0；
- Task 来源、事件、Operation、Interaction、Subscription、Outbox、Timeline、Continuation、
  Endpoint Token 和 Workspace Endpoint 共 10 类一致性违规则全部为 0；
- `operation_task_user_mismatch`、`interaction_task_user_mismatch` 和
  `outbox_task_user_mismatch` 均为 0；
- 李世玉有效 Token 具有 OA 读取、草稿、审批、正式提交和撤销权限，不具有会议创建权限；
- 本轮没有处理李世玉待办，也没有以李世玉身份执行任何业务写入。

## 4. 真实可逆写闭环

经用户在可信卡中确认，本轮仅以辛国茂身份执行一条可撤销测试：

- 时间：2026-08-06 13:30-17:30；
- 路线：济南到章丘；
- 交通：自驾车；
- 事由：`AgentBridge系统性验收测试-AB-SYS-0804-W1`。

执行链路：

1. 字段填写卡完成；
2. 正式提交授权卡完成；
3. `oa.business_trip.submit` 成功一次，Operation
   `7407c864-17ea-431c-a1a7-104af551d462`；
4. OA 权威已发回读确认 affair ID `1841161465448776113`；
5. 撤销说明字段卡完成；
6. 撤销授权卡完成；
7. `oa.workflow.revoke` 成功一次，Operation
   `c1a94b01-66c3-4cf8-b882-38f05ae22e4f`；
8. 已发列表中该事项消失，待发记录显示 `撤销`，用户随后确认已撤销。

两个执行授权均为 `consumed`，4 个字段/授权 Interaction 均为 `completed`。当时提交和撤销
归属同一 Task `f155fb3c-f8af-42a7-b7aa-6470a222e2a6`，最终状态为 `succeeded`。当日测试
范围内正式提交和撤销各 1 次，没有重复业务 Operation。后续界面复盘确认，同一 Task 会让
Workspace 以撤销卡覆盖原提交卡；`0.4.23` 已将撤销修正为独立 Task，只在内部关联原流程，
原提交卡和撤销卡分别保留。

## 5. 运行治理

- AgentBridge `active/running`，本轮服务启动后无 `Traceback`、`ERROR`、`MCP_TIMEOUT` 或
  `RESULT_UNKNOWN`；
- 6 轮 Session 保活均为 `active=4, eligible=4, kept_alive=4, expired=0, deferred=0`；
- 当前 Outbox `pending/delivering=0`，重复 `(eventId, endpointId, payloadType)` 数为 0；
- 李世玉通道保留 11 条历史 `failed` 审计记录，全部发生在 `0.4.22` 部署前；当前版本部署后
  新产生的 5 条微信投递均一次 `acknowledged`，新增失败为 0；
- OpenClaw 当前日志无 `ETIMEDOUT`、工具运行时缺失、`MCP_TIMEOUT` 或 `RESULT_UNKNOWN`。
  日志中的一次 `chmod .openclaw/state EPERM` 来自部署后的独立 CLI 运维探针，不是 Gateway
  业务进程故障；网页、Telegram、微信和真实业务链路同期正常。

## 6. 自动化门禁

代码提交前完整门禁结果：

- Python：485 passed，3 skipped；
- 子测试：199 passed；
- Agent Workspace Node：20 passed；
- OpenClaw 插件：111 passed；
- MCP App：TypeScript 检查与生产构建通过；
- Python compile、`pip check` 和 npm pack dry-run 通过。

## 7. 未覆盖边界

本轮没有执行以下真实业务写入：

- 李世玉待办处理；
- 会议创建；
- 泰华日志正式提交；
- 语雀写入（当前仅开放读取）。

这些是有意保留的安全边界，不影响本轮已覆盖能力的通过结论。下一阶段优先测试可信卡等待
期间的 Gateway 重启恢复，以及同一用户两个可操作消息端对同一授权决定的并发竞争。
