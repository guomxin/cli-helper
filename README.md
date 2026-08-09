# AgentBridge CLI Helper

Python-first, non-intrusive adapters that expose legacy B/S system capabilities
to agents without modifying the target system.

The active runtime is central AgentBridge:

- Versioned business capabilities shared by CLI and MCP
- Per-user managed Playwright profiles and HTTP sessions
- Trusted authentication, business-field, and write-authorization cards
- A Credential Broker that keeps credentials outside model-visible channels
- A durable operation ledger with idempotency and explicit outcome states
- A persistent Task Hub that links host tasks, operations, trusted interactions,
  client endpoints, subscriptions, and notification outbox records
- Seeyon OA, API-first Taihua work logs, and trusted-browser Yuque knowledge access under one multi-system runtime

The original Chrome extension, browser bridge, localhost daemon, daemon-backed
MCP server, and their public CLI commands were retired on 2026-07-13. They are
not fallback paths. See
[the retirement ledger](docs/legacy-bridge-retirement.md) for the remaining
capability-migration inventory.

## Requirements

- Python 3.12 or newer
- A Chromium runtime supported by Playwright
- Network access from the central worker to the target legacy system

The selected intranet deployment target is Linux. Set
`AGENTBRIDGE_SESSION_KEY_FILE` to an absolute path containing exactly 32 random
bytes; AgentBridge uses AES-256-GCM with session-bound authenticated data and
rejects symlinks, broad permissions, wrong keys, and modified ciphertext.
Windows continues to use user-scoped DPAPI. Plaintext Cookie persistence is not
an accepted fallback.

~~~bash
python -m pip install -e .
python -m playwright install chromium
python -m bscli.cli.main --home .bscli system init-seeyon-oa
~~~

## Capability CLI

List and describe the currently published capabilities:

~~~bash
python -m bscli.cli.main --home .bscli capability list
python -m bscli.cli.main --home .bscli capability describe oa.template.list
python -m bscli.cli.main --home .bscli capability describe oa.business_trip.prepare
~~~

Published OA capabilities:

- oa.template.list
- oa.workflow.pending.list
- oa.workflow.sent.list
- oa.workflow.done.list
- oa.workflow.tracked.list
- oa.workflow.detail.get
- oa.workflow.opinions.list
- oa.document.certificate.search
- oa.business_trip.prepare
- oa.business_trip.save_draft
- oa.business_trip.submit.prepare
- oa.business_trip.submit
- oa.leave.prepare
- oa.leave.save_draft
- oa.leave.submit.prepare
- oa.leave.submit
- oa.missed_punch.prepare
- oa.missed_punch.save_draft
- oa.missed_punch.approval.prepare
- oa.missed_punch.approve
- oa.efficiency_data.approval.prepare
- oa.efficiency_data.approve
- oa.travel_expense.approval.prepare
- oa.travel_expense.approve
- oa.labor_contract_renewal.approval.prepare
- oa.labor_contract_renewal.approve
- oa.attendance_confirmation.prepare
- oa.attendance_confirmation.confirm
- oa.weekly_report.acknowledgement.prepare
- oa.weekly_report.acknowledge
- oa.standard_collaboration.approval.prepare
- oa.standard_collaboration.approve
- oa.meeting.create.prepare
- oa.meeting.create
- oa.workflow.revoke.prepare
- oa.workflow.revoke

Published Taihua capabilities:

- `taihua.work_log.my.list`
- `taihua.work_log.team.list`
- `taihua.project.search`
- `taihua.work_log.create.prepare`
- `taihua.work_log.create`

Taihua uses central HTTP token sessions with refresh, exact-origin enforcement,
and no browser during normal reads or writes. See the
[Taihua adapter guide](docs/taihua-log-system-adapter.md).

Published Yuque capabilities:

- `yuque.public_books.list`
- `yuque.document.catalog`
- `yuque.document.search`
- `yuque.document.read`

