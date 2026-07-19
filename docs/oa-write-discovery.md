# OA Write Discovery Notes

> Historical discovery evidence. The Chrome extension and localhost daemon are
> no longer runnable. Reuse only the observed contracts and promote them into a
> controlled central inspector or workflow-specific capability.

Date: 2026-06-24

Scope: historical read-only evidence captured through the former browser bridge
and promotion into workflow-specific central adapters. No OA submit, approve,
reject, archive, revoke, delete, upload, or send endpoint was invoked during
discovery.

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
python -m bscli.cli.main --home .bscli oa matter launch-dry-run --name <matter_name> --field content_coll="Draft note"
python -m bscli.cli.main --home .bscli oa matter launch-save-draft --id <matter_id> --field content_coll="Draft note" --confirm
python -m bscli.cli.main --home .bscli oa matter preflight --keyword <pending_keyword> --intent approve
python -m bscli.cli.main --home .bscli oa matter preflight --id <pending_affair_id> --intent archive
python -m bscli.cli.main --home .bscli oa matter execute --keyword <pending_keyword> --intent approve --opinion "read" --confirm
python -m bscli.cli.main --home .bscli oa matter execute --keyword <meeting_keyword> --intent join --feedback "will attend" --confirm
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
The catalog also carries a first-batch target seed for matters that should be
promoted even when recent history is sparse: `【用印】用印申请单`,
`【HR】补签申请单`, `【HR】出差申请单`, and `新建会议`. The first three resolve
through template-center metadata; `新建会议` resolves through the fixed OA
meeting editor URL. `oa matter launch-dry-run` and
`oa matter launch-save-draft` keep agents at the matter layer, then delegate to
the existing launch dry-run/save-draft engine with either a template id or fixed
URL. They are wrappers around the existing governed launch workflow, not a new
write executor.
`oa matter preflight` is the received-pending business-intent bridge. It keeps
agent-facing commands at the matter/intent level (`approve`, `archive`) while
reporting the internal binding (`ContinueSubmit`, `Archive`) for governance and
debugging. It is read-only and does not promote any action by itself.
`oa matter execute` is the confirmed counterpart. It keeps the same
matter/intent surface, then routes ordinary approvals through the governed
`write_execute` implementation and meeting replies through
`meeting_reply_execute`. Keyword resolution defaults to one pending item so a
single business command does not silently become a batch operation.
`matter-missed-punch-request` is now the first formal received-workflow sample:
the user-facing intent is `approve`, the internal binding remains
`ContinueSubmit`, the default opinion is `同意`, and success is verified by the
pending item disappearing. The validated sample did not require extra business
form prefill; future samples must update the profile if the iframe exposes
required fields.

`matter-business-trip-request` is now the first formal launch-side workflow
sample. Its launch profile records the business intent as starting a business
trip request, binds execution to the governed `matter_launch_save_draft` route,
uses `content_coll` as the required/default low-risk draft field, and verifies
the promoted write by the launch draft acknowledgement. This sample is limited
to inspect, dry-run, and save-draft; it does not submit the business trip
workflow. Weekly report sending remains a received-side/system-generated
sample because the user cannot normally start that workflow from templates.

Layer 3, launch-page inspection and write discovery:

