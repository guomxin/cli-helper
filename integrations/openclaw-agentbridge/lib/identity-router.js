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
    return this.resolve({
      sessionKey: context.sessionKey,
      channel: context.messageChannel,
      senderId: context.requesterSenderId,
      accountId: context.agentAccountId,
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
