import {
  AGENTBRIDGE_TOOL_CATALOG,
} from "./tool-catalog.js";
import { extractToolPayload } from "./mcp-client.js";

export const IDENTITY_STATUS_TOOL_NAME = "agentbridge_identity_status";
export const HOST_CONTEXT_META_KEY = "io.agentbridge/host";
export const TASK_CONTEXT_META_KEY = "io.agentbridge/task";
export const AGENTBRIDGE_GOVERNED_ENTRY_TOOL_NAMES = Object.freeze([
  "oa_efficiency_data_approval_prepare",
  "oa_travel_expense_approval_prepare",
  "oa_labor_contract_renewal_approval_prepare",
  "oa_attendance_confirmation_prepare",
  "oa_weekly_report_acknowledgement_prepare",
  "oa_standard_collaboration_approval_prepare",
  "oa_workflow_revoke_prepare",
  "oa_business_trip_prepare",
  "oa_business_trip_submit_prepare",
  "oa_leave_prepare",
  "oa_leave_submit_prepare",
  "oa_missed_punch_prepare",
  "oa_missed_punch_approval_prepare",
  "oa_meeting_create_prepare",
  "yuque_session_login",
  "taihua_work_log_create_prepare",
  "taihua_session_login",
  "oa_session_login",
]);
const AGENTBRIDGE_GOVERNED_ENTRY_TOOLS = new Set(
  AGENTBRIDGE_GOVERNED_ENTRY_TOOL_NAMES,
);
const AGENTBRIDGE_AGENT_FACING_TOOL_CATALOG = Object.freeze(
  AGENTBRIDGE_TOOL_CATALOG.filter(
    (descriptor) =>
      descriptor.annotations?.readOnlyHint === true ||
      AGENTBRIDGE_GOVERNED_ENTRY_TOOLS.has(descriptor.name),
  ),
);
export const AGENTBRIDGE_AGENT_FACING_TOOL_NAMES = Object.freeze([
  IDENTITY_STATUS_TOOL_NAME,
  ...AGENTBRIDGE_AGENT_FACING_TOOL_CATALOG.map((tool) => tool.name),
]);
export const AGENTBRIDGE_PROXY_TOOL_NAMES = AGENTBRIDGE_AGENT_FACING_TOOL_NAMES;

export function createAgentBridgeProxyTools({
  context,
  identityRouter,
  serverName,
  taskIdResolver = null,
  taskIdBinder = null,
  taskRunRefResolver = null,
  taskContinuationResolver = null,
  interactionGetGuard = null,
  logger = null,
}) {
  const identity = identityRouter.resolveToolContext(context);
  const workspaceSession = isWorkspaceSession(context.sessionKey);
  const resolveIdentity = async (signal) => {
    if (identity.bound) {
      return identity;
    }
    if (!workspaceSession) {
      return identity;
    }
    return identityRouter.resolveWorkspaceSession(
      context.sessionKey,
      { signal },
    );
  };
  const statusTool = createIdentityStatusTool(resolveIdentity);
  if (!identity.bound && !workspaceSession) {
    return [statusTool];
  }
  const allowedToolNames = identity.bound
    ? identityRouter.allowedToolNamesForBinding?.(identity.binding)
    : null;
  const visibleCatalog = allowedToolNames
    ? AGENTBRIDGE_AGENT_FACING_TOOL_CATALOG.filter((descriptor) =>
        allowedToolNames.has(descriptor.name),
      )
    : AGENTBRIDGE_AGENT_FACING_TOOL_CATALOG;
  return [
    statusTool,
    ...visibleCatalog.map((descriptor) =>
      createProxyTool({
        descriptor,
        resolveIdentity,
        identityRouter,
        context,
        serverName,
        taskIdResolver,
        taskIdBinder,
        taskRunRefResolver,
        taskContinuationResolver,
        interactionGetGuard,
        logger,
      }),
    ),
  ];
}

function isWorkspaceSession(sessionKey) {
  return (
    typeof sessionKey === "string" &&
    /^agent:[^:]+:agentbridge-workspace:direct:/i.test(sessionKey.trim())
  );
}

function createIdentityStatusTool(resolveIdentity) {
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
    execute: async (_toolCallId, _rawParams, signal) => {
      const identity = await resolveIdentity(signal);
      return jsonToolResult({
        status: identity.bound ? "bound" : "unbound",
        identityLabel: identity.binding?.label || null,
        reason: identity.reason,
        nextAction: identity.bound
          ? null
          : "Ask the AgentBridge administrator to provision this client identity.",
      });
    },
  };
}

