# BSCLI

BSCLI is a Python-first adapter platform for turning existing B/S system capabilities into CLI commands and agent-callable tools.

This repository currently contains the first runnable skeleton:

- Python CLI
- Python local daemon
- Chrome extension bridge
- Command registry
- Trace store
- Seeyon OA example profile for `http://10.10.50.110/seeyon/main.do?method=main`

## Quick Start

Initialize the Seeyon OA profile:

```bash
python -m bscli.cli.main --home .bscli system init-seeyon-oa
```

Start the local daemon:

```bash
python -m bscli.cli.main --home .bscli daemon serve --host 127.0.0.1 --port 8765
```

Load the Chrome extension:

1. Open `chrome://extensions`.
2. Enable Developer mode.
3. Choose "Load unpacked".
4. Select `D:\Codes\CLIExp\extension`.

After changing files under `extension/`, click "Reload" for the unpacked
extension so the background service worker picks up the new task handlers.

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
on one of that profile's allowed origins.

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
python -m bscli.cli.main --home .bscli oa history export --kind sent --format csv --fields title,status,date,affair_id
```

`oa history` reads historical tabs such as sent, done, and tracked from the OA
home page. It discovers tab ids from `navigation_inventory`, then replays the
same `sentSection` backend projection API with the selected `panelId`. This
avoids clicking tabs in the browser while still using the user's real logged-in
session. Historical detail reads are treated as read-only samples and report
`read_effect.may_mark_read=false`.

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
python -m bscli.cli.main --home .bscli oa template show -6511139737225050501
python -m bscli.cli.main --home .bscli oa template details --limit 10 --include title,fields,attachments
python -m bscli.cli.main --home .bscli oa template attachments --limit 20
python -m bscli.cli.main --home .bscli oa template workflow --limit 20
python -m bscli.cli.main --home .bscli oa template export --format table --fields title,template_id
```

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
python -m bscli.cli.main --home .bscli oa write draft --affair-id <id> --action ContinueSubmit --opinion "agree"
python -m bscli.cli.main --home .bscli oa write dry-run --affair-id <id> --action ContinueSubmit --opinion "agree"
python -m bscli.cli.main --home .bscli oa write preflight --affair-id <id> --action ContinueSubmit --opinion "agree"
python -m bscli.cli.main --home .bscli oa write prepare --affair-id <id> --action ContinueSubmit --opinion "agree" --text-limit 800
python -m bscli.cli.main --home .bscli oa write execute --affair-id <id> --action ContinueSubmit --opinion "agree" --confirm
python -m bscli.cli.main --home .bscli oa audit writes list --limit 10
python -m bscli.cli.main --home .bscli oa audit writes show --index 1
python -m bscli.cli.main --home .bscli oa audit writes search --affair-id <id>
python -m bscli.cli.main --home .bscli oa audit verifications list --limit 10
python -m bscli.cli.main --home .bscli oa write smoke --timeout 60
```

`actions` is the local write-action registry. It is the first place to check
before promoting or adding an action because it centralizes labels, risk,
action type, promotion status, and verification method.
`capabilities` is the read-only inventory command for agents. It reads pending
items and reports each item's `category`, `affair_id`, current state,
`supported_write_actions`, and `verification_method`. Workflow submit actions
use `pending_disappearance` verification. Meeting reply actions use
`meeting_reply_readback` verification because a replied meeting can remain
visible in pending even after `myReply.feedbackFlag` has changed.
`discover` is the read-only history sampler for expanding write coverage. It
uses `oa history list`, opens up to `--deep-limit` historical detail pages, and
aggregates the candidate write actions seen there. The top-level `actions`
array is the cross-workflow action dictionary; the per-workflow `items` array is
the supporting evidence. Dry-run-only actions such as `Archive` remain
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
`execute` requires `--confirm`; after confirmation it reruns the same precheck,
dispatches the browser write task only if the target action is available, then
reads the pending list again and records whether the `affair_id` disappeared.
Other write actions remain blocked until they have their own mappings.
The same safe planning capabilities are also registered as agent-callable tools:
`oa__write_discover`, `oa__write_draft`, `oa__write_dry_run`, `oa__write_preflight`,
`oa__write_prepare`, `oa__write_execute`, and
`oa__pending_submit`; executable tools require a `confirm` argument in their
schema before they can perform a production write. Agents can also call
`oa__write_capabilities` first to decide which write command is applicable.
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
python -m bscli.cli.main --home .bscli oa meeting reply dry-run --id <pending_affair_id> --attitude join
python -m bscli.cli.main --home .bscli oa meeting reply execute --id <pending_affair_id> --attitude join --confirm
```

The execute form requires `--confirm`, posts the reply through the logged-in
Chrome bridge, then reads `meetingView` again and succeeds only when
`myReply.feedbackFlag` matches the requested attitude. The agent-facing tool
names are `oa__meeting_reply_dry_run` and `oa__meeting_reply_execute`; the
execute tool requires `confirm` in its input schema.

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

Read form templates through the discovered `sectionManager` backend API:

```bash
python -m bscli.cli.main --home .bscli command run oa template_list_api --timeout 30
```

Read one form template metadata record by `template_id`:

```bash
python -m bscli.cli.main --home .bscli command run oa template_detail --timeout 30 --json "{\"template_id\":\"-6511139737225050501\"}"
```

These home-page commands ask the extension for a raw HTML snapshot and parse it in
the Python daemon. That keeps Seeyon business extraction logic reusable for
extension, Playwright, saved-page, and future API-replay paths.

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

## Current Scope

Implemented:

- System profile storage
- Seeyon OA profile initialization
- Command registry
- Seeyon OA home-page parser
- SQLite trace store
- Minimal runtime for `daemon_api`
- Extension task bridge
- Local daemon endpoints
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
- Business CLI `oa history sections/list/search/export`
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
- Seeyon OA write actions beyond confirmed `ContinueSubmit`

## Tests

Run all tests:

```bash
python -m unittest discover
```
