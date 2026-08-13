import test from "node:test";
import assert from "node:assert/strict";

import {
  createInteractionSharedState,
  notificationPumpDelay,
} from "../lib/coordinator.js";
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

test("injects bounded same-user context only for explicit cross-end references", async () => {
  const calls = [];
  const routeContexts = [];
  const sessionKey = "agent:main:telegram:direct:user-a";
  const client = {
    async callTool(name, arguments_, options) {
      calls.push({ name, arguments_, options });
      return {
        status: "succeeded",
        entries: [
          {
            sequence: 40,
            role: "user",
            text: "读取我的 OA 待办",
            source: { clientType: "web", label: "Agent Workspace" },
          },
          {
            sequence: 41,
            role: "assistant",
            text: "第 1 条 affair_id 是 affair-new-123",
            source: { clientType: "web", label: "Agent Workspace" },
          },
        ],
      };
    },
  };
  const identity = {
    bound: true,
    binding: { key: "telegram:*:user-a" },
    client,
  };
  const identityRouter = {
    enabled: true,
    resolveToolContext(context) {
      routeContexts.push(context);
      return identity;
    },
    endpointKeyForSession(value) {
      return value === sessionKey ? "telegram:*:user-a" : null;
    },
    clientForSession() {
      return client;
    },
    removeSession() {},
  };
  const harness = fakeApi({ autoPoll: false, syncTimeline: true });
  registerAgentBridgeInteractions(harness.api, { identityRouter });
  const context = {
    sessionKey,
    messageProvider: "openclaw",
    channel: "cli",
    channelId: "user-a",
    chatId: "control-plane-chat",
    senderId: "control-plane-sender",
  };

  const sameEndpoint = await harness.hooks.before_prompt_build(
    { prompt: "查看第 1 条详情", messages: [] },
    context,
  );
  const crossEndpoint = await harness.hooks.before_prompt_build(
    {
      prompt: "查看刚才网页端列表中的第 1 条详情",
      messages: [],
    },
    context,
  );

  assert.equal(sameEndpoint, undefined);
  assert.equal(calls.length, 1);
  assert.equal(routeContexts.length, 1);
  assert.equal(routeContexts[0].messageChannel, "telegram");
  assert.equal(routeContexts[0].requesterSenderId, null);
  assert.equal(routeContexts[0].agentAccountId, null);
  assert.equal(calls[0].name, "agentbridge_host_cross_endpoint_context");
  assert.deepEqual(calls[0].arguments_, {
    agent_host: "openclaw",
    endpoint_key: "telegram:*:user-a",
    max_age_minutes: 360,
    limit: 12,
  });
  assert.match(crossEndpoint.prependContext, /untrusted conversation data/);
  assert.match(crossEndpoint.prependContext, /affair-new-123/);
  assert.match(crossEndpoint.prependContext, /Agent Workspace/);
});

test("persists ambiguous task choices and blocks duplicate work after selection", async () => {
  const calls = [];
  let businessCalls = 0;
  const sessionKey = "agent:main:telegram:direct:user-a";
  const client = {
    async callTool(name, arguments_) {
      calls.push({ name, arguments_ });
      if (name !== "agentbridge_host_task_continuation_resolve") {
        return { status: "succeeded" };
      }
      if (arguments_.ordinal === 2) {
        return {
          status: "selected",
          task: {
            taskId: "12345678-1234-4123-8123-123456789012",
            title: "Read OA sent workflows",
            status: "succeeded",
          },
          continuation: {
            state: "selected",
            executionMode: "observe_only",
            allowNewOperation: false,
            expiresAt: "2099-08-03T12:00:00+00:00",
          },
          snapshot: {
            summary: {
              phase: "terminal",
              origin: { clientType: "web", label: "Agent Workspace" },
              operation: {
                operationId: "operation-sent-list",
                capability: "oa.workflow.sent.list",
                status: "succeeded",
              },
            },
          },
        };
      }
      return {
        status: "ambiguous",
        count: 2,
        candidates: [
          {
            ordinal: 1,
            taskId: "12345678-1234-4123-8123-123456789011",
            title: "Read OA pending workflows",
            status: "succeeded",
            origin: { clientType: "web", label: "Agent Workspace" },
            updatedAt: "2026-08-03T10:00:00+00:00",
          },
          {
            ordinal: 2,
            taskId: "12345678-1234-4123-8123-123456789012",
            title: "Read OA sent workflows",
            status: "succeeded",
            origin: { clientType: "web", label: "Agent Workspace" },
            updatedAt: "2026-08-03T09:00:00+00:00",
          },
        ],
      };
    },
    async callToolResult() {
      businessCalls += 1;
      return { structuredContent: { status: "succeeded" } };
    },
  };
  const identity = {
    bound: true,
    binding: { key: "telegram:*:user-a" },
    client,
  };
  const identityRouter = {
    enabled: true,
    resolveToolContext() {
      return identity;
    },
    endpointKeyForSession(value) {
      return value === sessionKey ? "telegram:*:user-a" : null;
    },
    clientForSession() {
      return client;
    },
    removeSession() {},
  };
  const harness = fakeApi({ autoPoll: false, syncTimeline: true });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    identityRouter,
  });
  const context = {
    sessionKey,
    messageProvider: "telegram",
    senderId: "user-a",
    chatId: "user-a",
  };

  const choices = await harness.hooks.before_prompt_build(
    { prompt: "\u7ee7\u7eed\u4e4b\u524d\u7684\u4efb\u52a1", messages: [] },
    context,
  );
  assert.match(choices.prependContext, /choice=1/);
  assert.match(choices.prependContext, /choice=2/);

  const selected = await harness.hooks.before_prompt_build(
    { prompt: "\u7b2c\u4e8c\u4e2a\u4efb\u52a1", messages: [] },
    context,
  );
  assert.match(
    selected.prependContext,
    /12345678-1234-4123-8123-123456789012/,
  );
  assert.equal(
    coordinator.taskContinuationForSession(sessionKey).allowNewOperation,
    false,
  );
  const tools = harness.toolFactory({
    sessionKey,
    messageChannel: "telegram",
    requesterSenderId: "user-a",
    runId: "run-observe-only",
  });
  const pending = tools.find(
    (tool) => tool.name === "oa_workflow_pending_list",
  );
  const blocked = await pending.execute("tool-observe-only", { limit: 5 });
  assert.equal(
    blocked.details.structuredContent.error.code,
    "TASK_CONTINUATION_OBSERVE_ONLY",
  );
  assert.equal(businessCalls, 0);
  assert.deepEqual(
    calls
      .filter((call) => call.name === "agentbridge_host_task_continuation_resolve")
      .map((call) => call.arguments_.ordinal),
    [null, 2],
  );
});

test("does not treat an in-request business task sequence as host task continuation", async () => {
  const calls = [];
  const sessionKey = "agent:main:telegram:direct:user-a";
  const client = {
    async callTool(name, arguments_) {
      calls.push({ name, arguments_ });
      throw new Error(`unexpected tool: ${name}`);
    },
  };
  const identity = {
    bound: true,
    binding: { key: "telegram:*:user-a" },
    client,
  };
  const identityRouter = {
    enabled: true,
    resolveToolContext() {
      return identity;
    },
    endpointKeyForSession(value) {
      return value === sessionKey ? "telegram:*:user-a" : null;
    },
    clientForSession() {
      return client;
    },
    removeSession() {},
  };
  const harness = fakeApi({ autoPoll: false, syncTimeline: true });
  registerAgentBridgeInteractions(harness.api, { identityRouter });

  const result = await harness.hooks.before_prompt_build(
    {
      prompt: "查看照明系统巡检任务，并继续读取第 1 个任务每日进度",
      messages: [],
    },
    {
      sessionKey,
      messageProvider: "telegram",
      senderId: "user-a",
      chatId: "user-a",
    },
  );

  assert.equal(result, undefined);
  assert.deepEqual(calls, []);
});

test("allows report export while selecting a previous task choice", async () => {
  const calls = [];
  const sessionKey = "agent:main:telegram:direct:user-a";
  const taskId = "12345678-1234-4123-8123-123456789098";
  const client = {
    async callTool(name, arguments_) {
      calls.push({ name, arguments_ });
      assert.equal(name, "agentbridge_host_task_continuation_resolve");
      if (arguments_.ordinal === null) {
        return {
          status: "ambiguous",
          count: 2,
          candidates: [
            {
              ordinal: 1,
              taskId: "12345678-1234-4123-8123-123456789097",
              title: "First host task",
              status: "succeeded",
              origin: { clientType: "web", label: "Agent Workspace" },
              updatedAt: "2026-08-03T10:00:00+00:00",
            },
            {
              ordinal: 2,
              taskId,
              title: "Choose Smartlight inspection task",
              status: "succeeded",
              origin: { clientType: "web", label: "Agent Workspace" },
              updatedAt: "2026-08-03T09:00:00+00:00",
            },
          ],
        };
      }
      return {
        status: "selected",
        task: {
          taskId,
          title: "Choose Smartlight inspection task",
          status: "succeeded",
        },
        continuation: {
          state: "selected",
          executionMode: "follow_up",
          allowNewOperation: arguments_.allow_follow_up,
          expiresAt: "2099-08-03T12:00:00+00:00",
        },
        snapshot: {
          summary: {
            phase: "terminal",
            origin: { clientType: "web", label: "Agent Workspace" },
            operation: {
              operationId: "operation-inspection-list",
              capability: "smartlight.inspection_task.list",
              status: "succeeded",
            },
          },
        },
      };
    },
  };
  const identity = {
    bound: true,
    binding: { key: "telegram:*:user-a" },
    client,
  };
  const identityRouter = {
    enabled: true,
    resolveToolContext() {
      return identity;
    },
    endpointKeyForSession(value) {
      return value === sessionKey ? "telegram:*:user-a" : null;
    },
    clientForSession() {
      return client;
    },
    removeSession() {},
  };
  const harness = fakeApi({ autoPoll: false, syncTimeline: true });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    identityRouter,
  });

  const context = {
    sessionKey,
    messageProvider: "telegram",
    senderId: "user-a",
    chatId: "user-a",
  };
  const choices = await harness.hooks.before_prompt_build(
    { prompt: "继续之前的任务", messages: [] },
    context,
  );
  assert.match(choices.prependContext, /choice=2/);

  const result = await harness.hooks.before_prompt_build(
    {
      prompt: "选择刚才列出的第 2 项并导出每日进度 CSV",
      messages: [],
    },
    context,
  );

  assert.match(result.prependContext, new RegExp(taskId));
  assert.equal(calls.length, 2);
  assert.equal(calls[1].arguments_.ordinal, 2);
  assert.equal(calls[1].arguments_.allow_follow_up, true);
  assert.equal(
    coordinator.taskContinuationForSession(sessionKey).allowNewOperation,
    true,
  );
});

