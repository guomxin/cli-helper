import {
  appendPresentationLinks,
  buildPresentation,
  collectPublicInteractionReferences,
  isInteractionExpired,
  isPrivateSessionKey,
  processToolResult,
} from "./interaction.js";

const TERMINAL_STATES = new Set([
  "declined",
  "expired",
  "failed",
  "superseded",
]);
const MAX_INTERACTIONS = 100;
const MAX_POLL_ERRORS = 5;
const MAX_TOOL_BINDINGS = 1000;
const TOOL_BINDING_TTL_MS = 5 * 60 * 1000;
const LOGIN_CONTINUATION_TTL_MS = 5 * 60 * 1000;
const MAX_HYDRATION_REFERENCES = 3;
const LOGIN_READ_TOOLS = new Map([
  [
    "oa_workflow_pending_list",
    { kind: "oa_workflow", collection: "pending", system: "OA", label: "待办" },
  ],
  [
    "oa_workflow_sent_list",
    { kind: "oa_workflow", collection: "sent", system: "OA", label: "已发" },
  ],
  [
    "oa_workflow_done_list",
    { kind: "oa_workflow", collection: "done", system: "OA", label: "已办" },
  ],
  [
    "oa_workflow_tracked_list",
    { kind: "oa_workflow", collection: "tracked", system: "OA", label: "跟踪事项" },
  ],
  [
    "taihua_work_log_my_list",
    { kind: "taihua_work_log", system: "泰华日志系统", label: "我的工作日志" },
  ],
  [
    "taihua_work_log_team_list",
    { kind: "taihua_work_log", system: "泰华日志系统", label: "团队工作日志" },
  ],
  [
    "taihua_project_search",
    { kind: "taihua_project", system: "泰华日志系统", label: "项目" },
  ],
]);

