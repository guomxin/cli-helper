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
  [
    "YuqueSessionStatus",
    { tool: "yuque_session_status", arguments: {}, kind: "session" },
  ],
  [
    "YuquePublicBooks",
    { tool: "yuque_public_books_list", arguments: {}, kind: "list" },
  ],
  [
    "YuqueDocumentCatalog",
    {
      tool: "yuque_document_catalog",
      arguments: { book: "", limit: 20 },
      kind: "list",
    },
  ],
  [
    "YuqueDocumentSearch",
    {
      tool: "yuque_document_search",
      arguments: { query: "AI", book: "", page: 1, limit: 20 },
      kind: "list",
    },
  ],
  [
    "YuqueDocumentRead",
    {
      tool: "yuque_document_read",
      arguments: { document: "", book: "", max_chars: 4000 },
      kind: "yuqueDocument",
    },
  ],
  ["LoginReuse", { tool: "oa_session_login", arguments: {}, kind: "login" }],
  [
    "YuqueLoginReuse",
    { tool: "yuque_session_login", arguments: {}, kind: "login" },
  ],
  [
    "CrossEndpointContext",
    {
      tool: "agentbridge_host_cross_endpoint_context",
      arguments: {
        agent_host: "openclaw",
        endpoint_key: "",
        max_age_minutes: 360,
        limit: 12,
      },
      kind: "crossEndpointContext",
    },
  ],
  [
    "TaskContinuation",
    {
      tool: "agentbridge_host_task_continuation_resolve",
      arguments: {
        agent_host: "openclaw",
        endpoint_key: "",
        ordinal: null,
        source_client_type: "web",
        cross_endpoint_only: true,
        prefer_active: true,
        reuse_selected: false,
        allow_follow_up: false,
        max_age_minutes: 1_440,
        limit: 8,
      },
      kind: "taskContinuation",
    },
  ],
]);

