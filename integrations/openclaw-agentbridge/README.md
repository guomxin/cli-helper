# AgentBridge Interactions for OpenClaw

This native OpenClaw plugin recognizes `agentbridge.interaction.v1` envelopes
returned by AgentBridge MCP tools and renders trusted card buttons in private
conversations.

Security behavior is intentionally fail closed:

- card URLs are accepted only from `allowedCardOrigins`;
- card URLs are removed before tool results are returned to the model;
- interactions nested in operation audit history are sanitized but never
  captured, polled, or delivered as the current card;
- cards are not rendered in group, channel, or room sessions;
- credentials, business fields, cookies, and authorization decisions remain in
  AgentBridge trusted pages;
- background polling resumes a completed interaction once and delivers the
  next trusted card or a fixed terminal-status message through the original
  private channel without involving the model; an opaque heartbeat is retained
  only as a delivery fallback.

## Local installation

```powershell
openclaw plugins install --link D:\Codes\CLIExp\integrations\openclaw-agentbridge
openclaw config set env.vars.NODE_EXTRA_CA_CERTS "$env:USERPROFILE\.agentbridge\pki\root-ca.crt"
openclaw config set "mcp.servers.agentbridge.url" https://10.10.50.213:8790/mcp
openclaw config set "plugins.entries.agentbridge-interactions.config.allowedCardOrigins[0]" https://10.10.50.213:8780
openclaw plugins enable agentbridge-interactions
openclaw gateway restart
openclaw plugins inspect agentbridge-interactions --runtime --json
openclaw gateway status --deep --require-rpc
```

Linked plugin source changes require a real Gateway process restart. A config
hot reload can leave Node's previously imported module in memory. Verify the
startup log contains the expected plugin version, for example:

```text
AgentBridge interaction plugin registered (version=0.1.5, ...)
```

The CA setting must use OpenClaw's `env.vars` path rather than a temporary shell
variable. After installing or rebuilding the managed task, deep status should
list `NODE_EXTRA_CA_CERTS` under `environmentValueSources`; a real MCP read then
proves that the restarted Node process trusts the internal CA.

On Windows, a managed `openclaw gateway restart` can legitimately take more
than two minutes even when the command runner times out first. Wait at least
120 seconds before diagnosing failure, and do not issue a second restart or
kill Node processes during that window. Confirm the final listener, deep RPC
status, and plugin-version log before taking recovery action.

If a Node/NVM switch leaves the Windows Scheduled Task missing or an old
Gateway process alive, repair the launcher and restart with:

```powershell
openclaw gateway install --force --json
openclaw gateway status --deep --require-rpc --json
```

The plugin reuses the configured `mcp.servers.agentbridge` endpoint and its
environment-backed Authorization header for background polling. It never stores
or prints that header.

Telegram receives a native Web App button when the trusted card uses HTTPS.
Credential, business-input, and execution-authorization cards all use this
embedded path; private HTTP remains a portable-link fallback for local
development only. AgentBridge pages use a small self-hosted lifecycle bridge
that signals ready, expand, and close without reading or forwarding form data.
The plugin records the trusted private delivery route that initiated an
interaction. After a trusted page is completed,
background resume first sends the next trusted card directly through that same
channel adapter, without exposing its URL or submitted values to the model.
When no next card exists, success, rejection, expiry, and failure are reported
as fixed host-owned status text through the same adapter. If either direct path
is unavailable, an opaque private-session heartbeat is used as a fallback. The
fallback wake reason is hook-prefixed so OpenClaw does not gate it on a non-empty
`HEARTBEAT.md`; the event still contains no submitted values, credentials, or
trusted-card URL. `/agentbridge pending` remains a manual redraw fallback. Set
`wakeAgentOnComplete=false` only when provider policy forbids that heartbeat
fallback.

In a private conversation, `/agentbridge status` reports safe diagnostics and
`/agentbridge pending` redraws the latest unexpired trusted interaction.

`oa_session_status` live-verifies an active OA session but never creates a card.
Its `checkedAt` value is the current liveness-check time; `lastVerifiedAt`
remains the authentication epoch. `SESSION_CHECK_UNAVAILABLE` means retry
without requesting credentials because the encrypted session is preserved. To
exercise the authentication-card path, ask OpenClaw to log in to OA so it calls
`oa_session_login`. OpenClaw 2026.7.1 does not include the conversation key in
tool-result middleware context, so version 0.1.1 binds the private session
during `before_tool_call` and consumes that binding by `toolCallId`. Missing or
non-private bindings still fail closed.
