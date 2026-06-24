# Development Policy

## Write-Action Gate

OA write actions must not be committed or pushed until both checks pass:

- Automated tests that cover the changed registry, daemon, CLI, MCP, and audit
  behavior.
- A live safe validation against the real OA bridge that cannot mutate business
  data, such as a no-match `oa pending submit` run with `--confirm`.

If a live validation fails, stop and fix or document the blocker. Do not commit
or push partially verified write-action code.

## Useful Verification Commands

```bash
python -m unittest discover
python -m bscli.cli.main --home .bscli oa pending submit --keyword "__BSCLI_NO_MATCH_VALIDATION__" --action ContinueSubmit --opinion "read" --limit 3 --confirm --verify-wait 0 --timeout 60 --format json
```
