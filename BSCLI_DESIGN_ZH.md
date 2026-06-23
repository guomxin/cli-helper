# BSCLI 设计方案：非侵入式 B/S 系统 CLI 与智能体适配平台

## 1. 目标

构建一个面向既有 B/S 系统的非侵入式适配平台。

平台需要在不修改目标系统后端、前端、部署方式和认证机制的前提下，把 Web 系统里的业务能力封装成 CLI 命令，并进一步暴露为智能体可调用的工具。

核心思路是：

> 浏览器负责提供真实用户登录态、页面探索和兜底执行；稳定能力则沉淀为经过校验的 CLI 命令和智能体工具，并在条件允许时优先通过后台 API 执行。

## 2. 核心原则

- 不要求目标系统新增 API。
- 不让智能体掌握用户密码。
- 复用用户真实 Chrome 中的登录态。
- 浏览器作为入口、探索面和兜底执行器。
- 页面探索出稳定后台 API 后，优先使用 API 执行。
- 对外暴露业务命令，而不是底层点击、输入等浏览器动作。
- 所有命令必须经过注册、权限声明、结果校验和审计记录。
- 高风险写操作和敏感操作必须有人确认。

## 3. 总体架构

```text
智能体 / 用户
  |
  v
Python CLI / MCP Server
  |
  v
Python Local Daemon
  |
  | WebSocket / Native Messaging
  v
Chrome Extension
  |
  v
用户真实 Chrome Profile / 已登录 Tab / 后台 API
  |
  v
既有 B/S 系统
```

## 4. 核心组件

### 4.1 Python CLI

CLI 是面向用户、脚本和自动化流程的主要入口。

示例命令：

```bash
bscli system add oa --url https://oa.example.com
bscli system login oa
bscli system status oa
bscli explore oa
bscli command record oa search_employee
bscli command run oa search_employee --json '{"keyword":"张三"}'
bscli command trace <run-id>
bscli export-tools oa --format mcp
```

推荐库：

- `Typer` 或 `Click`

### 4.2 Python Local Daemon

Daemon 是本地控制平面。

职责：

- 接收 CLI 和智能体请求。
- 管理系统配置。
- 管理命令注册表。
- 调度 adapter 执行。
- 与 Chrome 扩展通信。
- 根据系统配置中的 `allowed_origins` 选择匹配的已登录浏览器标签页，避免多标签或多系统场景下把任务投递给错误页面。
- 执行权限检查和域名白名单检查。
- 存储 trace 和审计日志。
- 对智能体暴露 MCP 工具。

推荐库：

- `FastAPI` 或 `Starlette`

### 4.3 Chrome Extension

Chrome 扩展是浏览器侧桥接层。由于它运行在 Chrome 内部，因此需要使用 JavaScript 或 TypeScript 实现。

职责：

- 连接 Python daemon。
- 发现和绑定目标系统 tab。
- 复用用户真实 Chrome 登录态。
- 读取页面 URL、标题、DOM、选中文本和页面状态。
- 注入 content script。
- 在页面上下文中执行 `fetch`。
- 在可行时捕获网络请求和响应。
- 观察文件下载。
- 在需要时执行 UI 工作流。
- 对高风险操作弹出用户确认。

### 4.4 Command Registry

命令注册表负责把网站能力转换为稳定的业务命令。

每个命令必须声明：

- 命令名
- 描述
- 目标系统
- 输入 schema
- 输出 schema
- 访问类型：`read` 或 `write`
- 风险等级
- 执行策略
- 允许访问的 origin
- 允许访问的 endpoint
- 结果校验规则
- 是否需要用户确认

示例：

```yaml
name: search_employee
description: 查询员工信息
system: oa
access: read
risk: low
strategy: daemon_api
args:
  keyword:
    type: string
    required: true
output:
  type: table
  columns:
    - name
    - department
    - phone
api:
  method: POST
  path: /api/hr/employees/search
  auth:
    source: chrome_cookie
verify:
  type: json_path
  path: $.data
```

### 4.5 Adapter Runtime

Adapter Runtime 负责执行已经注册的命令。

Python adapter 可以写成普通 Python 函数：

