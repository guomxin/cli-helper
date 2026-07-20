import test from "node:test";
import assert from "node:assert/strict";

import { resolveMcpServer } from "../lib/config.js";


function hostConfig(server) {
  return { mcp: { servers: { agentbridge: server } } };
}


test("defaults AgentBridge MCP calls to a write-safe 150 second timeout", () => {
  const resolved = resolveMcpServer(
    hostConfig({ url: "https://10.10.50.213:8790/mcp" }),
    "agentbridge",
  );

  assert.equal(resolved.timeoutSeconds, 150);
});


test("honors an explicit bounded AgentBridge MCP timeout", () => {
  const resolved = resolveMcpServer(
    hostConfig({ url: "https://10.10.50.213:8790/mcp", timeout: 180 }),
    "agentbridge",
  );

  assert.equal(resolved.timeoutSeconds, 180);
});