# AgentBridge CLI Helper

Python-first, non-intrusive adapters that expose legacy B/S system capabilities
to agents without modifying the target system.

The active runtime is central AgentBridge:

- Versioned business capabilities shared by CLI and MCP
- Per-user managed Playwright profiles and HTTP sessions
- Trusted authentication, business-field, and write-authorization cards
- A Credential Broker that keeps credentials outside model-visible channels
- A durable operation ledger with idempotency and explicit outcome states
- Seeyon OA support with six read capabilities and one governed draft workflow

The original Chrome extension, browser bridge, localhost daemon, daemon-backed
MCP server, and their public CLI commands were retired on 2026-07-13. They are
not fallback paths. See
[the retirement ledger](docs/legacy-bridge-retirement.md) for the remaining
capability-migration inventory.

## Requirements

- Python 3.12 or newer
- A Chromium runtime supported by Playwright
- Network access from the central worker to the target legacy system

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

An inactive or OA-expired session returns `requires_user_action /
LOGIN_REQUIRED`; the service never falls back to a personal browser or retired
bridge. A transient live-probe failure returns `SESSION_CHECK_UNAVAILABLE` and
must be retried without asking for credentials. `SESSION_RUNTIME_MISMATCH`
means that the encrypted state was opened under a different Windows security
identity; the session is preserved and the request must be routed through the
bound central runtime.

Run the trusted-card Broker and capability Worker as one long-running central
service under a fixed OS security identity. Direct CLI processes that restore
Windows DPAPI state must run under that same identity. Agent integrations
should normally use the long-running central MCP service, which keeps this
runtime boundary stable across calls.

## Governed Business-Trip Draft

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

2. The user fills the returned /input/<opaque-id> card. Continue using only its
   opaque submission ID:

~~~bash
python -m bscli.cli.main --home .bscli capability invoke \
  oa.business_trip.prepare \
  --user-subject <trusted-user-subject> \
  --card-base-url http://127.0.0.1:8780 \
  --idempotency-key <prepare-key> \
  --json '{"input_submission_id":"<input-submission-id>"}'
~~~

3. The second prepare validates the live template and form contract, freezes
   the plan, and returns a separate authorization card. After user approval,
   save the draft:

~~~bash
python -m bscli.cli.main --home .bscli capability invoke \
  oa.business_trip.save_draft \
  --user-subject <trusted-user-subject> \
  --idempotency-key <save-key> \
  --json '{"authorization_id":"<authorization-id>"}'
~~~

A successful commit reloads the server-backed wait-send item, reads its fields
back, and reports workflow_submitted=false and submitted_count=0. Uncertain
post-click outcomes are recorded as unknown and are not retried automatically.

## Streamable HTTP MCP

Issue a short-lived identity token from a trusted administrator terminal:

~~~bash
python -m bscli.cli.main --home .bscli mcp token issue \
  --user-subject <trusted-user-subject> \
  --expected-principal <oa-display-name> \
  --scope oa:write:draft \
  --ttl-hours 24
~~~

Start the central MCP endpoint and trusted-card service in the same process:

~~~bash
python -m bscli.cli.main --home .bscli mcp central-serve \
  --host 127.0.0.1 \
  --port 8790 \
  --auth-host 127.0.0.1 \
  --auth-port 8780
~~~

Connect the MCP client to http://127.0.0.1:8790/mcp with an Authorization Bearer
header. MCP tools derive caller identity from the server-side token binding and
do not accept userSubject arguments.

Loopback HTTP and pre-issued Bearer tokens are PoC bootstrap mechanisms.
Non-loopback deployments require TLS. Production remote access also requires
enterprise OAuth/OIDC, token lifecycle policy, reverse-proxy trust validation,
rate limiting, and real multi-user worker isolation.

## Security Invariants

- Final-user devices install no browser extension, local daemon, or OA connector.
- Each user has a distinct central session and managed browser profile.
- Credentials and trusted-card field values bypass the model and MCP.
- Every write follows prepare -> authorize -> commit -> verify.
- A plan, authorization, and idempotency key are immutable at commit time.
- No capability silently falls back to a less-governed execution route.
- Windows session state uses user-scoped DPAPI; production multi-host
  deployments require an equivalent Vault/KMS-backed protector.

## Validation

~~~bash
python -m unittest discover -s tests
python -m compileall -q bscli
python -m pip check
~~~

The central path has completed single-user real-OA validation for trusted-card
login, encrypted-session restoration, workflow reads, rendered details and
opinions, business-field collection, authorization, wait-send draft save,
field readback, and idempotent replay. Every validated operation reported
browser_bridge_used=false.

A second real OA user, production TLS/reverse-proxy deployment, real mobile
network access, and additional central write workflows remain open validation
items.

## Design Documents

- [Target architecture](agent-oriented-legacy-bs-adaptation-design.md)
- [PoC validation plan](poc-validation-plan.md)
- [Deferred production considerations](deferred-considerations.md)
- [Legacy bridge retirement ledger](docs/legacy-bridge-retirement.md)
