import test from "node:test";
import assert from "node:assert/strict";

import { normalizeInteraction } from "../lib/interaction.js";
import { registerAgentBridgeInteractions } from "../lib/plugin.js";
import {
  CARD_ORIGIN,
  CARD_URL,
  interaction,
  openClawPublicResult,
  operationAuditResult,
  toolResult,
} from "./fixtures.js";

test("leaves an ordinary non-interaction tool result untouched", () => {
  const harness = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(harness.api, { mcpClient: null });
  const result = {
    content: [{ type: "text", text: '{"status":"succeeded","count":3}' }],
    details: { structuredContent: { status: "succeeded", count: 3 } },
  };

  const replacement = harness.middleware(
    {
      toolCallId: "tool-plain",
      toolName: "oa_workflow_pending",
      result,
    },
    { runtime: "openclaw" },
  );

  assert.equal(replacement, undefined);
});

test("sanitizes operation audit history without capturing an old card", () => {
  const harness = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(harness.api, { mcpClient: null });
  bindToolCall(harness, {
    toolCallId: "tool-audit",
    runId: "run-audit",
    sessionKey: "agent:main:telegram:direct:7052061588",
  });

  const replacement = harness.middleware(
    {
      toolCallId: "tool-audit",
      toolName: "agentbridge_operation_list",
      result: operationAuditResult(),
    },
    { runtime: "openclaw" },
  );

  assert.equal(JSON.stringify(replacement).includes(CARD_URL), false);
  const reply = harness.hooks.reply_payload_sending(
    {
      kind: "final",
      runId: "run-audit",
      sessionKey: "agent:main:telegram:direct:7052061588",
      channel: "telegram",
      payload: { text: "audit complete" },
    },
    {
      channelId: "telegram",
      sessionKey: "agent:main:telegram:direct:7052061588",
      runId: "run-audit",
    },
  );
  assert.equal(reply, undefined);
});

test("binds a real Telegram direct session before middleware and injects its card", () => {
  const harness = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(harness.api, { mcpClient: null });
  bindToolCall(harness, {
    toolCallId: "tool-1",
    runId: "run-1",
    sessionKey: "agent:main:telegram:direct:7052061588",
  });

  const replacement = harness.middleware(
    {
      toolCallId: "tool-1",
      toolName: "oa_session_login",
      result: toolResult(),
    },
    { runtime: "openclaw" },
  );
  assert.equal(JSON.stringify(replacement).includes(CARD_URL), false);

  const first = harness.hooks.reply_payload_sending(
    {
      kind: "final",
      runId: "run-1",
      sessionKey: "agent:main:telegram:direct:7052061588",
      channel: "telegram",
      payload: { text: "请完成登录。" },
    },
    {
      channelId: "telegram",
      sessionKey: "agent:main:telegram:direct:7052061588",
      runId: "run-1",
    },
  );
  assert.equal(first.payload.presentation.blocks.at(-1).buttons[0].url, CARD_URL);
  assert.equal(first.payload.text, "请完成登录。");

  const second = harness.hooks.reply_payload_sending(
    {
      kind: "final",
      runId: "run-1",
      sessionKey: "agent:main:telegram:direct:7052061588",
      channel: "telegram",
      payload: { text: "重复回复" },
    },
    {
      channelId: "telegram",
      sessionKey: "agent:main:telegram:direct:7052061588",
      runId: "run-1",
    },
  );
  assert.equal(second, undefined);
  assert.deepEqual(harness.middlewareOptions, { runtimes: ["openclaw"] });
});

test("hydrates a trusted card when OpenClaw drops private MCP result metadata", async () => {
  const harness = fakeApi({ autoPoll: false });
  const calls = [];
  const client = {
    async callTool(name, arguments_) {
      calls.push({ name, arguments_ });
      return toolResult();
    },
  };
  registerAgentBridgeInteractions(harness.api, { mcpClient: client });
  bindToolCall(harness, {
    toolCallId: "tool-hydrate",
    runId: "run-hydrate",
    sessionKey: "agent:main:telegram:direct:7052061588",
  });

  const replacement = await harness.middleware(
    {
      toolCallId: "tool-hydrate",
      toolName: "agentbridge__oa_session_login",
      result: openClawPublicResult(),
    },
    { runtime: "openclaw" },
  );

  assert.equal(replacement, undefined);
  assert.deepEqual(calls, [
    {
      name: "agentbridge_interaction_get",
      arguments_: { interaction_id: "interaction-1234567890" },
    },
  ]);
  const reply = harness.hooks.reply_payload_sending(
    {
      kind: "final",
      runId: "run-hydrate",
      sessionKey: "agent:main:telegram:direct:7052061588",
      channel: "telegram",
      payload: { text: "complete login" },
    },
    {
      channelId: "telegram",
      sessionKey: "agent:main:telegram:direct:7052061588",
      runId: "run-hydrate",
    },
  );
  assert.equal(reply.payload.presentation.blocks.at(-1).buttons[0].url, CARD_URL);
});

