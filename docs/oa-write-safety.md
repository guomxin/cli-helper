# OA Write Safety Model

This document describes the BSCLI/OA write-operation layer. The current
implementation can discover candidate actions, create write plans, record audit
entries, and execute the Seeyon `ContinueSubmit` action through the user's
logged-in Chrome session after explicit confirmation. It can also execute a
confirmed launch-page `SaveDraft` action that creates or updates an OA draft
without sending the workflow.

## Safety Boundary

- `oa detail actions --url <url>` reads a rendered detail page and extracts
  candidate write actions from page scripts.
- `oa pending actions --limit N` reads pending items, opens their detail pages,
  and indexes candidate write actions.
- `oa write capabilities --type pending` is the read-only agent inventory. It
  reports each pending item's category, supported write actions, current state,
  and verification method before an agent chooses a dry-run or execute command.
  Workflow page actions are split into three layers: promoted
  `supported_write_actions`, dry-run-only `unpromoted_write_actions`, and raw
  `discovered_write_actions`.
- `oa history list ...` and `oa write discover --source history ...` are
  read-only sample-mining commands. They use historical sent/done/tracked pages
  to find candidate actions and promotion evidence, but they do not authorize or
  execute any write action.
- `oa history profile`, `oa template match`, `oa launch inspect`, and
  `oa write discover --source launch` are the launch-page discovery path.
  `launch inspect` may open a new-flow page and therefore may create or keep an
  OA draft, but it only reads DOM, forms, buttons, scripts, hidden-field names,
  CSRF presence, and untested endpoint candidates. Launch-source candidates are
  forced to `execute_allowed=false` even when the same action code is executable
  in another governed context.
- `oa matter profile` and `oa matter inspect` are the business matter catalog
  layer. They organize historical clusters around matched templates and
  recommend atomic commands such as `launch_dry_run` or `launch_save_draft`.
  They are read-only; `matter inspect` opens a template launch page only when
  `--with-launch` is explicitly supplied.
- `oa matter launch-dry-run ...` and `oa matter launch-save-draft ... --confirm`
  are the matter-facing launch wrappers. They resolve a target matter by id,
  name, or alias, then delegate to the existing launch dry-run/save-draft
  engine with a template id or fixed launch URL. The first batch covers
  `【用印】用印申请单`, `【HR】补签申请单`, `【HR】出差申请单`, and `新建会议`.
  The dry-run command is read-only. The save-draft command keeps the same
  confirmation gate and `submitted_count=0` guard as `oa launch save-draft`.
- `oa matter matrix` is the agent-facing capability table for that catalog. It
  summarizes launch draft readiness, received-pending preflight readiness,
  coverage status, and next safe commands. It does not open launch pages, read
  pending details, dispatch browser write tasks, or authorize execution.
  Special modules can report promoted module-specific commands: meeting launch
  now appears as `direct_create_ready` with `meeting_create_execute` rather than
  as a generic collaboration-template launch.
- `oa matter preflight ...` is the business-intent preflight layer for received
  pending items. It resolves one pending item by id or keyword, reads workflow
  evidence, maps an intent such as `approve` or `archive` to an internal action
  binding, and returns `bscli.oa_matter_intent_preflight.v1`. It does not queue
  browser tasks, send requests, or call write endpoints. Opinion text is not
  echoed; only the opinion length is reported.
- `oa matter execute ... --confirm` is the confirmed business-intent execution
  layer. For ordinary received workflows it reruns `matter_preflight` and only
  dispatches the governed `write_execute` path when the preflight decision is
  `ready_for_execute`. For meeting intents (`join`, `not_join`, `pending`) it
  resolves the pending meeting and delegates to `meeting_reply_execute`. It does
  not promote dry-run-only actions such as `Archive`.
- `oa launch dry-run ...` is the launch-page save-draft precheck. It opens the
  launch page, validates requested field names/ids/labels against writable
  fields, verifies that a `saveDraft` / "保存待发" control exists, records a
  redacted audit row, and does not fill fields or click anything.
- `oa launch save-draft ... --confirm` is the governed launch-page draft write.
  It reuses the dry-run precheck, loads the versioned
  `seeyon.launch_save_draft.v1` page script, and sends a
  `seeyon_launch_save_draft` browser task only after confirmation. The extension
  opens the launch page and runs the daemon-sent script; the script fills
  requested fields and clicks only the save-draft control. The script refuses `sendId_a`,
  `ContinueSubmit`, `Submit`, "发送", and "提交" controls. Its successful result
  must include `draft_saved=true` and `submitted_count=0`.
- `oa write actions` reads the local write-action registry. The registry is the
  promotion source of truth for labels, risk, action type, execution status,
  and verification method.
