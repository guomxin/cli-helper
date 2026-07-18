import test from "node:test";
import assert from "node:assert/strict";

import {
  createAgentBridgeMcpClient,
  extractToolPayload,
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
