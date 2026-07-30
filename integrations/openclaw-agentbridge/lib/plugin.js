import { resolvePluginConfig } from "./config.js";
import { InteractionCoordinator, presentationForRecords } from "./coordinator.js";
import { AgentBridgeIdentityRouter } from "./identity-router.js";
import {
  appendPresentationLinks,
  channelFromPrivateSessionKey,
  isPrivateSessionKey,
  mergePresentations,
} from "./interaction.js";
import { createAgentBridgeMcpClient } from "./mcp-client.js";
import {
  AGENTBRIDGE_PROXY_TOOL_NAMES,
  createAgentBridgeProxyTools,
  hostContextMeta,
} from "./proxy-tools.js";

const PLUGIN_VERSION = "0.4.0";

export function registerAgentBridgeInteractions(api, dependencies = {}) {
  const config = resolvePluginConfig(api.pluginConfig);
  const identityRouter =
    dependencies.identityRouter ||
    new AgentBridgeIdentityRouter({
      config,
      hostConfig: api.config,
      env: dependencies.env,
      fetchImpl: dependencies.fetchImpl,
    });
  const mcpClient = identityRouter.enabled
    ? null
    : Object.hasOwn(dependencies, "mcpClient")
      ? dependencies.mcpClient
      : createAgentBridgeMcpClient({
          hostConfig: api.config,
          serverName: config.mcpServerName,
        });
  const coordinator = new InteractionCoordinator({
    api,
    config,
    mcpClient,
    mcpClientResolver: identityRouter.enabled
      ? (sessionKey) => identityRouter.clientForSession(sessionKey)
      : null,
    sharedState: dependencies.sharedState,
    sleep: dependencies.sleep,
    now: dependencies.now,
    fetchImpl: dependencies.documentFetchImpl || globalThis.fetch,
    saveMediaBufferImpl: dependencies.saveMediaBufferImpl,
  });

  if (identityRouter.enabled) {
    api.registerTool(
      (context) =>
        createAgentBridgeProxyTools({
          context,
          identityRouter,
          serverName: config.mcpServerName,
          taskIdResolver: (sessionKey) =>
            coordinator.activeTaskForSession(sessionKey),
          taskRunRefResolver: (toolCallId, sessionKey) =>
            coordinator.taskRunRefForToolCall(toolCallId, sessionKey),
          logger: api.logger,
        }),
      { names: AGENTBRIDGE_PROXY_TOOL_NAMES },
    );
  }

  if (config.allowedCardOrigins.length === 0) {
    api.logger.warn(
      "AgentBridge interaction cards are disabled until allowedCardOrigins is configured",
    );
  }
  if (config.autoPoll && !mcpClient && !identityRouter.enabled) {
    api.logger.warn(
      "AgentBridge background polling is unavailable because MCP endpoint authentication could not be resolved",
    );
  }

  api.registerAgentToolResultMiddleware(
    (event, context) => {
      const documentDelivery = coordinator.deliverPreparedDocumentResult(
        event,
        context,
      );
      const replacement = coordinator.captureToolResult(event, context);
      return documentDelivery
        ? documentDelivery.then(() => replacement)
        : replacement;
    },
    { runtimes: ["openclaw"] },
  );

  // OpenClaw 2026.7.1 omits session context from result middleware.
  api.on("before_tool_call", (event, context) => {
    coordinator.bindToolCall(event, context);
    if (
      identityRouter.enabled &&
      String(event.toolName || "").startsWith(`${config.mcpServerName}__`)
    ) {
      return {
        block: true,
        blockReason: "Use the identity-routed native AgentBridge tool instead of the legacy global MCP server.",
      };
    }
  });

  api.on("message_received", (event, context) => {
    coordinator.recordUserMessage(event, context);
    bindTrustedDeliveryRoute(coordinator, identityRouter, event, context);
  });

  api.on("message_sending", (event, context) => {
    const routeChannel = context.channelId;
    const sessionKey =
      context.sessionKey ||
      coordinator.deliverySessionKeyForRoute({
        channel: routeChannel,
        to: context.conversationId || event.to,
        accountId: context.accountId,
      });
    const channel =
      coordinator.deliveryChannelForSession(sessionKey) ||
      routeChannel ||
      channelFromPrivateSessionKey(sessionKey);
    if (
      channel !== "openclaw-weixin" ||
      coordinator.isDirectDeliveryActive(sessionKey)
    ) {
      return undefined;
    }
    const pending = coordinator.pendingForSession(sessionKey);
    api.logger.info(
      `AgentBridge WeChat message delivery check (private=${isPrivateSessionKey(sessionKey)}, pending=${pending.length})`,
    );
    const interactions = coordinator.takeForDelivery({ sessionKey });
    const presentation = presentationForRecords(interactions, channel);
    if (!presentation) {
      return undefined;
    }
    const payload = appendPresentationLinks(
      { text: event.content },
      presentation,
    );
    return { content: payload.text };
  });

  api.on("reply_payload_sending", (event, context) => {
    if (!["final", "block"].includes(event.kind)) {
      return undefined;
    }
    bindTrustedDeliveryRoute(coordinator, identityRouter, event, context);
    const routeChannel = event.channel || context.channelId;
    const sessionKey =
      event.sessionKey ||
      context.sessionKey ||
      coordinator.deliverySessionKeyForRoute({
        channel: routeChannel,
        to: context.conversationId,
        accountId: context.accountId,
      });
    if (coordinator.isDirectDeliveryActive(sessionKey)) {
      return undefined;
    }
    const channel =
      coordinator.deliveryChannelForSession(sessionKey) ||
      routeChannel ||
      channelFromPrivateSessionKey(sessionKey);
    if (routeChannel === "openclaw-weixin") {
      api.logger.info(
        `AgentBridge WeChat reply delivery check (private=${isPrivateSessionKey(sessionKey)}, channel=${channel || "unknown"}, pending=${coordinator.pendingForSession(sessionKey).length})`,
      );
    }
    const interactions = coordinator.takeForDelivery({ sessionKey });
    const presentation = presentationForRecords(interactions, channel);
    if (!presentation) {
      return undefined;
    }
    const mergedPresentation = mergePresentations(
      event.payload.presentation,
      presentation,
    );
    const payload = {
      ...event.payload,
      presentation: mergedPresentation,
    };
    return {
      payload:
        channel === "openclaw-weixin"
          ? appendPresentationLinks(payload, presentation)
          : payload,
    };
  });
  api.on("session_end", (event, context) => {
    if (["reset", "deleted"].includes(event.reason)) {
      const sessionKey = context.sessionKey || event.sessionKey;
      coordinator.removeSession(sessionKey);
      identityRouter.removeSession(sessionKey);
    }
  });

  api.on("gateway_start", async () => {
    await recoverHostTasks({
      coordinator,
      identityRouter,
      logger: api.logger,
    });
  });

  api.on("gateway_stop", () => {
    coordinator.stopAll();
  });

  api.registerCommand({
    name: "agentbridge",
    description: "查看 AgentBridge 可信交互状态或重新显示待处理卡片",
    acceptsArgs: true,
    requireAuth: true,
    async handler(context) {
      const sessionKey = context.sessionKey;
      if (!isPrivateSessionKey(sessionKey)) {
        return {
          text: "AgentBridge 可信卡片只允许在私聊会话中显示。",
        };
      }
      const action = String(context.args || "status").trim().toLowerCase();
      if (action.startsWith("link ")) {
        return confirmWorkspaceLink({
          identityRouter,
          context,
          sessionKey,
          linkCode: action.slice(5),
        });
      }
      if (action === "pending") {
        const interactions = coordinator.pendingForSession(sessionKey);
        const presentation = presentationForRecords(
          interactions,
          context.channelId || context.channel,
        );
        if (!presentation) {
          return { text: "当前没有未过期的 AgentBridge 可信交互。" };
        }
        return {
          text: "已重新显示当前 AgentBridge 可信交互。",
          presentation,
        };
      }
      if (action !== "status") {
        return {
          text: "用法：/agentbridge status、/agentbridge pending 或 /agentbridge link 配对码",
        };
      }
      const status = coordinator.statusForSession(sessionKey);
      return {
        text: [
          "AgentBridge 交互插件已启用。",
          `可信来源：${status.allowedOriginCount} 个`,
          `待处理交互：${status.pendingCount} 个`,
          `后台轮询：${status.mcpPollingConfigured ? "已配置" : "未配置"}`,
          `自动唤醒模型：${status.wakeAgentOnComplete ? "已启用" : "已关闭"}`,
        ].join("\n"),
      };
    },
  });

  if (typeof api.registerGatewayMethod === "function") {
    api.registerGatewayMethod(
      "agentbridge.workspace.bind",
      (options) =>
        bindWorkspaceGatewaySession({
          ...options,
          identityRouter,
          logger: api.logger,
        }),
      { scope: "operator.write" },
    );
  } else {
    api.logger.warn(
      "AgentBridge workspace binding is unavailable because this OpenClaw host does not expose registerGatewayMethod",
    );
  }

  api.logger.info(
    `AgentBridge interaction plugin registered (version=${PLUGIN_VERSION}, state=${coordinator.sharedStateId}, origins=${config.allowedCardOrigins.length}, identities=${config.identityBindings.length}, autoPoll=${config.autoPoll}, wakeAgent=${config.wakeAgentOnComplete})`,
  );
  return coordinator;
}