```powershell
python -m bscli.cli.main --home .bscli oa launch inspect --template-id <template_id> --settle-ms 0
python -m bscli.cli.main --home .bscli oa launch dry-run --template-id <template_id> --field content_coll="Draft note" --settle-ms 0
python -m bscli.cli.main --home .bscli oa launch save-draft --template-id <template_id> --field content_coll="Draft note" --confirm
python -m bscli.cli.main --home .bscli oa meeting create inspect --settle-ms 3000
python -m bscli.cli.main --home .bscli oa meeting create dry-run --field title="Planning" --field mtTitle="Project sync" --settle-ms 3000
python -m bscli.cli.main --home .bscli oa meeting create execute --subject "Planning" --room "3" --start "2026-07-03 16:00" --end "2026-07-03 17:00" --confirm
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
dynamic form surfaces. When CAP4 dynamic-form text is visible, `launch inspect`
also emits a read-only `business_form` profile with the detected title,
sections, field candidates, and table-column candidates. These candidates are
semantic evidence only: they are not merged into writable `fields` and cannot be
used by save-draft/write commands until a workflow-specific promotion validates
the underlying DOM/API behavior.

First launch-side expansion batch:

- `【用印】用印申请单`: template id `-6511139737225050501`; outer launch
  shell supports `content_coll` dry-run. Business form field extraction still
  needs frame/dynamic-form verification.
- `【HR】出差申请单`: template id `2668910351205287097`; registered as the
  first launch-side matter sample. Central exploration additionally confirmed
  CAP4 form app id `4948077657800057670` and the following stable business
  contract: `field0006` start time, `field0007` end time, `field0027` travel
  mode, `field0023` origin, `field0026` destination, `field0029` days,
  `field0022` hours, `field0009` reason, and `field0010` direct-supervisor
  choice. The promoted central sequence is:

  ```powershell
  python -m bscli.cli.main --home .bscli capability invoke oa.business_trip.prepare --user-subject <user> --card-base-url http://127.0.0.1:8780 --idempotency-key <input-key> --json '{}'
  # User fills the returned trusted field card; the agent receives no business values.
  python -m bscli.cli.main --home .bscli capability invoke oa.business_trip.prepare --user-subject <user> --card-base-url http://127.0.0.1:8780 --idempotency-key <prepare-key> --json '{"input_submission_id":"<input-submission-id>"}'
  # User approves the separately returned trusted action card.
  python -m bscli.cli.main --home .bscli capability invoke oa.business_trip.save_draft --user-subject <user> --idempotency-key <save-key> --json '{"authorization_id":"<authorization-id>"}'
  ```

  This draft path uses the managed central browser session and reports
  `browser_bridge_used=false`. The live template keeps the outer `content_coll`
  note control hidden, so the trusted field schema does not expose it. Formal
  submission is now a separate controlled pair:
  `oa.business_trip.submit.prepare` plus `oa.business_trip.submit`. It requires
  a new field submission, a separate submit authorization, the independent
  `oa:write:submit` scope, and adapter-internal sent-item/detail readback. A draft
  authorization cannot be reused, and the internal sent collection is not
  exposed through public collection inputs. The legacy `oa matter launch-*`
  sequence remains a migration oracle, not the target production protocol.

### Current Central Expansion (2026-07-19)

The current registry contains 20 OA capabilities: six reads and fourteen
workflow-stage write capabilities. The central MCP surface contains 27 tools
including session, operation, interaction, and profile tools.

- `【HR】请假申请单` resolves to template id `-7765568933726502821` and
  CAP4 form app id `6773919591095560889`. The reusable central-session probe
  confirmed `field0008` leave type, `field0006` start, `field0007` end,
  OA-computed `field0022` days, OA-computed `field0023` hours, `field0009`
  reason, and `field0010` direct supervisor.
- Live options are `年休`, `婚假`, `事假`, `调休`, `病假`, `陪产假`, `丧假`,
  `产假`, `工伤`, `学习`, `出差`, `外出`, `补签`, `育儿假`, and
  `父母护理假`. The first promoted draft contract deliberately accepts only
  attachment-free `年休`, `事假`, and `调休`; all other options fail before
  browser work until their conditional fields and attachment rules are known.
- `oa.leave.prepare` collects values through a trusted field card, settles known
  OA notice overlays, validates and reads the live form, then freezes a separate
  draft authorization without saving. `oa.leave.save_draft` clicks only
  `#saveDraft_a`, refuses `#sendId_a`, reloads the wait-send item, and requires
  stable summary/affair identifiers plus exact requested-field readback.
  Calculated days/hours are advisory because the real OA left both blank after a
  durable save.
- The 2026-07-19 `RESULT_UNKNOWN` was reconciled read-only through the OA
  `listWaitSend` page. A matching 11:36 leave draft existed, so no retry was
  attempted. `oa.leave.submit.prepare` / `oa.leave.submit` now form a separate
  `oa:write:submit` path with a new field submission, authorization, sent
  baseline, pre-send revalidation and sent-detail readback.