Yuque uses a per-user central browser session because its current account cannot
create a Personal Access Token and login requires a slider plus SMS verification.
The trusted interactive login card embeds a challenge-scoped noVNC surface backed
by isolated Xvfb, Chromium profile, loopback RFB, and loopback CDP resources. Native
browser input, cookies, endpoints, temporary VNC credentials, and control tokens
stay outside the model. Catalog and search work across all visible knowledge bases by default, while optional
book, type, date, sort, and pagination arguments narrow the result. Explicit reads
normalize Doc, Sheet, and Table content into structured text with row paging, outline,
table, link, image OCR, and attachment metadata. Search snippets remain omitted, and
selected content is redacted for likely credentials and tokens. Attachment download is
not claimed until a real independent file card is available for acceptance. See the
[Yuque department knowledge adapter guide](docs/yuque-department-knowledge-adapter.md).

Workflow capabilities expose business data and opaque affair IDs. They do not
expose internal URLs, raw HTML, cookies, private action endpoints, or hidden
form fields.

### Certificate delivery

`oa_certificate_search` returns opaque, user-bound download IDs and browser download
cards. When a chat user asks to receive a patent or software-copyright scan, the
host calls `oa_certificate_prepare_download` once for each selected ID. AgentBridge
fetches the file from OA under the same central session, caches it only for the
short grant lifetime, and exposes a fast media URL. A prepared file receives a fresh
30-minute delivery window and is linked to the current Task Hub task as a user-bound
artifact. Agent Workspace shows that artifact in the task card and task detail, while
Telegram and WeChat companion endpoints receive one attachment per message. If the
channel upload fails, the adapter sends the same short-lived URL as an explicit
fallback instead of silently dropping the file. Artifact metadata is user-isolated;
the administration surface can see state, size, task and expiry but never the download
URL. Ad-hoc client download scripts are not part of the supported path.

## Trusted Login

Start the trusted-card service. The same listener serves authentication,
business-input, write-authorization, and short-lived document-download pages:

~~~bash
python -m bscli.cli.main --home .bscli auth serve \
  --host 127.0.0.1 \
  --port 8780 \
  --public-base-url http://127.0.0.1:8780
~~~

Ensure that the user's OA session is usable:

~~~bash
python -m bscli.cli.main --home .bscli session login \
  --system oa \
  --user-subject <trusted-user-subject> \
  --expected-principal <oa-display-name> \
  --card-base-url http://127.0.0.1:8780
~~~

`session login` is idempotent. For an active session it performs a live OA
probe, refreshes the encrypted Cookie state, and returns `succeeded` with
`reused=true`; it does not create a card. Only when OA confirms that the
session is no longer authenticated does it return `LOGIN_REQUIRED` and a
short-lived `nextAction.cardUrl`.

`session status` and the MCP `oa_session_status` tool also live-probe an active
session. Their response distinguishes the authentication epoch
(`lastVerifiedAt`) from the current liveness check (`checkedAt`) and identifies
the source as `live`. It also reports real user activity (`lastUserActivityAt`),
the latest successful background probe (`lastKeepaliveAt`), the bounded lease
deadline/state (`keepaliveEligibleUntil` / `keepaliveState`), and the detected
expiry time (`expiredAt`). `lastActivityAt` remains a compatibility alias for
`lastUserActivityAt`. Inactive sessions are reported from the registry without
starting a browser. A temporary HTTP error or an unexpected non-login response
returns `SESSION_CHECK_UNAVAILABLE` and preserves the encrypted session state;
only an explicit login response expires and deletes that state.

Open that URL in a trusted browser only when it is returned. Ordinary login
fields are submitted directly to the Credential Broker. Systems that require
human page interaction, such as Yuque's slider and SMS flow, use the same trusted
origin to display a short-lived, login-only central browser surface. Neither mode
puts credentials, OTP values, cookies, browser endpoints, or control tokens in CLI
parameters, MCP tool arguments, the operation ledger, or model-visible fields.
Card expiry applies to that one authentication challenge, not to an active target
system session.

After login, invoke a read capability:

~~~bash
python -m bscli.cli.main --home .bscli capability invoke \
  oa.workflow.pending.list \
  --user-subject <trusted-user-subject> \
  --idempotency-key <request-key>
~~~

An inactive or OA-expired session returns
`requires_user_action / LOGIN_REQUIRED`; the service never falls back to a personal browser or retired
bridge. A transient live-probe failure returns `SESSION_CHECK_UNAVAILABLE` and
must be retried without asking for credentials. `SESSION_RUNTIME_MISMATCH`
means that the encrypted state could not be authenticated by the bound runtime,
for example because a Windows security identity or Linux key changed. The
session is preserved and the request must be routed through the correct runtime.

