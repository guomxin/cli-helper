import {
  AGENTBRIDGE_TOOL_CATALOG,
  AGENTBRIDGE_TOOL_NAMES,
} from "./tool-catalog.js";

export const IDENTITY_STATUS_TOOL_NAME = "agentbridge_identity_status";
export const AGENTBRIDGE_PROXY_TOOL_NAMES = Object.freeze([
  IDENTITY_STATUS_TOOL_NAME,
  ...AGENTBRIDGE_TOOL_NAMES,
]);

export function createAgentBridgeProxyTools({
  context,
  identityRouter,
  serverName,
}) {
  const identity = identityRouter.resolveToolContext(context);
  const statusTool = createIdentityStatusTool(identity);
  if (!identity.bound) {
    return [statusTool];
  }
  return [
    statusTool,
    ...AGENTBRIDGE_TOOL_CATALOG.map((descriptor) =>
      createProxyTool(descriptor, identity.client, serverName),
    ),
  ];
}

function createIdentityStatusTool(identity) {
  return {
    name: IDENTITY_STATUS_TOOL_NAME,
    label: "AgentBridge Identity Status",
    description:
      "Check whether this private conversation has a provisioned AgentBridge identity. " +
      "Use this when OA tools are unavailable; never ask the user for an MCP token.",
    parameters: {
      type: "object",
      properties: {},
      additionalProperties: false,
    },
    execute: async () =>
      jsonToolResult({
        status: identity.bound ? "bound" : "unbound",
        identityLabel: identity.binding?.label || null,
        reason: identity.reason,
        nextAction: identity.bound
          ? null
          : "Ask the AgentBridge administrator to provision this Telegram identity.",
      }),
  };
}

function createProxyTool(descriptor, client, serverName) {
  return {
    name: descriptor.name,
    label: descriptor.title || descriptor.name,
    description: descriptor.description || descriptor.name,
    parameters: descriptor.inputSchema || emptyObjectSchema(),
    ...(descriptor.annotations ? { annotations: descriptor.annotations } : {}),
    execute: async (_toolCallId, rawParams, signal) => {
      const result = await client.callToolResult(
        descriptor.name,
        normalizeParams(rawParams),
        { signal },
      );
      return {
        ...result,
        details: {
          mcpServer: serverName,
          mcpTool: descriptor.name,
          structuredContent: result?.structuredContent || null,
        },
      };
    },
  };
}

function normalizeParams(value) {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value
    : {};
}

function emptyObjectSchema() {
  return { type: "object", properties: {}, additionalProperties: false };
}

function jsonToolResult(value) {
  return {
    content: [{ type: "text", text: JSON.stringify(value) }],
    details: { structuredContent: value },
  };
}
