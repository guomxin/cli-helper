import { randomUUID } from "node:crypto";
import { mkdir, rename, unlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import {
  appendPresentationLinks,
  buildPresentation,
  collectPublicInteractionReferences,
  isInteractionExpired,
  isPrivateSessionKey,
  processToolResult,
} from "./interaction.js";
import {
  collectTaskReferences,
  hostContextMeta,
  TASK_CONTEXT_META_KEY,
} from "./proxy-tools.js";

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
const QUIET_COMPANION_TASK_EVENTS = new Set([
  "task.created",
  "task.operation.linked",
  "task.operation.running",
  "task.operation.requires_user_action",
  "task.interaction.waiting",
  "task.interaction.completed",
  "task.completed",
  "task.artifact.delivery",
]);
const PULL_BASED_CHANNELS = new Set(["web", "webchat"]);
const MAX_NOTIFICATION_IDLE_INTERVAL_MS = 10_000;
const PREPARED_DOCUMENT_NATIVE_ATTEMPTS = 2;
const PREPARED_DOCUMENT_RETRY_DELAY_MS = 1_000;
const PREPARED_DOCUMENT_RECEIPT_TTL_MS = 10 * 60 * 1000;
const MAX_PREPARED_DOCUMENT_RECEIPTS = 1_000;
const INDEPENDENT_TASK_ENTRY_TOOLS = new Set([
  "oa_workflow_revoke_prepare",
]);
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
  [
    "smartlight_system_overview",
    { kind: "smartlight_overview", system: "照明实验室测试系统", label: "系统概览" },
  ],
  [
    "smartlight_runtime_overview",
    { kind: "smartlight_runtime", system: "照明实验室测试系统", label: "运行概览" },
  ],
  [
    "smartlight_rtu_status_list",
    { kind: "smartlight_rtu_status", system: "照明实验室测试系统", label: "RTU 运行状态" },
  ],
  [
    "smartlight_lamp_status_list",
    { kind: "smartlight_lamp_status", system: "照明实验室测试系统", label: "单灯运行状态" },
  ],
  [
    "smartlight_lamp_alarm_list",
    { kind: "smartlight_lamp_alarm", system: "照明实验室测试系统", label: "单灯告警" },
  ],
  [
    "smartlight_lamp_alarm_analysis",
    { kind: "smartlight_lamp_alarm", system: "照明实验室测试系统", label: "单灯告警分析" },
  ],
  [
    "smartlight_rtu_survey_records",
    { kind: "smartlight_rtu_survey", system: "照明实验室测试系统", label: "RTU 巡测记录" },
  ],
  [
    "smartlight_energy_record_list",
    { kind: "smartlight_energy", system: "照明实验室测试系统", label: "RTU 用电记录" },
  ],
  [
    "smartlight_energy_analysis",
    { kind: "smartlight_energy", system: "照明实验室测试系统", label: "RTU 用电分析" },
  ],
  [
    "smartlight_lamp_survey_records",
    { kind: "smartlight_lamp_survey", system: "照明实验室测试系统", label: "单灯巡测记录" },
  ],
  [
    "smartlight_rtu_leakage_alarm_list",
    { kind: "smartlight_rtu_leakage", system: "照明实验室测试系统", label: "RTU 支路漏电报警" },
  ],
  [
    "smartlight_rtu_leakage_analysis",
    { kind: "smartlight_rtu_leakage", system: "照明实验室测试系统", label: "RTU 支路漏电分析" },
  ],
  [
    "smartlight_off_hours_current_list",
    { kind: "smartlight_off_hours_current", system: "照明实验室测试系统", label: "关灯时段电流" },
  ],
  [
    "smartlight_inspection_log_list",
    { kind: "smartlight_inspection_log", system: "照明实验室测试系统", label: "巡检日志统计" },
  ],
  [
    "smartlight_maintenance_record_list",
    { kind: "smartlight_maintenance", system: "照明实验室测试系统", label: "检修记录" },
  ],
  [
    "smartlight_lamppost_list",
    { kind: "smartlight_lamppost", system: "照明实验室测试系统", label: "灯杆" },
  ],
  [
    "smartlight_alarm_list",
    { kind: "smartlight_alarm", system: "照明实验室测试系统", label: "RTU 告警" },
  ],
  [
    "smartlight_alarm_remark_get",
    { kind: "smartlight_alarm", system: "照明实验室测试系统", label: "RTU 告警备注" },
  ],
  [
    "smartlight_inspection_task_list",
    { kind: "smartlight_inspection", system: "照明实验室测试系统", label: "巡检任务" },
  ],
  [
    "smartlight_leakage_summary",
    { kind: "smartlight_lamp_alarm", system: "照明实验室测试系统", label: "单灯告警（兼容入口）" },
  ],
  [
    "smartlight_asset_search",
    { kind: "smartlight_asset", system: "照明实验室测试系统", label: "设施查询" },
  ],
  [
    "smartlight_asset_detail",
    { kind: "smartlight_asset", system: "照明实验室测试系统", label: "设施详情" },
  ],
  [
    "smartlight_alarm_analysis",
    { kind: "smartlight_alarm", system: "照明实验室测试系统", label: "RTU 告警分析" },
  ],
  [
    "smartlight_inspection_task_detail",
    { kind: "smartlight_inspection", system: "照明实验室测试系统", label: "巡检任务详情" },
  ],
  [
    "smartlight_leakage_analysis",
    { kind: "smartlight_lamp_alarm", system: "照明实验室测试系统", label: "单灯告警分析（兼容入口）" },
  ],
  [
    "smartlight_report_export",
    { kind: "smartlight_report", system: "照明实验室测试系统", label: "CSV 报告" },
  ],
  [
    "yuque_public_books_list",
    { kind: "yuque_book", system: "部门信息库", label: "公共区知识库" },
  ],
  [
    "yuque_document_catalog",
    { kind: "yuque_document_list", system: "部门信息库", label: "文档目录" },
  ],
  [
    "yuque_document_search",
    { kind: "yuque_document_list", system: "部门信息库", label: "搜索结果" },
  ],
  [
    "yuque_document_read",
    { kind: "yuque_document", system: "部门信息库", label: "文档正文" },
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
    documentDeliveries: new Map(),
    documentDeliveryReceipts: new Map(),
    taskContinuations: new Map(),
    taskContinuationChoices: new Map(),
    independentTaskBindings: new Map(),
    identitySessionBindings: new Map(),
    identitySessionEndpoints: new Map(),
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
    notificationSleep = backgroundSleep,
    now = Date.now,
    fetchImpl = globalThis.fetch,
    saveMediaBufferImpl = saveOpenClawMediaBuffer,
    interactionPresenter = null,
  }) {
    this.api = api;
    this.config = config;
    this.mcpClient = mcpClient;
    this.mcpClientResolver = mcpClientResolver;
    this.sleep = sleep;
    this.notificationSleep = notificationSleep;
    this.now = now;
    this.fetchImpl = fetchImpl;
    this.saveMediaBufferImpl = saveMediaBufferImpl;
    this.interactionPresenter = interactionPresenter;
    this.sharedStateId = sharedState.id || "isolated";
    this.records = sharedState.records;
    this.polls = new Map();
    this.abortControllers = new Map();
    this.notificationController = null;
    this.notificationPump = null;
    this.toolBindings = new Map();
    this.sessionRoutes = sharedState.sessionRoutes;
    this.directDeliveries = sharedState.directDeliveries;
    this.loginContinuations =
      sharedState.loginContinuations || (sharedState.loginContinuations = new Map());
    this.recentUserMessages =
      sharedState.recentUserMessages || (sharedState.recentUserMessages = new Map());
    this.documentDeliveries =
      sharedState.documentDeliveries || (sharedState.documentDeliveries = new Map());
    this.documentDeliveryReceipts =
      sharedState.documentDeliveryReceipts ||
      (sharedState.documentDeliveryReceipts = new Map());
    this.taskContinuations =
      sharedState.taskContinuations || (sharedState.taskContinuations = new Map());
    this.taskContinuationChoices =
      sharedState.taskContinuationChoices ||
      (sharedState.taskContinuationChoices = new Map());
    this.independentTaskBindings =
      sharedState.independentTaskBindings ||
      (sharedState.independentTaskBindings = new Map());
  }

  recordUserMessage(event, context) {
    const sessionKey = event.sessionKey || context.sessionKey;
    const rawText = extractUserMessageText(event);
    if (!isPrivateSessionKey(sessionKey) || !rawText) {
      return;
    }
    const text = rawText.trim().slice(0, 1000);
    if (!text) {
      return;
    }
    // A continuation applies to one inbound turn. The prompt hook may bind a
    // fresh server-backed selection before this turn reaches any business tool.
    this.taskContinuations.delete(sessionKey);
    this.independentTaskBindings.delete(sessionKey);
    this.recentUserMessages.set(sessionKey, {
      text,
      capturedAt: this.now(),
      taskRunRef: `turn:${randomUUID()}`,
    });
  }

  bindWorkspaceTurn(sessionKey, turnRef, message = null) {
    if (!isPrivateSessionKey(sessionKey)) {
      return false;
    }
    const normalizedTurnRef = safeRoutePart(turnRef);
    if (!normalizedTurnRef) {
      return false;
    }
    this.taskContinuations.delete(sessionKey);
    this.independentTaskBindings.delete(sessionKey);
    const normalizedMessage = safeMessageText(message, 1000);
    this.recentUserMessages.set(sessionKey, {
      text: normalizedMessage || null,
      capturedAt: this.now(),
      taskRunRef: `workspace:${normalizedTurnRef}`,
    });
    return true;
  }

  normalizeBusinessToolArguments({ sessionKey, toolName, params }) {
    if (toolName !== "smartlight_alarm_list") {
      return params;
    }
    const message = this.recentUserMessages.get(sessionKey)?.text;
    return normalizeSmartlightAlarmListArguments(params, message);
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

  isPullBasedSession(sessionKey) {
    const channel = String(
      this.sessionRoutes.get(sessionKey)?.channel || "",
    ).toLowerCase();
    return PULL_BASED_CHANNELS.has(channel);
  }

  redundantInteractionGet({ sessionKey, runId, toolCallId, interactionId }) {
    this.prune();
    const normalizedInteractionId = safeRoutePart(interactionId);
    const normalizedToolCallId = normalizeToolCallId(toolCallId);
    const binding = normalizedToolCallId
      ? this.toolBindings.get(normalizedToolCallId)
      : null;
    const activeRunId = safeRoutePart(runId || binding?.runId);
    const record = normalizedInteractionId
      ? this.records.get(normalizedInteractionId)
      : null;
    if (
      !record ||
      !isPrivateSessionKey(sessionKey) ||
      record.sessionKey !== sessionKey ||
      !activeRunId ||
      record.runId !== activeRunId ||
      !["pending", "processing"].includes(record.interaction.state)
    ) {
      return null;
    }
    this.api.logger.info(
      "AgentBridge suppressed a redundant same-run interaction_get; the host already has the card",
    );
    return {
      status: "host_handled",
      interactionId: normalizedInteractionId,
      interactionState: record.interaction.state,
      message:
        "The host already has this trusted interaction. Do not fetch it again in this run; briefly ask the user to use the displayed card.",
    };
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

  taskRunRefForToolCall(toolCallId, sessionKey, toolName = null) {
    const normalized = normalizeToolCallId(toolCallId);
    const binding = normalized ? this.toolBindings.get(normalized) : null;
    if (!binding || (binding.sessionKey && binding.sessionKey !== sessionKey)) {
      return normalized;
    }
    const recent = this.recentUserMessages.get(sessionKey);
    if (
      !INDEPENDENT_TASK_ENTRY_TOOLS.has(String(toolName || "")) &&
      recent?.taskRunRef
    ) {
      return recent.taskRunRef;
    }
    return binding.runId || normalized;
  }

  deliverPreparedDocumentResult(event, context) {
    const payload = trustedAgentBridgeStructuredContent(
      event.result,
      this.config.mcpServerName,
    );
    const files = normalizePreparedDocuments(
      payload,
      this.config.allowedCardOrigins,
    );
    if (files.length === 0) {
      return null;
    }
    const toolCallId = normalizeToolCallId(event.toolCallId);
    const binding = toolCallId ? this.toolBindings.get(toolCallId) : null;
    const sessionKey = binding?.sessionKey || context.sessionKey;
    const taskId = taskIdFromToolResult(event.result);
    const deliveryRef = `tool-result:${toolCallId || randomUUID()}`;
    if (!isPrivateSessionKey(sessionKey)) {
      this.api.logger.warn(
        "AgentBridge prepared document withheld because no private session binding was available",
      );
      return Promise.resolve(
        preparedDocumentDeliveryReport(files, [], {
          state: "failed",
          errorCode: "PRIVATE_SESSION_REQUIRED",
        }),
      );
    }
    const previous = this.documentDeliveries.get(sessionKey) || Promise.resolve();
    const route = this.sessionRoutes.get(sessionKey);
    const endpointMode = this.isPullBasedSession(sessionKey)
      ? "workspace_download"
      : "native_channel";
    const delivery = previous
      .catch(() => undefined)
      .then(async () => {
        const receipts = [];
        for (const file of files) {
          receipts.push(
            await this.deliverPreparedDocumentDirect(sessionKey, file, {
              deliveryRef,
            }),
          );
        }
        const report = preparedDocumentDeliveryReport(files, receipts, {
          channel: route?.channel || "unknown",
          endpointMode,
        });
        await this.reportPreparedDocumentDelivery({
          sessionKey,
          taskId,
          deliveryRef,
          report,
        });
        return report;
      });
    this.documentDeliveries.set(sessionKey, delivery);
    return delivery.finally(() => {
      if (this.documentDeliveries.get(sessionKey) === delivery) {
        this.documentDeliveries.delete(sessionKey);
      }
    });
  }

  captureToolResult(event, context) {
    const binding = this.takeToolBinding(event.toolCallId);
    const taskId = taskIdFromToolResult(event.result);
    const processed = processToolResult(
      event.result,
      this.config.allowedCardOrigins,
    );
    if (processed.sanitized) {
      const sessionKey = binding?.sessionKey || context.sessionKey;
      const presented = this.presentInteractions(
        processed.interactions,
        sessionKey,
      );
      if (presented) {
        return presented.then((interactions) => {
          this.captureInteractions(interactions, binding, context, taskId);
          return { result: processed.result };
        });
      }
      this.captureInteractions(processed.interactions, binding, context, taskId);
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
      taskId,
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

  captureInteractions(interactions, binding, context, taskId = null) {
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
        taskId,
      });
      this.api.logger.info(
        `AgentBridge interaction captured for private session (type=${interaction.type}, state=${interaction.state})`,
      );
      this.startPolling(record);
    }
  }

  async hydratePublicInteractionReferences(
    references,
    binding,
    context,
    taskId = null,
  ) {
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
    const presented =
      (await this.presentInteractions(interactions, sessionKey)) || interactions;
    this.captureInteractions(presented, binding, context, taskId);
    return undefined;
  }

  presentInteractions(interactions, sessionKey) {
    if (
      typeof this.interactionPresenter !== "function" ||
      !isPrivateSessionKey(sessionKey) ||
      !interactions.some(
        (interaction) => interaction.type === "execution_authorization",
      )
    ) {
      return null;
    }
    return Promise.all(
      interactions.map(async (interaction) => {
        if (interaction.type !== "execution_authorization") {
          return interaction;
        }
        try {
          return (
            (await this.interactionPresenter(interaction, sessionKey)) ||
            interaction
          );
        } catch (error) {
          this.api.logger.warn(
            `AgentBridge endpoint presentation failed; original card retained (${safeErrorCode(error)})`,
          );
          return interaction;
        }
      }),
    );
  }

  activeTaskForSession(sessionKey) {
    this.prune();
    if (!isPrivateSessionKey(sessionKey)) {
      return null;
    }
    const matches = [...this.records.values()]
      .filter(
        (record) =>
          record.sessionKey === sessionKey &&
          record.taskId &&
          ["pending", "processing"].includes(record.interaction.state),
      )
      .sort((left, right) => right.capturedAt - left.capturedAt);
    return (
      matches[0]?.taskId ||
      this.taskContinuationForSession(sessionKey)?.taskId ||
      null
    );
  }

  bindTaskContinuation({
    sessionKey,
    taskId,
    taskStatus,
    executionMode,
    allowNewOperation,
    expiresAt,
  }) {
    if (!isPrivateSessionKey(sessionKey)) {
      return null;
    }
    const normalizedTaskId = safeRoutePart(taskId);
    if (!normalizedTaskId) {
      return null;
    }
    const serverExpiry = Date.parse(String(expiresAt || ""));
    const localExpiry = this.now() + 6 * 60 * 60 * 1000;
    const record = {
      taskId: normalizedTaskId,
      taskStatus: safeRoutePart(taskStatus) || "unknown",
      executionMode: safeRoutePart(executionMode) || "observe_only",
      allowNewOperation: allowNewOperation === true,
      expiresAt: Number.isFinite(serverExpiry)
        ? Math.min(serverExpiry, localExpiry)
        : localExpiry,
      capturedAt: this.now(),
    };
    this.taskContinuations.set(sessionKey, record);
    return record;
  }

  taskContinuationForSession(sessionKey) {
    this.prune();
    const record = this.taskContinuations.get(sessionKey) || null;
    return record && record.expiresAt > this.now() ? record : null;
  }

  bindTaskContinuationChoice(sessionKey, { ttlMs = 10 * 60 * 1000 } = {}) {
    if (!isPrivateSessionKey(sessionKey)) {
      return null;
    }
    const record = {
      capturedAt: this.now(),
      expiresAt: this.now() + ttlMs,
    };
    this.taskContinuationChoices.set(sessionKey, record);
    return record;
  }

  taskContinuationChoiceForSession(sessionKey) {
    this.prune();
    const record = this.taskContinuationChoices.get(sessionKey) || null;
    return record && record.expiresAt > this.now() ? record : null;
  }

  clearTaskContinuationChoice(sessionKey) {
    this.taskContinuationChoices.delete(sessionKey);
  }

  taskIdForBusinessCall(sessionKey, toolName = null) {
    if (INDEPENDENT_TASK_ENTRY_TOOLS.has(toolName)) {
      // Revoke is a new user-visible job even when it references the workflow
      // produced by a previous submission task.
      this.taskContinuations.delete(sessionKey);
      const binding = this.independentTaskBindings.get(sessionKey);
      return binding?.toolName === toolName ? binding.taskId : null;
    }
    const continuation = this.taskContinuationForSession(sessionKey);
    if (continuation) {
      return continuation.allowNewOperation ? continuation.taskId : null;
    }
    const matches = [...this.records.values()]
      .filter(
        (record) =>
          record.sessionKey === sessionKey &&
          record.taskId &&
          ["pending", "processing"].includes(record.interaction.state),
      )
      .sort((left, right) => right.capturedAt - left.capturedAt);
    return matches[0]?.taskId || null;
  }

  bindIndependentTask(sessionKey, toolName, taskId) {
    if (
      !isPrivateSessionKey(sessionKey) ||
      !INDEPENDENT_TASK_ENTRY_TOOLS.has(toolName)
    ) {
      return false;
    }
    const normalizedTaskId = safeRoutePart(taskId);
    if (!normalizedTaskId) {
      return false;
    }
    this.independentTaskBindings.set(sessionKey, {
      toolName,
      taskId: normalizedTaskId,
    });
    return true;
  }

  async restoreRecoveredInteraction({
    taskId,
    interaction,
    sessionKey,
    runId = null,
    mcpClient = null,
  }) {
    if (!isPrivateSessionKey(sessionKey) || !taskId || !interaction) {
      return false;
    }
    const existing = this.records.get(interaction.interactionId);
    const alreadyDelivered = Boolean(
      existing?.delivered &&
        existing.sessionKey === sessionKey &&
        existing.taskId === taskId,
    );
    const presented =
      (await this.presentInteractions([interaction], sessionKey)) || [
        interaction,
      ];
    const record = this.upsert({
      interaction: presented[0],
      sessionKey,
      runId,
      taskId,
    });
    record.mcpClient ||= mcpClient;
    if (
      record.interaction.state === "completed" &&
      record.interaction.resume?.ready === true &&
      record.interaction.resume?.completed !== true
    ) {
      await this.resume(record, new AbortController().signal);
      return true;
    }
    this.startPolling(record);
    if (!alreadyDelivered) {
      await this.deliverInteractionsDirect(sessionKey, presented);
    }
    return true;
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
    this.taskContinuations.delete(sessionKey);
    this.taskContinuationChoices.delete(sessionKey);
    this.independentTaskBindings.delete(sessionKey);
    this.sessionRoutes.delete(sessionKey);
    this.directDeliveries.delete(sessionKey);
    this.documentDeliveries.delete(sessionKey);
    for (const key of this.documentDeliveryReceipts.keys()) {
      if (key.startsWith(`${sessionKey}\u0000`)) {
        this.documentDeliveryReceipts.delete(key);
      }
    }
  }

  stopAll() {
    this.notificationController?.abort();
    this.notificationController = null;
    this.notificationPump = null;
    for (const controller of this.abortControllers.values()) {
      controller.abort();
    }
    this.abortControllers.clear();
    this.polls.clear();
    this.toolBindings.clear();
    this.loginContinuations.clear();
    this.recentUserMessages.clear();
    this.taskContinuations.clear();
    this.taskContinuationChoices.clear();
    this.independentTaskBindings.clear();
    this.sessionRoutes.clear();
    this.directDeliveries.clear();
    this.documentDeliveries.clear();
    this.documentDeliveryReceipts.clear();
  }

  startNotificationPump(identityRouter, { intervalMs = 2000 } = {}) {
    if (
      this.notificationController ||
      !identityRouter?.enabled
    ) {
      return false;
    }
    const controller = new AbortController();
    this.notificationController = controller;
    this.notificationPump = this.runNotificationPump(
      identityRouter,
      controller.signal,
      intervalMs,
    ).catch((error) => {
      if (!controller.signal.aborted) {
        this.api.logger.warn(
          `AgentBridge notification pump stopped: ${safeErrorCode(error)}`,
        );
      }
    });
    return true;
  }

  async runNotificationPump(identityRouter, signal, intervalMs) {
    await Promise.all(
      identityRouter.configuredIdentities().map(({ binding, client }) =>
        this.runEndpointNotificationPump(
          identityRouter,
          binding,
          client,
          signal,
          intervalMs,
        ),
      ),
    );
  }

  async runEndpointNotificationPump(
    identityRouter,
    binding,
    client,
    signal,
    intervalMs,
  ) {
    let idleRounds = 0;
    while (!signal.aborted) {
      const notificationCount = await this.deliverEndpointNotifications(
        identityRouter,
        binding,
        client,
        signal,
      );
      if (signal.aborted) {
        return;
      }
      idleRounds = notificationCount > 0 ? 0 : idleRounds + 1;
      await this.notificationSleep(
        notificationPumpDelay(intervalMs, idleRounds),
        signal,
      );
    }
  }

  async deliverEndpointNotifications(
    identityRouter,
    binding,
    client,
    signal,
  ) {
    let response;
    try {
      response = await client.callTool(
        "agentbridge_host_notification_claim",
        {
          agent_host: "openclaw",
          endpoint_key: binding.key,
          limit: 10,
          lease_seconds: 30,
        },
        { signal, meta: hostContextMeta() },
      );
    } catch (error) {
      if (!signal.aborted) {
        this.api.logger.debug?.(
          `AgentBridge endpoint notification claim unavailable (${safeErrorCode(error)})`,
        );
      }
      return 0;
    }
    const endpoint = response?.endpoint;
    for (const notification of Array.isArray(response?.notifications)
      ? response.notifications
      : []) {
      let delivered = false;
      const sessionKey = endpoint?.conversationRef;
      const route = endpoint?.route;
      await this.restoreWorkspaceInteractionFromNotification(
        notification,
        client,
      );
      if (
        notification?.deliveryMode === "origin_handled" ||
        notification?.deliveryMode === "no_op"
      ) {
        delivered = true;
      } else if (isPullBasedEndpoint(endpoint, route, binding)) {
        // Agent Workspace reads the same Task Hub event stream directly.
        delivered = true;
      } else if (
        notification?.deliveryMode === "status" &&
        (QUIET_COMPANION_TASK_EVENTS.has(notification?.event?.eventType) ||
          isSimpleReadSuccessNotification(notification))
      ) {
        // Keep companion chats focused on actionable cards and terminal results.
        delivered = true;
      } else if (
        isPrivateSessionKey(sessionKey) &&
        route &&
        typeof route === "object" &&
        !Array.isArray(route) &&
        identityRouter.restoreSessionBinding({
          sessionKey,
          bindingKey: binding.key,
        })
      ) {
        this.bindDeliveryRoute({
          sessionKey,
          channel: route.channel || binding.channel,
          to: route.to || binding.senderId,
          accountId: route.accountId || binding.accountId,
          threadId: route.threadId,
        });
        if (
          notification.deliveryMode === "trusted_interaction" &&
          notification.interaction
        ) {
          delivered = await this.deliverInteractionsDirect(
            sessionKey,
            [notification.interaction],
          );
        } else if (
          notification.deliveryMode === "artifact" &&
          notification.artifact
        ) {
          const receipt = await this.deliverPreparedDocumentDirect(
            sessionKey,
            notification.artifact,
            { deliveryRef: notification.deliveryId },
          );
          delivered = receipt.delivered;
          await this.reportPreparedDocumentDelivery({
            sessionKey,
            taskId: notification.task?.taskId,
            deliveryRef: notification.deliveryId,
            mcpClient: client,
            report: preparedDocumentDeliveryReport(
              [notification.artifact],
              [receipt],
            ),
          });
        } else if (
          notification.deliveryMode === "timeline_message" &&
          typeof notification.message === "string" &&
          Array.isArray(notification.attachments) &&
          notification.attachments.length > 0
        ) {
          delivered = await this.deliverTimelineMessageDirect(
            sessionKey,
            notification.message,
            notification.attachments,
          );
        } else if (
          ["status", "timeline_message"].includes(
            notification.deliveryMode,
          ) &&
          typeof notification.message === "string"
        ) {
          delivered = await this.deliverTextDirect(
            sessionKey,
            notification.message,
          );
        }
      }
      try {
        const deferUntilActivity =
          !delivered && isActivityGatedDirectEndpoint(endpoint, route, binding);
        await client.callTool(
          "agentbridge_host_notification_ack",
          {
            agent_host: "openclaw",
            endpoint_key: binding.key,
            delivery_id: notification.deliveryId,
            succeeded: delivered,
            retry_after_seconds: 5,
            defer_until_activity: deferUntilActivity,
          },
          { signal, meta: hostContextMeta() },
        );
      } catch (error) {
        if (!signal.aborted) {
          this.api.logger.warn(
            `AgentBridge endpoint notification acknowledgement failed (${safeErrorCode(error)})`,
          );
        }
      }
    }
    return Array.isArray(response?.notifications)
      ? response.notifications.length
      : 0;
  }

  async restoreWorkspaceInteractionFromNotification(notification, client) {
    if (!notification?.task?.taskId) {
      return false;
    }
    const sessionKey = safeRoutePart(
      notification.task.activeConversationRef,
    );
    if (!isWorkspaceSessionKey(sessionKey)) {
      return false;
    }
    let interaction = notification.interaction || null;
    const eventType = safeRoutePart(notification?.event?.eventType);
    const eventInteractionId = safeRoutePart(
      notification?.event?.payload?.interactionId,
    );
    if (
      !interaction &&
      eventInteractionId &&
      ["task.interaction.waiting", "task.interaction.completed"].includes(
        eventType,
      )
    ) {
      try {
        const response = await client.callTool(
          "agentbridge_interaction_get",
          { interaction_id: eventInteractionId },
          { meta: hostContextMeta() },
        );
        const processed = processToolResult(
          response,
          this.config.allowedCardOrigins,
        );
        interaction = processed.interactions.find(
          (item) => item.interactionId === eventInteractionId,
        );
      } catch (error) {
        this.api.logger.warn(
          `AgentBridge Workspace interaction notification recovery failed: ${safeErrorCode(error)}`,
        );
        return false;
      }
    }
    const resumableCompleted = Boolean(
      interaction?.state === "completed" &&
        interaction.resume?.ready === true &&
        interaction.resume?.completed !== true,
    );
    if (
      !interaction ||
      (!resumableCompleted &&
        !["pending", "processing"].includes(interaction.state))
    ) {
      return false;
    }
    this.bindDeliveryRoute({
      sessionKey,
      channel: "webchat",
      to: notification.task.originEndpointId || sessionKey,
      accountId: null,
      threadId: null,
    });
    const restored = await this.restoreRecoveredInteraction({
      taskId: notification.task.taskId,
      interaction,
      sessionKey,
      mcpClient: client,
    });
    if (restored) {
      this.api.logger.info(
        "AgentBridge restored Workspace interaction polling from the companion endpoint notification",
      );
    }
    return restored;
  }

  async waitForIdle() {
    await Promise.allSettled([
      ...this.polls.values(),
      ...(this.timelinePublisher
        ? [this.timelinePublisher.waitForIdle()]
        : []),
    ]);
  }

  upsert({
    interaction,
    sessionKey,
    runId,
    readContinuation = null,
    taskId = null,
  }) {
    const existing = this.records.get(interaction.interactionId);
    if (existing) {
      existing.interaction = interaction;
      existing.sessionKey = sessionKey || existing.sessionKey;
      existing.runId = runId || existing.runId;
      existing.taskId ||= taskId;
      existing.readContinuation ||= readContinuation;
      existing.mcpClient ||= this.clientForSession(existing.sessionKey);
      return existing;
    }
    const record = {
      interaction,
      sessionKey,
      runId,
      taskId,
      readContinuation,
      mcpClient: this.clientForSession(sessionKey),
      delivered: false,
      continuationQueued: false,
      resumeStarted: false,
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
    if (record.resumeStarted) {
      return false;
    }
    record.resumeStarted = true;
    let response;
    try {
      response = await record.mcpClient.callTool(
        "agentbridge_interaction_resume",
        {
          interaction_id: record.interaction.interactionId,
          idempotency_key: `openclaw:${record.interaction.interactionId}`,
        },
        {
          signal,
          meta: record.taskId
            ? {
                [TASK_CONTEXT_META_KEY]: {
                  taskId: record.taskId,
                },
              }
            : undefined,
        },
      );
    } catch (error) {
      record.resumeStarted = false;
      await this.notify(record, "resume_failed", safeErrorCode(error));
      return false;
    }

    const processed = processToolResult(
      response,
      this.config.allowedCardOrigins,
    );
    let nextInteractions = processed.interactions.filter(
      (item) => item.interactionId !== record.interaction.interactionId,
    );
    nextInteractions =
      (await this.presentInteractions(
        nextInteractions,
        record.sessionKey,
      )) || nextInteractions;
    for (const interaction of nextInteractions) {
      const next = this.upsert({
        interaction,
        sessionKey: record.sessionKey,
        runId: null,
        taskId: record.taskId,
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
    return true;
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
        {
          meta: record.taskId
            ? {
                [TASK_CONTEXT_META_KEY]: {
                  taskId: record.taskId,
                },
              }
            : undefined,
        },
      );
      await this.observeTaskResponse(record, response);
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

  async observeTaskResponse(record, response) {
    if (!record.taskId || !record.mcpClient) {
      return;
    }
    const references = collectTaskReferences(response);
    if (
      references.operationIds.length === 0 &&
      references.interactionIds.length === 0
    ) {
      return;
    }
    try {
      await record.mcpClient.callTool(
        "agentbridge_host_task_observe",
        {
          agent_host: "openclaw",
          task_id: record.taskId,
          operation_ids: references.operationIds,
          interaction_ids: references.interactionIds,
        },
        { meta: hostContextMeta() },
      );
    } catch (error) {
      this.api.logger.warn(
        `AgentBridge continued task observation failed: ${safeErrorCode(error)}`,
      );
    }
  }

  async materializePreparedDocument(file) {
    if (typeof this.fetchImpl !== "function") {
      throw new Error("prepared document fetch is unavailable");
    }
    const response = await this.fetchImpl(file.mediaUrl, {
      headers: { Accept: file.contentType },
      signal: AbortSignal.timeout(45_000),
    });
    if (!response?.ok) {
      throw new Error(`prepared document fetch failed: HTTP ${response?.status || 0}`);
    }
    const declaredSize = Number(response.headers?.get?.("content-length") || 0);
    const maximumSize = 25 * 1024 * 1024;
    if (declaredSize > maximumSize) {
      throw new Error("prepared document exceeds the media size limit");
    }
    const body = Buffer.from(await response.arrayBuffer());
    if (!body.length || body.length > maximumSize) {
      throw new Error("prepared document body is invalid");
    }
    const contentType =
      String(response.headers?.get?.("content-type") || "")
        .split(";", 1)[0]
        .trim()
        .toLowerCase() || file.contentType;
    if (
      ![
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/webp",
        "text/csv",
      ].includes(
        contentType,
      )
    ) {
      throw new Error("prepared document content type is unsupported");
    }
    const saved = await this.saveMediaBufferImpl(
      body,
      contentType,
      "inbound",
      maximumSize,
      file.filename,
      file.filename,
    );
    if (!saved?.path) {
      throw new Error("prepared document was not saved to the OpenClaw media store");
    }
    return saved.path;
  }

  async deliverPreparedDocumentDirect(
    sessionKey,
    file,
    { deliveryRef = null } = {},
  ) {
    this.prunePreparedDocumentReceipts();
    const receiptKey = preparedDocumentReceiptKey(
      sessionKey,
      deliveryRef,
      file,
    );
    const existing = receiptKey
      ? this.documentDeliveryReceipts.get(receiptKey)
      : null;
    if (existing) {
      return existing.promise;
    }
    const promise = this.performPreparedDocumentDelivery(sessionKey, file);
    if (receiptKey) {
      this.documentDeliveryReceipts.set(receiptKey, {
        promise,
        capturedAt: this.now(),
      });
    }
    const receipt = await promise;
    if (receiptKey && !receipt.delivered) {
      this.documentDeliveryReceipts.delete(receiptKey);
    }
    return receipt;
  }

  async performPreparedDocumentDelivery(sessionKey, file) {
    const route = this.sessionRoutes.get(sessionKey);
    if (!route) {
      this.api.logger.warn(
        "AgentBridge prepared document delivery unavailable because the private session route is missing",
      );
      return preparedDocumentReceipt(file, "failed", 0, "ROUTE_MISSING");
    }
    if (this.isPullBasedSession(sessionKey)) {
      this.api.logger.info(
        `AgentBridge prepared document exposed through the pull-based task card (channel=${route.channel}, filename=${safeCode(file.filename)})`,
      );
      return preparedDocumentReceipt(
        file,
        "fallback_link_sent",
        0,
        null,
      );
    }
    const text = `AgentBridge 文件已准备完成：${file.filename}`;
    let localMediaPath = null;
    let errorCode = "ATTACHMENT_DELIVERY_FAILED";
    try {
      localMediaPath = await this.materializePreparedDocument(file);
    } catch (error) {
      errorCode = safeErrorCode(error);
      this.api.logger.warn(
        `AgentBridge prepared document materialization failed: ${errorCode}`,
      );
    }
    let attemptCount = 0;
    while (localMediaPath && attemptCount < PREPARED_DOCUMENT_NATIVE_ATTEMPTS) {
      attemptCount += 1;
      try {
        const delivered = await this.sendRoutePayload(sessionKey, route, {
          text,
          mediaUrl: localMediaPath,
          forceDocument: true,
        });
        if (delivered) {
          this.api.logger.info(
            `AgentBridge prepared document delivered directly (channel=${route.channel}, filename=${safeCode(file.filename)}, attempts=${attemptCount})`,
          );
          return preparedDocumentReceipt(
            file,
            "attachment_sent",
            attemptCount,
            null,
          );
        }
        errorCode = "ATTACHMENT_DELIVERY_UNAVAILABLE";
        break;
      } catch (error) {
        errorCode = safeErrorCode(error);
        const retryable = isRetryablePreparedDocumentDeliveryError(error);
        this.api.logger.warn(
          `AgentBridge prepared document attachment delivery failed (attempt=${attemptCount}/${PREPARED_DOCUMENT_NATIVE_ATTEMPTS}, code=${errorCode})`,
        );
        if (
          !retryable ||
          attemptCount >= PREPARED_DOCUMENT_NATIVE_ATTEMPTS
        ) {
          break;
        }
        await this.sleep(PREPARED_DOCUMENT_RETRY_DELAY_MS);
      }
    }
    const fallback = [
      `OA 证书“${file.filename}”已准备好，但附件上传失败。`,
      "可在链接有效期内直接下载：",
      file.mediaUrl,
    ].join("\n");
    try {
      if (await this.sendRoutePayload(sessionKey, route, { text: fallback })) {
        this.api.logger.info(
          `AgentBridge prepared document delivered as a fallback link (channel=${route.channel}, filename=${safeCode(file.filename)})`,
        );
        return preparedDocumentReceipt(
          file,
          "fallback_link_sent",
          attemptCount,
          errorCode,
        );
      }
    } catch (error) {
      errorCode = safeErrorCode(error);
      this.api.logger.warn(
        `AgentBridge prepared document fallback delivery failed: ${errorCode}`,
      );
    }
    return preparedDocumentReceipt(file, "failed", attemptCount, errorCode);
  }

  async reportPreparedDocumentDelivery({
    sessionKey,
    taskId,
    deliveryRef,
    report,
    mcpClient = null,
  }) {
    if (!taskId || !report?.files?.some((file) => file.artifactId)) {
      return false;
    }
    const deliveryClient = mcpClient || this.clientForSession(sessionKey);
    if (!deliveryClient) {
      return false;
    }
    const channel = this.sessionRoutes.get(sessionKey)?.channel || "unknown";
    try {
      await deliveryClient.callTool(
        "agentbridge_host_artifact_delivery_report",
        {
          agent_host: "openclaw",
          task_id: taskId,
          delivery_ref: deliveryRef,
          channel,
          files: report.files
            .filter((file) => file.artifactId)
            .map((file) => ({
              artifact_id: file.artifactId,
              state: file.state,
              attempt_count: file.attemptCount,
              error_code: file.errorCode,
            })),
        },
        { meta: hostContextMeta() },
      );
      return true;
    } catch (error) {
      this.api.logger.warn(
        `AgentBridge prepared document delivery report failed: ${safeErrorCode(error)}`,
      );
      return false;
    }
  }

  async deliverTimelineMessageDirect(sessionKey, text, attachments) {
    const route = this.sessionRoutes.get(sessionKey);
    if (!route) {
      this.api.logger.warn(
        "AgentBridge timeline media delivery unavailable because the private session route is missing",
      );
      return false;
    }
    const files = normalizeTimelineAttachments(
      attachments,
      this.config.allowedCardOrigins,
    );
    if (files.length === 0) {
      return this.deliverTextDirect(sessionKey, text);
    }

    let adapter;
    try {
      adapter = await this.api.runtime.channel.outbound.loadAdapter(
        route.channel,
      );
    } catch (error) {
      this.api.logger.warn(
        `AgentBridge timeline media adapter unavailable: ${safeErrorCode(error)}`,
      );
    }
    if (!adapter?.sendPayload) {
      return this.deliverTimelineAttachmentLinks(sessionKey, text, files);
    }

    let materialized;
    try {
      materialized = await Promise.all(
        files.map(async (file) => ({
          ...file,
          localMediaPath: await this.materializePreparedDocument(file),
        })),
      );
    } catch (error) {
      this.api.logger.warn(
        `AgentBridge timeline media materialization failed: ${safeErrorCode(error)}`,
      );
      return this.deliverTimelineAttachmentLinks(sessionKey, text, files);
    }

    for (const [index, file] of materialized.entries()) {
      try {
        const delivered = await this.sendRoutePayload(sessionKey, route, {
          text:
            index === 0
              ? text
              : `附加图片 ${index + 1}/${materialized.length}：${file.filename}`,
          mediaUrl: file.localMediaPath,
        });
        if (delivered) {
          continue;
        }
      } catch (error) {
        this.api.logger.warn(
          `AgentBridge timeline media upload failed: ${safeErrorCode(error)}`,
        );
      }
      return this.deliverTimelineAttachmentLinks(sessionKey, text, files);
    }
    this.api.logger.info(
      `AgentBridge timeline media delivered directly (channel=${route.channel}, count=${materialized.length})`,
    );
    return true;
  }

  async deliverTimelineAttachmentLinks(sessionKey, text, files) {
    const fallback = [
      text,
      "附加图片：",
      ...files.map(
        (file, index) =>
          `${index + 1}. ${file.filename}\n${file.mediaUrl}`,
      ),
    ].join("\n");
    try {
      return await this.deliverTextDirect(sessionKey, fallback);
    } catch (error) {
      this.api.logger.warn(
        `AgentBridge timeline media fallback failed: ${safeErrorCode(error)}`,
      );
      return false;
    }
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
    if (this.isPullBasedSession(sessionKey)) {
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
        `AgentBridge trusted card is available through the pull-based task stream (channel=${route.channel}, count=${interactions.length})`,
      );
      return true;
    }
    try {
      const presentation = buildPresentation(interactions, route.channel);
      if (!presentation) {
        return false;
      }
      const text = "请处理下面的 AgentBridge 安全卡片。";
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
    if (this.isPullBasedSession(sessionKey)) {
      this.api.logger.info(
        `AgentBridge trusted status is available through the pull-based task stream (channel=${route.channel}, status=${safeCode(status)})`,
      );
      return true;
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
    this.prunePreparedDocumentReceipts();
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
    for (const [sessionKey, continuation] of this.taskContinuations) {
      if (continuation.expiresAt <= this.now()) {
        this.taskContinuations.delete(sessionKey);
      }
    }
    for (const [sessionKey, choice] of this.taskContinuationChoices) {
      if (choice.expiresAt <= this.now()) {
        this.taskContinuationChoices.delete(sessionKey);
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

  prunePreparedDocumentReceipts() {
    const cutoff = this.now() - PREPARED_DOCUMENT_RECEIPT_TTL_MS;
    for (const [key, receipt] of this.documentDeliveryReceipts) {
      if (receipt.capturedAt <= cutoff) {
        this.documentDeliveryReceipts.delete(key);
      }
    }
    while (
      this.documentDeliveryReceipts.size > MAX_PREPARED_DOCUMENT_RECEIPTS
    ) {
      const oldest = this.documentDeliveryReceipts.keys().next().value;
      if (!oldest) {
        break;
      }
      this.documentDeliveryReceipts.delete(oldest);
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

function normalizeSmartlightAlarmListArguments(params, message) {
  const normalized =
    params && typeof params === "object" && !Array.isArray(params)
      ? { ...params }
      : {};
  const text = safeMessageText(message, 1000);
  if (!text) {
    return normalized;
  }
  const explicitActivity =
    /last_activity/iu.test(text) ||
    /(?:最近|最新|最后|末次).{0,8}(?:活动|变化|更新|处理)/u.test(text) ||
    /(?:活动|变化|更新|处理).{0,8}(?:时间|最近|最新|最后|末次)/u.test(text);
  if (explicitActivity) {
    normalized.sort_by = "last_activity";
    return normalized;
  }
  const explicitOccurrence =
    /occurred_at/iu.test(text) ||
    /(?:首次|最近|最新).{0,6}发生/u.test(text) ||
    /发生时间/u.test(text);
  const genericLatest =
    /(?:最近|最新).{0,8}(?:RTU)?告警/u.test(text) ||
    /(?:最近|最新).{0,6}(?:一|1)条/u.test(text);
  if (explicitOccurrence || genericLatest) {
    normalized.sort_by = "occurred_at";
  }
  return normalized;
}

function isSimpleReadSuccessNotification(notification) {
  return (
    notification?.event?.eventType === "task.operation.succeeded" &&
    notification?.event?.payload?.capabilityEffect === "read"
  );
}

function safeMessageText(value, maximum) {
  if (typeof value !== "string" && typeof value !== "number") {
    return null;
  }
  const normalized = String(value).trim();
  return normalized ? normalized.slice(0, maximum) : null;
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
  const structuredContent =
    result?.structuredContent ?? details?.structuredContent;
  if (
    !details ||
    typeof details !== "object" ||
    Array.isArray(details) ||
    details.mcpServer !== serverName ||
    typeof details.mcpTool !== "string" ||
    !details.mcpTool.trim() ||
    !structuredContent ||
    typeof structuredContent !== "object" ||
    Array.isArray(structuredContent)
  ) {
    return null;
  }
  return structuredContent;
}

function taskIdFromToolResult(result) {
  const taskId = result?.details?.agentbridgeTaskId;
  if (typeof taskId !== "string") {
    return null;
  }
  const normalized = taskId.trim();
  return normalized.length >= 16 && normalized.length <= 128
    ? normalized
    : null;
}

async function saveOpenClawMediaBuffer(
  buffer,
  contentType,
  _subdir,
  maximumSize,
  originalFilename,
) {
  if (!Buffer.isBuffer(buffer) || !buffer.length || buffer.length > maximumSize) {
    throw new Error("prepared document media buffer is invalid");
  }
  const stateDir = resolveOpenClawStateDir();
  const inboundDir = path.join(stateDir, "media", "inbound");
  await mkdir(inboundDir, { recursive: true, mode: 0o700 });
  const rawStem = path.basename(String(originalFilename || "agentbridge-file"), path.extname(String(originalFilename || "")));
  const stem = rawStem
    .replace(/[^\p{L}\p{N}._-]+/gu, "_")
    .replace(/_+/g, "_")
    .replace(/^_|_$/g, "")
    .slice(0, 60) || "agentbridge-file";
  const extension = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "text/csv": ".csv",
  }[contentType];
  if (!extension) {
    throw new Error("prepared document content type is unsupported");
  }
  const id = `${stem}---${randomUUID()}${extension}`;
  const finalPath = path.join(inboundDir, id);
  const temporaryPath = `${finalPath}.tmp`;
  try {
    await writeFile(temporaryPath, buffer, { flag: "wx", mode: 0o600 });
    await rename(temporaryPath, finalPath);
  } catch (error) {
    await unlink(temporaryPath).catch(() => undefined);
    throw error;
  }
  return { id, path: finalPath, size: buffer.length, contentType };
}

function resolveOpenClawStateDir() {
  const home = process.env.OPENCLAW_HOME?.trim() || os.homedir();
  const expand = (value) =>
    value.startsWith("~") ? path.join(home, value.slice(1).replace(/^[\\/]+/, "")) : value;
  const stateOverride = process.env.OPENCLAW_STATE_DIR?.trim();
  if (stateOverride) {
    return path.resolve(expand(stateOverride));
  }
  const configPath = process.env.OPENCLAW_CONFIG_PATH?.trim();
  if (configPath) {
    return path.dirname(path.resolve(expand(configPath)));
  }
  return path.join(home, ".openclaw");
}

function normalizeTimelineAttachments(values, allowedOrigins) {
  if (!Array.isArray(values)) {
    return [];
  }
  return values
    .slice(0, 4)
    .map((value) => normalizeTimelineAttachment(value, allowedOrigins))
    .filter(Boolean)
    .sort((left, right) => left.ordinal - right.ordinal);
}

function normalizeTimelineAttachment(value, allowedOrigins) {
  if (!value || typeof value !== "object") {
    return null;
  }
  const filename = String(value.fileName || "").trim();
  const mediaUrl = String(value.mediaUrl || "").trim();
  if (!filename || !mediaUrl || value.type !== "image") {
    return null;
  }
  try {
    const parsed = new URL(mediaUrl);
    const trustedOrigins = new Set(allowedOrigins || []);
    if (
      !["http:", "https:"].includes(parsed.protocol) ||
      !trustedOrigins.has(parsed.origin) ||
      !/^\/media\/[A-Za-z0-9_-]{32,128}\/file$/.test(parsed.pathname) ||
      parsed.username ||
      parsed.password ||
      parsed.search ||
      parsed.hash
    ) {
      return null;
    }
  } catch {
    return null;
  }
  const contentType = String(value.mimeType || "").trim().toLowerCase();
  if (!["image/jpeg", "image/png", "image/webp"].includes(contentType)) {
    return null;
  }
  return {
    filename: filename.slice(0, 120),
    mediaUrl,
    contentType,
    ordinal: Number.isFinite(Number(value.ordinal))
      ? Number(value.ordinal)
      : 0,
  };
}

function normalizePreparedDocuments(payload, allowedOrigins) {
  if (payload?.schemaVersion === "agentbridge.document_delivery.v1") {
    const file = normalizePreparedDocumentFile(payload.file, allowedOrigins);
    return payload.status === "succeeded" && file ? [file] : [];
  }
  if (
    payload?.schemaVersion !== "agentbridge.document_delivery_batch.v1" ||
    !["succeeded", "partial"].includes(payload.status) ||
    !Array.isArray(payload.files)
  ) {
    return [];
  }
  return payload.files
    .slice(0, 20)
    .map((file) => normalizePreparedDocumentFile(file, allowedOrigins))
    .filter(Boolean);
}

function normalizePreparedDocumentFile(file, allowedOrigins) {
  if (!file || typeof file !== "object") {
    return null;
  }
  const filename = String(file.filename || "").trim();
  const mediaUrl = String(file.mediaUrl || "").trim();
  if (!filename || !mediaUrl) {
    return null;
  }
  try {
    const parsed = new URL(mediaUrl);
    const trustedOrigins = new Set(allowedOrigins || []);
    if (
      !["http:", "https:"].includes(parsed.protocol) ||
      !trustedOrigins.has(parsed.origin) ||
      !/^\/download\/[A-Za-z0-9_-]{32,128}\/file$/.test(parsed.pathname) ||
      parsed.username ||
      parsed.password ||
      parsed.search ||
      parsed.hash
    ) {
      return null;
    }
  } catch {
    return null;
  }
  const contentType = String(file.contentType || "").trim().toLowerCase();
  if (
    !["application/pdf", "image/jpeg", "image/png", "text/csv"].includes(
      contentType,
    )
  ) {
    return null;
  }
  return {
    filename: filename.slice(0, 240),
    mediaUrl,
    contentType,
    artifactId: normalizeOpaqueId(file.artifactId),
    downloadId: normalizeOpaqueId(file.downloadId),
  };
}

function extractUserMessageText(event) {
  if (typeof event?.text === "string" && event.text.trim()) {
    return event.text;
  }
  if (typeof event?.content === "string") {
    return event.content;
  }
  if (!Array.isArray(event?.content)) {
    return null;
  }
  const text = event.content
    .map((item) => {
      if (typeof item === "string") {
        return item;
      }
      if (!item || typeof item !== "object") {
        return "";
      }
      if (typeof item.text === "string") {
        return item.text;
      }
      return typeof item.content === "string" ? item.content : "";
    })
    .filter(Boolean)
    .join("\n")
    .trim();
  return text || null;
}

function normalizeOpaqueId(value) {
  const normalized = typeof value === "string" ? value.trim() : "";
  return /^[A-Za-z0-9_-]{16,128}$/.test(normalized) ? normalized : null;
}

function preparedDocumentReceipt(
  file,
  state,
  attemptCount,
  errorCode,
) {
  return {
    artifactId: normalizeOpaqueId(file?.artifactId),
    downloadId: normalizeOpaqueId(file?.downloadId),
    filename: String(file?.filename || "").slice(0, 240),
    state,
    delivered: ["attachment_sent", "fallback_link_sent"].includes(state),
    attemptCount: Math.max(0, Number(attemptCount) || 0),
    errorCode: errorCode ? safeCode(errorCode) : null,
  };
}

function preparedDocumentDeliveryReport(files, receipts, override = {}) {
  const outcomes = files.map(
    (file, index) =>
      receipts[index] ||
      preparedDocumentReceipt(
        file,
        override.state || "failed",
        0,
        override.errorCode || "DELIVERY_RESULT_MISSING",
      ),
  );
  const attachmentSentCount = outcomes.filter(
    (file) => file.state === "attachment_sent",
  ).length;
  const fallbackLinkSentCount = outcomes.filter(
    (file) => file.state === "fallback_link_sent",
  ).length;
  const failedCount = outcomes.filter((file) => file.state === "failed").length;
  const deliveredCount = attachmentSentCount + fallbackLinkSentCount;
  const state =
    failedCount === 0
      ? "delivered"
      : deliveredCount > 0
        ? "partial"
        : "failed";
  const endpointMode = override.endpointMode || "native_channel";
  const parts = [`${outcomes.length} 份文件已准备`];
  if (endpointMode === "workspace_download") {
    parts.push(`当前网页任务卡已生成 ${fallbackLinkSentCount} 个下载入口`);
  } else {
    if (attachmentSentCount > 0) {
      parts.push(`${attachmentSentCount} 份已作为附件发送`);
    }
    if (fallbackLinkSentCount > 0) {
      parts.push(`${fallbackLinkSentCount} 份附件上传失败，已改发下载链接`);
    }
  }
  if (failedCount > 0) parts.push(`${failedCount} 份未能送达`);
  return {
    mode: outcomes.length === 1 ? "direct_attachment" : "direct_attachment_batch",
    oneFilePerMessage: true,
    handledByHost: true,
    endpointMode,
    channel: override.channel || "unknown",
    state,
    completionMeaning: "endpoint_delivery_reported",
    preparedCount: outcomes.length,
    attachmentSentCount,
    fallbackLinkSentCount,
    failedCount,
    files: outcomes,
    userMessage: `${parts.join("，")}。`,
  };
}

function preparedDocumentReceiptKey(sessionKey, deliveryRef, file) {
  const normalizedRef = safeRoutePart(deliveryRef);
  const fileRef =
    normalizeOpaqueId(file?.artifactId) ||
    normalizeOpaqueId(file?.downloadId);
  if (!normalizedRef || !fileRef) {
    return null;
  }
  return `${sessionKey}\u0000${normalizedRef}\u0000${fileRef}`;
}

function isRetryablePreparedDocumentDeliveryError(error) {
  const evidence = [error?.code, error?.name, error?.message]
    .filter(Boolean)
    .join(" ")
    .toUpperCase();
  return [
    "HTTPERROR",
    "FETCH FAILED",
    "NETWORK",
    "TIMEOUT",
    "ETIMEDOUT",
    "ECONNRESET",
    "ECONNREFUSED",
    "EHOSTUNREACH",
    "ENETUNREACH",
    "SOCKET",
    "429",
    "HTTP 5",
  ].some((token) => evidence.includes(token));
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
    descriptor.kind === "taihua_project" ||
    normalizedToolName === "yuque_document_search"
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
  if (descriptor.kind.startsWith("yuque_")) {
    for (const name of ["book", "query", "document"]) {
      if (typeof source[name] === "string" && source[name].trim()) {
        arguments_[name] = source[name].trim().slice(0, 500);
      }
    }
    if (
      Number.isInteger(source.page) &&
      source.page >= 1 &&
      source.page <= 1000
    ) {
      arguments_.page = source.page;
    }
    if (
      Number.isInteger(source.max_chars) &&
      source.max_chars >= 500 &&
      source.max_chars <= 50000
    ) {
      arguments_.max_chars = source.max_chars;
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
  if (
    ["taihua_work_log_team_list", "taihua_work_log_my_list"].includes(
      matched[1],
    ) &&
    /(?:填写|填报|新建|创建|记录|修改|更新|保存|提交)[^。！？\n]{0,20}(?:工作)?日志|(?:写|填)[^。！？\n]{0,12}(?:工作)?日志/u.test(
      value,
    )
  ) {
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
  if (descriptor.kind === "yuque_document") {
    const title = safeDisplayText(result?.document?.title, 300) || "(未命名文档)";
    const book = safeDisplayText(result?.document?.book?.name, 160);
    const content = safeDisplayText(result?.content, 3200) || "(正文为空)";
    const suffix = result?.truncated === true ? "\n\n正文已按安全上限截断。" : "";
    return [
      `${descriptor.system} 登录已恢复，已自动继续读取文档：${title}`,
      book ? `知识库：${book}` : "",
      content + suffix,
    ]
      .filter(Boolean)
      .join("\n\n");
  }
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
  if (descriptor.kind === "yuque_book") {
    const name = safeDisplayText(item?.name, 300) || "(未命名知识库)";
    const count = Number.isInteger(item?.documentCount)
      ? `${item.documentCount} 篇`
      : "";
    const description = safeDisplayText(item?.description, 500);
    return [
      `${index}. ${name}`,
      count ? `   ${count}` : "",
      description ? `   ${description}` : "",
    ]
      .filter(Boolean)
      .join("\n");
  }
  if (descriptor.kind === "yuque_document_list") {
    const title = safeDisplayText(item?.title, 300) || "(未命名文档)";
    const type = safeDisplayText(item?.type, 80);
    const book = safeDisplayText(item?.book?.name, 160);
    const slug = safeDisplayText(item?.slug, 160);
    return [
      `${index}. ${title}`,
      [book, type].filter(Boolean).join(" | ")
        ? `   ${[book, type].filter(Boolean).join(" | ")}`
        : "",
      slug ? `   文档标识：${slug}` : "",
    ]
      .filter(Boolean)
      .join("\n");
  }
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
  if (
    result.batch &&
    typeof result.batch === "object" &&
    result.batch.state === "succeeded"
  ) {
    const total = Number(result.batch.totalCount) || 0;
    return `OA 补签申请已全部处理完成，共 ${total} 条。`;
  }
  if (result.meeting_created === true && result.meeting_sent === true) {
    return "OA 会议已创建并发送。";
  }
  if (
    result.business_intent === "submit_business_trip_request" &&
    result.workflow_submitted === true
  ) {
    return "OA 出差申请已提交审批。";
  }
  if (
    result.business_intent === "submit_leave_request" &&
    result.workflow_submitted === true
  ) {
    return "OA 请假申请已提交审批。";
  }
  if (
    result.business_intent === "revoke_sent_workflow" &&
    result.workflow_revoked === true
  ) {
    return "OA 已发流程已撤销。";
  }
  if (result.pending_action_processed === true) {
    const subjects = {
      efficiency_data: "OA \u6548\u80fd\u6570\u636e\u6d41\u7a0b",
      travel_expense: "OA \u5dee\u65c5\u8d39\u5ba1\u6279\u62a5\u9500\u5355",
      labor_contract_renewal: "OA \u52b3\u52a8\u5408\u540c\u7eed\u7b7e\u8868",
      intellectual_property_declaration: "OA \u77e5\u8bc6\u4ea7\u6743\u7533\u62a5\u5ba1\u6279\u5355",
      overtime: "OA \u52a0\u73ed\u7533\u8bf7\u5ba1\u6838\u5355",
      resignation: "OA \u79bb\u804c\u7533\u8bf7\u5355",
      attendance_confirmation: "OA \u6708\u5ea6\u8003\u52e4\u786e\u8ba4\u5355",
      weekly_report: "OA \u5468\u62a5\u53d1\u9001\u6d41\u7a0b",
      standard_collaboration: "OA \u666e\u901a\u534f\u540c\u4e8b\u9879",
    };
    const subject = subjects[result.workflow_profile] || "OA \u5f85\u529e\u4e8b\u9879";
    const action =
      result.action_kind === "acknowledgement"
        ? "\u5df2\u9605\u529e"
        : result.action_kind === "confirmation"
          ? "\u5df2\u786e\u8ba4\u5e76\u63d0\u4ea4"
          : "\u5df2\u5ba1\u6279\u901a\u8fc7";
    return `${subject}${action}\u3002`;
  }
  if (
    result.status === "created" &&
    result.workLog &&
    typeof result.workLog === "object"
  ) {
    return "泰华工作日志已提交。";
  }
  if (
    result.status === "updated" &&
    result.alarm &&
    typeof result.alarm === "object"
  ) {
    return "照明 RTU 告警备注已修改并回读确认。";
  }
  if (result.action === "submit_work_area") {
    return result.status === "already_completed"
      ? "照明 RTU 告警已经提交工区，本次未重复写入。"
      : "照明 RTU 告警已提交工区并回读确认。";
  }
  if (result.action === "revoke_work_area") {
    return result.status === "already_completed"
      ? "照明 RTU 告警当前未提交工区，本次未重复写入。"
      : "照明 RTU 告警工区提交已撤回并回读确认。";
  }
  if (result.action === "dispose") {
    return result.status === "already_completed"
      ? "照明 RTU 告警已经处置，本次未重复写入。"
      : "照明 RTU 告警已标记为已处置并回读确认。";
  }
  if (result.draft_saved === true && result.workflow_submitted === false) {
    return "OA 待发草稿已保存，未提交审批。";
  }
  if (result.workflow_approved === true) {
    return "OA 补签申请已审批通过。";
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
        [
          "OA_BUSINESS_RULE_REJECTED",
          "TAIHUA_BUSINESS_RULE_REJECTED",
          "SMARTLIGHT_BUSINESS_RULE_REJECTED",
        ].includes(
          safeCode(errorCode),
        )
      ) {
        const reason = safeBusinessRuleMessage(response);
        const normalizedCode = safeCode(errorCode);
        const taihua = normalizedCode === "TAIHUA_BUSINESS_RULE_REJECTED";
        const smartlight = normalizedCode === "SMARTLIGHT_BUSINESS_RULE_REJECTED";
        const system = smartlight ? "照明系统" : taihua ? "泰华日志系统" : "OA";
        const action = smartlight ? "RTU 告警操作" : taihua ? "工作日志" : "申请";
        return reason
          ? `${system} 未提交本次${action}：${reason}${code}。`
          : `${system} 根据业务规则拒绝了本次${action}${code}。`;
      }
      return `AgentBridge 未能完成本次安全操作${code}。`;
    default:
      return `AgentBridge 安全交互状态已更新：${safeCode(status)}${code}。`;
  }
}

function isPullBasedEndpoint(endpoint, route, binding) {
  const clientType = String(endpoint?.clientType || "").trim().toLowerCase();
  const channel = String(
    route?.channel || binding?.channel || "",
  ).trim().toLowerCase();
  return clientType === "web" || PULL_BASED_CHANNELS.has(channel);
}

function isActivityGatedDirectEndpoint(endpoint, route, binding) {
  const clientType = String(endpoint?.clientType || "").trim().toLowerCase();
  const channel = String(
    route?.channel || binding?.channel || "",
  ).trim().toLowerCase();
  return [clientType, channel].some((value) =>
    ["openclaw-weixin", "wechat", "weixin"].includes(value),
  );
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

function isWorkspaceSessionKey(sessionKey) {
  return Boolean(
    typeof sessionKey === "string" &&
      /^agent:[^:]+:agentbridge-workspace:direct:/i.test(sessionKey.trim()),
  );
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

function backgroundSleep(milliseconds, signal) {
  return new Promise((resolve) => {
    if (signal?.aborted) {
      resolve();
      return;
    }
    const timer = setTimeout(resolve, milliseconds);
    timer.unref?.();
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

export function notificationPumpDelay(
  intervalMs,
  idleRounds,
  maximumMs = MAX_NOTIFICATION_IDLE_INTERVAL_MS,
) {
  const base = Math.max(250, Number(intervalMs) || 0);
  const maximum = Math.max(base, Number(maximumMs) || base);
  const exponent = Math.min(Math.max(Number(idleRounds) - 1, 0), 8);
  return Math.min(maximum, base * 2 ** exponent);
}
