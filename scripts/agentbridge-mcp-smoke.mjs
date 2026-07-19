import { createAgentBridgeMcpClient } from "../integrations/openclaw-agentbridge/lib/mcp-client.js";

const CHECKS = new Map([
  ["SessionStatus", { tool: "oa_session_status", arguments: {} }],
  ["LoginReuse", { tool: "oa_session_login", arguments: {} }],
]);

const REQUIRED_RELEASE_TOOLS = [
  "oa_business_trip_prepare",
  "oa_business_trip_save_draft",
  "oa_business_trip_submit_prepare",
  "oa_business_trip_submit",
  "oa_leave_prepare",
  "oa_leave_save_draft",
  "oa_leave_submit_prepare",
  "oa_leave_submit",
  "oa_missed_punch_prepare",
  "oa_missed_punch_save_draft",
  "oa_missed_punch_approval_prepare",
  "oa_missed_punch_approve",
  "oa_meeting_create_prepare",
  "oa_meeting_create",
];

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
  if (!check && checkName !== "Release") {
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

  let toolCount = null;
  if (checkName === "Release") {
    const tools = await client.listTools();
    const names = new Set(tools.map((tool) => tool?.name).filter(Boolean));
    const missing = REQUIRED_RELEASE_TOOLS.filter((name) => !names.has(name));
    if (missing.length) {
      throw Object.assign(new Error("Release MCP tool catalog is incomplete"), {
        code: "MCP_TOOL_CATALOG_INCOMPLETE",
      });
    }
    toolCount = tools.length;
  }

  const effectiveCheck = check ?? CHECKS.get("SessionStatus");
  const payload = await client.callTool(effectiveCheck.tool, effectiveCheck.arguments);
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
          toolCount,
          requiredReleaseToolsPresent: checkName === "Release" ? true : null,
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