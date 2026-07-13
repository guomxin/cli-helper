# BSCLI

BSCLI is a Python-first adapter platform for turning existing B/S system capabilities into CLI commands and agent-callable tools.

This repository currently contains the original browser-bridge prototype and
the first central AgentBridge vertical slice:

- Python CLI
- Python local daemon
- Chrome extension bridge
- Command registry
- Trace store
- Seeyon OA example profile for `http://10.10.50.110/seeyon/main.do?method=main`
- Versioned business capability registry and operation ledger
- Per-user central Playwright profiles and session registry
- One-time trusted authentication cards and a memory-only Credential Broker
- Extension-independent central OA package with six read capabilities plus a
  governed business-trip draft write

## Central AgentBridge Slice

The new central path does not use the client Chrome extension or the localhost
daemon. Install the pinned Python dependency and Chromium runtime first:

```bash
python -m pip install -e .
python -m playwright install chromium
```

Discover central business capabilities:

```bash
python -m bscli.cli.main --home .bscli capability list
python -m bscli.cli.main --home .bscli capability describe oa.template.list
python -m bscli.cli.main --home .bscli capability describe oa.workflow.detail.get
```

Start the trusted authentication-card service. Loopback HTTP is supported only
for the local PoC:

```bash
python -m bscli.cli.main --home .bscli auth serve --host 127.0.0.1 --port 8780 --public-base-url http://127.0.0.1:8780
```

In another terminal, create a one-time `AuthChallenge` for the per-user central
OA session:

```bash
python -m bscli.cli.main --home .bscli session login --system oa --user-subject <trusted-user-subject> --expected-principal <oa-display-name> --card-base-url http://127.0.0.1:8780
python -m bscli.cli.main --home .bscli auth status <challenge-id>
```

Open the returned `nextAction.cardUrl` in a trusted system browser. Credentials
are submitted directly to the Credential Broker and are never CLI arguments or
model-visible fields. The Broker fills the registered Seeyon login form, lets
the page run its native login handler, verifies the observed OA principal, and
stores only the resulting cookies. A challenge is short-lived, CSRF-bound, and
can be consumed only once.

Non-loopback deployments require a certificate, key, and HTTPS public URL via
`auth serve --tls-cert ... --tls-key ... --public-base-url https://...`. The
current PoC has not yet validated reverse-proxy identity, a real mobile client,
or a second OA user.

Invoke the read capability and inspect its durable operation record:

```bash
python -m bscli.cli.main --home .bscli capability invoke oa.template.list --user-subject <trusted-user-subject> --idempotency-key <request-key>
python -m bscli.cli.main --home .bscli capability invoke oa.workflow.pending.list --user-subject <trusted-user-subject> --idempotency-key <request-key>
python -m bscli.cli.main --home .bscli capability invoke oa.workflow.done.list --user-subject <trusted-user-subject> --idempotency-key <request-key>
python -m bscli.cli.main --home .bscli capability invoke oa.workflow.tracked.list --user-subject <trusted-user-subject> --idempotency-key <request-key>
python -m bscli.cli.main --home .bscli capability invoke oa.workflow.detail.get --user-subject <trusted-user-subject> --json '{"collection":"done","affair_id":"<opaque-id>"}' --idempotency-key <request-key>
python -m bscli.cli.main --home .bscli capability invoke oa.workflow.opinions.list --user-subject <trusted-user-subject> --json '{"collection":"done","affair_id":"<opaque-id>"}' --idempotency-key <request-key>
python -m bscli.cli.main --home .bscli operation list --user-subject <trusted-user-subject>
python -m bscli.cli.main --home .bscli operation get <operation-id>
```

Workflow lists discover the current user's rendered section contract and then
read the section through the central HTTP session. Detail and opinion reads
resolve only opaque IDs returned by those lists, render the page in the same
central browser session, and merge same-origin iframe content. Public results
exclude internal OA URLs, raw HTML, cookies, action endpoints, and write hints.

The first central write vertical slice is deliberately narrower than the legacy
bridge commands. It prepares and saves an `【HR】出差申请单` draft, but never
sends or submits the workflow:

```bash
python -m bscli.cli.main --home .bscli capability invoke oa.business_trip.prepare --user-subject <trusted-user-subject> --card-base-url http://127.0.0.1:8780 --idempotency-key <prepare-key> --json '{"start_time":"2026-07-14 09:00","end_time":"2026-07-14 18:00","travel_mode":"火车","origin":"济南","destination":"青岛","reason":"客户交流","has_direct_supervisor":false}'
```

`prepare` validates the live template and CAP4 form contract without filling or
clicking anything. It freezes the exact plan and returns a separate trusted
`nextAction.cardUrl`. After the user approves that card, invoke the returned
save capability with the opaque one-time authorization:

```bash
python -m bscli.cli.main --home .bscli capability invoke oa.business_trip.save_draft --user-subject <trusted-user-subject> --idempotency-key <save-key> --json '{"authorization_id":"<authorization-id>"}'
```

The authorization is bound to the user, OA session, capability version, frozen
plan hash, and TTL, and is consumed exactly once at the commit boundary. A
successful result must reload the server-backed wait-send draft, read the fields
back, and report `workflow_submitted=false` and `submitted_count=0`. A failure
after the save click is recorded as `unknown` and is not retried automatically.
Optional outer fields such as `note` are accepted only when the live template
renders them as editable. A hidden `content_coll` is rejected during `prepare`
instead of producing an authorization that cannot be executed.

An inactive or expired session returns `requires_user_action` with
`error.code=LOGIN_REQUIRED`; it never silently falls back to the extension.
On Windows, process-level OA session cookies are stored only as a DPAPI-encrypted
blob under `.bscli/session-secrets`; the SQLite ledger and CLI output never
contain cookie values. The Credential Broker and capability Worker must run as
the same Windows security principal because user-scoped DPAPI ciphertext cannot
be decrypted by another account. Other operating systems must provide an
equivalent Vault/KMS-backed protector before this session path can run.