- `oa write draft ...` builds a local write plan only. It does not contact the
  daemon and does not create an audit row.
- `oa write dry-run ...` builds the same local write plan and appends a
  sanitized audit row to `.bscli/audit/oa-write-plans.jsonl`.
- `oa write preflight ...` runs the same read-only precheck as dry-run, appends
  a sanitized audit row, and returns an agent-facing decision packet. It reports
  `ready_for_execute`, `dry_run_only`, or `blocked`, plus the confirmation
  contract an agent must satisfy before any production execution.
- `oa write prepare ...` builds the agent task packet. It combines workflow
  evidence with `write_preflight`, returns sanitized next steps, and is the
  preferred command before asking the user for production confirmation.
- `oa write execute ... --confirm` contacts the daemon, loads a versioned local
  page script such as `seeyon.continue_submit.v1`, sends a `seeyon_write_execute`
  browser task, and opens the source detail page in an inactive tab. The Chrome
  extension is the stable runner: it waits for the page, injects the daemon-sent
  script source, collects the script result/outcome, and closes the tab. The
  script verifies the target `affairId`, writes the opinion into
  `content_deal_comment`, chooses the page's own submit entry such as
  `dealSubmitFunc()` for inform nodes, and relies on a follow-up pending-list
  check for business success.
- The extension also exposes a generic `page_script_execute` bridge task for
  future governed browser actions. New OA workflow action logic should live in
  versioned project scripts and be dispatched by the daemon; the extension
  should only need reloads when the bridge protocol, Chrome permissions, or
  generic runner behavior changes.
- `oa page script-smoke` is the read-only runner probe. It dispatches a fixed
  `bscli.bridge_smoke.v1` script through `page_script_execute`, returns the
  current OA page title, URL, ready state, and marker, and does not accept
  arbitrary JavaScript from the CLI.
- Without `--confirm`, `oa write execute ...` returns `ok=false` and records only
  a blocked local plan.
- `oa pending submit ... --confirm` is the governed daemon command for repeated
  pending items. The CLI and MCP tool both call the same daemon execution path.
  It reads the pending list, verifies that each detail page exposes the
  requested action, executes one item, then reads the pending list again. The
  next item is not attempted unless the previous `affairId` has disappeared
  from pending.
- `oa meeting reply dry-run ...` resolves a pending meeting item, reads
  `meetingView`, and checks whether the current user can reply.
- `oa meeting reply execute ... --confirm` posts the reply through
  `meetingAjaxManager.reply`, then reads `meetingView` again and succeeds only
  when `myReply.feedbackFlag` matches the requested attitude.
- `oa meeting create inspect` and `oa meeting create dry-run` are fixed-entry
  wrappers around the OA meeting editor page. They open the editor page and
  validate fields such as `title` and `mtTitle`, but they do not fill, save, or
  send a meeting.
- `oa meeting create execute ... --confirm` is the promoted meeting-launch
  command. It uses the logged-in Chrome bridge to read `meetingInfo`, checks
  room availability through `roomListInfo` and `validateRoomApps`, saves the
  standard body through `content.do?method=saveOrUpdate`, sends through
  `meetingAjaxManager.send`, and verifies by reading the room schedule. Live
  validation should also read `meetingView` or the view page and confirm that
  the title is correct and the body-count error is absent.
- `matter-missed-punch-request` is the first formal received-workflow sample.
  Its business intent is `approve`, its current execution binding is the
  governed `ContinueSubmit` path, its default opinion is `同意`, and its live
  validation rule is pending-list disappearance. The workflow profile records
  that no extra business-form prefill was required in the validated sample; if a
  future sample exposes required fields, the profile must be updated before
  execution is widened.
- `matter-business-trip-request` is the first formal launch-side workflow
  sample. It is allowed to inspect the launch page, run a non-mutating
  dry-run, and save a draft only after `--confirm`. It is not allowed to submit
  or send the business trip workflow until a separate promoted submit mapping
  and live validation plan exist.
- `oa write endpoints ...` classifies endpoint candidates found during dry-run
  evidence collection. It does not call the candidates and marks each result
  with `safe_to_call=false`.
- `oa write smoke` is the fixed live validation for write-action development.
  It reads pending items first and refuses to run a confirmed no-op validation
  if the default no-match keyword is present.

Only launch-page `SaveDraft`, collaboration `ContinueSubmit`, meeting reply,
and direct meeting creation are executable at this stage. Reject, archive,
delete, revoke, return, upload, generic send, and other write actions remain
blocked until each has a dedicated mapping and tests.

