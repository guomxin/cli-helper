import test from "node:test";
import assert from "node:assert/strict";

import { createInteractionSharedState } from "../lib/coordinator.js";
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

test("uses the bound WeChat route when the final reply omits channel metadata", () => {
  const harness = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(harness.api, { mcpClient: null });
  const sessionKey =
    "agent:main:openclaw-weixin:direct:wechat-user-1002@im.wechat";
  bindDeliveryRoute(harness, {
    sessionKey,
    to: "wechat-user-1002@im.wechat",
    channel: "openclaw-weixin",
    accountId: "wechat-bot-account",
  });
  bindToolCall(harness, {
    toolCallId: "tool-wechat-missing-reply-channel",
    runId: "run-wechat-missing-reply-channel",
    sessionKey,
    channel: "openclaw-weixin",
  });
  harness.middleware(
    {
      toolCallId: "tool-wechat-missing-reply-channel",
      toolName: "oa_session_login",
      result: toolResult(),
    },
    { runtime: "openclaw" },
  );

  const reply = harness.hooks.reply_payload_sending(
    {
      kind: "final",
      runId: "run-wechat-missing-reply-channel",
      sessionKey,
      payload: { text: "login card opened" },
    },
    {
      sessionKey,
      runId: "run-wechat-missing-reply-channel",
    },
  );

  assert.equal(reply.payload.text.includes(CARD_URL), true);
  assert.equal(reply.payload.text.includes("login card opened"), true);
});

test("recovers WeChat presentation from a private session key without a bound route", () => {
  const harness = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(harness.api, { mcpClient: null });
  const sessionKey =
    "agent:main:openclaw-weixin:direct:wechat-user-1002@im.wechat";
  bindToolCall(harness, {
    toolCallId: "tool-wechat-session-fallback",
    runId: "run-wechat-session-fallback",
    sessionKey,
    channel: "openclaw-weixin",
  });
  harness.middleware(
    {
      toolCallId: "tool-wechat-session-fallback",
      toolName: "oa_session_login",
      result: toolResult(),
    },
    { runtime: "openclaw" },
  );

  const reply = harness.hooks.reply_payload_sending(
    {
      kind: "final",
      runId: "run-wechat-session-fallback",
      sessionKey,
      payload: { text: "login card opened" },
    },
    {
      sessionKey,
      runId: "run-wechat-session-fallback",
    },
  );

  assert.equal(reply.payload.text.includes(CARD_URL), true);
  assert.equal(reply.payload.text.includes("login card opened"), true);
});

test("shares a captured WeChat card from the agent runtime with the gateway reply hook", () => {
  const sharedState = createInteractionSharedState();
  const gateway = fakeApi({ autoPoll: false });
  const runtime = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(gateway.api, { mcpClient: null, sharedState });
  registerAgentBridgeInteractions(runtime.api, { mcpClient: null, sharedState });
  const sessionKey =
    "agent:main:openclaw-weixin:direct:wechat-user-1002@im.wechat";
  bindDeliveryRoute(gateway, {
    sessionKey,
    to: "wechat-user-1002@im.wechat",
    channel: "openclaw-weixin",
    accountId: "wechat-bot-account",
  });
  bindToolCall(runtime, {
    toolCallId: "tool-wechat-cross-instance",
    runId: "run-wechat-cross-instance",
    sessionKey,
    channel: "openclaw-weixin",
  });
  runtime.middleware(
    {
      toolCallId: "tool-wechat-cross-instance",
      toolName: "oa_session_login",
      result: toolResult(),
    },
    { runtime: "openclaw" },
  );

  const reply = gateway.hooks.reply_payload_sending(
    {
      kind: "final",
      runId: "different-outbound-run",
      payload: { text: "login card opened" },
    },
    {
      channelId: "openclaw-weixin",
      accountId: "wechat-bot-account",
      conversationId: "wechat-user-1002@im.wechat",
      runId: "different-outbound-run",
    },
  );

  assert.equal(reply.payload.text.includes(CARD_URL), true);
  assert.equal(reply.payload.text.includes("login card opened"), true);
});