const REQUIRED_RELEASE_TOOLS = [
  "agentbridge_host_task_ensure",
  "agentbridge_host_task_observe",
  "agentbridge_host_task_recovery_list",
  "agentbridge_host_task_list",
  "agentbridge_host_task_continuation_resolve",
  "agentbridge_host_cross_endpoint_context",
  "agentbridge_host_interaction_present",
  "agentbridge_host_notification_claim",
  "agentbridge_host_notification_ack",
  "oa_certificate_search",
  "oa_certificate_prepare_download",
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
  "yuque_public_books_list",
  "yuque_document_catalog",
  "yuque_document_search",
  "yuque_document_read",
  "yuque_session_status",
  "yuque_session_login",
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
    const namesBase64 = argument("--certificate-names-base64", null);
    let names = null;
    if (namesBase64) {
      names = JSON.parse(Buffer.from(namesBase64, "base64").toString("utf8"));
      if (!Array.isArray(names) || names.length < 1 || names.length > 20) {
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
  if (
    ["YuqueDocumentCatalog", "YuqueDocumentSearch", "YuqueDocumentRead"].includes(
      checkName,
    )
  ) {
    check.arguments = {
      ...check.arguments,
      book: argument("--yuque-book", check.arguments.book),
    };
  }
  if (checkName === "YuqueDocumentSearch") {
    check.arguments = {
      ...check.arguments,
      query: argument("--yuque-query", check.arguments.query),
    };
  }
  if (checkName === "YuqueDocumentRead") {
    const document = argument("--yuque-document", "");
    const rowOffset = Number(argument("--yuque-row-offset", "0"));
    const maxRows = Number(argument("--yuque-max-rows", "100"));
    const maxChars = Number(argument("--yuque-max-chars", "4000"));
    if (!document) {
      throw Object.assign(new Error("Yuque document is required"), {
        code: "YUQUE_DOCUMENT_REQUIRED",
      });
    }
    if (!Number.isInteger(maxChars) || maxChars < 500 || maxChars > 50000) {
      throw Object.assign(new Error("Yuque max chars is invalid"), {
        code: "YUQUE_MAX_CHARS_INVALID",
      });
    }
    if (!Number.isInteger(rowOffset) || rowOffset < 0 || rowOffset > 100000) {
      throw Object.assign(new Error("Yuque row offset is invalid"), {
        code: "YUQUE_ROW_OFFSET_INVALID",
      });
    }
    if (!Number.isInteger(maxRows) || maxRows < 1 || maxRows > 500) {
      throw Object.assign(new Error("Yuque max rows is invalid"), {
        code: "YUQUE_MAX_ROWS_INVALID",
      });
    }
    check.arguments = {
      ...check.arguments,
      document,
      row_offset: rowOffset,
      max_rows: maxRows,
      max_chars: maxChars,
    };
  }
  if (["CrossEndpointContext", "TaskContinuation"].includes(checkName)) {
    const endpointKey = argument("--endpoint-key", "").trim();
    if (!endpointKey) {
      throw Object.assign(new Error("Endpoint key is required"), {
        code: "ENDPOINT_KEY_REQUIRED",
      });
    }
    check.arguments = {
      ...check.arguments,
      endpoint_key: endpointKey,
    };
  }
  if (checkName === "TaskContinuation") {
    const ordinal = Number(argument("--task-ordinal", "0"));
    if (!Number.isInteger(ordinal) || ordinal < 0 || ordinal > 20) {
      throw Object.assign(new Error("Task ordinal is invalid"), {
        code: "TASK_ORDINAL_INVALID",
      });
    }
    const sourceClientType = argument(
      "--source-client-type",
      check.arguments.source_client_type,
    ).trim();
    check.arguments = {
      ...check.arguments,
      ordinal: ordinal || null,
      source_client_type: sourceClientType || null,
      cross_endpoint_only: Boolean(sourceClientType),
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
    const payload = await client.callTool(
      effectiveCheck.tool,
      effectiveCheck.arguments,
      ["crossEndpointContext", "taskContinuation"].includes(
        effectiveCheck.kind,
      )
        ? {
            meta: {
              "io.agentbridge/host": {
                version: "1",
                agentHost: "openclaw",
              },
            },
          }
        : undefined,
    );
    const errorCode = payload?.error?.code ? safeCode(payload.error.code) : null;
    const result = payload?.result ?? payload;
    const expectedText = argument("--expected-text", "");
    const summary =
      effectiveCheck.kind === "login"
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
          : effectiveCheck.kind === "yuqueDocument"
          ? yuqueDocumentSummary({
              payload,
              result,
              checkName,
              identityLabel,
              errorCode,
            })
          : effectiveCheck.kind === "crossEndpointContext"
          ? crossEndpointContextSummary({
              payload,
              result,
              checkName,
              identityLabel,
              errorCode,
              expectedText,
            })
          : effectiveCheck.kind === "taskContinuation"
          ? taskContinuationSummary({
              payload,
              result,
              checkName,
              identityLabel,
              errorCode,
            })
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

function taskContinuationSummary({
  payload,
  result,
  checkName,
  identityLabel,
  errorCode,
}) {
  if (payload?.error || errorCode) {
    throw Object.assign(new Error("Task continuation resolution failed"), {
      code: errorCode || "TASK_CONTINUATION_FAILED",
    });
  }
  const status = String(result?.status ?? "unknown").slice(0, 80);
  if (!["selected", "ambiguous"].includes(status)) {
    throw Object.assign(new Error("No task continuation candidate was found"), {
      code: "TASK_CONTINUATION_NOT_FOUND",
    });
  }
  return {
    status: "succeeded",
    check: checkName,
    identityLabel,
    resolutionStatus: status,
    candidateCount: Number(result?.count ?? result?.candidates?.length ?? 0),
    taskId: result?.task?.taskId ?? null,
    taskStatus: result?.task?.status ?? null,
    continuationState: result?.continuation?.state ?? null,
    executionMode: result?.continuation?.executionMode ?? null,
    allowNewOperation: result?.continuation?.allowNewOperation ?? null,
    operationStatus: result?.snapshot?.summary?.operation?.status ?? null,
    interactionState: result?.snapshot?.summary?.interaction?.state ?? null,
    errorCode: null,
  };
}

function crossEndpointContextSummary({
  payload,
  result,
  checkName,
  identityLabel,
  errorCode,
  expectedText,
}) {
  if (payload?.error || errorCode) {
    throw Object.assign(new Error("Cross-endpoint context read failed"), {
      code: errorCode || "CROSS_ENDPOINT_CONTEXT_READ_FAILED",
    });
  }
  const entries = Array.isArray(result?.entries) ? result.entries : [];
  const combinedText = entries.map((entry) => String(entry?.text ?? "")).join("\n");
  if (expectedText && !combinedText.includes(expectedText)) {
    throw Object.assign(new Error("Expected cross-endpoint context was not found"), {
      code: "CROSS_ENDPOINT_CONTEXT_EXPECTED_TEXT_MISSING",
    });
  }
  return {
    status: "succeeded",
    check: checkName,
    identityLabel,
    entryCount: entries.length,
    expectedTextMatched: expectedText ? true : null,
    roles: [...new Set(entries.map((entry) => String(entry?.role ?? "")))].filter(Boolean),
    sourceClientTypes: [
      ...new Set(entries.map((entry) => String(entry?.source?.clientType ?? ""))),
    ].filter(Boolean),
    newestSequence: entries.length
      ? Number(entries.at(-1)?.sequence ?? 0) || null
      : null,
    errorCode: null,
  };
}

function yuqueDocumentSummary({
  payload,
  result,
  checkName,
  identityLabel,
  errorCode,
}) {
  if (payload?.error || errorCode) {
    throw Object.assign(new Error("Yuque document read failed"), {
      code: errorCode || "YUQUE_DOCUMENT_READ_FAILED",
    });
  }
  const content = String(result?.content ?? "");
  const structure = result?.structure ?? {};
  const sheets = Array.isArray(structure?.sheets) ? structure.sheets : [];
  const tables = Array.isArray(structure?.tables) ? structure.tables : [];
  const images = Array.isArray(structure?.images) ? structure.images : [];
  const attachments = Array.isArray(structure?.attachments)
    ? structure.attachments
    : [];
  return {
    status: "succeeded",
    check: checkName,
    identityLabel,
    title: String(result?.document?.title ?? "").slice(0, 300),
    book: String(result?.document?.book?.name ?? "").slice(0, 300),
    contentCharacters: content.length,
    contentFormat: String(result?.contentFormat ?? "").slice(0, 100),
    structureKind: String(structure?.kind ?? "").slice(0, 100),
    sheetCount: sheets.length,
    tableCount: tables.length,
    imageCount: images.length,
    attachmentCount: attachments.length,
    rowsReturned: sheets.reduce(
      (total, sheet) => total + Number(sheet?.returnedRows ?? 0),
      0,
    ),
    hasMoreRows: sheets.some((sheet) => Boolean(sheet?.hasMore)),
    truncated: Boolean(result?.truncated),
    redactionApplied: Boolean(result?.redaction?.applied),
    redactionCount: Number(result?.redaction?.count ?? 0),
    errorCode: null,
  };
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
    matchedQueries: Array.isArray(result?.matched_queries)
      ? result.matched_queries.map(String)
      : [],
    unmatchedQueries: Array.isArray(result?.unmatched_queries)
      ? result.unmatched_queries.map(String)
      : [],
    titles: items.map((item) => String(item?.title ?? "")).filter(Boolean),
    firstDownloadUrl: items[0].download_url,
    expiresAt: items[0].download_expires_at ?? null,
    errorCode: null,
  };
}
