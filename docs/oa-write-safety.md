# OA Write Safety Model

This document describes the first BSCLI/OA write-operation layer. The current
implementation is intentionally non-executing: it can discover candidate actions,
create write plans, and record dry-run audit entries, but it cannot submit,
approve, reject, archive, delete, revoke, or upload anything in production OA.

## Safety Boundary

- `oa detail actions --url <url>` reads a rendered detail page and extracts
  candidate write actions from page scripts.
- `oa pending actions --limit N` reads pending items, opens their detail pages,
  and indexes candidate write actions.
- `oa write draft ...` builds a local write plan only. It does not contact the
  daemon and does not create an audit row.
- `oa write dry-run ...` builds the same local write plan and appends a
  sanitized audit row to `.bscli/audit/oa-write-plans.jsonl`.
- `oa write execute ...` is reserved but blocked. Even with `--confirm`, it
  returns `ok=false` and records only a blocked local plan.

No write command sends a browser task, performs `fetch`, clicks a button, fills a
form, uploads a file, or calls an OA endpoint.

## Write Plan Shape

Write plans use `schema_version=bscli.oa_write_plan.v1` and contain:

- `mode`: `draft`, `dry-run`, or `execute`.
- `target`: currently `affair_id`, with optional `source_url`.
- `action`: normalized action code, display label, and risk.
- `opinion`: full text in CLI output, plus length.
- `safety`: `will_execute=false`, `requires_confirmation=true`,
  `dry_run_only=true`.
- `request`: currently `not_built`, `not_sent`, or `blocked`; method, URL, and
  body are always null.

Audit rows remove `opinion.text` and keep only metadata such as opinion length.
They also keep request bodies null, so sensitive payloads are not written to
disk.

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

Before any OA write can become executable, it must have:

- A discovered endpoint or UI workflow mapped to the exact business action.
- Required parameters identified from DOM, page scripts, or captured network
  records.
- CSRF/session requirements documented without storing secret values.
- A dry-run validator that can build the request without sending it.
- An explicit confirmation gate.
- A test proving that execution remains blocked without confirmation.
- A production-risk note covering rollback limitations.

Until those conditions are met and reviewed, BSCLI must stay at discovery,
draft, and dry-run only.

## Agent Tool Exposure

The safe planning commands are registered in the normal BSCLI command registry:

- `oa__write_draft`
- `oa__write_dry_run`
- `oa__write_execute`

`write_draft` and `write_dry_run` are exposed as read/low-risk daemon tools
because they do not mutate OA state. `write_execute` is exposed as a
write/high-risk human-gate tool and requires a `confirm` argument in the tool
schema, but the daemon implementation still returns a blocked plan and does not
deliver any browser task.