test("appends a captured card in message_sending when WeChat skips the reply payload hook", () => {
  const sharedState = createInteractionSharedState();
  const gateway = fakeApi({ autoPoll: false });
  const runtime = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(gateway.api, { mcpClient: null, sharedState });
  registerAgentBridgeInteractions(runtime.api, { mcpClient: null, sharedState });
  const sessionKey =
    "agent:main:openclaw-weixin:direct:wechat-user-1002@im.wechat";
  bindDeliveryRoute(gateway, {
    sessionKey,
    to: "wechat-user-1002@im.wechat",
    channel: "openclaw-weixin",
    accountId: "wechat-bot-account",
  });
  bindToolCall(runtime, {
    toolCallId: "tool-wechat-message-sending",
    runId: "run-wechat-message-sending",
    sessionKey,
    channel: "openclaw-weixin",
  });
  runtime.middleware(
    {
      toolCallId: "tool-wechat-message-sending",
      toolName: "oa_session_login",
      result: toolResult(),
    },
    { runtime: "openclaw" },
  );

  const sent = gateway.hooks.message_sending(
    {
      to: "wechat-user-1002@im.wechat",
      content: "login card opened",
    },
    {
      channelId: "openclaw-weixin",
      accountId: "wechat-bot-account",
      conversationId: "wechat-user-1002@im.wechat",
    },
  );

  assert.equal(sent.content.includes(CARD_URL), true);
  assert.equal(sent.content.includes("login card opened"), true);
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

test("replays the exact pending-list read after credential login", async () => {
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
      if (name === "agentbridge_interaction_resume") {
        return {
          status: "succeeded",
          result: { authenticated: true },
          nextAction: { type: "retry_original_request" },
        };
      }
      assert.equal(name, "oa_workflow_pending_list");
      return {
        status: "succeeded",
        result: {
          collection: "pending",
          count: 1,
          items: [
            {
              affair_id: "pending-123",
              title: "Quarterly report",
              sender: "Alice",
              date: "2026-07-26",
              read: false,
            },
          ],
        },
      };
    },
  };
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: client,
    sleep: async () => {},
  });
  bindDeliveryRoute(harness, { sessionKey, to: "7052061588" });

  const loginRequired = {
    protocolVersion: "0.1",
    status: "requires_user_action",
    error: { code: "LOGIN_REQUIRED" },
    nextAction: { type: "session_login" },
  };
  bindToolCall(harness, {
    toolCallId: "tool-pending-before-login",
    runId: "run-pending-before-login",
    sessionKey,
    toolName: "oa_workflow_pending_list",
    params: { limit: 7, idempotency_key: "must-not-be-replayed" },
  });
  harness.middleware(
    {
      toolCallId: "tool-pending-before-login",
      toolName: "oa_workflow_pending_list",
      result: {
        content: [{ type: "text", text: JSON.stringify(loginRequired) }],
        details: {
          mcpServer: "agentbridge",
          mcpTool: "oa_workflow_pending_list",
          structuredContent: loginRequired,
        },
      },
    },
    { runtime: "openclaw" },
  );
  bindToolCall(harness, {
    toolCallId: "tool-login-for-pending",
    runId: "run-pending-before-login",
    sessionKey,
  });
  harness.middleware(
    {
      toolCallId: "tool-login-for-pending",
      toolName: "oa_session_login",
      result: toolResult(),
    },
    { runtime: "openclaw" },
  );

  await coordinator.waitForIdle();

  assert.deepEqual(
    calls.map((call) => call.name),
    [
      "agentbridge_interaction_get",
      "agentbridge_interaction_resume",
      "oa_workflow_pending_list",
    ],
  );
  assert.deepEqual(calls.at(-1).arguments_, { limit: 7 });
  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(
    harness.sentPayloads[0].payload.text.includes("Quarterly report"),
    true,
  );
  assert.equal(harness.heartbeatRuns.length, 0);
});