test("does not hydrate a public interaction reference from another MCP server", async () => {
  const harness = fakeApi({ autoPoll: false });
  let calls = 0;
  const client = {
    async callTool() {
      calls += 1;
      return toolResult();
    },
  };
  registerAgentBridgeInteractions(harness.api, { mcpClient: client });
  bindToolCall(harness, {
    toolCallId: "tool-spoof",
    runId: "run-spoof",
    sessionKey: "agent:main:telegram:direct:7052061588",
  });
  const result = openClawPublicResult();
  result.details.mcpServer = "untrusted-server";

  const replacement = await harness.middleware(
    {
      toolCallId: "tool-spoof",
      toolName: "untrusted__oa_session_login",
      result,
    },
    { runtime: "openclaw" },
  );

  assert.equal(replacement, undefined);
  assert.equal(calls, 0);
});
test("never renders a captured card in a group session", () => {
  const harness = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(harness.api, { mcpClient: null });
  bindToolCall(harness, {
    toolCallId: "tool-group",
    runId: "run-group",
    sessionKey: "agent:main:telegram:group:-100",
  });

  const replacement = harness.middleware(
    {
      toolCallId: "tool-group",
      toolName: "oa_session_login",
      result: toolResult(),
    },
    { runtime: "openclaw" },
  );
  assert.equal(JSON.stringify(replacement).includes(CARD_URL), false);
  const reply = harness.hooks.reply_payload_sending(
    {
      kind: "final",
      runId: "run-group",
      sessionKey: "agent:main:telegram:group:-100",
      channel: "telegram",
      payload: { text: "no card" },
    },
    { channelId: "telegram", sessionKey: "agent:main:telegram:group:-100" },
  );
  assert.equal(reply, undefined);
  assert.equal(harness.logs.warn.some((line) => line.includes("not private")), true);
});

test("withholds an unbound card when result middleware has no session context", () => {
  const harness = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(harness.api, { mcpClient: null });

  const replacement = harness.middleware(
    {
      toolCallId: "tool-unbound",
      toolName: "oa_session_login",
      result: toolResult(),
    },
    { runtime: "openclaw" },
  );

  assert.equal(JSON.stringify(replacement).includes(CARD_URL), false);
  const reply = harness.hooks.reply_payload_sending(
    {
      kind: "final",
      sessionKey: "agent:main:telegram:direct:7052061588",
      channel: "telegram",
      payload: { text: "no card" },
    },
    {
      channelId: "telegram",
      sessionKey: "agent:main:telegram:direct:7052061588",
    },
  );
  assert.equal(reply, undefined);
  assert.equal(
    harness.logs.warn.some((line) => line.includes("session binding")),
    true,
  );
});

test("pending command redraws a previously delivered interaction without a model call", async () => {
  const harness = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(harness.api, { mcpClient: null });
  bindToolCall(harness, {
    toolCallId: "tool-2",
    runId: "run-2",
    sessionKey: "agent:main:main",
  });
  harness.middleware(
    {
      toolCallId: "tool-2",
      toolName: "oa_session_login",
      result: toolResult(),
    },
    { runtime: "openclaw" },
  );

  const result = await harness.command.handler({
    args: "pending",
    channel: "webchat",
    channelId: "webchat",
    sessionKey: "agent:main:main",
  });
  assert.equal(result.presentation.blocks.at(-1).buttons[0].url, CARD_URL);

  const status = await harness.command.handler({
    args: "status",
    channel: "webchat",
    sessionKey: "agent:main:main",
  });
  assert.equal(status.text.includes("待处理交互：1 个"), true);
  assert.equal(status.text.includes(CARD_URL), false);
});

