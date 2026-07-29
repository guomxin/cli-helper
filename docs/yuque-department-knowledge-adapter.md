# 部门信息库（语雀）适配一期

> 目标系统：`https://tc-aiot.yuque.com/`  
> 系统标识：`yuque`  
> 当前范围：中央交互式登录与只读检索，不开放业务写入

## 1. 为什么认证方式不同

部门信息库使用语雀组织空间。登录页同时包含滑块验证和短信验证码，不能把它
抽象成普通的“账号 + 密码”表单，也不应由 AgentBridge 自动绕过滑块。

一期新增通用认证模式 `interactive_browser`：

1. 智能体调用 `yuque_session_login`；
2. AgentBridge 创建与 `user_subject + system_id` 绑定的短期挑战；
3. 用户在 8780 可信卡中启动 Xvfb 隔离显示中的完整中央 Chromium；
4. 可信卡仅向用户显示中央浏览器截图，并以有序短轨迹段实时转发触控、键盘和滚动事件；
5. 滑块、手机号和短信验证码不进入 MCP 参数、模型上下文或聊天记录；
6. `/api/mine` 核验实际登录姓名与 Token 绑定姓名一致后，Credential Broker
   保存加密 Cookie，销毁临时画面控制通道；
7. OpenClaw 在模型循环外检测交互完成，并恢复原请求。

受控浏览器画面使用现有 HTTPS 可信卡来源，不安装 Chrome 扩展、VNC、
noVNC 或用户端 CLI。Xvfb 只提供不对外监听的服务器内部显示；登录后的读取
与保活仍使用无界面 worker。短期控制令牌只返回给卡片 JavaScript，不进入
`agentbridge.interaction.v1` 的模型可见投影。

## 2. 一期 MCP 工具

| 工具 | 作用 |
| --- | --- |
| `yuque_session_status` | 实时核验当前调用者的独立语雀会话 |
| `yuque_session_login` | 复用会话，失效时打开交互式登录卡 |
| `yuque_public_books_list` | 列出公共区知识库 |
| `yuque_document_catalog` | 按知识库列出或按标题筛选文档 |
| `yuque_document_search` | 在指定知识库中全文检索 |
| `yuque_document_read` | 明确选择一篇文档后读取正文 |

一期能力注册为：

- `yuque.public_books.list`
- `yuque.document.catalog`
- `yuque.document.search`
- `yuque.document.read`

全部使用 `yuque:read`。当前没有 `yuque:write:*` scope，也没有文档创建、
编辑、删除、评论或分享工具。

## 3. 数据最小化与脱敏

真实探索发现，全文检索命中摘要可能同时包含环境地址、账号、密码、SSH 信息
或 API 密钥。因此适配器采用两级读取：

- 目录和搜索结果仅返回标题、文档标识、知识库及必要元数据，不返回目录描述或服务端搜索摘要；
- 只有调用 `yuque_document_read` 明确选择文档后才读取正文；
- 正文转为纯文本后，疑似账号密码、Token、API Key、URL 内嵌凭据和私钥
  一律替换为 `[REDACTED]`；
- 返回 `redaction` 元数据说明是否脱敏、类别和数量；
- 默认最多返回 12,000 字符，调用方可在 500 至 50,000 字符内调整。

登录请求体、Cookie、短信验证码、页面截图和短期浏览器控制令牌不得写入日志、
测试夹具、操作账本或模型结果。

## 4. 语雀 Web 接口

一期使用组织域名下的浏览器会话接口，不依赖个人 Access Token：

- `GET /api/mine`
- `GET /api/modules/org_wiki/wiki/show?organizationId=...`
- `GET /api/docs?book_id=...`
- `GET /api/zsearch?...`
- `GET /api/docs/{slug}?book_id=...`

当前测试账号的个人 Access Token 页面需要额外会员能力，系统级应用也没有可用
的创建入口，所以部署不能依赖 `X-Auth-Token`。如果后续由语雀管理员提供正式
系统 AccessToken，可新增 Token 适配器并保留相同 MCP 工具契约。

## 5. 部署参数

版本化 systemd unit 已包含：

```text
--yuque-base-url https://tc-aiot.yuque.com
--yuque-organization-id 20020375
```

首次启用语雀交互登录时，需要安装服务器内部虚拟显示依赖并同步两个 systemd
unit：

```powershell
.\scripts\Deploy-AgentBridge.ps1 -InstallSystemDependencies
```

该开关只通过 Ubuntu 仓库安装 `xvfb`，不开放 VNC、WebSocket 或额外网络端口。
后续普通发布继续使用不带该开关的部署命令；部署脚本会验证 `Xvfb` 已存在并
确保 `agentbridge-xvfb.service` 先于 AgentBridge 启动。两个服务通过共享的
私有 `/tmp` 命名空间和受限 Xauthority 通信，X11 TCP 监听被明确关闭。

本地调试也可显式传入：

```powershell
python -m bscli.cli.main --home .bscli mcp central-serve `
  --yuque-base-url https://tc-aiot.yuque.com `
  --yuque-organization-id 20020375
```

为 OpenClaw 身份签发权限时使用 `yuque:read`。Token 发放逻辑会同时创建该
用户的 `yuque` 会话绑定。不要把一个用户的 Bearer Token 共享给另一位聊天
用户。

## 6. 验收顺序

1. 调用 `yuque_session_status`，确认返回未登录或已登录；
2. 调用 `yuque_session_login`，在可信卡完成滑块和短信验证；
3. 登录后自动续办或再次调用状态工具，核对姓名；
4. 调用 `yuque_public_books_list`，应看到公共区的四个知识库；
5. 调用 `yuque_document_catalog(book="共享文档")`，应返回完整目录；
6. 调用 `yuque_document_search(query="物联网平台")`，确认没有摘要字段泄露；
7. 明确选择一篇非敏感文档调用 `yuque_document_read`，核对正文、截断和脱敏；
8. 观察一次 10 分钟保活周期，确认语雀、OA、泰华会话仍按用户和系统隔离。

真实验收阶段只执行读取，不创建、修改或删除语雀内容。
