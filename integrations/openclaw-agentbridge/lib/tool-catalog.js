import { readFileSync } from "node:fs";

const catalog = JSON.parse(
  readFileSync(new URL("./agentbridge-tools.json", import.meta.url), "utf8"),
);

if (
  catalog?.schemaVersion !== "agentbridge.openclaw-tool-catalog.v1" ||
  !Array.isArray(catalog.tools)
) {
  throw new Error("AgentBridge OpenClaw tool catalog is invalid");
}

export const AGENTBRIDGE_TOOL_CATALOG = Object.freeze(
  catalog.tools.map((tool) => Object.freeze(tool)),
);

export const AGENTBRIDGE_TOOL_NAMES = Object.freeze(
  AGENTBRIDGE_TOOL_CATALOG.map((tool) => tool.name),
);
