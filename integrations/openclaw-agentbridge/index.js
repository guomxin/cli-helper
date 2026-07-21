import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import { registerAgentBridgeInteractions } from "./lib/plugin.js";

export default definePluginEntry({
  id: "agentbridge-interactions",
  name: "AgentBridge Interactions",
  description: "Trusted AgentBridge interaction cards for private OpenClaw chats",
  version: "0.1.12",
  register(api) {
    registerAgentBridgeInteractions(api);
  },
});

export { registerAgentBridgeInteractions };
