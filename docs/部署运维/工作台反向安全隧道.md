# Agent Workspace 反向 SSH 隧道

本文记录 AgentBridge Linux 中心端与 Windows OpenClaw 工作站之间的当前连接方案，
用于解释运行原理、进程边界、切网恢复、安全限制和日常排障。本文描述的是当前
内网 PoC 基线，不把该隧道扩展为 VPN、远程桌面或通用端口代理。

## 1. 目标与边界

Agent Workspace 部署在 `10.10.50.213`，OpenClaw Gateway 运行在 Windows 工作站的
`127.0.0.1:18789`。工作站可能在有线、无线、家庭或度假网络之间切换，局域网 IP
不能作为 AgentBridge 的稳定配置。

当前方案由 Windows 主动连接固定的 213 服务器，并通过 SSH 反向端口转发，在服务器
回环地址建立 `127.0.0.1:18789`。AgentBridge 始终连接该回环地址，不再感知工作站
当前 IP。

该隧道只承载 AgentBridge 到 OpenClaw Gateway 的 WebSocket 流量：

- 浏览器仍直接访问 213 上的 Agent Workspace，不经过 SSH 隧道；
- 可信卡片、MCP、管理端和目标系统会话仍由 AgentBridge 中心端负责；
- Telegram 和微信由本机 OpenClaw 通道独立接收消息，不依赖 Workspace 浏览器；
- 隧道中断时，已经落库的任务、时间线和文件记录仍保留，但 Workspace 暂时不能
  发起新的 OpenClaw 运行。

## 2. 数据流

```mermaid
flowchart LR
    B["用户浏览器"] -->|"HTTPS :8783"| A["213 AgentBridge Python"]
    A -->|"ws://127.0.0.1:18789"| R["213 sshd 反向监听"]
    R ==>|"SSH 加密连接 :22"| S["Windows ssh.exe"]
    S -->|"TCP 127.0.0.1:18789"| O["Windows OpenClaw Gateway"]
    O --> S --> R --> A --> B
```

工作站执行的核心参数是：

```text
-R 127.0.0.1:18789:127.0.0.1:18789
```

三个地址从左到右分别表示：

1. 在 213 服务器上只监听 `127.0.0.1:18789`；
2. 收到连接后，通过现有 SSH 会话把字节送到 Windows；
3. Windows `ssh.exe` 再连接本机 `127.0.0.1:18789` 的 OpenClaw Gateway。

AgentBridge 的对应参数是：

```text
--workspace-gateway-url ws://127.0.0.1:18789
```

AgentBridge 和 OpenClaw 之间仍执行 Gateway Token 校验及已配对设备校验。SSH 隧道只
提供网络可达性，不替代应用身份认证。

## 3. 两端进程

### 3.1 Windows 工作站

任务计划程序在当前用户登录时启动：

```text
Windows Task Scheduler
└─ powershell.exe
   └─ ssh.exe
```

各进程职责如下：

| 进程 | 来源 | 职责 |
| --- | --- | --- |
| `powershell.exe` | `Start-AgentBridgeWorkspaceTunnel.ps1` | 构造固定 SSH 参数；SSH 退出后等待 5 秒并重新启动 |
| `ssh.exe` | Windows OpenSSH | 维持到 `10.10.50.213:22` 的连接并提供反向端口转发 |
| `node.exe` | OpenClaw Gateway | 独立监听本机 `18789`，不属于隧道子进程 |
| `OpenConsole.exe` / `conhost.exe` | Windows 控制台宿主 | 承载 PowerShell 或 OpenClaw 控制台；不参与业务协议 |

进程 PID 会随重启变化，不应写入配置或监控规则。当前任务使用
`-WindowStyle Hidden`，但 Windows Terminal 被配置为默认控制台宿主时仍可能创建一个
可见 PowerShell 窗口。关闭该窗口会终止当前隧道，不应把它当成重复 OpenClaw 关闭。

### 3.2 Linux 中心端

```text
systemd
└─ AgentBridge Python

sshd 主进程
└─ sshd: root
   └─ 监听 127.0.0.1:18789
```

AgentBridge Python 继续监听 `8780`、`8782`、`8783` 和 `8790`。每条工作站 SSH 会话
由一个 `sshd` 子进程维护；反向端口属于该子进程。实际 Workspace 请求到达时，sshd
在同一 SSH 连接中建立转发通道，不会在 213 上启动 OpenClaw、Node.js 或第二个
AgentBridge。

## 4. 启动与恢复机制

安装入口：

```powershell
.\scripts\Install-AgentBridgeWorkspaceTunnel.ps1
```

安装脚本创建当前用户级任务 `AgentBridge Workspace Tunnel`，配置为：

- 当前 Windows 用户登录时启动；
- 同一时刻只保留一个实例；
- 使用专用 AgentBridge SSH 私钥和仓库内固定 Host Key；
- 不交互读取密码；
- SSH 每 30 秒发送一次保活，连续 3 次无响应后退出；
- SSH 退出后 PowerShell 等待 5 秒重新连接；
- 任务异常退出时，任务计划程序还会按 1 分钟间隔重启。

换网时的恢复顺序是：

