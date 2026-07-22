import test from "node:test";
import assert from "node:assert/strict";

import {
  matchIdentityBinding,
  resolveMcpEndpoint,
  resolvePluginConfig,
} from "../lib/config.js";
import { InteractionCoordinator } from "../lib/coordinator.js";
import { AgentBridgeIdentityRouter } from "../lib/identity-router.js";
import {
  AGENTBRIDGE_PROXY_TOOL_NAMES,
  createAgentBridgeProxyTools,
} from "../lib/proxy-tools.js";

const MCP_URL = "https://10.10.50.213:8790/mcp";

test("normalizes bindings and prefers an account-specific identity", () => {
  const config = multiUserConfig({
    identityBindings: [
      binding("1001", "TOKEN_GENERAL", { label: "general" }),
      binding("1001", "TOKEN_ACCOUNT", {
        accountId: "oa-bot",
        label: "account-specific",
      }),
    ],
  });

  assert.equal(
    matchIdentityBinding(config.identityBindings, {
      channel: "TELEGRAM",
      senderId: 1001,
      accountId: "oa-bot",
    }).label,
    "account-specific",
  );
  assert.equal(
    matchIdentityBinding(config.identityBindings, {
      channel: "telegram",
      senderId: "1001",
      accountId: "another-bot",
    }).label,
    "general",
  );
  assert.deepEqual(resolveMcpEndpoint(config, {}), {
    url: MCP_URL,
    timeoutSeconds: 150,
  });
});

test("rejects duplicate trusted sender selectors", () => {
  assert.throws(
    () =>
      multiUserConfig({
        identityBindings: [
          binding("1001", "TOKEN_A"),
          binding("1001", "TOKEN_B"),
        ],
      }),
    /duplicate AgentBridge identity binding/,
  );
});

test("routes two Telegram users to different MCP bearer tokens", async () => {
  const requests = [];
  const router = createRouter({
    requests,
    env: { TOKEN_A: "token-a", TOKEN_B: "token-b" },
  });
  const contextA = toolContext("1001");
  const contextB = toolContext("2002");

  const identityA = router.resolveToolContext(contextA);
  const identityB = router.resolveToolContext(contextB);
  await Promise.all([
    identityA.client.callTool("oa_session_status", {}),
    identityB.client.callTool("oa_session_status", {}),
  ]);

  assert.equal(identityA.bound, true);
  assert.equal(identityB.bound, true);
  assert.deepEqual(
    requests.map((request) => request.authorization).sort(),
    ["Bearer token-a", "Bearer token-b"],
  );
  assert.equal(JSON.stringify(identityA).includes("token-a"), false);
  assert.equal(JSON.stringify(identityB).includes("token-b"), false);
});

test("fails closed when one OpenClaw session changes Telegram identity", () => {
  const router = createRouter({
    requests: [],
    env: { TOKEN_A: "token-a", TOKEN_B: "token-b" },
  });
  const sessionKey = "agent:main:telegram:direct:shared-session";

  assert.equal(
    router.resolveToolContext(toolContext("1001", sessionKey)).bound,
    true,
  );
  const conflicted = router.resolveToolContext(toolContext("2002", sessionKey));

  assert.equal(conflicted.bound, false);
  assert.equal(conflicted.reason, "session_identity_conflict");
  assert.equal(router.clientForSession(sessionKey), null);
});

test("withholds OA tools from an unprovisioned Telegram user", async () => {
  const router = createRouter({
    requests: [],
    env: { TOKEN_A: "token-a", TOKEN_B: "token-b" },
  });
  const tools = createAgentBridgeProxyTools({
    context: toolContext("9999"),
    identityRouter: router,
    serverName: "agentbridge",
  });

  assert.deepEqual(tools.map((tool) => tool.name), [
    "agentbridge_identity_status",
  ]);
  const result = await tools[0].execute("status", {});
  assert.equal(result.details.structuredContent.status, "unbound");
  assert.equal(
    result.details.structuredContent.reason,
    "identity_not_provisioned",
  );
});

test("exposes the full catalog and proxies raw MCP metadata for a bound user", async () => {
  const requests = [];
  const router = createRouter({
    requests,
    env: { TOKEN_A: "token-a", TOKEN_B: "token-b" },
    responseResult: {
      content: [{ type: "text", text: "AgentBridge status" }],
      structuredContent: { status: "succeeded", authenticated: true },
      _meta: { "io.agentbridge/test": { private: true } },
    },
  });
  const tools = createAgentBridgeProxyTools({
    context: toolContext("1001"),
    identityRouter: router,
    serverName: "agentbridge",
  });

  assert.equal(tools.length, AGENTBRIDGE_PROXY_TOOL_NAMES.length);
  assert.equal(new Set(tools.map((tool) => tool.name)).size, tools.length);
  const statusTool = tools.find((tool) => tool.name === "oa_session_status");
  const result = await statusTool.execute("tool-call", {});

  assert.equal(requests[0].authorization, "Bearer token-a");
  assert.equal(requests[0].body.params.name, "oa_session_status");
  assert.equal(result.details.mcpServer, "agentbridge");
  assert.equal(result.details.mcpTool, "oa_session_status");
  assert.equal(result.structuredContent.authenticated, true);
  assert.deepEqual(result._meta, {
    "io.agentbridge/test": { private: true },
  });
});

test("pins coordinator records to their originating session clients", () => {
  const clients = {
    "session-a": { name: "client-a" },
    "session-b": { name: "client-b" },
  };
  const coordinator = new InteractionCoordinator({
    api: { logger: { info() {}, warn() {} } },
    config: {
      allowedCardOrigins: [],
      autoPoll: false,
      maxPollSeconds: 30,
      pollIntervalSeconds: 1,
      wakeAgentOnComplete: false,
    },
    mcpClientResolver: (sessionKey) => clients[sessionKey] || null,
  });

  const first = coordinator.upsert({
    interaction: { interactionId: "interaction-a-123456" },
    sessionKey: "session-a",
    runId: "run-a",
  });
  const second = coordinator.upsert({
    interaction: { interactionId: "interaction-b-123456" },
    sessionKey: "session-b",
    runId: "run-b",
  });

  assert.equal(first.mcpClient, clients["session-a"]);
  assert.equal(second.mcpClient, clients["session-b"]);
});

function multiUserConfig(overrides = {}) {
  return resolvePluginConfig({
    allowedCardOrigins: ["https://10.10.50.213:8780"],
    mcpUrl: MCP_URL,
    identityBindings: [
      binding("1001", "TOKEN_A", { label: "User A" }),
      binding("2002", "TOKEN_B", { label: "User B" }),
    ],
    ...overrides,
  });
}

function binding(senderId, tokenEnv, overrides = {}) {
  return {
    channel: "telegram",
    senderId,
    tokenEnv,
    ...overrides,
  };
}

function toolContext(
  senderId,
  sessionKey = `agent:main:telegram:direct:${senderId}`,
) {
  return {
    sessionKey,
    messageChannel: "telegram",
    requesterSenderId: senderId,
  };
}

function createRouter({ requests, env, responseResult = null }) {
  return new AgentBridgeIdentityRouter({
    config: multiUserConfig(),
    hostConfig: {},
    env,
    fetchImpl: async (_url, options) => {
      requests.push({
        authorization: options.headers.Authorization,
        body: JSON.parse(options.body),
      });
      return new Response(
        JSON.stringify({
          jsonrpc: "2.0",
          id: "response",
          result:
            responseResult || {
              content: [
                {
                  type: "text",
                  text: JSON.stringify({ status: "succeeded" }),
                },
              ],
            },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    },
  });
}
