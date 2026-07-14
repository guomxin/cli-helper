# OA Write Action Expansion Playbook

> Central-runtime note: the old browser bridge was removed on 2026-07-13. Any
> bridge command examples below are historical evidence, not executable steps.

This playbook turns one-off OA exploration into a repeatable promotion path for
new write actions. Use it when adding a new action for a workflow, a launch
template, or a special OA module such as meetings.

## Promotion Shape

Every promoted write action should answer six questions before it is exposed as
an executable command:

1. What business intent does the user ask for?
2. Which OA page or backend API initializes the action?
3. Which fields and IDs are required before execution?
4. Which request actually performs the write?
5. What readback proves the write succeeded?
6. What can go wrong, and where should the command stop?

The command surface should stay business-level. Raw page actions, endpoint
names, and form internals are implementation details.

## Discovery Checklist

Use this order instead of jumping straight to a submit call:

1. Resolve the target from a stable business identifier, pending item, template,
   or fixed module entry.
2. Read the current page or initialization API and preserve only non-secret
   structural evidence.
3. Identify the minimum required write inputs.
4. Locate the precheck endpoint or page validation step.
5. Locate the content/body save step when the OA module has a body iframe or
   hidden main-body fields.
6. Locate the final submit/send/save request.
7. Identify the readback source that proves the write happened.
8. Decide whether page-level rendered verification is needed in addition to API
   readback.

For Seeyon workflows, remember that the visible workflow toolbar and the real
business form can be in different frames. If high-level detail fields are empty,
inspect the iframe before assuming the form has no required fields.

## Implementation Checklist

Promoted execution should follow this lifecycle:

1. Build a dry-run/preflight packet first.
2. Require explicit confirmation for execution.
3. Re-read target state immediately before sending the write.
4. Run OA's own precheck or availability check.
5. Save body/content records before submit when the frontend does so.
6. Send exactly one write request.
7. Read back the target state.
8. Return a structured result with checks, submitted status, verification, and
   enough IDs for follow-up inspection.
9. Write sanitized audit data without storing full opinion text or secrets.

Do not promote an action when verification only says "request returned 200".
The readback has to prove the business state changed as expected.

## Live Validation Checklist

Before committing and pushing a new write action:

1. Run focused unit tests that prove confirmation gating, happy path ordering,
   and failure blocking.
2. Run the relevant broader central service, CLI, MCP, and card tests.
3. Validate once against real OA using the managed central session and
   CentralBrowserWorker, with the retired extension absent.
4. For non-ASCII values, avoid raw Chinese literals in PowerShell here-strings;
   use UTF-8 JSON or Python-generated Unicode code points.
5. Verify backend readback and rendered/page-level state when the user would see
   the result in the UI.
6. If live validation fails, stop. Do not commit or push partially verified
   write-action code.

## Reference: Meeting Create

Direct meeting creation is the current reference implementation for a special
module write action:

1. `meetingInfo` initializes the form and returns the temporary body module ID.
2. `roomListInfo` reads room availability for the requested time.
3. `validateRoomApps` asks OA to validate the selected room app.
4. `content.do?method=saveOrUpdate` saves the standard body before send.
5. `meetingAjaxManager.send` creates the meeting.
6. `roomListInfo` verifies the created room app.
7. Live validation also reads `meetingInfo`, `meetingView`, and the view page to
   confirm the title is correct and the body-count error is absent.

The important lesson is that creating only the meeting shell is not enough. The
frontend saves the body against the temporary module before sending; the backend
path must do the same or the view page can show a body-count error.

## Reference: Received Workflow Approval

For ordinary pending workflows, do not create one implementation per button.
Keep the user-facing command at the matter/intent level:

- `approve` maps to a validated submit action when the detail page exposes an
  executable `ContinueSubmit` path.
- `archive` remains dry-run-only until archive destination handling and
  verification are validated.
- Business-form prefill must be workflow-specific only when evidence shows the
  form requires it.

Verification is pending disappearance for standard submissions, but this is not
valid for every module. Meetings, drafts, and archive-like actions need their
own readback rules.
