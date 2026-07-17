import { normalizeHttpOrigin, isRecord } from "./config.js";

export const INTERACTION_SCHEMA_VERSION = "agentbridge.interaction.v1";
export const WITHHELD_URL = "[trusted AgentBridge card URL withheld by OpenClaw host]";
export const SERVER_WITHHELD_URL =
  "[trusted AgentBridge URL withheld from model context]";

const MCP_APP_RESOURCE_URI = "ui://agentbridge/trusted-interaction.html";

const INTERACTION_TYPES = new Set([
  "credential",
  "business_input",
  "execution_authorization",
]);
const INTERACTION_STATES = new Set([
  "pending",
  "processing",
  "completed",
  "declined",
  "expired",
  "failed",
  "superseded",
]);
const BUTTON_LABELS = {
  credential: "安全登录",
  business_input: "填写信息",
  execution_authorization: "核对并确认",
};
const STATE_LABELS = {
  pending: "等待操作",
  processing: "处理中",
  completed: "已完成",
  declined: "已拒绝",
  expired: "已过期",
  failed: "失败",
  superseded: "已替换",
};
const MAX_WALK_DEPTH = 14;
const MAX_WALK_NODES = 10000;
const MAX_JSON_TEXT_LENGTH = 1024 * 1024;
const HISTORICAL_INTERACTION_CONTAINERS = new Set([
  "operation",
  "operations",
]);

export function processToolResult(result, allowedOrigins) {
  const normalizedOrigins = new Set(
    allowedOrigins.map(normalizeHttpOrigin).filter(Boolean),
  );
  const interactions = collectInteractions(result, normalizedOrigins);
  const trustedInteractions = collectInteractions(result, normalizedOrigins, {
    includeHistorical: true,
  });
  if (trustedInteractions.length === 0) {
    return { interactions, result, sanitized: false };
  }
  return {
    interactions,
    result: sanitizeTrustedResult(result, trustedInteractions),
    sanitized: true,
  };
}

export function collectInteractions(value, allowedOrigins, options = {}) {
  const includeHistorical = options.includeHistorical === true;
  const interactions = new Map();
  const visited = new WeakSet();
  let visitedNodes = 0;

  function walk(current, depth) {
    if (depth > MAX_WALK_DEPTH || visitedNodes >= MAX_WALK_NODES) {
      return;
    }
    visitedNodes += 1;

    if (typeof current === "string") {
      const parsed = parseJsonText(current);
      if (parsed !== null) {
        walk(parsed, depth + 1);
      }
      return;
    }
    if (typeof current !== "object" || current === null) {
      return;
    }
    if (visited.has(current)) {
      return;
    }
    visited.add(current);

    const interaction = normalizeInteraction(current, allowedOrigins);
    if (interaction) {
      interactions.set(interaction.interactionId, interaction);
      return;
    }
    const entries = Array.isArray(current)
      ? current.entries()
      : Object.entries(current);
    for (const [key, child] of entries) {
      if (
        !includeHistorical &&
        HISTORICAL_INTERACTION_CONTAINERS.has(String(key))
      ) {
        continue;
      }
      walk(child, depth + 1);
    }
  }

  walk(value, 0);
  return [...interactions.values()];
}

export function collectPublicInteractionReferences(value) {
  const references = new Map();
  const visited = new WeakSet();
  let visitedNodes = 0;

  function walk(current, depth) {
    if (depth > MAX_WALK_DEPTH || visitedNodes >= MAX_WALK_NODES) {
      return;
    }
    visitedNodes += 1;

    if (typeof current === "string") {
      const parsed = parseJsonText(current);
      if (parsed !== null) {
        walk(parsed, depth + 1);
      }
      return;
    }
    if (typeof current !== "object" || current === null || visited.has(current)) {
      return;
    }
    visited.add(current);

    const reference = normalizePublicInteractionReference(current);
    if (reference) {
      references.set(reference.interactionId, reference);
      return;
    }
    const entries = Array.isArray(current)
      ? current.entries()
      : Object.entries(current);
    for (const [key, child] of entries) {
      if (HISTORICAL_INTERACTION_CONTAINERS.has(String(key))) {
        continue;
      }
      walk(child, depth + 1);
    }
  }

  walk(value, 0);
  return [...references.values()];
}

function normalizePublicInteractionReference(value) {
  if (!isRecord(value) || value.schemaVersion !== INTERACTION_SCHEMA_VERSION) {
    return null;
  }
  const interactionId = safeString(value.interactionId, 128);
  const type = safeString(value.type, 64);
  const state = safeString(value.state, 64);
  const presentation = value.presentation;
  if (
    interactionId.length < 16 ||
    !INTERACTION_TYPES.has(type) ||
    !["pending", "processing"].includes(state) ||
    !isRecord(presentation) ||
    presentation.owner !== "agentbridge" ||
    presentation.modelMustNotCollectValues !== true ||
    presentation.hostHandled !== true ||
    presentation.url !== SERVER_WITHHELD_URL ||
    presentation.uiResourceUri !== MCP_APP_RESOURCE_URI
  ) {
    return null;
  }
  return Object.freeze({ interactionId, type, state });
}
export function normalizeInteraction(value, allowedOrigins) {
  if (!isRecord(value) || value.schemaVersion !== INTERACTION_SCHEMA_VERSION) {
    return null;
  }
  const interactionId = safeString(value.interactionId, 128);
  const type = safeString(value.type, 64);
  const state = safeString(value.state, 64);
  if (
    interactionId.length < 16 ||
    !INTERACTION_TYPES.has(type) ||
    !INTERACTION_STATES.has(state) ||
    !isRecord(value.presentation) ||
    value.presentation.owner !== "agentbridge" ||
    value.presentation.modelMustNotCollectValues !== true
  ) {
    return null;
  }

  const rawUrl = safeString(value.presentation.url, 4096);
  let cardUrl = null;
  if (rawUrl) {
    try {
      const parsed = new URL(rawUrl);
      if (
        ['http:', 'https:'].includes(parsed.protocol) &&
        !parsed.username &&
        !parsed.password &&
        !parsed.hash &&
        allowedOrigins.has(parsed.origin)
      ) {
        cardUrl = parsed.toString();
      }
    } catch {
      cardUrl = null;
    }
  }
  if (["pending", "processing"].includes(state) && !cardUrl) {
    return null;
  }

  const display = isRecord(value.display) ? { ...value.display } : {};
  const poll = isRecord(value.poll) ? { ...value.poll } : {};
  const resume = isRecord(value.resume) ? { ...value.resume } : {};
  return Object.freeze({
    schemaVersion: INTERACTION_SCHEMA_VERSION,
    interactionId,
    type,
    state,
    title: safeString(value.title, 200) || "AgentBridge",
    message: safeString(value.message, 1000),
    operationId: safeString(value.operationId, 128) || null,
    presentation: Object.freeze({
      owner: "agentbridge",
      url: cardUrl,
      modelMustNotCollectValues: true,
    }),
    display: Object.freeze(display),
    expiresAt: safeString(value.expiresAt, 80) || null,
    poll: Object.freeze(poll),
    resume: Object.freeze(resume),
  });
}

