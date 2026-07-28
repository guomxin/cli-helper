import { createAgentBridgeMcpClient } from "../integrations/openclaw-agentbridge/lib/mcp-client.js";

const CHECKS = new Map([
  ["SessionStatus", { tool: "oa_session_status", arguments: {}, kind: "session" }],
  [
    "TaihuaSessionStatus",
    { tool: "taihua_session_status", arguments: {}, kind: "session" },
  ],
  [
    "OaPendingRead",
    { tool: "oa_workflow_pending_list", arguments: { limit: 1 }, kind: "list" },
  ],
  [
    "CertificateSearch",
    {
      tool: "oa_certificate_search",
      arguments: {
        name: "一种基于动态温差补偿的光敏值修正方法及系统",
        document_type: "patent_certificate",
        limit: 5,
      },
      kind: "documentSearch",
    },
  ],
  [
    "TaihuaMyLogs",
    { tool: "taihua_work_log_my_list", arguments: { limit: 1 }, kind: "list" },
  ],
  ["LoginReuse", { tool: "oa_session_login", arguments: {}, kind: "login" }],
]);

const REQUIRED_RELEASE_TOOLS = [
  "oa_certificate_search",
  "oa_business_trip_prepare",
  "oa_business_trip_save_draft",
  "oa_business_trip_submit_prepare",
  "oa_business_trip_submit",
  "oa_leave_prepare",
  "oa_leave_save_draft",
  "oa_leave_submit_prepare",
  "oa_leave_submit",
  "oa_workflow_revoke_prepare",
  "oa_workflow_revoke",
  "oa_missed_punch_prepare",
  "oa_missed_punch_save_draft",
  "oa_missed_punch_approval_prepare",
  "oa_missed_punch_approve",
  "oa_efficiency_data_approval_prepare",
  "oa_efficiency_data_approve",
  "oa_travel_expense_approval_prepare",
  "oa_travel_expense_approve",
  "oa_labor_contract_renewal_approval_prepare",
  "oa_labor_contract_renewal_approve",
  "oa_weekly_report_acknowledgement_prepare",
  "oa_weekly_report_acknowledge",
  "oa_standard_collaboration_approval_prepare",
  "oa_standard_collaboration_approve",
  "oa_meeting_create_prepare",
  "oa_meeting_create",
  "taihua_work_log_my_list",
  "taihua_work_log_team_list",
  "taihua_project_search",
  "taihua_work_log_create_prepare",
  "taihua_work_log_create",
  "taihua_session_status",
  "taihua_session_login",
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
  const identityLabel = argument("--identity-label", null);
  const check = CHECKS.get(checkName);
  if (!check && !["Release", "WorkflowCollections"].includes(checkName)) {
    throw Object.assign(new Error("Unsupported smoke check"), { code: "INVALID_CHECK" });
  }
  if (checkName === "CertificateSearch") {
    const namesJson = argument("--certificate-names-json", null);
    let names = null;
    if (namesJson) {
      names = JSON.parse(namesJson);
      if (!Array.isArray(names) || names.length < 1 || names.length > 10) {
        throw Object.assign(new Error("Invalid certificate names"), {
          code: "INVALID_CERTIFICATE_NAMES",
        });
      }
    }
    check.arguments = {
      ...check.arguments,
      name: names ? null : argument("--certificate-name", check.arguments.name),
      names,
      document_type: argument(
        "--certificate-document-type",
        check.arguments.document_type,
      ),
    };
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

  if (checkName === "WorkflowCollections") {
    const definitions = [
      ["pending", "oa_workflow_pending_list", "section_api"],
      ["sent", "oa_workflow_sent_list", "history_page_grid"],
      ["done", "oa_workflow_done_list", "history_page_grid"],
      ["tracked", "oa_workflow_tracked_list", "tracked_page_grid"],
    ];
    const collections = {};
    const idSets = {};
    for (const [collection, tool, expectedSource] of definitions) {
      const payload = await client.callTool(tool, { limit: 100 });
      if (payload?.error) {
        throw Object.assign(new Error("Workflow collection read failed"), {
          code: payload.error.code || "WORKFLOW_COLLECTION_READ_FAILED",
        });
      }
      const result = payload?.result ?? payload;
      if (result?.collection !== collection || result?.source !== expectedSource) {
        throw Object.assign(new Error("Workflow collection contract mismatch"), {
          code: "WORKFLOW_COLLECTION_CONTRACT_MISMATCH",
        });
      }
      const ids = (result?.items ?? [])
        .map((item) => String(item?.affair_id ?? ""))
        .filter(Boolean);
      idSets[collection] = new Set(ids);
      collections[collection] = {
        source: result.source,
        loaded: Number(result.count ?? ids.length),
        total: Number(result.total ?? ids.length),
        page: Number(result.page ?? 1),
      };
    }
    const overlap = (left, right) =>
      [...idSets[left]].filter((value) => idSets[right].has(value)).length;
    process.stdout.write(
      JSON.stringify({
        status: "succeeded",
        check: checkName,
        collections,
        overlaps: {
          sentDone: overlap("sent", "done"),
          sentTracked: overlap("sent", "tracked"),
          doneTracked: overlap("done", "tracked"),
        },
      }) + "\n",
    );
  } else {
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
    const result = payload?.result ?? payload;
    const summary =
      checkName === "LoginReuse"
        ? {
            status: "succeeded",
            check: checkName,
            identityLabel,
            operationStatus: String(payload?.status ?? "unknown").slice(0, 80),
            reused: Boolean(payload?.reused),
            hasInteraction: Boolean(payload?.interaction),
            nextAction: payload?.nextAction?.type
              ? String(payload.nextAction.type).slice(0, 80)
              : null,
            errorCode,
          }
        : effectiveCheck.kind === "documentSearch"
          ? certificateSearchSummary({
              payload,
              result,
              checkName,
              identityLabel,
              errorCode,
            })
          : effectiveCheck.kind === "list"
          ? {
              status: "succeeded",
              check: checkName,
              identityLabel,
              itemCount: Number(result?.count ?? result?.items?.length ?? 0),
              total: Number(result?.total ?? result?.count ?? result?.items?.length ?? 0),
              errorCode,
            }
          : {
              status: "succeeded",
              check: checkName,
              identityLabel,
              toolCount,
              requiredReleaseToolsPresent: checkName === "Release" ? true : null,
              sessionStatus: String(result?.status ?? "unknown").slice(0, 80),
              sessionId: result?.sessionId ?? null,
              systemId: result?.systemId ?? null,
              userSubject: result?.userSubject ?? null,
              downstreamPrincipalRef: result?.downstreamPrincipalRef ?? null,
              keepaliveState: result?.keepaliveState ?? null,
              lastActivityAt: result?.lastActivityAt ?? null,
              lastKeepaliveAt: result?.lastKeepaliveAt ?? null,
              checkedAt: result?.checkedAt ?? payload?.checkedAt ?? null,
              errorCode,
            };
    process.stdout.write(JSON.stringify(summary) + "\n");
  }
} catch (error) {
  process.stdout.write(
    JSON.stringify({ status: "failed", errorCode: safeCode(error?.code) }) + "\n",
  );
  process.exitCode = 1;
}

function certificateSearchSummary({
  payload,
  result,
  checkName,
  identityLabel,
  errorCode,
}) {
  if (payload?.error || errorCode) {
    throw Object.assign(new Error("Certificate search failed"), {
      code: errorCode || "CERTIFICATE_SEARCH_FAILED",
    });
  }
  const items = Array.isArray(result?.items) ? result.items : [];
  if (!items.length) {
    throw Object.assign(new Error("Certificate search returned no matches"), {
      code: "CERTIFICATE_SEARCH_EMPTY",
    });
  }
  for (const item of items) {
    if (
      "_download_reference" in item ||
      "resource_id" in item ||
      "source_id" in item
    ) {
      throw Object.assign(new Error("Certificate search leaked OA identifiers"), {
        code: "CERTIFICATE_SEARCH_IDENTIFIER_LEAK",
      });
    }
    if (!String(item?.download_url ?? "").startsWith("https://")) {
      throw Object.assign(new Error("Certificate search download URL is invalid"), {
        code: "CERTIFICATE_SEARCH_URL_INVALID",
      });
    }
  }
  return {
    status: "succeeded",
    check: checkName,
    identityLabel,
    matchCount: items.length,
    exactMatches: items.filter((item) => item?.match_kind === "exact").length,
    titles: items.map((item) => String(item?.title ?? "")).filter(Boolean),
    firstDownloadUrl: items[0].download_url,
    expiresAt: items[0].download_expires_at ?? null,
    errorCode: null,
  };
}