test("leaves a business-list ordinal export to the current conversation", async () => {
  const calls = [];
  const sessionKey = "agent:main:telegram:direct:user-a";
  const client = {
    async callTool(name, arguments_) {
      calls.push({ name, arguments_ });
      throw new Error(`unexpected tool: ${name}`);
    },
  };
  const identity = {
    bound: true,
    binding: { key: "telegram:*:user-a" },
    client,
  };
  const identityRouter = {
    enabled: true,
    resolveToolContext() {
      return identity;
    },
    endpointKeyForSession(value) {
      return value === sessionKey ? "telegram:*:user-a" : null;
    },
    clientForSession() {
      return client;
    },
    removeSession() {},
  };
  const harness = fakeApi({ autoPoll: false, syncTimeline: true });
  registerAgentBridgeInteractions(harness.api, { identityRouter });

  const result = await harness.hooks.before_prompt_build(
    {
      prompt: "选择刚才列出的第 2 项并导出每日进度 CSV",
      messages: [],
    },
    {
      sessionKey,
      messageProvider: "telegram",
      senderId: "user-a",
      chatId: "user-a",
    },
  );

  assert.equal(result, undefined);
  assert.deepEqual(calls, []);
});

test("binds an explicit task follow-up to the existing task ID", async () => {
  const calls = [];
  const businessCalls = [];
  const sessionKey = "agent:main:telegram:direct:user-a";
  const taskId = "12345678-1234-4123-8123-123456789099";
  const client = {
    async callTool(name, arguments_, options) {
      calls.push({ name, arguments_, options });
      if (name === "agentbridge_host_task_continuation_resolve") {
        return {
          status: "selected",
          task: {
            taskId,
            title: "Read OA pending workflows",
            status: "succeeded",
          },
          continuation: {
            state: "selected",
            executionMode: "follow_up",
            allowNewOperation: true,
            expiresAt: "2099-08-03T12:00:00+00:00",
          },
          snapshot: {
            summary: {
              phase: "terminal",
              origin: { clientType: "web", label: "Agent Workspace" },
              operation: {
                operationId: "operation-pending-list",
                capability: "oa.workflow.pending.list",
                status: "succeeded",
              },
            },
          },
        };
      }
      if (name === "agentbridge_host_task_observe") {
        return { status: "succeeded" };
      }
      throw new Error(`unexpected tool: ${name}`);
    },
    async callToolResult(name, arguments_, options) {
      businessCalls.push({ name, arguments_, options });
      return {
        structuredContent: {
          status: "succeeded",
          operationId: "operation-detail",
        },
      };
    },
  };
  const identity = {
    bound: true,
    binding: { key: "telegram:*:user-a" },
    client,
  };
  const identityRouter = {
    enabled: true,
    resolveToolContext() {
      return identity;
    },
    endpointKeyForSession(value) {
      return value === sessionKey ? "telegram:*:user-a" : null;
    },
    clientForSession() {
      return client;
    },
    removeSession() {},
  };
  const harness = fakeApi({ autoPoll: false, syncTimeline: true });
  registerAgentBridgeInteractions(harness.api, { identityRouter });
  const context = {
    sessionKey,
    messageProvider: "telegram",
    senderId: "user-a",
    chatId: "user-a",
  };

  await harness.hooks.before_prompt_build(
    {
      prompt:
        "\u7ee7\u7eed\u4e4b\u524d\u7684\u4efb\u52a1\uff0c\u67e5\u770b\u7b2c1\u6761\u8be6\u60c5",
      messages: [],
    },
    context,
  );
  const tools = harness.toolFactory({
    sessionKey,
    messageChannel: "telegram",
    requesterSenderId: "user-a",
    runId: "run-follow-up",
  });
  const pending = tools.find(
    (tool) => tool.name === "oa_workflow_pending_list",
  );
  const result = await pending.execute("tool-follow-up", { limit: 5 });

  assert.equal(businessCalls.length, 1);
  assert.equal(businessCalls[0].name, "oa_workflow_pending_list");
  assert.deepEqual(businessCalls[0].options.meta, {
    "io.agentbridge/task": { taskId },
  });
  assert.equal(result.details.agentbridgeTaskId, taskId);
  assert.equal(
    calls.some((call) => call.name === "agentbridge_host_task_ensure"),
    false,
  );
});

test("keeps workflow revoke on a separate task across trusted-card stages", async () => {
  const calls = [];
  const businessCalls = [];
  const sessionKey = "agent:main:telegram:direct:user-a";
  const submittedTaskId = "12345678-1234-4123-8123-123456789091";
  const revokeTaskId = "12345678-1234-4123-8123-123456789092";
  const client = {
    async callTool(name, arguments_, options) {
      calls.push({ name, arguments_, options });
      if (name === "agentbridge_host_task_continuation_resolve") {
        return {
          status: "selected",
          task: {
            taskId: submittedTaskId,
            title: "Submit OA business-trip request",
            status: "succeeded",
          },
          continuation: {
            state: "selected",
            executionMode: "follow_up",
            allowNewOperation: true,
            expiresAt: "2099-08-03T12:00:00+00:00",
          },
          snapshot: {
            summary: {
              phase: "terminal",
              origin: { clientType: "web", label: "Agent Workspace" },
              operation: {
                operationId: "operation-business-trip-submit",
                capability: "oa.business_trip.submit",
                status: "succeeded",
              },
            },
          },
        };
      }
      if (name === "agentbridge_host_task_ensure") {
        return {
          status: "succeeded",
          task: { taskId: revokeTaskId },
        };
      }
      if (name === "agentbridge_host_task_observe") {
        return { status: "succeeded" };
      }
      throw new Error(`unexpected tool: ${name}`);
    },
    async callToolResult(name, arguments_, options) {
      businessCalls.push({ name, arguments_, options });
      return {
        structuredContent: {
          status: "requires_user_action",
          operationId: `operation-revoke-${businessCalls.length}`,
        },
      };
    },
  };
  const identity = {
    bound: true,
    binding: { key: "telegram:*:user-a" },
    client,
  };
  const identityRouter = {
    enabled: true,
    resolveToolContext() {
      return identity;
    },
    endpointKeyForSession(value) {
      return value === sessionKey ? "telegram:*:user-a" : null;
    },
    clientForSession() {
      return client;
    },
    removeSession() {},
  };
  const harness = fakeApi({ autoPoll: false, syncTimeline: true });
  registerAgentBridgeInteractions(harness.api, { identityRouter });
  const context = {
    sessionKey,
    messageProvider: "telegram",
    senderId: "user-a",
    chatId: "user-a",
  };

  await harness.hooks.before_prompt_build(
    {
      prompt: "\u7ee7\u7eed\u4e4b\u524d\u7684\u4efb\u52a1\uff0c\u64a4\u9500\u521a\u63d0\u4ea4\u7684\u51fa\u5dee\u7533\u8bf7",
      messages: [],
    },
    context,
  );

  const runIds = ["run-revoke-fields", "run-revoke-authorize"];
  for (const [index, runId] of runIds.entries()) {
    const tools = harness.toolFactory({
      sessionKey,
      messageChannel: "telegram",
      requesterSenderId: "user-a",
      runId,
    });
    const revoke = tools.find(
      (tool) => tool.name === "oa_workflow_revoke_prepare",
    );
    const result = await revoke.execute(`tool-revoke-${index + 1}`, {
      affair_id: "affair-business-trip-1",
    });
    assert.equal(result.details.agentbridgeTaskId, revokeTaskId);
  }

  assert.equal(businessCalls.length, 2);
  assert.equal(
    businessCalls.every(
      (call) =>
        call.name === "oa_workflow_revoke_prepare" &&
        call.options.meta["io.agentbridge/task"].taskId === revokeTaskId,
    ),
    true,
  );
  assert.equal(
    businessCalls.some(
      (call) =>
        call.options.meta["io.agentbridge/task"].taskId === submittedTaskId,
    ),
    false,
  );
  assert.equal(
    calls.filter((call) => call.name === "agentbridge_host_task_ensure").length,
    1,
  );
});

test("resolves a recent cross-endpoint task without asking for an internal task number", async () => {
  const calls = [];
  const sessionKey = "agent:main:telegram:direct:user-a";
  const taskId = "12345678-1234-4123-8123-123456789088";
  const client = {
    async callTool(name, arguments_) {
      calls.push({ name, arguments_ });
      if (name === "agentbridge_host_cross_endpoint_context") {
        return { status: "succeeded", entries: [] };
      }
      if (name !== "agentbridge_host_task_continuation_resolve") {
        throw new Error(`unexpected tool: ${name}`);
      }
      assert.equal(arguments_.ordinal, null);
      assert.equal(arguments_.source_client_type, "web");
      assert.equal(arguments_.prefer_latest, true);
      return {
        status: "selected",
        task: {
          taskId,
          title: "Read OA pending workflows",
          status: "succeeded",
        },
        continuation: {
          state: "selected",
          executionMode: "follow_up",
          allowNewOperation: true,
          expiresAt: "2099-08-03T12:00:00+00:00",
        },
        snapshot: {
          summary: {
            phase: "terminal",
            origin: { clientType: "web", label: "Agent Workspace" },
            operation: {
              operationId: "operation-pending-list",
              capability: "oa.workflow.pending.list",
              status: "succeeded",
            },
          },
        },
      };
    },
  };
  const identityRouter = {
    enabled: true,
    resolveToolContext() {
      return { bound: true, client };
    },
    endpointKeyForSession(value) {
      return value === sessionKey ? "telegram:*:user-a" : null;
    },
    clientForSession() {
      return client;
    },
    removeSession() {},
  };
  const harness = fakeApi({ autoPoll: false, syncTimeline: true });
  registerAgentBridgeInteractions(harness.api, { identityRouter });

  const result = await harness.hooks.before_prompt_build(
    {
      prompt: "\u7ee7\u7eed\u521a\u624d\u7f51\u9875\u91cc\u7684\u5f85\u529e\u4efb\u52a1\uff0c\u67e5\u770b\u7b2c1\u6761\u8be6\u60c5",
      messages: [],
    },
    {
      sessionKey,
      messageProvider: "telegram",
      senderId: "user-a",
      chatId: "user-a",
    },
  );

  assert.match(result.prependContext, new RegExp(taskId));
  assert.equal(
    calls.filter(
      (call) => call.name === "agentbridge_host_task_continuation_resolve",
    ).length,
    1,
  );
});