`Archive` / `处理后归档` is intentionally promoted only to dry-run-only. Agents
may call `oa write dry-run --affair-id <id> --action Archive` to prove the
target exists and the detail page exposes the action, but `execute` remains
blocked until the action has an execution mapping, a post-write verification
method, and a user-confirmed production test.

## Governance Lifecycle

Promoted write actions share the same lifecycle:

1. Resolve the target from a stable business id or source URL.
2. Run a dry-run precheck that reads OA state but does not mutate it.
3. Require an explicit confirmation gate before production execution.
4. Execute through the logged-in Chrome bridge or a promoted backend API.
5. Read OA state back with the action-specific verification method.
6. Write a sanitized audit row that does not store opinion or feedback text.

For launch-page drafts, the verification method is
`draft_save_scheduled_ack`: the extension must acknowledge that it validated the
launch page, scheduled field filling plus the save-draft click, and reported
`submitted_count=0`. This is intentionally weaker than pending-list
disappearance because saving a draft does not move a pending workflow item. It
is still a real write because it may create or update a draft in OA.

The plan objects expose this as `governance.lifecycle`, together with
`governance.verification_method`, so agents can distinguish the safety protocol
from the concrete business command.
For unpromoted actions such as `Archive`, `governance.verification_method` is
`not_promoted`; that value means dry-run can validate current page capability,
but production success verification has not been accepted yet.
Dry-run may also attach `promotion.evidence` after reading the detail page. This
evidence can include the matched page action, safe hidden-field names,
CSRF-token presence, and untested endpoint candidates from rendered HTML. It is
for promotion analysis only and does not authorize execution.
Preflight wraps this same evidence in a sanitized `bscli.oa_write_preflight.v1`
packet. The packet's `execution_contract.will_execute` is always false,
`request_sent` is always false, and `network_probe_sent` is always false.
Only a later human-gated execute command with `confirm=true` can cross the
production boundary.
Prepare wraps preflight plus workflow evidence in
`bscli.oa_write_prepare.v1`. Its `next_steps.status` can be
`needs_human_confirmation`, `dry_run_only`, or `blocked`; it does not send a
request, dispatch a browser write task, or store opinion text.
`promotion.evidence.endpoint_analysis` and `oa write endpoints` use static URL
classification only. Automatic network probes are disabled because candidates
often contain write-like methods such as `save`, `finish`, or `archive`.

## Write Plan Shape

Write plans use `schema_version=bscli.oa_write_plan.v1` and contain:

- `mode`: `draft`, `dry-run`, or `execute`.
- `target`: currently `affair_id`, with optional `source_url`.
- `action`: normalized action code, display label, and risk.
- `opinion`: full text in CLI output, plus length.
- `promotion`: whether the action is executable or dry-run-only, plus the
  requirements that must be met before execution can be promoted.
  `promotion.evidence`, when present, records read-only clues collected during
  precheck; endpoint candidates remain `tested=false` until separately
  validated without mutating OA state.
- `safety`: local draft/dry-run plans use `will_execute=false` and
  `dry_run_only=true`; confirmed execution plans use `will_execute=true` and
  `dry_run_only=false`.
- `request`: currently `not_built`, `not_sent`, `blocked`, or
  `sent_by_extension`; method, URL, and body remain null because execution uses
  the live page workflow rather than a hand-built backend request.
- `request.payload_preview`: a local, non-sent preview containing `affairId`,
  `actionCode`, `opinionText`, optional `sourceUrl`, and `dryRunOnly=true`.
- `request.payload_fields`: a field-level summary for audit and validation.

Audit rows remove `opinion.text` and keep only metadata such as opinion length.
They also keep request bodies null and redact
`request.payload_preview.opinionText`, so sensitive payload text is not written
to disk.

Launch draft plans use `schema_version=bscli.oa_launch_draft_plan.v1` and
contain:

- `mode`: `dry-run` or `save-draft`.
- `target`: `template_id`, launch `url`, and page title when known.
- `action`: fixed to `SaveDraft` / "保存待发" with medium risk.
- `fields`: requested fields with `name`, `id`, `label`, `matched`,
  `writable`, `value_present`, and `length`; raw field values are not stored.
- `safety`: `submitted_count=0`, `only_allowed_action=SaveDraft`, and explicit
  forbidden send/submit action markers.
- `request`: a browser-click plan with a payload preview, never a backend write
  URL or raw body.

Launch draft audit rows are written to `.bscli/audit/oa-launch-drafts.jsonl`.
They store field names and value lengths only; raw field text is not written to
disk.

The CLI audit reader preserves that boundary. `oa audit writes show --index N`
returns a sanitized single record, and `oa audit writes search` returns
summaries filtered by `affair_id`, action, or status. Lists and indexes are
newest-first.

