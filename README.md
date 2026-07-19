# AgentBridge CLI Helper

Python-first, non-intrusive adapters that expose legacy B/S system capabilities
to agents without modifying the target system.

The active runtime is central AgentBridge:

- Versioned business capabilities shared by CLI and MCP
- Per-user managed Playwright profiles and HTTP sessions
- Trusted authentication, business-field, and write-authorization cards
- A Credential Broker that keeps credentials outside model-visible channels
- A durable operation ledger with idempotency and explicit outcome states
- Seeyon OA support with six read capabilities and twelve governed workflow-stage capabilities

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
- oa.workflow.done.list
- oa.workflow.tracked.list
- oa.workflow.detail.get
- oa.workflow.opinions.list
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
- oa.meeting.create.prepare
- oa.meeting.create

Workflow capabilities expose business data and opaque affair IDs. They do not
expose internal URLs, raw HTML, cookies, private action endpoints, or hidden
form fields.

## Trusted Login

Start the trusted-card service. The same listener serves authentication,
business-input, and write-authorization cards:

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
the source as `live`. Inactive sessions are reported from the registry without
starting a browser. A temporary HTTP error or an unexpected non-login response
returns `SESSION_CHECK_UNAVAILABLE` and preserves the encrypted session state;
only an explicit login response expires and deletes that state.

Open that URL in a trusted browser only when it is returned. Credentials are
submitted directly to the Credential Broker. They are never CLI parameters,
MCP tool arguments, operation-ledger values, or model-visible fields. Card
expiry applies to that one authentication challenge, not to an already active
OA session.

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
real central OA session has passed the non-mutating prepare/preflight path; an
actual submission remains pending a specifically approved live test.

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
after exactly one new matching sent item and its detail can be read back. No live
leave submission has been performed by this implementation change.

## Streamable HTTP MCP

Issue a short-lived identity token from a trusted administrator terminal:

~~~bash
python -m bscli.cli.main --home .bscli mcp token issue \
  --user-subject <trusted-user-subject> \
  --expected-principal <oa-display-name> \
  --scope oa:write:draft \
  --ttl-hours 24
~~~

Choose only the scopes required by that client: `oa:write:draft`,
`oa:write:approval`, `oa:write:meeting`, and `oa:write:submit` are independent.
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
  --session-keepalive-lease 28800
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
  --session-keepalive-lease 28800
~~~

Configure OpenClaw with the HTTPS endpoint and exact trusted-card origin. Store
the CA path in OpenClaw's durable service environment so it is written into the
managed Gateway launcher and survives future restarts:

~~~powershell
openclaw config set env.vars.NODE_EXTRA_CA_CERTS "$env:USERPROFILE\.agentbridge\pki\root-ca.crt"
openclaw config set mcp.servers.agentbridge.url https://10.20.30.40:8790/mcp
openclaw config set plugins.entries.agentbridge-interactions.config.allowedCardOrigins.0 https://10.20.30.40:8780
~~~

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

The central path has completed single-user real-OA validation for trusted-card
login, encrypted-session restoration, workflow reads, rendered details and
opinions, business-field collection, authorization, wait-send draft save,
field readback, and idempotent replay. Every validated operation reported
browser_bridge_used=false.

Formal Windows current-user root trust, native TLS, and production Telegram
WebView clicks for credential, business-input, and execution-authorization cards
are now validated. Login-card reuse and login-completion continuation are covered
by central and host tests; a currently active real session also returned
`reused=true` without a new interaction. A second real OA user, real mobile CA
distribution, a natural-expiry end-to-end continuation observation, and additional
central write workflows remain open validation items. The current intranet server
and OpenClaw path use private-IP HTTPS with a dedicated internal CA.

## Design Documents

- [Development validation and release workflow](docs/development-and-release-workflow.md)
- [Current Linux intranet PoC deployment plan](docs/current-deployment-plan.md)
- [Target architecture](agent-oriented-legacy-bs-adaptation-design.md)
- [PoC validation plan](poc-validation-plan.md)
- [Deferred production considerations](deferred-considerations.md)
- [Legacy bridge retirement ledger](docs/legacy-bridge-retirement.md)
- [Agent interaction protocol](docs/agent-interaction-protocol.md)
- [Remote MCP low-install onboarding](docs/remote-mcp-onboarding.md)
