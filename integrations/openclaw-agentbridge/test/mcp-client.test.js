import test from "node:test";
import assert from "node:assert/strict";

import {
  createAgentBridgeMcpClient,
  extractToolPayload,
  McpCallError,
  parseMcpResponse,
} from "../lib/mcp-client.js";

test("calls the configured MCP server with an environment-resolved bearer header", async () => {
  const requests = [];
  const client = createAgentBridgeMcpClient({
    hostConfig: {
      mcp: {
        servers: {
          agentbridge: {
            url: "http://10.10.50.213:8790/mcp",
            headers: { Authorization: "Bearer ${AGENTBRIDGE_TEST_TOKEN}" },
            timeout: 5,
          },
        },
      },
    },
    serverName: "agentbridge",
    env: { AGENTBRIDGE_TEST_TOKEN: "secret-token" },
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return new Response(
        JSON.stringify({
          jsonrpc: "2.0",
          id: "response-id",
          result: {
            content: [{ type: "text", text: JSON.stringify({ status: "succeeded" }) }],
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    },
  });

  const response = await client.callTool("agentbridge_interaction_get", {
    interaction_id: "interaction-1234567890",
  });

  assert.deepEqual(response, { status: "succeeded" });
  assert.equal(requests.length, 1);
  assert.equal(requests[0].options.headers.Authorization, "Bearer secret-token");
  assert.equal(JSON.stringify(requests[0]).includes("AGENTBRIDGE_TEST_TOKEN"), false);
});

test("lists tools through the same authenticated MCP transport", async () => {
  let requestBody;
  const client = createAgentBridgeMcpClient({
    hostConfig: {
      mcp: {
        servers: {
          agentbridge: {
            url: "http://10.10.50.213:8790/mcp",
            headers: { Authorization: "Bearer test-token" },
          },
        },
      },
    },
    serverName: "agentbridge",
    fetchImpl: async (_url, options) => {
      requestBody = JSON.parse(options.body);
      return new Response(
        JSON.stringify({
          jsonrpc: "2.0",
          id: "response-id",
          result: { tools: [{ name: "oa_missed_punch_prepare" }] },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    },
  });

  assert.deepEqual(await client.listTools(), [{ name: "oa_missed_punch_prepare" }]);
  assert.equal(requestBody.method, "tools/list");
  assert.deepEqual(requestBody.params, {});
});

test("preserves the underlying transport cause when MCP is unreachable", async () => {
  const socketError = new Error("socket closed");
  socketError.code = "ECONNRESET";
  const fetchError = new TypeError("fetch failed", { cause: socketError });
  const client = createAgentBridgeMcpClient({
    endpoint: {
      url: "https://agentbridge.example.test/mcp",
      timeoutSeconds: 5,
    },
    tokenEnv: "TOKEN",
    env: { TOKEN: "secret-token" },
    fetchImpl: async () => {
      throw fetchError;
    },
  });

  await assert.rejects(
    client.callTool("oa_workflow_pending_list", { limit: 5 }),
    (error) => {
      assert.equal(error instanceof McpCallError, true);
      assert.equal(error.code, "MCP_UNREACHABLE");
      assert.equal(error.transportCode, "ECONNRESET");
      assert.equal(error.retryable, true);
      assert.equal(error.cause, fetchError);
      assert.equal(error.attempts, 1);
      return true;
    },
  );
});

test("retries a caller-approved idempotent MCP call and reports recovery", async () => {
  const retries = [];
  const recoveries = [];
  const sleeps = [];
  let attempts = 0;
  const client = createAgentBridgeMcpClient({
    endpoint: {
      url: "https://agentbridge.example.test/mcp",
      timeoutSeconds: 5,
    },
    tokenEnv: "TOKEN",
    env: { TOKEN: "secret-token" },
    fetchImpl: async () => {
      attempts += 1;
      if (attempts === 1) {
        const socketError = new Error("connection reset");
        socketError.code = "ECONNRESET";
        throw new TypeError("fetch failed", { cause: socketError });
      }
      return new Response(
        JSON.stringify({
          jsonrpc: "2.0",
          id: "response-id",
          result: {
            structuredContent: { status: "succeeded" },
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    },
  });

  const result = await client.callTool(
    "oa_missed_punch_approval_prepare",
    {
      affair_id: "affair-123",
      opinion: "同意",
      idempotency_key: "openclaw:tool-123",
    },
    {
      retry: {
        delaysMs: [500, 2_000],
        sleep: async (delayMs) => sleeps.push(delayMs),
        onRetry: (event) => retries.push(event),
        onRecovered: (event) => recoveries.push(event),
      },
    },
  );

  assert.deepEqual(result, { status: "succeeded" });
  assert.equal(attempts, 2);
  assert.deepEqual(sleeps, [500]);
  assert.equal(retries.length, 1);
  assert.equal(retries[0].error.transportCode, "ECONNRESET");
  assert.equal(retries[0].nextAttempt, 2);
  assert.equal(recoveries.length, 1);
  assert.equal(recoveries[0].attempts, 2);
  assert.equal(recoveries[0].lastError.transportCode, "ECONNRESET");
});

test("does not retry an unauthorized MCP response", async () => {
  let attempts = 0;
  const client = createAgentBridgeMcpClient({
    endpoint: {
      url: "https://agentbridge.example.test/mcp",
      timeoutSeconds: 5,
    },
    tokenEnv: "TOKEN",
    env: { TOKEN: "secret-token" },
    fetchImpl: async () => {
      attempts += 1;
      return new Response("unauthorized", { status: 401 });
    },
  });

  await assert.rejects(
    client.callTool("oa_workflow_pending_list", {}, {
      retry: {
        delaysMs: [1, 1],
        sleep: async () => {},
      },
    }),
    (error) => error.code === "MCP_HTTP_401" && error.retryable === false,
  );
  assert.equal(attempts, 1);
});

test("places trusted host metadata beside tool arguments", async () => {
  let requestBody;
  const client = createAgentBridgeMcpClient({
    endpoint: {
      url: "https://agentbridge.example.test/mcp",
      timeoutSeconds: 5,
    },
    tokenEnv: "TOKEN",
    env: { TOKEN: "secret-token" },
    fetchImpl: async (_url, options) => {
      requestBody = JSON.parse(options.body);
      return new Response(
        JSON.stringify({
          jsonrpc: "2.0",
          id: "response-id",
          result: {
            structuredContent: { status: "succeeded" },
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    },
  });

  await client.callTool(
    "agentbridge_host_task_ensure",
    { agent_host: "openclaw" },
    {
      meta: {
        "io.agentbridge/host": {
          version: "1",
          agentHost: "openclaw",
        },
      },
    },
  );

  assert.deepEqual(requestBody.params._meta, {
    "io.agentbridge/host": {
      version: "1",
      agentHost: "openclaw",
    },
  });
  assert.deepEqual(requestBody.params.arguments, {
    agent_host: "openclaw",
  });
});
test("does not create a polling client when the bearer environment value is absent", () => {
  const client = createAgentBridgeMcpClient({
    hostConfig: {
      mcp: {
        servers: {
          agentbridge: {
            url: "http://10.10.50.213:8790/mcp",
            headers: { Authorization: "Bearer ${MISSING_TOKEN}" },
          },
        },
      },
    },
    serverName: "agentbridge",
    env: {},
  });

  assert.equal(client, null);
});

test("preserves host-private metadata beside the structured tool payload", () => {
  const privateInteraction = {
    interactionId: "interaction-private-123456",
    presentation: { url: "https://cards.example.test/auth/opaque-token" },
  };
  const payload = extractToolPayload({
    structuredContent: { status: "requires_user_action" },
    _meta: { "io.agentbridge/interaction": privateInteraction },
  });

  assert.equal(payload.status, "requires_user_action");
  assert.equal(
    payload._meta["io.agentbridge/interaction"],
    privateInteraction,
  );
});
test("parses Streamable HTTP SSE data responses", () => {
  const parsed = parseMcpResponse(
    'event: message\ndata: {"jsonrpc":"2.0","id":"1","result":{"ok":true}}\n\n',
  );
  assert.deepEqual(parsed.result, { ok: true });
});
