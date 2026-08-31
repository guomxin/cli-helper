import test from "node:test";
import assert from "node:assert/strict";
import { InteractionCoordinator } from "../lib/coordinator.js";
import { CARD_ORIGIN, interaction, toolResult } from "./fixtures.js";

function harness({ retryable = true, recover = true, maxPollSeconds = 300 } = {}) {
  let now = Date.parse("2026-08-31T00:00:00Z");
  const calls = [];
  const notices = [];
  const delays = [];
  const client = {
    async callTool(name) {
      calls.push(name);
      if (!recover || calls.length <= 6) {
        throw Object.assign(new Error("transport unavailable"), {
          code: "MCP_UNREACHABLE", transportCode: "ECONNRESET", retryable,
        });
      }
      return toolResult(interaction({
        state: "completed", resume: { ready: true, completed: false },
      }));
    },
  };
  const coordinator = new InteractionCoordinator({
    api: { logger: { info() {}, warn() {} } },
    config: { pollIntervalSeconds: 2, maxPollSeconds, allowedCardOrigins: [CARD_ORIGIN] },
    now: () => now,
    sleep: async (ms) => { delays.push(ms); now += ms; },
  });
  coordinator.notify = async (_record, status) => notices.push(status);
  coordinator.resume = async () => notices.push("resumed");
  return {
    calls, notices, delays,
    run: () => coordinator.poll({ interaction: interaction(), mcpClient: client }, new AbortController().signal),
  };
}

test("transient status-query errors beyond five attempts recover without replaying business tools", async () => {
  const h = harness();
  await h.run();
  assert.equal(h.calls.length, 7);
  assert.ok(h.calls.every((name) => name === "agentbridge_interaction_get"));
  assert.ok(h.delays.includes(30_000));
  assert.deepEqual(h.notices, ["resumed"]);
});

test("non-retryable poll errors still stop at the existing error limit", async () => {
  const h = harness({ retryable: false });
  await h.run();
  assert.equal(h.calls.length, 5);
  assert.deepEqual(h.notices, ["poll_failed"]);
});

test("persistent transport failure stops at the bounded polling deadline", async () => {
  const h = harness({ recover: false, maxPollSeconds: 45 });
  await h.run();
  assert.equal(h.calls.length, 6);
  assert.deepEqual(h.notices, ["poll_expired"]);
});
