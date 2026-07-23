# AgentBridge Interactions for OpenClaw

This native OpenClaw plugin recognizes `agentbridge.interaction.v1` envelopes
returned by AgentBridge MCP tools and renders trusted card buttons in private
conversations.

Version 0.2 also supports multiple messaging identities in one OpenClaw Gateway. It
registers the complete AgentBridge MCP catalog as native OpenClaw tools and
selects an environment-backed Bearer token from trusted runtime sender context,
never from model tool arguments. See
[`docs/openclaw-multi-user-identity-routing.md`](../../docs/openclaw-multi-user-identity-routing.md)
for provisioning, migration, and the remaining real second-user acceptance.

AgentBridge also publishes a standard MCP Apps resource. This plugin is the
compatibility adapter for OpenClaw versions that do not yet provide equivalent
MCP Apps rendering, private-session binding, polling, and resume behavior; it
is not a dependency of the central OA business implementation. The plugin can
read the full envelope from host-private
`CallToolResult._meta["io.agentbridge/interaction"]`, while the model-visible
result contains only the redacted interaction status. OpenClaw 2026.7.1 drops
top-level MCP result `_meta` while materializing remote tools, so the adapter
also recognizes a strictly validated public interaction reference and uses its
authenticated background MCP client to retrieve the private envelope. This
fallback is accepted only from the configured AgentBridge MCP server and never
copies the trusted URL into model-visible content.

Security behavior is intentionally fail closed:

- card URLs are accepted only from `allowedCardOrigins`;
- card URLs are removed before tool results are returned to the model;
- interactions nested in operation audit history are sanitized but never
  captured, polled, or delivered as the current card;
- identity-routed tools are available only to configured trusted sender IDs;
- one OpenClaw session cannot switch to another sender identity;
- polling and resume use the same per-user client that captured the interaction;
- legacy global `agentbridge__...` tools are blocked in identity-routing mode;
- cards are not rendered in group, channel, or room sessions;
- credentials, business fields, cookies, and authorization decisions remain in
  AgentBridge trusted pages;
- repeated `oa_session_login` calls for the same bound session and unchanged
  authentication contract reuse the existing unexpired credential card and
  interaction, including while the trusted page is processing;
- only a successful credential resume that explicitly returns
  `nextAction.type=retry_original_request` may queue a non-sensitive
  continuation and wake the same private agent once;
- background polling resumes a completed interaction once and delivers the
  next trusted card or a fixed terminal-status message through the original
  private channel without involving the model; an opaque heartbeat is retained
  only as a delivery fallback.

## Local installation

The commands below retain the legacy single-user MCP configuration for existing
installations. Do not use one global `mcp.servers.agentbridge` Bearer token for
multiple messaging users. Multi-user deployments configure plugin `mcpUrl` plus
`identityBindings`, then remove the global MCP server entry.

```powershell
openclaw plugins install --link D:\Codes\CLIExp\integrations\openclaw-agentbridge
openclaw config set env.vars.NODE_EXTRA_CA_CERTS "$env:USERPROFILE\.agentbridge\pki\root-ca.crt"
openclaw config set "mcp.servers.agentbridge.url" https://10.10.50.213:8790/mcp
openclaw config set "mcp.servers.agentbridge.timeout" 150
openclaw config set "plugins.entries.agentbridge-interactions.config.allowedCardOrigins[0]" https://10.10.50.213:8780
openclaw config set tools.alsoAllow '[\"agentbridge-interactions\"]' --strict-json
openclaw plugins enable agentbridge-interactions
openclaw gateway restart
openclaw plugins inspect agentbridge-interactions --runtime --json
openclaw gateway status --deep --require-rpc
```

Restricted profiles such as `tools.profile: "coding"` do not expose native
third-party plugin tools by default. Keep the restricted profile and add only
`agentbridge-interactions` through `tools.alsoAllow`; do not use
`group:plugins`. If `tools.alsoAllow` already contains other entries, merge
this plugin id into the existing array instead of replacing it. A plugin can
report `loaded` while all of its tools are still filtered, so acceptance must
also confirm that `agentbridge_identity_status` is visible in a real bound
private session.