async function confirmWorkspaceLink({
  identityRouter,
  context,
  sessionKey,
  linkCode,
}) {
  const code = String(linkCode || "")
    .trim()
    .toUpperCase()
    .replaceAll("-", "");
  if (!/^[A-HJ-NP-Z2-9]{8}$/.test(code)) {
    return { text: "配对码格式无效，请输入网页显示的 8 位配对码。" };
  }
  const resolved = identityRouter.resolvePinnedSession({
    sessionKey,
    channel: context.channelId || context.channel,
    accountId: context.accountId,
  });
  if (!resolved?.bound) {
    return {
      text: "当前聊天身份尚未绑定 AgentBridge，不能确认网页端配对。",
    };
  }
  const binding = resolved.binding;
  try {
    await resolved.client.callTool(
      "agentbridge_host_workspace_link_confirm",
      {
        agent_host: "openclaw",
        endpoint_key: binding.key,
        client_type: binding.channel,
        external_subject: binding.senderId,
        account_id: binding.accountId,
        conversation_ref: sessionKey,
        label: binding.label,
        route: {
          channel: binding.channel,
          to: binding.senderId,
          accountId: binding.accountId,
        },
        link_code: code,
      },
      { meta: hostContextMeta() },
    );
  } catch (error) {
    return {
      text: `网页端配对未完成（${safeErrorCode(error)}）。请确认配对码仍在有效期内。`,
    };
  }
  return {
    text: "网页端身份配对已确认。请返回 Agent Workspace 设置登录账号和密码。",
  };
}