Run the trusted-card Broker and capability Worker as one long-running central
service under a fixed OS security identity and session-key boundary. Direct CLI
processes that restore session state must use that same boundary. Agent
integrations should normally use the long-running central MCP service, which
keeps it stable across calls.

## Host-Independent Interactions

Authentication, business input, and execution authorization now share
`agentbridge.interaction.v1`. AgentBridge returns an `interaction` object and
never launches a browser itself. Codex, OpenClaw, or another host renders the
trusted URL, polls state outside the model loop, and calls the resume tool when
the user-bound record is ready.

On MCP, the trusted URL is moved to host-private
`CallToolResult._meta["io.agentbridge/interaction"]`; model-visible content and
structured output contain only a fixed placeholder. Tools that directly
present trusted interactions advertise the standard MCP Apps resource
`ui://agentbridge/trusted-interaction.html`. A compatible host can therefore
render, poll, resume, and hand a following interaction back to the user without
an AgentBridge-specific plugin. The current OpenClaw plugin remains the adapter
for hosts that do not yet implement MCP Apps.

~~~bash
python -m bscli.cli.main --home .bscli interaction get \
  <interaction-id> --user-subject <trusted-user-subject>

python -m bscli.cli.main --home .bscli interaction resume \
  <interaction-id> --user-subject <trusted-user-subject> \
  --idempotency-key <stable-resume-key>
~~~

The equivalent MCP tools are `agentbridge_interaction_get` and
`agentbridge_interaction_resume`. The installable native OpenClaw plugin in
[`integrations/openclaw-agentbridge`](integrations/openclaw-agentbridge)
captures these envelopes, withholds trusted URLs from the model, renders cards
only in private sessions, and polls/resumes outside the model loop. The Python
renderer remains a host-adapter reference. See the
[agent interaction protocol](docs/agent-interaction-protocol.md) and the
[remote MCP low-install onboarding guide](docs/remote-mcp-onboarding.md).

## Task Continuity

The Task Hub foundation, phase-two Agent Workspace, and server-backed task
continuation are active for identity-routed OpenClaw calls. A private
host adapter creates an opaque task on the first AgentBridge business tool call
and associates subsequent Operation and Interaction IDs without accepting a
model-supplied user identity or task argument. `ClientEndpoint`, `AgentTask`,
append-only `TaskEvent`, subscription, and notification-outbox records share the
central SQLite ledger, while credentials, cookies, submitted field values,
authorization secrets, and trusted-card URLs remain outside it.

The OpenClaw plugin keeps the existing Telegram and WeChat behavior. It uses the
official `gateway_start` hook to restore each configured identity's pending
interaction, original private route, polling, and card delivery after a Gateway
restart. The host-only MCP coordination tools require private request metadata
and are deliberately absent from the plugin's model-visible native tool
catalog. The independent [Agent Workspace](docs/agent-workspace.md) adds
persistent web login, OpenClaw chat, task timelines, a Continue action, and
endpoint visibility without placing MCP or Gateway tokens in the browser.
Natural-language continuation can select one same-user task, reuse its pending
trusted interaction, observe running or terminal state without duplicate work,
or attach an explicit follow-up to the original task ID. Shared model Transcript
and OBO execution-host transfer remain later phases in the
[multi-end task design](docs/omnichannel-agent-task-continuity-design.md).

## Governed OA Writes

Every published write is workflow-specific and follows trusted field collection,
live prepare, separate authorization, deterministic commit, and authoritative
readback. Draft, approval, meeting, and formal-submission scopes are independent.
Prepare tools accept optional prefill seeds only for values the user already supplied
in the conversation. Those defaults reduce duplicate entry but remain editable; the
submitted trusted-card values are authoritative. Omitted values stay inside the card,
and neither submitted values nor card URLs are echoed into model-visible results.

### Business-trip draft

The first write vertical slice saves a Seeyon business-trip application as a
wait-send draft. It never sends or submits the workflow.

1. Request the trusted business-field card:

~~~bash
python -m bscli.cli.main --home .bscli capability invoke \
  oa.business_trip.prepare \
  --user-subject <trusted-user-subject> \
  --card-base-url http://127.0.0.1:8780 \
  --idempotency-key <input-key> \
  --json '{}'