export function createInteractionSharedState() {
  return {
    id:
      globalThis.crypto?.randomUUID?.() ||
      `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    records: new Map(),
    sessionRoutes: new Map(),
    directDeliveries: new Map(),
    loginContinuations: new Map(),
    recentUserMessages: new Map(),
  };
}

export class InteractionCoordinator {
  constructor({
    api,
    config,
    mcpClient = null,
    mcpClientResolver = null,
    sharedState = createInteractionSharedState(),
    sleep = defaultSleep,
    now = Date.now,
  }) {
    this.api = api;
    this.config = config;
    this.mcpClient = mcpClient;
    this.mcpClientResolver = mcpClientResolver;
    this.sleep = sleep;
    this.now = now;
    this.sharedStateId = sharedState.id || "isolated";
    this.records = sharedState.records;
    this.polls = new Map();
    this.abortControllers = new Map();
    this.toolBindings = new Map();
    this.sessionRoutes = sharedState.sessionRoutes;
    this.directDeliveries = sharedState.directDeliveries;
    this.loginContinuations =
      sharedState.loginContinuations || (sharedState.loginContinuations = new Map());
    this.recentUserMessages =
      sharedState.recentUserMessages || (sharedState.recentUserMessages = new Map());
  }

  recordUserMessage(event, context) {
    const sessionKey = event.sessionKey || context.sessionKey;
    const rawText = event.content ?? event.text;
    if (!isPrivateSessionKey(sessionKey) || typeof rawText !== "string") {
      return;
    }
    const text = rawText.trim().slice(0, 1000);
    if (!text) {
      return;
    }
    this.recentUserMessages.set(sessionKey, {
      text,
      capturedAt: this.now(),
    });
  }

  bindDeliveryRoute({ sessionKey, channel, to, accountId, threadId }) {
    if (!isPrivateSessionKey(sessionKey)) {
      return false;
    }
    const normalizedChannel = safeRoutePart(channel);
    const normalizedTo = safeRoutePart(to);
    if (!normalizedChannel || !normalizedTo) {
      return false;
    }
    this.sessionRoutes.set(sessionKey, {
      channel: normalizedChannel,
      to: normalizedTo,
      accountId: safeRoutePart(accountId) || null,
      threadId: normalizeThreadId(threadId),
    });
    return true;
  }

  deliveryChannelForSession(sessionKey) {
    return this.sessionRoutes.get(sessionKey)?.channel || null;
  }

  deliverySessionKeyForRoute({ channel, to, accountId }) {
    const normalizedChannel = safeRoutePart(channel);
    const normalizedTo = safeRoutePart(to);
    const normalizedAccountId = safeRoutePart(accountId);
    if (!normalizedChannel || !normalizedTo) {
      return null;
    }
    const matches = [...this.sessionRoutes.entries()].filter(
      ([, route]) =>
        route.channel === normalizedChannel &&
        route.to === normalizedTo &&
        (!normalizedAccountId ||
          !route.accountId ||
          route.accountId === normalizedAccountId),
    );
    return matches.length === 1 ? matches[0][0] : null;
  }

  bindToolCall(event, context) {
    const toolCallId = normalizeToolCallId(event.toolCallId);
    if (!toolCallId) {
      return;
    }
    this.toolBindings.set(toolCallId, {
      sessionKey: context.sessionKey || null,
      runId: event.runId || context.runId || null,
      capturedAt: this.now(),
      readContinuation: normalizeReadContinuation(
        event.toolName,
        event.params,
        this.now(),
      ),
    });
    this.pruneToolBindings();
  }

  captureToolResult(event, context) {
    const binding = this.takeToolBinding(event.toolCallId);
    const processed = processToolResult(
      event.result,
      this.config.allowedCardOrigins,
    );
    if (processed.sanitized) {
      this.captureInteractions(processed.interactions, binding, context);
      return { result: processed.result };
    }

    const publicPayload = trustedAgentBridgeStructuredContent(
      event.result,
      this.config.mcpServerName,
    );
    if (!publicPayload) {
      return undefined;
    }
    this.rememberLoginContinuation(publicPayload, binding, context);
    const references = collectPublicInteractionReferences(publicPayload).slice(
      0,
      MAX_HYDRATION_REFERENCES,
    );
    if (references.length === 0) {
      return undefined;
    }
    return this.hydratePublicInteractionReferences(
      references,
      binding,
      context,
    );
  }

  rememberLoginContinuation(payload, binding, context) {
    if (!isLoginRequiredPayload(payload) || !binding?.readContinuation) {
      return;
    }
    const sessionKey = binding.sessionKey || context.sessionKey;
    if (!isPrivateSessionKey(sessionKey)) {
      return;
    }
    this.loginContinuations.set(sessionKey, binding.readContinuation);
  }

  consumeLoginContinuation(sessionKey) {
    const remembered = this.loginContinuations.get(sessionKey);
    if (isFreshContinuation(remembered, this.now())) {
      this.loginContinuations.delete(sessionKey);
      return remembered;
    }
    this.loginContinuations.delete(sessionKey);
    const recent = this.recentUserMessages.get(sessionKey);
    if (!recent || this.now() - recent.capturedAt > LOGIN_CONTINUATION_TTL_MS) {
      return null;
    }
    return inferReadContinuation(recent.text, this.now());
  }

  captureInteractions(interactions, binding, context) {
    const sessionKey = binding?.sessionKey || context.sessionKey;
    const runId = binding?.runId || context.runId;
    const privateSession = isPrivateSessionKey(sessionKey);
    for (const interaction of interactions) {
      if (!privateSession) {
        this.api.logger.warn(
          sessionKey
            ? "AgentBridge interaction withheld because the OpenClaw session is not private"
            : "AgentBridge interaction withheld because no private session binding was available",
        );
        continue;
      }
      const readContinuation =
        interaction.type === "credential"
          ? this.consumeLoginContinuation(sessionKey)
          : null;
      const record = this.upsert({
        interaction,
        sessionKey,
        runId,
        readContinuation,
      });
      this.api.logger.info(
        `AgentBridge interaction captured for private session (type=${interaction.type}, state=${interaction.state})`,
      );
      this.startPolling(record);
    }
  }

  async hydratePublicInteractionReferences(references, binding, context) {
    const sessionKey = binding?.sessionKey || context.sessionKey;
    if (!isPrivateSessionKey(sessionKey)) {
      this.api.logger.warn(
        sessionKey
          ? "AgentBridge interaction reference withheld because the OpenClaw session is not private"
          : "AgentBridge interaction reference withheld because no private session binding was available",
      );
      return undefined;
    }
    const mcpClient = this.clientForSession(sessionKey);
    if (!mcpClient) {
      this.api.logger.warn(
        "AgentBridge interaction metadata recovery is unavailable because MCP endpoint authentication could not be resolved",
      );
      return undefined;
    }

    const interactions = [];
    for (const reference of references) {
      let response;
      try {
        response = await mcpClient.callTool(
          "agentbridge_interaction_get",
          { interaction_id: reference.interactionId },
        );
      } catch (error) {
        this.api.logger.warn(
          `AgentBridge interaction metadata recovery failed: ${safeErrorCode(error)}`,
        );
        continue;
      }
      const processed = processToolResult(
        response,
        this.config.allowedCardOrigins,
      );
      const interaction = processed.interactions.find(
        (item) =>
          item.interactionId === reference.interactionId &&
          item.type === reference.type &&
          ["pending", "processing"].includes(item.state) &&
          !isInteractionExpired(item, this.now()),
      );
      if (interaction) {
        interactions.push(interaction);
      }
    }
    if (interactions.length === 0) {
      this.api.logger.warn(
        "AgentBridge interaction metadata recovery returned no active trusted interaction",
      );
      return undefined;
    }
    this.captureInteractions(interactions, binding, context);
    return undefined;
  }
  takeForDelivery({ sessionKey }) {
    this.prune();
    if (!isPrivateSessionKey(sessionKey)) {
      return [];
    }
    const matches = [...this.records.values()].filter(
      (record) => record.sessionKey === sessionKey && !record.delivered,
    );
    for (const record of matches) {
      record.delivered = true;
    }
    return matches.map((record) => record.interaction);
  }

  pendingForSession(sessionKey) {
    this.prune();
    if (!isPrivateSessionKey(sessionKey)) {
      return [];
    }
    return [...this.records.values()]
      .filter(
        (record) =>
          record.sessionKey === sessionKey &&
          ["pending", "processing"].includes(record.interaction.state),
      )
      .sort((left, right) => right.capturedAt - left.capturedAt)
      .slice(0, 3)
      .map((record) => record.interaction);
  }

  statusForSession(sessionKey) {
    this.prune();
    const privateSession = isPrivateSessionKey(sessionKey);
    const records = privateSession
      ? [...this.records.values()].filter((record) => record.sessionKey === sessionKey)
      : [];
    return {
      privateSession,
      allowedOriginCount: this.config.allowedCardOrigins.length,
      mcpPollingConfigured: Boolean(
        this.clientForSession(sessionKey) && this.config.autoPoll,
      ),
      pendingCount: records.filter((record) =>
        ["pending", "processing"].includes(record.interaction.state),
      ).length,
      activePollCount: records.filter((record) => this.polls.has(record.interaction.interactionId)).length,
      wakeAgentOnComplete: this.config.wakeAgentOnComplete,
    };
  }

  clientForSession(sessionKey) {
    return this.mcpClientResolver?.(sessionKey) || this.mcpClient;
  }

  isDirectDeliveryActive(sessionKey) {
    return (this.directDeliveries.get(sessionKey) || 0) > 0;
  }

  removeSession(sessionKey) {
    for (const [interactionId, record] of this.records) {
      if (record.sessionKey === sessionKey) {
        this.abortControllers.get(interactionId)?.abort();
        this.records.delete(interactionId);
      }
    }
    for (const [toolCallId, binding] of this.toolBindings) {
      if (binding.sessionKey === sessionKey) {
        this.toolBindings.delete(toolCallId);
      }
    }
    this.loginContinuations.delete(sessionKey);
    this.recentUserMessages.delete(sessionKey);
    this.sessionRoutes.delete(sessionKey);
    this.directDeliveries.delete(sessionKey);
  }

  stopAll() {
    for (const controller of this.abortControllers.values()) {
      controller.abort();
    }
    this.abortControllers.clear();
    this.polls.clear();
    this.toolBindings.clear();
    this.loginContinuations.clear();
    this.recentUserMessages.clear();
    this.sessionRoutes.clear();
    this.directDeliveries.clear();
  }

  async waitForIdle() {
    await Promise.allSettled([...this.polls.values()]);
  }

  upsert({ interaction, sessionKey, runId, readContinuation = null }) {
    const existing = this.records.get(interaction.interactionId);
    if (existing) {
      existing.interaction = interaction;
      existing.sessionKey = sessionKey || existing.sessionKey;
      existing.runId = runId || existing.runId;
      existing.readContinuation ||= readContinuation;
      existing.mcpClient ||= this.clientForSession(existing.sessionKey);
      return existing;
    }
    const record = {
      interaction,
      sessionKey,
      runId,
      readContinuation,
      mcpClient: this.clientForSession(sessionKey),
      delivered: false,
      continuationQueued: false,
      capturedAt: this.now(),
    };
    this.records.set(interaction.interactionId, record);
    this.prune();
    return record;
  }

  startPolling(record) {
    if (
      !this.config.autoPoll ||
      !record.mcpClient ||
      !["pending", "processing"].includes(record.interaction.state) ||
      this.polls.has(record.interaction.interactionId)
    ) {
      return;
    }
    const controller = new AbortController();
    this.abortControllers.set(record.interaction.interactionId, controller);
    const promise = this.poll(record, controller.signal)
      .catch((error) => {
        if (!controller.signal.aborted) {
          this.api.logger.warn(
            `AgentBridge interaction polling stopped: ${safeErrorCode(error)}`,
          );
        }
      })
      .finally(() => {
        this.polls.delete(record.interaction.interactionId);
        this.abortControllers.delete(record.interaction.interactionId);
      });
    this.polls.set(record.interaction.interactionId, promise);
  }

  async poll(record, signal) {
    const deadline = Math.min(
      this.now() + this.config.maxPollSeconds * 1000,
      interactionDeadline(record.interaction) ?? Number.POSITIVE_INFINITY,
    );
    let consecutiveErrors = 0;

    while (!signal.aborted && this.now() < deadline) {
      await this.sleep(this.config.pollIntervalSeconds * 1000, signal);
      if (signal.aborted) {
        return;
      }
      let response;
      try {
        response = await record.mcpClient.callTool(
          "agentbridge_interaction_get",
          { interaction_id: record.interaction.interactionId },
          { signal },
        );
        consecutiveErrors = 0;
      } catch (error) {
        consecutiveErrors += 1;
        if (consecutiveErrors >= MAX_POLL_ERRORS) {
          await this.notify(record, "poll_failed", safeErrorCode(error));
          return;
        }
        continue;
      }

      const processed = processToolResult(
        response,
        this.config.allowedCardOrigins,
      );
      const current = processed.interactions.find(
        (item) => item.interactionId === record.interaction.interactionId,
      );
      if (!current) {
        continue;
      }
      record.interaction = current;
      if (TERMINAL_STATES.has(current.state)) {
        await this.notify(record, current.state, null);
        return;
      }
      if (current.state !== "completed") {
        continue;
      }
      if (current.resume.ready !== true || current.resume.completed === true) {
        await this.notify(record, "completed", null);
        return;
      }
      await this.resume(record, signal);
      return;
    }
    if (!signal.aborted) {
      await this.notify(record, "poll_expired", null);
    }
  }

  async resume(record, signal) {
    let response;
    try {
      response = await record.mcpClient.callTool(
        "agentbridge_interaction_resume",
        {
          interaction_id: record.interaction.interactionId,
          idempotency_key: `openclaw:${record.interaction.interactionId}`,
        },
        { signal },
      );
    } catch (error) {
      await this.notify(record, "resume_failed", safeErrorCode(error));
      return;
    }

    const processed = processToolResult(
      response,
      this.config.allowedCardOrigins,
    );
    const nextInteractions = processed.interactions.filter(
      (item) => item.interactionId !== record.interaction.interactionId,
    );
    for (const interaction of nextInteractions) {
      const next = this.upsert({
        interaction,
        sessionKey: record.sessionKey,
        runId: null,
      });
      this.startPolling(next);
    }
    await this.notify(
      record,
      nextInteractions.length > 0 ? "next_interaction_required" : safeStatus(response),
      safeResponseErrorCode(response),
      nextInteractions,
      {
        resumeOriginalRequest: shouldResumeOriginalRequest(
          record,
          response,
          nextInteractions,
        ),
        response,
      },
    );
  }

  async notify(
    record,
    status,
    errorCode,
    nextInteractions = [],
    { resumeOriginalRequest = false, response = null } = {},
  ) {
    if (!record.sessionKey) {
      return;
    }
    if (
      nextInteractions.length > 0 &&
      (await this.deliverInteractionsDirect(record.sessionKey, nextInteractions))
    ) {
      return;
    }
    if (resumeOriginalRequest && this.config.wakeAgentOnComplete) {
      if (record.continuationQueued) {
        return;
      }
      record.continuationQueued = true;
      const pendingBeforeStatus = this.undeliveredPendingFor(record);
      if (
        pendingBeforeStatus.length > 0 &&
        (await this.deliverInteractionsDirect(
          record.sessionKey,
          pendingBeforeStatus,
        ))
      ) {
        return;
      }
      if (
        record.readContinuation &&
        (await this.replayReadContinuation(record))
      ) {
        return;
      }
      await this.deliverStatusDirect(record.sessionKey, status, errorCode, response);
      const pendingAfterStatus = this.undeliveredPendingFor(record);
      if (
        pendingAfterStatus.length > 0 &&
        (await this.deliverInteractionsDirect(
          record.sessionKey,
          pendingAfterStatus,
        ))
      ) {
        return;
      }
      this.api.runtime.system.enqueueSystemEvent(
        [
          "AgentBridge 登录已完成。",
          "继续处理触发本次登录的原始用户请求，并重新调用所需工具取得最新结果。",
          "除非实时会话检查再次明确要求登录，否则不要重复调用登录工具。",
          "不要索取或复述密码、业务字段、授权内容或可信卡片 URL。",
        ].join(""),
        {
          sessionKey: record.sessionKey,
          contextKey: `agentbridge:continue:${record.interaction.interactionId}`,
        },
      );
      await this.wakeAgent(
        record.sessionKey,
        "hook:agentbridge-login-completed",
      );
      const pendingAfterContinuation = this.undeliveredPendingFor(record, {
        allowLaterRun: true,
      });
      if (
        pendingAfterContinuation.length > 0 &&
        (await this.deliverInteractionsDirect(
          record.sessionKey,
          pendingAfterContinuation,
        ))
      ) {
        return;
      }
      this.api.logger.info(
        "AgentBridge original request continuation queued after login",
      );
      return;
    }
    if (
      nextInteractions.length === 0 &&
      (await this.deliverStatusDirect(record.sessionKey, status, errorCode, response))
    ) {
      return;
    }
    const suffix = errorCode ? `，错误码 ${errorCode}` : "";
    this.api.runtime.system.enqueueSystemEvent(
      `AgentBridge 可信交互宿主事件：${status}${suffix}。不要向用户索取密码、业务字段或授权内容。`,
      {
        sessionKey: record.sessionKey,
        contextKey: `agentbridge:${record.interaction.interactionId}`,
      },
    );
    if (this.config.wakeAgentOnComplete) {
      await this.wakeAgent(record.sessionKey);
    }
  }

  async replayReadContinuation(record) {
    const continuation = record.readContinuation;
    if (!continuation || !record.mcpClient) {
      return false;
    }
    let response;
    try {
      response = await record.mcpClient.callTool(
        continuation.toolName,
        continuation.arguments,
      );
    } catch (error) {
      const system =
        LOGIN_READ_TOOLS.get(continuation.toolName)?.system || "下游系统";
      const text =
        `${system} 登录已完成，但 AgentBridge 自动继续原读取请求失败` +
        `（错误码：${safeErrorCode(error)}）。请重新发送一次读取请求。`;
      return this.deliverReadContinuation(record, text, null);
    }

    if (safeStatus(response) !== "succeeded") {
      const code = safeResponseErrorCode(response) || safeStatus(response);
      const system =
        LOGIN_READ_TOOLS.get(continuation.toolName)?.system || "下游系统";
      const text =
        `${system} 登录已完成，但原读取请求仍然失败` +
        `（错误码：${safeCode(code)}）。请重新发送一次读取请求。`;
      return this.deliverReadContinuation(record, text, response);
    }

    const text = formatReadContinuation(continuation, response);
    const delivered = await this.deliverReadContinuation(record, text, response);
    if (delivered) {
      this.api.logger.info(
        `AgentBridge read request continued after login (tool=${continuation.toolName})`,
      );
    }
    return delivered;
  }

  async deliverReadContinuation(record, text, response) {
    if (await this.deliverTextDirect(record.sessionKey, text)) {
      return true;
    }
    const serialized = JSON.stringify(response || {}).slice(0, 12000);
    this.api.runtime.system.enqueueSystemEvent(
      [
        text,
        `Continued AgentBridge read tool: ${record.readContinuation.toolName}.`,
        `Structured result: ${serialized}`,
        "Answer the original user request from this result. Do not call the login tool again.",
      ].join("\n"),
      {
        sessionKey: record.sessionKey,
        contextKey: `agentbridge:read-continuation:${record.interaction.interactionId}`,
      },
    );
    await this.wakeAgent(
      record.sessionKey,
      "hook:agentbridge-read-continuation-completed",
    );
    return true;
  }

  async deliverTextDirect(sessionKey, text) {
    const route = this.sessionRoutes.get(sessionKey);
    if (!route) {
      this.api.logger.warn(
        "AgentBridge direct read continuation unavailable because the private session route is missing",
      );
      return false;
    }
    try {
      if (!(await this.sendRoutePayload(sessionKey, route, { text }))) {
        return false;
      }
      return true;
    } catch (error) {
      this.api.logger.warn(
        `AgentBridge direct read continuation failed: ${safeErrorCode(error)}`,
      );
      return false;
    }
  }

  async deliverInteractionsDirect(sessionKey, interactions) {
    const route = this.sessionRoutes.get(sessionKey);
    if (!route) {
      this.api.logger.warn(
        "AgentBridge direct card delivery unavailable because the private session route is missing",
      );
      return false;
    }
    try {
      const presentation = buildPresentation(interactions, route.channel);
      if (!presentation) {
        return false;
      }
      const text = "AgentBridge 已收到你提交的信息，请继续完成下面的安全操作。";
      const initialPayload = { text, presentation };
      if (
        !(await this.sendRoutePayload(
          sessionKey,
          route,
          initialPayload,
          presentation,
        ))
      ) {
        this.api.logger.warn(
          `AgentBridge direct card delivery unavailable for channel ${route.channel}`,
        );
        return false;
      }
      const deliveredIds = new Set(
        interactions.map((interaction) => interaction.interactionId),
      );
      for (const item of this.records.values()) {
        if (
          item.sessionKey === sessionKey &&
          deliveredIds.has(item.interaction.interactionId)
        ) {
          item.delivered = true;
        }
      }
      this.api.logger.info(
        `AgentBridge next trusted card delivered directly (channel=${route.channel}, count=${interactions.length})`,
      );
      return true;
    } catch (error) {
      this.api.logger.warn(
        `AgentBridge direct card delivery failed: ${safeErrorCode(error)}`,
      );
      return false;
    }
  }

  async deliverStatusDirect(sessionKey, status, errorCode, response = null) {
    const route = this.sessionRoutes.get(sessionKey);
    if (!route) {
      this.api.logger.warn(
        "AgentBridge direct status delivery unavailable because the private session route is missing",
      );
      return false;
    }
    const text = safeStatusMessage(status, errorCode, response);
    try {
      if (!(await this.sendRoutePayload(sessionKey, route, { text }))) {
        this.api.logger.warn(
          `AgentBridge direct status delivery unavailable for channel ${route.channel}`,
        );
        return false;
      }
      this.api.logger.info(
        `AgentBridge trusted interaction status delivered directly (channel=${route.channel}, status=${safeCode(status)})`,
      );
      return true;
    } catch (error) {
      this.api.logger.warn(
        `AgentBridge direct status delivery failed: ${safeErrorCode(error)}`,
      );
      return false;
    }
  }

  async sendRoutePayload(sessionKey, route, initialPayload, presentation = null) {
    const depth = this.directDeliveries.get(sessionKey) || 0;
    this.directDeliveries.set(sessionKey, depth + 1);
    try {
      const adapter = await this.api.runtime.channel.outbound.loadAdapter(
        route.channel,
      );
      if (!adapter?.sendPayload && !adapter?.sendText) {
        return false;
      }
      const text =
        typeof initialPayload.text === "string" ? initialPayload.text : "";
      const baseContext = {
        cfg: this.api.config,
        to: route.to,
        text,
        ...(route.accountId ? { accountId: route.accountId } : {}),
        ...(route.threadId !== null ? { threadId: route.threadId } : {}),
      };
      const payload =
        presentation && adapter.renderPresentation
          ? await adapter.renderPresentation({
              payload: initialPayload,
              presentation,
              ctx: { ...baseContext, payload: initialPayload },
            })
          : presentation
            ? appendPresentationLinks(initialPayload, presentation)
            : initialPayload;
      if (!payload) {
        return false;
      }
      if (!adapter.sendPayload) {
        await adapter.sendText({
          ...baseContext,
          text: typeof payload.text === "string" ? payload.text : text,
        });
        return true;
      }
      await adapter.sendPayload({
        ...baseContext,
        text: typeof payload.text === "string" ? payload.text : text,
        payload,
      });
      return true;
    } finally {
      if (depth === 0) {
        this.directDeliveries.delete(sessionKey);
      } else {
        this.directDeliveries.set(sessionKey, depth);
      }
    }
  }

  undeliveredPendingFor(record, { allowLaterRun = false } = {}) {
    this.prune();
    return [...this.records.values()]
      .filter(
        (candidate) =>
          candidate !== record &&
          candidate.sessionKey === record.sessionKey &&
          candidate.delivered === false &&
          ["pending", "processing"].includes(candidate.interaction.state) &&
          (allowLaterRun
            ? candidate.capturedAt >= record.capturedAt
            : record.runId && candidate.runId
              ? candidate.runId === record.runId
              : candidate.capturedAt >= record.capturedAt),
      )
      .sort((left, right) => left.capturedAt - right.capturedAt)
      .slice(0, 3)
      .map((candidate) => candidate.interaction);
  }

  async wakeAgent(sessionKey, reason = "hook:agentbridge-interaction-updated") {
    const options = {
      // OpenClaw infers hook wake semantics from this prefix when the plugin
      // runtime's runHeartbeatOnce facade cannot forward an explicit source.
      reason,
      sessionKey,
      heartbeat: { target: "last" },
    };
    if (typeof this.api.runtime.system.runHeartbeatOnce === "function") {
      try {
        const result = await this.api.runtime.system.runHeartbeatOnce(options);
        if (result?.status === "ran") {
          this.api.logger.info("AgentBridge completion heartbeat ran immediately");
          return;
        }
        this.api.logger.warn(
          `AgentBridge immediate heartbeat did not run: ${safeCode(result?.reason || result?.status || "UNKNOWN")}`,
        );
      } catch (error) {
        this.api.logger.warn(
          `AgentBridge immediate heartbeat failed: ${safeErrorCode(error)}`,
        );
      }
    }
    this.api.runtime.system.requestHeartbeat({
      source: "hook",
      intent: "event",
      reason: options.reason,
      sessionKey,
      heartbeat: options.heartbeat,
    });
    this.api.logger.info("AgentBridge completion heartbeat queued as fallback");
  }

  prune() {
    this.pruneToolBindings();
    const continuationCutoff = this.now() - LOGIN_CONTINUATION_TTL_MS;
    for (const [sessionKey, continuation] of this.loginContinuations) {
      if (continuation.capturedAt <= continuationCutoff) {
        this.loginContinuations.delete(sessionKey);
      }
    }
    for (const [sessionKey, message] of this.recentUserMessages) {
      if (message.capturedAt <= continuationCutoff) {
        this.recentUserMessages.delete(sessionKey);
      }
    }
    for (const [interactionId, record] of this.records) {
      if (isInteractionExpired(record.interaction, this.now())) {
        this.abortControllers.get(interactionId)?.abort();
        this.records.delete(interactionId);
      }
    }
    while (this.records.size > MAX_INTERACTIONS) {
      const oldest = this.records.keys().next().value;
      if (!oldest) {
        break;
      }
      this.abortControllers.get(oldest)?.abort();
      this.records.delete(oldest);
    }
  }

  takeToolBinding(toolCallId) {
    const normalized = normalizeToolCallId(toolCallId);
    if (!normalized) {
      return null;
    }
    const binding = this.toolBindings.get(normalized) || null;
    this.toolBindings.delete(normalized);
    return binding;
  }

  pruneToolBindings() {
    const cutoff = this.now() - TOOL_BINDING_TTL_MS;
    for (const [toolCallId, binding] of this.toolBindings) {
      if (binding.capturedAt <= cutoff) {
        this.toolBindings.delete(toolCallId);
      }
    }
    while (this.toolBindings.size > MAX_TOOL_BINDINGS) {
      const oldest = this.toolBindings.keys().next().value;
      if (!oldest) {
        break;
      }
      this.toolBindings.delete(oldest);
    }
  }
}

export function presentationForRecords(interactions, channel) {
  return buildPresentation(interactions, channel);
}

function interactionDeadline(interaction) {
  if (!interaction.expiresAt) {
    return null;
  }
  const value = Date.parse(interaction.expiresAt);
  return Number.isFinite(value) ? value : null;
}

function trustedAgentBridgeStructuredContent(result, serverName) {
  const details = result?.details;
  if (
    !details ||
    typeof details !== "object" ||
    Array.isArray(details) ||
    details.mcpServer !== serverName ||
    typeof details.mcpTool !== "string" ||
    !details.mcpTool.trim() ||
    !details.structuredContent ||
    typeof details.structuredContent !== "object"
  ) {
    return null;
  }
  return details.structuredContent;
}

function normalizeReadContinuation(toolName, params, capturedAt) {
  const normalizedToolName = String(toolName || "").trim();
  const descriptor = LOGIN_READ_TOOLS.get(normalizedToolName);
  if (!descriptor) {
    return null;
  }
  const source =
    params && typeof params === "object" && !Array.isArray(params) ? params : {};
  const arguments_ = {};
  if (typeof source.keyword === "string" && source.keyword.trim()) {
    arguments_.keyword = source.keyword.trim().slice(0, 200);
  }
  const maximumLimit =
    descriptor.kind === "taihua_project"
      ? 50
      : descriptor.kind === "oa_workflow"
        ? 100
        : 500;
  if (
    Number.isInteger(source.limit) &&
    source.limit >= 1 &&
    source.limit <= maximumLimit
  ) {
    arguments_.limit = source.limit;
  }
  if (descriptor.kind === "taihua_work_log") {
    for (const name of ["log_date", "start_date", "end_date"]) {
      if (
        typeof source[name] === "string" &&
        /^\d{4}-\d{2}-\d{2}$/.test(source[name])
      ) {
        arguments_[name] = source[name];
      }
    }
    for (const name of ["member", "department", "watch_group"]) {
      if (typeof source[name] === "string" && source[name].trim()) {
        arguments_[name] = source[name].trim().slice(0, 200);
      }
    }
    for (const [name, maximum] of [
      ["page", 10000],
      ["size", 100],
    ]) {
      if (Number.isInteger(source[name]) && source[name] >= 1 && source[name] <= maximum) {
        arguments_[name] = source[name];
      }
    }
    if (["submittedAt", "logDate"].includes(source.view_mode)) {
      arguments_.view_mode = source.view_mode;
    }
    for (const name of ["dept_id", "member_id", "watch_group_id"]) {
      if (Number.isInteger(source[name]) && source[name] >= 1) {
        arguments_[name] = source[name];
      }
    }
  }
  return Object.freeze({
    toolName: normalizedToolName,
    arguments: Object.freeze(arguments_),
    capturedAt,
  });
}

function inferReadContinuation(text, capturedAt) {
  const value = String(text || "");
  const candidates = [
    ["团队日志", "taihua_work_log_team_list"],
    ["我的日志", "taihua_work_log_my_list"],
    ["泰华项目", "taihua_project_search"],
    ["\u5f85\u529e", "oa_workflow_pending_list"],
    ["\u5df2\u53d1", "oa_workflow_sent_list"],
    ["\u5df2\u529e", "oa_workflow_done_list"],
    ["\u8ddf\u8e2a", "oa_workflow_tracked_list"],
  ];
  const matched = candidates.find(([keyword]) => value.includes(keyword));
  if (!matched) {
    return null;
  }
  const arguments_ = {};
  const limitMatch = value.match(/(?:\u8fd1|\u524d)?\s*(\d{1,3})\s*\u6761/);
  if (limitMatch) {
    const limit = Number.parseInt(limitMatch[1], 10);
    if (limit >= 1 && limit <= 100) {
      arguments_.limit = limit;
    }
  }
  return normalizeReadContinuation(matched[1], arguments_, capturedAt);
}

function isFreshContinuation(continuation, now) {
  return Boolean(
    continuation &&
      Number.isFinite(continuation.capturedAt) &&
      now - continuation.capturedAt <= LOGIN_CONTINUATION_TTL_MS,
  );
}

function isLoginRequiredPayload(payload) {
  return Boolean(
    payload &&
      typeof payload === "object" &&
      !Array.isArray(payload) &&
      (payload?.error?.code === "LOGIN_REQUIRED" ||
        payload?.nextAction?.type === "session_login"),
  );
}

function formatReadContinuation(continuation, response) {
  const descriptor = LOGIN_READ_TOOLS.get(continuation.toolName);
  const result =
    response?.result && typeof response.result === "object"
      ? response.result
      : {};
  const items = Array.isArray(result.items) ? result.items : [];
  const count = Number.isInteger(result.count) ? result.count : items.length;
  const lines = [
    `${descriptor.system} 登录已恢复，已自动继续读取${descriptor.label}，共 ${count} 条：`,
  ];
  if (items.length === 0) {
    return lines.join("\n");
  }
  let shown = 0;
  for (const item of items) {
    const block = formatReadItem(descriptor, item, shown + 1);
    if ([...lines, block].join("\n\n").length > 3500) {
      break;
    }
    lines.push(block);
    shown += 1;
  }
  if (shown < items.length) {
    lines.push(`其余 ${items.length - shown} 条未在本条消息中展开。`);
  }
  return lines.join("\n\n");
}

function formatReadItem(descriptor, item, index) {
  if (descriptor.kind === "taihua_work_log") {
    const date = safeDisplayText(item?.logDate, 40);
    const person = safeDisplayText(item?.fullname || item?.username, 120);
    const hours = Number.isFinite(Number(item?.hours)) ? `${item.hours} 小时` : "";
    const project = safeDisplayText(item?.projectName, 200);
    const metadata = [date, person, hours, project].filter(Boolean).join(" | ");
    const content = safeDisplayText(item?.content, 1000) || "(无日志内容)";
    return [`${index}. ${metadata}`.trim(), `   ${content}`].join("\n");
  }
  if (descriptor.kind === "taihua_project") {
    const name = safeDisplayText(item?.name, 300) || "(未命名项目)";
    const code = safeDisplayText(item?.code, 120);
    const status = safeDisplayText(item?.status, 120);
    const metadata = [code, status].filter(Boolean).join(" | ");
    return [`${index}. ${name}`, metadata ? `   ${metadata}` : ""]
      .filter(Boolean)
      .join("\n");
  }

  const date = safeDisplayText(item?.date, 40);
  const title = safeDisplayText(item?.title, 300) || "(untitled)";
  const affairId = safeDisplayText(item?.affair_id, 160);
  let metadata;
  if (descriptor.collection === "pending") {
    const readState = item?.read ? "已读" : "未读";
    const sender = safeDisplayText(item?.sender, 120);
    metadata = [`[${readState}]`, date, sender].filter(Boolean).join(" | ");
  } else {
    const status = safeDisplayText(item?.status, 120);
    metadata = [date, status].filter(Boolean).join(" | ");
  }
  return [
    `${index}. ${metadata}`.trim(),
    `   ${title}`,
    affairId ? `   affair_id: ${affairId}` : "",
  ]
    .filter(Boolean)
    .join("\n");
}

function safeDisplayText(value, limit) {
  return String(value ?? "")
    .replace(/[\u0000-\u001f\u007f]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, limit);
}

function shouldResumeOriginalRequest(record, response, nextInteractions) {
  return Boolean(
    record?.interaction?.type === "credential" &&
      nextInteractions.length === 0 &&
      safeStatus(response) === "succeeded" &&
      response?.nextAction?.type === "retry_original_request",
  );
}

function safeStatus(response) {
  const status = String(response?.status ?? "completed")
    .toLowerCase()
    .replace(/[^a-z0-9_.-]/g, "_")
    .slice(0, 80);
  return status || "completed";
}

function safeResponseErrorCode(response) {
  return response?.error?.code ? safeCode(response.error.code) : null;
}

function safeBusinessRuleMessage(response) {
  const message = response?.error?.message;
  if (typeof message !== "string") {
    return "";
  }
  return message
    .replace(/<[^>]*>/g, "")
    .replace(/https?:\/\/\S+/gi, "[\u94fe\u63a5\u5df2\u9690\u85cf]")
    .replace(/[\u0000-\u001f\u007f]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 500);
}

function safeErrorCode(error) {
  return safeCode(error?.code || error?.name || "UNKNOWN_ERROR");
}

function safeCode(value) {
  return String(value)
    .toUpperCase()
    .replace(/[^A-Z0-9_.-]/g, "_")
    .slice(0, 80);
}

function safeSucceededMessage(response) {
  const result = response?.result;
  if (!result || typeof result !== "object" || Array.isArray(result)) {
    return "AgentBridge 已完成本次安全操作。";
  }
  const verified =
    result.verification?.confirmed === true || result.verification?.matched === true;
  if (result.meeting_created === true && result.meeting_sent === true) {
    return verified
      ? "OA 会议已创建并发送，并已通过回读确认。"
      : "OA 会议已创建并发送。";
  }
  if (
    result.business_intent === "submit_business_trip_request" &&
    result.workflow_submitted === true
  ) {
    return verified
      ? "OA 出差申请已提交审批，并已通过已发事项回读确认。"
      : "OA 出差申请已提交审批。";
  }
  if (
    result.business_intent === "submit_leave_request" &&
    result.workflow_submitted === true
  ) {
    return verified
      ? "OA 请假申请已提交审批，并已通过已发事项回读确认。"
      : "OA 请假申请已提交审批。";
  }
  if (
    result.business_intent === "revoke_sent_workflow" &&
    result.workflow_revoked === true
  ) {
    return verified
      ? "OA 已发流程已撤销，并已通过已发消失及待发撤销状态回读确认。"
      : "OA 已发流程已撤销。";
  }
  if (result.pending_action_processed === true) {
    const subjects = {
      efficiency_data: "OA \u6548\u80fd\u6570\u636e\u6d41\u7a0b",
      travel_expense: "OA \u5dee\u65c5\u8d39\u5ba1\u6279\u62a5\u9500\u5355",
      labor_contract_renewal: "OA \u52b3\u52a8\u5408\u540c\u7eed\u7b7e\u8868",
      weekly_report: "OA \u5468\u62a5\u53d1\u9001\u6d41\u7a0b",
      standard_collaboration: "OA \u666e\u901a\u534f\u540c\u4e8b\u9879",
    };
    const subject = subjects[result.workflow_profile] || "OA \u5f85\u529e\u4e8b\u9879";
    const action =
      result.action_kind === "acknowledgement" ? "\u5df2\u9605\u529e" : "\u5df2\u5ba1\u6279\u901a\u8fc7";
    return verified
      ? `${subject}${action}\uff0c\u5e76\u5df2\u901a\u8fc7\u5f85\u529e\u56de\u8bfb\u786e\u8ba4\u3002`
      : `${subject}${action}\u3002`;
  }
  if (
    result.status === "created" &&
    result.workLog &&
    typeof result.workLog === "object"
  ) {
    return verified
      ? "泰华工作日志已正式提交，并已通过日志回读确认。"
      : "泰华工作日志已正式提交。";
  }  if (result.draft_saved === true && result.workflow_submitted === false) {
    return verified
      ? "OA 待发草稿已保存，未提交审批，并已通过回读确认。"
      : "OA 待发草稿已保存，未提交审批。";
  }
  if (result.workflow_approved === true) {
    return verified
      ? "OA 补签申请已审批通过，并已通过待办回读确认。"
      : "OA 补签申请已审批通过。";
  }
  return "AgentBridge 已完成本次安全操作。";
}

function safeStatusMessage(status, errorCode, response = null) {
  const code = errorCode ? `（错误码：${safeCode(errorCode)}）` : "";
  switch (safeStatus({ status })) {
    case "succeeded":
      return safeSucceededMessage(response);
    case "already_resumed":
      return "AgentBridge 已完成本次安全操作，无需重复处理。";
    case "declined":
      return "你已拒绝本次 AgentBridge 安全操作，系统未继续执行。";
    case "expired":
      return "本次 AgentBridge 安全交互已过期，请在智能体中重新发起。";
    case "superseded":
      return "本次 AgentBridge 安全交互已被新的请求替代。";
    case "completed":
      return "AgentBridge 已收到安全页面的处理结果。";
    case "poll_expired":
      return "AgentBridge 等待安全交互完成已超时，请在智能体中重新发起。";
    case "poll_failed":
      return `AgentBridge 暂时无法查询安全交互状态${code}。`;
    case "resume_failed":
      return `AgentBridge 未能继续执行本次安全操作${code}。`;
    case "unknown":
      if (safeCode(errorCode) === "RESULT_UNKNOWN") {
        return "业务系统写操作的最终结果未能确认。AgentBridge 已停止且不会自动重试，请先到对应业务系统中核对实际结果后再决定下一步（错误码：RESULT_UNKNOWN）。";
      }
      return "AgentBridge 无法确认本次安全操作的最终状态" + code + "。";
    case "failed":
      if (
        ["OA_BUSINESS_RULE_REJECTED", "TAIHUA_BUSINESS_RULE_REJECTED"].includes(
          safeCode(errorCode),
        )
      ) {
        const reason = safeBusinessRuleMessage(response);
        const taihua = safeCode(errorCode) === "TAIHUA_BUSINESS_RULE_REJECTED";
        const system = taihua ? "泰华日志系统" : "OA";
        const action = taihua ? "工作日志" : "申请";
        return reason
          ? `${system} 未提交本次${action}：${reason}${code}。`
          : `${system} 根据业务规则拒绝了本次${action}${code}。`;
      }
      return `AgentBridge 未能完成本次安全操作${code}。`;
    default:
      return `AgentBridge 安全交互状态已更新：${safeCode(status)}${code}。`;
  }
}

function normalizeToolCallId(value) {
  if (typeof value !== "string") {
    return null;
  }
  const normalized = value.trim();
  return normalized ? normalized.slice(0, 256) : null;
}

function safeRoutePart(value) {
  if (typeof value !== "string" && typeof value !== "number") {
    return null;
  }
  const normalized = String(value).trim();
  return normalized ? normalized.slice(0, 512) : null;
}

function normalizeThreadId(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  return safeRoutePart(value);
}

function defaultSleep(milliseconds, signal) {
  return new Promise((resolve) => {
    if (signal?.aborted) {
      resolve();
      return;
    }
    const timer = setTimeout(resolve, milliseconds);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}