test("polls, resumes once, and queues only a non-sensitive host event", async () => {
  const harness = fakeApi({
    autoPoll: true,
    pollIntervalSeconds: 1,
    wakeAgentOnComplete: false,
  });
  const calls = [];
  const completed = JSON.parse(toolResult().content[0].text).interaction;
  completed.state = "completed";
  completed.resume = {
    tool: "agentbridge_interaction_resume",
    ready: true,
    completed: false,
  };
  const client = {
    async callTool(name, arguments_) {
      calls.push({ name, arguments_ });
      if (name === "agentbridge_interaction_get") {
        return { status: "succeeded", interaction: completed };
      }
      return { status: "succeeded", result: { authenticated: true } };
    },
  };
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: client,
    sleep: async () => {},
  });
  bindToolCall(harness, {
    toolCallId: "tool-3",
    runId: "run-3",
    sessionKey: "agent:main:main",
  });
  harness.middleware(
    {
      toolCallId: "tool-3",
      toolName: "oa_session_login",
      result: toolResult(),
    },
    { runtime: "openclaw" },
  );

  await coordinator.waitForIdle();

  assert.deepEqual(
    calls.map((call) => call.name),
    ["agentbridge_interaction_get", "agentbridge_interaction_resume"],
  );
  assert.equal(harness.systemEvents.length, 1);
  assert.equal(harness.systemEvents[0].text.includes(CARD_URL), false);
  assert.equal(harness.heartbeats.length, 0);
});

test("continues the original request once after credential login succeeds", async () => {
  const harness = fakeApi({
    autoPoll: true,
    pollIntervalSeconds: 1,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  const calls = [];
  const completed = JSON.parse(toolResult().content[0].text).interaction;
  completed.state = "completed";
  completed.resume = {
    tool: "agentbridge_interaction_resume",
    ready: true,
    completed: false,
  };
  const client = {
    async callTool(name, arguments_) {
      calls.push({ name, arguments_ });
      if (name === "agentbridge_interaction_get") {
        return { status: "succeeded", interaction: completed };
      }
      return {
        status: "succeeded",
        result: { authenticated: true },
        nextAction: { type: "retry_original_request" },
      };
    },
  };
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: client,
    sleep: async () => {},
  });
  bindDeliveryRoute(harness, {
    sessionKey,
    to: "7052061588",
  });
  bindToolCall(harness, {
    toolCallId: "tool-login-continuation",
    runId: "run-login-continuation",
    sessionKey,
  });
  harness.middleware(
    {
      toolCallId: "tool-login-continuation",
      toolName: "oa_session_login",
      result: toolResult(),
    },
    { runtime: "openclaw" },
  );

  await coordinator.waitForIdle();

  assert.deepEqual(
    calls.map((call) => call.name),
    ["agentbridge_interaction_get", "agentbridge_interaction_resume"],
  );
  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(harness.sentPayloads[0].to, "7052061588");
  assert.equal(harness.sentPayloads[0].payload.text.includes("AgentBridge"), true);
  assert.equal(
    JSON.stringify(harness.sentPayloads[0].payload).includes(CARD_URL),
    false,
  );
  assert.equal(harness.systemEvents.length, 1);
  assert.equal(
    harness.systemEvents[0].text.includes("继续处理触发本次登录的原始用户请求"),
    true,
  );
  assert.equal(harness.systemEvents[0].text.includes(CARD_URL), false);
  assert.equal(harness.systemEvents[0].options.sessionKey, sessionKey);
  assert.equal(
    harness.systemEvents[0].options.contextKey,
    "agentbridge:continue:" + completed.interactionId,
  );
  assert.equal(harness.heartbeatRuns.length, 1);
  assert.equal(
    harness.heartbeatRuns[0].reason,
    "hook:agentbridge-login-completed",
  );
  assert.equal(harness.heartbeats.length, 0);

  const record = coordinator.records.get(completed.interactionId);
  await coordinator.notify(
    record,
    "succeeded",
    null,
    [],
    { resumeOriginalRequest: true },
  );
  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(harness.systemEvents.length, 1);
  assert.equal(harness.heartbeatRuns.length, 1);
});

