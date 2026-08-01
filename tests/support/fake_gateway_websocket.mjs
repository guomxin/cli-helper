import { appendFileSync } from "node:fs";


const realSetTimeout = globalThis.setTimeout;
const realDateNow = Date.now.bind(Date);
const clockStartedAt = realDateNow();
Date.now = () => clockStartedAt + (realDateNow() - clockStartedAt) * 100;
globalThis.setTimeout = (callback, delay, ...args) =>
  realSetTimeout(callback, Math.max(1, Math.round(Number(delay) / 100)), ...args);

const scenario = process.env.AB_GATEWAY_FAKE_SCENARIO || "normal";
const tracePath = process.env.AB_GATEWAY_FAKE_TRACE;
let sendCount = 0;
let targetedAbortCount = 0;
let sessionListCount = 0;
let lastRunId = null;

function trace(request) {
  if (!tracePath) return;
  appendFileSync(
    tracePath,
    `${JSON.stringify({
      method: request.method,
      runId: request.params?.runId ?? null,
      idempotencyKey: request.params?.idempotencyKey ?? null,
    })}\n`,
    "utf8",
  );
}

class FakeWebSocket {
  constructor() {
    this.listeners = new Map();
    this.closed = false;
    queueMicrotask(() =>
      this.emit("message", {
        data: JSON.stringify({
          type: "event",
          event: "connect.challenge",
          payload: { nonce: "fake-nonce" },
        }),
      }),
    );
  }

  addEventListener(name, handler) {
    const handlers = this.listeners.get(name) ?? [];
    handlers.push(handler);
    this.listeners.set(name, handlers);
  }

  send(raw) {
    const request = JSON.parse(String(raw));
    trace(request);
    if (request.method === "connect") {
      this.respond(request, { protocol: 4 });
      return;
    }
    if (request.method === "chat.abort") {
      if (request.params?.runId) targetedAbortCount += 1;
      this.respond(request, {
        ok: true,
        aborted: true,
        runIds: request.params?.runId
          ? [request.params.runId]
          : ["old-run"],
      });
      return;
    }
    if (request.method === "sessions.list") {
      sessionListCount += 1;
      this.respond(request, this.sessionListPayload(request));
      return;
    }
    if (request.method === "agentbridge.workspace.bind") {
      this.respond(request, { ok: true });
      return;
    }
    if (request.method === "chat.send") {
      sendCount += 1;
      const runId = request.params.idempotencyKey;
      lastRunId = runId;
      this.respond(request, { status: "started", runId }, () => {
        if (
          scenario === "normal" ||
          scenario === "preflight_wait" ||
          (scenario === "startup_recovery" && sendCount > 1)
        ) {
          this.emitRunCompletion(request.params.sessionKey, runId);
        }
      });
      return;
    }
    this.respondError(request, "UNEXPECTED_METHOD", request.method);
  }

  close() {
    this.closed = true;
  }

  sessionListPayload(request) {
    const key = request.params.search;
    if (scenario === "preflight_wait" && sessionListCount === 1) {
      return {
        sessions: [
          { key, hasActiveRun: true, activeRunIds: ["old-run"] },
        ],
      };
    }
    if (scenario === "preflight_stuck" && sendCount === 0) {
      return {
        sessions: [
          { key, hasActiveRun: true, activeRunIds: ["old-run"] },
        ],
      };
    }
    if (
      scenario === "startup_recovery" &&
      sendCount === 1 &&
      targetedAbortCount === 0
    ) {
      return {
        sessions: [
          { key, hasActiveRun: true, activeRunIds: ["old-run"] },
        ],
      };
    }
    if (
      scenario === "startup_active" &&
      sendCount === 1 &&
      targetedAbortCount === 0
    ) {
      const runId = lastRunId;
      realSetTimeout(() => this.emitRunCompletion(key, runId), 10);
      return {
        sessions: [{ key, hasActiveRun: true, activeRunIds: [runId] }],
      };
    }
    return { sessions: [{ key, hasActiveRun: false }] };
  }

  emitRunCompletion(sessionKey, runId) {
    this.emit("message", {
      data: JSON.stringify({
        type: "event",
        event: "agent",
        payload: {
          sessionKey,
          runId,
          stream: "lifecycle",
          data: { phase: "start" },
        },
      }),
    });
    this.emit("message", {
      data: JSON.stringify({
        type: "event",
        event: "chat",
        payload: {
          sessionKey,
          runId,
          state: "final",
          message: {
            role: "assistant",
            content: [{ type: "text", text: "done" }],
          },
        },
      }),
    });
  }

  respond(request, payload, after = null) {
    queueMicrotask(() => {
      this.emit("message", {
        data: JSON.stringify({
          type: "res",
          id: request.id,
          ok: true,
          payload,
        }),
      });
      if (after) queueMicrotask(after);
    });
  }

  respondError(request, code, message) {
    queueMicrotask(() =>
      this.emit("message", {
        data: JSON.stringify({
          type: "res",
          id: request.id,
          ok: false,
          error: { code, message },
        }),
      }),
    );
  }

  emit(name, event) {
    if (this.closed) return;
    for (const handler of this.listeners.get(name) ?? []) handler(event);
  }
}

globalThis.WebSocket = FakeWebSocket;