export function sanitizeTrustedResult(result, interactions) {
  const byId = new Map(interactions.map((item) => [item.interactionId, item]));
  const urls = new Set(
    interactions.map((item) => item.presentation.url).filter(Boolean),
  );
  const seen = new WeakMap();

  function sanitize(value) {
    if (typeof value === "string") {

      let text = value;
      for (const url of urls) {
        text = text.split(url).join(WITHHELD_URL);
      }
      return text;
    }
    if (typeof value !== "object" || value === null) {
      return value;
    }
    if (seen.has(value)) {
      return seen.get(value);
    }
    const clone = Array.isArray(value) ? [] : {};
    seen.set(value, clone);
    for (const [key, child] of Object.entries(value)) {
      clone[key] = sanitize(child);
    }
    if (
      !Array.isArray(value) &&
      value.schemaVersion === INTERACTION_SCHEMA_VERSION &&
      byId.has(value.interactionId) &&
      isRecord(clone.presentation)
    ) {
      clone.presentation.url = WITHHELD_URL;
      clone.presentation.hostHandled = true;
    }
    return clone;
  }

  return sanitize(result);
}

export function isPrivateSessionKey(sessionKey) {
  if (typeof sessionKey !== "string" || !sessionKey.trim()) {
    return false;
  }
  const normalized = sessionKey.trim().toLowerCase();
  if (/:(group|channel|room):/.test(normalized)) {
    return false;
  }
  if (/^agent:[^:]+:main(?:$|:thread:)/.test(normalized)) {
    return true;
  }
  return /:(direct|dm):/.test(normalized);
}

export function isInteractionExpired(interaction, now = Date.now()) {
  if (!interaction.expiresAt) {
    return false;
  }
  const expiresAt = Date.parse(interaction.expiresAt);
  return Number.isFinite(expiresAt) && expiresAt <= now;
}

export function buildPresentation(interactions, channel) {
  const active = interactions.filter((item) => !isInteractionExpired(item));
  if (active.length === 0) {
    return null;
  }
  const blocks = [];
  for (const [index, interaction] of active.entries()) {
    if (index > 0) {
      blocks.push({ type: "divider" });
    }
    if (interaction.message) {
      blocks.push({ type: "text", text: interaction.message });
    }
    blocks.push({ type: "context", text: contextText(interaction) });
    if (["pending", "processing"].includes(interaction.state)) {
      const url = interaction.presentation.url;
      const button = {
        label: BUTTON_LABELS[interaction.type],
        priority: 100,
        style: "primary",
      };
      if (channel === "telegram" && url.startsWith("https://")) {
        button.webApp = { url };
      } else {
        button.url = url;
      }
      blocks.push({ type: "buttons", buttons: [button] });
    }
  }
  return {
    title: active.length === 1 ? active[0].title : "AgentBridge 需要你的操作",
    tone: presentationTone(active),
    blocks,
  };
}

export function mergePresentations(existing, agentbridge) {
  if (!existing || !Array.isArray(existing.blocks)) {
    return agentbridge;
  }
  return {
    ...existing,
    blocks: [
      ...existing.blocks,
      { type: "divider" },
      ...agentbridge.blocks,
    ],
  };
}

function contextText(interaction) {
  const parts = ["AgentBridge 可信交互"];
  if (typeof interaction.display.systemName === "string") {
    parts.push(interaction.display.systemName.slice(0, 100));
  }
  if (Number.isInteger(interaction.display.fieldCount)) {
    parts.push(`${interaction.display.fieldCount} 个字段`);
  }
  parts.push(`状态：${STATE_LABELS[interaction.state] || interaction.state}`);
  return parts.join(" · ");
}

function presentationTone(interactions) {
  const states = new Set(interactions.map((item) => item.state));
  if (["failed", "expired"].some((state) => states.has(state))) {
    return "danger";
  }
  if (["pending", "processing"].some((state) => states.has(state))) {
    return "warning";
  }
  if (states.has("completed")) {
    return "success";
  }
  return "neutral";
}

function parseJsonText(value) {
  const text = value.trim();
  if (
    text.length === 0 ||
    text.length > MAX_JSON_TEXT_LENGTH ||
    !['{', '['].includes(text[0])
  ) {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function safeString(value, maximumLength) {
  return typeof value === "string" ? value.trim().slice(0, maximumLength) : "";
}
