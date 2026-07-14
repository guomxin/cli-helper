# AgentBridge Interactions for OpenClaw

This native OpenClaw plugin recognizes `agentbridge.interaction.v1` envelopes
returned by AgentBridge MCP tools and renders trusted card buttons in private
conversations.

Security behavior is intentionally fail closed:

- card URLs are accepted only from `allowedCardOrigins`;
- card URLs are removed before tool results are returned to the model;
- cards are not rendered in group, channel, or room sessions;
- credentials, business fields, cookies, and authorization decisions remain in
  AgentBridge trusted pages;
- background polling can resume a completed interaction once, while automatic
  model wake-up is disabled by default.

## Local installation

```powershell
openclaw plugins install --link D:\Codes\CLIExp\integrations\openclaw-agentbridge
openclaw config set "plugins.entries.agentbridge-interactions.config.allowedCardOrigins[0]" http://10.10.50.213:8780
openclaw plugins enable agentbridge-interactions
openclaw gateway restart
openclaw plugins inspect agentbridge-interactions --runtime --json
openclaw gateway status --deep --require-rpc
```

The plugin reuses the configured `mcp.servers.agentbridge` endpoint and its
environment-backed Authorization header for background polling. It never stores
or prints that header.

Telegram receives a native Web App button only when the trusted card uses
HTTPS. The controlled private-IP HTTP PoC uses a normal URL button so the card
opens in the user's browser. New interactions produced by background resume are
kept host-side; they appear on the next private reply or through
`/agentbridge pending`. Automatic model wake-up remains disabled unless
`wakeAgentOnComplete` is deliberately enabled after the model data boundary is
approved.

In a private conversation, `/agentbridge status` reports safe diagnostics and
`/agentbridge pending` redraws the latest unexpired trusted interaction.
