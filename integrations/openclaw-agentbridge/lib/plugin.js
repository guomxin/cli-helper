import { resolvePluginConfig } from "./config.js";
import { InteractionCoordinator, presentationForRecords } from "./coordinator.js";
import { isPrivateSessionKey, mergePresentations } from "./interaction.js";
import { createAgentBridgeMcpClient } from "./mcp-client.js";

const PLUGIN_VERSION = "0.1.6";

export function registerAgentBridgeInteractions(api, dependencies = {}) {
  const config = resolvePluginConfig(api.pluginConfig);
  const mcpClient = Object.hasOwn(dependencies, "mcpClient")
    ? dependencies.mcpClient
    : createAgentBridgeMcpClient({
        hostConfig: api.config,
        serverName: config.mcpServerName,
      });
  const coordinator = new InteractionCoordinator({
    api,
    config,
    mcpClient,
    sleep: dependencies.sleep,
    now: dependencies.now,
  });

  if (config.allowedCardOrigins.length === 0) {
    api.logger.warn(
      "AgentBridge interaction cards are disabled until allowedCardOrigins is configured",
    );
  }
  if (config.autoPoll && !mcpClient) {
    api.logger.warn(
      "AgentBridge background polling is unavailable because MCP endpoint authentication could not be resolved",
    );
  }

  api.registerAgentToolResultMiddleware(
    (event, context) => coordinator.captureToolResult(event, context),
    { runtimes: ["openclaw"] },
  );

  // OpenClaw 2026.7.1 omits session context from result middleware.
  api.on("before_tool_call", (event, context) => {
    coordinator.bindToolCall(event, context);
  });

  api.on("message_received", (event, context) => {
    bindTrustedDeliveryRoute(coordinator, event, context);
  });

  api.on("reply_payload_sending", (event, context) => {
    if (!["final", "block"].includes(event.kind)) {
      return undefined;
    }
    bindTrustedDeliveryRoute(coordinator, event, context);
    const interactions = coordinator.takeForDelivery({
      runId: event.runId || context.runId,
      sessionKey: event.sessionKey || context.sessionKey,
    });
    const presentation = presentationForRecords(
      interactions,
      event.channel || context.channelId,
    );
    if (!presentation) {
      return undefined;
    }
    return {
      payload: {
        ...event.payload,
        presentation: mergePresentations(
          event.payload.presentation,
          presentation,
        ),
      },
    };
  });

  api.on("session_end", (event, context) => {
    if (["reset", "deleted"].includes(event.reason)) {
      coordinator.removeSession(context.sessionKey || event.sessionKey);
    }
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
          text: "用法：/agentbridge status 或 /agentbridge pending",
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

  api.logger.info(
    `AgentBridge interaction plugin registered (version=${PLUGIN_VERSION}, origins=${config.allowedCardOrigins.length}, autoPoll=${config.autoPoll}, wakeAgent=${config.wakeAgentOnComplete})`,
  );
  return coordinator;
}

function bindTrustedDeliveryRoute(coordinator, event, context) {
  coordinator.bindDeliveryRoute({
    sessionKey: event.sessionKey || context.sessionKey,
    channel: event.channel || context.channelId,
    to:
      context.conversationId ||
      event.conversationId ||
      event.senderId ||
      context.senderId ||
      event.from,
    accountId: event.accountId || context.accountId,
    threadId: event.threadId || context.threadId,
  });
}