~~~

2. The host renders the returned business-input interaction. After the user
   submits the trusted form and polling reports `resume.ready=true`, resume it:

~~~bash
python -m bscli.cli.main --home .bscli interaction resume \
  <field-interaction-id> \
  --user-subject <trusted-user-subject> \
  --idempotency-key <prepare-key>
~~~

3. The second prepare validates the live template and form contract, freezes
   the plan, and returns a separate authorization interaction. After user
   approval, resume that interaction:

~~~bash
python -m bscli.cli.main --home .bscli interaction resume \
  <authorization-interaction-id> \
  --user-subject <trusted-user-subject> \
  --idempotency-key <save-key>
~~~

A successful commit reloads the server-backed wait-send item, reads its fields
back, and reports `workflow_submitted=false` and `submitted_count=0`. Uncertain
post-click outcomes are recorded as unknown and are not retried automatically.

### Business-trip formal submission

`oa.business_trip.submit.prepare` and `oa.business_trip.submit` are a separate
controlled-write pair. They require a new field submission and a new action
authorization; a draft authorization cannot be reused. Commit consumes approval
immediately before the OA send control and succeeds only after exactly one new
matching item is found in the adapter-internal sent collection and its detail can
be read back. That sent collection is verification-only and is not a public list
or detail surface.

Formal submission requires the independent `oa:write:submit` token scope. The
formal submission path has completed real authorized commit, sent-list readback,
and controlled revoke validation.

### Leave request

`oa.leave.prepare` and `oa.leave.save_draft` implement the `【HR】请假申请单`
wait-send path. The first phase supports attachment-free `年休`, `事假`, and
`调休` only. Draft success requires stable wait-send identifiers plus exact
readback of every user-entered field. OA-computed days and hours are retained as
advisory evidence because the live OA can leave both display controls blank even
after the draft is durably saved.

The 2026-07-19 live operation was reconciled read-only against OA: one matching
11:36 draft existed in `待发事项`, so the former `RESULT_UNKNOWN` was a verifier
false negative and was not retried. The draft pair remains under `oa:write:draft`.

`oa.leave.submit.prepare` and `oa.leave.submit` are a separate formal-submission
pair under `oa:write:submit`. They require a new field submission and a new action
authorization, consume approval immediately before `#sendId_a`, and succeed only
after exactly one new matching sent item and its detail can be read back. A live
leave submission has completed this path; the user later cancelled that test item manually.

### Sent workflow revoke

`oa.workflow.revoke.prepare` and `oa.workflow.revoke` expose revocation as an
independent cross-workflow controlled write. The agent first obtains an opaque
`affair_id` from `oa_workflow_list_sent`; the trusted field card collects or
prefills the mandatory revoke comment, and a separate action card freezes the
sent item's affair, summary, process, title, and form identity.

Commit uses OA's native revoke action for exactly one row. Authorization is
consumed immediately before the final confirmation, and success requires both
sent-list disappearance and the same identity returning to wait-send with the
registered revoked state. Any uncertain post-confirmation result is reported as
unknown and is never retried automatically. This is not an automatic test-data
cleanup hook because OA may notify participants or retain audit and business
side effects.

The pair requires the independent `oa:write:revoke` scope. Existing identity
tokens are not widened when the capability is deployed.
## Admin Control Plane

AgentBridge includes an independent administration surface for multi-user runtime
visibility and write governance. It uses separate administrator accounts and
sessions; it never exposes credentials, cookies, trusted-card URLs, business
field values, or issued Token secrets.

For the current intranet deployment, open
`https://10.10.50.213:8782`. Bootstrap the first administrator from a trusted
server terminal with a password supplied only on standard input:

~~~bash
sudo -u agentbridge /home/guomao/agentbridge/venv/bin/python -P \
  -m bscli.cli.main --home /home/guomao/agentbridge/data \
  admin account bootstrap --username admin --password-stdin
~~~

The first login requires an immediate password change. Administrators can issue
or revoke MCP Tokens, inspect or invalidate downstream sessions, and pause write
capabilities globally or by system, user, capability, and version. Auditors are
read-only. Every control action requires a reason and is written to an
append-only audit ledger. See the
[administration guide](docs/agentbridge-admin-console.md).
## Streamable HTTP MCP

