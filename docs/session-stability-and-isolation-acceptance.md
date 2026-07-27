# 会话稳定性与多用户隔离验收

本文定义 AgentBridge 中央会话、OpenClaw 身份路由和受控保活的可重复验收方法。
所有命令默认只读，不提交 OA 流程，不创建泰华日志，也不操作第二用户待办。

## 1. 验收目标

每次涉及身份路由、Token、会话恢复、保活、Credential Broker 或 MCP 接入的改动，
至少证明：

1. 每个聊天身份只解析到自己的 MCP Token 和 `userSubject`；
2. 同一用户在多轮检查中的 `systemId`、`userSubject` 和会话保持稳定；
3. 不同用户不能解析到同一个 `userSubject`；
4. 一个用户过期、刷新失败或 Token 被吊销时，不影响其他用户；
5. OA 与泰华状态相互独立，不能因为一个系统失效而清除另一个系统的会话；
6. 输出不得包含 Bearer Token、Cookie、刷新令牌、密码或验证码。

## 2. 单身份只读检查

显式选择身份，不再依赖 OpenClaw 配置中的第一个 Token：

```powershell
.\scripts\Test-AgentBridgeMcp.ps1 `
  -Check SessionStatus `
  -IdentityLabel "辛国茂"
```

可用的只读检查：

| Check | 作用 |
|---|---|
| `SessionStatus` | 实时检查 OA 会话 |
| `TaihuaSessionStatus` | 实时检查泰华会话 |
| `OaPendingRead` | 最多读取一条 OA 待办并返回数量摘要 |
| `TaihuaMyLogs` | 最多读取一条个人日志并返回数量摘要 |
| `WorkflowCollections` | 核对待办、已发、已办和跟踪四个集合 |
| `Release` | 核对 MCP 工具目录和 OA 会话 |

身份还可以使用 `-IdentityChannel` 和 `-IdentitySenderId` 精确选择。选择条件必须只命中
一个具有可用 Token 的绑定，否则脚本失败关闭。

## 3. 双用户即时隔离

```powershell
.\scripts\Test-AgentBridgeIdentityIsolation.ps1 `
  -IdentityLabel "辛国茂","李世玉" `
  -Check SessionStatus `
  -Cycles 2 `
  -IntervalSeconds 2
```

成功条件：

- 两个身份均为 `active`；
- 返回两个不同的 `userSubject`；
- 每个身份在两轮中的会话指纹保持一致；
- 下游账号分别匹配绑定用户；
- 最终输出不含 Token。

该检查只调用 OA 会话状态，不读取或处理第二用户待办。

## 4. 长时稳定性观察

下面的命令每 30 分钟检查一次，连续运行 24 小时：

```powershell
.\scripts\Test-AgentBridgeIdentityIsolation.ps1 `
  -IdentityLabel "辛国茂","李世玉" `
  -Check SessionStatus `
  -Cycles 48 `
  -IntervalSeconds 1800
```

脚本只输出最终摘要；任一身份变为非活动状态、身份发生变化或两个标签解析到同一主体时
立即失败。需要观察中间进度时，应由外层任务调度器记录每次独立调用，不得把 Token 写入
日志。

泰华当前只为辛国茂配置权限，可单独执行：

```powershell
.\scripts\Test-AgentBridgeIdentityIsolation.ps1 `
  -IdentityLabel "辛国茂" `
  -Check TaihuaSessionStatus `
  -Cycles 48 `
  -IntervalSeconds 1800
```

## 5. 故障隔离

自动化测试必须覆盖：

- 一个会话明确过期时，仅删除该会话的加密状态，其他用户继续保活；
- 一个 Token 被吊销后，仅该 Token 验证失败，另一用户 Token 保持 active；
- 临时刷新失败保留原会话，不能误判为退出；
- 不同系统使用不同会话记录和加密状态。

生产 Token 的吊销属于管理操作，不在普通烟测中执行。真实吊销演练应签发短期临时
Token，先验证两个 Token 均可用，只吊销临时 Token，再确认正式 Token 不受影响。

## 6. 失败处理

| 现象 | 处理 |
|---|---|
| 身份选择命中 0 个或多个绑定 | 检查 OpenClaw `identityBindings`，不要回退到第一个 Token |
| 会话为 expired | 只为该用户发起登录卡，登录成功后续办原请求 |
| `SESSION_CHECK_UNAVAILABLE` | 保留会话，检查网络或下游服务，不要求用户重新输入密码 |
| 两个标签映射到同一主体 | 立即停止，核对 Token 签发和聊天身份绑定 |
| 多轮中会话指纹变化 | 检查服务重启、状态目录、会话重建和跨用户路由 |
| 泰华刷新失败但访问令牌仍有效 | 继续使用当前访问令牌，并在真正失效时返回明确登录要求 |

## 7. 发布记录

每次验收记录以下非敏感证据：

- Git 提交和 Linux Release；
- 检查时间、周期、身份标签和系统；
- `userSubject`、下游账号、会话状态和保活状态；
- 只读列表的数量摘要；
- 是否执行真实业务写入。

严禁记录 Token、Cookie、密码、验证码或完整可信卡片 URL。
