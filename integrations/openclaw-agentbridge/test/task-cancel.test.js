import test from "node:test";
import assert from "node:assert/strict";
import { createAgentBridgeProxyTools } from "../lib/proxy-tools.js";

function harness(payload, selected = "current-task", planOwner = selected) {
  const calls = [];
  const tools = createAgentBridgeProxyTools({
    context: { sessionKey: "agent:main:telegram:direct:user-a" },
    serverName: "agentbridge",
    identityRouter: { resolveToolContext: () => ({ bound: true, binding: {}, client: {
      async callTool(name, params) {
        assert.equal(name, "agentbridge_task_plan_get");
        return { plan: { planId: params.plan_id, taskId: planOwner } };
      },
      async callToolResult(name, params) {
        calls.push({ name, params });
        return { structuredContent: payload, content: [{ type: "text", text: JSON.stringify(payload) }] };
      },
    } }) },
    taskContinuationResolver: () => selected ? { taskId: selected } : null,
  });
  return { calls, cancel: tools.find(t => t.name === "agentbridge_task_cancel"),
    cancelPlan: tools.find(t => t.name === "agentbridge_task_plan_cancel") };
}

function resultPayload(result) {
  return result.structuredContent || JSON.parse(result.content[0].text);
}

test("cancel rejects a historical target before contacting the service", async () => {
  const h = harness({ status: "succeeded" });
  const response = await h.cancel.execute("call", { task_id: "historical-task" });
  assert.equal(resultPayload(response).error.code, "TASK_CANCEL_TARGET_MISMATCH");
  assert.equal(h.calls.length, 0);
});

test("cancel validates both the returned task ID and terminal state", async () => {
  for (const payload of [
    { status: "succeeded", task: { taskId: "historical-task", status: "canceled" } },
    { status: "succeeded", task: { taskId: "current-task", status: "waiting_user" } },
    { status: "succeeded" },
    {},
  ]) {
    const h = harness(payload);
    const response = await h.cancel.execute("call", { task_id: "current-task" });
    assert.equal(resultPayload(response).error.code, "TASK_CANCEL_RESULT_UNCONFIRMED");
    assert.equal(h.calls.length, 1);
  }
});

test("ordinary and composed cancellations confirm only the exact target", async () => {
  for (const target of [
    { task: { taskId: "current-task", status: "canceled" } },
    { plan: { taskId: "current-task", planId: "plan-1", state: "canceled" } },
  ]) {
    const h = harness({ status: "succeeded", ...target });
    const response = await h.cancel.execute("call", { task_id: "current-task" });
    assert.equal(resultPayload(response).status, "succeeded");
  }
  const h = harness({ status: "succeeded", plan: { taskId: "current-task", planId: "wrong-plan", state: "canceled" } });
  assert.equal(resultPayload(await h.cancelPlan.execute("call", { plan_id: "plan-1" })).error.code,
    "TASK_CANCEL_RESULT_UNCONFIRMED");
});

test("central write-in-progress rejection is preserved without retry", async () => {
  const h = harness({ status: "rejected", error: { code: "PLAN_COMMIT_IN_PROGRESS" } });
  const response = await h.cancel.execute("call", { task_id: "current-task" });
  assert.equal(resultPayload(response).error.code, "PLAN_COMMIT_IN_PROGRESS");
  assert.equal(h.calls.length, 1);
});

test("analysis cancellation pending is progress only for the exact active task", async () => {
  const payload = { status: "running", cancellation_pending: true,
    task: { taskId: "current-task", status: "running" } };
  const h = harness(payload);
  const response = resultPayload(await h.cancel.execute("call", { task_id: "current-task" }));
  assert.equal(response.status, "running");
  assert.equal(response.cancellation_pending, true);
  assert.equal(h.calls.length, 1);
  const wrong = harness({ ...payload, task: { taskId: "historical-task", status: "running" } });
  assert.equal(resultPayload(await wrong.cancel.execute("call", { task_id: "current-task" })).error.code,
    "TASK_CANCEL_RESULT_UNCONFIRMED");
});

test("plan cancellation checks ownership of the selected task before mutation", async () => {
  const h = harness({ status: "succeeded" }, "current-task", "historical-task");
  const response = await h.cancelPlan.execute("call", { plan_id: "historical-plan" });
  assert.equal(resultPayload(response).error.code, "TASK_CANCEL_TARGET_MISMATCH");
  assert.equal(h.calls.length, 0);
});

test("explicit cancellation without a continuation still verifies its exact result", async () => {
  const h = harness({ status: "succeeded", task: { taskId: "explicit-task", status: "canceled" } }, null);
  assert.equal(resultPayload(await h.cancel.execute("call", { task_id: "explicit-task" })).status, "succeeded");
});
