# 多端任务一期、二期深度验收报告

> 验收日期：2026-08-04
>
> 范围：Agent Workspace、Telegram、微信、Task Hub、OA 只读能力和双用户隔离
>
> 安全边界：只读读取待办和详情；未处理待办，未执行任何业务写入

## 1. 验收目标

本轮不是只验证“页面能返回结果”，而是同时核对：

- 一期的跨端文本、任务事件、单卡更新、刷新去重和终态收敛；
- 二期的显式任务选择、原任务接续和 Operation 防重复；
- 辛国茂、李世玉两个真实用户的 Session、端点、任务、时间线和 Outbox 隔离；
- 用户可见耗时与 OA 接口、模型推理、投递阶段的实际耗时分布；
- 当前发布的运行态异常和历史遗留记录是否会影响新链路。

## 2. 环境基线

- AgentBridge：`10.10.50.213`，中心服务、Workspace、卡片和 MCP 由同一
  `agentbridge.service` 承载；
- OpenClaw：2026.7.1，`agentbridge-interactions` 0.4.21；
- 活动身份：`guomao -> 辛国茂`、`lishiyu -> 李世玉`；
- 活动端点：辛国茂网页 + Telegram，李世玉网页 + 微信；
- 两个 OA Session 在验收前后均为 `active`，保活状态均为 `eligible`；
- Workspace `/healthz` 返回 200，验收时段 AgentBridge 服务无 warning/error 日志。

## 3. 一期：双网页近同时只读

| 用户 | 验收码 | Task ID | OA 总数 | 本次返回 | 列表 Operation |
| --- | --- | --- | ---: | ---: | --- |
| 辛国茂 | `XGM-DEEP-0804-A7K2` | `d947836d-24da-4b5b-ab86-ec96d8fe3fbc` | 3 | 2 | 1 次，成功 |
| 李世玉 | `LSY-DEEP-0804-R4M8` | `91c5a46e-1828-4f72-8519-59e48566632e` | 33 | 2 | 1 次，成功 |

结果：

1. 两个网页返回各自 OA 数据，标题、发起人和 `affair_id` 没有交叉；
2. 每个请求只创建一个 Task 和一个 `oa.workflow.pending.list`；
3. 两个列表 Operation 分别耗时约 2.21 秒和 2.29 秒；
4. Task Hub 均显示“任务创建 -> 操作关联 -> 操作成功”，活动任务数最终为 0；
5. 每个网页只有一张对应应用卡。刷新页面后原位置、文本和卡片数量不变，没有历史
   重放或重复追加；
6. 端点页只显示当前用户的两个端点，没有暴露另一个用户的端点。

## 4. 二期：网页任务从消息端继续

辛国茂在 Telegram、李世玉在微信中分别携带准确 `taskId`，要求只读查看原列表第 1 条
详情，并明确禁止重新读取列表和处理事项。

| 用户 | 原任务是否复用 | 新增 Operation | 重复列表 | 业务写入 | 消息端投递 |
| --- | --- | --- | ---: | ---: | --- |
| 辛国茂 | 是 | 1 次 `oa.workflow.detail.get` | 0 | 0 | Telegram 成功 |
| 李世玉 | 是 | 1 次 `oa.workflow.detail.get` | 0 | 0 | 微信成功 |

后台证据：

- 两个 Task 的 `origin_endpoint_id` 保持为原网页端点；
- `active_conversation_ref` 分别切换到 Telegram 和微信私聊；
- 两条 continuation 均为 `selected + follow_up + explicit_task_id`；
- 每个 Task 正好关联“列表 + 详情”两个成功 Operation；
- 每个 Task 的 5 个 `task_event` Outbox 项均为 `acknowledged`；
- 网页按顺序显示消息端指令、原卡片进度更新和详情回复；原应用卡没有复制；
- `user_timeline` 全库没有重复 `dedupe_key`。

## 5. 隔离与运行态

接续完成后重新运行双用户隔离验收：

