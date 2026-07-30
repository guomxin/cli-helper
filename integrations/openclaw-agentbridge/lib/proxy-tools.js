import {
  AGENTBRIDGE_TOOL_CATALOG,
  AGENTBRIDGE_TOOL_NAMES,
} from "./tool-catalog.js";
import { extractToolPayload } from "./mcp-client.js";

export const IDENTITY_STATUS_TOOL_NAME = "agentbridge_identity_status";
export const HOST_CONTEXT_META_KEY = "io.agentbridge/host";
export const TASK_CONTEXT_META_KEY = "io.agentbridge/task";
export const AGENTBRIDGE_PROXY_TOOL_NAMES = Object.freeze([
  IDENTITY_STATUS_TOOL_NAME,
  ...AGENTBRIDGE_TOOL_NAMES,
]);

export function createAgentBridgeProxyTools({
  context,
  identityRouter,
  serverName,
  taskIdResolver = null,
  taskRunRefResolver = null,
  logger = null,
}) {
  const identity = identityRouter.resolveToolContext(context);
  const statusTool = createIdentityStatusTool(identity);
  if (!identity.bound) {
    return [statusTool];
  }
  return [
    statusTool,
    ...AGENTBRIDGE_TOOL_CATALOG.map((descriptor) =>
      createProxyTool({
        descriptor,
        identity,
        context,
        serverName,
        taskIdResolver,
        taskRunRefResolver,
        logger,
      }),
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

function createProxyTool({
  descriptor,
  identity,
  context,
  serverName,
  taskIdResolver,
  taskRunRefResolver,
  logger,
}) {
  return {
    name: descriptor.name,
    label: descriptor.title || descriptor.name,
    description: descriptor.description || descriptor.name,
    parameters: descriptor.inputSchema || emptyObjectSchema(),
    ...(descriptor.annotations ? { annotations: descriptor.annotations } : {}),
    execute: async (toolCallId, rawParams, signal) => {
      const taskId = await resolveTaskId({
        descriptor,
        identity,
        context,
        toolCallId,
        taskIdResolver,
        taskRunRefResolver,
        logger,
      });
      const result = await identity.client.callToolResult(
        descriptor.name,
        normalizeParams(rawParams),
        {
          signal,
          meta: taskId
            ? {
                [TASK_CONTEXT_META_KEY]: {
                  taskId,
                },
              }
            : undefined,
        },
      );
      if (taskId) {
        await observeTaskResult({
          client: identity.client,
          taskId,
          result,
          logger,
          signal,
        });
      }
      return {
        ...result,
        details: {
          mcpServer: serverName,
          mcpTool: descriptor.name,
          structuredContent: result?.structuredContent || null,
          ...(taskId ? { agentbridgeTaskId: taskId } : {}),
        },
      };
    },
  };
}

async function resolveTaskId({
  descriptor,
  identity,
  context,
  toolCallId,
  taskIdResolver,
  taskRunRefResolver,
  logger,
}) {
  if (!isTaskEligibleTool(descriptor.name)) {
    return null;
  }
  const sessionKey = boundedText(context.sessionKey, 1024);
  if (!sessionKey) {
    return null;
  }
  const resumedTaskId = boundedText(taskIdResolver?.(sessionKey), 128);
  if (resumedTaskId) {
    return resumedTaskId;
  }
  const runRef =
    boundedText(taskRunRefResolver?.(toolCallId, sessionKey), 256) ||
    boundedText(context.runId, 256) ||
    boundedText(toolCallId, 256);
  if (!runRef) {
    return null;
  }
  const binding = identity.binding;
  const delivery =
    context.deliveryContext &&
    typeof context.deliveryContext === "object" &&
    !Array.isArray(context.deliveryContext)
      ? context.deliveryContext
      : {};
  try {
    const response = await identity.client.callTool(
      "agentbridge_host_task_ensure",
      {
        agent_host: "openclaw",
        host_task_key: boundedText(`${sessionKey}|${runRef}`, 1024),
        endpoint_key: binding.key,
        client_type: binding.channel,
        external_subject: binding.senderId,
        conversation_ref: sessionKey,
        title: boundedText(descriptor.title || descriptor.name, 240),
        account_id: binding.accountId,
        label: binding.label,
        route: {
          channel: binding.channel,
          to: boundedText(delivery.to, 768) || binding.senderId,
          accountId:
            boundedText(delivery.accountId, 512) || binding.accountId,
          threadId: boundedText(delivery.threadId, 512),
        },
        capabilities: ["direct_status", "trusted_interaction"],
      },
      { meta: hostContextMeta() },
    );
    const taskId = boundedText(response?.task?.taskId, 128);
    if (!taskId) {
      logger?.warn?.(
        "AgentBridge task creation returned no task ID; business call continues",
      );
    }
    return taskId;
  } catch (error) {
    logger?.warn?.(
      `AgentBridge task creation unavailable; business call continues (${safeErrorCode(error)})`,
    );
    return null;
  }
}

async function observeTaskResult({ client, taskId, result, logger, signal }) {
  const references = collectTaskReferences(extractToolPayload(result));
  if (
    references.operationIds.length === 0 &&
    references.interactionIds.length === 0
  ) {
    return;
  }
  try {
    await client.callTool(
      "agentbridge_host_task_observe",
      {
        agent_host: "openclaw",
        task_id: taskId,
        operation_ids: references.operationIds,
        interaction_ids: references.interactionIds,
      },
      { signal, meta: hostContextMeta() },
    );
  } catch (error) {
    logger?.warn?.(
      `AgentBridge task observation unavailable; business result preserved (${safeErrorCode(error)})`,
    );
  }
}

export function collectTaskReferences(value) {
  const operationIds = new Set();
  const interactionIds = new Set();
  const seen = new Set();
  const visit = (item, depth) => {
    if (
      depth > 12 ||
      !item ||
      typeof item !== "object" ||
      seen.has(item)
    ) {
      return;
    }
    seen.add(item);
    if (Array.isArray(item)) {
      for (const child of item.slice(0, 100)) {
        visit(child, depth + 1);
      }
      return;
    }
    for (const [key, child] of Object.entries(item)) {
      if (
        key === "operationId" &&
        typeof child === "string" &&
        child.trim()
      ) {
        operationIds.add(child.trim().slice(0, 256));
      } else if (
        key === "interactionId" &&
        typeof child === "string" &&
        child.trim()
      ) {
        interactionIds.add(child.trim().slice(0, 256));
      } else {
        visit(child, depth + 1);
      }
      if (operationIds.size + interactionIds.size >= 40) {
        return;
      }
    }
  };
  visit(value, 0);
  return {
    operationIds: [...operationIds].slice(0, 20),
    interactionIds: [...interactionIds].slice(0, 20),
  };
}

export function hostContextMeta() {
  return {
    [HOST_CONTEXT_META_KEY]: {
      version: "1",
      agentHost: "openclaw",
    },
  };
}

function isTaskEligibleTool(name) {
  return (
    typeof name === "string" &&
    !name.startsWith("agentbridge_") &&
    !name.endsWith("_session_status")
  );
}

function normalizeParams(value) {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value
    : {};
}

function emptyObjectSchema() {
  return { type: "object", properties: {}, additionalProperties: false };
}

function boundedText(value, maximum) {
  if (typeof value !== "string" && typeof value !== "number") {
    return null;
  }
  const normalized = String(value).trim();
  return normalized ? normalized.slice(0, maximum) : null;
}

function safeErrorCode(error) {
  const value = error?.code || error?.name || "TASK_HUB_ERROR";
  return String(value)
    .toUpperCase()
    .replace(/[^A-Z0-9_.-]/g, "_")
    .slice(0, 80);
}

function jsonToolResult(value) {
  return {
    content: [{ type: "text", text: JSON.stringify(value) }],
    details: { structuredContent: value },
  };
}