The 2026-07-11 live Seeyon validation authenticated the expected principal
`辛国茂` through the card. Fresh CLI processes restored the encrypted session
and read 118 templates. The expanded package then read 3 pending, 9 done, and
9 tracked workflows through the section API, and rendered one done workflow
with 8 business fields and 1 structured opinion. Every result reported
`browser_bridge_used=false`; list reads used `central_http_session`, while
detail and opinion reads used `central_browser_session`. This proves the
single-user central path only; cross-user isolation still requires a second
real account and separate Worker security principals.

The 2026-07-13 W1 validation prepared a frozen business-trip plan, obtained
approval through the separate trusted action card, consumed that authorization
once at the save boundary, and created a wait-send draft for the expected OA
principal. The server-backed draft reload returned stable summary and affair
identifiers and matched all seven requested business fields. The result reported
`browser_bridge_used=false`, `workflow_submitted=false`, and
`submitted_count=0`. During validation, a browser-native form-serialization bug
in the action card was fixed and a hidden optional-note field was moved into
prepare-time validation.

## Central Streamable HTTP MCP

The central MCP path uses the same `CentralCapabilityService`, operation ledger,
session registry, encrypted cookie state, and Seeyon adapter as the CLI. The MCP
tools do not accept `userSubject`; caller identity comes only from a server-side
Bearer identity-token binding.

From a trusted administrator terminal, bind and issue a short-lived client token:

```bash
python -m bscli.cli.main --home .bscli mcp token issue --user-subject <trusted-user-subject> --expected-principal <oa-display-name> --label <client-name> --ttl-hours 24
python -m bscli.cli.main --home .bscli mcp token issue --user-subject <trusted-user-subject> --expected-principal <oa-display-name> --label <draft-client-name> --scope oa:write:draft --ttl-hours 24
python -m bscli.cli.main --home .bscli mcp token list --user-subject <trusted-user-subject>
python -m bscli.cli.main --home .bscli mcp token revoke <token-id>
```

The bearer secret is displayed only by `token issue`. It is stored server-side
only as a SHA-256 digest and must go directly into the trusted MCP client
configuration, never into a model prompt, tool argument, chat, or ordinary log.

Start the Streamable HTTP MCP endpoint and authentication-card service in one
process so Windows DPAPI always uses the same security principal:

```bash
python -m bscli.cli.main --home .bscli mcp central-serve --host 127.0.0.1 --port 8790 --auth-host 127.0.0.1 --auth-port 8780
```

Connect the MCP client to `http://127.0.0.1:8790/mcp` with
`Authorization: Bearer <issued-token>`. The server exposes the six central OA
read capabilities, `oa_business_trip_prepare`,
`oa_business_trip_save_draft`, session status/login, and caller-scoped
operation-ledger tools. Write tools require `oa:write:draft`; read-only tokens
cannot call them. `oa_session_login` returns a trusted card URL, and write
prepare returns a separate action-card URL. Credentials and approval decisions
both bypass MCP and the model.

Loopback HTTP and pre-issued Bearer tokens are PoC bootstrap mechanisms.
Non-loopback MCP and authentication-card listeners both require HTTPS and
explicit certificates/public URLs. Production remote access still requires a
real OAuth/OIDC authorization server, token rotation and revocation policy,
reverse-proxy trust validation, rate limiting, and a second-user isolation test.

The 2026-07-12 real MCP validation used the official Python MCP client to call
`oa_session_login`, opened the returned trusted card, authenticated the expected
OA principal, and then called `oa_workflow_pending_list`. It read 3 pending
items with `transport=central_http_session` and `browserBridgeUsed=false`, wrote
operation `a9965f5e-fb08-4561-8fdc-809e11b39c4e`, automatically revoked the
one-time identity token, and closed both loopback listeners.

The legacy path below remains available as a migration oracle while capabilities
are moved one by one.

## Quick Start

Initialize the Seeyon OA profile:

```bash
python -m bscli.cli.main --home .bscli system init-seeyon-oa
```

Start the local daemon:

```bash
python -m bscli.cli.main --home .bscli daemon serve --host 127.0.0.1 --port 8765
```

The daemon creates `.bscli/daemon-token` on first start. CLI and MCP calls that
use the same `--home` read this token automatically when calling protected local
daemon endpoints such as `/commands/run`.

Load the Chrome extension:

1. Open `chrome://extensions`.
2. Enable Developer mode.
3. Choose "Load unpacked".
4. Select `D:\Codes\CLIExp\extension`.

After changing files under `extension/`, click "Reload" for the unpacked
extension so the background service worker picks up the new task handlers.
The checked-in extension manifest is scoped to the built-in Seeyon OA origin
`http://10.10.50.110/*`. Adding a new system profile configures daemon routing,
but Chrome still needs matching extension host permissions and a reload before
that new system can be bridged.

Open and log in to the OA system in Chrome:

```text
http://10.10.50.110/seeyon/main.do?method=main
```

After login, request a DOM snapshot task:

```bash
python -m bscli.cli.main --home .bscli explore dom-snapshot oa
```

Or run the first Seeyon OA command and wait for the extension result:

```bash
python -m bscli.cli.main --home .bscli command run oa current_page_snapshot --timeout 30
```

Check whether the daemon sees a connected OA browser tab:

```bash
python -m bscli.cli.main --home .bscli command run oa session_status --timeout 5
```

Every daemon command run returns a `run_id` and writes an audit record to
`.bscli/trace.db`. Inspect recent runs with:

```bash
python -m bscli.cli.main --home .bscli trace list
python -m bscli.cli.main --home .bscli trace show <run_id>
```

The Chrome extension registers each open HTTP/HTTPS tab as a separate bridge
client, so the OA tab does not have to be the active foreground tab. When
several browser tabs are registered, BSCLI routes each task only to a tab whose
URL origin matches the target system profile. For the built-in OA case, that
means an origin of `http://10.10.50.110`. For a new B/S system, add a profile
first:

```bash
python -m bscli.cli.main --home .bscli system add crm --name "CRM" --url http://crm.example.test/home
```

Then `explore dom-snapshot crm` will be delivered only to a browser tab opened
on one of that profile's allowed origins, after the extension has been granted
matching host permissions for the CRM origin.

List built-in OA commands:

```bash
python -m bscli.cli.main command list oa
```

Export agent-callable tool metadata for the OA adapter:

```bash
python -m bscli.cli.main tool manifest oa
```

The manifest uses stable tool names such as `oa__pending_list`, includes JSON
Schema input definitions, and keeps the original `system` / `command` metadata
needed to call `command run`.

Run BSCLI as a local MCP stdio server:

```bash
python -m bscli.cli.main --home .bscli mcp serve --daemon-url http://127.0.0.1:8765
```

The MCP server exposes the same tool names as the manifest and forwards tool
calls to the local daemon, which then uses the logged-in browser bridge.
Saved discovered APIs are exposed too, for example
`oa__discovered__template_section`, and are mapped internally to
`discovered_run`.
Discovered tools that are not low-risk `GET` calls expose a required boolean
`confirm` argument; the daemon will run them only when that argument is exactly
`true`.
Tool arguments are validated against each tool's JSON Schema before any daemon
call is made.
Backend execution failures, such as a stopped daemon or disconnected browser
extension, are returned as MCP tool results with `isError: true` and actionable
next steps.

Run the richer page inventory command for adapter discovery:

```bash
python -m bscli.cli.main --home .bscli command run oa page_inventory --timeout 30
```

Read OA portal tabs, left-side shortcuts, and home-page sections:

```bash
python -m bscli.cli.main --home .bscli command run oa navigation_inventory --timeout 30
```

## OA Business CLI

The `oa` command group exposes business-oriented aliases for common Seeyon OA
operations. These commands still run through the same daemon, Chrome extension
bridge, trace store, origin policy, and confirmation gate.

Session and page inspection:

```bash
python -m bscli.cli.main --home .bscli oa status
python -m bscli.cli.main --home .bscli oa doctor
python -m bscli.cli.main --home .bscli oa capabilities
python -m bscli.cli.main --home .bscli oa page snapshot
python -m bscli.cli.main --home .bscli oa page inventory
python -m bscli.cli.main --home .bscli oa nav list
```

`oa doctor` checks the daemon, trace store, browser bridge session, discovered
API count, and static capability map. `oa capabilities` returns the
agent-facing read/write/discovered capability map without executing writes.

Read an OA detail page from a URL found in list output:

```bash
python -m bscli.cli.main --home .bscli oa detail read --url "http://10.10.50.110/seeyon/collaboration/collaboration.do?method=summary&affairId=..."
python -m bscli.cli.main --home .bscli oa detail attachments --url "http://10.10.50.110/seeyon/collaboration/collaboration.do?method=summary&affairId=..."
python -m bscli.cli.main --home .bscli oa detail workflow --url "http://10.10.50.110/seeyon/collaboration/collaboration.do?method=summary&affairId=..."
python -m bscli.cli.main --home .bscli oa detail actions --url "http://10.10.50.110/seeyon/collaboration/collaboration.do?method=summary&affairId=..."
```

The detail reader opens the target URL in a temporary inactive Chrome tab,
waits for the page to render, captures HTML inside the logged-in browser
context, and extracts page text, table-like form fields, attachment download
links, workflow/opinion hints, and candidate write actions. Candidate actions
are discovery metadata only; they do not execute page writes.

Workflow read commands:

```bash
python -m bscli.cli.main --home .bscli oa inbox analyze --type pending --limit 10
python -m bscli.cli.main --home .bscli oa inbox analyze --type pending --deep --deep-limit 2 --text-limit 800
python -m bscli.cli.main --home .bscli oa workflow list --type pending
python -m bscli.cli.main --home .bscli oa workflow search --type pending --keyword weekly
python -m bscli.cli.main --home .bscli oa workflow list --type sent --limit 10
python -m bscli.cli.main --home .bscli oa workflow brief --type pending --limit 20
python -m bscli.cli.main --home .bscli oa workflow inspect --type pending --id 6924695233995293606
python -m bscli.cli.main --home .bscli oa workflow evidence --type pending --id 6924695233995293606
python -m bscli.cli.main --home .bscli oa workflow timeline --type pending --id 6924695233995293606
python -m bscli.cli.main --home .bscli oa workflow detail --type pending --id 6924695233995293606
python -m bscli.cli.main --home .bscli oa workflow opinions --type pending --id 6924695233995293606
python -m bscli.cli.main --home .bscli oa workflow opinions --type pending --keyword weekly --limit 3
python -m bscli.cli.main --home .bscli oa workflow attachments --type sent --limit 10 --format csv --fields source_title,name,href
python -m bscli.cli.main --home .bscli oa workflow actions --type pending --limit 10 --format table --fields source_title,code,label,risk
python -m bscli.cli.main --home .bscli oa workflow opinions --url "http://10.10.50.110/seeyon/collaboration/collaboration.do?method=summary&affairId=..."
```

`oa workflow` is the agent-facing workflow toolbox. It reuses the existing
pending/sent list APIs and the detail reader, but exposes them as one business
surface for finding workflows, reading detail pages, collecting opinions,
attachments, and candidate actions. Today `--type` supports `pending` and
`sent`; more workflow collections should be added only after their backing OA
API has been discovered and verified. Prefer `--id` for business commands; the
optional `--url` value is a rendered OA detail-page URL fallback, not a backend
opinion API URL.

`oa workflow brief` is list-only and does not open workflow detail pages, so it
will not change a pending item's read/unread state. `inspect`, `evidence`, and
`timeline` intentionally open a single rendered detail page and include
`read_effect` metadata noting that pending detail reads may mark an item as
read.

`oa inbox analyze` is the higher-level inbox triage command for agents. By
default it calls the list-only brief reader, ranks work items with deterministic
attention signals, and returns follow-up commands such as `oa workflow evidence
--id ...`. It only opens workflow detail pages when `--deep` is passed, and then
only up to `--deep-limit`; this is useful when an agent needs a small evidence
packet before asking for or preparing a later write action.

