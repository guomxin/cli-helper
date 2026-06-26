# OA Matter Intent Preflight Design

Date: 2026-06-26

## Goal

Expose OA write preparation through business matter intents instead of raw page
action names. Agents should ask whether a received pending matter can be
approved or archived; the system should resolve the current pending item,
inspect its page evidence, and report the internal binding without executing
anything.

## Design

The public layer is `oa matter preflight`:

```powershell
python -m bscli.cli.main --home .bscli oa matter preflight --keyword "weekly" --intent approve --opinion "read"
python -m bscli.cli.main --home .bscli oa matter preflight --id <affair_id> --intent archive --opinion "read"
```

This command is read-only. It resolves one pending item by `--id` or
`--keyword`, reads the existing workflow evidence packet, maps the business
intent to an internal action binding, and returns
`bscli.oa_matter_intent_preflight.v1`.

The first received-pending intents are:

- `approve` -> internal action `ContinueSubmit`, executable only when the page
  exposes that action and the existing write registry marks it promoted.
- `archive` -> internal action `Archive`, currently dry-run-only even when the
  page exposes it.

The raw actions remain internal execution bindings. They are still visible in
diagnostic fields, audit evidence, and low-level debug commands, but the
matter-level command does not require agents to choose `ContinueSubmit` or
`Archive` directly.

## Safety

`matter_preflight` never queues an extension task, sends an OA request, or calls
any write endpoint. It returns an execution contract with `will_execute=false`
and `request_sent=false`. Opinion text is not echoed; only its length is
reported.

Executable status is inherited from the existing write-action registry:

- promoted actions can report `ready_for_execute`;
- dry-run-only actions report `dry_run_only`;
- missing page actions report `blocked`.

## Future Work

Later iterations can add `oa matter handle` and `oa matter start`, but only by
delegating to already governed lower-level actions and after live OA validation.
New real write actions, such as `Archive` execution or launch-page send, must
still pass the existing promotion requirements before they become executable.