Batch submit verification rows are written separately to
`.bscli/audit/oa-write-verifications.jsonl`. Each row uses
`schema_version=bscli.oa_write_verification.v1` and records the `affair_id`,
action metadata, submit task metadata, and a normalized verification object:

- `type`: currently `pending_disappearance`.
- `status`: `disappeared`, `still_pending`, `verify_failed`,
  `submit_failed`, `action_missing`, `invalid_item`, or `not_checked`.
- `verified`: true only when a post-submit pending-list read actually proved
  `disappeared` or `still_pending`.
- `before_present`, `after_present`, and `present_after_submit`: booleans for
  agent-friendly checks.

The verification audit does not store opinion text.

Meeting reply verification uses a different rule. A meeting can remain visible
in pending after the reply is recorded, so pending-list disappearance is not a
reliable success signal. Meeting reply execution therefore reads `meetingView`
after submit and treats the write as successful only when the current user's
`myReply.feedbackFlag` matches the requested action.

## Risk Classification

Action codes considered high risk include:

- `ContinueSubmit`
- `Submit`
- `Archive`
- `UploadAttachment`
- `Return`
- `Revoke`
- `Disagree`
- `Delete`

Other discovered write-capable UI actions currently default to medium risk.
All write actions require confirmation metadata even when they are dry-run only.

## Promotion Requirements

Before a new OA write action can become executable, it must have:

- A local write-action spec with action type, risk, promotion status, and
  verification method.
- A discovered endpoint or UI workflow mapped to the exact business action.
- Required parameters identified from DOM, page scripts, or captured network
  records.
- CSRF/session requirements documented without storing secret values.
- A dry-run validator that can build the request without sending it.
- An explicit confirmation gate.
- A test proving that execution remains blocked without confirmation.
- A passing `oa write smoke` run against the real bridge.
- A production-risk note covering rollback limitations.

Until those conditions are met and reviewed for that action, BSCLI must keep it
at discovery, draft, and dry-run only.
Use `docs/oa-write-action-expansion-playbook.md` as the practical checklist for
moving an action through discovery, implementation, live validation, and
promotion.

## Agent Tool Exposure

The safe planning commands are registered in the normal BSCLI command registry:

- `oa__launch_dry_run`
- `oa__launch_save_draft`
- `oa__matter_profile`
- `oa__matter_matrix`
- `oa__matter_inspect`
- `oa__matter_launch_dry_run`
- `oa__matter_launch_save_draft`
- `oa__matter_preflight`
- `oa__matter_execute`
- `oa__write_capabilities`
- `oa__write_discover`
- `oa__write_draft`
- `oa__write_dry_run`
- `oa__write_endpoint_candidates`
- `oa__write_preflight`
- `oa__write_prepare`
- `oa__write_execute`
- `oa__pending_submit`
- `oa__meeting_reply_dry_run`
- `oa__meeting_reply_execute`
- `oa__meeting_create_inspect`
- `oa__meeting_create_dry_run`
- `oa__meeting_create_execute`

`matter_profile`, `matter_matrix`, `matter_inspect`, `matter_preflight`,
`launch_dry_run`, `write_capabilities`, `write_discover`, `write_draft`,
`write_dry_run`, `write_preflight`, `write_prepare`, and
`meeting_reply_dry_run` are exposed as read/low-risk daemon tools because they
do not mutate OA state. `launch_save_draft`, `write_execute`, `pending_submit`,
`matter_execute`, and `meeting_reply_execute` are exposed as write/human-gate
tools and require `confirm` in their input schema before they can perform a
write. Confirmed launch drafts are delivered through the Chrome extension
bridge and verified by `draft_save_scheduled_ack` with `submitted_count=0`.
Confirmed `ContinueSubmit` executions are delivered through the Chrome
extension bridge and verified by
pending disappearance. Confirmed meeting replies are delivered through
`meetingAjaxManager.reply` and verified by `meetingView.myReply.feedbackFlag`.
Confirmed direct meeting creation is exposed through both the CLI/daemon path
and the agent-facing `oa__meeting_create_execute` tool. It is delivered through
`meetingAjaxManager.send` after saving the meeting body and is verified by room
schedule readback plus live `meetingView` or view-page checks during validation.

Weekly-report inform/read-notice pages are still executed as `ContinueSubmit`.
The extension prefers the page's direct `dealSubmitFunc` on `nodePolicy=inform`
pages and reports the selected submit entry plus any immediate submit outcome.
Use `after_submit_wait_ms` for slow pages before the daemon performs pending
disappearance verification. `Archive` / post-processing archive remains
dry-run-only because the OA page can require a document archive destination
before it can submit safely.