The same workflow surface is also exported to agents through the tool manifest
and MCP server. Current tool names include `oa__workflow_list`,
`oa__workflow_brief`, `oa__workflow_inspect`, `oa__workflow_evidence`,
`oa__workflow_timeline`, `oa__workflow_detail`, `oa__workflow_opinions`,
`oa__workflow_attachments`, `oa__workflow_actions`, `oa__history_list`,
`oa__history_sections`, and `oa__inbox_analyze`.
For example, an agent can call
`oa__workflow_opinions` with `{"type":"pending","id":"..."}` and let the daemon
resolve the list item, open the rendered detail page, and return only the
opinion entries. Opinion entries always include `text`; when the rendered page
has a recognizable pattern they also include structured `handler`, `opinion`,
and `time` fields.

Historical workflow samples:

```bash
python -m bscli.cli.main --home .bscli oa history sections
python -m bscli.cli.main --home .bscli oa history list --kind done --limit 20
python -m bscli.cli.main --home .bscli oa history search --kind tracked --keyword contract --limit 10
python -m bscli.cli.main --home .bscli oa history profile --kind done --limit 50
python -m bscli.cli.main --home .bscli oa history clusters --kind all --limit 20
python -m bscli.cli.main --home .bscli oa history export --kind sent --format csv --fields title,status,date,affair_id
python -m bscli.cli.main --home .bscli oa matter profile --kind all --limit 50
python -m bscli.cli.main --home .bscli oa matter matrix --kind all --limit 50
python -m bscli.cli.main --home .bscli oa matter inspect --id <matter_id>
python -m bscli.cli.main --home .bscli oa matter inspect --id <matter_id> --with-launch
python -m bscli.cli.main --home .bscli oa matter preflight --keyword "weekly" --intent approve --opinion "read"
python -m bscli.cli.main --home .bscli oa matter preflight --id <pending_affair_id> --intent archive --opinion "read"
python -m bscli.cli.main --home .bscli oa matter execute --keyword "weekly" --intent approve --opinion "read" --confirm
python -m bscli.cli.main --home .bscli oa matter execute --keyword "meeting" --intent join --feedback "will attend" --confirm
```

`oa history` reads historical tabs such as sent, done, and tracked from the OA
home page. It discovers tab ids from `navigation_inventory`, then replays the
same `sentSection` backend projection API with the selected `panelId`. This
avoids clicking tabs in the browser while still using the user's real logged-in
session. Historical detail reads are treated as read-only samples and report
`read_effect.may_mark_read=false`.
`oa history profile` clusters historical titles, categories, statuses, dates,
`affair_id`, and `href` into high-frequency workflow types. `clusters` is the
same profile view under a more business-oriented name.
`oa matter profile` turns those historical clusters into a matter catalog by
matching each matter type to the user's launchable OA templates. It reports
matched and unmatched templates, sample historical items, and available atomic
actions such as `launch_save_draft`.
`oa matter matrix` projects the same catalog into an agent-facing capability
matrix. Each matter row summarizes launch handling, received-pending handling,
coverage status, next safe commands, and verification requirements without
opening launch pages or executing writes.
Special modules can surface promoted module-specific commands in the same
matrix. `matter-meeting-create` now reports `direct_create_ready` with
`meeting_create_inspect`, `meeting_create_dry_run`, and
`meeting_create_execute` instead of pretending meeting launch is a normal
collaboration-template save-draft path.
Workflow-specific samples can also refine received-pending handling. The first
sample is `matter-missed-punch-request`, which keeps the user-facing intent at
`approve`, maps it to governed `ContinueSubmit`, uses the default opinion
`同意`, and verifies success by pending-list disappearance.
Workflow-specific launch samples refine the start/draft side. The first
launch-side sample is `matter-business-trip-request`, which keeps agents at
the matter layer and uses `content_coll` as the default low-risk draft field:
inspect, dry-run, then the confirmed draft-only path.

```powershell
python -m bscli.cli.main --home .bscli oa matter inspect --id matter-business-trip-request --with-launch
python -m bscli.cli.main --home .bscli oa matter launch-dry-run --id matter-business-trip-request --field content_coll="Draft note"
python -m bscli.cli.main --home .bscli oa matter launch-save-draft --id matter-business-trip-request --field content_coll="Draft note" --confirm
```

This is not a submitted business trip workflow; it is the promoted draft-level
sample for a human-launchable form.
System-generated flows such as weekly report sending remain received-side
handling samples rather than launch samples.
`oa matter inspect` reads one matter entry by id or name. By default it does not
open the template launch page; add `--with-launch` when you explicitly want the
matched template's fields and save-draft controls inspected.
`oa matter preflight` is the first business-intent layer for received pending
items. It lets agents ask for an intent such as `approve` or `archive` without
choosing raw OA action codes. The daemon resolves the pending item, reads
workflow evidence, maps the intent to an internal binding such as
`ContinueSubmit` or `Archive`, and returns a read-only
`bscli.oa_matter_intent_preflight.v1` packet. It does not execute writes,
enqueue extension tasks, or echo the opinion text; `Archive` remains
dry-run-only until separately promoted.
`oa matter execute` is the confirmed business-intent execution entry. For
ordinary received workflows it reruns `matter_preflight`, only proceeds when
the decision is `ready_for_execute`, then delegates to the governed
`write_execute` path. For meeting intents (`join`, `not_join`, `pending`) it
resolves the pending meeting by id, URL, meeting id, or keyword and delegates to
`meeting_reply_execute`. It still requires `--confirm`; the low-level action
code is an implementation detail, not something the agent has to choose.
When promoting new write actions, follow
[`docs/oa-write-action-expansion-playbook.md`](docs/oa-write-action-expansion-playbook.md)
so discovery, confirmation, content-save handling, readback verification, and
live validation stay consistent.

Pending, sent, and template objects:

```bash
python -m bscli.cli.main --home .bscli oa pending list
python -m bscli.cli.main --home .bscli oa pending search --keyword budget --limit 10
python -m bscli.cli.main --home .bscli oa pending show -7317807227272018131
python -m bscli.cli.main --home .bscli oa pending details --limit 10 --include title,text,attachments --text-limit 2000
python -m bscli.cli.main --home .bscli oa pending attachments --limit 20 --format csv --fields source_title,name,href
python -m bscli.cli.main --home .bscli oa pending workflow --limit 20 --format table --fields source_title,text
python -m bscli.cli.main --home .bscli oa pending actions --limit 20 --format table --fields source_title,code,label,risk
python -m bscli.cli.main --home .bscli oa pending export --format csv --fields title,affair_id

python -m bscli.cli.main --home .bscli oa sent list
python -m bscli.cli.main --home .bscli oa sent search --keyword contract
python -m bscli.cli.main --home .bscli oa sent details --limit 10
python -m bscli.cli.main --home .bscli oa sent attachments --limit 20 --format csv --fields source_title,name,href
python -m bscli.cli.main --home .bscli oa sent workflow --limit 20
python -m bscli.cli.main --home .bscli oa sent export --format csv --fields title,sender,affair_id

python -m bscli.cli.main --home .bscli oa template list
python -m bscli.cli.main --home .bscli oa template search --keyword seal
python -m bscli.cli.main --home .bscli oa template list --category 财务审批 --fields title,template_id,form_app_id,category_name
python -m bscli.cli.main --home .bscli oa template show -6511139737225050501
python -m bscli.cli.main --home .bscli oa template match --kind done --limit 50
python -m bscli.cli.main --home .bscli oa template details --limit 10 --include title,fields,attachments
python -m bscli.cli.main --home .bscli oa template attachments --limit 20
python -m bscli.cli.main --home .bscli oa template workflow --limit 20
python -m bscli.cli.main --home .bscli oa template export --format table --fields title,template_id

python -m bscli.cli.main --home .bscli oa launch inspect --template-id <template_id> --settle-ms 0
python -m bscli.cli.main --home .bscli oa launch dry-run --template-id <template_id> --field content_coll="Draft note" --settle-ms 0
python -m bscli.cli.main --home .bscli oa launch save-draft --template-id <template_id> --field content_coll="Draft note" --confirm
```

`oa template match` maps high-frequency historical workflow clusters to
launchable templates with `matched`, `ambiguous`, or `unmatched` status.
`oa launch inspect` opens a template launch page in an inactive Chrome tab and
extracts forms, fields, hidden-field names, buttons, script actions, CSRF
presence, and untested endpoint candidates. It may leave an OA draft, but it
does not click or call submit, approve, archive, delete, revoke, upload, or send
actions. The Chrome bridge treats a launch tab as readable once the DOM can be
scripted, even if Chrome still reports the tab as loading; this avoids false
timeouts on OA pages that keep background resources open. In current live
Seeyon pages, `subject` is often read-only, so use writable fields such as
`content_coll` or `formTextId` for launch dry-runs. If CAP4 dynamic-form text is
visible in the rendered page or same-tab frames, `launch inspect` also reports a
read-only `business_form` profile with the form title, sections, field
candidates, and table-column candidates. These candidates are not merged into
writable `fields`; they are evidence for later workflow-specific write-action
promotion.
`oa launch dry-run` uses the same launch-page inspection path, validates that
the requested field names, ids, or labels are writable, verifies that a
`saveDraft` / "保存待发" control exists, and returns a sanitized
`bscli.oa_launch_draft_plan.v1` plan without filling or clicking anything.
`oa launch save-draft --confirm` is the first promoted launch-page write action:
it validates the target page through the logged-in Chrome bridge, schedules
field filling plus a click on only the save-draft control, and reports
`draft_save_scheduled_ack` with `submitted_count=0`. It refuses `sendId_a`,
`ContinueSubmit`, "发送", and "提交" controls, and writes a redacted audit row
under `.bscli/audit/oa-launch-drafts.jsonl`. It may create or update an OA
draft, but it must not send the workflow.

Page/API discovery:

```bash
python -m bscli.cli.main --home .bscli oa probe install
python -m bscli.cli.main --home .bscli oa probe logs
python -m bscli.cli.main --home .bscli oa probe candidates

python -m bscli.cli.main --home .bscli oa api inspect --method GET --url "http://10.10.50.110/seeyon/ajax.do?..."
python -m bscli.cli.main --home .bscli oa api replay --method GET --url "http://10.10.50.110/seeyon/ajax.do?..."
python -m bscli.cli.main --home .bscli oa api save template-section --method GET --url "http://10.10.50.110/seeyon/ajax.do?..."

python -m bscli.cli.main --home .bscli oa discovered list
python -m bscli.cli.main --home .bscli oa discovered show template-section
python -m bscli.cli.main --home .bscli oa discovered run template-section
```

Collection commands support `--keyword`, `--limit`, `--fields`, and
`--format json|table|csv`, so agents can request either structured JSON or
compact tabular output without needing post-processing glue.
Batch detail commands first read the corresponding list, then call
`detail_read` for each row with an `href`. Use `--include` to choose detail
sections (`title,text,fields,attachments,workflow`) and `--text-limit` to cap
large page text.

Safe write planning is available, and confirmed `ContinueSubmit` writes can run
through the Chrome extension bridge:

```bash
python -m bscli.cli.main --home .bscli oa write actions --format table --fields code,label,action_type,promotion_status,verification_method
python -m bscli.cli.main --home .bscli oa write capabilities --type pending --limit 10 --format table --fields title,category,affair_id,verification_method
python -m bscli.cli.main --home .bscli oa write discover --source history --kind done --limit 20 --deep-limit 5 --format json
python -m bscli.cli.main --home .bscli oa write discover --source launch --template-id <template_id> --format json
python -m bscli.cli.main --home .bscli oa write draft --affair-id <id> --action ContinueSubmit --opinion "agree"
python -m bscli.cli.main --home .bscli oa write dry-run --affair-id <id> --action ContinueSubmit --opinion "agree"
python -m bscli.cli.main --home .bscli oa write preflight --affair-id <id> --action ContinueSubmit --opinion "agree"
python -m bscli.cli.main --home .bscli oa write prepare --affair-id <id> --action ContinueSubmit --opinion "agree" --text-limit 800
python -m bscli.cli.main --home .bscli oa write execute --affair-id <id> --action ContinueSubmit --opinion "agree" --confirm
python -m bscli.cli.main --home .bscli oa write execute --affair-id <id> --action ContinueSubmit --opinion "agree" --business-form-wait-ms 30000 --script-timeout-ms 30000 --after-submit-wait-ms 20000 --confirm
python -m bscli.cli.main --home .bscli oa launch dry-run --template-id <template_id> --field content_coll="Draft note" --settle-ms 0
python -m bscli.cli.main --home .bscli oa launch save-draft --template-id <template_id> --field content_coll="Draft note" --confirm
python -m bscli.cli.main --home .bscli oa audit writes list --limit 10
python -m bscli.cli.main --home .bscli oa audit writes show --index 1
python -m bscli.cli.main --home .bscli oa audit writes search --affair-id <id>
python -m bscli.cli.main --home .bscli oa audit verifications list --limit 10
python -m bscli.cli.main --home .bscli oa write smoke --timeout 60
```