async function bindWorkspaceGatewaySession({
  params,
  respond,
  identityRouter,
  logger,
}) {
  const sessionKey = safeText(params?.sessionKey, 1024);
  const endpointKey = safeText(params?.endpointKey, 768);
  const grant = safeText(params?.grant, 256);
  if (
    !sessionKey ||
    !endpointKey ||
    !grant ||
    !isPrivateSessionKey(sessionKey)
  ) {
    respond(false, undefined, {
      code: "INVALID_REQUEST",
      message: "Invalid AgentBridge workspace binding request.",
    });
    return;
  }
  for (const { binding, client } of identityRouter.configuredIdentities()) {
    try {
      const result = await client.callTool(
        "agentbridge_host_workspace_session_bind",
        {
          agent_host: "openclaw",
          endpoint_key: endpointKey,
          session_key: sessionKey,
          grant,
        },
        { meta: hostContextMeta() },
      );
      if (
        result?.status === "succeeded" &&
        identityRouter.restoreSessionBinding({
          sessionKey,
          bindingKey: binding.key,
        })
      ) {
        respond(true, {
          status: "bound",
          sessionKey,
        });
        return;
      }
    } catch {
      // A one-use grant is intentionally valid for only one configured identity.
    }
  }
  logger.warn("AgentBridge rejected a workspace gateway binding");
  respond(false, undefined, {
    code: "FORBIDDEN",
    message: "AgentBridge workspace identity binding was rejected.",
  });
}

