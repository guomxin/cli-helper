// Read descriptors and presentation only; central ledger owns task recovery.
export const LOGIN_CONTINUATION_TTL_MS = 5 * 60 * 1000;
export const LOGIN_READ_TOOLS = new Map([
  [
    "oa_workflow_pending_list",
    { kind: "oa_workflow", collection: "pending", system: "OA", label: "待办" },
  ],
  [
    "oa_workflow_sent_list",
    { kind: "oa_workflow", collection: "sent", system: "OA", label: "已发" },
  ],
  [
    "oa_workflow_done_list",
    { kind: "oa_workflow", collection: "done", system: "OA", label: "已办" },
  ],
  [
    "oa_workflow_tracked_list",
    { kind: "oa_workflow", collection: "tracked", system: "OA", label: "跟踪事项" },
  ],
  [
    "taihua_work_log_my_list",
    { kind: "taihua_work_log", system: "泰华日志系统", label: "我的工作日志" },
  ],
  [
    "taihua_work_log_team_list",
    { kind: "taihua_work_log", system: "泰华日志系统", label: "团队工作日志" },
  ],
  [
    "taihua_project_search",
    { kind: "taihua_project", system: "泰华日志系统", label: "项目" },
  ],
  [
    "smartlight_system_overview",
    { kind: "smartlight_overview", system: "照明实验室测试系统", label: "系统概览" },
  ],
  [
    "smartlight_runtime_overview",
    { kind: "smartlight_runtime", system: "照明实验室测试系统", label: "运行概览" },
  ],
  [
    "smartlight_rtu_status_list",
    { kind: "smartlight_rtu_status", system: "照明实验室测试系统", label: "RTU 运行状态" },
  ],
  [
    "smartlight_lamp_status_list",
    { kind: "smartlight_lamp_status", system: "照明实验室测试系统", label: "单灯运行状态" },
  ],
  [
    "smartlight_lamp_alarm_list",
    { kind: "smartlight_lamp_alarm", system: "照明实验室测试系统", label: "单灯告警" },
  ],
  [
    "smartlight_lamp_alarm_analysis",
    { kind: "smartlight_lamp_alarm", system: "照明实验室测试系统", label: "单灯告警分析" },
  ],
  [
    "smartlight_rtu_survey_records",
    { kind: "smartlight_rtu_survey", system: "照明实验室测试系统", label: "RTU 巡测记录" },
  ],
  [
    "smartlight_energy_record_list",
    { kind: "smartlight_energy", system: "照明实验室测试系统", label: "RTU 用电记录" },
  ],
  [
    "smartlight_energy_analysis",
    { kind: "smartlight_energy", system: "照明实验室测试系统", label: "RTU 用电分析" },
  ],
  [
    "smartlight_lamp_survey_records",
    { kind: "smartlight_lamp_survey", system: "照明实验室测试系统", label: "单灯巡测记录" },
  ],
  [
    "smartlight_rtu_leakage_alarm_list",
    { kind: "smartlight_rtu_leakage", system: "照明实验室测试系统", label: "RTU 支路漏电报警" },
  ],
  [
    "smartlight_rtu_leakage_analysis",
    { kind: "smartlight_rtu_leakage", system: "照明实验室测试系统", label: "RTU 支路漏电分析" },
  ],
  [
    "smartlight_off_hours_current_list",
    { kind: "smartlight_off_hours_current", system: "照明实验室测试系统", label: "关灯时段电流" },
  ],
  [
    "smartlight_inspection_log_list",
    { kind: "smartlight_inspection_log", system: "照明实验室测试系统", label: "巡检日志统计" },
  ],
  [
    "smartlight_maintenance_record_list",
    { kind: "smartlight_maintenance", system: "照明实验室测试系统", label: "检修记录" },
  ],
  [
    "smartlight_lamppost_list",
    { kind: "smartlight_lamppost", system: "照明实验室测试系统", label: "灯杆" },
  ],
  [
    "smartlight_alarm_list",
    { kind: "smartlight_alarm", system: "照明实验室测试系统", label: "RTU 告警" },
  ],
  [
    "smartlight_alarm_remark_get",
    { kind: "smartlight_alarm", system: "照明实验室测试系统", label: "RTU 告警备注" },
  ],
  [
    "smartlight_inspection_task_list",
    { kind: "smartlight_inspection", system: "照明实验室测试系统", label: "巡检任务" },
  ],
  [
    "smartlight_leakage_summary",
    { kind: "smartlight_lamp_alarm", system: "照明实验室测试系统", label: "单灯告警（兼容入口）" },
  ],
  [
    "smartlight_asset_search",
    { kind: "smartlight_asset", system: "照明实验室测试系统", label: "设施查询" },
  ],
  [
    "smartlight_asset_detail",
    { kind: "smartlight_asset", system: "照明实验室测试系统", label: "设施详情" },
  ],
  [
    "smartlight_alarm_analysis",
    { kind: "smartlight_alarm", system: "照明实验室测试系统", label: "RTU 告警分析" },
  ],
  [
    "smartlight_inspection_task_detail",
    { kind: "smartlight_inspection", system: "照明实验室测试系统", label: "巡检任务详情" },
  ],
  [
    "smartlight_leakage_analysis",
    { kind: "smartlight_lamp_alarm", system: "照明实验室测试系统", label: "单灯告警分析（兼容入口）" },
  ],
  [
    "smartlight_report_export",
    { kind: "smartlight_report", system: "照明实验室测试系统", label: "CSV 报告" },
  ],
  [
    "yuque_public_books_list",
    { kind: "yuque_book", system: "部门信息库", label: "公共区知识库" },
  ],
  [
    "yuque_document_catalog",
    { kind: "yuque_document_list", system: "部门信息库", label: "文档目录" },
  ],
  [
    "yuque_document_search",
    { kind: "yuque_document_list", system: "部门信息库", label: "搜索结果" },
  ],
  [
    "yuque_document_read",
    { kind: "yuque_document", system: "部门信息库", label: "文档正文" },
  ],
]);

