# AgentBridge 文档导航

本文档目录只列出当前有效资料。已经退役的浏览器桥、localhost daemon、
代理型 CLI 和早期探索方案统一放在 [archive](./archive/README.md)，不得作为
实现或部署依据。

## 从这里开始

- [项目总览](../README.md)：能力范围、快速入口和安全不变量。
- [目标架构](../agent-oriented-legacy-bs-adaptation-design.md)：面向智能体的中心化
  B/S 遗留系统适配设计。
- [PoC 验证计划](../poc-validation-plan.md)：已验证能力、证据和待验收事项。
- [后续增强事项](../deferred-considerations.md)：暂缓到生产化阶段的问题。

## 架构与安全

- [智能体交互协议](./agent-interaction-protocol.md)：认证卡、字段卡、授权卡及恢复协议。
- [受控写模型](./governed-write-model.md)：写操作的信任边界、状态机和验收要求。
- [多端智能体任务延续设计](./omnichannel-agent-task-continuity-design.md)：独立用户网页端、
  Telegram、微信之间的任务延续、手机确认与状态通知目标设计。
- [Agent Workspace 网页端](./agent-workspace.md)：二期网页客户端、一次性身份配对、
  OpenClaw Gateway BFF、会话安全和部署验收。
- [OpenClaw 多用户身份路由](./openclaw-multi-user-identity-routing.md)：聊天身份到
  AgentBridge Token 和用户会话的隔离。
- [会话稳定性与多用户隔离验收](./session-stability-and-isolation-acceptance.md)：
  显式身份烟测、长时观察和单方故障验收。
- [开发安全策略](./development-policy.md)：开发和真实环境验证的强制边界。

## 部署与运维

- [当前内网部署](./current-deployment-plan.md)：10.10.50.213 的运行架构和部署记录。
- [管理控制台](./agentbridge-admin-console.md)：管理员身份、运行总览、会话、Token、写暂停与追加式审计。
- [开发验证与发布](./development-and-release-workflow.md)：测试、构建、部署和冒烟流程。
- [远程 MCP 接入](./remote-mcp-onboarding.md)：OpenClaw 等宿主的低安装接入方式。

## 系统适配

- [OA 事项能力矩阵](./oa-matter-matrix.md)：发起与接收处理的流程覆盖情况。
- [OA 写能力扩展手册](./oa-write-action-expansion-playbook.md)：从探索证据到正式能力的
  提升流程。
- [OA 证书扫描件检索与下载](./oa-certificate-document-download.md)：文档中心目录、按名称检索和短时可信下载边界。
- [泰华日志系统适配](./taihua-log-system-adapter.md)：API、会话和日志能力说明。
- [部门信息库（语雀）适配](./yuque-department-knowledge-adapter.md)：交互式登录、跨库检索、Doc/Sheet/Table 结构化读取与敏感内容脱敏。

## 退役与归档

- [旧浏览器桥退役记录](./legacy-bridge-retirement.md)：删除范围、保留边界和防回归规则。
- [历史资料目录](./archive/README.md)：仅用于追溯，不得复制回当前运行时。
