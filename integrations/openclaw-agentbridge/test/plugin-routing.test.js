import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { registerAgentBridgeInteractions } from "../lib/plugin.js";
import { AGENTBRIDGE_PROXY_TOOL_NAMES } from "../lib/proxy-tools.js";

test("declares every native AgentBridge tool in the plugin contract", () => {
  const manifest = JSON.parse(
    readFileSync(
      new URL("../openclaw.plugin.json", import.meta.url),
      "utf8",
    ),
  );

  assert.deepEqual(manifest.contracts.tools, AGENTBRIDGE_PROXY_TOOL_NAMES);
});

test("registers native per-user tools and blocks the legacy global MCP surface", () => {
  const harness = fakeApi();
  const coordinator = registerAgentBridgeInteractions(harness.api, {
    env: { TOKEN_A: "token-a" },
    fetchImpl: async () => {
      throw new Error("not called");
    },
  });

  assert.deepEqual(harness.toolOptions.names, AGENTBRIDGE_PROXY_TOOL_NAMES);
  const tools = harness.toolFactory({
    sessionKey: "agent:main:telegram:direct:1001",
    messageChannel: "telegram",
    requesterSenderId: "1001",
  });
  assert.equal(tools.some((tool) => tool.name === "oa_session_status"), true);

  const blocked = harness.hooks.before_tool_call(
    {
      toolName: "agentbridge__oa_session_status",
      toolCallId: "legacy-tool-call",
      params: {},
    },
    { sessionKey: "agent:main:telegram:direct:1001" },
  );
  assert.equal(blocked.block, true);
  assert.match(blocked.blockReason, /identity-routed native AgentBridge tool/);

  const native = harness.hooks.before_tool_call(
    {
      toolName: "oa_session_status",
      toolCallId: "native-tool-call",
      params: {},
    },
    { sessionKey: "agent:main:telegram:direct:1001" },
  );
  assert.equal(native, undefined);

  harness.hooks.message_received(
    {
      sessionKey: "agent:main:telegram:direct:1001",
      channel: "telegram",
      senderId: "1001",
      from: "1001",
    },
    {
      sessionKey: "agent:main:telegram:direct:1001",
      channelId: "telegram",
      conversationId: "1001",
    },
  );
  assert.notEqual(
    coordinator.clientForSession("agent:main:telegram:direct:1001"),
    null,
  );

  harness.hooks.session_end(
    {
      reason: "reset",
      sessionKey: "agent:main:telegram:direct:1001",
    },
    { sessionKey: "agent:main:telegram:direct:1001" },
  );
  assert.equal(
    coordinator.clientForSession("agent:main:telegram:direct:1001"),
    null,
  );
});

function fakeApi() {
  const hooks = {};
  const state = {
    toolFactory: null,
    toolOptions: null,
  };
  const api = {
    pluginConfig: {
      allowedCardOrigins: ["https://10.10.50.213:8780"],
      mcpUrl: "https://10.10.50.213:8790/mcp",
      identityBindings: [
        {
          channel: "telegram",
          senderId: "1001",
          tokenEnv: "TOKEN_A",
          label: "User A",
        },
      ],
      autoPoll: true,
    },
    config: {
      mcp: {
        servers: {
          agentbridge: {
            url: "https://10.10.50.213:8790/mcp",
            headers: { Authorization: "Bearer ${LEGACY_TOKEN}" },
          },
        },
      },
    },
    logger: { info() {}, warn() {} },
    runtime: {
      system: {
        enqueueSystemEvent() {},
        requestHeartbeat() {},
      },
    },
    registerTool(factory, options) {
      state.toolFactory = factory;
      state.toolOptions = options;
    },
    registerAgentToolResultMiddleware() {},
    registerCommand() {},
    on(name, handler) {
      hooks[name] = handler;
    },
  };
  return {
    api,
    hooks,
    get toolFactory() {
      return state.toolFactory;
    },
    get toolOptions() {
      return state.toolOptions;
    },
  };
}
