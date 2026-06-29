# OA Write Discovery Notes

Date: 2026-06-24

Scope: read-only exploration through the Chrome extension bridge. No OA submit,
approve, reject, archive, revoke, delete, upload, or send endpoint was invoked.

## Current Discovery Sources

Rendered OA collaboration detail pages contain a JavaScript action array named
`jsonArrBase`. BSCLI parses this array from the rendered HTML snapshot and
exposes each action as:

- `code`
- `label`
- `id`
- `access=write`
- `risk`
- `requires_confirmation=true`
- `supports_dry_run=true`
- `source=jsonArrBase`

The parser also reports write hints without leaking values:

- `CSRFTOKEN` presence
- hidden input names and whether each has a value
- candidate `.do?method=...` endpoints found in rendered HTML, marked
  `method=UNKNOWN`, `risk=high`, and `tested=false`

The write-discovery pipeline now has three read-only/draft-level layers.

Layer 1, high-frequency history profiling:

```powershell
python -m bscli.cli.main --home .bscli oa history sections
python -m bscli.cli.main --home .bscli oa history list --kind done --limit 20
python -m bscli.cli.main --home .bscli oa history profile --kind done --limit 50
python -m bscli.cli.main --home .bscli oa history clusters --kind all --limit 20
```

`oa history profile` clusters sent/done/tracked workflow rows by title pattern,
category, status, date range, `affair_id`, and `href`. This is the frequency
map used to decide which business processes deserve write-action expansion
first.

Layer 2, template matching:

```powershell
python -m bscli.cli.main --home .bscli oa template match --kind done --limit 50
python -m bscli.cli.main --home .bscli oa matter profile --kind all --limit 50
python -m bscli.cli.main --home .bscli oa matter inspect --id <matter_id>
python -m bscli.cli.main --home .bscli oa matter inspect --id <matter_id> --with-launch
python -m bscli.cli.main --home .bscli oa matter preflight --keyword <pending_keyword> --intent approve
python -m bscli.cli.main --home .bscli oa matter preflight --id <pending_affair_id> --intent archive
```

`oa template match` compares historical clusters with the launchable template
list and returns candidates with score and evidence. It reports `matched`,
`ambiguous`, or `unmatched`; ambiguous clusters are not guessed into a concrete
template.
`oa matter profile` packages this into a business matter catalog: each matter
has a stable `matter_id`, historical samples, template match state, and the
atomic write/read commands that are safe to consider next. `oa matter inspect`
reads one matter entry; it opens the launch page only when `--with-launch` is
passed.
`oa matter preflight` is the received-pending business-intent bridge. It keeps
agent-facing commands at the matter/intent level (`approve`, `archive`) while
reporting the internal binding (`ContinueSubmit`, `Archive`) for governance and
debugging. It is read-only and does not promote any action by itself.

Layer 3, launch-page inspection and write discovery:

```powershell
python -m bscli.cli.main --home .bscli oa launch inspect --template-id <template_id> --settle-ms 0
python -m bscli.cli.main --home .bscli oa launch dry-run --template-id <template_id> --field content_coll="Draft note" --settle-ms 0
python -m bscli.cli.main --home .bscli oa launch save-draft --template-id <template_id> --field content_coll="Draft note" --confirm
python -m bscli.cli.main --home .bscli oa meeting create inspect --settle-ms 3000
python -m bscli.cli.main --home .bscli oa meeting create dry-run --field title="Planning" --field mtTitle="Project sync" --settle-ms 3000
python -m bscli.cli.main --home .bscli oa write discover --source history --kind done --limit 20 --deep-limit 5
python -m bscli.cli.main --home .bscli oa write discover --source launch --template-id <template_id>
```

`oa history list` discovers the sent/done/tracked tab ids from the rendered home
page and replays the `sentSection` projection API with the selected `panelId`.
It does not click the browser UI. `oa write discover` then opens a bounded
number of historical detail pages and aggregates the candidate actions found
there. The result is evidence for future promotion work only; it does not make a
historical action executable.

