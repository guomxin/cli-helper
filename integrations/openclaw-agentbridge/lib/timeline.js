import { createHash, randomUUID } from "node:crypto";

const RECENT_KEY_TTL_MS = 10_000;
const MAX_RECENT_KEYS = 500;

export class TimelinePublisher {
  constructor({
    identityRouter,
    logger,
    sharedState = {},
    now = Date.now,
  }) {
    this.identityRouter = identityRouter;
    this.logger = logger;
    this.now = now;
    this.queues =
      sharedState.timelineQueues ||
      (sharedState.timelineQueues = new Map());
    this.recentKeys =
      sharedState.timelineRecentKeys ||
      (sharedState.timelineRecentKeys = new Map());
    this.publications =
      sharedState.timelinePublications ||
      (sharedState.timelinePublications = new Map());
  }

  capture({
    sessionKey,
    role,
    text,
    event = {},
    context = {},
    taskId = null,
  }) {
    const normalizedText = safeText(text, 50_000);
    if (
      !normalizedText ||
      !["user", "assistant"].includes(role) ||
      isWorkspaceSession(sessionKey)
    ) {
      return Promise.resolve(false);
    }
    const resolved = this.identityRouter.resolvePinnedSession({
      sessionKey,
      channel: event.channel || context.channelId,
      accountId: event.accountId || context.accountId,
    });
    const endpointKey = this.identityRouter.endpointKeyForSession(sessionKey);
    if (!resolved?.bound || !endpointKey) {
      return Promise.resolve(false);
    }
    const binding = resolved.binding;
    const messageKey = this.messageKey({
      sessionKey,
      role,
      text: normalizedText,
      event,
      context,
    });
    const existingPublication = this.publications.get(messageKey);
    if (existingPublication) {
      return existingPublication.promise;
    }
    const route = {
      channel: binding.channel,
      to:
        safeText(
          context.conversationId ||
            event.conversationId ||
            event.to ||
            binding.senderId,
          768,
        ) || binding.senderId,
      accountId:
        safeText(context.accountId || event.accountId, 512) ||
        binding.accountId,
      threadId: safeText(context.threadId || event.threadId, 512),
    };
    const publication = this.enqueue(binding.key, async () => {
      try {
        await resolved.client.callTool(
          "agentbridge_host_timeline_append",
          {
            agent_host: "openclaw",
            endpoint_key: endpointKey,
            client_type: binding.channel,
            external_subject: binding.senderId,
            conversation_ref: sessionKey,
            message_key: messageKey,
            role,
            text: normalizedText,
            account_id: binding.accountId,
            label: binding.label,
            route,
            task_id: safeText(taskId, 128),
          },
          {
            signal: AbortSignal.timeout(3_000),
            meta: {
              "io.agentbridge/host": {
                version: "1",
                agentHost: "openclaw",
              },
            },
          },
        );
        return true;
      } catch (error) {
        this.logger?.warn?.(
          `AgentBridge cross-end text synchronization failed (${safeErrorCode(error)})`,
        );
        return false;
      }
    });
    const publishedAt = this.now();
    this.publications.set(messageKey, {
      promise: publication,
      createdAt: publishedAt,
    });
    void publication.then((succeeded) => {
      if (
        !succeeded &&
        this.publications.get(messageKey)?.promise === publication
      ) {
        this.publications.delete(messageKey);
      }
    });
    return publication;
  }

  async waitForIdle() {
    await Promise.allSettled([...this.queues.values()]);
  }

  messageKey({ sessionKey, role, text, event, context }) {
    const now = this.now();
    this.prune(now);
    const fingerprint = createHash("sha256")
      .update(`${role}\0${sessionKey}\0${text}`, "utf8")
      .digest("hex");
    const recent = this.recentKeys.get(fingerprint);
    if (recent && now - recent.createdAt <= RECENT_KEY_TTL_MS) {
      return recent.messageKey;
    }
    const sourceId = safeText(
      event.messageId ||
        event.id ||
        event.runId ||
        context.messageId ||
        context.runId,
      256,
    );
    const messageKey = sourceId
      ? `${role}:${sessionKey}:${sourceId}`.slice(0, 768)
      : (
          `${role}:${sessionKey}:${now}:${randomUUID()}`
        ).slice(0, 768);
    this.recentKeys.set(fingerprint, { messageKey, createdAt: now });
    return messageKey;
  }

  enqueue(key, work) {
    const previous = this.queues.get(key) || Promise.resolve();
    const current = previous
      .catch(() => undefined)
      .then(work)
      .finally(() => {
        if (this.queues.get(key) === current) {
          this.queues.delete(key);
        }
      });
    this.queues.set(key, current);
    return current;
  }

  prune(now) {
    for (const [key, value] of this.recentKeys) {
      if (now - value.createdAt > RECENT_KEY_TTL_MS) {
        this.recentKeys.delete(key);
      }
    }
    while (this.recentKeys.size > MAX_RECENT_KEYS) {
      this.recentKeys.delete(this.recentKeys.keys().next().value);
    }
    for (const [key, value] of this.publications) {
      if (now - value.createdAt > RECENT_KEY_TTL_MS) {
        this.publications.delete(key);
      }
    }
    while (this.publications.size > MAX_RECENT_KEYS) {
      this.publications.delete(this.publications.keys().next().value);
    }
  }
}

function isWorkspaceSession(sessionKey) {
  return (
    typeof sessionKey === "string" &&
    /^agent:[^:]+:agentbridge-workspace:direct:/i.test(sessionKey.trim())
  );
}

function safeText(value, maximum) {
  if (typeof value !== "string" && typeof value !== "number") {
    return null;
  }
  const normalized = String(value).replaceAll("\0", "").trim();
  return normalized ? normalized.slice(0, maximum) : null;
}

function safeErrorCode(error) {
  return String(error?.code || error?.name || "TIMELINE_SYNC_FAILED")
    .toUpperCase()
    .replace(/[^A-Z0-9_.-]/g, "_")
    .slice(0, 80);
}
