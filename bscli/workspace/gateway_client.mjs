import {
  createHash,
  createPrivateKey,
  generateKeyPairSync,
  randomUUID,
  sign,
} from "node:crypto";
import {
  chmodSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import { dirname } from "node:path";
import { normalizeGatewayEvent } from "./gateway_events.mjs";


const gatewayUrl = requiredEnv("AB_GATEWAY_URL");
const gatewayToken = requiredEnv("AB_GATEWAY_TOKEN");
const identityPath = requiredEnv("AB_GATEWAY_IDENTITY_PATH");
const input = JSON.parse(readFileSync(0, "utf8"));
const timeoutMs = clampInteger(input.timeoutMs, 1_000, 180_000, 30_000);
const mode = ["stream", "send-stream"].includes(input.mode)
  ? input.mode
  : "call";
const streamingMode = mode !== "call";
const method =
  mode === "call" ? requiredText(input.method, "method", 160) : null;
const params = isRecord(input.params) ? input.params : {};
const streamSessionKey =
  streamingMode
    ? requiredText(input.sessionKey, "sessionKey", 1_024)
    : null;
const endpointKey =
  mode === "send-stream"
    ? requiredText(input.endpointKey, "endpointKey", 768)
    : null;
const grant =
  mode === "send-stream"
    ? requiredText(input.grant, "grant", 256)
    : null;
const message =
  mode === "send-stream"
    ? requiredText(input.message, "message", 20_000)
    : null;
const idempotencyKey =
  mode === "send-stream"
    ? requiredText(input.idempotencyKey, "idempotencyKey", 128)
    : null;
const identity = loadOrCreateIdentity(identityPath);
const scopes = ["operator.read", "operator.write"];

if (typeof WebSocket !== "function") {
  fail("WEBSOCKET_UNAVAILABLE", "Node.js WebSocket support is unavailable.");
}

let settled = false;
let connectSent = false;
let methodSent = false;
let connected = false;
let streamRunId = mode === "send-stream" ? idempotencyKey : null;
let streamAccepted = false;
let streamHadToolActivity = false;
let abortSent = false;
let abortTimer = null;
const connectId = `connect-${randomUUID()}`;
const bindRequestId = `bind-${randomUUID()}`;
const requestId = `rpc-${randomUUID()}`;
const abortRequestId = `abort-${randomUUID()}`;
const socket = new WebSocket(gatewayUrl);
const timer = setTimeout(
  () => handleRequestTimeout(),
  timeoutMs,
);

socket.addEventListener("message", (event) => {
  let frame;
  try {
    frame = JSON.parse(String(event.data));
  } catch {
    return;
  }
  if (
    frame?.type === "event" &&
    frame.event === "connect.challenge" &&
    !connectSent
  ) {
    const nonce = requiredText(frame?.payload?.nonce, "nonce", 512);
    connectSent = true;
    socket.send(
      JSON.stringify({
        type: "req",
        id: connectId,
        method: "connect",
        params: connectParams(nonce),
      }),
    );
    return;
  }
  if (frame?.type === "event" && connected && streamingMode) {
    const normalized = normalizeGatewayEvent(
      frame,
      streamSessionKey,
      streamRunId,
    );
    if (normalized) {
      if (normalized.type === "progress" && normalized.kind === "tool") {
        streamHadToolActivity = true;
      }
      process.stdout.write(`${JSON.stringify(normalized)}\n`);
      if (
        normalized.type === "chat" &&
        ["final", "error", "aborted"].includes(normalized.state)
      ) {
        finish();
      }
    }
    return;
  }
  if (frame?.type !== "res") {
    return;
  }
  if (frame.id === connectId) {
    if (!frame.ok) {
      finishError(
        safeCode(frame?.error?.details?.code || frame?.error?.code),
        safeMessage(frame?.error?.message),
        frame?.error?.details,
      );
      return;
    }
    connected = true;
    if (mode === "stream") {
      process.stdout.write(`${JSON.stringify({ type: "ready" })}\n`);
    } else if (mode === "send-stream") {
      socket.send(
        JSON.stringify({
          type: "req",
          id: bindRequestId,
          method: "agentbridge.workspace.bind",
          params: {
            sessionKey: streamSessionKey,
            endpointKey,
            grant,
          },
        }),
      );
    } else if (!methodSent) {
      methodSent = true;
      socket.send(
        JSON.stringify({
          type: "req",
          id: requestId,
          method,
          params,
        }),
      );
    }
    return;
  }
  if (frame.id === bindRequestId && mode === "send-stream") {
    if (!frame.ok) {
      finishError(
        safeCode(frame?.error?.details?.code || frame?.error?.code),
        safeMessage(frame?.error?.message),
        frame?.error?.details,
      );
      return;
    }
    socket.send(
      JSON.stringify({
        type: "req",
        id: requestId,
        method: "chat.send",
        params: {
          sessionKey: streamSessionKey,
          message,
          deliver: false,
          idempotencyKey,
          timeoutMs: Math.min(timeoutMs, 120_000),
        },
      }),
    );
    return;
  }
  if (frame.id === abortRequestId && mode === "send-stream") {
    if (abortTimer) {
      clearTimeout(abortTimer);
      abortTimer = null;
    }
    if (!frame.ok) {
      finishError(
        "GATEWAY_RUN_TIMEOUT_ABORT_UNCONFIRMED",
        "The timed-out OpenClaw run could not be confirmed as stopped.",
        timeoutDetails(false),
      );
      return;
    }
    const payload = isRecord(frame.payload) ? frame.payload : {};
    const abortConfirmed =
      payload.aborted === true &&
      (!Array.isArray(payload.runIds) || payload.runIds.includes(streamRunId));
    finishError(
      abortConfirmed
        ? "GATEWAY_RUN_TIMEOUT_ABORTED"
        : "GATEWAY_RUN_TIMEOUT_ABORT_UNCONFIRMED",
      abortConfirmed
        ? "The timed-out OpenClaw run was stopped."
        : "The timed-out OpenClaw run was no longer abortable.",
      timeoutDetails(abortConfirmed),
    );
    return;
  }
  if (frame.id !== requestId) {
    return;
  }
  if (!frame.ok) {
    finishError(
      safeCode(frame?.error?.details?.code || frame?.error?.code),
      safeMessage(frame?.error?.message),
      frame?.error?.details,
    );
    return;
  }
  if (mode === "send-stream") {
    const payload = isRecord(frame.payload) ? frame.payload : {};
    streamRunId =
      requiredText(
        payload.runId || payload.run_id || idempotencyKey,
        "runId",
        256,
      );
    streamAccepted = true;
    process.stdout.write(
      `${JSON.stringify({
        type: "accepted",
        runId: streamRunId,
        status:
          typeof payload.status === "string"
            ? payload.status.slice(0, 80)
            : "accepted",
      })}\n`,
    );
    return;
  }
  finish({ ok: true, payload: frame.payload ?? null });
});

function handleRequestTimeout() {
  if (mode === "stream" && connected) {
    finish();
    return;
  }
  if (mode === "send-stream" && connected && streamAccepted) {
    requestStreamAbort();
    return;
  }
  finishError(
    "GATEWAY_TIMEOUT",
    "OpenClaw Gateway request timed out.",
  );
}

function requestStreamAbort() {
  if (settled || abortSent) return;
  abortSent = true;
  try {
    socket.send(
      JSON.stringify({
        type: "req",
        id: abortRequestId,
        method: "chat.abort",
        params: {
          sessionKey: streamSessionKey,
          runId: streamRunId,
        },
      }),
    );
  } catch {
    finishError(
      "GATEWAY_RUN_TIMEOUT_ABORT_UNCONFIRMED",
      "The timed-out OpenClaw run could not be stopped.",
      timeoutDetails(false),
    );
    return;
  }
  abortTimer = setTimeout(
    () =>
      finishError(
        "GATEWAY_RUN_TIMEOUT_ABORT_UNCONFIRMED",
        "OpenClaw did not confirm that the timed-out run stopped.",
        timeoutDetails(false),
      ),
    10_000,
  );
}

function timeoutDetails(aborted) {
  return {
    runId: streamRunId,
    abortRequested: abortSent,
    aborted,
    hadToolActivity: streamHadToolActivity,
  };
}

socket.addEventListener("error", () => {
  finishError(
    "GATEWAY_CONNECTION_FAILED",
    "Could not connect to the OpenClaw Gateway.",
  );
});

socket.addEventListener("close", (event) => {
  if (!settled) {
    finishError(
      "GATEWAY_CONNECTION_CLOSED",
      safeMessage(event.reason || "OpenClaw Gateway closed the connection."),
    );
  }
});

function connectParams(nonce) {
  const signedAt = Date.now();
  const clientId = "gateway-client";
  const clientMode = "backend";
  const role = "operator";
  const platform = "linux";
  const deviceFamily = "server";
  const payload = [
    "v3",
    identity.deviceId,
    clientId,
    clientMode,
    role,
    scopes.join(","),
    String(signedAt),
    gatewayToken,
    nonce,
    platform,
    deviceFamily,
  ].join("|");
  const signature = sign(
    null,
    Buffer.from(payload, "utf8"),
    createPrivateKey(identity.privateKeyPem),
  ).toString("base64url");
  return {
    minProtocol: 4,
    maxProtocol: 4,
    client: {
      id: clientId,
      displayName: "AgentBridge Workspace",
      version: "0.1.0",
      platform,
      deviceFamily,
      mode: clientMode,
    },
    caps: ["tool-events"],
    role,
    scopes,
    device: {
      id: identity.deviceId,
      publicKey: identity.publicKey,
      signature,
      signedAt,
      nonce,
    },
    auth: { token: gatewayToken },
    locale: "zh-CN",
    userAgent: "agentbridge-workspace/0.1.0",
  };
}

function loadOrCreateIdentity(path) {
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8"));
    if (
      parsed?.version === 1 &&
      typeof parsed.deviceId === "string" &&
      typeof parsed.publicKey === "string" &&
      typeof parsed.privateKeyPem === "string"
    ) {
      return parsed;
    }
  } catch {}
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const publicDer = publicKey.export({ type: "spki", format: "der" });
  const publicRaw = publicDer.subarray(publicDer.length - 32);
  const stored = {
    version: 1,
    deviceId: createHash("sha256").update(publicRaw).digest("hex"),
    publicKey: publicRaw.toString("base64url"),
    privateKeyPem: privateKey.export({ type: "pkcs8", format: "pem" }),
    createdAt: new Date().toISOString(),
  };
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  const temporary = `${path}.${process.pid}.tmp`;
  writeFileSync(temporary, `${JSON.stringify(stored)}\n`, { mode: 0o600 });
  chmodSync(temporary, 0o600);
  renameSync(temporary, path);
  return stored;
}