Issue a short-lived identity token from a trusted administrator terminal:

~~~bash
python -m bscli.cli.main --home .bscli mcp token issue \
  --user-subject <trusted-user-subject> \
  --expected-principal <oa-display-name> \
  --scope oa:write:draft \
  --ttl-hours 24
~~~

Choose only the scopes required by that client. OA scopes (`oa:read`,
`oa:write:draft`, `oa:write:approval`, `oa:write:meeting`, `oa:write:submit`,
and `oa:write:revoke`) are independent from Taihua scopes (`taihua:read` and
`taihua:write:worklog`) and the Yuque read scope (`yuque:read`).
The token command adds the corresponding system's base read scope when any
write scope for that system is requested, and only creates session bindings for
systems represented by the final scope set.
Completing a trusted card or deploying a new capability never widens an already
issued token.

Start the central MCP endpoint and trusted-card service in the same process:

~~~bash
python -m bscli.cli.main --home .bscli mcp central-serve \
  --host 127.0.0.1 \
  --port 8790 \
  --auth-host 127.0.0.1 \
  --auth-port 8780 \
  --session-keepalive-interval 600 \
  --session-keepalive-lease 604800
~~~

Session keepalive is disabled unless `--session-keepalive-interval` is set. The
example probes active OA sessions every 10 minutes while they remain inside an
eight-hour activity lease. Login and real agent requests renew that lease;
background probes do not renew themselves. An explicit OA login response
expires the session, while a transient probe failure preserves it for retry.

Connect the MCP client to http://127.0.0.1:8790/mcp with an Authorization Bearer
header. MCP tools derive caller identity from the server-side token binding and
do not accept userSubject arguments.

After connection, call `agentbridge_server_profile` to discover the transport,
interaction delivery methods, client footprint, and write-safety boundary.
The same profile is available as `agentbridge://server/profile`, and the
`agentbridge_oa_operator` MCP prompt supplies concise operating rules without
requiring a separately installed Skill.

For an intranet deployment, OpenClaw may run on the user's workstation while
AgentBridge runs on another company-network machine. Issue a private-IP server
certificate from a DPAPI-protected AgentBridge internal CA on the Windows
administrator workstation:

~~~powershell
$TlsPackage = Join-Path $env:TEMP "agentbridge-tls"
python -m bscli.cli.main pki issue-server `
  --ip 10.20.30.40 `
  --state-dir "$env:USERPROFILE\.agentbridge\pki" `
  --output-dir $TlsPackage
Import-Certificate `
  -FilePath "$env:USERPROFILE\.agentbridge\pki\root-ca.crt" `
  -CertStoreLocation Cert:\CurrentUser\Root
~~~

Deploy only `$TlsPackage\server.crt` and `$TlsPackage\server.key` to the Linux
host, then delete the temporary package. The protected root
private key stays on the Windows workstation and must never be copied to Linux
or committed. Start AgentBridge with both listeners using the same IP-SAN
certificate:

~~~bash
python -m bscli.cli.main --home .bscli mcp central-serve \
  --host 10.20.30.40 \
  --port 8790 \
  --public-base-url https://10.20.30.40:8790 \
  --tls-cert /path/to/server.crt \
  --tls-key /path/to/server.key \
  --auth-host 10.20.30.40 \
  --auth-port 8780 \
  --auth-public-base-url https://10.20.30.40:8780 \
  --auth-tls-cert /path/to/server.crt \
  --auth-tls-key /path/to/server.key \
  --session-keepalive-interval 600 \
  --session-keepalive-lease 604800
~~~

Configure OpenClaw with the HTTPS endpoint and exact trusted-card origin. Store
the CA path in OpenClaw's durable service environment so it is written into the
managed Gateway launcher and survives future restarts:

~~~powershell
openclaw config set env.vars.NODE_EXTRA_CA_CERTS "$env:USERPROFILE\.agentbridge\pki\root-ca.crt"
openclaw config set plugins.entries.agentbridge-interactions.config.mcpUrl https://10.20.30.40:8790/mcp
openclaw config set plugins.entries.agentbridge-interactions.config.allowedCardOrigins.0 https://10.20.30.40:8780
openclaw config set tools.alsoAllow '[\"agentbridge-interactions\"]' --strict-json
~~~

For a multi-user Gateway, configure one plugin `identityBindings` entry and one
environment-backed Bearer token per trusted messaging identity. Do not keep a
global `mcp.servers.agentbridge` Bearer alongside multi-user bindings; that
would expose a second shared-identity tool surface. See
`docs/openclaw-multi-user-identity-routing.md` for the complete mapping.

When OpenClaw uses a restricted tool profile such as `coding`, the native
AgentBridge adapter must be explicitly added through `tools.alsoAllow`.
Allow only `agentbridge-interactions`, not `group:plugins`, and merge it with
any existing `alsoAllow` entries.

Telegram then presents credential, business-input, and execution-authorization
cards as native Web App buttons inside its own WebView instead of opening an
external browser.

The OpenClaw plugin is a host compatibility adapter, not part of the central
business architecture. MCP Apps-capable hosts need only the remote MCP
connection, TLS trust, and MCP authorization. Core-MCP-only hosts can use read
tools while the OA session is active, but require either MCP Apps or a private
host adapter for login, business input, and execution authorization.

Loopback HTTP remains a local-development mechanism. The explicit private-IP
HTTP switch is retained only for isolated recovery and must not be used for a
routable deployment. Production remote access also requires enterprise
OAuth/OIDC, token lifecycle policy, rate limiting, and real multi-user worker
isolation.

## Security Invariants

- Final-user devices install no browser extension, local daemon, or OA connector.
- Each user has a distinct central session and managed browser profile.
- Credentials and trusted-card field values bypass the model and MCP.
- The internal root private key is DPAPI-protected on the administrator
  workstation; Linux receives only a leaf certificate and leaf private key.
- Every write follows prepare -> authorize -> commit -> verify.
- A plan, authorization, and idempotency key are immutable at commit time.
- No capability silently falls back to a less-governed execution route.
- Windows session state uses user-scoped DPAPI; Linux uses a restricted
  key-file AES-256-GCM protector. Production multi-host deployments require a
  Vault/KMS-backed protector with workload identity and key rotation.

## Validation

On Windows, use the persistent layered validation entry points:

~~~powershell
.\scripts\Invoke-AgentBridgeValidation.ps1 `
  -Mode Targeted `
  -PythonTests @('tests/test_auth_challenges.py', 'tests/test_central_service.py') `
  -OpenClaw

.\scripts\Invoke-AgentBridgeValidation.ps1 -Mode Full
.\scripts\Test-AgentBridgeMcp.ps1 -Check SessionStatus
.\scripts\Deploy-AgentBridge.ps1 -PlanOnly
~~~

Targeted OpenClaw checks skip `npm pack` unless `-PackCheck` is supplied; full
validation always includes it. The persistent Python 3.12 environment is
fingerprinted from `pyproject.toml`, so unchanged dependencies are reused.
See the [development validation and release workflow](docs/development-and-release-workflow.md)
for MCP smoke-test safety boundaries and wheel deployment commands.

The central path has completed real-OA validation for trusted-card login,
encrypted-session restoration, workflow reads, rendered details and opinions,
business-field collection, authorization, draft and formal submission, approval,
revoke, meeting creation, field readback, and idempotent replay. Results identify
the actual central execution channel through `transport`.

Formal Windows current-user root trust, native TLS, and production Telegram
WebView clicks for credential, business-input, and execution-authorization cards
are now validated. Login-card reuse and login-completion continuation are covered
by central and host tests; a currently active real session also returned
`reused=true` without a new interaction. A second real OA user, real mobile CA
distribution, a natural-expiry end-to-end continuation observation, and additional
central write workflows remain open validation items. The current intranet server
and OpenClaw path use private-IP HTTPS with a dedicated internal CA.

## Documentation

Start with the [documentation map](docs/README.md). The primary references are:

- [Target architecture](agent-oriented-legacy-bs-adaptation-design.md)
- [Agent interaction protocol](docs/agent-interaction-protocol.md)
- [Governed write model](docs/governed-write-model.md)
- [Current Linux intranet deployment](docs/current-deployment-plan.md)
- [Development validation and release workflow](docs/development-and-release-workflow.md)
- [PoC validation plan](poc-validation-plan.md)
- [Deferred production considerations](deferred-considerations.md)