test("confirms a workspace link from the authenticated command sender after restart", async () => {
  const requests = [];
  const senderId = "7052061588";
  const sessionKey = `agent:main:telegram:direct:${senderId}`;
  const harness = fakeApi({
    autoPoll: false,
    mcpUrl: "https://10.10.50.213:8790/mcp",
    identityBindings: [
      {
        channel: "telegram",
        senderId,
        tokenEnv: "USER_TOKEN",
        label: "User A",
      },
    ],
  });
  registerAgentBridgeInteractions(harness.api, {
    env: { USER_TOKEN: "token-a" },
    fetchImpl: async (_url, options) => {
      const body = JSON.parse(options.body);
      requests.push({
        authorization: options.headers.Authorization,
        body,
      });
      return new Response(
        JSON.stringify({
          jsonrpc: "2.0",
          id: body.id,
          result: {
            structuredContent: { status: "succeeded" },
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    },
  });

  const result = await harness.command.handler({
    args: "link ABCDEFGH",
    senderId,
    channel: "telegram",
    channelId: "telegram",
    isAuthorizedSender: true,
    sessionKey,
  });

  assert.match(result.text, /身份配对已确认/);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].authorization, "Bearer token-a");
  assert.equal(
    requests[0].body.params.name,
    "agentbridge_host_workspace_link_confirm",
  );
  assert.deepEqual(requests[0].body.params.arguments, {
    agent_host: "openclaw",
    endpoint_key: "telegram:*:7052061588",
    client_type: "telegram",
    external_subject: senderId,
    account_id: null,
    conversation_ref: sessionKey,
    label: "User A",
    route: {
      channel: "telegram",
      to: senderId,
      accountId: null,
    },
    link_code: "ABCDEFGH",
  });
});

test("rejects a workspace link command from an unprovisioned sender", async () => {
  const requests = [];
  const harness = fakeApi({
    autoPoll: false,
    mcpUrl: "https://10.10.50.213:8790/mcp",
    identityBindings: [
      {
        channel: "telegram",
        senderId: "7052061588",
        tokenEnv: "USER_TOKEN",
      },
    ],
  });
  registerAgentBridgeInteractions(harness.api, {
    env: { USER_TOKEN: "token-a" },
    fetchImpl: async (...args) => {
      requests.push(args);
      throw new Error("not called");
    },
  });

  const result = await harness.command.handler({
    args: "link ABCDEFGH",
    senderId: "9999999999",
    channel: "telegram",
    channelId: "telegram",
    isAuthorizedSender: true,
    sessionKey: "agent:main:telegram:direct:9999999999",
  });

  assert.match(result.text, /尚未绑定 AgentBridge/);
  assert.equal(requests.length, 0);
});

test("registers and enforces the one-use workspace Gateway binding", async () => {
  const attempts = [];
  const restored = [];
  const identityRouter = {
    enabled: true,
    configuredIdentities() {
      return [
        {
          binding: { key: "telegram:*:user-a" },
          client: {
            async callTool(name, params, options) {
              attempts.push({ binding: "user-a", name, params, options });
              throw new Error("grant belongs to another identity");
            },
          },
        },
        {
          binding: { key: "openclaw-weixin:*:user-b" },
          client: {
            async callTool(name, params, options) {
              attempts.push({ binding: "user-b", name, params, options });
              return { status: "succeeded" };
            },
          },
        },
      ];
    },
    restoreSessionBinding({ sessionKey, bindingKey }) {
      restored.push({ sessionKey, bindingKey });
      return bindingKey === "openclaw-weixin:*:user-b";
    },
    clientForSession() {
      return null;
    },
    resolveToolContext() {
      return { bound: false, reason: "not_bound" };
    },
    removeSession() {},
  };
  const harness = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(harness.api, { identityRouter });
  const registered = harness.gatewayMethods.get(
    "agentbridge.workspace.bind",
  );
  const responses = [];

  await registered.handler({
    params: {
      sessionKey:
        "agent:main:agentbridge-workspace:direct:account-123",
      endpointKey: "workspace:account-123",
      grant: "abwg_1234567890123456789012345678901234567890",
      turnRef: "request-bind-123",
    },
    respond(ok, payload, error) {
      responses.push({ ok, payload, error });
    },
  });

  assert.deepEqual(registered.options, { scope: "operator.write" });
  assert.equal(attempts.length, 2);
  assert.equal(
    attempts[1].name,
    "agentbridge_host_workspace_session_bind",
  );
  assert.equal(attempts[1].params.turn_ref, "request-bind-123");
  assert.deepEqual(attempts[1].options, {
    meta: {
      "io.agentbridge/host": {
        version: "1",
        agentHost: "openclaw",
      },
    },
  });
  assert.deepEqual(restored, [
    {
      sessionKey:
        "agent:main:agentbridge-workspace:direct:account-123",
      bindingKey: "openclaw-weixin:*:user-b",
    },
  ]);
  assert.deepEqual(responses, [
    {
      ok: true,
      payload: {
        status: "bound",
        sessionKey:
          "agent:main:agentbridge-workspace:direct:account-123",
      },
      error: undefined,
    },
  ]);
});

test("shares workspace identity and endpoint bindings with the agent runtime instance", async () => {
  const sharedState = createInteractionSharedState();
  const requests = [];
  const pluginConfig = {
    autoPoll: false,
    mcpUrl: "https://10.10.50.213:8790/mcp",
    identityBindings: [
      {
        channel: "telegram",
        senderId: "7052061588",
        tokenEnv: "USER_TOKEN",
        label: "User A",
      },
    ],
  };
  const fetchImpl = async (_url, options) => {
    const body = JSON.parse(options.body);
    const toolName = body.params?.name;
    requests.push({
      authorization: options.headers.Authorization,
      body,
    });
    const structuredContent =
      toolName === "agentbridge_host_task_ensure"
        ? {
            status: "succeeded",
            task: { taskId: "task-workspace-shared-1234567890" },
          }
        : toolName === "oa_workflow_pending_list"
          ? { status: "succeeded", result: { count: 0, items: [] } }
          : { status: "succeeded" };
    return new Response(
      JSON.stringify({
        jsonrpc: "2.0",
        id: body.id,
        result: {
          structuredContent,
        },
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  };
  const gateway = fakeApi(pluginConfig);
  const runtime = fakeApi(pluginConfig);
  registerAgentBridgeInteractions(gateway.api, {
    sharedState,
    env: { USER_TOKEN: "token-a" },
    fetchImpl,
  });
  registerAgentBridgeInteractions(runtime.api, {
    sharedState,
    env: { USER_TOKEN: "token-a" },
    fetchImpl,
  });
  const sessionKey =
    "agent:main:agentbridge-workspace:direct:account-123";
  const responses = [];

  await gateway.gatewayMethods.get("agentbridge.workspace.bind").handler({
    params: {
      sessionKey,
      endpointKey: "workspace:account-123",
      grant: "abwg_1234567890123456789012345678901234567890",
      turnRef: "workspace-turn-123",
    },
    respond(ok, payload, error) {
      responses.push({ ok, payload, error });
    },
  });
  const tools = runtime.toolFactory({
    sessionKey,
    messageChannel: "webchat",
    runId: "workspace-shared-run",
  });
  const pendingTool = tools.find(
    (tool) => tool.name === "oa_workflow_pending_list",
  );
  const result = await pendingTool.execute("workspace-pending", { limit: 5 });

  assert.equal(responses[0].ok, true);
  assert.equal(result.details.structuredContent.status, "succeeded");
  assert.equal(requests.length, 3);
  assert.deepEqual(
    requests.map((request) => request.body.params.name),
    [
      "agentbridge_host_workspace_session_bind",
      "agentbridge_host_task_ensure",
      "oa_workflow_pending_list",
    ],
  );
  const taskEnsure = requests.find(
    (request) =>
      request.body.params.name === "agentbridge_host_task_ensure",
  );
  assert.equal(
    taskEnsure.body.params.arguments.endpoint_key,
    "workspace:account-123",
  );
  assert.equal(taskEnsure.body.params.arguments.client_type, "web");
  assert.equal(taskEnsure.body.params.arguments.task_scope, "user_turn");
  assert.equal(
    requests[0].body.params.arguments.turn_ref,
    "workspace-turn-123",
  );
  assert.equal(
    requests.every(
      (request) => request.authorization === "Bearer token-a",
    ),
    true,
  );
});

test("uses the shared Workspace turn as a local task-key fast path", async () => {
  const sharedState = createInteractionSharedState();
  const requests = [];
  const pluginConfig = {
    autoPoll: false,
    mcpUrl: "https://10.10.50.213:8790/mcp",
    identityBindings: [
      {
        channel: "telegram",
        senderId: "7052061588",
        tokenEnv: "USER_TOKEN",
        label: "User A",
      },
    ],
  };
  const fetchImpl = async (_url, options) => {
    const body = JSON.parse(options.body);
    requests.push(body);
    const name = body.params?.name;
    const structuredContent =
      name === "agentbridge_host_task_ensure"
        ? {
            status: "succeeded",
            task: { taskId: "task-workspace-turn-1234567890" },
          }
        : { status: "succeeded", result: { count: 0, items: [] } };
    return new Response(
      JSON.stringify({
        jsonrpc: "2.0",
        id: body.id,
        result: { structuredContent },
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  };
  const gateway = fakeApi(pluginConfig);
  const runtime = fakeApi(pluginConfig);
  registerAgentBridgeInteractions(gateway.api, {
    sharedState,
    env: { USER_TOKEN: "token-a" },
    fetchImpl,
  });
  registerAgentBridgeInteractions(runtime.api, {
    sharedState,
    env: { USER_TOKEN: "token-a" },
    fetchImpl,
  });
  const sessionKey =
    "agent:main:agentbridge-workspace:direct:account-123";

  await gateway.gatewayMethods.get("agentbridge.workspace.bind").handler({
    params: {
      sessionKey,
      endpointKey: "workspace:account-123",
      grant: "abwg_1234567890123456789012345678901234567890",
      turnRef: "request-123456",
    },
    respond() {},
  });
  const firstTools = runtime.toolFactory({
    sessionKey,
    messageChannel: "webchat",
    runId: "call-search|fc-search",
  });
  runtime.hooks.before_tool_call(
    {
      toolCallId: "tool-search",
      runId: "call-search|fc-search",
      toolName: "oa_workflow_pending_list",
    },
    { sessionKey },
  );
  await firstTools
    .find((tool) => tool.name === "oa_workflow_pending_list")
    .execute("tool-search", { limit: 5 });
  const secondTools = runtime.toolFactory({
    sessionKey,
    messageChannel: "webchat",
    runId: "call-prepare|fc-prepare",
  });
  runtime.hooks.before_tool_call(
    {
      toolCallId: "tool-prepare",
      runId: "call-prepare|fc-prepare",
      toolName: "oa_workflow_sent_list",
    },
    { sessionKey },
  );
  await secondTools
    .find((tool) => tool.name === "oa_workflow_sent_list")
    .execute("tool-prepare", { limit: 5 });
  const revokeTools = runtime.toolFactory({
    sessionKey,
    messageChannel: "webchat",
    runId: "call-revoke|fc-revoke",
  });
  runtime.hooks.before_tool_call(
    {
      toolCallId: "tool-revoke",
      runId: "call-revoke|fc-revoke",
      toolName: "oa_workflow_revoke_prepare",
    },
    { sessionKey },
  );
  await revokeTools
    .find((tool) => tool.name === "oa_workflow_revoke_prepare")
    .execute("tool-revoke", { affair_id: "affair-123" });

  const taskEnsures = requests.filter(
    (request) => request.params?.name === "agentbridge_host_task_ensure",
  );
  assert.equal(taskEnsures.length, 3);
  assert.deepEqual(
    taskEnsures.map(
      (request) => request.params.arguments.host_task_key,
    ),
    [
      `${sessionKey}|workspace:request-123456`,
      `${sessionKey}|workspace:request-123456`,
      `${sessionKey}|call-revoke|fc-revoke`,
    ],
  );
  assert.deepEqual(
    taskEnsures.map((request) => request.params.arguments.task_scope),
    ["user_turn", "user_turn", "independent"],
  );
});

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

test("remembers the host task attached to a pending trusted interaction", () => {
  const harness = fakeApi({ autoPoll: false });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  bindToolCall(harness, {
    toolCallId: "tool-task-binding",
    runId: "run-task-binding",
    sessionKey,
  });
  const result = toolResult();
  result.details.agentbridgeTaskId = "task-1234567890-abcdef";

  harness.middleware(
    {
      toolCallId: "tool-task-binding",
      toolName: "oa_session_login",
      result,
    },
    { runtime: "openclaw" },
  );

  assert.equal(
    coordinator.activeTaskForSession(sessionKey),
    "task-1234567890-abcdef",
  );
});

test("replaces a final authorization with its endpoint-specific URL", async () => {
  const requests = [];
  const senderId = "7052061588";
  const sessionKey = `agent:main:telegram:direct:${senderId}`;
  const endpointUrl =
    `${CARD_ORIGIN}/authorize/auth-1234567890-abcdef/` +
    "present/presentation-1234567890-abcdef";
  const authorization = interaction({
    interactionId: "interaction-authorization-1234567890",
    type: "execution_authorization",
    title: "确认提交请假申请",
    presentation: { url: `${CARD_ORIGIN}/authorize/auth-1234567890-abcdef` },
  });
  const harness = fakeApi({
    autoPoll: false,
    mcpUrl: "https://10.10.50.213:8790/mcp",
    identityBindings: [
      {
        channel: "telegram",
        senderId,
        tokenEnv: "USER_TOKEN",
      },
    ],
  });
  registerAgentBridgeInteractions(harness.api, {
    env: { USER_TOKEN: "token-a" },
    fetchImpl: async (_url, options) => {
      const body = JSON.parse(options.body);
      requests.push(body);
      return new Response(
        JSON.stringify({
          jsonrpc: "2.0",
          id: body.id,
          result: {
            structuredContent: {
              status: "succeeded",
              interaction: {
                ...authorization,
                presentation: {
                  ...authorization.presentation,
                  url: endpointUrl,
                  individualized: true,
                },
              },
            },
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    },
  });
  harness.hooks.message_received(
    {
      content: "提交请假申请",
      senderId,
      channel: "telegram",
      sessionKey,
    },
    {
      channelId: "telegram",
      senderId,
      sessionKey,
      conversationId: senderId,
    },
  );
  bindToolCall(harness, {
    toolCallId: "tool-endpoint-presentation",
    runId: "run-endpoint-presentation",
    sessionKey,
  });

  const replacement = await harness.middleware(
    {
      toolCallId: "tool-endpoint-presentation",
      toolName: "oa_leave_submit_prepare",
      result: toolResult(authorization),
    },
    { runtime: "openclaw", sessionKey },
  );
  const reply = harness.hooks.reply_payload_sending(
    {
      kind: "final",
      sessionKey,
      channel: "telegram",
      payload: { text: "请确认。" },
    },
    { sessionKey, channelId: "telegram" },
  );

  assert.equal(
    requests[0].params.name,
    "agentbridge_host_interaction_present",
  );
  assert.equal(
    reply.payload.presentation.blocks.at(-1).buttons[0].url,
    endpointUrl,
  );
});

test("uses one host task run reference for multiple tools in the same agent run", () => {
  const harness = fakeApi({ autoPoll: false });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  bindToolCall(harness, {
    toolCallId: "tool-run-a",
    runId: "run-shared",
    sessionKey,
  });
  bindToolCall(harness, {
    toolCallId: "tool-run-b",
    runId: "run-shared",
    sessionKey,
  });

  assert.equal(
    coordinator.taskRunRefForToolCall("tool-run-a", sessionKey),
    "run-shared",
  );
  assert.equal(
    coordinator.taskRunRefForToolCall("tool-run-b", sessionKey),
    "run-shared",
  );
});

test("uses one user-turn task reference when OpenClaw assigns per-tool run IDs", () => {
  const harness = fakeApi({ autoPoll: false });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  const sessionKey = "agent:main:agentbridge-workspace:direct:account-a";
  coordinator.recordUserMessage(
    { sessionKey, content: "下载图片中的软著" },
    { sessionKey },
  );
  bindToolCall(harness, {
    toolCallId: "tool-search-a",
    runId: "call-search|fc-search",
    sessionKey,
  });
  bindToolCall(harness, {
    toolCallId: "tool-prepare-a",
    runId: "call-prepare|fc-prepare",
    sessionKey,
  });

  const searchRef = coordinator.taskRunRefForToolCall(
    "tool-search-a",
    sessionKey,
    "oa_certificate_search",
  );
  const prepareRef = coordinator.taskRunRefForToolCall(
    "tool-prepare-a",
    sessionKey,
    "oa_certificate_prepare_downloads",
  );
  assert.match(searchRef, /^turn:/);
  assert.equal(prepareRef, searchRef);
});

test("uses one task reference for a multimodal user turn", () => {
  const harness = fakeApi({ autoPoll: false });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  const sessionKey = "agent:main:agentbridge-workspace:direct:account-a";
  coordinator.recordUserMessage(
    {
      sessionKey,
      text: "下载图片中的软著",
      content: [
        { type: "text", text: "下载图片中的软著" },
        { type: "image", source: { type: "url", url: "media://image-a" } },
      ],
    },
    { sessionKey },
  );
  bindToolCall(harness, {
    toolCallId: "tool-search-multimodal",
    runId: "call-search|fc-multimodal",
    sessionKey,
  });
  bindToolCall(harness, {
    toolCallId: "tool-prepare-multimodal",
    runId: "call-prepare|fc-multimodal",
    sessionKey,
  });

  const searchRef = coordinator.taskRunRefForToolCall(
    "tool-search-multimodal",
    sessionKey,
    "oa_certificate_search",
  );
  const prepareRef = coordinator.taskRunRefForToolCall(
    "tool-prepare-multimodal",
    sessionKey,
    "oa_certificate_prepare_downloads",
  );
  assert.match(searchRef, /^turn:/);
  assert.equal(prepareRef, searchRef);
});

test("restores a pending interaction and its original route on gateway start", async () => {
  const requests = [];
  const senderId = "7052061588";
  const sessionKey = `agent:main:telegram:direct:${senderId}`;
  const recoveredInteraction = interaction({
    interactionId: "interaction-recovered-1234567890",
  });
  const harness = fakeApi({
    autoPoll: false,
    mcpUrl: "https://10.10.50.213:8790/mcp",
    identityBindings: [
      {
        channel: "telegram",
        senderId,
        tokenEnv: "USER_TOKEN",
        label: "User A",
      },
    ],
  });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    env: { USER_TOKEN: "token-a" },
    fetchImpl: async (_url, options) => {
      const body = JSON.parse(options.body);
      requests.push(body);
      const structuredContent =
        body.params.name === "agentbridge_host_identity_profile"
          ? {
              status: "succeeded",
              identity: {
                userSubject: "user-a",
                scopes: ["oa:read"],
              },
              agentToolAccess: { allowedToolNames: [] },
            }
          : {
              status: "succeeded",
              count: 1,
              recoveries: [
                {
                  task: { taskId: "task-recovered-1234567890" },
                  endpoint: {
                    clientType: "telegram",
                    externalSubject: senderId,
                    accountId: null,
                    conversationRef: sessionKey,
                    route: {
                      channel: "telegram",
                      to: senderId,
                      accountId: null,
                      threadId: null,
                    },
                  },
                  interaction: recoveredInteraction,
                },
              ],
            };
      return new Response(
        JSON.stringify({
          jsonrpc: "2.0",
          id: body.id,
          result: { structuredContent },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    },
  });

  await harness.hooks.gateway_start();

  assert.equal(requests.length, 3);
  assert.equal(
    requests[0].params.name,
    "agentbridge_host_identity_profile",
  );
  assert.equal(
    requests[1].params.name,
    "agentbridge_host_task_recovery_list",
  );
  assert.equal(
    requests[2].params.name,
    "agentbridge_host_notification_claim",
  );
  assert.deepEqual(requests[1].params._meta, {
    "io.agentbridge/host": {
      version: "1",
      agentHost: "openclaw",
    },
  });
  assert.equal(coordinator.pendingForSession(sessionKey).length, 1);
  assert.equal(
    coordinator.activeTaskForSession(sessionKey),
    "task-recovered-1234567890",
  );
  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(
    harness.sentPayloads[0].payload.presentation.blocks.at(-1).buttons[0].url,
    CARD_URL,
  );
  coordinator.stopAll();
});

test("delivers a non-origin authorization and acknowledges its outbox item", async () => {
  const sessionKey = "agent:main:openclaw-weixin:direct:wechat-user-a";
  const authorizationUrl =
    `${CARD_ORIGIN}/authorize/auth-1234567890-abcdef/` +
    "present/wechat-presentation-1234567890";
  const authorization = interaction({
    interactionId: "interaction-notification-1234567890",
    type: "execution_authorization",
    presentation: { url: authorizationUrl },
  });
  const harness = fakeApi({ autoPoll: false });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  const calls = [];
  const client = {
    async callTool(name, params) {
      calls.push({ name, params });
      if (name === "agentbridge_host_notification_claim") {
        return {
          endpoint: {
            conversationRef: sessionKey,
            route: {
              channel: "openclaw-weixin",
              to: "wechat-user-a",
            },
          },
          notifications: [
            {
              deliveryId: "delivery-1234567890-abcdef",
              deliveryMode: "trusted_interaction",
              interaction: authorization,
            },
          ],
        };
      }
      return { status: "succeeded" };
    },
  };
  const identityRouter = {
    restoreSessionBinding() {
      return true;
    },
  };
  const binding = {
    key: "openclaw-weixin:*:wechat-user-a",
    channel: "openclaw-weixin",
    senderId: "wechat-user-a",
    accountId: null,
  };

  await coordinator.deliverEndpointNotifications(
    identityRouter,
    binding,
    client,
    new AbortController().signal,
  );

  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(
    JSON.stringify(harness.sentPayloads[0].payload).includes(
      authorizationUrl,
    ),
    true,
  );
  assert.equal(calls.at(-1).name, "agentbridge_host_notification_ack");
  assert.equal(calls.at(-1).params.succeeded, true);
});

test("acknowledges pull-based workspace notifications without direct webchat delivery", async () => {
  const harness = fakeApi({ autoPoll: false });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  const calls = [];
  const client = {
    async callTool(name, params) {
      calls.push({ name, params });
      if (name === "agentbridge_host_notification_claim") {
        return {
          endpoint: {
            clientType: "web",
            conversationRef:
              "agent:main:agentbridge-workspace:direct:account-123",
            route: { channel: "webchat", to: "account-123" },
          },
          notifications: [
            {
              deliveryId: "delivery-workspace-card-1234567890",
              deliveryMode: "trusted_interaction",
              interaction: interaction({
                interactionId: "interaction-workspace-card-1234567890",
              }),
            },
            {
              deliveryId: "delivery-workspace-status-1234567890",
              deliveryMode: "status",
              event: { eventType: "task.operation.succeeded" },
              message: "Task completed.",
            },
          ],
        };
      }
      return { status: "succeeded" };
    },
  };

  await coordinator.deliverEndpointNotifications(
    { restoreSessionBinding: () => true },
    {
      key: "workspace:account-123",
      channel: "webchat",
      senderId: "account-123",
      accountId: null,
    },
    client,
    new AbortController().signal,
  );

  assert.equal(harness.sentPayloads.length, 0);
  const acknowledgements = calls.filter(
    (item) => item.name === "agentbridge_host_notification_ack",
  );
  assert.equal(acknowledgements.length, 2);
  assert.equal(
    acknowledgements.every((item) => item.params.succeeded === true),
    true,
  );
});

test("delivers a task artifact to a companion chat as one attachment", async () => {
  const harness = fakeApi({ autoPoll: false });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
    ...preparedDocumentDependencies(),
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  const calls = [];
  const client = {
    async callTool(name, params) {
      calls.push({ name, params });
      if (name === "agentbridge_host_notification_claim") {
        return {
          endpoint: {
            clientType: "telegram",
            conversationRef: sessionKey,
            route: { channel: "telegram", to: "7052061588" },
          },
          notifications: [
            {
              deliveryId: "delivery-artifact-1234567890",
              deliveryMode: "artifact",
              task: { taskId: "task-artifact-1234567890" },
              artifact: {
                artifactId: "artifact-1234567890",
                artifactType: "certificate_scan",
                filename: "certificate.pdf",
                contentType: "application/pdf",
                size: 128,
                mediaUrl: `${CARD_ORIGIN}/download/${"a".repeat(43)}/file`,
                expiresAt: "2099-07-14T12:00:00+00:00",
              },
            },
          ],
        };
      }
      return { status: "succeeded" };
    },
  };

  await coordinator.deliverEndpointNotifications(
    { restoreSessionBinding: () => true },
    {
      key: "telegram:*:7052061588",
      channel: "telegram",
      senderId: "7052061588",
      accountId: null,
    },
    client,
    new AbortController().signal,
  );

  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(harness.sentPayloads[0].payload.mediaUrl, "C:/media/certificate.bin");
  const report = calls.find(
    (item) => item.name === "agentbridge_host_artifact_delivery_report",
  );
  assert.equal(report.params.task_id, "task-artifact-1234567890");
  assert.equal(report.params.files[0].state, "attachment_sent");
  assert.equal(calls.at(-1).name, "agentbridge_host_notification_ack");
  assert.equal(calls.at(-1).params.succeeded, true);
});

test("keeps workspace card and status updates on the pull stream without a model wake", async () => {
  const harness = fakeApi({ autoPoll: false, wakeAgentOnComplete: true });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  const sessionKey =
    "agent:main:agentbridge-workspace:direct:workspace-account-a";
  bindDeliveryRoute(harness, {
    sessionKey,
    channel: "webchat",
    to: "workspace-account-a",
  });
  const next = interaction({
    interactionId: "interaction-workspace-next-1234567890",
    type: "execution_authorization",
  });
  coordinator.upsert({
    interaction: next,
    sessionKey,
    runId: null,
    taskId: "task-workspace-pull-1234567890",
  });

  await coordinator.notify(
    {
      sessionKey,
      interaction: { interactionId: "interaction-workspace-fields-1234567890" },
    },
    "next_interaction_required",
    null,
    [next],
  );
  await coordinator.notify(
    {
      sessionKey,
      interaction: { interactionId: next.interactionId },
    },
    "succeeded",
    null,
  );

  assert.equal(harness.sentPayloads.length, 0);
  assert.equal(harness.systemEvents.length, 0);
  assert.equal(harness.heartbeatRuns.length, 0);
  assert.equal(harness.heartbeats.length, 0);
  assert.equal(coordinator.takeForDelivery({ sessionKey }).length, 0);
  assert.equal(
    harness.logs.warn.some((message) => message.includes("webchat")),
    false,
  );
});

test("preserves the dedicated login continuation wake for a workspace session", async () => {
  const harness = fakeApi({ autoPoll: false, wakeAgentOnComplete: true });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  const sessionKey =
    "agent:main:agentbridge-workspace:direct:workspace-account-a";
  bindDeliveryRoute(harness, {
    sessionKey,
    channel: "webchat",
    to: "workspace-account-a",
  });

  await coordinator.notify(
    {
      sessionKey,
      continuationQueued: false,
      interaction: { interactionId: "interaction-workspace-login-1234567890" },
    },
    "succeeded",
    null,
    [],
    { resumeOriginalRequest: true, response: { status: "succeeded" } },
  );

  assert.equal(harness.sentPayloads.length, 0);
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

test("suppresses same-run interaction_get but permits a later-run redisplay", async () => {
  const sessionKey = "agent:main:telegram:direct:user-a";
  const businessCalls = [];
  const client = {
    async callToolResult(name, params) {
      businessCalls.push({ name, params });
      return {
        content: [{ type: "text", text: '{"status":"succeeded"}' }],
        structuredContent: { status: "succeeded" },
      };
    },
  };
  const identityRouter = {
    enabled: true,
    resolveToolContext() {
      return {
        bound: true,
        binding: { key: "telegram:*:user-a", label: "User A" },
        client,
      };
    },
    clientForSession() {
      return client;
    },
    endpointKeyForSession() {
      return "telegram:*:user-a";
    },
    bindSession() {
      return true;
    },
    removeSession() {},
  };
  const harness = fakeApi({ autoPoll: false });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    identityRouter,
  });
  const pending = interaction({
    interactionId: "interaction-same-run-1234567890",
    type: "business_input",
  });
  coordinator.upsert({
    interaction: pending,
    sessionKey,
    runId: "run-card-created",
  });

  const sameRunTools = harness.toolFactory({
    sessionKey,
    runId: "run-card-created",
    messageChannel: "telegram",
    requesterSenderId: "user-a",
  });
  const sameRunGet = sameRunTools.find(
    (tool) => tool.name === "agentbridge_interaction_get",
  );
  const guarded = await sameRunGet.execute(
    "tool-get-same-run",
    { interaction_id: pending.interactionId },
  );

  assert.equal(guarded.details.structuredContent.status, "host_handled");
  assert.equal(businessCalls.length, 0);

  const laterRunTools = harness.toolFactory({
    sessionKey,
    runId: "run-user-reports-missing-card",
    messageChannel: "telegram",
    requesterSenderId: "user-a",
  });
  const laterRunGet = laterRunTools.find(
    (tool) => tool.name === "agentbridge_interaction_get",
  );
  await laterRunGet.execute(
    "tool-get-later-run",
    { interaction_id: pending.interactionId },
  );

  assert.equal(businessCalls.length, 1);
  assert.equal(businessCalls[0].name, "agentbridge_interaction_get");
});

test("suppresses routine companion status chatter but retains acknowledgement", async () => {
  const harness = fakeApi({ autoPoll: false });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  const calls = [];
  const client = {
    async callTool(name, params) {
      calls.push({ name, params });
      if (name === "agentbridge_host_notification_claim") {
        return {
          endpoint: {
            clientType: "telegram",
            conversationRef: "agent:main:telegram:direct:1001",
            route: { channel: "telegram", to: "1001" },
          },
          notifications: [
            {
              deliveryId: "delivery-routine-status-1234567890",
              deliveryMode: "status",
              event: { eventType: "task.operation.running" },
              message: "Task is running.",
            },
            {
              deliveryId: "delivery-completed-status-1234567890",
              deliveryMode: "status",
              event: { eventType: "task.completed" },
              message: "Task completed.",
            },
          ],
        };
      }
      return { status: "succeeded" };
    },
  };

  await coordinator.deliverEndpointNotifications(
    { restoreSessionBinding: () => true },
    {
      key: "telegram:*:1001",
      channel: "telegram",
      senderId: "1001",
      accountId: null,
    },
    client,
    new AbortController().signal,
  );

  assert.equal(harness.sentPayloads.length, 0);
  const acknowledgements = calls.filter(
    (item) => item.name === "agentbridge_host_notification_ack",
  );
  assert.equal(acknowledgements.length, 2);
  assert.equal(
    acknowledgements.every((item) => item.params.succeeded === true),
    true,
  );
});

test("delivers an ordered timeline message once and acknowledges it", async () => {
  const sessionKey = "agent:main:telegram:direct:1001";
  const harness = fakeApi({ autoPoll: false });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  const calls = [];
  const client = {
    async callTool(name, params) {
      calls.push({ name, params });
      if (name === "agentbridge_host_notification_claim") {
        return {
          endpoint: {
            clientType: "telegram",
            conversationRef: sessionKey,
            route: { channel: "telegram", to: "1001" },
          },
          notifications: [
            {
              deliveryId: "delivery-timeline-1234567890",
              deliveryMode: "timeline_message",
              message: "[Web - You]\nSubmit a business trip request.",
            },
          ],
        };
      }
      return { status: "succeeded" };
    },
  };

  const notificationCount = await coordinator.deliverEndpointNotifications(
    { restoreSessionBinding: () => true },
    {
      key: "telegram:*:1001",
      channel: "telegram",
      senderId: "1001",
      accountId: null,
    },
    client,
    new AbortController().signal,
  );

  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(notificationCount, 1);
  assert.equal(
    harness.sentPayloads[0].payload.text,
    "[Web - You]\nSubmit a business trip request.",
  );
  const acknowledgements = calls.filter(
    (item) => item.name === "agentbridge_host_notification_ack",
  );
  assert.equal(acknowledgements.length, 1);
  assert.equal(acknowledgements[0].params.succeeded, true);
});

test("delivers governed timeline images with the ordered cross-end message", async () => {
  const sessionKey = "agent:main:telegram:direct:1001";
  const harness = fakeApi({ autoPoll: false });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
    documentFetchImpl: async () => ({
      ok: true,
      status: 200,
      headers: {
        get(name) {
          return name.toLowerCase() === "content-type"
            ? "image/png"
            : name.toLowerCase() === "content-length"
              ? "12"
              : null;
        },
      },
      async arrayBuffer() {
        return Buffer.from("timeline-png");
      },
    }),
    saveMediaBufferImpl: async (_body, contentType) => ({
      id: "timeline.png",
      path: "C:/media/timeline.png",
      size: 12,
      contentType,
    }),
  });
  const calls = [];
  const attachmentId = "a".repeat(43);
  const client = {
    async callTool(name, params) {
      calls.push({ name, params });
      if (name === "agentbridge_host_notification_claim") {
        return {
          endpoint: {
            clientType: "telegram",
            conversationRef: sessionKey,
            route: { channel: "telegram", to: "1001" },
          },
          notifications: [
            {
              deliveryId: "delivery-timeline-image-1234567890",
              deliveryMode: "timeline_message",
              message: "【网页端 · 你】\n请识别图片",
              attachments: [
                {
                  attachmentId,
                  type: "image",
                  mimeType: "image/png",
                  fileName: "clipboard.png",
                  mediaUrl: `${CARD_ORIGIN}/media/${attachmentId}/file`,
                  ordinal: 0,
                },
              ],
            },
          ],
        };
      }
      return { status: "succeeded" };
    },
  };

  await coordinator.deliverEndpointNotifications(
    { restoreSessionBinding: () => true },
    {
      key: "telegram:*:1001",
      channel: "telegram",
      senderId: "1001",
      accountId: null,
    },
    client,
    new AbortController().signal,
  );

  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(harness.sentPayloads[0].payload.mediaUrl, "C:/media/timeline.png");
  assert.match(harness.sentPayloads[0].payload.text, /请识别图片/);
  const acknowledgement = calls.find(
    (item) => item.name === "agentbridge_host_notification_ack",
  );
  assert.equal(acknowledgement.params.succeeded, true);
});

test("falls back to governed timeline image links on a text-only channel", async () => {
  const sessionKey = "agent:main:openclaw-weixin:direct:user-a";
  const harness = fakeApi({ autoPoll: false });
  harness.api.runtime.channel.outbound.loadAdapter = async () => ({
    async sendText(context) {
      harness.sentPayloads.push({ payload: { text: context.text } });
      return { channel: "openclaw-weixin", messageId: "message-1" };
    },
  });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  const attachmentId = "b".repeat(43);
  const client = {
    async callTool(name) {
      if (name === "agentbridge_host_notification_claim") {
        return {
          endpoint: {
            clientType: "openclaw-weixin",
            conversationRef: sessionKey,
            route: { channel: "openclaw-weixin", to: "user-a" },
          },
          notifications: [
            {
              deliveryId: "delivery-timeline-link-1234567890",
              deliveryMode: "timeline_message",
              message: "【网页端 · 你】\n请识别图片",
              attachments: [
                {
                  attachmentId,
                  type: "image",
                  mimeType: "image/png",
                  fileName: "clipboard.png",
                  mediaUrl: `${CARD_ORIGIN}/media/${attachmentId}/file`,
                  ordinal: 0,
                },
              ],
            },
          ],
        };
      }
      return { status: "succeeded" };
    },
  };

  await coordinator.deliverEndpointNotifications(
    { restoreSessionBinding: () => true },
    {
      key: "openclaw-weixin:*:user-a",
      channel: "openclaw-weixin",
      senderId: "user-a",
      accountId: null,
    },
    client,
    new AbortController().signal,
  );

  assert.equal(harness.sentPayloads.length, 1);
  assert.match(harness.sentPayloads[0].payload.text, /clipboard\.png/);
  assert.match(harness.sentPayloads[0].payload.text, /\/media\/.+\/file/);
});

test("defers an undeliverable WeChat notification until the next inbound activity", async () => {
  const sessionKey =
    "agent:main:openclaw-weixin:direct:wechat-user-a@im.wechat";
  const harness = fakeApi({ autoPoll: false });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  coordinator.deliverTextDirect = async () => false;
  const calls = [];
  const client = {
    async callTool(name, params) {
      calls.push({ name, params });
      if (name === "agentbridge_host_notification_claim") {
        return {
          endpoint: {
            clientType: "openclaw-weixin",
            conversationRef: sessionKey,
            route: {
              channel: "openclaw-weixin",
              to: "wechat-user-a@im.wechat",
            },
          },
          notifications: [
            {
              deliveryId: "delivery-wechat-deferred-1234567890",
              deliveryMode: "timeline_message",
              message: "[Web - Assistant]\nOA query completed.",
            },
          ],
        };
      }
      return { status: "succeeded" };
    },
  };

  await coordinator.deliverEndpointNotifications(
    { restoreSessionBinding: () => true },
    {
      key: "openclaw-weixin:*:wechat-user-a@im.wechat",
      channel: "openclaw-weixin",
      senderId: "wechat-user-a@im.wechat",
      accountId: null,
    },
    client,
    new AbortController().signal,
  );

  const acknowledgement = calls.find(
    (item) => item.name === "agentbridge_host_notification_ack",
  );
  assert.equal(acknowledgement.params.succeeded, false);
  assert.equal(acknowledgement.params.defer_until_activity, true);
});

test("backs off idle notification polling and resets after activity", () => {
  assert.deepEqual(
    [1, 2, 3, 4, 5].map((idleRounds) =>
      notificationPumpDelay(2_000, idleRounds),
    ),
    [2_000, 4_000, 8_000, 10_000, 10_000],
  );
  assert.equal(notificationPumpDelay(2_000, 0), 2_000);
});

test("starts an independent notification pump for every configured identity", async () => {
  const harness = fakeApi({ autoPoll: false });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  const started = [];
  const releases = [];
  coordinator.runEndpointNotificationPump = async (
    _identityRouter,
    binding,
  ) =>
    new Promise((resolve) => {
      started.push(binding.key);
      releases.push(resolve);
    });
  const identityRouter = {
    configuredIdentities: () => [
      { binding: { key: "telegram:*:1001" }, client: { id: "a" } },
      { binding: { key: "openclaw-weixin:*:user-2" }, client: { id: "b" } },
    ],
  };

  const pump = coordinator.runNotificationPump(
    identityRouter,
    new AbortController().signal,
    2_000,
  );
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(started, [
    "telegram:*:1001",
    "openclaw-weixin:*:user-2",
  ]);
  releases.forEach((release) => release());
  await pump;
});

test("does not block inbound processing on timeline publication", async () => {
  const senderId = "wechat-user-nonblocking";
  const sessionKey =
    "agent:main:openclaw-weixin:direct:wechat-user-nonblocking";
  let releaseFetch;
  const harness = fakeApi({
    autoPoll: false,
    syncTimeline: true,
    mcpUrl: "https://10.10.50.213:8790/mcp",
    identityBindings: [
      {
        channel: "openclaw-weixin",
        senderId,
        tokenEnv: "USER_TOKEN",
      },
    ],
  });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    env: { USER_TOKEN: "token-a" },
    fetchImpl: async (_url, options) =>
      new Promise((resolve) => {
        releaseFetch = () => {
          const body = JSON.parse(options.body);
          resolve(
            new Response(
              JSON.stringify({
                jsonrpc: "2.0",
                id: body.id,
                result: { structuredContent: { status: "succeeded" } },
              }),
              { status: 200, headers: { "Content-Type": "application/json" } },
            ),
          );
        };
      }),
  });

  const hookResult = harness.hooks.message_received(
    {
      from: senderId,
      senderId,
      sessionKey,
      content: "Check OA session status.",
      messageId: "incoming-nonblocking-1",
    },
    {
      channelId: "openclaw-weixin",
      conversationId: senderId,
      sessionKey,
    },
  );

  assert.equal(hookResult, undefined);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(typeof releaseFetch, "function");
  releaseFetch();
  await coordinator.waitForIdle();
});

test("retries timeline publication with one stable idempotency key", async () => {
  const senderId = "wechat-user-retry";
  const sessionKey = "agent:main:openclaw-weixin:direct:wechat-user-retry";
  const requests = [];
  const retryDelays = [];
  const harness = fakeApi({
    autoPoll: false,
    syncTimeline: true,
    mcpUrl: "https://10.10.50.213:8790/mcp",
    identityBindings: [
      {
        channel: "openclaw-weixin",
        senderId,
        tokenEnv: "USER_TOKEN",
      },
    ],
  });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    env: { USER_TOKEN: "token-a" },
    timelineSleep: async (milliseconds) => retryDelays.push(milliseconds),
    fetchImpl: async (_url, options) => {
      const body = JSON.parse(options.body);
      requests.push(body);
      if (requests.length < 3) {
        throw new TypeError("temporary network failure");
      }
      return new Response(
        JSON.stringify({
          jsonrpc: "2.0",
          id: body.id,
          result: { structuredContent: { status: "succeeded" } },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    },
  });

  harness.hooks.message_received(
    {
      from: senderId,
      senderId,
      sessionKey,
      content: "Check OA session status.",
      messageId: "incoming-retry-1",
    },
    {
      channelId: "openclaw-weixin",
      conversationId: senderId,
      sessionKey,
    },
  );
  await coordinator.waitForIdle();

  assert.equal(requests.length, 3);
  assert.deepEqual(retryDelays, [250, 1_000]);
  assert.equal(
    new Set(
      requests.map((body) => body.params.arguments.message_key),
    ).size,
    1,
  );
  assert.equal(harness.logs.warn.length, 0);
});

test("synchronizes user and assistant text once across duplicate WeChat hooks", async () => {
  const requests = [];
  const senderId = "wechat-user-a";
  const sessionKey =
    "agent:main:openclaw-weixin:direct:wechat-user-a";
  const harness = fakeApi({
    autoPoll: false,
    syncTimeline: true,
    mcpUrl: "https://10.10.50.213:8790/mcp",
    identityBindings: [
      {
        channel: "openclaw-weixin",
        senderId,
        tokenEnv: "USER_TOKEN",
        label: "User A",
      },
    ],
  });
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    env: { USER_TOKEN: "token-a" },
    fetchImpl: async (_url, options) => {
      const body = JSON.parse(options.body);
      requests.push(body);
      return new Response(
        JSON.stringify({
          jsonrpc: "2.0",
          id: body.id,
          result: {
            structuredContent: { status: "succeeded" },
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    },
  });
  const context = {
    channelId: "openclaw-weixin",
    conversationId: senderId,
    sessionKey,
  };

  await harness.hooks.message_received(
    {
      from: senderId,
      senderId,
      sessionKey,
      content: "Submit a business trip request.",
      messageId: "incoming-1",
    },
    context,
  );
  harness.hooks.message_sending(
    {
      to: senderId,
      content: "Please confirm the trusted card.",
      messageId: "outgoing-1",
    },
    context,
  );
  harness.hooks.reply_payload_sending(
    {
      kind: "final",
      channel: "openclaw-weixin",
      sessionKey,
      messageId: "outgoing-2",
      payload: { text: "Please confirm the trusted card." },
    },
    context,
  );
  await coordinator.waitForIdle();

  const timelineCalls = requests.filter(
    (body) =>
      body.params?.name === "agentbridge_host_timeline_append",
  );
  assert.equal(timelineCalls.length, 2);
  assert.deepEqual(
    timelineCalls.map((body) => body.params.arguments.role),
    ["user", "assistant"],
  );
  assert.deepEqual(
    timelineCalls.map((body) => body.params.arguments.text),
    [
      "Submit a business trip request.",
      "Please confirm the trusted card.",
    ],
  );
  assert.equal(
    new Set(
      timelineCalls.map(
        (body) => body.params.arguments.endpoint_key,
      ),
    ).size,
    1,
  );
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
test("replays a Yuque document search after interactive credential login", async () => {
  const harness = fakeApi({
    autoPoll: true,
    pollIntervalSeconds: 1,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:wechat:direct:yuque-user";
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
      assert.equal(name, "yuque_document_search");
      return {
        status: "succeeded",
        result: {
          count: 1,
          items: [
            {
              id: "262116028",
              slug: "xc22kk0yg6ovnaht",
              title: "物联网平台对接说明",
              type: "Doc",
              book: { name: "共享文档" },
              snippet: null,
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
  bindDeliveryRoute(harness, { sessionKey, to: "yuque-user", channel: "wechat" });

  const loginRequired = {
    protocolVersion: "0.1",
    status: "requires_user_action",
    error: { code: "LOGIN_REQUIRED" },
    nextAction: { type: "session_login", system: "yuque" },
  };
  bindToolCall(harness, {
    toolCallId: "tool-yuque-before-login",
    runId: "run-yuque-before-login",
    sessionKey,
    toolName: "yuque_document_search",
    params: {
      query: "物联网平台",
      book: "共享文档",
      page: 2,
      limit: 15,
      idempotency_key: "must-not-be-replayed",
    },
  });
  harness.middleware(
    {
      toolCallId: "tool-yuque-before-login",
      toolName: "yuque_document_search",
      result: {
        content: [{ type: "text", text: JSON.stringify(loginRequired) }],
        details: {
          mcpServer: "agentbridge",
          mcpTool: "yuque_document_search",
          structuredContent: loginRequired,
        },
      },
    },
    { runtime: "openclaw" },
  );
  bindToolCall(harness, {
    toolCallId: "tool-login-for-yuque",
    runId: "run-yuque-before-login",
    sessionKey,
  });
  harness.middleware(
    {
      toolCallId: "tool-login-for-yuque",
      toolName: "yuque_session_login",
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
      "yuque_document_search",
    ],
  );
  assert.deepEqual(calls.at(-1).arguments_, {
    query: "物联网平台",
    book: "共享文档",
    page: 2,
    limit: 15,
  });
  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(
    harness.sentPayloads[0].payload.text.includes("部门信息库 登录已恢复"),
    true,
  );
  assert.equal(
    harness.sentPayloads[0].payload.text.includes("物联网平台对接说明"),
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
    "OA 会议已创建并发送。",
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
    "OA \u5468\u62a5\u53d1\u9001\u6d41\u7a0b\u5df2\u9605\u529e\u3002",
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
    "OA \u52b3\u52a8\u5408\u540c\u7eed\u7b7e\u8868\u5df2\u5ba1\u6279\u901a\u8fc7\u3002",
  );
});

test("reports a verified intellectual-property declaration approval", async () => {
  const harness = fakeApi({
    autoPoll: true,
    pollIntervalSeconds: 1,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  const pending = interaction({
    interactionId: "interaction-intellectual-property-authorization-123456",
    type: "execution_authorization",
    title: "Approve intellectual-property declaration",
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
          workflow_profile: "intellectual_property_declaration",
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
    toolCallId: "tool-intellectual-property-authorization",
    runId: "run-intellectual-property-authorization",
    sessionKey,
  });
  harness.middleware(
    {
      toolCallId: "tool-intellectual-property-authorization",
      toolName: "oa_intellectual_property_declaration_approval_prepare",
      result: toolResult(pending),
    },
    { runtime: "openclaw" },
  );

  await coordinator.waitForIdle();

  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(
    harness.sentPayloads[0].payload.text,
    "OA \u77e5\u8bc6\u4ea7\u6743\u7533\u62a5\u5ba1\u6279\u5355\u5df2\u5ba1\u6279\u901a\u8fc7\u3002",
  );
});

test("reports a verified monthly-attendance confirmation after authorization resumes", async () => {
  const harness = fakeApi({
    autoPoll: true,
    pollIntervalSeconds: 1,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  const pending = interaction({
    interactionId: "interaction-attendance-confirmation-123456",
    type: "execution_authorization",
    title: "Confirm monthly attendance",
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
          action_kind: "confirmation",
          workflow_profile: "attendance_confirmation",
          workflow_confirmed: true,
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
    toolCallId: "tool-attendance-confirmation",
    runId: "run-attendance-confirmation",
    sessionKey,
  });
  harness.middleware(
    {
      toolCallId: "tool-attendance-confirmation",
      toolName: "oa_attendance_confirmation_prepare",
      result: toolResult(pending),
    },
    { runtime: "openclaw" },
  );

  await coordinator.waitForIdle();

  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(
    harness.sentPayloads[0].payload.text,
    "OA \u6708\u5ea6\u8003\u52e4\u786e\u8ba4\u5355\u5df2\u786e\u8ba4\u5e76\u63d0\u4ea4\u3002",
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
    "OA 出差申请已提交审批。",
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
    "OA 请假申请已提交审批。",
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
    "OA 已发流程已撤销。",
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

  assert.equal(harness.sentPayloads[0].payload.text, "泰华工作日志已提交。");
  assert.equal(
    harness.sentPayloads[1].payload.text.includes("该日期已有工作日志"),
    true,
  );
  assert.equal(
    harness.sentPayloads[1].payload.text.includes("TAIHUA_BUSINESS_RULE_REJECTED"),
    true,
  );
});
test("reports verified Smartlight alarm remark update and business rejection", async () => {
  const harness = fakeApi({
    autoPoll: false,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:telegram:direct:smartlight-user";
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  bindDeliveryRoute(harness, {
    sessionKey,
    to: "smartlight-user",
  });

  await coordinator.deliverStatusDirect(sessionKey, "succeeded", null, {
    result: {
      status: "updated",
      alarm: { alarmId: "alarm-1", remark: "现场已复核" },
      verification: { matched: true },
    },
  });
  await coordinator.deliverStatusDirect(
    sessionKey,
    "failed",
    "SMARTLIGHT_BUSINESS_RULE_REJECTED",
    {
      error: {
        code: "SMARTLIGHT_BUSINESS_RULE_REJECTED",
        message: "授权后目标告警备注已被其他操作修改。",
      },
    },
  );

  assert.equal(
    harness.sentPayloads[0].payload.text,
    "照明 RTU 告警备注已修改并回读确认。",
  );
  assert.equal(
    harness.sentPayloads[1].payload.text.includes("其他操作修改"),
    true,
  );
  assert.equal(
    harness.sentPayloads[1].payload.text.includes(
      "SMARTLIGHT_BUSINESS_RULE_REJECTED",
    ),
    true,
  );
});
test("reports each verified Smartlight RTU action with exact business wording", async () => {
  const harness = fakeApi({
    autoPoll: false,
    wakeAgentOnComplete: true,
  });
  const sessionKey = "agent:main:telegram:direct:smartlight-rtu-user";
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
  });
  bindDeliveryRoute(harness, {
    sessionKey,
    to: "smartlight-rtu-user",
  });

  for (const action of ["submit_work_area", "revoke_work_area", "dispose"]) {
    await coordinator.deliverStatusDirect(sessionKey, "succeeded", null, {
      result: {
        status: "succeeded",
        action,
        alarm: { alarmId: `alarm-${action}` },
        verification: { matched: true },
      },
    });
  }

  assert.deepEqual(
    harness.sentPayloads.map((item) => item.payload.text),
    [
      "照明 RTU 告警已提交工区并回读确认。",
      "照明 RTU 告警工区提交已撤回并回读确认。",
      "照明 RTU 告警已标记为已处置并回读确认。",
    ],
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
    sleep: async () => undefined,
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

  assert.equal(
    replacement.result.details.structuredContent.hostDelivery.state,
    "delivered",
  );
  assert.equal(
    replacement.result.details.structuredContent.hostDelivery.attachmentSentCount,
    1,
  );
  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(harness.sentPayloads[0].payload.mediaUrl, "C:/media/certificate.bin");
  assert.equal(harness.sentPayloads[0].payload.forceDocument, true);
  assert.match(harness.sentPayloads[0].payload.text, /certificate-a\.pdf/);
});

test("delivers a prepared Smartlight CSV report as a document attachment", async () => {
  const harness = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
    sleep: async () => undefined,
    ...preparedDocumentDependencies({
      contentType: "text/csv",
      body: Buffer.from("\ufeffdate,count\r\n2026-08-12,3\r\n", "utf8"),
      savedId: "smartlight-report.csv",
      savedPath: "C:/media/smartlight-report.csv",
    }),
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  bindDeliveryRoute(harness, { sessionKey, to: "7052061588" });
  bindToolCall(harness, {
    toolCallId: "tool-smartlight-report-delivery",
    runId: "run-smartlight-report-delivery",
    sessionKey,
    toolName: "smartlight_report_export",
  });

  const replacement = await harness.middleware(
    {
      toolCallId: "tool-smartlight-report-delivery",
      toolName: "smartlight_report_export",
      result: preparedDocumentResult(
        "smartlight-alarm-analysis-20260812.csv",
        "s".repeat(43),
      ),
    },
    { runtime: "openclaw", sessionKey },
  );

  assert.equal(
    replacement.result.details.structuredContent.hostDelivery.state,
    "delivered",
  );
  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(
    harness.sentPayloads[0].payload.mediaUrl,
    "C:/media/smartlight-report.csv",
  );
  assert.equal(harness.sentPayloads[0].payload.forceDocument, true);
  assert.match(
    harness.sentPayloads[0].payload.text,
    /smartlight-alarm-analysis-20260812\.csv/,
  );
});

test("delivers a prepared OA certificate batch as ordered original files", async () => {
  const harness = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
    ...preparedDocumentDependencies(),
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  bindDeliveryRoute(harness, { sessionKey, to: "7052061588" });
  bindToolCall(harness, {
    toolCallId: "tool-certificate-batch",
    runId: "run-certificate-batch",
    sessionKey,
    toolName: "oa_certificate_prepare_downloads",
  });

  const replacement = await harness.middleware(
    {
      toolCallId: "tool-certificate-batch",
      toolName: "oa_certificate_prepare_downloads",
      result: preparedDocumentBatchResult([
        ["certificate-a.jpg", "a".repeat(43)],
        ["certificate-b.pdf", "b".repeat(43)],
      ]),
    },
    { runtime: "openclaw", sessionKey },
  );

  assert.equal(harness.sentPayloads.length, 2);
  assert.deepEqual(
    harness.sentPayloads.map((item) => item.payload.forceDocument),
    [true, true],
  );
  assert.match(harness.sentPayloads[0].payload.text, /certificate-a\.jpg/);
  assert.match(harness.sentPayloads[1].payload.text, /certificate-b\.pdf/);
  assert.equal(
    replacement.result.details.structuredContent.hostDelivery.preparedCount,
    2,
  );
  assert.equal(
    replacement.result.details.structuredContent.hostDelivery.attachmentSentCount,
    2,
  );
});

test("retries one transient attachment failure before succeeding", async () => {
  const harness = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
    sleep: async () => undefined,
    ...preparedDocumentDependencies(),
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  bindDeliveryRoute(harness, { sessionKey, to: "7052061588" });
  let mediaAttempts = 0;
  harness.api.runtime.channel.outbound.loadAdapter = async () => ({
    async sendPayload(context) {
      if (context.payload.mediaUrl) {
        mediaAttempts += 1;
        if (mediaAttempts === 1) {
          const error = new Error("telegram upload timed out");
          error.code = "ETIMEDOUT";
          throw error;
        }
      }
      harness.sentPayloads.push(context);
      return { channel: "telegram", messageId: "attachment-message" };
    },
  });
  bindToolCall(harness, {
    toolCallId: "tool-certificate-retry",
    runId: "run-certificate-retry",
    sessionKey,
    toolName: "oa_certificate_prepare_download",
  });

  const replacement = await harness.middleware(
    {
      toolCallId: "tool-certificate-retry",
      toolName: "oa_certificate_prepare_download",
      result: preparedDocumentResult("certificate-retry.pdf", "r".repeat(43)),
    },
    { runtime: "openclaw", sessionKey },
  );

  assert.equal(mediaAttempts, 2);
  assert.equal(harness.sentPayloads.length, 1);
  assert.equal(
    replacement.result.details.structuredContent.hostDelivery.files[0].attemptCount,
    2,
  );
  assert.equal(
    replacement.result.details.structuredContent.hostDelivery.fallbackLinkSentCount,
    0,
  );
});

test("falls back to a short-lived download link when attachment upload fails", async () => {
  const harness = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
    sleep: async () => undefined,
    ...preparedDocumentDependencies(),
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  bindDeliveryRoute(harness, { sessionKey, to: "7052061588" });
  let attempts = 0;
  harness.api.runtime.channel.outbound.loadAdapter = async () => ({
    async sendPayload(context) {
      attempts += 1;
      if (context.payload.mediaUrl) {
        const error = new Error("telegram upload timed out");
        error.code = "ETIMEDOUT";
        throw error;
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

  assert.equal(attempts, 3);
  assert.equal(harness.sentPayloads.length, 1);
  assert.match(harness.sentPayloads[0].payload.text, /附件上传失败/);
  assert.match(harness.sentPayloads[0].payload.text, /\/file/);
});

test("treats the Workspace task-card download as a successful fallback delivery", async () => {
  const harness = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
    ...preparedDocumentDependencies(),
  });
  const sessionKey =
    "agent:main:agentbridge-workspace:direct:account-123";
  bindDeliveryRoute(harness, {
    sessionKey,
    channel: "webchat",
    to: "account-123",
  });
  bindToolCall(harness, {
    toolCallId: "tool-certificate-workspace",
    runId: "run-certificate-workspace",
    sessionKey,
    toolName: "oa_certificate_prepare_download",
  });

  const replacement = await harness.middleware(
    {
      toolCallId: "tool-certificate-workspace",
      toolName: "oa_certificate_prepare_download",
      result: preparedDocumentResult(
        "certificate-workspace.pdf",
        "w".repeat(43),
      ),
    },
    { runtime: "openclaw", sessionKey },
  );

  const report = replacement.result.details.structuredContent.hostDelivery;
  assert.equal(report.state, "delivered");
  assert.equal(report.attachmentSentCount, 0);
  assert.equal(report.fallbackLinkSentCount, 1);
  assert.equal(report.failedCount, 0);
  assert.equal(report.endpointMode, "workspace_download");
  assert.match(report.userMessage, /网页任务卡已生成 1 个下载入口/);
  assert.doesNotMatch(report.userMessage, /0 份已作为附件发送/);
  assert.equal(harness.sentPayloads.length, 0);
});

test("deduplicates a repeated prepared-document tool result", async () => {
  const harness = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(harness.api, {
    mcpClient: null,
    ...preparedDocumentDependencies(),
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  bindDeliveryRoute(harness, { sessionKey, to: "7052061588" });
  bindToolCall(harness, {
    toolCallId: "tool-certificate-deduplicated",
    runId: "run-certificate-deduplicated",
    sessionKey,
    toolName: "oa_certificate_prepare_download",
  });
  const event = {
    toolCallId: "tool-certificate-deduplicated",
    toolName: "oa_certificate_prepare_download",
    result: preparedDocumentResult("certificate-dedupe.pdf", "d".repeat(43)),
  };

  await harness.middleware(event, { runtime: "openclaw", sessionKey });
  await harness.middleware(event, { runtime: "openclaw", sessionKey });

  assert.equal(harness.sentPayloads.length, 1);
});

test("reports exact prepared-document outcomes to the central task", async () => {
  const calls = [];
  const harness = fakeApi({ autoPoll: false });
  registerAgentBridgeInteractions(harness.api, {
    mcpClient: {
      async callTool(name, params, options) {
        calls.push({ name, params, options });
        return { status: "succeeded" };
      },
    },
    ...preparedDocumentDependencies(),
  });
  const sessionKey = "agent:main:telegram:direct:7052061588";
  bindDeliveryRoute(harness, { sessionKey, to: "7052061588" });
  bindToolCall(harness, {
    toolCallId: "tool-certificate-report",
    runId: "run-certificate-report",
    sessionKey,
    toolName: "oa_certificate_prepare_downloads",
  });

  const replacement = await harness.middleware(
    {
      toolCallId: "tool-certificate-report",
      toolName: "oa_certificate_prepare_downloads",
      result: preparedDocumentBatchResult(
        [
          ["certificate-a.pdf", "a".repeat(43)],
          ["certificate-b.pdf", "b".repeat(43)],
        ],
        { taskId: "task-certificate-1234567890" },
      ),
    },
    { runtime: "openclaw", sessionKey },
  );

  const reportCall = calls.find(
    (call) => call.name === "agentbridge_host_artifact_delivery_report",
  );
  assert.ok(reportCall);
  assert.equal(reportCall.params.task_id, "task-certificate-1234567890");
  assert.equal(reportCall.params.files.length, 2);
  assert.deepEqual(
    reportCall.params.files.map((file) => file.state),
    ["attachment_sent", "attachment_sent"],
  );
  assert.equal(
    replacement.result.details.structuredContent.hostDelivery.userMessage,
    "2 份文件已准备，2 份已作为附件发送。",
  );
});

function preparedDocumentDependencies({
  contentType = "application/pdf",
  body = Buffer.from("%PDF-1.7 prepared"),
  savedId = "certificate.bin",
  savedPath = "C:/media/certificate.bin",
} = {}) {
  return {
    documentFetchImpl: async () => ({
      ok: true,
      status: 200,
      headers: {
        get(name) {
          return name.toLowerCase() === "content-type"
            ? contentType
            : name.toLowerCase() === "content-length"
              ? String(body.length)
              : null;
        },
      },
      async arrayBuffer() {
        return body;
      },
    }),
    saveMediaBufferImpl: async () => ({
      id: savedId,
      path: savedPath,
      size: body.length,
      contentType,
    }),
  };
}

function preparedDocumentResult(filename, downloadId, { taskId = null } = {}) {
  const structuredContent = {
    protocolVersion: "0.1",
    schemaVersion: "agentbridge.document_delivery.v1",
    status: "succeeded",
    file: {
      downloadId,
      filename,
      contentType: filename.endsWith(".pdf")
        ? "application/pdf"
        : filename.endsWith(".csv")
          ? "text/csv"
          : "image/jpeg",
      size: 128,
      mediaUrl: `${CARD_ORIGIN}/download/${downloadId}/file`,
      expiresAt: "2099-07-14T12:00:00+00:00",
      artifactId: `artifact-${downloadId}`,
    },
  };
  return {
    content: [{ type: "text", text: JSON.stringify(structuredContent) }],
    details: {
      mcpServer: "agentbridge",
      mcpTool: "oa_certificate_prepare_download",
      structuredContent,
      ...(taskId ? { agentbridgeTaskId: taskId } : {}),
    },
  };
}

function preparedDocumentBatchResult(entries, { taskId = null } = {}) {
  const structuredContent = {
    protocolVersion: "0.1",
    schemaVersion: "agentbridge.document_delivery_batch.v1",
    status: "succeeded",
    requestedCount: entries.length,
    preparedCount: entries.length,
    failedCount: 0,
    files: entries.map(([filename, downloadId]) => ({
      downloadId,
      filename,
      contentType: filename.endsWith(".pdf") ? "application/pdf" : "image/jpeg",
      size: 128,
      mediaUrl: `${CARD_ORIGIN}/download/${downloadId}/file`,
      expiresAt: "2099-07-14T12:00:00+00:00",
      artifactId: `artifact-${downloadId}`,
    })),
    errors: [],
  };
  return {
    content: [{ type: "text", text: JSON.stringify(structuredContent) }],
    details: {
      mcpServer: "agentbridge",
      mcpTool: "oa_certificate_prepare_downloads",
      structuredContent,
      ...(taskId ? { agentbridgeTaskId: taskId } : {}),
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
    toolFactory: null,
    gatewayMethods: new Map(),
  };
  const api = {
    pluginConfig: {
      allowedCardOrigins: [CARD_ORIGIN],
      syncTimeline: false,
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
    registerTool(factory) {
      state.toolFactory = factory;
    },
    on(name, handler) {
      hooks[name] = handler;
    },
    registerCommand(command) {
      state.command = command;
    },
    registerGatewayMethod(name, handler, options) {
      state.gatewayMethods.set(name, { handler, options });
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
    get toolFactory() {
      return state.toolFactory;
    },
    get gatewayMethods() {
      return state.gatewayMethods;
    },
  };
}