export function normalizeReadContinuation(toolName, params, capturedAt) {
  const normalizedToolName = String(toolName || "").trim();
  const descriptor = LOGIN_READ_TOOLS.get(normalizedToolName);
  if (!descriptor) {
    return null;
  }
  const source =
    params && typeof params === "object" && !Array.isArray(params) ? params : {};
  const arguments_ = {};
  if (typeof source.keyword === "string" && source.keyword.trim()) {
    arguments_.keyword = source.keyword.trim().slice(0, 200);
  }
  const maximumLimit =
    descriptor.kind === "taihua_project" ||
    normalizedToolName === "yuque_document_search"
      ? 50
      : descriptor.kind === "oa_workflow"
        ? (["oa_workflow_done_list", "oa_workflow_sent_list"].includes(normalizedToolName) ? 1000 : 100)
        : 500;
  if (
    Number.isInteger(source.limit) &&
    source.limit >= 1 &&
    source.limit <= maximumLimit
  ) {
    arguments_.limit = source.limit;
  }
  if (descriptor.kind === "taihua_work_log") {
    for (const name of ["log_date", "start_date", "end_date"]) {
      if (
        typeof source[name] === "string" &&
        /^\d{4}-\d{2}-\d{2}$/.test(source[name])
      ) {
        arguments_[name] = source[name];
      }
    }
    for (const name of ["member", "department", "watch_group"]) {
      if (typeof source[name] === "string" && source[name].trim()) {
        arguments_[name] = source[name].trim().slice(0, 200);
      }
    }
    for (const [name, maximum] of [
      ["page", 10000],
      ["size", 100],
    ]) {
      if (Number.isInteger(source[name]) && source[name] >= 1 && source[name] <= maximum) {
        arguments_[name] = source[name];
      }
    }
    if (["submittedAt", "logDate"].includes(source.view_mode)) {
      arguments_.view_mode = source.view_mode;
    }
    for (const name of ["dept_id", "member_id", "watch_group_id"]) {
      if (Number.isInteger(source[name]) && source[name] >= 1) {
        arguments_[name] = source[name];
      }
    }
  }
  if (["oa_workflow_done_list", "oa_workflow_sent_list"].includes(normalizedToolName)) {
    for (const name of ["start_date", "end_date"]) {
      if (typeof source[name] === "string" && /^\d{4}-\d{2}-\d{2}$/.test(source[name])) {
        arguments_[name] = source[name];
      }
    }
  }
  if (descriptor.kind.startsWith("yuque_")) {
    for (const name of ["book", "query", "document"]) {
      if (typeof source[name] === "string" && source[name].trim()) {
        arguments_[name] = source[name].trim().slice(0, 500);
      }
    }
    if (
      Number.isInteger(source.page) &&
      source.page >= 1 &&
      source.page <= 1000
    ) {
      arguments_.page = source.page;
    }
    if (
      Number.isInteger(source.max_chars) &&
      source.max_chars >= 500 &&
      source.max_chars <= 50000
    ) {
      arguments_.max_chars = source.max_chars;
    }
  }
  return Object.freeze({
    toolName: normalizedToolName,
    arguments: Object.freeze(arguments_),
    capturedAt,
  });
}