function createProxyTool({
  descriptor,
  resolveIdentity,
  identityRouter,
  context,
  serverName,
  taskIdResolver,
  taskIdBinder,
  taskRunRefResolver,
  taskContinuationResolver,
  interactionGetGuard,
  logger,
}) {
  return {
    name: descriptor.name,
    label: descriptor.title || descriptor.name,
    description: descriptor.description || descriptor.name,
    parameters: descriptor.inputSchema || emptyObjectSchema(),
    ...(descriptor.annotations ? { annotations: descriptor.annotations } : {}),
    execute: async (toolCallId, rawParams, signal) => {
      const identity = await resolveIdentity(signal);
      if (!identity.bound) {
        return jsonToolResult({
          status: "unbound",
          reason: identity.reason,
          error: {
            code: "IDENTITY_NOT_PROVISIONED",
            message:
              "This AgentBridge client identity is not provisioned.",
          },
        });
      }
      const params = normalizeParams(rawParams);
      if (descriptor.name === "agentbridge_interaction_get") {
        const guarded = interactionGetGuard?.({
          sessionKey: context.sessionKey,
          runId: context.runId,
          toolCallId,
          interactionId: params.interaction_id,
        });
        if (guarded) {
          return {
            ...jsonToolResult(guarded),
            details: {
              mcpServer: serverName,
              mcpTool: descriptor.name,
              structuredContent: guarded,
            },
          };
        }
      }
      const continuation = taskContinuationResolver?.(context.sessionKey);
      if (
        continuation &&
        continuation.allowNewOperation !== true &&
        isTaskEligibleTool(descriptor.name)
      ) {
        return jsonToolResult({
          status: "continuation_blocked",
          taskId: continuation.taskId,
          taskStatus: continuation.taskStatus,
          executionMode: continuation.executionMode,
          error: {
            code: "TASK_CONTINUATION_OBSERVE_ONLY",
            message:
              "The selected task is waiting, running, or terminal. Use the supplied task snapshot and existing trusted interaction; do not start another business operation.",
          },
        });
      }
      const taskId = await resolveTaskId({
        descriptor,
        identity,
        identityRouter,
        context,
        toolCallId,
        taskIdResolver,
        taskIdBinder,
        taskRunRefResolver,
        logger,
      });
      const result = await identity.client.callToolResult(
        descriptor.name,
        params,
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
  identityRouter,
  context,
  toolCallId,
  taskIdResolver,
  taskIdBinder,
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
  const resumedTaskId = boundedText(
    taskIdResolver?.(sessionKey, descriptor.name),
    128,
  );
  if (resumedTaskId) {
    return resumedTaskId;
  }
  const runRef =
    boundedText(
      taskRunRefResolver?.(toolCallId, sessionKey, descriptor.name),
      256,
    ) ||
    boundedText(context.runId, 256) ||
    boundedText(toolCallId, 256);
  if (!runRef) {
    return null;
  }
  const binding = identity.binding;
  const workspaceSession = isWorkspaceSession(sessionKey);
  const endpointKey = identityRouter.endpointKeyForSession(sessionKey);
  if (!endpointKey) {
    logger?.warn?.(
      "AgentBridge task creation skipped because the session endpoint is not bound",
    );
    return null;
  }
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
        endpoint_key: endpointKey,
        client_type: workspaceSession ? "web" : binding.channel,
        external_subject: workspaceSession
          ? workspaceSubject(endpointKey)
          : binding.senderId,
        conversation_ref: sessionKey,
        title: boundedText(descriptor.title || descriptor.name, 240),
        account_id: workspaceSession
          ? workspaceSubject(endpointKey)
          : binding.accountId,
        label: workspaceSession ? "Agent Workspace" : binding.label,
        route: workspaceSession
          ? {}
          : {
              channel: binding.channel,
              to: boundedText(delivery.to, 768) || binding.senderId,
              accountId:
                boundedText(delivery.accountId, 512) || binding.accountId,
              threadId: boundedText(delivery.threadId, 512),
            },
        capabilities: workspaceSession
          ? [
              "workspace.chat",
              "workspace.task.read",
              "workspace.interaction.open",
            ]
          : ["direct_status", "trusted_interaction"],
      },
      { meta: hostContextMeta() },
    );
    const taskId = boundedText(response?.task?.taskId, 128);
    if (!taskId) {
      logger?.warn?.(
        "AgentBridge task creation returned no task ID; business call continues",
      );
    } else {
      taskIdBinder?.(sessionKey, descriptor.name, taskId);
    }
    return taskId;
  } catch (error) {
    logger?.warn?.(
      `AgentBridge task creation unavailable; business call continues (${safeErrorCode(error)})`,
    );
    return null;
  }
}

function workspaceSubject(endpointKey) {
  return boundedText(String(endpointKey).slice("workspace:".length), 768);
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
