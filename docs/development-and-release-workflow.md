# AgentBridge 开发验证与发布流程

> 适用环境：Windows 开发机、OpenClaw 本机宿主、Linux AgentBridge `10.10.50.213:/home/guomao/agentbridge`
>
> 目标：把日常反馈、发布前回归、远程实机冒烟和 Linux 部署分层，避免反复建环境、重复调用慢速 CLI、重复重启 OpenClaw Gateway。

## 1. 四条执行路径

| 路径 | 用途 | 是否访问真实 AgentBridge/OA | 是否重启服务 |
|---|---|---:|---:|
| 定向验证 | 修改后快速反馈 | 否 | 否 |
| 全量验证 | 提交或发布前回归 | 否 | 否 |
| MCP 冒烟 | 验证中心服务、TLS、身份和 OA 会话 | 是 | 否 |
| wheel 部署 | 发布中心 Python 包并自动冒烟 | 是 | AgentBridge；OpenClaw 默认不重启 |

这四条路径不能相互替代。单元测试证明代码契约，MCP 冒烟证明当前部署可用，wheel 部署证明发布链路可重复。

## 2. 持久测试环境

统一入口为 `scripts/Invoke-AgentBridgeValidation.ps1`。默认环境位于：

```text
%LOCALAPPDATA%\AgentBridge\test-venv-py312
```

脚本根据 Python 版本和 `pyproject.toml` SHA-256 生成依赖指纹。指纹不变时直接复用环境；指纹变化时才重新安装项目、pytest 和 wheel 构建后端。可以通过 `AGENTBRIDGE_TEST_PYTHON`、`-BootstrapPython` 或 `-VenvPath` 显式覆盖。

首次初始化需要安装依赖，可能耗时数分钟。不要因为首次慢而删除环境，否则每次都会重新付出同样成本。

## 3. 日常定向验证

Python 改动优先选择直接相关的测试文件：

```powershell
.\scripts\Invoke-AgentBridgeValidation.ps1 `
  -Mode Targeted `
  -PythonTests @('tests/test_auth_challenges.py', 'tests/test_central_service.py')
```

OpenClaw 插件改动：

```powershell
.\scripts\Invoke-AgentBridgeValidation.ps1 -Mode Targeted -OpenClaw
```

只有修改包清单或准备发布插件时才增加打包检查：

```powershell
.\scripts\Invoke-AgentBridgeValidation.ps1 -Mode Targeted -OpenClaw -PackCheck
```

MCP App 改动：

```powershell
.\scripts\Invoke-AgentBridgeValidation.ps1 -Mode Targeted -McpApp
```

`Targeted` 至少要求一个测试路径或组件开关，避免看似成功但实际没有执行任何检查。

## 4. 发布前全量验证

```powershell
.\scripts\Invoke-AgentBridgeValidation.ps1 -Mode Full
```

全量模式执行：

- Python 全量 pytest；
- `compileall`；
- `pip check`；
- OpenClaw 插件测试；
- OpenClaw `npm pack --dry-run` 包清单检查。

只有已经完成同一工作区、同一提交候选的全量验证时，开发态部署才可以显式使用 `-SkipValidation`。正式发布不要跳过。

## 5. 真实 MCP 冒烟

会话状态探针：

```powershell
.\scripts\Test-AgentBridgeMcp.ps1 -Check SessionStatus
```

它通过正式内网 HTTPS MCP 调用 `oa_session_status`，不会创建认证卡，也不会执行 OA 业务写入。

发布运行时联合探针：

```powershell
.\scripts\Test-AgentBridgeMcp.ps1 -Check Release
```

它先要求公开 MCP 工具目录包含当前补签和会议工具，再调用 `oa_session_status`。工具目录缺失时以 `MCP_TOOL_CATALOG_INCOMPLETE` 失败，防止“新 wheel 已安装、systemd 仍从旧源码启动”被普通会话探针掩盖。

登录复用探针：

```powershell
.\scripts\Test-AgentBridgeMcp.ps1 -Check LoginReuse
```

它调用 `oa_session_login`。会话有效时应返回 `reused=true`、`hasInteraction=false`；会话已过期时可能创建或复用安全登录卡，但仍不会执行 OA 业务写入。

