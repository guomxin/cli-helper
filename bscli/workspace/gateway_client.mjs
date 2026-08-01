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
import {
  canRecoverStartup,
  recoveryIdempotencyKey,
  sessionRunState,
} from "./gateway_run_guard.mjs";


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
const preflightAbort =
  mode === "send-stream" && input.preflightAbort !== false;
const acceptTimeoutMs =
  mode === "send-stream"
    ? clampInteger(input.acceptTimeoutMs, 5_000, 60_000, 20_000)
    : timeoutMs;
const startupProgressTimeoutMs =
  mode === "send-stream"
    ? clampInteger(input.startupProgressTimeoutMs, 5_000, 60_000, 15_000)
    : timeoutMs;
const sessionIdleTimeoutMs =
  mode === "send-stream"
    ? clampInteger(input.sessionIdleTimeoutMs, 1_000, 30_000, 15_000)
    : timeoutMs;
const sessionIdlePollMs =
  mode === "send-stream"
    ? clampInteger(input.sessionIdlePollMs, 100, 2_000, 250)
    : 250;
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
let streamHadProgress = false;
let streamHadToolActivity = false;
let recoveryUsed = false;
let recoveryAttempt = 0;
let currentIdempotencyKey = idempotencyKey;
let recoveredFromRunId = null;
let abortSent = false;
let abortTimer = null;
let abortPurpose = null;
let startupTimer = null;
let sessionStateTimer = null;
let sessionStateRequestId = null;
let sessionStatePurpose = null;
let sessionIdleDeadline = 0;
let requestStage = "connect";
let preflightFallbackSent = false;
const connectId = `connect-${randomUUID()}`;
const preflightAbortRequestId = `preflight-abort-${randomUUID()}`;
const preflightAbortFallbackRequestId =
  `preflight-abort-fallback-${randomUUID()}`;