test("delivers an already captured field card instead of waking login continuation", async () => {
  const harness = fakeApi({
    autoPoll: true,
    pollIntervalSeconds: 1,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  let releasePoll;
  const pollGate = new Promise((resolve) => {
    releasePoll = resolve;
  });
  const completed = JSON.parse(toolResult().content[0].text).interaction;
  completed.state = "completed";
  completed.resume = {
    tool: "agentbridge_interaction_resume",
    ready: true,
    completed: false,
  };
  const client = {
    async callTool(name) {
      if (name === "agentbridge_interaction_get") {
        return { status: "succeeded", interaction: completed };
      }
      return {
        status: "succeeded",
        result: { authenticated: true },
        nextAction: { type: "retry_original_request" },
      };
    },
  };
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: client,
    sleep: async () => pollGate,
  });
  bindDeliveryRoute(harness, {
    sessionKey,
    to: "7052061588",
  });
  bindToolCall(harness, {
    toolCallId: "tool-login-with-field-card",
    runId: "run-login-with-field-card",
    sessionKey,
  });
  harness.middleware(
    {
      toolCallId: "tool-login-with-field-card",
      toolName: "oa_session_login",
      result: toolResult(),
    },
    { runtime: "openclaw" },
  );

  const fieldUrl = CARD_ORIGIN + "/fields/opaque-field-token";
  const fieldInteraction = normalizeInteraction(
    interaction({
      interactionId: "interaction-field-after-login-123456",
      type: "business_input",
      title: "填写并提交请假申请",
      presentation: { url: fieldUrl },
    }),
    new Set([CARD_ORIGIN]),
  );
  coordinator.upsert({
    interaction: fieldInteraction,
    sessionKey,
    runId: "run-login-with-field-card",
  });

  releasePoll();
  await coordinator.waitForIdle();

  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(
    JSON.stringify(harness.sentPayloads[0].payload).includes(fieldUrl),
    true,
  );
  assert.equal(harness.systemEvents.length, 0);
  assert.equal(harness.heartbeatRuns.length, 0);
  assert.equal(harness.heartbeats.length, 0);
});

