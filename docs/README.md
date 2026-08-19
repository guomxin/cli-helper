# AgentBridge 文档中心

这里是项目文档的唯一入口。根目录 [README](../README.md) 负责说明项目是什么和如何开始；
本文负责告诉开发者、运维人员和适配实施人员应该相信哪份文档、到哪里维护它。

## 先读这三份

1. [项目当前状态](./project-status.md)：截至 2026-08-19 的系统、能力、部署、用户端和待办事项，是当前事实的总入口。
2. [目标架构](./architecture/agent-oriented-legacy-bs-adaptation-design.md)：AgentBridge 的长期边界、能力模型、身份、会话和安全设计。
3. [当前内网部署](./operations/current-deployment-plan.md)：线上拓扑、端口、服务、发布、健康检查和恢复方式。

## 目录结构

| 目录 | 保存内容 | 维护规则 |
| --- | --- | --- |
| [architecture](./architecture/) | 跨系统架构、交互协议、写治理、多端任务和身份路由 | 行为或信任边界变化时更新 |
| [platform](./platform/) | Workspace、管理控制台、远程 MCP 等平台能力 | 平台体验或接口变化时更新 |
| [operations](./operations/) | 现行部署、发布、开发策略和运维手册 | 只描述当前可执行流程 |
| [systems](./systems/README.md) | 按 OA、泰华、语雀、照明系统划分的适配资料 | 一个系统一个子目录 |
| [acceptance](./acceptance/README.md) | PoC 基线、阶段验收和带日期的证据快照 | 已完成快照原则上只勘误，不覆盖历史结论 |
| [roadmap](./roadmap/) | 未完成事项和生产化门槛 | 状态变化时移入现行文档或标记完成 |
| [archive](./archive/README.md) | 已退役或被替代的设计与演进记录 | 不得作为当前实现、命令或部署依据 |

## 架构与安全

- [智能体交互协议](./architecture/agent-interaction-protocol.md)：认证卡、字段卡、授权卡和恢复协议。
- [受控写模型](./architecture/governed-write-model.md)：`prepare -> authorize -> commit -> verify` 的边界。
- [多端任务延续设计](./architecture/omnichannel-agent-task-continuity-design.md)：Workspace、Telegram、微信之间的任务、消息、卡片和文件同步。
- [OpenClaw 多用户身份路由](./architecture/openclaw-multi-user-identity-routing.md)：聊天身份、MCP Token、用户主体和下游账号的隔离。

## 平台与接入

- [Agent Workspace](./platform/agent-workspace.md)：独立网页客户端、身份配对、多模态输入和跨端任务视图。
- [管理控制台](./platform/agentbridge-admin-console.md)：用户、会话、Token、任务、操作、审计和写暂停。
- [远程 MCP 接入](./platform/remote-mcp-onboarding.md)：OpenClaw 等宿主的低客户端安装接入方式。

## 运维与开发

- [当前内网部署](./operations/current-deployment-plan.md)：当前线上运行基线。
- [Workspace 反向 SSH 隧道](./operations/agent-workspace-reverse-ssh-tunnel.md)：工作站切网后的连接与排障。
- [开发验证与发布](./operations/development-and-release-workflow.md)：分层测试、构建、部署和冒烟。
- [开发安全策略](./operations/development-policy.md)：真实系统验证和写操作的强制边界。
- [发布手册](./operations/release-runbook.md)：日常发布步骤和故障处置。

## 路线图

- [后续增强事项](./roadmap/deferred-considerations.md)：企业级身份、隔离、密钥、可用性、审计和灾备事项。

## 文档治理约定

- `docs/project-status.md` 是“现在做到哪里”的唯一汇总，不在多份设计中重复维护版本数字。
- 现行文档不追加流水账；有长期追溯价值的演进记录进入 `archive`，验收证据进入 `acceptance`。
- 系统专属内容放入 `systems/<system>/`，跨系统机制才放入 `architecture` 或 `platform`。
- 新验收使用 `主题-YYYY-MM-DD.md`；已发布的验收快照只做事实勘误，不改写当时结果。
- 移动文件或改名时必须更新链接并运行 `python -m unittest tests.test_documentation`。
- 文档不得包含密码、Bearer Token、Cookie、私钥、临时卡片 URL 或其他可复用秘密。