test("replays a filtered Taihua team work-log read after credential login", async () => {
  const harness = fakeApi({
    autoPoll: true,
    pollIntervalSeconds: 1,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:wechat:direct:taihua-user";
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
      if (name === "agentbridge_interaction_resume") {
        return {
          status: "succeeded",
          result: { authenticated: true },
          nextAction: { type: "retry_original_request" },
        };
      }
      assert.equal(name, "taihua_work_log_team_list");
      return {
        status: "succeeded",
        result: {
          count: 1,
          items: [
            {
              id: "work-log-1",
              logDate: "2026-07-26",
              hours: 8,
              content: "完成 AgentBridge 泰华系统适配。",
              projectName: "AgentBridge",
            },
          ],
        },
      };
    },
  };
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: client,
    sleep: async () => {},
  });
  bindDeliveryRoute(harness, { sessionKey, to: "taihua-user", channel: "wechat" });

  const loginRequired = {
    protocolVersion: "0.1",
    status: "requires_user_action",
    error: { code: "LOGIN_REQUIRED" },
    nextAction: { type: "session_login", system: "taihua" },
  };
  bindToolCall(harness, {
    toolCallId: "tool-taihua-before-login",
    runId: "run-taihua-before-login",
    sessionKey,
    toolName: "taihua_work_log_team_list",
    params: {
      start_date: "2026-07-20",
      end_date: "2026-07-26",
      member: "刘大扬",
      department: "山东泰华照明科技有限公司",
      watch_group: "重点关注",
      page: 1,
      size: 30,
      view_mode: "logDate",
      dept_id: 300000101,
      member_id: 300000881,
      watch_group_id: 9,
      idempotency_key: "must-not-be-replayed",
    },
  });
  harness.middleware(
    {
      toolCallId: "tool-taihua-before-login",
      toolName: "taihua_work_log_team_list",
      result: {
        content: [{ type: "text", text: JSON.stringify(loginRequired) }],
        details: {
          mcpServer: "agentbridge",
          mcpTool: "taihua_work_log_team_list",
          structuredContent: loginRequired,
        },
      },
    },
    { runtime: "openclaw" },
  );
  bindToolCall(harness, {
    toolCallId: "tool-login-for-taihua",
    runId: "run-taihua-before-login",
    sessionKey,
  });
  harness.middleware(
    {
      toolCallId: "tool-login-for-taihua",
      toolName: "taihua_session_login",
      result: toolResult(),
    },
    { runtime: "openclaw" },
  );

  await coordinator.waitForIdle();

  assert.deepEqual(
    calls.map((call) => call.name),
    [
      "agentbridge_interaction_get",
      "agentbridge_interaction_resume",
      "taihua_work_log_team_list",
    ],
  );
  assert.deepEqual(calls.at(-1).arguments_, {
    start_date: "2026-07-20",
    end_date: "2026-07-26",
    member: "刘大扬",
    department: "山东泰华照明科技有限公司",
    watch_group: "重点关注",
    page: 1,
    size: 30,
    view_mode: "logDate",
    dept_id: 300000101,
    member_id: 300000881,
    watch_group_id: 9,
  });
  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(
    harness.sentPayloads[0].payload.text.includes("AgentBridge 泰华系统适配"),
    true,
  );
  assert.equal(
    harness.sentPayloads[0].payload.text.includes("泰华日志系统 登录已恢复"),
    true,
  );
});
test("infers a sent-list continuation when login is the first tool", async () => {
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
      if (name === "agentbridge_interaction_resume") {
        return {
          status: "succeeded",
          result: { authenticated: true },
          nextAction: { type: "retry_original_request" },
        };
      }
      return {
        status: "succeeded",
        result: { collection: "sent", count: 0, items: [] },
      };
    },
  };
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: client,
    sleep: async () => {},
  });
  bindDeliveryRoute(harness, { sessionKey, to: "7052061588" });
  harness.hooks.message_received(
    {
      sessionKey,
      channel: "telegram",
      senderId: "7052061588",
      from: "7052061588",
      content: "\u767b\u5f55 OA \u5e76\u67e5\u770b\u8fd1 10 \u6761\u5df2\u53d1\u4e8b\u9879",
    },
    { channelId: "telegram", sessionKey, conversationId: "7052061588" },
  );
  bindToolCall(harness, {
    toolCallId: "tool-login-first",
    runId: "run-login-first",
    sessionKey,
  });
  harness.middleware(
    {
      toolCallId: "tool-login-first",
      toolName: "oa_session_login",
      result: toolResult(),
    },
    { runtime: "openclaw" },
  );

  await coordinator.waitForIdle();

  assert.equal(calls.at(-1).name, "oa_workflow_sent_list");
  assert.deepEqual(calls.at(-1).arguments_, { limit: 10 });
  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(harness.heartbeatRuns.length, 0);
});

