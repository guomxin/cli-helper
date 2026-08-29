import test from "node:test";
import assert from "node:assert/strict";

import { HOST_CONTEXT_META_KEY } from "../lib/host-contract.js";
import { createHostRuntimeReporter } from "../lib/runtime-reporter.js";

test("reports one bounded runtime snapshot per configured identity", async () => {
  const calls = [];
  const client = {
    async callTool(name, params, options) {
      calls.push({ name, params, options });
      return { status: "succeeded", snapshotId: "snapshot-1" };
    },
  };
  const reporter = createHostRuntimeReporter({
    identityRouter: {
      enabled: true,
      configuredIdentities() {
        return [{ binding: { key: "user-a", label: "用户甲" }, client }];
      },
    },
    coordinator: {
      hostRuntimeCounts() {
        return { activeTaskCount: 2, waitingInteractionCount: 1 };
      },
    },
    now: () => Date.parse("2026-08-29T10:00:00Z"),
  });

  const results = await reporter.collectOnce();

  assert.equal(results.length, 1);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].name, "agentbridge_host_runtime_snapshot");
  assert.equal(calls[0].params.snapshot.activeTaskCount, 2);
  assert.equal(calls[0].params.snapshot.waitingInteractionCount, 1);
  assert.equal(calls[0].params.snapshot.transportErrorCount, 0);
  assert.equal(
    calls[0].options.meta[HOST_CONTEXT_META_KEY].hostInstanceId,
    "openclaw-gateway",
  );
  assert.equal(JSON.stringify(calls).includes("Bearer"), false);
});

test("runtime reporting failure is isolated from business task state", async () => {
  const warnings = [];
  const reporter = createHostRuntimeReporter({
    identityRouter: {
      enabled: true,
      configuredIdentities() {
        return [
          {
            binding: { key: "user-b", label: "用户乙" },
            client: {
              async callTool() {
                const error = new Error("offline");
                error.code = "ETIMEDOUT";
                throw error;
              },
            },
          },
        ];
      },
    },
    coordinator: {
      hostRuntimeCounts() {
        return { activeTaskCount: 7, waitingInteractionCount: 3 };
      },
    },
    logger: { warn: (message) => warnings.push(message) },
  });

  const result = await reporter.collectOnce();

  assert.equal(result[0].errorCode, "ETIMEDOUT");
  assert.deepEqual(reporter.status(), {
    running: false,
    transportErrorCount: 1,
    lastErrorCode: "ETIMEDOUT",
  });
  assert.equal(warnings.length, 1);
});

test("a structured runtime rejection is counted as a failed signal", async () => {
  const reporter = createHostRuntimeReporter({
    identityRouter: {
      enabled: true,
      configuredIdentities() {
        return [
          {
            binding: { key: "user-c", label: "用户丙" },
            client: {
              async callTool() {
                return {
                  status: "failed",
                  error: { code: "HOST_REGISTRATION_REQUIRED" },
                };
              },
            },
          },
        ];
      },
    },
    coordinator: { hostRuntimeCounts: () => ({}) },
  });

  const result = await reporter.collectOnce();

  assert.equal(result[0].errorCode, "HOST_REGISTRATION_REQUIRED");
  assert.equal(reporter.status().transportErrorCount, 1);
});
