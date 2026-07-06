# Development Policy

## Current Architecture Baseline

The active implementation uses Python standard-library components unless a
specific change justifies adding dependencies: `argparse` for the CLI,
`http.server` for the local daemon, and Chrome extension polling for browser
bridge tasks. Treat older FastAPI/Starlette, WebSocket/Native Messaging, Typer,
uv, or hatch notes as exploratory sketches, not current requirements.

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

## Live OA Validation Encoding

Windows PowerShell can corrupt non-ASCII OA arguments before Python receives
them, especially when ad hoc validation scripts embed Chinese text directly in
a here-string. For live OA write tests, do not rely on raw Chinese literals in
PowerShell script bodies. Prefer one of these inputs:

- A UTF-8 JSON file or stdin payload that Python reads explicitly as UTF-8.
- Python-generated Unicode code points, for example building the subject from
  `chr(...)` values inside the script.
- Existing CLI arguments only when the shell path has already been verified to
  preserve UTF-8 for that command.

After a write involving non-ASCII values, verify the exact text through the
backend readback and, when possible, the rendered view page. A successful
meeting-create validation must prove that the title is not stored as `?????`
and that the Seeyon body-count error is absent.

## Windows Daemon Startup

When starting the local daemon from Windows PowerShell, first check whether the
process environment contains both `Path` and `PATH`. Some PowerShell launch
paths raise a duplicate-key error when `Start-Process` copies that environment.
The safe local workaround is to normalize only the current PowerShell process
environment, then start the daemon hidden:

```powershell
$pathValue = [Environment]::GetEnvironmentVariable('Path','Process')
if (-not $pathValue) { $pathValue = [Environment]::GetEnvironmentVariable('PATH','Process') }
[Environment]::SetEnvironmentVariable('PATH', $null, 'Process')
[Environment]::SetEnvironmentVariable('Path', $pathValue, 'Process')
Start-Process -FilePath 'C:\Users\xingm\AppData\Local\Python\bin\python.exe' -ArgumentList @('-m','bscli.cli.main','--home','.bscli','daemon','serve','--host','127.0.0.1','--port','8765') -WorkingDirectory 'D:\Codes\CLIExp' -WindowStyle Hidden
```

In Codex's sandbox, child background processes may be cleaned up when the
command exits. If the daemon must remain available for real Chrome-bridge
validation, start it with the same command outside the sandbox after approval,
then verify with `python -m bscli.cli.main --home .bscli daemon status`.

## Useful Verification Commands

```bash
python -m unittest discover
python -m bscli.cli.main --home .bscli oa write smoke --timeout 60 --format json
python -m bscli.cli.main --home .bscli oa launch inspect --template-id <template_id> --settle-ms 0 --timeout 60 --format json
python -m bscli.cli.main --home .bscli oa launch dry-run --template-id <template_id> --field content_coll="Draft note" --settle-ms 0 --timeout 60 --format json
```