Linked plugin source changes require a real Gateway process restart. A config
hot reload can leave Node's previously imported module in memory. Verify the
startup log contains the expected plugin version, for example:

```text
AgentBridge interaction plugin registered (version=0.2.3, ...)
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

In legacy single-user mode the plugin reuses the configured
`mcp.servers.agentbridge` endpoint and its environment-backed Authorization
header. In multi-user mode it uses plugin `mcpUrl` only as an address and chooses
the Authorization header from the bound sender's `tokenEnv`; it never stores or
prints token values. The interaction record pins that client for background
polling and resume.

Governed OA submissions can include browser setup, the
multi-stage CAP4 send chain, and server-side readback, so the endpoint timeout
must remain at least 150 seconds. A host timeout is not proof that OA rejected or
accepted a write; reconcile the AgentBridge operation ledger and OA collections
before any retry.

Telegram receives a native Web App button when the trusted card uses HTTPS.
Credential, business-input, and execution-authorization cards all use this
embedded path. The same private message also includes a host-rendered
"浏览器打开" URL button for Android Telegram clients that reject a user-installed
internal CA in their embedded WebView. Both buttons carry the same short-lived
trusted URL only in host presentation metadata; the URL remains absent from
model-visible results. Private HTTP remains a portable-link fallback for local
development only.

The official Tencent WeChat adapter exposes text and media delivery but no
presentation renderer. For WeChat and any other adapter without
`renderPresentation`, the trusted host appends the action label and short-lived
HTTP(S) URL directly to the outbound text. The URL still never enters the model
result, and Telegram continues to use native buttons. AgentBridge pages use a small self-hosted
lifecycle bridge
that signals ready, expand, and close without reading or forwarding form data.
The plugin records the trusted private delivery route that initiated an
interaction. After a trusted page is completed,
background resume first sends the next trusted card directly through that same
channel adapter, without exposing its URL or submitted values to the model.
When no next card exists, success, rejection, expiry, and failure are reported
as fixed host-owned status text through the same adapter.

A successful credential resume with
`nextAction.type=retry_original_request` is the deliberate exception to the
model-free terminal path: the plugin sends the fixed status, enqueues a
non-sensitive instruction to retry the original user request, and wakes that
same private agent exactly once. Business-input and execution-authorization
completion never infer this continuation.

If either direct path
is unavailable, an opaque private-session heartbeat is used as a fallback. The
fallback wake reason is hook-prefixed so OpenClaw does not gate it on a non-empty
`HEARTBEAT.md`; the event still contains no submitted values, credentials, or
trusted-card URL. `/agentbridge pending` remains a manual redraw fallback. Set
`wakeAgentOnComplete=false` only when provider policy forbids background model
wake-ups. Direct card and status delivery still work, but credential completion
then requires the user or host to retry the original request.

In a private conversation, `/agentbridge status` reports safe diagnostics and
`/agentbridge pending` redraws the latest unexpired trusted interaction.

For acceptance testing, use a real inbound message from the target private
conversation. `openclaw agent --deliver` can execute the MCP tool and deliver
the model's text while bypassing the normal inbound reply path that attaches a
host presentation, so a text-only result from that command is not evidence that
card rendering failed. If an interaction is already captured, use
`/agentbridge pending` in the same private conversation to redraw it without
creating a second operation.

`oa_session_status` live-verifies an active OA session but never creates a card.
Its `checkedAt` value is the current liveness-check time; `lastVerifiedAt`
remains the authentication epoch. `SESSION_CHECK_UNAVAILABLE` means retry
without requesting credentials because the encrypted session is preserved. To
exercise the authentication-card path, ask OpenClaw to log in to OA so it calls
`oa_session_login`. OpenClaw 2026.7.1 does not include the conversation key in
tool-result middleware context, so version 0.1.1 binds the private session
during `before_tool_call` and consumes that binding by `toolCallId`. Missing or
non-private bindings still fail closed.

After credential login completes, the plugin checks for pending trusted cards
before the status reply, after that reply, and again after the original-request
continuation heartbeat. A field or confirmation card created by that heartbeat
is delivered directly even when the continuation has a new run id; a card
already delivered through the normal reply path is not sent twice.
