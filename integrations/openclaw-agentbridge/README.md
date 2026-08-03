# AgentBridge Interactions for OpenClaw

This native OpenClaw plugin recognizes `agentbridge.interaction.v1` envelopes
returned by AgentBridge MCP tools and renders trusted card buttons in private
conversations.

Version 0.4 supports multiple messaging identities and the independent Agent
Workspace in one OpenClaw Gateway. It
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
- repeated OA, Taihua, or Yuque session-login calls for the same bound session and unchanged
  authentication contract reuse the existing unexpired credential card and
  interaction, including while the trusted page is processing;
- after a successful credential resume that explicitly returns
  `nextAction.type=retry_original_request`, a login-blocked OA workflow-list or
  Taihua log/project or Yuque knowledge read is replayed once through the same per-user MCP client
  and delivered directly to the originating private channel; login-first
  requests can infer only the registered read intents from the current user message;
- write tools are never captured or replayed by login continuation;
- background polling resumes a completed interaction once and delivers the
  next trusted card or a fixed terminal-status message through the original
  private channel without involving the model; an opaque heartbeat is retained
  only as a delivery fallback.

Version 0.3 added the first persistent Task Hub adapter. The plugin lazily creates
one server-owned task when an AgentBridge business tool is first called, reuses
the same task for tools in the same OpenClaw run, and attaches the opaque task
reference through host-private MCP metadata rather than model arguments.
Operation and interaction IDs are observed back into AgentBridge after each
call. On `gateway_start`, each configured identity independently restores its
own pending interactions, original private route, polling, and card delivery
from AgentBridge. Task coordination failures are logged but do not replace a
valid legacy-system tool result.

Version 0.4 adds two host-only web integration controls. The
`/agentbridge link <code>` command confirms a short-lived Agent Workspace
enrollment through the already trusted Telegram or WeChat identity. The
`agentbridge.workspace.bind` Gateway method redeems a one-use server grant and
pins the synthetic web session to the only MCP identity that can redeem it.
Version 0.4.3 also treats that process-level pin as a cache: if Agent Runtime
starts after the pin has disappeared, the plugin calls the host-private,
read-only `agentbridge_host_workspace_session_resolve` tool with each
configured Bearer identity and restores only the identity that centrally owns
the exact web session. Browser or model input cannot select the identity.
Version 0.4.4 adds endpoint-specific execution-authorization presentations and
an acknowledged notification outbox pump. A final authorization can be visible
in Agent Workspace, Telegram, and WeChat at the same time. Every endpoint gets
its own URL and card session, while AgentBridge atomically accepts only the
first valid decision. Non-origin channels never resume the business task; the
originating session remains the sole commit/verify coordinator. Failed channel
deliveries are leased and retried at most five times.

Version 0.4.8 adds an ordered, host-private display timeline for non-sensitive
user and assistant text. Bound Telegram and WeChat sessions publish text with
stable idempotency keys; duplicate OpenClaw outbound hooks share one publication.
Agent Workspace appends its own messages through the BFF, reads remote messages
through SSE, and reuses one stable DOM node for each task card. Credentials,
trusted-card field values, tool arguments/results, system prompts, and complete
OpenClaw transcripts are never copied into this timeline. Set
`syncTimeline=false` only to disable messaging-endpoint publication during
diagnosis; the default is `true`.

Version 0.4.9 gives Agent Workspace, Telegram, and WeChat the same agent-facing
catalog: read-only tools, every governed prepare flow, and trusted session-login
entries. Direct commit/save/approve/revoke/create tools and
`agentbridge_interaction_resume` remain internal. After a user completes the
trusted field and authorization surfaces, the originating coordinator resumes
the frozen interaction and the central service rechecks the Bearer scopes before
commit/verify.

Version 0.4.10 hardened cross-end coordination under multiple identities. Empty
notification claims use a read-only fast path, each identity polls independently,
and idle polling backs off from two to ten seconds before resetting immediately
when work appears. Timeline publication no longer blocks inbound chat hooks and
retries transient failures with one stable idempotency key. The central MCP logs
host-control calls slower than one second without recording message content or
credentials.

Version 0.4.13 loads a scope-aware tool profile for every configured identity
and keeps the same reduced catalog across that user's web and chat endpoints.
Version 0.4.14 adds bounded cross-end reference resolution. Only prompts that
explicitly mention another endpoint and use a referential phrase trigger a
host-private read of up to 12 messages from the same user's other endpoints
within six hours. The current endpoint is excluded, injected text is capped at
6,000 characters and marked as untrusted conversation data, and no execution
authority moves between sessions. Ordinary prompts do not perform this read.

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
AgentBridge interaction plugin registered (version=0.4.14, ...)
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

Certificate files use a separate host-owned delivery path. After
`oa_certificate_prepare_download` finishes the slow OA fetch, the plugin queues
the prepared files per private session and sends each as an independent
`mediaUrl` payload. One failed attachment therefore cannot suppress later files.
If the channel adapter throws, the plugin immediately falls back to a text message
containing the same short-lived prepared-file URL. Configure Telegram transport
proxy and bounded retry in OpenClaw itself; this plugin does not patch OpenClaw
source or retain files after the AgentBridge grant expires.

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
model-free terminal path. Registered OA workflow-list reads, Taihua
`my/team work logs` or `project search` reads, and Yuque public-book, catalog,
search, or selected-document reads retain only their allowlisted selectors,
filters, dates, and pagination values. They drop the old idempotency key and
replay once through the same identity-bound MCP client after login. Login-first
intent inference is likewise restricted to named read collections that do not
require unsafe parameter guessing. A successful replay is formatted for the
corresponding business object and delivered directly to the original channel;
a failed replay reports its error code.

No draft, approval, submission, meeting, revoke, or other write tool is eligible
for automatic replay. Other credential continuations retain the one-time opaque
agent wake fallback. Business-input and execution-authorization completion never
infer login continuation.

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
