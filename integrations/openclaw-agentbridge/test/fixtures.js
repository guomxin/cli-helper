export const CARD_ORIGIN = "http://10.10.50.213:8780";
export const CARD_URL = `${CARD_ORIGIN}/auth/opaque-card-token`;

export function interaction(overrides = {}) {
  const base = {
    schemaVersion: "agentbridge.interaction.v1",
    interactionId: "interaction-1234567890",
    type: "credential",
    state: "pending",
    title: "登录致远 OA",
    message: "请在 AgentBridge 安全页面完成登录。",
    presentation: {
      owner: "agentbridge",
      preferred: "embedded_secure_web_app",
      fallback: "url",
      url: CARD_URL,
      modelMustNotCollectValues: true,
    },
    display: {
      systemName: "致远 OA",
    },
    expiresAt: "2099-07-14T12:00:00+00:00",
    poll: {
      tool: "agentbridge_interaction_get",
      recommendedIntervalSeconds: 2,
    },
    resume: {
      tool: "agentbridge_interaction_resume",
      ready: false,
      completed: false,
    },
  };
  return deepMerge(base, overrides);
}

export function toolResult(envelope = interaction()) {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify({
          protocolVersion: "0.1",
          status: "requires_user_action",
          interaction: envelope,
        }),
      },
    ],
    details: {
      structuredContent: {
        interaction: envelope,
      },
    },
  };
}

function deepMerge(left, right) {
  const result = structuredClone(left);
  for (const [key, value] of Object.entries(right)) {
    if (
      value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      result[key] &&
      typeof result[key] === "object" &&
      !Array.isArray(result[key])
    ) {
      result[key] = deepMerge(result[key], value);
    } else {
      result[key] = value;
    }
  }
  return result;
}