`actions` is the local write-action registry. It is the first place to check
before promoting or adding an action because it centralizes labels, risk,
action type, promotion status, and verification method.
For inform/read-notice pages such as weekly-report notifications, use
`ContinueSubmit` with an explicit `--after-submit-wait-ms` when the page is slow
to disappear from pending. `Archive` / post-processing archive remains
dry-run-only because the OA page may require a document archive destination.
`capabilities` is the read-only inventory command for agents. It reads pending
items and reports each item's `category`, `affair_id`, current state,
`supported_write_actions`, and `verification_method`. Workflow submit actions
use `pending_disappearance` verification. Meeting reply actions use
`meeting_reply_readback` verification because a replied meeting can remain
visible in pending even after `myReply.feedbackFlag` has changed.
`discover` is the read-only sampler for expanding write coverage. With
`--source history`, it uses `oa history list`, opens up to `--deep-limit`
historical detail pages, and aggregates the candidate write actions seen there.
With `--source launch`, it reuses `oa launch inspect` to read a template's new
flow page and collect launch-page action and button candidates. The top-level
`actions` array is the cross-workflow action dictionary; the per-workflow
`items` array is the supporting evidence. Launch-source candidates are always
`execute_allowed=false`; dry-run-only actions such as `Archive` also remain
`execute_allowed=false` until separately promoted.
For workflow items, the action inventory is intentionally split:

- `supported_write_actions`: promoted actions with both dry-run and confirmed
  execute support.
- `unpromoted_write_actions`: real write-like actions found on the page that
  may be dry-run checked but cannot execute yet.
- `discovered_write_actions`: raw page actions, including helper actions such
  as opinion phrases, tracking, or print.

For example, `Archive` / `处理后归档` is reported as `workflow.archive` with
`dry_run_allowed=true` and `execute_allowed=false` until its execution mapping
and post-write verification method are promoted.
When dry-run reads the detail page, unpromoted actions also include
`promotion.evidence`: whether the action is present, which page script exposed
it, safe hidden-field names, CSRF-token presence, and untested endpoint
candidates found in rendered HTML. These are promotion clues only; they are not
used to execute a write.

Endpoint candidates can be classified without calling them:

```bash
python -m bscli.cli.main --home .bscli oa write endpoints --affair-id <id> --action Archive
```

This command reuses the dry-run precheck, classifies rendered-HTML endpoint
candidates as likely action-related, auxiliary, or unknown, and returns
`safe_to_call=false` for every candidate. It does not issue network probes to
write-like URLs.

`draft` is an offline local plan and does not contact the daemon or browser.
`dry-run` is the write precheck: it runs through the daemon, resolves the
pending workflow by `affair_id` when `--source-url` is omitted, reads the
rendered detail page, checks that the requested action is currently available,
and returns a machine-readable report with `checks`, `missing`,
`blocked_reasons`, `suggestions`, `target.source_item`, and `precheck`
metadata. It never dispatches a browser write task, and writes a sanitized audit
row under `.bscli/audit/oa-write-plans.jsonl` with opinion text redacted.
`preflight` is the agent-facing execution gate. It runs the same read-only
precheck as `dry-run`, returns a sanitized `plan`, a `decision.status`
(`ready_for_execute`, `dry_run_only`, or `blocked`), and an
`execution_contract` with the command template that still requires human
confirmation. It never dispatches a browser write task and never probes
write-like endpoint candidates.
`prepare` is the agent task packet command. It combines `workflow_evidence` and
`write_preflight`, then returns the workflow evidence summary, preflight
decision, sanitized plan, and `next_steps` in one response. It is the preferred
read-only step before asking the user to confirm a production write.
`oa launch dry-run` and `oa launch save-draft --confirm` are the launch-page
draft path. They target a new-flow template page rather than an existing pending
workflow item. `dry-run` validates fields and the save-draft control without
mutation. `save-draft` is confirmation-gated and may leave a draft in OA, but it
is guarded to click only the save-draft control and always reports
`submitted_count=0`.
`execute` requires `--confirm`; after confirmation it reruns the same precheck,
dispatches the browser write task only if the target action is available, then
reads the pending list again and records whether the `affair_id` disappeared.
Other write actions remain blocked until they have their own mappings.
The same safe planning capabilities are also registered as agent-callable tools:
`oa__matter_profile`, `oa__matter_inspect`, `oa__launch_dry_run`,
`oa__launch_save_draft`, `oa__write_discover`, `oa__write_draft`,
`oa__write_dry_run`, `oa__write_preflight`, `oa__write_prepare`,
`oa__write_execute`, `oa__matter_execute`, and `oa__pending_submit`;
executable tools require a `confirm` argument in their schema before they can
perform a write. Agents can also call `oa__matter_profile` first to choose a
business matter type, then use `oa__matter_preflight` or confirmed
`oa__matter_execute` for received matters.
`oa audit writes list` and `oa audit verifications list` summarize local audit
rows while keeping opinion text redacted; newest rows are shown first. Use
`show --index N` to inspect one sanitized audit record and `search` to filter by
`affair_id`, action, or status.
`oa write smoke` is the fixed live safety check for write-action development.
It first reads pending items and refuses to continue if the default no-match
keyword is present. Only after proving zero matches does it call the confirmed
batch-submit path, which must return `target_count=0` and `submitted_count=0`.

