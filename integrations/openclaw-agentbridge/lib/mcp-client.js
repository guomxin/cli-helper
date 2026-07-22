import { randomUUID } from "node:crypto";

import { resolveMcpServer } from "./config.js";

export class McpCallError extends Error {
  constructor(code, message = code) {
    super(message);
    this.name = "McpCallError";
    this.code = code;
  }
}

export function createAgentBridgeMcpClient({
  hostConfig,
  serverName,
  endpoint = null,
  tokenEnv = null,
  fetchImpl = globalThis.fetch,
  env = process.env,
}) {
  const connection = resolveConnection({
    hostConfig,
    serverName,
    endpoint,
    tokenEnv,
    env,
  });
  if (!connection || typeof fetchImpl !== "function") {
    return null;
  }

  return Object.freeze({
    async listTools({ signal } = {}) {
      const result = await request("tools/list", {}, { signal });
      return Array.isArray(result?.tools) ? result.tools : [];
    },
    async callToolResult(name, arguments_, { signal } = {}) {
      return request(
        "tools/call",
        { name, arguments: arguments_ },
        { signal },
      );
    },
    async callTool(name, arguments_, { signal } = {}) {
      const result = await request(
        "tools/call",
        { name, arguments: arguments_ },
        { signal },
      );
      return extractToolPayload(result);
    },
  });

  async function request(method, params, { signal } = {}) {
    const timeoutSignal = AbortSignal.timeout(
      connection.timeoutSeconds * 1000,
    );
    const requestSignal = signal
      ? AbortSignal.any([signal, timeoutSignal])
      : timeoutSignal;
    let response;
    try {
      response = await fetchImpl(connection.url, {
        method: "POST",
        headers: {
          Authorization: connection.authorization,
          Accept: "application/json, text/event-stream",
          "Content-Type": "application/json",
          "MCP-Protocol-Version": "2025-06-18",
        },
        body: JSON.stringify({
          jsonrpc: "2.0",
          id: randomUUID(),
          method,
          params,
        }),
        signal: requestSignal,
      });
    } catch (error) {
      if (requestSignal.aborted) {
        throw new McpCallError("MCP_TIMEOUT", "AgentBridge MCP request timed out");
      }
      throw new McpCallError("MCP_UNREACHABLE", "AgentBridge MCP is unreachable");
    }
    if (!response.ok) {
      throw new McpCallError(`MCP_HTTP_${response.status}`);
    }
    const rpc = parseMcpResponse(await response.text());
    if (rpc.error) {
      throw new McpCallError(
        normalizeErrorCode(rpc.error.code, "MCP_RPC_ERROR"),
        "AgentBridge MCP returned an RPC error",
      );
    }
    return rpc.result;
  }
}

function resolveConnection({ hostConfig, serverName, endpoint, tokenEnv, env }) {
  if (endpoint) {
    const token =
      typeof tokenEnv === "string" && typeof env[tokenEnv] === "string"
        ? env[tokenEnv].trim()
        : "";
    if (!token) {
      return null;
    }
    return {
      url: endpoint.url,
      timeoutSeconds: endpoint.timeoutSeconds,
      authorization: `Bearer ${token}`,
    };
  }

  const server = resolveMcpServer(hostConfig, serverName);
  if (!server) {
    return null;
  }
  const authorization = resolveHeader(server.headers, "Authorization", env);
  if (!authorization) {
    return null;
  }
  return {
    url: server.url,
    timeoutSeconds: server.timeoutSeconds,
    authorization,
  };
}

export function parseMcpResponse(raw) {
  const text = String(raw || "").trim();
  if (!text) {
    throw new McpCallError("MCP_EMPTY_RESPONSE");
  }
  if (text.startsWith("{")) {
    try {
      return JSON.parse(text);
    } catch {
      throw new McpCallError("MCP_INVALID_RESPONSE");
    }
  }
  for (const line of text.split(/\r?\n/)) {
    if (!line.startsWith("data:")) {
      continue;
    }
    try {
      return JSON.parse(line.slice(5).trim());
    } catch {
      throw new McpCallError("MCP_INVALID_RESPONSE");
    }
  }
  throw new McpCallError("MCP_INVALID_RESPONSE");
}

export function extractToolPayload(result) {
  let payload = result;
  if (result && typeof result.structuredContent === "object") {
    payload = result.structuredContent;
  } else if (Array.isArray(result?.content)) {
    for (const block of result.content) {
      if (block?.type !== "text" || typeof block.text !== "string") {
        continue;
      }
      const text = block.text.trim();
      if (!text.startsWith("{") && !text.startsWith("[")) {
        continue;
      }
      try {
        payload = JSON.parse(text);
        break;
      } catch {
        continue;
      }
    }
  }
  if (
    payload &&
    typeof payload === "object" &&
    !Array.isArray(payload) &&
    result?._meta &&
    typeof result._meta === "object" &&
    !Array.isArray(result._meta)
  ) {
    return { ...payload, _meta: result._meta };
  }
  return payload;
}

function resolveHeader(headers, name, env) {
  const pair = Object.entries(headers).find(
    ([key]) => key.toLowerCase() === name.toLowerCase(),
  );
  if (!pair || typeof pair[1] !== "string") {
    return null;
  }
  let missing = false;
  const resolved = pair[1].replace(
    /\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g,
    (_match, variable) => {
      const value = env[variable];
      if (typeof value !== "string" || !value) {
        missing = true;
        return "";
      }
      return value;
    },
  );
  return missing || !resolved.trim() ? null : resolved.trim();
}

function normalizeErrorCode(value, fallback) {
  const normalized = String(value ?? "")
    .toUpperCase()
    .replace(/[^A-Z0-9_.-]/g, "_")
    .slice(0, 80);
  return normalized || fallback;
}