export function inferReadContinuation(text, capturedAt) {
  const value = String(text || "");
  const candidates = [
    ["团队日志", "taihua_work_log_team_list"],
    ["我的日志", "taihua_work_log_my_list"],
    ["泰华项目", "taihua_project_search"],
    ["\u5f85\u529e", "oa_workflow_pending_list"],
    ["\u5df2\u53d1", "oa_workflow_sent_list"],
    ["\u5df2\u529e", "oa_workflow_done_list"],
    ["\u8ddf\u8e2a", "oa_workflow_tracked_list"],
  ];
  const matched = candidates.find(([keyword]) => value.includes(keyword));
  if (!matched) {
    return null;
  }
  if (
    ["taihua_work_log_team_list", "taihua_work_log_my_list"].includes(
      matched[1],
    ) &&
    /(?:填写|填报|新建|创建|记录|修改|更新|保存|提交)[^。！？\n]{0,20}(?:工作)?日志|(?:写|填)[^。！？\n]{0,12}(?:工作)?日志/u.test(
      value,
    )
  ) {
    return null;
  }
  const arguments_ = {};
  const limitMatch = value.match(/(?:\u8fd1|\u524d)?\s*(\d{1,3})\s*\u6761/);
  if (limitMatch) {
    const limit = Number.parseInt(limitMatch[1], 10);
    if (limit >= 1 && limit <= 100) {
      arguments_.limit = limit;
    }
  }
  return normalizeReadContinuation(matched[1], arguments_, capturedAt);
}

export function isFreshContinuation(continuation, now) {
  return Boolean(
    continuation &&
      Number.isFinite(continuation.capturedAt) &&
      now - continuation.capturedAt <= LOGIN_CONTINUATION_TTL_MS,
  );
}

export function isLoginRequiredPayload(payload) {
  return Boolean(
    payload &&
      typeof payload === "object" &&
      !Array.isArray(payload) &&
      (payload?.error?.code === "LOGIN_REQUIRED" ||
        payload?.nextAction?.type === "session_login"),
  );
}

export function loginToolForReadTool(toolName) {
  const normalized = String(toolName || "");
  if (normalized.startsWith("oa_")) return "oa_session_login";
  if (normalized.startsWith("taihua_")) return "taihua_session_login";
  if (normalized.startsWith("yuque_")) return "yuque_session_login";
  if (normalized.startsWith("smartlight_")) {
    return "smartlight_session_login";
  }
  return null;
}