```python
from bscli import command, Context


@command(
    system="oa",
    name="search_employee",
    access="read",
    strategy="daemon_api",
)
async def search_employee(ctx: Context, keyword: str):
    resp = await ctx.http.post(
        "/api/hr/employees/search",
        json={"keyword": keyword},
    )
    return resp.json()["data"]
```

Runtime 需要提供：

- 类型化命令上下文。
- 浏览器桥接客户端。
- 可携带浏览器认证信息的 HTTP client。
- Trace 写入能力。
- 结果校验辅助函数。
- 用户确认辅助函数。

### 4.6 Trace Store

每次命令执行都应该可审计、可复盘。

Trace 数据应包括：

- Run ID
- 系统 ID
- 命令名
- 参数
- 访问类型
- 使用的执行策略
- 访问过的 URL 和 endpoint
- 开始和结束时间
- 结果摘要
- 错误详情
- 校验结果
- 网络摘要
- DOM snapshot 路径
- 失败时的截图路径

推荐存储：

- SQLite
- `SQLModel` 或其他轻量 ORM

### 4.7 MCP Tool Export

注册后的命令应能导出为智能体工具。

示例：

```bash
bscli export-tools oa --format mcp
```

智能体看到的应该是业务工具：

```json
{
  "name": "oa_search_employee",
  "description": "在 OA 系统中查询员工信息",
  "input_schema": {
    "type": "object",
    "properties": {
      "keyword": {
        "type": "string"
      }
    },
    "required": ["keyword"]
  }
}
```

智能体不应该获得不受限制的浏览器控制权。

## 5. 执行策略

命令应支持多种执行策略。

推荐优先级：

```text
PUBLIC_API
  ↓
DAEMON_API
  ↓
PAGE_FETCH
  ↓
DOM_READ
  ↓
UI_WORKFLOW
  ↓
HUMAN_GATE
```

### 5.1 PUBLIC_API

当系统存在官方 API 或文档化 API 时，优先使用。

这是最稳定、最推荐的策略。

### 5.2 DAEMON_API

由 Python daemon 直接调用通过页面探索发现的后台 API。

Cookie、token 等认证材料来自用户 Chrome 会话，但必须受到系统、域名和 endpoint 白名单约束。

### 5.3 PAGE_FETCH

由 Chrome 扩展在页面上下文中执行 `fetch`。

适用于请求依赖以下条件的场景：

- 同源上下文
- CSRF token
- 运行时生成的 header
- 前端应用状态

### 5.4 DOM_READ

从 DOM 或前端渲染状态中读取结构化数据。

适用于：

- 表格
- 详情页
- 仪表盘指标
- 页面预加载 JSON 状态

### 5.5 UI_WORKFLOW

通过浏览器执行真实 UI 步骤。

适用于：

- 复杂表单
- 文件上传
- 文件下载
- 工作流提交
- API 不稳定或难以复现的系统

### 5.6 HUMAN_GATE

暂停执行，让用户接管或确认。

以下场景必须进入 HUMAN_GATE：

- 验证码
- MFA
- SSO 重新认证
- 删除操作
- 审批操作
- 支付操作
- 高风险写操作

## 6. 浏览器的定位

浏览器承担三类角色。

### 6.1 登录上下文

用户在正常 Chrome 中完成登录。

系统不保存用户密码，也不要求智能体知道凭据。

### 6.2 探索界面

通过浏览器发现：

- DOM 结构
- 表单
- 按钮
- 表格
- XHR 和 fetch 请求
- GraphQL 操作
- Header
- CSRF token
- 请求 payload
- 响应 schema

### 6.3 兜底执行器

当直接 API 执行不可靠时，可以通过浏览器执行真实 UI 工作流。

## 7. API 发现流程

很多 B/S 系统的页面背后都有可复用的后台 API。

平台应支持以下流程：

```text
打开目标页面
  |
  v
捕获网络请求和 DOM 状态
  |
  v
识别候选后台 API
  |
  v
提取参数、header、token 和响应结构
  |
  v
回放并校验候选 API
  |
  v
将候选 API 晋升为注册命令
```

示例命令：

