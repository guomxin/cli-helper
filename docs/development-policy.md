# Development Policy

## Current Architecture Baseline

The only active runtime is central AgentBridge:

- argparse-based capability CLI;
- official Python MCP SDK with Streamable HTTP;
- CentralCapabilityService shared by CLI and MCP;
- per-user HTTP sessions and managed Playwright profiles;
- trusted authentication, business-input, and authorization cards;
- host-independent `agentbridge.interaction.v1` envelopes with poll/resume;
- SQLite operation, identity, field-submission, and authorization ledgers.

Do not add a client browser extension, localhost daemon, daemon proxy command,
personal-browser Profile dependency, or silent fallback execution path.

AgentBridge returns interaction envelopes; it does not launch a browser or
assume Codex is the caller. Host renderers may translate envelopes into native
buttons or embedded web apps, but they must contain no OA business rules and
must not route credentials or trusted business fields through the model.

The Credential Broker, capability Worker, and every process that restores a
given user's encrypted session state must run under one fixed OS security
identity. Treat `session login` as an idempotent session-ensure operation:

- reuse an active session only after a live server probe and refresh its state;
- create an authentication card only after OA confirms login expiry;
- retry `SESSION_CHECK_UNAVAILABLE` without requesting credentials;
- route `SESSION_RUNTIME_MISMATCH` through the bound central runtime and never
  expire, delete, or replace the preserved session as a recovery shortcut.

## Change Gate

Do not commit or push a behavior change until:

1. Focused tests for the changed capability, adapter, card, or policy pass.
2. The complete automated suite and compile check pass.
3. A live safe validation is completed when the change touches a real OA
   contract and suitable test data exists.

If live validation fails, stop and fix or record the blocker. Do not commit
partially verified write behavior.

## Write Actions

Every central write capability must use:

    prepare -> authorize -> commit -> verify

Requirements:

- organize public capabilities by business workflow, not low-level endpoints;
- collect sensitive business fields through a trusted field card;
- freeze an immutable plan before authorization;
- bind authorization to user, system, session, capability version, plan hash,
  target, and TTL;
- consume authorization once at the commit boundary;
- use an idempotency key and object/session locking;
- verify the server-backed business state, not only an HTTP status;
- return unknown after an unverified commit boundary and never retry blindly.

## Live OA Validation

- Use only the central managed session and CentralBrowserWorker.
- Keep the retired extension absent and do not start a localhost daemon.
- Use a trusted card when login, field entry, or authorization is required.
- For non-ASCII values, use UTF-8 JSON or another already verified encoding path.
- Verify exact text and business state through backend or rendered readback.
- Obtain explicit user approval before a test that creates a draft or changes
  workflow state.

## Verification Commands

~~~bash
python -m unittest discover -s tests
python -m compileall -q bscli
python -m pip check
python -m bscli.cli.main --home .bscli capability list
python -m bscli.cli.main --home .bscli session status --system oa --user-subject <user>
~~~

The retirement guard tests must continue to prove that the legacy public
commands and transport files do not return.