For repeated pending items, use the governed batch submit command. It runs in
the daemon execution layer, so CLI and agent tools share the same confirmation
gate, action check, post-submit verification, and audit trail. It submits one
item at a time and verifies that each `affair_id` disappears from pending before
continuing:

```bash
python -m bscli.cli.main --home .bscli oa pending submit --keyword "weekly report" --action ContinueSubmit --opinion "read" --limit 3 --confirm
```

If a submitted item is still present after verification, the command stops and
does not attempt later items. Verification audit rows are written to
`.bscli/audit/oa-write-verifications.jsonl` without storing the opinion text.

Meeting replies have their own governed command because Seeyon exposes them
through the `meetingAjaxManager` API rather than the collaboration page submit
workflow:

```bash
python -m bscli.cli.main --home .bscli oa meeting create inspect --settle-ms 3000
python -m bscli.cli.main --home .bscli oa meeting create dry-run --field title="Planning" --field mtTitle="Project sync" --settle-ms 3000
python -m bscli.cli.main --home .bscli oa meeting create execute --subject "Planning" --room "3" --start "2026-07-03 16:00" --end "2026-07-03 17:00" --confirm
python -m bscli.cli.main --home .bscli oa meeting reply dry-run --id <pending_affair_id> --attitude join
python -m bscli.cli.main --home .bscli oa meeting reply execute --id <pending_affair_id> --attitude join --confirm
```

`meeting create inspect` and `meeting create dry-run` are thin, read-only
wrappers around the fixed OA meeting editor URL. They validate the editor page
and its writable fields, but they do not fill, save, or send a meeting.
`meeting create execute` is the promoted meeting-launch path. It requires
`--confirm`, reads `meetingInfo`, checks the requested room with `roomListInfo`
and `validateRoomApps`, saves the standard body through
`/seeyon/content/content.do?method=saveOrUpdate`, then sends the meeting through
`meetingAjaxManager.send`. Verification reads the room schedule and, for live
checks, should also read `meetingView` or the view page to ensure the title and
body render without the Seeyon "body count" error.

Meeting replies use a separate governed command. The execute form requires
`--confirm`, posts the reply through the logged-in Chrome bridge, then reads
`meetingView` again and succeeds only when `myReply.feedbackFlag` matches the
requested attitude. The agent-facing tool names are
`oa__meeting_reply_dry_run` and `oa__meeting_reply_execute`; the execute tool
requires `confirm` in its input schema.
Direct meeting creation is also registered as agent-facing tools:
`oa__meeting_create_inspect`, `oa__meeting_create_dry_run`, and
`oa__meeting_create_execute`. The execute tool requires `confirm` and follows
the same promoted backend path as the CLI command.

Read the structured pending list from the OA home page:

```bash
python -m bscli.cli.main --home .bscli command run oa pending_list --timeout 30
```

Read the structured pending list through the discovered `sectionManager`
backend API, replayed inside the logged-in page context:

```bash
python -m bscli.cli.main --home .bscli command run oa pending_list_api --timeout 30
```

Read the structured sent-list section through the discovered `sectionManager`
backend API:

```bash
python -m bscli.cli.main --home .bscli command run oa sent_list_api --timeout 30
```

Read one pending item from the OA home page by `affair_id`:

```bash
python -m bscli.cli.main --home .bscli command run oa pending_detail --timeout 30 --json "{\"affair_id\":\"-7317807227272018131\"}"
```

Read form templates from the OA home page without opening new forms:

```bash
python -m bscli.cli.main --home .bscli command run oa template_list --timeout 30
```

Read form templates through the template center REST API:

```bash
python -m bscli.cli.main --home .bscli command run oa template_list_api --timeout 30
python -m bscli.cli.main --home .bscli command run oa template_list_api --timeout 30 --json "{\"category\":\"财务审批\",\"keyword\":\"差旅\",\"limit\":5}"
```

Read one form template metadata record by `template_id`:

```bash
python -m bscli.cli.main --home .bscli command run oa template_detail --timeout 30 --json "{\"template_id\":\"-6511139737225050501\"}"
```

The DOM-oriented home-page commands ask the extension for a raw HTML snapshot
and parse it in the Python daemon. `template_list_api` instead performs a
logged-in page-context fetch against the template center REST endpoint, which
returns stable template metadata such as `template_id`, `form_app_id`,
`category_name`, `module_type`, and `body_type`.

Parse a saved OA home-page HTML fragment offline:

```bash
python -m bscli.cli.main adapter parse-seeyon-home --kind navigation --html-file home.html
python -m bscli.cli.main adapter parse-seeyon-home --kind pending --html-file home.html
python -m bscli.cli.main adapter parse-seeyon-home --kind templates --html-file home.html
```

Install a network probe before performing an OA action in the page:

```bash
python -m bscli.cli.main --home .bscli command run oa network_probe_install --timeout 30
```

After clicking or submitting something in OA, read captured fetch/XHR records:

```bash
python -m bscli.cli.main --home .bscli command run oa network_log_snapshot --timeout 30
```

Or ask BSCLI to summarize captured records into backend API candidates:

```bash
python -m bscli.cli.main --home .bscli command run oa network_api_candidates --timeout 30
```

Replay a candidate API in the logged-in page context:

```bash
python -m bscli.cli.main --home .bscli command run oa api_replay --timeout 30 --json "{\"method\":\"POST\",\"url\":\"/seeyon/rest/pending/list\",\"headers\":{\"content-type\":\"application/json\"},\"body\":\"{\\\"page\\\":1}\"}"
```

Raw API replay, inspect, and save commands are limited to URLs that resolve to
the target system profile's `allowed_origins`. Relative paths such as
`/seeyon/rest/...` resolve against the system `base_url`; absolute URLs outside
the system origin are rejected before any browser task is queued.

Inspect a candidate API response shape without saving it:

```bash
python -m bscli.cli.main --home .bscli command run oa api_inspect --timeout 30 --json "{\"method\":\"GET\",\"url\":\"http://10.10.50.110/seeyon/ajax.do?...\"}"
```

Save a verified candidate API as local discovered metadata:

