import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { InteractionCoordinator } from "../lib/coordinator.js";
import { HOST_CONTEXT_META_KEY, TASK_CONTEXT_META_KEY, OPENCLAW_HOST_VERSION } from "../lib/host-contract.js";
import { PLUGIN_VERSION } from "../lib/plugin.js";

test("release package, plugin, and registered host versions cannot drift", () => {
  const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  assert.equal(PLUGIN_VERSION, pkg.version);
  assert.equal(OPENCLAW_HOST_VERSION, pkg.version);
});

function coordinator() {
  return new InteractionCoordinator({
    api: { logger: { info() {}, warn() {} } },
    config: { allowedCardOrigins: [], wakeAgentOnComplete: true },
  });
}

test("legacy login replay preserves formal host context and coordinator lease", async () => {
  const c = coordinator();
  let request;
  c.observeTaskResponse = async () => {};
  c.deliverReadContinuation = async () => true;
  await c.replayReadContinuation({
    taskId: "task-a", coordinatorLeaseVersion: 7,
    readContinuation: { toolName: "oa_workflow_pending_list", arguments: { keyword: "申请", limit: 20 } },
    mcpClient: { async callTool(...args) {
      request = args;
      return { status: "succeeded", result: { collection: "pending", items: [] } };
    } },
  });
  assert.equal(request[2].meta[HOST_CONTEXT_META_KEY].hostInstanceId, "openclaw-gateway");
  assert.deepEqual(request[2].meta[TASK_CONTEXT_META_KEY], { taskId: "task-a", coordinatorLeaseVersion: "7" });
  assert.deepEqual(request[1], { keyword: "申请", limit: 20 });
});

for (const status of ["succeeded", "failed"]) {
  test(`recovered central read ${status} never replays, sends twice, or wakes heartbeat`, async () => {
    const c = coordinator();
    const calls = [];
    c.notify = async () => assert.fail("central timeline owns feedback");
    c.replayReadContinuation = async () => assert.fail("no second read");
    const record = {
      taskId: "task-a", interaction: { interactionId: "login-a", type: "credential" },
      mcpClient: { async callTool(name, _args, options) {
        calls.push(name);
        assert.ok(options.meta[HOST_CONTEXT_META_KEY]);
        if (name === "agentbridge_host_coordinator_lease_acquire") {
          return { coordinatorLease: { hostInstanceId: "openclaw-gateway", version: 7 } };
        }
        assert.equal(options.meta[TASK_CONTEXT_META_KEY].coordinatorLeaseVersion, "7");
        return { status, taskStatus: status, nextAction: { type: "original_request_completed" } };
      } },
    };
    assert.equal(await c.resume(record, new AbortController().signal), true);
    assert.equal(c.isTaskTerminal("task-a"), true);
    assert.deepEqual(calls, ["agentbridge_host_coordinator_lease_acquire", "agentbridge_interaction_resume"]);
    assert.equal(await c.resume(record, new AbortController().signal), false);
  });
}
