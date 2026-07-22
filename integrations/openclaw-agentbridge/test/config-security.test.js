import test from "node:test";
import assert from "node:assert/strict";

import { resolvePluginConfig } from "../lib/config.js";

test("requires a dedicated MCP URL when identity routing is enabled", () => {
  assert.throws(
    () =>
      resolvePluginConfig({
        identityBindings: [
          {
            channel: "telegram",
            senderId: "1001",
            tokenEnv: "TOKEN_A",
          },
        ],
      }),
    /mcpUrl is required/,
  );
});

test("ignores malformed identity bindings instead of accepting model-like input", () => {
  const config = resolvePluginConfig({
    mcpUrl: "https://10.10.50.213:8790/mcp",
    identityBindings: [
      { channel: "telegram", senderId: "", tokenEnv: "TOKEN_A" },
      { channel: "telegram", senderId: "1001", tokenEnv: "not valid" },
      "telegram:1001",
    ],
  });

  assert.deepEqual(config.identityBindings, []);
});
