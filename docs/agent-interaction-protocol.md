# Agent Interaction Protocol

## Purpose

AgentBridge exposes one host-independent interaction contract for Codex,
OpenClaw, and other CLI or MCP clients. AgentBridge never opens a browser on
the user's device. It returns an `InteractionEnvelope`; the host renders it,
polls its state, and resumes it after the trusted surface completes.

The first version deliberately supports only three interaction types:

- `credential`: legacy-system login through the Credential Broker;
- `business_input`: business fields submitted directly to AgentBridge;
- `execution_authorization`: review and approval of one frozen write plan.

The three original security ledgers remain authoritative. The interaction
index stores only an opaque mapping to those records; it never duplicates
credentials, business-field values, or frozen plans.

## Envelope

```json
{
  "schemaVersion": "agentbridge.interaction.v1",
  "interactionId": "opaque-id",
  "type": "business_input",
  "state": "pending",
  "title": "填写出差申请",
  "message": "请在 AgentBridge 安全页面填写业务信息。",
  "operationId": "operation-id",
  "presentation": {
    "owner": "agentbridge",
    "preferred": "embedded_secure_web_app",
    "fallback": "url",
    "url": "https://cards.example.test/input/opaque-resource",
    "modelMustNotCollectValues": true
  },
  "display": {
    "systemName": "致远 OA",
    "fieldCount": 7
  },
  "expiresAt": "2026-07-14T10:15:00+00:00",
  "poll": {
    "tool": "agentbridge_interaction_get",
    "recommendedIntervalSeconds": 2
  },
  "resume": {
    "tool": "agentbridge_interaction_resume",
    "ready": false,
    "completed": false
  }
}
```

`display` contains only non-sensitive presentation metadata. Form schemas,
submitted values, credentials, frozen plans, cookies, internal URLs, and
browser details are not part of the envelope.

`presentation.url` is a short-lived trusted-card capability URL. A production
deployment must generate it from the central service's externally reachable
HTTPS base URL. `127.0.0.1` is only a local development default and cannot be
used by a phone or a remote agent host. Hosts must treat the URL as sensitive
interaction metadata: render it only to the bound user's private channel and
do not copy it into ordinary logs or a public conversation.

During the initial controlled intranet PoC, AgentBridge may instead publish a
literal private-IP HTTP URL when the operator explicitly enables
`--allow-insecure-private-http`. This preserves the same host-independent
interaction envelope and lets a desktop OpenClaw client open the card on a
different intranet machine, but the transport is plaintext and is not a
production or public/mobile-network deployment mode.

## MCP Transport Projection

The direct service and CLI contracts may return the complete envelope. The MCP
projection separates model-visible status from host-private presentation data:

- `content` and `structuredContent` keep the interaction ID, type, state,
  non-sensitive display data, poll contract, and resume contract;
- every occurrence of the trusted URL is replaced by a fixed placeholder;
- the complete envelope is carried in
  `CallToolResult._meta["io.agentbridge/interaction"]`;
- interactive tools advertise `_meta.ui.resourceUri` with
  `ui://agentbridge/trusted-interaction.html`.

An MCP host must not inject the private result metadata into model context or
ordinary logs. An approved MCP App or host adapter may consume it only for the
bound user's private interaction surface. This is the MCP equivalent of the
OpenClaw plugin's pre-model URL withholding, but it is now enforced by the
server result projection as well.

State handling is deliberately small:

- `pending` and `processing`: keep polling; resume is not ready;
- `completed` with `resume.ready=true`: invoke resume once;
- `completed` with `resume.completed=true`: the trusted record was already
  consumed, so do not repeat the underlying operation;
- `declined`, `expired`, `failed`, or `superseded`: stop and start a new
  interaction only when the user still wants the operation.

For `credential` login, a repeated request with the same bound user, system,
session, and authentication contract reuses the existing unexpired `pending`
or `processing` challenge and its interaction. An expired challenge is replaced;
a changed contract may supersede a pending challenge, while a mismatched
processing challenge fails closed until that attempt finishes.

## Host Algorithm

1. Invoke a business capability or session-ensure tool.
2. Obtain the complete envelope from host-private MCP result metadata or the
   direct CLI/service contract. Never recover the URL from model-visible text.
   Render it as an MCP App action or a private host-adapter button.