function finish(payload) {
  if (settled) return;
  settled = true;
  clearTimeout(timer);
  if (abortTimer) {
    clearTimeout(abortTimer);
    abortTimer = null;
  }
  if (streamingMode) {
    process.stdout.write(`${JSON.stringify({ type: "eof" })}\n`);
  } else {
    process.stdout.write(`${JSON.stringify(payload)}\n`);
  }
  try {
    socket.close();
  } catch {}
}

function finishError(code, message, details = undefined) {
  const error = {
    code: safeCode(code),
    message: safeMessage(message),
    ...(isRecord(details) ? { details } : {}),
  };
  if (streamingMode) {
    if (!settled) {
      process.stdout.write(
        `${JSON.stringify({ type: "error", error })}\n`,
      );
    }
    finish();
  } else {
    finish({ ok: false, error });
  }
}

function fail(code, message) {
  process.stdout.write(
    `${JSON.stringify({ ok: false, error: { code, message } })}\n`,
  );
  process.exit(2);
}

function requiredEnv(name) {
  const value = process.env[name]?.trim();
  if (!value) fail("GATEWAY_CONFIG_INVALID", `${name} is required.`);
  return value;
}

function requiredText(value, name, maximum) {
  const normalized =
    typeof value === "string" || typeof value === "number"
      ? String(value).trim()
      : "";
  if (!normalized || normalized.length > maximum) {
    fail("GATEWAY_REQUEST_INVALID", `${name} is invalid.`);
  }
  return normalized;
}

function safeCode(value) {
  const normalized = String(value || "GATEWAY_REQUEST_FAILED")
    .toUpperCase()
    .replace(/[^A-Z0-9_.-]/g, "_")
    .slice(0, 80);
  return normalized || "GATEWAY_REQUEST_FAILED";
}

function safeMessage(value) {
  return String(value || "OpenClaw Gateway request failed.").slice(0, 500);
}

function clampInteger(value, minimum, maximum, fallback) {
  const number = Number(value);
  return Number.isInteger(number)
    ? Math.min(Math.max(number, minimum), maximum)
    : fallback;
}

function isRecord(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
