const DEFAULTS = Object.freeze({
  mcpServerName: "agentbridge",
  autoPoll: true,
  pollIntervalSeconds: 2,
  maxPollSeconds: 900,
  wakeAgentOnComplete: false,
});

export function resolvePluginConfig(raw) {
  const source = isRecord(raw) ? raw : {};
  const allowedCardOrigins = [];
  for (const value of Array.isArray(source.allowedCardOrigins)
    ? source.allowedCardOrigins
    : []) {
    const normalized = normalizeHttpOrigin(value);
    if (normalized && !allowedCardOrigins.includes(normalized)) {
      allowedCardOrigins.push(normalized);
    }
  }

  return Object.freeze({
    allowedCardOrigins: Object.freeze(allowedCardOrigins),
    mcpServerName: boundedString(source.mcpServerName, DEFAULTS.mcpServerName, 100),
    autoPoll: booleanValue(source.autoPoll, DEFAULTS.autoPoll),
    pollIntervalSeconds: boundedInteger(
      source.pollIntervalSeconds,
      DEFAULTS.pollIntervalSeconds,
      1,
      30,
    ),
    maxPollSeconds: boundedInteger(
      source.maxPollSeconds,
      DEFAULTS.maxPollSeconds,
      30,
      1800,
    ),
    wakeAgentOnComplete: booleanValue(
      source.wakeAgentOnComplete,
      DEFAULTS.wakeAgentOnComplete,
    ),
  });
}

export function resolveMcpServer(hostConfig, serverName) {
  if (!isRecord(hostConfig) || !isRecord(hostConfig.mcp)) {
    return null;
  }
  const servers = hostConfig.mcp.servers;
  if (!isRecord(servers) || !isRecord(servers[serverName])) {
    return null;
  }
  const server = servers[serverName];
  const url = boundedString(server.url, "", 2048);
  if (!url) {
    return null;
  }
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    return null;
  }
  return {
    url: parsed.toString(),
    headers: isRecord(server.headers) ? { ...server.headers } : {},
    timeoutSeconds: boundedInteger(server.timeout, 60, 1, 300),
  };
}

export function normalizeHttpOrigin(value) {
  if (typeof value !== "string" || !value.trim()) {
    return null;
  }
  try {
    const parsed = new URL(value.trim());
    if (!['http:', 'https:'].includes(parsed.protocol)) {
      return null;
    }
    if (parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash) {
      return null;
    }
    return parsed.origin;
  } catch {
    return null;
  }
}

function boundedInteger(value, fallback, minimum, maximum) {
  if (!Number.isInteger(value)) {
    return fallback;
  }
  return Math.min(maximum, Math.max(minimum, value));
}

function boundedString(value, fallback, maximumLength) {
  if (typeof value !== "string") {
    return fallback;
  }
  const normalized = value.trim();
  if (!normalized) {
    return fallback;
  }
  return normalized.slice(0, maximumLength);
}

function booleanValue(value, fallback) {
  return typeof value === "boolean" ? value : fallback;
}

export function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