test("does not infer a work-log read continuation from a write request", async () => {
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
      assert.equal(name, "agentbridge_interaction_resume");
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
  bindDeliveryRoute(harness, { sessionKey, to: "7052061588" });
  harness.hooks.message_received(
    {
      sessionKey,
      channel: "telegram",
      senderId: "7052061588",
      from: "7052061588",
      content:
        "填写今日我的日志，内容：AgentBridge日志系统修改保持用户登录机制；" +
        "支持按用户、项目和正文关键词及时间段查询，工时：3小时",
    },
    { channelId: "telegram", sessionKey, conversationId: "7052061588" },
  );
  bindToolCall(harness, {
    toolCallId: "tool-taihua-write-login-first",
    runId: "run-taihua-write-login-first",
    sessionKey,
    toolName: "taihua_session_login",
  });
  harness.middleware(
    {
      toolCallId: "tool-taihua-write-login-first",
      toolName: "taihua_session_login",
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
  assert.equal(
    harness.systemEvents[0].text.includes("继续处理触发本次登录的原始用户请求"),
    true,
  );
  assert.equal(harness.heartbeatRuns.length, 1);
  assert.equal(
    harness.heartbeatRuns[0].reason,
    "hook:agentbridge-login-completed",
  );
});
test("delivers a field card captured during the login continuation heartbeat", async () => {
  const sessionKey = "agent:main:telegram:direct:7052061588";
  const fieldUrl = CARD_ORIGIN + "/fields/continuation-field-token";
  const fieldInteraction = normalizeInteraction(
    interaction({
      interactionId: "interaction-field-during-continuation-123456",
      type: "business_input",
      title: "填写并提交请假申请",
      presentation: { url: fieldUrl },
    }),
    new Set([CARD_ORIGIN]),
  );
  let coordinator;
  const harness = fakeApi({
    autoPoll: true,
    pollIntervalSeconds: 1,
    wakeAgentOnComplete: true,
    async __heartbeatHandler() {
      coordinator.upsert({
        interaction: fieldInteraction,
        sessionKey,
        runId: "run-login-continuation-next-card",
      });
      return { status: "ran", durationMs: 1 };
    },
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
  coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: client,
    sleep: async () => {},
  });
  bindDeliveryRoute(harness, { sessionKey, to: "7052061588" });
  bindToolCall(harness, {
    toolCallId: "tool-login-continuation-next-card",
    runId: "run-login-original",
    sessionKey,
  });
  harness.middleware(
    {
      toolCallId: "tool-login-continuation-next-card",
      toolName: "oa_session_login",
      result: toolResult(),
    },
    { runtime: "openclaw" },
  );

  await coordinator.waitForIdle();

  assert.equal(harness.sentPayloads.length, 2);
  assert.equal(
    JSON.stringify(harness.sentPayloads[0].payload).includes(fieldUrl),
    false,
  );
  assert.equal(
    JSON.stringify(harness.sentPayloads[1].payload).includes(fieldUrl),
    true,
  );
  assert.equal(
    coordinator.records.get(fieldInteraction.interactionId).delivered,
    true,
  );
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

test("reports a verified weekly-report acknowledgement after authorization resumes", async () => {
  const harness = fakeApi({
    autoPoll: true,
    pollIntervalSeconds: 1,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  const pending = interaction({
    interactionId: "interaction-weekly-report-authorization-123456",
    type: "execution_authorization",
    title: "Acknowledge weekly report",
  });
  const completed = structuredClone(pending);
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
        result: {
          pending_action_processed: true,
          action_kind: "acknowledgement",
          workflow_profile: "weekly_report",
          workflow_acknowledged: true,
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
    toolCallId: "tool-weekly-report-authorization",
    runId: "run-weekly-report-authorization",
    sessionKey,
  });
  harness.middleware(
    {
      toolCallId: "tool-weekly-report-authorization",
      toolName: "oa_weekly_report_acknowledgement_prepare",
      result: toolResult(pending),
    },
    { runtime: "openclaw" },
  );

  await coordinator.waitForIdle();

  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(
    harness.sentPayloads[0].payload.text,
    "OA \u5468\u62a5\u53d1\u9001\u6d41\u7a0b\u5df2\u9605\u529e\uff0c\u5e76\u5df2\u901a\u8fc7\u5f85\u529e\u56de\u8bfb\u786e\u8ba4\u3002",
  );
});

test("reports a verified labor-contract renewal approval after authorization resumes", async () => {
  const harness = fakeApi({
    autoPoll: true,
    pollIntervalSeconds: 1,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  const pending = interaction({
    interactionId: "interaction-labor-contract-authorization-123456",
    type: "execution_authorization",
    title: "Approve labor contract renewal",
  });
  const completed = structuredClone(pending);
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
        result: {
          pending_action_processed: true,
          action_kind: "approval",
          workflow_profile: "labor_contract_renewal",
          workflow_approved: true,
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
    toolCallId: "tool-labor-contract-authorization",
    runId: "run-labor-contract-authorization",
    sessionKey,
  });
  harness.middleware(
    {
      toolCallId: "tool-labor-contract-authorization",
      toolName: "oa_labor_contract_renewal_approval_prepare",
      result: toolResult(pending),
    },
    { runtime: "openclaw" },
  );

  await coordinator.waitForIdle();

  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(
    harness.sentPayloads[0].payload.text,
    "OA \u52b3\u52a8\u5408\u540c\u7eed\u7b7e\u8868\u5df2\u5ba1\u6279\u901a\u8fc7\uff0c\u5e76\u5df2\u901a\u8fc7\u5f85\u529e\u56de\u8bfb\u786e\u8ba4\u3002",
  );
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
test("reports a verified workflow revoke after authorization resumes", async () => {
  const harness = fakeApi({
    autoPoll: true,
    pollIntervalSeconds: 1,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  const pending = interaction({
    interactionId: "interaction-workflow-revoke-authorization-123456",
    type: "execution_authorization",
    title: "撤销已发流程",
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
          business_intent: "revoke_sent_workflow",
          workflow_revoked: true,
          revoked_count: 1,
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
    toolCallId: "tool-workflow-revoke-authorization",
    runId: "run-workflow-revoke-authorization",
    sessionKey,
  });
  harness.middleware(
    {
      toolCallId: "tool-workflow-revoke-authorization",
      toolName: "oa_workflow_revoke_prepare",
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
    "OA 已发流程已撤销，并已通过已发消失及待发撤销状态回读确认。",
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

test("delivers a final trusted status through a text-only channel adapter", async () => {
  const harness = fakeApi({
    autoPoll: false,
    wakeAgentOnComplete: true,
  });
  const sentTexts = [];
  harness.api.runtime.channel.outbound.loadAdapter = async () => ({
    async sendText(context) {
      sentTexts.push(context);
      return { channel: "openclaw-weixin", messageId: "message-1" };
    },
  });
  const sessionKey = "agent:main:openclaw-weixin:direct:wechat-user-1";
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  bindDeliveryRoute(harness, {
    sessionKey,
    channel: "openclaw-weixin",
    to: "wechat-user-1",
  });

  await coordinator.notify(
    {
      sessionKey,
      interaction: { interactionId: "interaction-completed-123456" },
    },
    "succeeded",
    null,
  );

  assert.equal(sentTexts.length, 1);
  assert.equal(sentTexts[0].to, "wechat-user-1");
  assert.equal(
    sentTexts[0].text,
    "AgentBridge \u5df2\u5b8c\u6210\u672c\u6b21\u5b89\u5168\u64cd\u4f5c\u3002",
  );
  assert.equal(harness.systemEvents.length, 0);
  assert.equal(harness.heartbeatRuns.length, 0);
  assert.equal(harness.heartbeats.length, 0);
});

test("delivers the next trusted card through a text-only channel adapter", async () => {
  const harness = fakeApi({
    autoPoll: false,
    wakeAgentOnComplete: true,
  });
  const sentTexts = [];
  harness.api.runtime.channel.outbound.loadAdapter = async () => ({
    async sendText(context) {
      sentTexts.push(context);
      return { channel: "openclaw-weixin", messageId: "message-1" };
    },
  });
  const sessionKey = "agent:main:openclaw-weixin:direct:wechat-user-1";
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  bindDeliveryRoute(harness, {
    sessionKey,
    channel: "openclaw-weixin",
    to: "wechat-user-1",
  });
  const authorizationUrl = `${CARD_ORIGIN}/authorize/opaque-authorization-token`;
  const authorization = interaction({
    interactionId: "interaction-authorization-text-only-123456",
    type: "execution_authorization",
    title: "Confirm business trip submission",
    presentation: { url: authorizationUrl },
  });

  const delivered = await coordinator.deliverInteractionsDirect(sessionKey, [authorization]);

  assert.equal(delivered, true);
  assert.equal(sentTexts.length, 1);
  assert.equal(sentTexts[0].to, "wechat-user-1");
  assert.equal(sentTexts[0].text.includes(authorizationUrl), true);
  assert.equal(harness.systemEvents.length, 0);
});

test("reports verified Taihua work-log success and business rejection", async () => {
  const harness = fakeApi({
    autoPoll: false,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:openclaw-weixin:direct:taihua-user";
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  bindDeliveryRoute(harness, {
    sessionKey,
    to: "taihua-user",
  });

  await coordinator.deliverStatusDirect(sessionKey, "succeeded", null, {
    result: {
      status: "created",
      workLog: { id: "work-log-1" },
      verification: { matched: true },
    },
  });
  await coordinator.deliverStatusDirect(
    sessionKey,
    "failed",
    "TAIHUA_BUSINESS_RULE_REJECTED",
    {
      error: {
        code: "TAIHUA_BUSINESS_RULE_REJECTED",
        message: "该日期已有工作日志。",
      },
    },
  );

  assert.equal(
    harness.sentPayloads[0].payload.text.includes("泰华工作日志已正式提交"),
    true,
  );
  assert.equal(
    harness.sentPayloads[0].payload.text.includes("回读确认"),
    true,
  );
  assert.equal(
    harness.sentPayloads[1].payload.text.includes("该日期已有工作日志"),
    true,
  );
  assert.equal(
    harness.sentPayloads[1].payload.text.includes("TAIHUA_BUSINESS_RULE_REJECTED"),
    true,
  );
});
test("reports a sanitized OA business-rule rejection and hides generic failures", async () => {
  const harness = fakeApi({
    autoPoll: false,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:openclaw-weixin:direct:wechat-user-1";
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  bindDeliveryRoute(harness, {
    sessionKey,
    to: "wechat-user-1",
  });

  await coordinator.deliverStatusDirect(
    sessionKey,
    "failed",
    "OA_BUSINESS_RULE_REJECTED",
    {
      error: {
        code: "OA_BUSINESS_RULE_REJECTED",
        message: "<b>The selected interval is not eligible.</b> https://oa.example.test/private",
      },
    },
  );

  assert.equal(harness.sentPayloads.length, 1);
  const text = harness.sentPayloads[0].payload.text;
  assert.equal(text.includes("OA_BUSINESS_RULE_REJECTED"), true);
  assert.equal(text.includes("The selected interval is not eligible."), true);
  assert.equal(text.includes("\u94fe\u63a5\u5df2\u9690\u85cf"), true);
  assert.equal(text.includes("<b>"), false);
  assert.equal(text.includes("https://oa.example.test/private"), false);

  await coordinator.deliverStatusDirect(
    sessionKey,
    "failed",
    "CAPABILITY_EXECUTION_FAILED",
    { error: { code: "CAPABILITY_EXECUTION_FAILED", message: "internal secret" } },
  );
  const genericText = harness.sentPayloads[1].payload.text;
  assert.equal(genericText.includes("internal secret"), false);
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

test("delivers a prepared OA certificate as one direct attachment message", async () => {
  const harness = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
    ...preparedDocumentDependencies(),
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  bindDeliveryRoute(harness, { sessionKey, to: "7052061588" });
  bindToolCall(harness, {
    toolCallId: "tool-certificate-delivery",
    runId: "run-certificate-delivery",
    sessionKey,
    toolName: "oa_certificate_prepare_download",
  });

  const replacement = await harness.middleware(
    {
      toolCallId: "tool-certificate-delivery",
      toolName: "oa_certificate_prepare_download",
      result: preparedDocumentResult("certificate-a.pdf", "a".repeat(43)),
    },
    { runtime: "openclaw", sessionKey },
  );

  assert.equal(replacement, undefined);
  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(harness.sentPayloads[0].payload.mediaUrl, "C:/media/certificate.bin");
  assert.match(harness.sentPayloads[0].payload.text, /certificate-a\.pdf/);
});

test("falls back to a short-lived download link when attachment upload fails", async () => {
  const harness = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
    ...preparedDocumentDependencies(),
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  bindDeliveryRoute(harness, { sessionKey, to: "7052061588" });
  let attempts = 0;
  harness.api.runtime.channel.outbound.loadAdapter = async () => ({
    async sendPayload(context) {
      attempts += 1;
      if (context.payload.mediaUrl) {
        throw new Error("telegram upload failed");
      }
      harness.sentPayloads.push(context);
      return { channel: "telegram", messageId: "fallback-message" };
    },
  });
  bindToolCall(harness, {
    toolCallId: "tool-certificate-fallback",
    runId: "run-certificate-fallback",
    sessionKey,
    toolName: "oa_certificate_prepare_download",
  });

  await harness.middleware(
    {
      toolCallId: "tool-certificate-fallback",
      toolName: "oa_certificate_prepare_download",
      result: preparedDocumentResult("certificate-b.jpg", "b".repeat(43)),
    },
    { runtime: "openclaw", sessionKey },
  );

  assert.equal(attempts, 2);
  assert.equal(harness.sentPayloads.length, 1);
  assert.match(harness.sentPayloads[0].payload.text, /附件上传失败/);
  assert.match(harness.sentPayloads[0].payload.text, /\/file/);
});

function preparedDocumentDependencies() {
  return {
    documentFetchImpl: async () => ({
      ok: true,
      status: 200,
      headers: {
        get(name) {
          return name.toLowerCase() === "content-type"
            ? "application/pdf"
            : name.toLowerCase() === "content-length"
              ? "21"
              : null;
        },
      },
      async arrayBuffer() {
        return Buffer.from("%PDF-1.7 prepared");
      },
    }),
    saveMediaBufferImpl: async () => ({
      id: "certificate.bin",
      path: "C:/media/certificate.bin",
      size: 21,
      contentType: "application/pdf",
    }),
  };
}

function preparedDocumentResult(filename, downloadId) {
  const structuredContent = {
    protocolVersion: "0.1",
    schemaVersion: "agentbridge.document_delivery.v1",
    status: "succeeded",
    file: {
      downloadId,
      filename,
      contentType: filename.endsWith(".pdf") ? "application/pdf" : "image/jpeg",
      size: 128,
      mediaUrl: `${CARD_ORIGIN}/download/${downloadId}/file`,
      expiresAt: "2099-07-14T12:00:00+00:00",
    },
  };
  return {
    content: [{ type: "text", text: JSON.stringify(structuredContent) }],
    details: {
      mcpServer: "agentbridge",
      mcpTool: "oa_certificate_prepare_download",
      structuredContent,
    },
  };
}

function bindToolCall(
  harness,
  {
    toolCallId,
    runId,
    sessionKey,
    channel = "telegram",
    toolName = "oa_session_login",
    params = {},
  },
) {
  harness.hooks.before_tool_call(
    {
      toolName,
      params,
      toolCallId,
      runId,
    },
    {
      channelId: channel,
      sessionKey,
      runId,
      toolCallId,
    },
  );
}

function bindDeliveryRoute(
  harness,
  { sessionKey, to, channel = "telegram", accountId = null },
) {
  harness.hooks.message_received(
    {
      from: to,
      senderId: to,
      sessionKey,
      accountId,
      content: "测试消息",
    },
    {
      channelId: channel,
      conversationId: to,
      sessionKey,
      accountId,
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
          if (typeof pluginConfig.__heartbeatHandler === "function") {
            return pluginConfig.__heartbeatHandler(options);
          }
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
