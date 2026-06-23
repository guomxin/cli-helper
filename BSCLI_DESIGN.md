# BSCLI Design: Non-Intrusive CLI and Agent Adapter for B/S Systems

## 1. Goal

Build a non-intrusive adapter platform for existing B/S systems.

The platform should expose existing web system capabilities as CLI commands and agent-callable tools without requiring changes to the target system backend, frontend, deployment, or authentication mechanism.

The core idea is:

> The browser provides real user login context, exploration, and fallback execution. Stable capabilities are promoted into verified CLI commands and agent tools, preferably executed through backend APIs when available.

## 2. Key Principles

- Do not require target systems to expose new APIs.
- Do not ask agents to own user passwords.
- Reuse the user's real Chrome login state.
- Treat the browser as the entry point, exploration surface, and fallback executor.
- Prefer direct backend API execution when page exploration reveals stable APIs.
- Expose business commands, not low-level browser actions.
- Require command registration, permission declaration, verification, and audit logs.
- Use human confirmation for risky write actions and sensitive operations.

## 3. High-Level Architecture

```text
Agent / User
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
User's Real Chrome Profile / Logged-In Tabs / Backend APIs
  |
  v
Existing B/S System
```

## 4. Main Components

### 4.1 Python CLI

The CLI is the main human-facing and script-facing entry point.

Example commands:

```bash
bscli system add oa --url https://oa.example.com
bscli system login oa
bscli system status oa
bscli explore oa
bscli command record oa search_employee
bscli command run oa search_employee --json '{"keyword":"Zhang San"}'
bscli command trace <run-id>
bscli export-tools oa --format mcp
```

Recommended library:

- `Typer` or `Click`

### 4.2 Python Local Daemon

The daemon is the local control plane.

Responsibilities:

- Receive requests from CLI and agents.
- Manage system profiles.
- Manage command registry.
- Dispatch adapter execution.
- Communicate with the Chrome extension.
- Enforce permissions and domain allowlists.
- Store trace and audit logs.
- Expose MCP tools for agents.

Recommended library:

- `FastAPI` or `Starlette`

### 4.3 Chrome Extension

The extension is the browser-side bridge. It must be implemented in JavaScript or TypeScript because it runs inside Chrome.

Responsibilities:

- Connect to the Python daemon.
- Discover and bind target tabs.
- Reuse the user's real Chrome login state.
- Read page URL, title, DOM, selected text, and page state.
- Inject content scripts.
- Execute page-context `fetch`.
- Capture network requests and responses when possible.
- Observe downloads.
- Perform UI workflow actions when needed.
- Show user confirmation prompts for risky operations.

### 4.4 Command Registry

The command registry turns website capabilities into stable business commands.

Every command must declare:

- Command name
- Description
- Target system
- Input schema
- Output schema
- Access type: `read` or `write`
- Risk level
- Execution strategy
- Allowed origins
- Allowed endpoints
- Verification rule
- Whether user confirmation is required

Example:

```yaml
name: search_employee
description: Search employee information
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

The adapter runtime executes registered commands.

Python adapters can be written as normal Python functions:

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

The runtime should provide:

- Typed command context
- Browser bridge client
- HTTP client with browser-derived authentication
- Trace writer
- Verification helpers
- User confirmation helpers

### 4.6 Trace Store

Every command run should be auditable.

Trace data should include:

- Run ID
- System ID
- Command name
- Arguments
- Access type
- Strategy used
- URLs and endpoints touched
- Start and end time
- Result summary
- Error details
- Verification result
- Network summary
- DOM snapshot path
- Screenshot path on failure

Recommended storage:

- SQLite
- `SQLModel` or another lightweight ORM

### 4.7 MCP Tool Export

Registered commands should be exportable as agent tools.

Example:

```bash
bscli export-tools oa --format mcp
```

Agents should see business tools such as:

```json
{
  "name": "oa_search_employee",
  "description": "Search employee information in OA",
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

Agents should not receive unrestricted browser control.

## 5. Execution Strategies

Commands should support multiple execution strategies.

Recommended priority:

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

Use official or documented APIs when available.

This is the most stable and preferred strategy.

### 5.2 DAEMON_API

The Python daemon directly calls backend APIs discovered through browser exploration.

Authentication material such as cookies or tokens comes from the user's Chrome session and must be restricted by system and endpoint allowlists.

### 5.3 PAGE_FETCH

The Chrome extension executes `fetch` inside the page context.

This is useful when requests depend on:

- Same-origin context
- CSRF tokens
- Runtime-generated headers
- Frontend application state

### 5.4 DOM_READ

Read structured data from DOM or frontend-rendered state.

Useful for:

- Tables
- Detail pages
- Dashboard values
- Preloaded JSON state

### 5.5 UI_WORKFLOW

Execute real UI steps through the browser.

Useful for:

- Complex forms
- File upload
- File download
- Workflow submission
- Systems where API calls are unstable or hard to reproduce

### 5.6 HUMAN_GATE

Pause and ask the user to take over or confirm.

Required for:

- CAPTCHA
- MFA
- SSO re-authentication
- Deletion
- Approval
- Payment
- High-risk write operations

## 6. Browser Role

The browser has three roles.

### 6.1 Login Context

The user logs in using normal Chrome.

The system does not store user passwords and does not require the agent to know credentials.

### 6.2 Exploration Surface

The browser is used to discover:

- DOM structure
- Forms
- Buttons
- Tables
- XHR and fetch requests
- GraphQL operations
- Headers
- CSRF tokens
- Request payloads
- Response schemas

### 6.3 Fallback Executor

When direct API execution is not reliable, the browser can execute the real UI workflow.

## 7. API Discovery Flow

Many B/S systems expose useful backend APIs behind their pages.

The platform should support this flow:

```text
Open target page
  |
  v
Capture network requests and DOM state
  |
  v
Identify candidate backend APIs
  |
  v
Extract parameters, headers, tokens, and response structure
  |
  v
Replay and verify candidate API
  |
  v
Promote candidate API into a registered command
```

Example commands:

```bash
bscli explore oa --record-network
bscli api discover oa --from-trace <trace-id>
bscli api replay oa --candidate <candidate-id>
bscli command promote-api oa search_employee --candidate <candidate-id>
```

## 8. Security Model

Security boundaries should exist from the first version.

Rules:

- A system must be explicitly registered.
- Allowed origins must be explicitly declared.
- API endpoints must be allowlisted before daemon-side execution.
- Write commands should require confirmation by default.
- High-risk operations must use `HUMAN_GATE`.
- Agents must not receive cookies, passwords, or unrestricted browser control.
- The daemon should reject unregistered commands and unknown endpoints.
- Every run should produce an audit trace.

Example system profile:

```yaml
id: oa
name: Company OA
base_url: https://oa.example.com
allowed_origins:
  - https://oa.example.com
auth:
  mode: chrome_extension
commands:
  write_requires_confirm: true
```

## 9. MVP Scope

The first version should prove the complete loop with a small scope.

Required commands:

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

Initial supported scenarios:

1. Query a page table.
2. Submit a form.
3. Export or download a report file.

The MVP should include:

- Python CLI
- Python daemon
- Chrome extension bridge
- System profile storage
- Command registry
- One DOM read command
- One UI workflow command
- One API-promoted command
- Trace storage
- Basic MCP export

## 10. Recommended Technology Stack

Python side:

- CLI: `Typer`
- Daemon: `FastAPI`
- WebSocket: FastAPI WebSocket support
- Schema: `Pydantic v2`
- HTTP client: `httpx`
- Config: `PyYAML` or `ruamel.yaml`
- Trace store: SQLite + `SQLModel`
- Plugin loading: `importlib.metadata` entry points
- MCP: Python MCP SDK
- Packaging: `uv` or `hatch`

Chrome side:

- Chrome Extension Manifest V3
- TypeScript or plain JavaScript
- `chrome.runtime`
- `chrome.tabs`
- `chrome.scripting`
- `chrome.webRequest` or Chrome debugging APIs where appropriate
- WebSocket or Native Messaging connection to daemon

## 11. Suggested Project Structure

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

## 12. Relationship to OpenCLI

This design borrows several ideas from OpenCLI:

- Use a command registry rather than raw browser automation.
- Reuse user browser login state.
- Support multiple execution strategies.
- Treat adapter development as a first-class workflow.
- Collect trace information for debugging and maintenance.

Differences:

- This platform is focused on enterprise B/S systems rather than public websites.
- Python is the main implementation language.
- Chrome extension bridge is part of the first version.
- API discovery and promotion are first-class features.
- Human confirmation, endpoint allowlists, and audit logs are core requirements.
- MCP export is part of the agent integration path.

## 13. One-Sentence Summary

BSCLI is a Python-first, Chrome-login-state-driven adapter platform that turns existing B/S system capabilities into safe, verified, auditable CLI commands and agent tools, using browser exploration to discover APIs and browser automation as a fallback.