```bash
bscli explore oa --record-network
bscli api discover oa --from-trace <trace-id>
bscli api replay oa --candidate <candidate-id>
bscli command promote-api oa search_employee --candidate <candidate-id>
```

## 8. 安全模型

安全边界必须从第一版就存在。

规则：

- 系统必须显式注册。
- 允许访问的 origin 必须显式声明。
- Daemon 侧 API 执行前，endpoint 必须加入白名单。
- 写操作默认需要确认。
- 高风险操作必须使用 `HUMAN_GATE`。
- 智能体不能获得 cookie、密码或不受限制的浏览器控制权。
- Daemon 应拒绝未注册命令和未知 endpoint。
- 每次执行都必须产生审计 trace。

系统配置示例：

```yaml
id: oa
name: 公司 OA
base_url: https://oa.example.com
allowed_origins:
  - https://oa.example.com
auth:
  mode: chrome_extension
commands:
  write_requires_confirm: true
```

运行期任务路由以 `allowed_origins` 为基础：Chrome 扩展上报每个标签页的 URL，daemon 只把某个系统的任务投递给 origin 匹配该系统配置的标签页。这样同一浏览器里同时打开 OA、CRM、财务系统时，CLI 和智能体命令仍能复用各自真实登录态，并避免互相串页。

## 9. MVP 范围

第一版应先证明完整闭环，范围要小。

需要支持的命令：

```bash
bscli system add
bscli system login
bscli system status
bscli explore
bscli command record
bscli command run
bscli command trace
bscli export-tools --format mcp
```

首批支持场景：

1. 查询页面表格。
2. 填表提交。
3. 导出或下载报表文件。

MVP 应包含：

- Python CLI
- Python daemon
- Chrome 扩展桥
- 系统配置存储
- 命令注册表
- 一个 DOM 读取命令
- 一个 UI 工作流命令
- 一个由 API 晋升而来的命令
- Trace 存储
- 基础 MCP 导出

## 10. 推荐技术栈

Python 侧：

- CLI：`Typer`
- Daemon：`FastAPI`
- WebSocket：FastAPI WebSocket
- Schema：`Pydantic v2`
- HTTP client：`httpx`
- 配置：`PyYAML` 或 `ruamel.yaml`
- Trace 存储：SQLite + `SQLModel`
- 插件加载：`importlib.metadata` entry points
- MCP：Python MCP SDK
- 打包：`uv` 或 `hatch`

Chrome 侧：

- Chrome Extension Manifest V3
- TypeScript 或 JavaScript
- `chrome.runtime`
- `chrome.tabs`
- `chrome.scripting`
- 视情况使用 `chrome.webRequest` 或 Chrome debugging APIs
- WebSocket 或 Native Messaging 连接 daemon

## 11. 建议项目结构

```text
bscli/
  pyproject.toml
  src/
    bscli/
      cli/
        main.py
      daemon/
        app.py
        extension_ws.py
      core/
        registry.py
        config.py
        schema.py
        runtime.py
        trace.py
      browser/
        bridge.py
        protocol.py
      adapters/
        loader.py
        context.py
      mcp/
        server.py
  extension/
    manifest.json
    background.js
    content.js
    popup.html
```

## 12. 与 OpenCLI 的关系

本方案借鉴 OpenCLI 的几个关键思想：

- 使用命令注册表，而不是直接暴露浏览器自动化。
- 复用用户浏览器登录态。
- 支持多种执行策略。
- 把 adapter 开发流程作为一等能力。
- 收集 trace，便于调试和维护。

主要区别：

- 本平台更偏企业 B/S 系统，而不是公开互联网网站。
- Python 是主体实现语言。
- Chrome 扩展桥从第一版开始就是核心组件。
- API 发现和 API 晋升是一等能力。
- 用户确认、endpoint 白名单和审计日志是核心安全要求。
- MCP 导出是智能体集成的主要路径。

## 13. 一句话总结

BSCLI 是一个 Python 优先、由 Chrome 真实登录态驱动的 B/S 系统适配平台；它通过浏览器探索发现系统能力，把稳定能力沉淀为安全、可校验、可审计的 CLI 命令和智能体工具，并在必要时用浏览器自动化兜底。
