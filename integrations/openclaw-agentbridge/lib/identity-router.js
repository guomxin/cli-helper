import { matchIdentityBinding, resolveMcpEndpoint } from "./config.js";
import { isPrivateSessionKey } from "./interaction.js";
import { createAgentBridgeMcpClient } from "./mcp-client.js";

export class AgentBridgeIdentityRouter {
  constructor({
    config,
    hostConfig,
    env = process.env,
    fetchImpl = globalThis.fetch,
  }) {
    this.config = config;
    this.endpoint = resolveMcpEndpoint(config, hostConfig);
    this.env = env;
    this.fetchImpl = fetchImpl;
    this.bindingsByKey = new Map(
      config.identityBindings.map((binding) => [binding.key, binding]),
    );
    this.clients = new Map();
    this.sessionBindings = new Map();
  }

  get enabled() {
    return this.config.identityBindings.length > 0;
  }

  resolveToolContext(context) {
    const deliveryContext =
      context.deliveryContext &&
      typeof context.deliveryContext === "object" &&
      !Array.isArray(context.deliveryContext)
        ? context.deliveryContext
        : {};
    const sessionKey = context.sessionKey;
    const channel = context.messageChannel || deliveryContext.channel;
    const accountId = context.agentAccountId || deliveryContext.accountId;
    const senderId =
      context.requesterSenderId ||
      trustedDirectDeliverySender({
        sessionKey,
        channel,
        deliveryTo: deliveryContext.to,
      });

    if (!senderId) {
      const pinned = this.resolvePinnedSession({
        sessionKey,
        channel,
        accountId,
      });
      if (pinned) {
        return pinned;
      }
    }
    return this.resolve({
      sessionKey,
      channel,
      senderId,
      accountId,
    });
  }

  bindSession({ sessionKey, channel, senderId, accountId }) {
    const resolved = this.resolve({
      sessionKey,
      channel,
      senderId,
      accountId,
      bindSession: true,
    });
    return resolved.bound;
  }

  clientForSession(sessionKey) {
    const bindingKey = this.sessionBindings.get(sessionKey);
    if (!bindingKey || bindingKey === "conflict") {
      return null;
    }
    return this.clientForBinding(this.bindingsByKey.get(bindingKey));
  }

  statusForSession(sessionKey) {
    const bindingKey = this.sessionBindings.get(sessionKey);
    const binding = this.bindingsByKey.get(bindingKey);
    return {
      enabled: this.enabled,
      bound: Boolean(binding && this.clientForBinding(binding)),
      label: binding?.label || null,
      state:
        bindingKey === "conflict"
          ? "identity_conflict"
          : binding
            ? "bound"
            : "unbound",
    };
  }

  removeSession(sessionKey) {
    this.sessionBindings.delete(sessionKey);
  }

  resolvePinnedSession({ sessionKey, channel, accountId }) {
    if (!isPrivateSessionKey(sessionKey) || !this.endpoint) {
      return null;
    }
    const bindingKey = this.sessionBindings.get(sessionKey);
    if (!bindingKey || bindingKey === "conflict") {
      return null;
    }
    const binding = this.bindingsByKey.get(bindingKey);
    if (!binding) {
      return null;
    }
    const normalizedChannel = identityPart(channel, true);
    const normalizedAccountId = identityPart(accountId, false);
    if (
      (normalizedChannel && normalizedChannel !== binding.channel) ||
      (normalizedAccountId &&
        binding.accountId !== null &&
        normalizedAccountId !== binding.accountId)
    ) {
      this.sessionBindings.set(sessionKey, "conflict");
      return unbound("session_identity_conflict");
    }
    const client = this.clientForBinding(binding);
    if (!client) {
      return unbound("identity_token_unavailable");
    }
    return Object.freeze({
      bound: true,
      binding,
      client,
      reason: null,
    });
  }

  resolve({
    sessionKey,
    channel,
    senderId,
    accountId,
    bindSession = true,
  }) {
    if (!isPrivateSessionKey(sessionKey)) {
      return unbound("private_session_required");
    }
    if (!this.endpoint) {
      return unbound("mcp_endpoint_unavailable");
    }
    const binding = matchIdentityBinding(this.config.identityBindings, {
      channel,
      senderId,
      accountId,
    });
    if (!binding) {
      return unbound("identity_not_provisioned");
    }

    const existing = this.sessionBindings.get(sessionKey);
    if (existing === "conflict" || (existing && existing !== binding.key)) {
      this.sessionBindings.set(sessionKey, "conflict");
      return unbound("session_identity_conflict");
    }
    if (bindSession) {
      this.sessionBindings.set(sessionKey, binding.key);
    }

    const client = this.clientForBinding(binding);
    if (!client) {
      return unbound("identity_token_unavailable");
    }
    return Object.freeze({
      bound: true,
      binding,
      client,
      reason: null,
    });
  }

  clientForBinding(binding) {
    if (!binding || !this.endpoint) {
      return null;
    }
    if (this.clients.has(binding.key)) {
      return this.clients.get(binding.key);
    }
    const client = createAgentBridgeMcpClient({
      endpoint: this.endpoint,
      tokenEnv: binding.tokenEnv,
      env: this.env,
      fetchImpl: this.fetchImpl,
    });
    if (client) {
      this.clients.set(binding.key, client);
    }
    return client;
  }
}

function unbound(reason) {
  return Object.freeze({
    bound: false,
    binding: null,
    client: null,
    reason,
  });
}

function trustedDirectDeliverySender({ sessionKey, channel, deliveryTo }) {
  if (
    identityPart(channel, true) !== "openclaw-weixin" ||
    typeof sessionKey !== "string" ||
    typeof deliveryTo !== "string"
  ) {
    return null;
  }
  const match = sessionKey
    .trim()
    .match(/^agent:[^:]+:openclaw-weixin:direct:(.+)$/i);
  const peer = identityPart(match?.[1], true);
  const target = identityPart(deliveryTo, true);
  return peer && target && peer === target ? deliveryTo.trim() : null;
}

function identityPart(value, lowercase) {
  if (typeof value !== "string" && typeof value !== "number") {
    return null;
  }
  const normalized = String(value).trim().slice(0, 512);
  return normalized ? (lowercase ? normalized.toLowerCase() : normalized) : null;
}