`oa launch inspect` opens a template launch page in an inactive Chrome tab and
extracts forms, normal fields, hidden-field names, buttons, `jsonArrBase`
actions, CSRF-token presence, and untested endpoint candidates. Opening a launch
page may create or retain an OA draft, which is allowed for this discovery
phase. It does not click submit/send/approve/reject/archive/delete/revoke/upload
controls and does not call suspected write endpoints. `oa write discover
--source launch` aggregates those launch-page candidates, but every candidate is
forced to `execute_allowed=false` until a separate user-confirmed execution plan
exists. The bridge waits for the page to become script-readable rather than
requiring Chrome's tab status to reach `complete`, because Seeyon launch pages
can keep background resources loading after the usable DOM is available. In
current live templates, the visible `subject` field is read-only; prefer
`content_coll` or `formTextId` for low-risk field dry-runs. Rendered snapshots
also collect same-tab frame HTML and merge it into launch-page parsing, because
some Seeyon business forms render their real fields inside frames or embedded
dynamic form surfaces.

First launch-side expansion batch:

- `【用印】用印申请单`: template id `-6511139737225050501`; outer launch
  shell supports `content_coll` dry-run. Business form field extraction still
  needs frame/dynamic-form verification.
- `【报销】差旅费审批报销单`: template id `-2046021869351779722`; outer launch
  shell matches the same collaboration save-draft pattern. A 4-second rendered
  wait still showed only shell fields, so this is the primary target for the
  new frame-aware snapshot path.
- `新建会议`: navigation URL
  `/seeyon/meeting.do?method=editor&showTab=true`; not a template-center item.
  `oa meeting create inspect` reads meeting fields such as `title`, `mtTitle`,
  `meetingTime`, `conferees`, `leader`, and `tel`. `oa meeting create dry-run`
  validates fields and the `save_a` / "保存待发" control without filling or
  clicking anything.

The first promoted launch-page execution plan is save draft. `oa launch dry-run`
validates requested fields and the `saveDraft` / "保存待发" control without
mutation. `oa launch save-draft --confirm` schedules field filling plus a
save-draft click through the extension; it refuses send/submit controls and must
return `draft_save_scheduled_ack` with `submitted_count=0`.

## Live Read-Only Findings

Command used:

```powershell
python -m bscli.cli.main --home .bscli oa pending actions --limit 20 --format csv --fields source_title,code,label,risk,source --timeout 90
```

Observed candidate action codes:

| Code | Risk | Source | Notes |
| --- | --- | --- | --- |
| `Comment` | medium | `jsonArrBase` | Save/comment style action. |
| `ContinueSubmit` | high | `jsonArrBase` | Likely submit/continue processing action. |
| `Opinion` | medium | `jsonArrBase` | Opinion text UI action. |
| `CommonPhrase` | medium | `jsonArrBase` | Common phrase helper. |
| `UploadAttachment` | high | `jsonArrBase` | File upload changes business data. |
| `Archive` | high | `jsonArrBase` | Archive-after-processing style action. |
| `Track` | medium | `jsonArrBase` | Tracking action observed on one pending item. |
| `Print` | medium | `jsonArrBase` | Print action observed on weekly-report items. |

Observed pending-detail titles included two weekly report items and one data
industry expert-pool notice. The discovery command only parsed detail pages and
did not call any write endpoint.

Additional read-only context check:

```powershell
python -m bscli.cli.main --home .bscli oa pending details --limit 1 --include title,write_hints --timeout 90
```

For the weekly-report detail page, BSCLI observed:

- `CSRFTOKEN` exists, value not stored.
- Hidden fields include write-relevant names such as `affairId`, `summaryId`,
  `processId`, `caseId`, `subObjectId`, and `currentNodeId`; only
  `value_present` is recorded.
- One rendered-HTML endpoint candidate remained after filtering read-page
  false positives:
  `/seeyon/supervise/supervise.do?method=saveOrUpdateSupervise`.

This endpoint candidate was not replayed or called. It is marked high risk,
`method=UNKNOWN`, and `tested=false`.

## Endpoint Status

No production write endpoint has been promoted. The safe plan builder therefore
uses:

- `request.status=not_built` for `draft`
- `request.status=not_sent` for `dry-run`
- `request.status=blocked` for `execute`

Dry-run plans now include a local `request.payload_preview` so humans and
agents can inspect the intended `affairId`, `actionCode`, `opinionText`, and
`sourceUrl` without sending it. Audit rows redact the opinion text from that
preview.

The next discovery step is to install the network probe, manually perform a
non-destructive page interaction only when safe, and inspect captured requests.
Any interaction that might submit, approve, reject, archive, delete, revoke,
send, or upload must wait for explicit user confirmation.
