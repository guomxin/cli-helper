# 旧浏览器桥退役记录

> 状态：已完成。退役前最后一个完整旧运行时基线为 `2d6a06b`。

## 1. 最终结论

旧 Chrome 扩展、浏览器轮询桥、localhost daemon、daemon 版 MCP 和代理型 CLI
已经退出源码、部署包和公开工具面。当前唯一运行路径是中心 AgentBridge：

```text
智能体宿主
  -> HTTPS MCP / CLI
  -> 用户身份与最小权限
  -> 中心会话和受控 Worker
  -> 工作流专属适配器
  -> 目标系统
```

运行时不会检测、启动或回退到员工个人浏览器中的扩展。

## 2. 已删除内容

一期删除了：

- `extension/`；
- `bscli/browser/bridge.py`；
- `bscli/daemon/`；
- 旧 daemon MCP server；
- 旧 `CommandRegistry`、`RuntimeEngine`、`TraceStore` 和工具清单运行框架；
- `daemon`、`oa`、`explore`、`command`、`discovered` 等代理型 CLI；
- 只验证扩展、daemon HTTP、轮询任务或代理型 CLI 的测试。

二期继续删除了：

- `auth_mode=chrome_extension` 的运行时自动迁移分支；
- 对外结果中的 `browser_bridge_used` / `browserBridgeUsed` 兼容字段；
- 根目录和当前文档区中的旧桥设计、旧命令手册与早期实现计划。

已知本机和 Linux 部署配置都使用 `auth_mode=central_session`。未知认证模式现在直接
失败，不再静默转换。

## 3. 当前保留内容

以下名称虽然涉及浏览器或历史系统，但属于当前架构，不是旧桥残留：

- `CentralBrowserWorker`：运行在中心服务身份下的受控 Playwright Worker；
- `CentralHttpWorker`：保存每用户 HTTP Cookie 或 Token 状态的中心执行器；
- 可信认证卡、字段卡、授权卡与 Credential Broker；
- CLI、远程 MCP 和 OpenClaw 多用户身份路由；
- 从旧实现中提取出的纯解析逻辑、页面契约、事项知识和核验规则；
- `transport` 字段，用于说明实际执行通道，例如 `central_http_session`、
  `central_browser_session` 或 `central_http_token`。

这些组件不连接 Chrome 扩展，不依赖员工个人浏览器 Profile，也不暴露任意浏览器控制。

## 4. 历史资料

旧实现只允许通过以下方式追溯：

- Git 基线 `2d6a06b`；
- [历史资料目录](./archive/README.md)；
- 本退役记录中的删除边界。

历史资料可用于提取业务字段、后台 API、页面动作和验证规则，但不得恢复旧运输层或
把任意 URL、脚本、点击能力重新暴露给智能体。

## 5. 防回归规则

自动化测试必须持续保证：

- 旧代理命令不能从 CLI 调用；
- 旧扩展、bridge、daemon 和 MCP server 文件不存在；
- `bscli` 源码不再产生旧 bridge 结果字段；
- 系统配置只接受中心认证模式；
- systemd 服务只能从版本化 wheel 加载当前 `bscli`；
- 真实验证在未安装扩展、未启动 localhost daemon 的条件下完成。

新增能力必须进入中心能力注册表，并遵守
[受控写模型](./governed-write-model.md)或对应的只读适配器契约。