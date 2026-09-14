import { readFileSync } from "node:fs";

const catalog = JSON.parse(
  readFileSync(new URL("./agentbridge-tools.json", import.meta.url), "utf8"),
);

if (
  catalog?.schemaVersion !== "agentbridge.openclaw-tool-catalog.v1" ||
  !Array.isArray(catalog.tools) ||
  !Array.isArray(catalog.userTurnSourceTools)
) {
  throw new Error("AgentBridge OpenClaw tool catalog is invalid");
}

export const AGENTBRIDGE_TOOL_CATALOG = Object.freeze(
  catalog.tools.map((tool) => Object.freeze(tool)),
);

// Generated from the central planning policy, rather than a second host allowlist.
export const AGENTBRIDGE_USER_TURN_SOURCE_TOOLS = Object.freeze(
  catalog.userTurnSourceTools,
);

export const AGENTBRIDGE_TOOL_NAMES = Object.freeze(
  AGENTBRIDGE_TOOL_CATALOG.map((tool) => tool.name),
);