export function formatReadContinuation(continuation, response) {
  const descriptor = LOGIN_READ_TOOLS.get(continuation.toolName);
  const result =
    response?.result && typeof response.result === "object"
      ? response.result
      : {};
  if (descriptor.kind === "yuque_document") {
    const title = safeDisplayText(result?.document?.title, 300) || "(未命名文档)";
    const book = safeDisplayText(result?.document?.book?.name, 160);
    const content = safeDisplayText(result?.content, 3200) || "(正文为空)";
    const suffix = result?.truncated === true ? "\n\n正文已按安全上限截断。" : "";
    return [
      `${descriptor.system} 登录已恢复，已自动继续读取文档：${title}`,
      book ? `知识库：${book}` : "",
      content + suffix,
    ]
      .filter(Boolean)
      .join("\n\n");
  }
  const items = Array.isArray(result.items) ? result.items : [];
  const count = Number.isInteger(result.count) ? result.count : items.length;
  const lines = [
    `${descriptor.system} 登录已恢复，已自动继续读取${descriptor.label}，共 ${count} 条：`,
  ];
  if (items.length === 0) {
    return lines.join("\n");
  }
  let shown = 0;
  for (const item of items) {
    const block = formatReadItem(descriptor, item, shown + 1);
    if ([...lines, block].join("\n\n").length > 3500) {
      break;
    }
    lines.push(block);
    shown += 1;
  }
  if (shown < items.length) {
    lines.push(`其余 ${items.length - shown} 条未在本条消息中展开。`);
  }
  return lines.join("\n\n");
}

function formatReadItem(descriptor, item, index) {
  if (descriptor.kind === "yuque_book") {
    const name = safeDisplayText(item?.name, 300) || "(未命名知识库)";
    const count = Number.isInteger(item?.documentCount)
      ? `${item.documentCount} 篇`
      : "";
    const description = safeDisplayText(item?.description, 500);
    return [
      `${index}. ${name}`,
      count ? `   ${count}` : "",
      description ? `   ${description}` : "",
    ]
      .filter(Boolean)
      .join("\n");
  }
  if (descriptor.kind === "yuque_document_list") {
    const title = safeDisplayText(item?.title, 300) || "(未命名文档)";
    const type = safeDisplayText(item?.type, 80);
    const book = safeDisplayText(item?.book?.name, 160);
    const slug = safeDisplayText(item?.slug, 160);
    return [
      `${index}. ${title}`,
      [book, type].filter(Boolean).join(" | ")
        ? `   ${[book, type].filter(Boolean).join(" | ")}`
        : "",
      slug ? `   文档标识：${slug}` : "",
    ]
      .filter(Boolean)
      .join("\n");
  }
  if (descriptor.kind === "taihua_work_log") {
    const date = safeDisplayText(item?.logDate, 40);
    const person = safeDisplayText(item?.fullname || item?.username, 120);
    const hours = Number.isFinite(Number(item?.hours)) ? `${item.hours} 小时` : "";
    const project = safeDisplayText(item?.projectName, 200);
    const metadata = [date, person, hours, project].filter(Boolean).join(" | ");
    const content = safeDisplayText(item?.content, 1000) || "(无日志内容)";
    return [`${index}. ${metadata}`.trim(), `   ${content}`].join("\n");
  }
  if (descriptor.kind === "taihua_project") {
    const name = safeDisplayText(item?.name, 300) || "(未命名项目)";
    const code = safeDisplayText(item?.code, 120);
    const status = safeDisplayText(item?.status, 120);
    const metadata = [code, status].filter(Boolean).join(" | ");
    return [`${index}. ${name}`, metadata ? `   ${metadata}` : ""]
      .filter(Boolean)
      .join("\n");
  }

  const date = safeDisplayText(item?.date, 40);
  const title = safeDisplayText(item?.title, 300) || "(untitled)";
  const affairId = safeDisplayText(item?.affair_id, 160);
  let metadata;
  if (descriptor.collection === "pending") {
    const readState = item?.read ? "已读" : "未读";
    const sender = safeDisplayText(item?.sender, 120);
    metadata = [`[${readState}]`, date, sender].filter(Boolean).join(" | ");
  } else {
    const status = safeDisplayText(item?.status, 120);
    metadata = [date, status].filter(Boolean).join(" | ");
  }
  return [
    `${index}. ${metadata}`.trim(),
    `   ${title}`,
    affairId ? `   affair_id: ${affairId}` : "",
  ]
    .filter(Boolean)
    .join("\n");
}

function safeDisplayText(value, limit) {
  return String(value ?? "")
    .replace(/[\u0000-\u001f\u007f]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, limit);
}
