import { createAgentBridgeMcpClient } from "../integrations/openclaw-agentbridge/lib/mcp-client.js";

const CHECKS = new Map([
  ["SessionStatus", { tool: "oa_session_status", arguments: {} }],
  ["LoginReuse", { tool: "oa_session_login", arguments: {} }],
]);

function argument(name, fallback) {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

function safeCode(value, fallback = "MCP_SMOKE_FAILED") {
  const normalized = String(value ?? "")
    .toUpperCase()
    .replace(/[^A-Z0-9_.-]/g, "_")
    .slice(0, 80);
  return normalized || fallback;
}

async function readStdin() {
  let input = "";
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) {
    input += chunk;
  }
  return input;
}

try {
  const checkName = argument("--check", "SessionStatus");
  const serverName = argument("--server-name", "agentbridge");
  const check = CHECKS.get(checkName);
  if (!check) {
    throw Object.assign(new Error("Unsupported smoke check"), { code: "INVALID_CHECK" });
  }

  const server = JSON.parse(await readStdin());
  if (
    typeof server?.url !== "string" ||
    typeof server?.headers?.Authorization !== "string" ||
    !server.headers.Authorization.startsWith("Bearer ")
  ) {
    throw Object.assign(new Error("Resolved MCP configuration is incomplete"), {
      code: "INVALID_MCP_CONFIG",
    });
  }

  const client = createAgentBridgeMcpClient({
    hostConfig: { mcp: { servers: { [serverName]: server } } },
    serverName,
  });
  if (!client) {
    throw Object.assign(new Error("MCP client was not created"), {
      code: "MCP_CLIENT_NOT_CREATED",
    });
  }

  const payload = await client.callTool(check.tool, check.arguments);
  const errorCode = payload?.error?.code ? safeCode(payload.error.code) : null;
  const summary =
    checkName === "LoginReuse"
      ? {
          status: "succeeded",
          check: checkName,
          operationStatus: String(payload?.status ?? "unknown").slice(0, 80),
          reused: Boolean(payload?.reused),
          hasInteraction: Boolean(payload?.interaction),
          nextAction: payload?.nextAction?.type
            ? String(payload.nextAction.type).slice(0, 80)
            : null,
          errorCode,
        }
      : {
          status: "succeeded",
          check: checkName,
          sessionStatus: String(
            payload?.result?.status ?? payload?.status ?? "unknown",
          ).slice(0, 80),
          checkedAt: payload?.result?.checkedAt ?? payload?.checkedAt ?? null,
          errorCode,
        };
  process.stdout.write(JSON.stringify(summary) + "\n");
} catch (error) {
  process.stdout.write(
    JSON.stringify({ status: "failed", errorCode: safeCode(error?.code) }) + "\n",
  );
  process.exitCode = 1;
}