3. Poll `agentbridge_interaction_get` outside the model loop at the recommended
   interval. Do not ask the model to collect or repeat trusted values.
4. When `resume.ready` becomes true, call
   `agentbridge_interaction_resume(interaction_id)` once with a stable
   idempotency key.
5. Render any new interaction returned by resume and repeat the same algorithm.
6. Only when a successful `credential` resume explicitly returns
   `nextAction.type=retry_original_request`, enqueue one non-sensitive event in
   the originating private conversation and schedule one agent turn to retry
   the original request. Do not infer this from status text or apply it to other
   interaction types.
7. Stop when the capability succeeds, fails, becomes unknown, or the user
   declines the interaction.

The resume tool cannot enter business fields or approve a plan. It can only
consume a trusted record that the bound user has already completed. Existing
session locks, single-use field submissions, single-use authorizations, and
operation idempotency remain the write-safety boundary.

## MCP Apps

Tools that can directly return a trusted interaction reference the bundled
`ui://agentbridge/trusted-interaction.html` resource with the official
`text/html;profile=mcp-app` MIME type. A compatible host renders that resource
inside its isolated MCP Apps surface.

The bundled App:

- reads the complete envelope only from host-private result metadata;
- asks the host to open the AgentBridge HTTPS page;
- polls `agentbridge_interaction_get` outside the model loop;
- resumes a ready interaction once with a stable idempotency key;
- renders a following interaction without asking the model to reconstruct the
  workflow;
- updates model context at terminal state and, when supported, requests one
  concise follow-up turn.

A core-MCP-only host can still use read tools while the OA session is valid.
When a trusted interaction is required, it must either support MCP Apps or use a
private adapter such as the current OpenClaw plugin. AgentBridge fails closed
instead of exposing a card URL to the model.

Core MCP URL elicitation is intentionally deferred. The current card URL is a
short-lived capability URL, but the web opener is not yet independently
authenticated against the MCP subject. Standards-compliant URL elicitation
requires that browser-user binding so a forwarded URL cannot be used for the
wrong account.

## CLI

```bash
python -m bscli.cli.main --home .bscli interaction get \
  <interaction-id> --user-subject <trusted-user-subject>

python -m bscli.cli.main --home .bscli interaction resume \
  <interaction-id> --user-subject <trusted-user-subject> \
  --idempotency-key <stable-resume-key>
```

The equivalent MCP tools are `agentbridge_interaction_get` and
`agentbridge_interaction_resume`. MCP identity comes from the bound Bearer
token; the tools do not accept `userSubject`. Service discovery is available
through `agentbridge_server_profile`, `agentbridge://server/profile`, and the
`agentbridge_oa_operator` prompt.

## Codex Development Validation

Codex can exercise the same CLI or MCP contract without any Codex-specific
business path. During local development, the host may hand the trusted URL to
the user's normal Chrome window and poll the interaction in the background.
Chrome is only the card display surface in this flow: OA login and business
work still run in the central Worker, and the retired browser bridge is not
used. This is also the fallback when a Codex embedded browser cannot accept
secure keyboard input reliably.

## OpenClaw

`bscli.integrations.openclaw.render_openclaw_interaction` converts an envelope
to OpenClaw `presentation` blocks plus an `automation` polling contract and
remains the host-adapter reference implementation. The production-shaped
runtime path is the installable native plugin under
`integrations/openclaw-agentbridge`.

The plugin:

- recognizes only `agentbridge.interaction.v1` and the three declared types;
- consumes host-private MCP result metadata when the host preserves it; when
  OpenClaw 2026.7.1 drops top-level result `_meta`, it accepts only a strictly
  validated public reference from the configured AgentBridge MCP server and
  retrieves the private envelope with its authenticated background client;
- accepts card URLs only from explicitly configured exact origins;
- removes the short-lived card URL before the MCP result reaches the model;
- renders cards only in private OpenClaw sessions, never groups or channels;
- uses a Telegram Web App button for every HTTPS credential, business-input,
  and execution-authorization card; private HTTP is a local-development link
  fallback only;