冒烟脚本直接读取 `%USERPROFILE%\.openclaw\openclaw.json`，并从进程、用户、机器环境或 `%USERPROFILE%\.openclaw\.env` 解析 `${VAR}` 引用，不再为每次探针调用多个 `openclaw config get`。Bearer 只通过子进程标准输入传给本地 Node 客户端，不进入命令行、JSON 摘要或日志；输出仅包含白名单状态字段。

## 6. wheel 一键部署

先看计划，不访问服务器：

```powershell
.\scripts\Deploy-AgentBridge.ps1 -PlanOnly
```

正式执行：

```powershell
.\scripts\Deploy-AgentBridge.ps1
```

脚本会：

1. 默认拒绝存在已跟踪未提交修改的工作区；
2. 运行发布验证；
3. 使用持久环境构建标准 wheel；
4. 单次 SCP 上传；
5. 单次 SSH 完成版本化留存、安装、`compileall`、`pip check`，并安装受版本控制的 systemd unit；
6. 校验服务工作目录、Python `-P` 安全路径和 `bscli` 的 site-packages 来源后重启；
7. 通过正式 HTTPS MCP 自动执行 `Release` 工具目录与会话联合冒烟。

需要同时验证登录复用时：

```powershell
.\scripts\Deploy-AgentBridge.ps1 -IncludeLoginReuseSmoke
```

仅开发态、且刚刚在同一工作区通过全量验证时：

```powershell
.\scripts\Deploy-AgentBridge.ps1 `
  -AllowDirty `
  -SkipValidation `
  -IncludeLoginReuseSmoke
```

`-AllowDirty` 会把发布 ID 标记为 `-dirty`，不能作为正式发布证据。

只有 OpenClaw 插件或其持久运行配置变化时才使用：

```powershell
.\scripts\Deploy-AgentBridge.ps1 -RestartOpenClaw
```

Windows 托管 Gateway 重启实测可能超过两分钟。中央 Python 包、systemd 配置或 OA 适配代码变化，不应顺带重启 Gateway。

## 7. 安全边界

- 自动冒烟只包含 `oa_session_status`；可选登录复用只包含 `oa_session_login`。
- 脚本不包含待办审批、草稿保存、会议预订或任何 OA 业务写工具。
- 真实 OA 写操作仍必须走 prepare、可信字段卡、执行授权、commit、回读验证，并由用户针对具体事项确认。
- OpenClaw Bearer、Cookie、密码、卡片 URL 和业务字段不得写入仓库、命令行或普通日志。
- `output/release` 是本地临时构建目录并被 Git 忽略；Linux 版本化 wheel 位于 `/home/guomao/agentbridge/releases/<release-id>/`。

## 8. 2026-07-17 基准

在当前 Windows 工作站和已缓存依赖环境中：

| 操作 | 结果 | 墙钟时间 |
|---|---|---:|
| 定向 Python 31 项 + OpenClaw 25 项 | 全部通过 | 59.10 秒 |
| 全量 Python + OpenClaw + 包检查 | `200 passed, 3 skipped, 19 subtests`；Node `25/25` | 69.17-91.22 秒 |
| `SessionStatus` 真实 MCP 冒烟 | OA 会话 `active` | 6.73 秒 |
| `LoginReuse` 真实 MCP 冒烟 | `reused=true`，无 interaction | 8.96 秒 |
| wheel 构建、上传、安装、重启和双冒烟 | 成功 | 36.10-62.29 秒 |

此前同一定向组合在重复打包和慢速配置解析下约为 91 秒；最初创建环境和安装依赖约为 526 秒。当前剩余耗时主要来自真实测试本身，而不是反复初始化。

以上时间来自连续实测，不是硬性 SLA。网络、OA、杀毒扫描和磁盘状态会造成波动。

## 9. 推荐节奏

1. 修改代码后跑最小相关定向验证。
2. 涉及远程发布或工具契约时补一次 `Release`；纯会话逻辑可用 `SessionStatus`，登录逻辑变化时再补 `LoginReuse`。
3. 准备提交时跑全量验证。
4. 先 `-PlanOnly`，再执行 wheel 部署。
5. 仅当插件变化时重启一次 Gateway，并等待其完全收敛。
6. 部署成功后以自动 MCP 冒烟结果为准，不再手工重复同一批检查。