1. 旧 TCP/SSH 连接失效；
2. OpenSSH 通过网络错误或保活超时确认连接已经断开；
3. `ssh.exe` 退出；
4. PowerShell 等待 5 秒；
5. 工作站从新 IP 主动连接 `10.10.50.213:22`；
6. 新 `sshd` 子进程重新绑定服务器 `127.0.0.1:18789`；
7. AgentBridge 后续 Gateway 请求自动使用新隧道。

因此“5 秒重连”从 SSH 已经退出后开始计算。若网络静默丢包，断线发现本身最长可能
接近 90 秒；操作系统立即报告断网时通常更快。

## 5. 安全边界

- 服务器反向端口固定绑定 `127.0.0.1`，不允许内网或公网直接访问 `18789`；
- 传输经过 SSH 加密，不发送 SSH 密码，私钥保留在 Windows 用户目录；
- `IdentitiesOnly=yes` 固定使用专用密钥，`UserKnownHostsFile` 固定校验 213 Host Key；
- `ExitOnForwardFailure=yes` 保证端口未成功建立时 SSH 立即失败，不产生“连接存在但
  转发不可用”的假健康状态；
- `-N -T` 表示本隧道不执行远端命令、不分配交互终端；
- Gateway Token 和 OpenClaw 设备配对仍是第二层应用认证；
- 当前 PoC 使用现有 `root` 专用 SSH 密钥。生产化时应改为只允许该反向转发的受限
  服务账号和受限公钥，避免让隧道密钥具备通用远程管理能力。

## 6. 日常检查

### 6.1 Windows

```powershell
Get-ScheduledTask -TaskName "AgentBridge Workspace Tunnel"
Get-ScheduledTaskInfo -TaskName "AgentBridge Workspace Tunnel"

Get-CimInstance Win32_Process |
  Where-Object {
    $_.CommandLine -like "*Start-AgentBridgeWorkspaceTunnel.ps1*" -or
    $_.CommandLine -like "*127.0.0.1:18789:127.0.0.1:18789*"
  } |
  Select-Object ProcessId, ParentProcessId, Name, CommandLine
```

任务运行时 `LastTaskResult=267009`（十六进制 `0x41301`）表示“任务仍在运行”，不是
失败代码。

需要重新建立隧道时：

```powershell
Stop-ScheduledTask -TaskName "AgentBridge Workspace Tunnel"
Start-ScheduledTask -TaskName "AgentBridge Workspace Tunnel"
```

### 6.2 Linux

```bash
systemctl is-active agentbridge
ss -ltnp '( sport = :18789 )'
curl -sS --max-time 8 http://127.0.0.1:18789/ -o /dev/null -D -
```

正常状态应满足：

- AgentBridge 为 `active`；
- `127.0.0.1:18789` 由 `sshd` 监听；
- 服务器访问该地址可获得 OpenClaw HTTP 响应；
- `https://10.10.50.213:8783/healthz` 返回 Workspace `ok`。

## 7. 故障定位

| 现象 | 优先检查 | 说明 |
| --- | --- | --- |
| Workspace 页面完全打不开 | `8783/healthz`、AgentBridge systemd | 这是 Workspace HTTPS 或中心服务问题，不一定与隧道有关 |
| 页面可打开，但发送任务失败 | 213 的 `127.0.0.1:18789`、Windows 计划任务、OpenClaw Gateway | 常见于隧道或 Gateway 不可用 |
| TG/微信可用，Workspace 发送失败 | 反向监听和 Workspace Gateway | 消息通道独立可用，进一步指向隧道链路 |
| Windows 切网后短时不可用 | SSH 进程和计划任务状态 | 等待断线发现与 5 秒重连，不修改服务器 IP |
| 213 没有 `18789` 监听 | Windows `ssh.exe`、22 端口可达、密钥和 Host Key | 隧道未成功建立 |
| 213 有监听但不能访问 OpenClaw | 本机 OpenClaw `18789`、Gateway 进程 | SSH 正常，但目标本地服务未监听 |
| PowerShell 窗口关闭后网页任务失败 | 重新启动计划任务 | 当前窗口承载隧道守护脚本 |

不要通过修改代理、防火墙、OpenClaw 对外监听地址或把 `18789` 暴露到内网来绕过故障。
先按“Workspace HTTPS、服务器反向监听、SSH 会话、本机 Gateway”四段逐一定位。

## 8. 当前验证基线

2026-08-18 至 2026-08-19 已完成：

- Windows 计划任务和 SSH 子进程持续运行；
- 213 上 `127.0.0.1:18789` 由对应 `sshd` 子进程监听；
- 213 通过隧道访问本机 OpenClaw 返回 HTTP 200；
- 工作站 IP 变化后不再修改 AgentBridge systemd 参数；
- 同时建立 12 条不完成 TLS 的客户端连接时，Workspace `healthz` 仍返回 200；
- 释放故障连接后 `8783` 监听队列恢复为 0，AgentBridge 无 error 和异常重启。

最后一项同时依赖中心 HTTPS 服务的“TLS 握手在线程内限时执行”修复。它解决的是
客户端换网后半开连接堵塞 Workspace 接收线程的问题，与 SSH 隧道断线重连属于两个
不同层面的可靠性机制。
