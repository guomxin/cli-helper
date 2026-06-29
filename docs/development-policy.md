# Development Policy

## Write-Action Gate

OA write actions must not be committed or pushed until both checks pass:

- Automated tests that cover the changed registry, daemon, CLI, MCP, and audit
  behavior.
- A live safe validation against the real OA bridge. For submit-style actions,
  use a no-op validation such as `oa write smoke`. For launch-page draft
  actions, a confirmed validation may create or update a draft only when the
  user has explicitly approved that draft-level test and the command does not
  send or submit a workflow.

If a live validation fails, stop and fix or document the blocker. Do not commit
or push partially verified write-action code.

## Useful Verification Commands

```bash
python -m unittest discover
python -m bscli.cli.main --home .bscli oa write smoke --timeout 60 --format json
python -m bscli.cli.main --home .bscli oa launch inspect --template-id <template_id> --settle-ms 0 --timeout 60 --format json
python -m bscli.cli.main --home .bscli oa launch dry-run --template-id <template_id> --field content_coll="Draft note" --settle-ms 0 --timeout 60 --format json
```
