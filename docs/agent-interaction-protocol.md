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

State handling is deliberately small:

- `pending` and `processing`: keep polling; resume is not ready;
- `completed` with `resume.ready=true`: invoke resume once;
- `completed` with `resume.completed=true`: the trusted record was already
  consumed, so do not repeat the underlying operation;
- `declined`, `expired`, `failed`, or `superseded`: stop and start a new
  interaction only when the user still wants the operation.

## Host Algorithm

1. Invoke a business capability or session-ensure tool.
2. When the result contains `interaction`, render its trusted URL as an
   embedded web app or a portable link button.
3. Poll `agentbridge_interaction_get` outside the model loop at the recommended
   interval. Do not ask the model to collect or repeat trusted values.
4. When `resume.ready` becomes true, call
   `agentbridge_interaction_resume(interaction_id)` once with a stable
   idempotency key.
5. Render any new interaction returned by resume and repeat the same algorithm.
6. Stop when the capability succeeds, fails, becomes unknown, or the user
   declines the interaction.

The resume tool cannot enter business fields or approve a plan. It can only
consume a trusted record that the bound user has already completed. Existing
session locks, single-use field submissions, single-use authorizations, and
operation idempotency remain the write-safety boundary.

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
token; the tools do not accept `userSubject`.

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
to OpenClaw `presentation` blocks plus an `automation` polling contract.

- Telegram private chats receive a `webApp` button.
- Other channels receive a portable URL button and can apply their native
  presentation adapter.
- Completed interactions contain no user button; the adapter calls the resume
  tool automatically.
- A channel without interactive controls degrades to the trusted URL.

The renderer contains no OA business rules and never receives trusted values.
It has been contract-checked against the locally installed OpenClaw 2026.6.1
presentation implementation, which accepts both `button.url` and
`button.webApp.url`.

This first version uses polling as the universal completion mechanism. A
future installable OpenClaw plugin may add authenticated completion webhooks
and native approve/reject callbacks. Until that callback identity and
anti-replay path exists, execution authorization remains inside the trusted
AgentBridge web surface rather than becoming a model-visible chat command.

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