const bindRequestId = `bind-${randomUUID()}`;
let requestId = `rpc-${randomUUID()}`;
let abortRequestId = `abort-${randomUUID()}`;
const socket = new WebSocket(gatewayUrl);
let timer = setTimeout(
  () => handleRequestTimeout(),
  acceptTimeoutMs,
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
      streamHadProgress = true;
      clearStartupTimer();
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
      if (preflightAbort) {
        requestPreflightAbort(true);
      } else {
        requestWorkspaceBind();
      }
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
  if (
    mode === "send-stream" &&
    [preflightAbortRequestId, preflightAbortFallbackRequestId].includes(
      frame.id,
    )
  ) {
    if (!frame.ok) {
      const code = safeCode(
        frame?.error?.details?.code || frame?.error?.code,
      );
      if (
        frame.id === preflightAbortRequestId &&
        code === "INVALID_REQUEST" &&
        !preflightFallbackSent
      ) {
        preflightFallbackSent = true;
        requestPreflightAbort(false);
        return;
      }
      finishError(
        code,
        safeMessage(frame?.error?.message),
        stageDetails(),
      );
      return;
    }
    beginSessionIdleWait("preflight");
    return;
  }
  if (
    mode === "send-stream" &&
    sessionStateRequestId &&
    frame.id === sessionStateRequestId
  ) {
    handleSessionStateResponse(frame);
    return;
  }
  if (frame.id === bindRequestId && mode === "send-stream") {
    if (!frame.ok) {
      finishError(
        safeCode(frame?.error?.details?.code || frame?.error?.code),
        safeMessage(frame?.error?.message),
        {
          ...(isRecord(frame?.error?.details) ? frame.error.details : {}),
          ...stageDetails(),
        },
      );
      return;
    }
    requestChatSend();
    return;
  }
  if (frame.id === abortRequestId && mode === "send-stream") {
    if (abortTimer) {
      clearTimeout(abortTimer);
      abortTimer = null;
    }
    handleAbortResponse(frame);
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
        payload.runId || payload.run_id || currentIdempotencyKey,
        "runId",
        256,
      );
    streamAccepted = true;
    requestStage = recoveryUsed ? "run_recovered" : "run";
    clearTimeout(timer);
    timer = setTimeout(() => handleRequestTimeout(), timeoutMs);
    armStartupTimer();
    process.stdout.write(
      `${JSON.stringify({
        type: "accepted",
        runId: streamRunId,
        status:
          typeof payload.status === "string"
            ? payload.status.slice(0, 80)
            : "accepted",
        attempt: recoveryAttempt,
        ...(recoveredFromRunId ? { recoveredFromRunId } : {}),
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
    requestStreamAbort("timeout");
    return;
  }
  finishError(
    "GATEWAY_TIMEOUT",
    "OpenClaw Gateway request timed out.",
    stageDetails(),
  );
}

function requestPreflightAbort(preserveSideRuns) {
  requestStage = "preflight_abort";
  socket.send(
    JSON.stringify({
      type: "req",
      id: preserveSideRuns
        ? preflightAbortRequestId
        : preflightAbortFallbackRequestId,
      method: "chat.abort",
      params: {
        sessionKey: streamSessionKey,
        ...(preserveSideRuns ? { preserveSideRuns: true } : {}),
      },
    }),
  );
}

function requestWorkspaceBind() {
  requestStage = "bind";
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
}

function requestChatSend() {
  requestStage = recoveryUsed ? "send_accept_recovery" : "send_accept";
  requestId = `rpc-${randomUUID()}`;
  clearTimeout(timer);
  timer = setTimeout(() => handleRequestTimeout(), acceptTimeoutMs);
  socket.send(
    JSON.stringify({
      type: "req",
      id: requestId,
      method: "chat.send",
      params: {
        sessionKey: streamSessionKey,
        message,
        deliver: false,
        idempotencyKey: currentIdempotencyKey,
        timeoutMs: Math.min(timeoutMs, 120_000),
      },
    }),
  );
}

function beginSessionIdleWait(purpose) {
  clearSessionStateTimer();
  sessionStatePurpose = purpose;
  sessionIdleDeadline = Date.now() + sessionIdleTimeoutMs;
  requestSessionState();
}

function requestSessionState() {
  if (settled || !sessionStatePurpose) return;
  clearSessionStateTimer();
  requestStage =
    sessionStatePurpose === "startup_probe"
      ? "startup_probe"
      : `wait_idle_${sessionStatePurpose}`;
  sessionStateRequestId = `session-state-${randomUUID()}`;
  socket.send(
    JSON.stringify({
      type: "req",
      id: sessionStateRequestId,
      method: "sessions.list",
      params: {
        limit: 20,
        search: streamSessionKey,
        includeGlobal: true,
        includeUnknown: true,
        archived: false,
      },
    }),
  );
  sessionStateTimer = setTimeout(
    handleSessionStateTimeout,
    Math.min(5_000, sessionIdleTimeoutMs),
  );
}

function handleSessionStateResponse(frame) {
  const purpose = sessionStatePurpose;
  sessionStateRequestId = null;
  clearSessionStateTimer();
  if (!purpose) return;
  if (!frame.ok) {
    if (purpose === "startup_probe") {
      sessionStatePurpose = null;
      streamHadProgress = true;
      return;
    }
    finishError(
      "GATEWAY_SESSION_STATE_UNAVAILABLE",
      "OpenClaw session state could not be confirmed before starting the run.",
      stageDetails(),
    );
    return;
  }
  const payload = isRecord(frame.payload) ? frame.payload : {};
  const runState = sessionRunState(payload, streamSessionKey, streamRunId);
  if (purpose === "startup_probe") {
    sessionStatePurpose = null;
    handleStartupProbe(runState);
    return;
  }
  if (!runState.active) {
    sessionStatePurpose = null;
    if (purpose === "preflight") {
      requestWorkspaceBind();
    } else {
      startRecoveryAttempt();
    }
    return;
  }
  if (Date.now() >= sessionIdleDeadline) {
    finishError(
      "GATEWAY_SESSION_NOT_IDLE",
      "The previous OpenClaw run did not release the session in time.",
      {
        ...stageDetails(),
        activeRunIds: runState.activeRunIds,
        waitPurpose: purpose,
      },
    );
    return;
  }
  sessionStateTimer = setTimeout(requestSessionState, sessionIdlePollMs);
}

function handleSessionStateTimeout() {
  sessionStateTimer = null;
  sessionStateRequestId = null;
  if (sessionStatePurpose === "startup_probe") {
    sessionStatePurpose = null;
    streamHadProgress = true;
    return;
  }
  finishError(
    "GATEWAY_SESSION_STATE_UNAVAILABLE",
    "OpenClaw session state did not respond before starting the run.",
    stageDetails(),
  );
}

function armStartupTimer() {
  clearStartupTimer();
  startupTimer = setTimeout(() => {
    startupTimer = null;
    if (settled || streamHadProgress || streamHadToolActivity) return;
    sessionStatePurpose = "startup_probe";
    requestSessionState();
  }, startupProgressTimeoutMs);
}

function clearStartupTimer() {
  if (startupTimer) {
    clearTimeout(startupTimer);
    startupTimer = null;
  }
}

function clearSessionStateTimer() {
  if (sessionStateTimer) {
    clearTimeout(sessionStateTimer);
    sessionStateTimer = null;
  }
}

function handleStartupProbe(runState) {
  if (streamHadProgress || streamHadToolActivity) return;
  if (runState.currentRunActive) {
    streamHadProgress = true;
    requestStage = recoveryUsed ? "run_recovered" : "run";
    process.stdout.write(
      `${JSON.stringify({
        type: "progress",
        runId: streamRunId,
        kind: "lifecycle",
        phase: "start",
        label: "\u667a\u80fd\u4f53\u5df2\u542f\u52a8\uff0c\u6b63\u5728\u5904\u7406",
        source: "session-state",
      })}\n`,
    );
    return;
  }
  const recover = canRecoverStartup({
    progressObserved: streamHadProgress,
    toolActivity: streamHadToolActivity,
    recoveryUsed,
    runState,
  });
  requestStreamAbort(recover ? "startup_recovery" : "startup_final");
}

function startRecoveryAttempt() {
  recoveredFromRunId = streamRunId;
  recoveryUsed = true;
  recoveryAttempt += 1;
  currentIdempotencyKey = recoveryIdempotencyKey(
    idempotencyKey,
    recoveryAttempt,
  );
  streamRunId = currentIdempotencyKey;
  streamAccepted = false;
  streamHadProgress = false;
  streamHadToolActivity = false;
  abortSent = false;
  abortPurpose = null;
  abortRequestId = `abort-${randomUUID()}`;
  process.stdout.write(
    `${JSON.stringify({
      type: "progress",
      runId: recoveredFromRunId,
      kind: "system",
      phase: "recovery",
      label: "\u667a\u80fd\u4f53\u542f\u52a8\u5ef6\u8fdf\uff0c\u6b63\u5728\u81ea\u52a8\u6062\u590d",
      attempt: recoveryAttempt,
    })}\n`,
  );
  requestChatSend();
}

function requestStreamAbort(purpose = "timeout") {
  if (settled || abortSent) return;
  clearStartupTimer();
  clearTimeout(timer);
  abortSent = true;
  abortPurpose = purpose;
  abortRequestId = `abort-${randomUUID()}`;
  requestStage = `abort_${purpose}`;
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
    finishAbortError(false);
    return;
  }
  abortTimer = setTimeout(
    () => finishAbortError(false),
    10_000,
  );
}

function handleAbortResponse(frame) {
  if (!frame.ok) {
    finishAbortError(false);
    return;
  }
  const payload = isRecord(frame.payload) ? frame.payload : {};
  const abortConfirmed =
    payload.aborted === true &&
    (!Array.isArray(payload.runIds) || payload.runIds.includes(streamRunId));
  if (abortPurpose === "startup_recovery" && abortConfirmed) {
    beginSessionIdleWait("recovery");
    return;
  }
  finishAbortError(abortConfirmed);
}

function finishAbortError(aborted) {
  const startup = abortPurpose?.startsWith("startup_") === true;
  finishError(
    startup
      ? aborted
        ? "GATEWAY_START_STALLED_ABORTED"
        : "GATEWAY_START_STALLED_ABORT_UNCONFIRMED"
      : aborted
        ? "GATEWAY_RUN_TIMEOUT_ABORTED"
        : "GATEWAY_RUN_TIMEOUT_ABORT_UNCONFIRMED",
    startup
      ? aborted
        ? "The stalled OpenClaw startup was stopped."
        : "The stalled OpenClaw startup could not be confirmed as stopped."
      : aborted
        ? "The timed-out OpenClaw run was stopped."
        : "The timed-out OpenClaw run could not be confirmed as stopped.",
    timeoutDetails(aborted),
  );
}

function timeoutDetails(aborted) {
  return {
    runId: streamRunId,
    abortRequested: abortSent,
    aborted,
    abortPurpose,
    recoveryUsed,
    recoveryAttempt,
    hadProgress: streamHadProgress,
    hadToolActivity: streamHadToolActivity,
  };
}

function stageDetails() {
  return {
    stage: requestStage,
    accepted: streamAccepted,
    recoveryUsed,
    recoveryAttempt,
    hadProgress: streamHadProgress,
    hadToolActivity: streamHadToolActivity,
  };
}

socket.addEventListener("error", () => {
  finishError(
    "GATEWAY_CONNECTION_FAILED",
    "Could not connect to the OpenClaw Gateway.",
    stageDetails(),
  );
});

socket.addEventListener("close", (event) => {
  if (!settled) {
    finishError(
      "GATEWAY_CONNECTION_CLOSED",
      safeMessage(event.reason || "OpenClaw Gateway closed the connection."),
      stageDetails(),
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
  clearStartupTimer();
  clearSessionStateTimer();
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