- Prepare tools for business trip, leave, missed punch and approval now accept
  schema-validated values already supplied in conversation as editable card
  defaults. The trusted-card submission remains authoritative and its completed
  values stay out of model-visible interaction metadata.
- `scripts/inspect_oa_template_contract.py` is the reusable read-only contract
  probe. `scripts/validate_oa_write_preflight.py` is the fail-closed real-session
  preflight: it blocks collaboration POSTs, records any save/send control click,
  and covers business-trip submit, leave draft, and leave submit prepare paths.

### Previous Central Expansion (2026-07-18)

The second central expansion promotes two additional workflow families without
restoring the retired bridge:

- `【HR】补签申请单` launch uses template id `-8494358180075582561` and
  CAP4 form app id `-3950641196724501449`. A read-only central-session probe
  confirmed `field0007` start time, `field0008` end time, calculated
  `field0009` duration, `field0010` location, `field0011` reason, and
  `field0012` explanation. The exact reason options are `忘记打卡`,
  `人脸识别有误`, and `其他`. `oa.missed_punch.prepare` collects values through
  a trusted field card and freezes the live form contract;
  `oa.missed_punch.save_draft` consumes a separate authorization, clicks only
  `saveDraft_a`, and verifies the wait-send reload. `sendId_a` remains forbidden.
- Received missed-punch approval is exposed as
  `oa.missed_punch.approval.prepare` and `oa.missed_punch.approve`. The opaque
  pending `affair_id` is frozen into the field interaction, the opinion is
  collected outside the model conversation, the exact title and
  `ContinueSubmit` availability are revalidated, and success requires pending
  disappearance. The internal page binding is not an agent-facing command.
- Meeting creation is exposed as `oa.meeting.create.prepare` and
  `oa.meeting.create`. Prepare performs `meetingInfo`, `roomListInfo`, and
  `validateRoomApps` checks without creating a meeting. After a separate trusted
  authorization, commit repeats the room checks, saves the standard body with
  ASCII-escaped JSON, sends through `meetingAjaxManager.send`, and requires both
  room-list and `meetingView` readback. The initial public contract deliberately
  uses the current OA user as the sole attendee; organization-picker expansion
  remains separate work.

All six capabilities use the encrypted per-user central browser session and
report `browser_bridge_used=false`. The old `oa matter`, `oa launch`, and
`oa meeting create execute` commands below are retained only as historical
contract-discovery evidence and are not runnable/public production paths.
- `【报销】差旅费审批报销单`: template id `-2046021869351779722`; outer launch
  shell matches the same collaboration save-draft pattern. Frame-aware rendered
  snapshots now expose the CAP4 business form text, and `business_form` reports
  its sections, semantic field candidates, and table-column candidates.
- `新建会议`: navigation URL
  `/seeyon/meeting.do?method=editor&showTab=true`; not a template-center item.
  `oa meeting create inspect` reads meeting fields such as `title`, `mtTitle`,
  `meetingTime`, `conferees`, `leader`, and `tel`. `oa meeting create dry-run`
  validates fields and the `save_a` / save-draft control without filling or
  clicking anything. `oa meeting create execute` now uses a direct backend path:
  initialize with `meetingInfo`, check `roomListInfo` and `validateRoomApps`,
  save the standard body with `content.do?method=saveOrUpdate`, then send with
  `meetingAjaxManager.send`. The successful live validation case created a
  3# room meeting in one pass and verified `meetingInfo`, `meetingView`, room
  schedule description, and absence of the Seeyon body-count error. The matter
  historical matter matrix reported this as `direct_create_ready`. Those
  daemon-era command names now serve only as migration evidence; the current
  agent surface is `oa.meeting.create.prepare` plus `oa.meeting.create` over the
  central MCP runtime.

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

No generic endpoint discovered from rendered HTML is promoted merely because it
looks write-like. The business-trip central adapter separately promotes the
known `collaboration.do?method=saveDraft` behavior behind a fixed contract,
one-time authorization, and wait-send readback. The legacy safe plan builder
therefore uses:

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