test("direct host status delivery cannot consume an undelivered field card", async () => {
  const harness = fakeApi({
    autoPoll: false,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  bindDeliveryRoute(harness, {
    sessionKey,
    to: "7052061588",
  });
  const fieldUrl = CARD_ORIGIN + "/fields/reentrant-field-token";
  const fieldInteraction = normalizeInteraction(
    interaction({
      interactionId: "interaction-reentrant-field-123456",
      type: "business_input",
      title: "填写并提交请假申请",
      presentation: { url: fieldUrl },
    }),
    new Set([CARD_ORIGIN]),
  );
  const record = coordinator.upsert({
    interaction: fieldInteraction,
    sessionKey,
    runId: "run-reentrant-card",
  });

  let nestedReply;
  harness.api.runtime.channel.outbound.loadAdapter = async () => ({
    renderPresentation({ payload }) {
      return payload;
    },
    async sendPayload(context) {
      nestedReply = harness.hooks.reply_payload_sending(
        {
          kind: "block",
          sessionKey,
          channel: "telegram",
          payload: context.payload,
        },
        { sessionKey, channelId: "telegram" },
      );
      harness.sentPayloads.push(context);
      return { channel: "telegram", messageId: "status-message" };
    },
  });

  await coordinator.deliverStatusDirect(
    sessionKey,
    "succeeded",
    null,
    { result: { authenticated: true } },
  );

  assert.equal(nestedReply, undefined);
  assert.equal(record.delivered, false);

  const normalReply = harness.hooks.reply_payload_sending(
    {
      kind: "final",
      runId: "run-reentrant-card",
      sessionKey,
      channel: "telegram",
      payload: { text: "请填写请假信息" },
    },
    { sessionKey, channelId: "telegram" },
  );
  assert.equal(JSON.stringify(normalReply).includes(fieldUrl), true);
  assert.equal(record.delivered, true);
});

test("proactively wakes the private agent and delivers the next trusted card", async () => {
  const harness = fakeApi({
    autoPoll: true,
    pollIntervalSeconds: 1,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  const completed = JSON.parse(toolResult().content[0].text).interaction;
  completed.state = "completed";
  completed.resume = {
    tool: "agentbridge_interaction_resume",
    ready: true,
    completed: false,
  };
  const authorizationUrl = `${CARD_ORIGIN}/authorize/opaque-authorization-token`;
  const authorization = interaction({
    interactionId: "interaction-authorization-123456",
    type: "execution_authorization",
    title: "确认保存 OA 待发草稿",
    presentation: {
      url: authorizationUrl,
    },
  });
  const client = {
    async callTool(name, arguments_, options = {}) {
      if (name === "agentbridge_interaction_get") {
        if (arguments_.interaction_id === authorization.interactionId) {
          return new Promise((resolve) => {
            options.signal.addEventListener(
              "abort",
              () => resolve({ status: "succeeded", interaction: authorization }),
              { once: true },
            );
          });
        }
        return { status: "succeeded", interaction: completed };
      }
      return toolResult(authorization);
    },
  };
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: client,
    sleep: async () => {},
  });
  bindDeliveryRoute(harness, {
    sessionKey,
    to: "7052061588",
  });
  bindToolCall(harness, {
    toolCallId: "tool-proactive",
    runId: "run-original",
    sessionKey,
  });
  harness.middleware(
    {
      toolCallId: "tool-proactive",
      toolName: "oa_business_trip_prepare",
      result: toolResult(),
    },
    { runtime: "openclaw" },
  );

  for (
    let index = 0;
    index < 20 && harness.sentPayloads.length === 0;
    index += 1
  ) {
    await new Promise((resolve) => setImmediate(resolve));
  }

  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(harness.sentPayloads[0].to, "7052061588");
  assert.equal(
    JSON.stringify(harness.sentPayloads[0].payload).includes(authorizationUrl),
    true,
  );
  assert.equal(harness.systemEvents.length, 0);
  assert.equal(harness.heartbeatRuns.length, 0);
  assert.equal(harness.heartbeats.length, 0);
  const idle = coordinator.waitForIdle();
  coordinator.stopAll();
  await idle;
});

test("reports the verified meeting outcome after authorization resumes", async () => {
  const harness = fakeApi({
    autoPoll: true,
    pollIntervalSeconds: 1,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  const pending = interaction({
    interactionId: "interaction-meeting-authorization-123456",
    type: "execution_authorization",
    title: "创建并发送会议",
  });
  const completed = structuredClone(pending);
  completed.state = "completed";
  completed.resume = {
    tool: "agentbridge_interaction_resume",
    ready: true,
    completed: false,
  };
  const calls = [];
  const client = {
    async callTool(name, arguments_) {
      calls.push({ name, arguments_ });
      if (name === "agentbridge_interaction_get") {
        return { status: "succeeded", interaction: completed };
      }
      return {
        status: "succeeded",
        result: {
          meeting_created: true,
          meeting_sent: true,
          submitted_count: 1,
          verification: { confirmed: true },
        },
      };
    },
  };
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: client,
    sleep: async () => {},
  });
  bindDeliveryRoute(harness, {
    sessionKey,
    to: "7052061588",
  });
  bindToolCall(harness, {
    toolCallId: "tool-meeting-authorization",
    runId: "run-meeting-authorization",
    sessionKey,
  });
  harness.middleware(
    {
      toolCallId: "tool-meeting-authorization",
      toolName: "oa_meeting_create_prepare",
      result: toolResult(pending),
    },
    { runtime: "openclaw" },
  );

  await coordinator.waitForIdle();

  assert.deepEqual(
    calls.map((call) => call.name),
    ["agentbridge_interaction_get", "agentbridge_interaction_resume"],
  );
  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(
    harness.sentPayloads[0].payload.text,
    "OA 会议已创建并发送，并已通过回读确认。",
  );
  assert.equal(harness.systemEvents.length, 0);
  assert.equal(harness.heartbeatRuns.length, 0);
});

test("reports a verified business-trip submission after authorization resumes", async () => {
  const harness = fakeApi({
    autoPoll: true,
    pollIntervalSeconds: 1,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  const pending = interaction({
    interactionId: "interaction-trip-submit-authorization-123456",
    type: "execution_authorization",
    title: "提交出差申请",
  });
  const completed = structuredClone(pending);
  completed.state = "completed";
  completed.resume = {
    tool: "agentbridge_interaction_resume",
    ready: true,
    completed: false,
  };
  const calls = [];
  const client = {
    async callTool(name, arguments_) {
      calls.push({ name, arguments_ });
      if (name === "agentbridge_interaction_get") {
        return { status: "succeeded", interaction: completed };
      }
      return {
        status: "succeeded",
        result: {
          business_intent: "submit_business_trip_request",
          workflow_submitted: true,
          submitted_count: 1,
          verification: { confirmed: true },
        },
      };
    },
  };
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: client,
    sleep: async () => {},
  });
  bindDeliveryRoute(harness, { sessionKey, to: "7052061588" });
  bindToolCall(harness, {
    toolCallId: "tool-trip-submit-authorization",
    runId: "run-trip-submit-authorization",
    sessionKey,
  });
  harness.middleware(
    {
      toolCallId: "tool-trip-submit-authorization",
      toolName: "oa_business_trip_submit_prepare",
      result: toolResult(pending),
    },
    { runtime: "openclaw" },
  );

  await coordinator.waitForIdle();

  assert.deepEqual(
    calls.map((call) => call.name),
    ["agentbridge_interaction_get", "agentbridge_interaction_resume"],
  );
  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(
    harness.sentPayloads[0].payload.text,
    "OA 出差申请已提交审批，并已通过已发事项回读确认。",
  );
  assert.equal(harness.systemEvents.length, 0);
  assert.equal(harness.heartbeatRuns.length, 0);
});

test("reports a verified leave submission after authorization resumes", async () => {
  const harness = fakeApi({
    autoPoll: true,
    pollIntervalSeconds: 1,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  const pending = interaction({
    interactionId: "interaction-leave-submit-authorization-123456",
    type: "execution_authorization",
    title: "提交请假申请",
  });
  const completed = structuredClone(pending);
  completed.state = "completed";
  completed.resume = {
    tool: "agentbridge_interaction_resume",
    ready: true,
    completed: false,
  };
  const calls = [];
  const client = {
    async callTool(name, arguments_) {
      calls.push({ name, arguments_ });
      if (name === "agentbridge_interaction_get") {
        return { status: "succeeded", interaction: completed };
      }
      return {
        status: "succeeded",
        result: {
          business_intent: "submit_leave_request",
          workflow_submitted: true,
          submitted_count: 1,
          verification: { confirmed: true },
        },
      };
    },
  };
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: client,
    sleep: async () => {},
  });
  bindDeliveryRoute(harness, { sessionKey, to: "7052061588" });
  bindToolCall(harness, {
    toolCallId: "tool-leave-submit-authorization",
    runId: "run-leave-submit-authorization",
    sessionKey,
  });
  harness.middleware(
    {
      toolCallId: "tool-leave-submit-authorization",
      toolName: "oa_leave_submit_prepare",
      result: toolResult(pending),
    },
    { runtime: "openclaw" },
  );

  await coordinator.waitForIdle();

  assert.deepEqual(
    calls.map((call) => call.name),
    ["agentbridge_interaction_get", "agentbridge_interaction_resume"],
  );
  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(
    harness.sentPayloads[0].payload.text,
    "OA 请假申请已提交审批，并已通过已发事项回读确认。",
  );
  assert.equal(harness.systemEvents.length, 0);
  assert.equal(harness.heartbeatRuns.length, 0);
});
test("delivers a final trusted status directly without waking the model", async () => {
  const harness = fakeApi({
    autoPoll: false,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  bindDeliveryRoute(harness, {
    sessionKey,
    to: "7052061588",
  });

  await coordinator.notify(
    {
      sessionKey,
      interaction: { interactionId: "interaction-completed-123456" },
    },
    "succeeded",
    null,
  );

  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(harness.sentPayloads[0].to, "7052061588");
  assert.equal(
    harness.sentPayloads[0].payload.text,
    "AgentBridge 已完成本次安全操作。",
  );
  assert.equal(harness.systemEvents.length, 0);
  assert.equal(harness.heartbeatRuns.length, 0);
  assert.equal(harness.heartbeats.length, 0);
});

test("explains an unknown OA write result without implying an automatic retry", async () => {
  const harness = fakeApi({
    autoPoll: false,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  bindDeliveryRoute(harness, {
    sessionKey,
    to: "7052061588",
  });

  await coordinator.deliverStatusDirect(
    sessionKey,
    "unknown",
    "RESULT_UNKNOWN",
  );

  assert.equal(harness.sentPayloads.length, 1);
  const text = harness.sentPayloads[0].payload.text;
  assert.equal(text.includes("最终结果未能确认"), true);
  assert.equal(text.includes("不会自动重试"), true);
  assert.equal(text.includes("RESULT_UNKNOWN"), true);
});

test("uses an opaque heartbeat only when direct status delivery is unavailable", async () => {
  const harness = fakeApi({
    autoPoll: false,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });

  await coordinator.notify(
    {
      sessionKey,
      interaction: { interactionId: "interaction-completed-123456" },
    },
    "succeeded",
    null,
  );

  assert.equal(harness.sentPayloads.length, 0);
  assert.equal(harness.systemEvents.length, 1);
  assert.equal(harness.systemEvents[0].text.includes(CARD_URL), false);
  assert.equal(harness.heartbeatRuns.length, 1);
  assert.equal(
    harness.heartbeatRuns[0].reason,
    "hook:agentbridge-interaction-updated",
  );
  assert.equal(harness.heartbeats.length, 0);
});

test("queues a heartbeat fallback when the immediate completion wake is skipped", async () => {
  const harness = fakeApi({
    autoPoll: false,
    __heartbeatResult: { status: "skipped", reason: "flood" },
  });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });

  await coordinator.wakeAgent("agent:main:telegram:direct:7052061588");

  assert.equal(harness.heartbeatRuns.length, 1);
  assert.equal(
    harness.heartbeatRuns[0].reason,
    "hook:agentbridge-interaction-updated",
  );
  assert.equal(harness.heartbeats.length, 1);
  assert.equal(
    harness.heartbeats[0].reason,
    "hook:agentbridge-interaction-updated",
  );
  assert.equal(
    harness.logs.warn.some((line) => line.includes("FLOOD")),
    true,
  );
});

function bindToolCall(harness, { toolCallId, runId, sessionKey }) {
  harness.hooks.before_tool_call(
    {
      toolName: "oa_session_login",
      params: {},
      toolCallId,
      runId,
    },
    {
      channelId: "telegram",
      sessionKey,
      runId,
      toolCallId,
    },
  );
}

function bindDeliveryRoute(harness, { sessionKey, to }) {
  harness.hooks.message_received(
    {
      from: to,
      senderId: to,
      sessionKey,
      content: "测试消息",
    },
    {
      channelId: "telegram",
      conversationId: to,
      sessionKey,
    },
  );
}

function fakeApi(pluginConfig) {
  const hooks = {};
  const logs = { info: [], warn: [] };
  const systemEvents = [];
  const heartbeats = [];
  const heartbeatRuns = [];
  const sentPayloads = [];
  const state = {
    middleware: null,
    middlewareOptions: null,
    command: null,
  };
  const api = {
    pluginConfig: {
      allowedCardOrigins: [CARD_ORIGIN],
      ...pluginConfig,
    },
    config: {},
    logger: {
      info(message) {
        logs.info.push(message);
      },
      warn(message) {
        logs.warn.push(message);
      },
    },
    runtime: {
      channel: {
        outbound: {
          async loadAdapter() {
            return {
              renderPresentation({ payload }) {
                return payload;
              },
              async sendPayload(context) {
                sentPayloads.push(context);
                return { channel: "telegram", messageId: "message-1" };
              },
            };
          },
        },
      },
      system: {
        enqueueSystemEvent(text, options) {
          systemEvents.push({ text, options });
          return true;
        },
        requestHeartbeat(options) {
          heartbeats.push(options);
        },
        async runHeartbeatOnce(options) {
          heartbeatRuns.push(options);
          return pluginConfig.__heartbeatResult || {
            status: "ran",
            durationMs: 1,
          };
        },
      },
    },
    registerAgentToolResultMiddleware(handler, options) {
      state.middleware = handler;
      state.middlewareOptions = options;
    },
    on(name, handler) {
      hooks[name] = handler;
    },
    registerCommand(command) {
      state.command = command;
    },
  };
  return {
    api,
    hooks,
    logs,
    systemEvents,
    heartbeats,
    heartbeatRuns,
    sentPayloads,
    get middleware() {
      return state.middleware;
    },
    get middlewareOptions() {
      return state.middlewareOptions;
    },
    get command() {
      return state.command;
    },
  };
}