```bash
python -m bscli.cli.main --home .bscli command run oa api_save --timeout 30 --json "{\"name\":\"template-section\",\"method\":\"GET\",\"url\":\"http://10.10.50.110/seeyon/ajax.do?...\",\"description\":\"Template section projection\"}"
```

Saved API metadata is written under `.bscli/discovered/<system>/apis/`.

List saved discovered APIs:

```bash
python -m bscli.cli.main --home .bscli discovered list oa
```

Show one saved discovered API metadata record:

```bash
python -m bscli.cli.main --home .bscli discovered show oa template-section
```

Run a saved discovered API through the logged-in browser page context:

```bash
python -m bscli.cli.main --home .bscli discovered run oa template-section --timeout 30
```

Discovered APIs may declare parameter schemas in their saved metadata:

```json
{
  "parameters": {
    "keyword": {"type": "string", "required": true},
    "page": {"type": "integer"}
  },
  "request": {
    "method": "GET",
    "url": "http://10.10.50.110/seeyon/ajax.do?q={{keyword}}&page={{page}}"
  }
}
```

Run a parameterized discovered API with:

```bash
python -m bscli.cli.main --home .bscli discovered run oa search --json "{\"keyword\":\"budget\",\"page\":1}" --timeout 30
```

The same parameter schema is exported to MCP tools, so
`oa__discovered__search` receives `keyword` and `page` as normal tool
arguments instead of requiring a fixed replay request.

Low-risk `GET` APIs run without extra confirmation when their URL origin
matches the target system profile. Non-GET, non-read, or higher-risk APIs are
blocked before a task is delivered to the browser unless the caller explicitly
confirms the run:

```bash
python -m bscli.cli.main --home .bscli discovered run oa submit --confirm --timeout 30
```

Confirmation never bypasses the system origin allowlist. Cross-origin saved
APIs are still rejected before browser execution.

Discovered APIs are also exported in the tool manifest as dynamic tools, for
example `oa__discovered__template_section`.

The extension polls the daemon and submits task results back to:

```text
GET http://127.0.0.1:8765/extension/results/<task-id>
```

The legacy bridge remains Python-standard-library first: `argparse` for the
CLI, `http.server` for the local daemon, and Chrome extension polling for
browser tasks. The central AgentBridge slice adds pinned Playwright for managed
browser sessions; it does not introduce FastAPI/Starlette, WebSocket/Native
Messaging, Typer, uv, or hatch.

## Current Scope

Implemented:

- Versioned central `CapabilitySpec` registry and JSON CLI discovery
- Durable central operation ledger with idempotency conflict detection
- Per-user central session registry and principal mismatch quarantine
- DPAPI-encrypted central browser session-state store on Windows
- Central Playwright persistent profiles, origin allowlist, and profile lease
- Six extension-independent central OA read capabilities for templates,
  workflow lists, rendered details, and opinions
- Central `oa.business_trip.prepare` and `oa.business_trip.save_draft`
- One-time write authorization store and trusted action-card confirmation
- Wait-send reload and exact field readback for business-trip drafts
- MCP `oa:write:draft` scope enforcement
- CLI `capability list/describe/invoke`
- CLI `session status/login`
- CLI `operation get/list`
- System profile storage
- Seeyon OA profile initialization
- Command registry
- Seeyon OA home-page parser
- SQLite trace store
- Minimal runtime for `daemon_api`
- Extension task bridge
- Extension completed-task TTL cleanup
- Local daemon endpoints
- Local daemon Host/Origin guard and token-protected command endpoints
- Chrome extension DOM snapshot task
- Chrome extension HTML snapshot task
- Chrome extension rendered detail snapshot task
- Profile-based browser tab routing for multi-tab / multi-system use
- Daemon-side Seeyon OA home-page parsing
- API response shape inspection
- Local discovered API metadata store
- Dynamic discovered API runtime
- Command execution trace records with `run_id`
- Discovered API origin policy checks and confirmation gate
- Raw API replay/inspect/save origin checks
- CLI `command run oa current_page_snapshot`
- CLI `command run oa detail_read`
- CLI `command run oa api_inspect`
- CLI `command run oa api_replay`
- CLI `command run oa api_save`
- CLI `command run oa navigation_inventory`
- CLI `command run oa page_inventory`
- CLI `command run oa network_probe_install`
- CLI `command run oa network_log_snapshot`
- CLI `command run oa network_api_candidates`
- CLI `command run oa pending_detail`
- CLI `command run oa pending_list`
- CLI `command run oa pending_list_api`
- CLI `command run oa sent_list_api`
- CLI `command run oa session_status`
- CLI `command run oa template_detail`
- CLI `command run oa template_list`
- CLI `command run oa template_list_api`
- CLI `discovered list`
- CLI `discovered show`
- CLI `discovered run`
- CLI `adapter parse-seeyon-home`
- CLI `command list oa`
- CLI `tool manifest oa`
- CLI `mcp serve`
- Business CLI `oa doctor`
- Business CLI `oa capabilities`
- Business CLI `oa detail read`
- Business CLI `oa detail attachments/workflow`
- Business CLI `oa inbox analyze`
- Business CLI `oa workflow list/search/brief/inspect/evidence/timeline/detail/opinions/attachments/actions`
- Business CLI `oa history sections/list/search/profile/clusters/export`
- Business CLI `oa matter profile/matrix/inspect/preflight`
- Business CLI `oa template match`
- Business CLI `oa launch inspect`
- Business CLI `oa write actions`
- Business CLI `oa write discover`
- Business CLI `oa write preflight`
- Business CLI `oa write prepare`
- Business CLI `oa write smoke`
- Business CLI `oa audit writes/verifications list/show/search`
- Business CLI `oa pending/sent/template list/search/show/export`
- Business CLI `oa pending/sent/template details/attachments/workflow`
- Business CLI `oa probe/api/discovered ...`
- Governed write CLI `oa write execute`
- Governed batch write CLI `oa pending submit`
- MCP write tools `oa__write_execute` and `oa__pending_submit`

Not implemented yet:

- UI workflow recording
- Central workflow writes beyond the business-trip save-draft sample
- Central business-trip send/submit

## Tests

Run all tests:

```bash
python -m unittest discover
```
