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
python -m bscli.cli.main --home .bscli oa page snapshot
python -m bscli.cli.main --home .bscli oa page inventory
python -m bscli.cli.main --home .bscli oa nav list
```

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
python -m bscli.cli.main --home .bscli oa write draft --affair-id <id> --action ContinueSubmit --opinion "agree"
python -m bscli.cli.main --home .bscli oa write dry-run --affair-id <id> --action ContinueSubmit --opinion "agree"
python -m bscli.cli.main --home .bscli oa write execute --affair-id <id> --action ContinueSubmit --opinion "agree" --confirm
```

`draft` and `dry-run` never contact the daemon or browser. They include a local
`request.payload_preview` for review. `dry-run` writes a sanitized audit row
under `.bscli/audit/oa-write-plans.jsonl` with opinion text redacted. `execute`
requires `--confirm`, dispatches a browser task, verifies the detail-page
`affairId`, fills the opinion, and invokes the page's own Seeyon submit
function. Other write actions remain blocked until they have their own mappings.
The same safe planning capabilities are also registered as agent-callable tools:
`oa__write_draft`, `oa__write_dry_run`, and `oa__write_execute`; the execute
tool requires a `confirm` argument in its schema before it can perform a
production write.

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
- Business CLI `oa detail read`
- Business CLI `oa detail attachments/workflow`
- Business CLI `oa pending/sent/template list/search/show/export`
- Business CLI `oa pending/sent/template details/attachments/workflow`
- Business CLI `oa probe/api/discovered ...`

Not implemented yet:

- UI workflow recording
- Real Seeyon OA business command adapters beyond read-only home-page exploration

## Tests

Run all tests:

```bash
python -m unittest discover
```
