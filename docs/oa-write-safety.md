# OA Write Safety Model

This document describes the BSCLI/OA write-operation layer. The current
implementation can discover candidate actions, create write plans, record audit
entries, and execute the Seeyon `ContinueSubmit` action through the user's
logged-in Chrome session after explicit confirmation.

## Safety Boundary

- `oa detail actions --url <url>` reads a rendered detail page and extracts
  candidate write actions from page scripts.
- `oa pending actions --limit N` reads pending items, opens their detail pages,
  and indexes candidate write actions.
- `oa write draft ...` builds a local write plan only. It does not contact the
  daemon and does not create an audit row.
- `oa write dry-run ...` builds the same local write plan and appends a
  sanitized audit row to `.bscli/audit/oa-write-plans.jsonl`.
- `oa write execute ... --confirm` contacts the daemon, sends a
  `seeyon_write_execute` browser task, opens the source detail page in an
  inactive tab, verifies the target `affairId`, writes the opinion into
  `content_deal_comment`, invokes the page's own `dealSubmitFunc()` submit
  function, and relies on a follow-up pending-list check for business success.
- Without `--confirm`, `oa write execute ...` returns `ok=false` and records only
  a blocked local plan.

Only `ContinueSubmit` is executable at this stage. Reject, archive, delete,
revoke, return, upload, and other write actions remain blocked until each has a
dedicated mapping and tests.

## Write Plan Shape

Write plans use `schema_version=bscli.oa_write_plan.v1` and contain:

- `mode`: `draft`, `dry-run`, or `execute`.
- `target`: currently `affair_id`, with optional `source_url`.
- `action`: normalized action code, display label, and risk.
- `opinion`: full text in CLI output, plus length.
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

- A discovered endpoint or UI workflow mapped to the exact business action.
- Required parameters identified from DOM, page scripts, or captured network
  records.
- CSRF/session requirements documented without storing secret values.
- A dry-run validator that can build the request without sending it.
- An explicit confirmation gate.
- A test proving that execution remains blocked without confirmation.
- A production-risk note covering rollback limitations.

Until those conditions are met and reviewed for that action, BSCLI must keep it
at discovery, draft, and dry-run only.

## Agent Tool Exposure

The safe planning commands are registered in the normal BSCLI command registry:

- `oa__write_draft`
- `oa__write_dry_run`
- `oa__write_execute`

`write_draft` and `write_dry_run` are exposed as read/low-risk daemon tools
because they do not mutate OA state. `write_execute` is exposed as a
write/high-risk human-gate tool and requires a `confirm` argument in the tool
schema. Confirmed `ContinueSubmit` executions are delivered through the Chrome
extension bridge.
