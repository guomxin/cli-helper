import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import { createInteractionSharedState } from "./lib/coordinator.js";
import { registerAgentBridgeInteractions } from "./lib/plugin.js";

const PROCESS_STATE_KEY = Symbol.for(
  "guomxin.agentbridge.openclaw.interaction-state.v1",
);
const sharedState =
  globalThis[PROCESS_STATE_KEY] ||
  (globalThis[PROCESS_STATE_KEY] = createInteractionSharedState());

export default definePluginEntry({
  id: "agentbridge-interactions",
  name: "AgentBridge Interactions",
  description: "Trusted AgentBridge interaction cards for private OpenClaw chats",
  version: "0.2.9",
  register(api) {
    registerAgentBridgeInteractions(api, { sharedState });
  },
});

export { registerAgentBridgeInteractions };
