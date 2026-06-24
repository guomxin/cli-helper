# OA Write Discovery Notes

Date: 2026-06-24

Scope: read-only exploration through the Chrome extension bridge. No OA submit,
approve, reject, archive, revoke, delete, upload, or send endpoint was invoked.

## Current Discovery Source

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