async function recoverHostTasks({ coordinator, identityRouter, logger }) {
  if (!identityRouter.enabled) {
    return;
  }
  let recovered = 0;
  for (const { binding, client } of identityRouter.configuredIdentities()) {
    let response;
    try {
      response = await client.callTool(
        "agentbridge_host_task_recovery_list",
        {
          agent_host: "openclaw",
          endpoint_key: binding.key,
          limit: 50,
        },
        { meta: hostContextMeta() },
      );
    } catch (error) {
      logger.warn(
        `AgentBridge task recovery unavailable for one identity (${safeErrorCode(error)})`,
      );
      continue;
    }
    for (const item of Array.isArray(response?.recoveries)
      ? response.recoveries
      : []) {
      const taskId = safeText(item?.task?.taskId, 128);
      const interaction = item?.interaction;
      const endpoint = item?.endpoint;
      const sessionKey = safeText(endpoint?.conversationRef, 1024);
      const route = endpoint?.route;
      if (
        !taskId ||
        !interaction ||
        !sessionKey ||
        endpoint?.clientType !== binding.channel ||
        endpoint?.externalSubject !== binding.senderId ||
        (binding.accountId !== null &&
          endpoint?.accountId !== binding.accountId) ||
        !route ||
        typeof route !== "object" ||
        Array.isArray(route)
      ) {
        logger.warn("AgentBridge discarded an invalid task recovery record");
        continue;
      }
      if (
        !identityRouter.restoreSessionBinding({
          sessionKey,
          bindingKey: binding.key,
        })
      ) {
        logger.warn(
          "AgentBridge task recovery could not restore its private identity binding",
        );
        continue;
      }
      coordinator.bindDeliveryRoute({
        sessionKey,
        channel: route.channel || binding.channel,
        to: route.to || binding.senderId,
        accountId: route.accountId || binding.accountId,
        threadId: route.threadId,
      });
      if (
        await coordinator.restoreRecoveredInteraction({
          taskId,
          interaction,
          sessionKey,
        })
      ) {
        recovered += 1;
      }
    }
  }
  logger.info(
    `AgentBridge task recovery completed (recovered=${recovered})`,
  );
}

function safeText(value, maximum) {
  if (typeof value !== "string" && typeof value !== "number") {
    return null;
  }
  const normalized = String(value).trim();
  return normalized ? normalized.slice(0, maximum) : null;
}

function safeErrorCode(error) {
  return String(error?.code || error?.name || "TASK_RECOVERY_ERROR")
    .toUpperCase()
    .replace(/[^A-Z0-9_.-]/g, "_")
    .slice(0, 80);
}

function bindTrustedDeliveryRoute(coordinator, identityRouter, event, context) {
  const sessionKey = event.sessionKey || context.sessionKey;
  const channel = event.channel || context.channelId;
  const senderId = event.senderId || context.senderId || event.from;
  const accountId = event.accountId || context.accountId;
  if (senderId) {
    identityRouter.bindSession({
      sessionKey,
      channel,
      senderId,
      accountId,
    });
  }
  coordinator.bindDeliveryRoute({
    sessionKey,
    channel,
    to:
      context.conversationId ||
      event.conversationId ||
      senderId,
    accountId,
    threadId: event.threadId || context.threadId,
  });
}