- relies on a self-hosted, data-blind page bridge for ready, expand, and close;
  the bridge neither reads form controls nor loads third-party JavaScript;
- polls `agentbridge_interaction_get` outside the model loop and resumes a
  completed interaction once with a stable idempotency key;
- delivers a following interaction or terminal status directly through the
  originating private channel, with `/agentbridge pending` as a manual redraw;
- after a successful credential resume explicitly requests
  `retry_original_request`, queues one value-free continuation event and wakes
  the same private agent once;
- uses an opaque model wake-up only when ordinary direct host delivery is
  unavailable; it never derives credential continuation from model text.

`/agentbridge status` returns non-sensitive plugin diagnostics, while
`/agentbridge pending` redraws the latest unexpired card. The plugin reuses the
configured AgentBridge MCP endpoint and its environment-backed Authorization
header without logging or persisting the resolved value.

The plugin and renderer contain no OA business rules and never receive trusted
form values. On 2026-07-14, version `0.1.0` was linked into local OpenClaw
2026.7.1; runtime inspection reported the plugin loaded and explicitly enabled,
with three lifecycle hooks, the `/agentbridge` command, and the OpenClaw tool
result middleware contract. Gateway RPC and the live startup log both confirmed
the plugin was active alongside Telegram.

Polling remains the universal completion mechanism. Until an authenticated,
anti-replay callback path exists, execution authorization stays inside the
trusted AgentBridge web surface rather than becoming a model-visible chat
command.

## Validation Evidence

On 2026-07-14, a real single-user OA safety validation completed the following
path without an OA write:

- credential interaction changed from `pending` to `completed` through
  background polling and resumed to the bound active session;
- live session probing verified downstream principal `辛国茂`, returned 118
  templates, reused the central HTTP session, and reported
  `browserBridgeUsed=false`;
- `oa.business_trip.prepare {}` created a nine-field `business_input`
  interaction, and querying it returned only non-sensitive display metadata;
- attempting to resume the untouched field interaction returned
  `INTERACTION_PENDING`; no OA form was opened and no draft or workflow state
  was created.

On 2026-07-16, the private-IP HTTPS deployment completed a real Telegram
inbound validation with the formal internal CA:

- Windows current-user root trust and native TLS validation succeeded;
- a `business_input` card opened inside Telegram Desktop, submitted directly
  to AgentBridge, and resumed to an `execution_authorization` card through the
  original private-channel route;
- the user cancelled the authorization; its state became `rejected`, no commit
  operation was created, and OA was not written;
- plugin 0.1.5 redacts interactions found inside operation audit history but
  does not capture or deliver them as current cards;
- a real OpenClaw `agentbridge_operation_list(limit=3)` call completed with one
  tool call, zero failures, no new invalid-middleware warning, and no historical
  card capture.

At the 2026-07-16 checkpoint, the formal HTTPS credential-card click remained
deferred until the next natural login so the active OA session was not
invalidated only for a test. The same card type and HTTPS Web App mapping were
covered by automated tests.

On 2026-07-17, plugin 0.1.6 completed the first remote-MCP real-host
acceptance against OpenClaw 2026.7.1 and Telegram Desktop:

- the installed host was confirmed to discard top-level MCP result `_meta`;
  the plugin hydrated the private envelope only after validating the public
  reference, configured MCP server identity, interaction state, resource URI,
  expiry, and trusted origin;
- an HTTPS credential card completed OA login without placing credentials or
  its capability URL in model-visible output;
- `oa_business_trip_prepare` produced a nine-field card; because the synthetic
  `openclaw agent --deliver` path delivered text without the normal inbound
  presentation attachment, `/agentbridge pending` safely redrew the already
  captured card in the same Telegram private conversation;
- submitting the fields caused background resume to deliver the following
  execution-authorization card directly, and cancelling it produced the fixed
  `DECLINED` terminal notification;
- the authoritative authorization row was `rejected`, with no commit operation
  and no consumption; the operation ledger contained no new
  `oa.business_trip.save_draft` entry for this acceptance run;
- Node plugin tests passed 24/24, the Python 3.12 repository suite passed 197
  with 3 expected skips, and the package dry run contained only 9 declared
  files.