- 用户数 2，活动端点 4，活动任务 0，未确认投递 0；
- 两个用户各有一个已选接续状态，且 Task、Endpoint、UserSubject 一致；
- Task 来源、TaskEvent、Operation、Interaction、Subscription、Outbox、Timeline、
  Continuation、Endpoint Token 和 Workspace Endpoint 共 10 类违规均为 0；
- 辛国茂端没有李世玉标记或业务数据，李世玉端没有辛国茂标记或业务数据。

运行诊断仍显示李世玉有 2 条历史失败投递。只读核查确认它们是 2026-08-03 旧版本
对一次“LSY 网页检查 OA 登录状态”的用户文本和助手文本镜像，均无 Task、无业务写入，
且早于 0.4.21 部署。本轮新任务及接续投递全部确认，不受这两条历史记录影响。

## 6. 性能分解

| 链路 | 用户感知约耗时 | OA Operation | 主要额外耗时 |
| --- | ---: | ---: | --- |
| 辛国茂网页读取 | 34 秒 | 2.21 秒 | 模型理解、工具编排和最终回答 |
| 李世玉网页读取 | 38 秒 | 2.29 秒 | 模型理解、工具编排和最终回答 |
| Telegram 接续详情 | 32 秒 | 4.71 秒 | 多轮模型请求和结果整理 |
| 微信接续详情 | 39 秒 | 5.03 秒 | 多轮模型请求、消息通道发送 |

本轮 OpenAI 模型请求单次约 2.9--5.0 秒，但一个业务回合会发生多轮请求，因此主要
耗时不在 OA 接口和 AgentBridge 数据库。后续性能优化应优先减少只读任务的模型轮次、
压缩工具结果和避免重复总结，而不是继续优化已经为 2--5 秒的 OA 调用。

## 7. 本轮发现与修复

### 7.1 网页端点最近活动时间陈旧

现象：两个网页刚完成真实请求，端点页仍显示辛国茂 07-30、李世玉 08-02。

原因：Workspace 账号首次注册端点时会写 `last_seen_at`，后续已认证网页请求只刷新
Workspace Session，没有同步刷新 `client_endpoints`。

修复：已认证 Workspace 请求现在只触达当前账号所属且仍为 active 的网页端点，更新
`updated_at` 和 `last_seen_at`。它不会重建端点、切换身份、扩大权限或复活停用端点。

### 7.2 OpenClaw CLI 运维诊断可能挂起

`openclaw gateway status/health/call health` 在当前 Windows 环境可长时间不返回，外层
超时后还可能残留 CLI 子进程。一次沙箱内诊断还记录了 `.openclaw/state` 的 `EPERM`
告警；显式放宽文件权限后直接健康 RPC 仍可复现挂起。

这不是本轮用户链路故障：同一时段网页、Telegram、微信、Gateway 日志和 Task Hub
均正常，OpenClaw 实际网关进程保持监听，残留的诊断子进程已单独清理。后续应给部署
脚本的外部 OpenClaw 命令增加进程树级超时与清理，并单独定位 OpenClaw 2026.7.1 / 
Node 26.5.0 的 CLI 行为；在根因明确前，以真实 RPC 日志、端口监听和受控业务烟测组合
判断运行态，不把一个挂起的 CLI 命令直接等同于网关失效。

## 8. 结论与剩余风险

一期和二期的双用户真实只读链路通过：同步、去重、单卡、原任务接续、Operation 防重、
消息端投递和身份隔离均有页面与数据库双重证据。

剩余风险按优先级为：

1. 跨端文件领取、断点续传、失败恢复和双用户文件串号验收；
2. 同一用户两个可操作消息端对同一授权决定的真实竞争验收；
3. Gateway 在可信卡等待期间重启的当前版本真实恢复验收；
4. OpenClaw CLI 运维诊断挂起的进程级治理；
5. 两个真实用户同时进行可回读、可撤销写任务的隔离验收。
