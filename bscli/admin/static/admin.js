"use strict";

const state = { account: null, view: "overview", modalAction: null, coordinationTab: "tasks" };
const titles = {
  overview: ["CONTROL PLANE", "运行总览"],
  users: ["IDENTITY", "用户与令牌"],
  sessions: ["DOWNSTREAM", "系统会话"],
  capabilities: ["GOVERNANCE", "能力与策略"],
  operations: ["EXECUTION", "操作记录"],
  interactions: ["TRUSTED UX", "可信交互"],
  traces: ["END TO END", "链路追踪"],
  incidents: ["RELIABILITY", "事件治理"],
  coordination: ["TASK HUB", "多端任务"],
  runtime: ["RUNTIME", "系统运行"],
  audit: ["ADMIN AUDIT", "管理审计"],
};
const statusText = {
  active: "有效", inactive: "未活动", revoked: "已撤销", expired: "已过期", quarantined: "已隔离",
  awaiting_login: "待登录", new: "未登录", succeeded: "成功", failed: "失败", completed: "已完成",
  unknown: "结果未知", outcome_unknown: "结果未知", requires_user_action: "用户交互节点", waiting_user: "等待用户",
  awaiting_user: "当前等待用户", user_action_completed: "用户已处理", resumed: "已续办",
  user_action_expired: "交互已过期", user_action_rejected: "用户已拒绝", user_action_superseded: "已被替换",
  user_action_failed: "交互失败", user_action_handoff: "已转交用户",
  running: "执行中", canceled: "已取消", cancelled: "已取消", pending: "待处理", delivering: "投递中", deferred: "等待端点活动", acknowledged: "已送达",
  submitted: "已填写", approved: "已授权", rejected: "已拒绝", consumed: "已使用", superseded: "已替换",
  paused: "已暂停", available: "可用", selected: "已选择", awaiting_selection: "待选择",
  observe_only: "只读接续", resume: "恢复执行", follow_up: "后续操作", pull: "网页拉取", direct: "聊天直推",
  eligible: "保活中", outside_lease: "未保活（超期）", activity_unknown: "活动未知", not_configured: "未配置",
  ready: "同步就绪", waiting_activity: "等待微信活动",
  waiting: "等待中", open: "待处理", investigating: "调查中", resolved: "已解决", suppressed: "已抑制", archived: "已归档",
  healthy: "健康", unavailable: "不可用", meeting: "达标", breached: "未达标", insufficient_data: "数据不足",
};
const statusClass = value => ["active", "succeeded", "approved", "submitted", "completed", "acknowledged", "eligible", "selected", "available", "healthy", "meeting", "resolved", "archived"].includes(value) ? "ok" :
  ["failed", "unknown", "outcome_unknown", "expired", "quarantined", "revoked", "rejected", "user_action_failed", "unavailable", "breached"].includes(value) ? "bad" :
  ["pending", "delivering", "deferred", "waiting_activity", "awaiting_login", "awaiting_user", "waiting_user", "waiting", "paused", "awaiting_selection", "outside_lease", "user_action_expired", "user_action_rejected", "open", "acknowledged", "investigating"].includes(value) ? "warn" :
  ["running", "resume", "follow_up", "pull", "direct"].includes(value) ? "info" : "neutral";
const scopeGroups = [
  { label: "致远 OA", items: ["oa:read", "oa:write:draft", "oa:write:approval", "oa:write:meeting", "oa:write:submit", "oa:write:revoke"] },
  { label: "泰华日志", items: ["taihua:read", "taihua:write:worklog"] },
  { label: "部门信息库", items: ["yuque:read"] },
  { label: "照明实验室", items: ["smartlight:read", "smartlight:write:alarm_remark", "smartlight:write:alarm_work_area_submit", "smartlight:write:alarm_work_area_revoke", "smartlight:write:alarm_disposition"] },
];

const $ = selector => document.querySelector(selector);
const content = $("#content");
const modal = $("#modal");

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  })[character]);
}
function fmtTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? escapeHtml(value) : date.toLocaleString("zh-CN", { hour12: false });
}
function fmtBytes(value) {
  const size = Number(value || 0);
  if (!Number.isFinite(size) || size <= 0) return "--";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}
function fmtDuration(value) {
  const duration = Number(value || 0);
  if (!Number.isFinite(duration) || duration <= 0) return "--";
  if (duration < 1000) return `${Math.round(duration)} ms`;
  if (duration < 60000) return `${(duration / 1000).toFixed(1)} 秒`;
  return `${(duration / 60000).toFixed(1)} 分`;
}
function fmtMetric(metric) {
  if (metric.value == null) return "--";
  if (metric.metricKey.endsWith("_rate") || metric.metricKey.endsWith("_coverage")) return `${(metric.value * 100).toFixed(1)}%`;
  if (metric.metricKey.endsWith("_ms")) return fmtDuration(metric.value);
  return Number(metric.value).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}
function boundaryLabel(value) {
  const labels = { B0_NO_EFFECT: "B0 无副作用", B1_READ_ONLY: "B1 只读", B2_INTERACTION_CREATED: "B2 已创建交互", B3_PREPARED_AUTHORIZED: "B3 已准备/授权", B4_COMMIT_ATTEMPTED: "B4 已尝试提交", B5_VERIFIED: "B5 已权威核验" };
  return labels[value] || value || "--";
}
function shortId(value) { return value ? `${escapeHtml(value.slice(0, 8))}…` : "--"; }
function badge(value) { return `<span class="status ${statusClass(value)}">${escapeHtml(statusText[value] || value || "未知")}</span>`; }
function sessionStateBadge(session) {
  if (session.state === "active" && session.session_state_basis === "last_confirmed") {
    return `<span class="status warn" title="当前没有实时校验；这是最后一次确认成功时保存的状态">上次确认有效</span>`;
  }
  return badge(session.state);
}
function empty(message) { return `<div class="empty">${escapeHtml(message)}</div>`; }
function table(headers, rows, filterable = false) {
  if (!rows.length) return empty("暂无记录");
  return `<div class="table-shell"><table${filterable ? " data-filter-table" : ""}><thead><tr>${headers.map(item => `<th>${escapeHtml(item)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}
function filterRow(searchText, status, cells) {
  return `<tr data-filter-text="${escapeHtml(String(searchText).toLowerCase())}" data-filter-status="${escapeHtml(status || "")}">${cells}</tr>`;
}
function filteredTable(headers, rows, placeholder, statuses = []) {
  if (!rows.length) return empty("暂无记录");
  const options = statuses.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(statusText[value] || value)}</option>`).join("");
  return `<section class="data-block" data-filter-scope><div class="filters"><label class="filter-field"><span>搜索</span><input type="search" data-filter-search placeholder="${escapeHtml(placeholder)}"></label>${statuses.length ? `<label class="filter-field compact"><span>状态</span><select data-filter-status><option value="">全部状态</option>${options}</select></label>` : ""}<span class="filter-count" data-filter-count>${rows.length} 条</span></div>${table(headers, rows, true)}<div class="filter-empty hidden" data-filter-empty>没有匹配记录</div></section>`;
}
function applyFilter(control) {
  const scope = control.closest("[data-filter-scope]");
  if (!scope) return;
  const search = (scope.querySelector("[data-filter-search]")?.value || "").trim().toLowerCase();
  const status = scope.querySelector("[data-filter-status]")?.value || "";
  let visible = 0;
  scope.querySelectorAll("tbody tr").forEach(row => {
    const matches = (!search || row.dataset.filterText.includes(search)) && (!status || row.dataset.filterStatus === status);
    row.classList.toggle("hidden", !matches);
    if (matches) visible += 1;
  });
  const count = scope.querySelector("[data-filter-count]");
  if (count) count.textContent = `${visible} 条`;
  scope.querySelector("[data-filter-empty]")?.classList.toggle("hidden", visible !== 0);
}
function scopeBadges(scopes) { return `<div class="scope-pills">${scopes.map(scope => `<span>${escapeHtml(scope)}</span>`).join("")}</div>`; }
function csrfToken() {
  const prefix = "agentbridge_admin_csrf=";
  const item = document.cookie.split(";").map(value => value.trim()).find(value => value.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : "";
}
async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = { ...(options.headers || {}) };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-AgentBridge-CSRF"] = csrfToken();
  }
  const response = await fetch(path, { ...options, method, headers, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error?.message || `请求失败 (${response.status})`);
    error.code = data.error?.code || "REQUEST_FAILED";
    error.status = response.status;
    throw error;
  }
  return data;
}
function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.toggle("error", error);
  node.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.add("hidden"), 3600);
}
function showLogin() {
  $("#login-view").classList.remove("hidden");
  $("#app").classList.add("hidden");
  state.account = null;
}
function showApp(account) {
  state.account = account;
  $("#login-view").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#account-name").textContent = account.username;
  $("#account-initial").textContent = account.username.slice(0, 1).toUpperCase();
  $("#password-banner").classList.toggle("hidden", !account.must_change_password);
}
async function initialize() {
  try {
    const session = await api("/api/session");
    if (!session.authenticated) { showLogin(); return; }
    showApp(session.account);
    if (session.account.must_change_password) openPasswordModal(true);
    else await loadView(state.view);
  } catch (error) { showLogin(); }
}

async function loadView(view) {
  if (!state.account || state.account.must_change_password) return;
  state.view = view;
  document.querySelectorAll("#nav button").forEach(button => button.classList.toggle("active", button.dataset.view === view));
  $("#view-kicker").textContent = titles[view][0];
  $("#view-title").textContent = titles[view][1];
  content.innerHTML = '<div class="loading">正在读取中心状态</div>';
  try {
    const renderers = {
      overview: renderOverview, users: renderUsers, sessions: renderSessions, capabilities: renderCapabilities,
      operations: renderOperations, interactions: renderInteractions, traces: renderTraces,
      incidents: renderIncidents, coordination: renderCoordination, runtime: renderRuntime, audit: renderAudit,
    };
    await renderers[view]();
    $("#freshness").textContent = `刷新于 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
  } catch (error) {
    if (error.status === 401) { showLogin(); return; }
    if (error.code === "PASSWORD_CHANGE_REQUIRED") { openPasswordModal(true); return; }
    content.innerHTML = empty(`读取失败：${error.message}`);
  }
}

function metric(label, value, hint, extra = "") { return `<div class="metric ${extra}"><span class="label">${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><span class="hint">${escapeHtml(hint)}</span></div>`; }
async function renderOverview() {
  const data = await api("/api/overview");
  $("#sidebar-release").textContent = data.runtime.release_id;
  const summary = data.summary;
  const attention = summary.failed_operations_24h + summary.paused_policies + summary.isolation_violations + summary.critical_incidents;
  content.innerHTML = `
    <div class="metric-grid">
      ${metric("接入用户", summary.users, `${summary.active_tokens} 个有效令牌`)}
      ${metric("活动会话", summary.active_sessions, "下游登录状态")}
      ${metric("活动任务", summary.active_tasks, `${summary.waiting_tasks} 个等待用户`)}
      ${metric("活动端点", summary.active_endpoints, "网页与聊天通道")}
      ${metric("待投递", summary.outstanding_deliveries, "尚未确认送达", summary.outstanding_deliveries ? "alert" : "")}
      ${metric("24h 异常", summary.failed_operations_24h, `${summary.operations_24h} 次操作`, summary.failed_operations_24h ? "alert" : "")}
      ${metric("开放事件", summary.open_incidents, `${summary.critical_incidents} 个 P0/P1`, summary.critical_incidents ? "alert" : "")}
      ${metric("治理约束", summary.paused_policies + summary.isolation_violations, `写暂停 ${summary.paused_policies} / 隔离异常 ${summary.isolation_violations}`, summary.paused_policies || summary.isolation_violations ? "alert" : "")}
    </div>
    <div class="health-strip ${attention ? "attention" : "healthy"}"><div><strong>${attention ? "存在需要管理员关注的运行项" : "核心运行状态正常"}</strong><span>${attention ? "请查看事件治理、操作记录与隔离完整性。" : "任务隔离、写策略和近 24 小时执行未发现异常。"}</span></div><button class="button secondary small" data-open-view="incidents">查看事件治理</button></div>
    <div class="split">
      <section class="panel"><div class="panel-head"><h3>系统状态</h3><span>按中心会话注册表统计</span></div><div class="system-list">${data.systems.map(system => `
        <div class="system-row"><div class="system-name"><strong>${escapeHtml(system.label)}</strong><span>${escapeHtml(system.system_id)}</span></div><div class="system-stat"><strong>${system.active_sessions}</strong><span>活动</span></div><div class="system-stat"><strong>${system.attention_sessions}</strong><span>需关注</span></div><div class="system-stat"><strong>${system.total_sessions}</strong><span>总计</span></div></div>`).join("")}</div></section>
      <section class="panel"><div class="panel-head"><h3>生效中的写暂停</h3><span>${data.paused_policies.length} 条</span></div>${data.paused_policies.length ? `<div class="system-list">${data.paused_policies.map(policy => `<div class="system-row policy-row"><div class="system-name"><strong>${escapeHtml(policy.scope_type)} · ${escapeHtml(policy.scope_value)}</strong><span>${escapeHtml(policy.reason)}</span></div>${badge(policy.state)}</div>`).join("")}</div>` : empty("没有生效中的写暂停")}</section>
    </div>
    <div class="view-head section-spaced-sm"><div><h2>最近操作</h2><p>只展示运行元数据，不展示业务字段和值。</p></div></div>${operationTable(data.recent_operations, false)}`;
}

function principalBindingSummary(user) {
  return Object.entries(user.principal_bindings || {}).map(([system, binding]) => `${escapeHtml(system)}: ${escapeHtml(binding.verified || binding.expected || "--")}`).join("<br>") || "--";
}
async function renderUsers() {
  const [users, tokens] = await Promise.all([api("/api/users"), api("/api/tokens")]);
  const userRows = users.items.map(user => filterRow(`${user.user_subject} ${Object.values(user.principal_bindings || {}).flatMap(value => [value.expected, value.verified]).join(" ")}`, "", `<td><strong>${escapeHtml(user.user_subject)}</strong></td><td>${principalBindingSummary(user)}</td><td>${user.active_token_count} / ${user.token_count}</td><td>${Object.entries(user.sessions).map(([system, value]) => `${escapeHtml(system)} ${badge(value)}`).join(" ") || "--"}</td><td><div class="actions">${state.account.role === "admin" ? `<button class="button secondary small" data-pause-user="${escapeHtml(user.user_subject)}">暂停写入</button>` : ""}</div></td>`));
  const tokenRows = tokens.items.map(token => filterRow(`${token.token_id} ${token.label} ${token.user_subject} ${token.scopes.join(" ")}`, token.state, `<td class="code">${shortId(token.token_id)}</td><td>${escapeHtml(token.label || "未命名")}</td><td>${escapeHtml(token.user_subject)}</td><td>${scopeBadges(token.scopes)}</td><td>${badge(token.state)}</td><td>${fmtTime(token.expires_at)}</td><td><div class="actions">${state.account.role === "admin" && token.state === "active" ? `<button class="button secondary small" data-revoke-token="${token.token_id}">撤销</button>` : ""}</div></td>`));
  content.innerHTML = `<div class="toolbar"><div><strong>身份绑定</strong><div class="muted">令牌密钥只在签发时显示一次；各系统主体独立绑定。</div></div>${state.account.role === "admin" ? '<button class="button primary" data-issue-token>签发令牌</button>' : ""}</div>
    ${filteredTable(["用户标识", "各系统下游主体", "有效 / 全部令牌", "系统会话", ""], userRows, "搜索用户或下游主体")}
    <div class="view-head section-spaced"><div><h2>MCP Token</h2><p>一个 Token 可以承载多个系统的能力范围；管理员看不到已签发密钥。</p></div></div>
    ${filteredTable(["Token ID", "标签", "用户", "权限范围", "状态", "到期时间", ""], tokenRows, "搜索 Token、用户或权限", ["active", "expired", "revoked"])} `;
}

async function renderSessions() {
  const data = await api("/api/sessions");
  const rows = data.items.map(session => {
    const latest = session.latest_event || {};
    const latestSummary = [latest.source, latest.reason].filter(Boolean).join(" · ");
    return filterRow(`${session.user_subject} ${session.system_id} ${session.expected_principal_ref} ${session.downstream_principal_ref} ${session.last_error} ${latestSummary}`, session.state, `<td>${escapeHtml(session.user_subject)}</td><td>${escapeHtml(session.system_id)}</td><td>${escapeHtml(session.expected_principal_ref || "--")}</td><td>${escapeHtml(session.downstream_principal_ref || "--")}</td><td>${sessionStateBadge(session)}</td><td title="${escapeHtml(session.keepalive_explanation || "")}">${badge(session.keepalive_state || "not_configured")}</td><td>${fmtTime(session.last_user_activity_at)}</td><td>${fmtTime(session.last_keepalive_at)}</td><td>${fmtTime(session.keepalive_eligible_until)}</td><td class="truncate" title="${escapeHtml(latestSummary || session.last_error || "")}">${escapeHtml(latestSummary || session.last_error || "--")}</td><td><div class="actions"><button class="button secondary small" data-session-history="${session.session_id}">历史</button>${state.account.role === "admin" ? `<button class="button secondary small" data-check-session="${session.session_id}">实时检查</button><button class="button secondary small" data-rebind-session="${session.session_id}" data-expected-principal="${escapeHtml(session.expected_principal_ref || "")}">修改绑定</button>${session.state === "active" ? `<button class="button secondary small" data-invalidate-session="${session.session_id}">失效</button>` : ""}` : ""}</div></td>`);
  });
  content.innerHTML = `<div class="view-head"><div><h2>用户 × 系统会话矩阵</h2><p>“上次确认有效”表示注册表没有发现失效，但当前未做实时保证；“未保活（超期）”表示超过最近用户活动租约，后台已主动停止保活。实时检查不会延长租约。</p></div></div>${filteredTable(["用户", "系统", "预期主体", "已验证主体", "登录状态", "保活资格", "最近用户活动", "最近保活", "自动保活资格至", "最近事件", ""], rows, "搜索用户、系统、主体或事件", ["active", "expired", "awaiting_login", "new", "quarantined"])}`;
}

async function renderCapabilities() {
  const [capabilities, policies] = await Promise.all([api("/api/capabilities"), api("/api/policies")]);
  const policyRows = policies.items.map(policy => filterRow(`${policy.scope_type} ${policy.scope_value} ${policy.reason} ${policy.updated_by}`, policy.state, `<td>${escapeHtml(policy.scope_type)}</td><td class="code">${escapeHtml(policy.scope_value)}</td><td>${escapeHtml(policy.capability_version)}</td><td>${badge(policy.state)}</td><td class="truncate" title="${escapeHtml(policy.reason)}">${escapeHtml(policy.reason)}</td><td>${escapeHtml(policy.updated_by)}</td><td>${fmtTime(policy.updated_at)}</td><td><div class="actions">${state.account.role === "admin" && policy.state === "paused" ? `<button class="button secondary small" data-resume-policy="${policy.policy_id}">恢复</button>` : ""}</div></td>`));
  const capabilityRows = capabilities.items.map(item => { const capabilityState = item.effect === "read" ? "available" : item.paused_by.length ? "paused" : "active"; return filterRow(`${item.name} ${item.system} ${item.effect}`, capabilityState, `<td class="code">${escapeHtml(item.name)}</td><td>${escapeHtml(item.system)}</td><td>${escapeHtml(item.effect)}</td><td>${escapeHtml(item.version)}</td><td>${badge(capabilityState)}</td><td><div class="actions">${state.account.role === "admin" && item.effect !== "read" ? `<button class="button secondary small" data-pause-capability="${escapeHtml(item.name)}" data-version="${escapeHtml(item.version)}">暂停</button>` : ""}</div></td>`); });
  content.innerHTML = `<div class="toolbar"><div><strong>写操作控制</strong><div class="muted">可按全局、系统、用户或具体能力暂停，不影响读取能力。</div></div>${state.account.role === "admin" ? '<div class="actions"><button class="button secondary" data-pause-system="oa">暂停 OA</button><button class="button secondary" data-pause-system="taihua">暂停泰华</button><button class="button danger" data-global-pause>全局暂停写入</button></div>' : ""}</div>
    ${filteredTable(["范围", "对象", "版本", "状态", "原因", "操作人", "更新时间", ""], policyRows, "搜索策略对象、原因或操作人", ["paused", "active"])}
    <div class="view-head section-spaced"><div><h2>能力目录</h2><p>暂停策略在准备与最终提交边界都会重新检查。</p></div></div>${filteredTable(["能力", "系统", "效果", "版本", "状态", ""], capabilityRows, "搜索能力、系统或效果", ["available", "active", "paused"])}`;
}

async function renderOperations() {
  const data = await api("/api/operations?limit=300");
  content.innerHTML = `<div class="view-head"><div><h2>能力执行记录</h2><p>不展示输入摘要、结果正文和业务字段。</p></div></div>${operationTable(data.items, true)}`;
}
function operationTable(items, filterable = true) {
  const rows = items.map(item => { const effective = item.effective_status || item.status; return filterRow(`${item.operation_id} ${item.user_subject} ${item.capability_name} ${item.status} ${item.interaction_type} ${item.interaction_state} ${item.error_code} ${item.error_message}`, effective, `<td class="code">${shortId(item.operation_id)}</td><td>${escapeHtml(item.user_subject)}</td><td class="code">${escapeHtml(item.capability_name)}</td><td>${badge(effective)}</td><td>${item.interaction_state ? badge(item.interaction_state) : "--"}</td><td class="truncate" title="${escapeHtml(item.error_message || "")}">${escapeHtml(item.error_code || "--")}</td><td>${fmtTime(item.created_at)}</td><td>${fmtTime(item.finished_at)}</td>`); });
  const headers = ["Operation ID", "用户", "能力", "有效状态", "交互状态", "错误", "开始", "结束"];
  return filterable ? filteredTable(headers, rows, "搜索操作、用户、能力或错误", ["awaiting_user", "user_action_completed", "resumed", "user_action_expired", "user_action_rejected", "user_action_superseded", "user_action_handoff", "succeeded", "failed", "running", "unknown"]) : table(headers, rows);
}

async function renderInteractions() {
  const data = await api("/api/interactions?limit=300");
  const rows = data.items.map(item => filterRow(`${item.interaction_id} ${item.user_subject} ${item.system_id} ${item.interaction_type} ${item.title}`, item.state, `<td class="code">${shortId(item.interaction_id)}</td><td>${escapeHtml(item.user_subject)}</td><td>${escapeHtml(item.system_id)}</td><td>${escapeHtml(item.interaction_type)}</td><td class="truncate">${escapeHtml(item.title)}</td><td>${badge(item.state)}</td><td>${fmtTime(item.created_at)}</td><td>${fmtTime(item.expires_at)}</td>`));
  content.innerHTML = `<div class="view-head"><div><h2>可信交互时间线</h2><p>只看交互状态；不展示卡片 URL、字段值、密码或授权计划。</p></div></div>${filteredTable(["Interaction ID", "用户", "系统", "类型", "标题", "状态", "创建", "到期"], rows, "搜索交互、用户、系统或标题", ["pending", "submitted", "approved", "rejected", "expired", "consumed", "superseded"])}`;
}

async function renderTraces() {
  const data = await api("/api/traces?limit=400");
  const rows = data.items.map(item => filterRow(
    `${item.trace_id} ${item.request_id} ${item.user_subject} ${item.task_id} ${item.host_type} ${item.host_instance_id} ${item.system_id} ${item.capability_name} ${item.status} ${item.side_effect_boundary}`,
    item.status,
    `<td class="code">${shortId(item.trace_id)}</td><td>${escapeHtml(item.user_subject)}</td><td>${escapeHtml(item.host_type || "--")}</td><td>${escapeHtml(item.request_kind || "--")}</td><td>${escapeHtml(item.system_id || "--")}</td><td class="truncate" title="${escapeHtml(item.capability_name || "")}">${escapeHtml(item.capability_name || "--")}</td><td>${badge(item.status)}</td><td><span class="boundary">${escapeHtml(boundaryLabel(item.side_effect_boundary))}</span></td><td>${item.stage_count || 0}</td><td>${fmtDuration(item.recorded_duration_ms)}</td><td>${fmtTime(item.started_at)}</td><td><button class="button secondary small" data-trace-detail="${escapeHtml(item.trace_id)}">详情</button></td>`,
  ));
  content.innerHTML = `<div class="view-head"><div><h2>端到端执行链路</h2><p>从宿主接收、MCP、能力、下游适配、可信交互到投递回执；不记录密码、Cookie、业务字段或下载地址。</p></div><button class="button secondary" data-open-view="incidents">查看异常事件</button></div>${filteredTable(["Trace ID", "用户", "宿主", "类型", "系统", "能力", "状态", "副作用边界", "阶段", "记录耗时", "开始", ""], rows, "搜索 Trace、用户、任务、系统或能力", ["active", "waiting", "succeeded", "failed", "unknown", "cancelled"])}`;
}

async function openTraceDetail(traceId) {
  const data = await api(`/api/traces/${traceId}`);
  const trace = data.trace;
  const spanRows = data.spans.map((span, index) => `<tr><td>${index + 1}</td><td>${fmtTime(span.started_at)}</td><td class="code">${escapeHtml(span.stage)}</td><td>${badge(span.status)}</td><td>${fmtDuration(span.duration_ms)}</td><td><span class="boundary">${escapeHtml(boundaryLabel(span.side_effect_boundary))}</span></td><td class="truncate" title="${escapeHtml(span.error_code || "")}">${escapeHtml(span.error_code || "--")}</td></tr>`);
  const incidentRows = data.incidents.map(item => `<tr><td>${escapeHtml(item.severity)}</td><td>${escapeHtml(item.title)}</td><td>${badge(item.state)}</td><td>${fmtTime(item.last_seen_at)}</td></tr>`);
  openModal({
    kicker: "TRACE DETAIL",
    title: `链路 ${trace.trace_id.slice(0, 8)}`,
    submit: "关闭",
    body: `<dl class="detail-grid"><div><dt>用户</dt><dd>${escapeHtml(trace.user_subject)}</dd></div><div><dt>宿主</dt><dd>${escapeHtml(trace.host_type)} / ${escapeHtml(trace.host_instance_id || "--")}</dd></div><div><dt>任务</dt><dd class="code">${escapeHtml(trace.task_id || "--")}</dd></div><div><dt>能力</dt><dd class="code">${escapeHtml(trace.capability_name || "--")}</dd></div><div><dt>状态</dt><dd>${badge(trace.status)}</dd></div><div><dt>最远边界</dt><dd>${escapeHtml(boundaryLabel(trace.side_effect_boundary))}</dd></div><div><dt>开始</dt><dd>${fmtTime(trace.started_at)}</dd></div><div><dt>结束</dt><dd>${fmtTime(trace.finished_at)}</dd></div></dl><h3 class="modal-section-title">阶段时间线</h3>${table(["序", "时间", "阶段", "状态", "耗时", "副作用", "错误"], spanRows)}<h3 class="modal-section-title">关联事件</h3>${table(["级别", "事件", "状态", "最近出现"], incidentRows)}`,
    action: async () => closeModal(),
  });
}

function incidentStateBadge(value) {
  const labels = { open: "待处理", acknowledged: "已确认", investigating: "调查中", resolved: "已解决", suppressed: "已抑制" };
  return `<span class="status ${statusClass(value)}">${escapeHtml(labels[value] || value || "未知")}</span>`;
}
function severityBadge(value) {
  const cls = ["P0", "P1"].includes(value) ? "bad" : value === "P2" ? "warn" : "neutral";
  return `<span class="status ${cls}">${escapeHtml(value || "--")}</span>`;
}
async function renderIncidents(evaluate = false) {
  const [governance, incidents] = await Promise.all([api(`/api/governance${evaluate ? "?evaluate=true" : ""}`), api("/api/incidents?limit=500")]);
  const summary = governance.summary;
  const incidentRows = incidents.items.map(item => {
    const open = ["open", "acknowledged", "investigating"].includes(item.state);
    const actions = state.account.role === "admin" && open ? `<div class="actions">${item.state === "open" ? `<button class="button secondary small" data-incident-action="acknowledge" data-incident-id="${item.incident_id}">确认</button>` : ""}${item.state !== "investigating" ? `<button class="button secondary small" data-incident-action="investigate" data-incident-id="${item.incident_id}">调查</button>` : ""}<button class="button secondary small" data-incident-action="resolve" data-incident-id="${item.incident_id}">解决</button><button class="button secondary small" data-incident-action="suppress" data-incident-id="${item.incident_id}">抑制</button></div>` : "";
    return filterRow(`${item.incident_id} ${item.rule_id} ${item.title} ${item.symptom_code} ${item.root_cause_code} ${item.user_subject} ${item.system_id} ${item.object_id}`, item.state, `<td>${severityBadge(item.severity)}</td><td>${incidentStateBadge(item.state)}</td><td class="truncate" title="${escapeHtml(item.title)}"><strong>${escapeHtml(item.title)}</strong><br><span class="muted code">${escapeHtml(item.rule_id)}</span></td><td>${escapeHtml(item.user_subject || "--")}</td><td>${escapeHtml(item.system_id || item.host_type || "--")}</td><td class="code">${escapeHtml(item.symptom_code || "--")}</td><td>${item.occurrence_count}</td><td>${fmtTime(item.last_seen_at)}</td><td>${actions}</td>`);
  });
  const sloRows = governance.slo.metrics.map(metricItem => `<tr><td class="code">${escapeHtml(metricItem.metricKey)}</td><td>${metricItem.sampleCount}</td><td><strong>${escapeHtml(fmtMetric(metricItem))}</strong></td><td>${metricItem.target == null ? "--" : escapeHtml(fmtMetric({ ...metricItem, value: metricItem.target }))}</td><td>${badge(metricItem.status)}</td></tr>`);
  const signalRows = governance.recent_signals.map(item => `<tr><td>${fmtTime(item.observed_at)}</td><td class="code">${escapeHtml(item.signal_type)}</td><td>${escapeHtml(item.source)}</td><td>${badge(item.status)}</td><td>${escapeHtml(item.user_subject || "--")}</td><td>${escapeHtml(item.system_id || item.host_type || "--")}</td></tr>`);
  const recoveryRows = governance.recent_recoveries.map(item => `<tr><td>${fmtTime(item.created_at)}</td><td class="code">${escapeHtml(item.action_type)}</td><td>${escapeHtml(item.target_type)}</td><td class="code">${shortId(item.target_id)}</td><td>${escapeHtml(item.actor)}</td><td>${badge(item.status)}</td><td>${escapeHtml(item.error_code || "--")}</td></tr>`);
  const observationRows = (governance.observations || []).map(item => `<tr><td><strong>${escapeHtml(item.name)}</strong><br><span class="muted code">${shortId(item.observation_id)}</span></td><td>${badge(item.state)}</td><td>${fmtTime(item.started_at)}</td><td>${fmtTime(item.ends_at)}</td><td>${item.snapshot_count}</td><td>${fmtTime(item.last_snapshot_at)}</td><td>${escapeHtml(item.created_by)}</td></tr>`);
  content.innerHTML = `<div class="metric-grid">${metric("开放事件", summary.open_incidents, "待处理、已确认或调查中", summary.open_incidents ? "alert" : "")}${metric("P0/P1", summary.critical_incidents, "需要优先处置", summary.critical_incidents ? "alert" : "")}${metric("活动链路", summary.active_traces, "执行中或等待用户")}${metric("影子观察", summary.active_observations || 0, `最近采样 ${fmtTime(summary.last_observation_at)}`)}</div><div class="toolbar section-spaced"><div><strong>异常事件</strong><div class="muted">检测结果可以确认、调查、解决或抑制；系统不会自动重试结果未知的业务写入。</div></div>${state.account.role === "admin" ? '<button class="button secondary" data-evaluate-runtime>立即评估</button>' : ""}</div>${filteredTable(["级别", "状态", "事件", "用户", "系统/宿主", "症状", "次数", "最近出现", ""], incidentRows, "搜索规则、事件、用户、系统或对象", ["open", "acknowledged", "investigating", "resolved", "suppressed"])}<div class="view-head section-spaced"><div><h2>影子观察</h2><p>按小时采集中央运行账本与 SLO，不调用业务系统，也不自动恢复业务写入。</p></div></div>${table(["观察批次", "状态", "开始", "计划结束", "快照", "最近采样", "发起人"], observationRows)}<div class="split"><section><div class="view-head section-spaced"><div><h2>24 小时 SLO</h2><p>零隔离异常、写后核验覆盖率与关键阶段延迟。</p></div></div>${table(["指标", "样本", "当前值", "目标", "状态"], sloRows)}</section><section><div class="view-head section-spaced"><div><h2>最近信号</h2><p>非敏感运行信号。</p></div></div>${table(["时间", "信号", "来源", "状态", "用户", "系统/宿主"], signalRows)}</section></div><div class="view-head section-spaced"><div><h2>受控恢复账本</h2><p>所有恢复动作带幂等键、操作者与结果，失败不会静默重复。</p></div></div>${table(["时间", "动作", "对象", "对象 ID", "操作者", "状态", "错误"], recoveryRows)}`;
}

async function renderCoordination() {
  const data = await api("/api/coordination?limit=300");
  data.artifacts = Array.isArray(data.artifacts) ? data.artifacts : [];
  const summary = data.summary;
  const taskRows = data.tasks.map(item => filterRow(`${item.task_id} ${item.user_subject} ${item.title} ${item.origin_client_type} ${item.origin_label} ${item.current_operation_id} ${item.current_interaction_id}`, item.status, `<td class="code">${shortId(item.task_id)}</td><td>${escapeHtml(item.user_subject)}</td><td class="truncate" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</td><td>${badge(item.status)}</td><td>${escapeHtml(item.origin_label || item.origin_client_type || "--")}</td><td class="code">${shortId(item.current_operation_id)}</td><td class="code">${shortId(item.current_interaction_id)}</td><td>${fmtTime(item.updated_at)}</td><td>${fmtTime(item.finished_at)}</td>`));
  const endpointRows = data.endpoints.map(item => filterRow(`${item.endpoint_id} ${item.user_subject} ${item.client_type} ${item.label} ${item.capabilities.join(" ")} ${item.delivery_state}`, item.state, `<td class="code">${shortId(item.endpoint_id)}</td><td>${escapeHtml(item.user_subject)}</td><td>${escapeHtml(item.client_type)}</td><td>${badge(item.delivery_mode)}</td><td>${escapeHtml(item.label || "--")}</td><td>${scopeBadges(item.capabilities)}</td><td>${badge(item.state)}</td><td>${badge(item.delivery_state)}${item.deferred_delivery_count ? ` <span class="muted">${item.deferred_delivery_count}</span>` : ""}</td><td>${fmtTime(item.last_seen_at)}</td>`));
  const continuationRows = data.continuations.map(item => filterRow(`${item.endpoint_id} ${item.user_subject} ${item.client_type} ${item.selected_task_id} ${item.reason}`, item.state, `<td class="code">${shortId(item.endpoint_id)}</td><td>${escapeHtml(item.user_subject)}</td><td>${escapeHtml(item.client_type || "--")}</td><td>${badge(item.state)}</td><td>${badge(item.execution_mode)}</td><td class="code">${shortId(item.selected_task_id)}</td><td>${item.candidate_count}</td><td>${escapeHtml(item.reason || "--")}</td><td>${fmtTime(item.expires_at)}</td>`));
  const deliveryRows = data.deliveries.map(item => { const actions = state.account.role === "admin" ? `<div class="actions">${["failed", "deferred", "archived"].includes(item.state) ? `<button class="button secondary small" data-recovery-action="retry_delivery" data-recovery-target="${item.delivery_id}">重试</button>` : ""}${["failed", "acknowledged"].includes(item.state) ? `<button class="button secondary small" data-recovery-action="archive_delivery" data-recovery-target="${item.delivery_id}">归档</button>` : ""}</div>` : ""; return filterRow(`${item.delivery_id} ${item.task_id} ${item.endpoint_id} ${item.user_subject} ${item.event_type}`, item.state, `<td class="code">${shortId(item.delivery_id)}</td><td>${escapeHtml(item.user_subject)}</td><td>${escapeHtml(item.event_type || item.payload_type)}</td><td class="code">${shortId(item.endpoint_id)}</td><td>${badge(item.state)}</td><td>${item.attempt_count}</td><td>${fmtTime(item.next_attempt_at)}</td><td>${fmtTime(item.acknowledged_at)}</td><td>${fmtTime(item.updated_at)}</td><td>${actions}</td>`); });
  const artifactRows = data.artifacts.map(item => filterRow(`${item.artifact_id} ${item.task_id} ${item.user_subject} ${item.filename} ${item.artifact_type} ${item.content_type}`, item.state, `<td class="code">${shortId(item.artifact_id)}</td><td>${escapeHtml(item.user_subject)}</td><td class="truncate" title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</td><td>${escapeHtml(item.artifact_type)}</td><td>${escapeHtml(item.content_type)}</td><td>${fmtBytes(item.byte_size)}</td><td>${badge(item.state)}</td><td class="code">${shortId(item.task_id)}</td><td>${fmtTime(item.created_at)}</td><td>${fmtTime(item.expires_at)}</td>`));
  const violationRows = Object.entries(data.isolation?.violations || {}).map(([name, count]) => `<tr><td class="code">${escapeHtml(name)}</td><td>${count}</td><td>${badge(count === 0 ? "succeeded" : "failed")}</td></tr>`);
  content.innerHTML = `<div class="metric-grid">
    ${metric("涉及用户", summary.users, "Task Hub 用户")}
    ${metric("活动任务", summary.active_tasks, "尚未进入终态")}
    ${metric("等待用户", summary.waiting_tasks, "卡片或确认节点")}
    ${metric("活动端点", summary.active_endpoints, "网页与聊天通道")}
    ${metric("投递模式", `${summary.pull_endpoints} / ${summary.direct_endpoints}`, "拉取 / 直推")}
    ${metric("活动接续", summary.active_continuations, "跨端任务上下文")}
    ${metric("可取用文件", summary.ready_artifacts, `${summary.expired_artifacts} 个链接已过期`)}
    ${metric("待投递", summary.outstanding_deliveries, `等待活动 ${summary.deferred_deliveries} / 历史失败 ${summary.failed_deliveries}`, summary.outstanding_deliveries ? "alert" : "")}
    ${metric("隔离完整性", summary.isolation_violations ? "异常" : "通过", `${summary.isolation_violations} 项异常`, summary.isolation_violations ? "alert" : "")}
  </div>
  <div class="view-head section-spaced"><div><h2>任务协调明细</h2><p>只显示治理元数据；聊天正文、卡片链接、通道路由和业务字段不会进入此页面。</p></div></div>
  <div class="segmented" role="tablist" aria-label="多端任务视图">
    <button role="tab" data-coordination-tab="tasks">任务 <span>${data.tasks.length}</span></button>
    <button role="tab" data-coordination-tab="endpoints">端点 <span>${data.endpoints.length}</span></button>
    <button role="tab" data-coordination-tab="continuations">接续 <span>${data.continuations.length}</span></button>
    <button role="tab" data-coordination-tab="deliveries">投递 <span>${data.deliveries.length}</span></button>
    <button role="tab" data-coordination-tab="artifacts">文件 <span>${data.artifacts.length}</span></button>
    <button role="tab" data-coordination-tab="isolation">隔离 <span>${summary.isolation_violations}</span></button>
  </div>
  <section data-coordination-panel="tasks">${filteredTable(["Task ID", "用户", "任务", "状态", "发起端", "当前操作", "当前交互", "更新", "结束"], taskRows, "搜索任务、用户、端点或关联 ID", ["active", "waiting_user", "running", "completed", "failed", "outcome_unknown", "canceled", "superseded"])}</section>
  <section data-coordination-panel="endpoints">${filteredTable(["Endpoint ID", "用户", "客户端", "投递方式", "标签", "能力", "状态", "同步状态", "最近活动"], endpointRows, "搜索端点、用户、客户端或能力", ["active", "inactive"])}</section>
  <section data-coordination-panel="continuations">${filteredTable(["Endpoint ID", "用户", "客户端", "状态", "执行模式", "选中任务", "候选", "原因", "到期"], continuationRows, "搜索用户、端点、任务或原因", ["selected", "awaiting_selection", "expired", "canceled"])}</section>
  <section data-coordination-panel="deliveries">${filteredTable(["Delivery ID", "用户", "事件", "Endpoint ID", "状态", "尝试", "下次重试", "确认", "更新", ""], deliveryRows, "搜索投递、用户、事件或端点", ["pending", "delivering", "deferred", "acknowledged", "failed", "archived"])}</section>
  <section data-coordination-panel="artifacts">${filteredTable(["Artifact ID", "用户", "文件", "类型", "内容类型", "大小", "状态", "Task ID", "创建", "到期"], artifactRows, "搜索文件、用户、任务或类型", ["ready", "expired"])}</section>
  <section data-coordination-panel="isolation">${table(["检查项", "异常数", "结果"], violationRows)}</section>`;
  selectCoordinationTab(state.coordinationTab);
}
function selectCoordinationTab(tab) {
  state.coordinationTab = tab;
  content.querySelectorAll("[data-coordination-tab]").forEach(button => {
    const selected = button.dataset.coordinationTab === tab;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
  });
  content.querySelectorAll("[data-coordination-panel]").forEach(panel => panel.classList.toggle("hidden", panel.dataset.coordinationPanel !== tab));
}

async function renderRuntime() {
  const [data, accounts] = await Promise.all([api("/api/runtime"), api("/api/admin-accounts")]);
  const accountRows = accounts.items.map(account => filterRow(`${account.username} ${account.role}`, account.state, `<td>${escapeHtml(account.username)}</td><td>${escapeHtml(account.role)}</td><td>${badge(account.state)}</td><td>${account.must_change_password ? badge("pending") : badge("active")}</td><td>${fmtTime(account.last_login_at)}</td><td>${fmtTime(account.created_at)}</td>`));
  const taskHub = data.coordination?.task_hub || { summary: {}, isolation: { passed: false, violations: {} } };
  const hostControl = data.coordination?.host_control || { operations: [], recent_slow_calls: [], slow_after_ms: 1000 };
  const workspaceGateway = data.workspace_gateway || { configured: false };
  const governance = data.governance || { active_traces: 0, open_incidents: 0, critical_incidents: 0, running_recoveries: 0 };
  const gatewayHasCurrentError = Boolean(
    workspaceGateway.last_error_code &&
    (!workspaceGateway.last_success_at ||
      new Date(workspaceGateway.last_error_at).getTime() >
        new Date(workspaceGateway.last_success_at).getTime()),
  );
  const operationRows = hostControl.operations.map(operation => filterRow(`${operation.operation_name}`, operation.error_count ? "failed" : operation.slow_count ? "running" : "succeeded", `<td class="code">${escapeHtml(operation.operation_name)}</td><td>${operation.call_count}</td><td>${operation.slow_count}</td><td>${operation.error_count}</td><td>${operation.average_elapsed_ms} ms</td><td>${operation.max_elapsed_ms} ms</td><td>${fmtTime(operation.last_called_at)}</td>`));
  const slowRows = hostControl.recent_slow_calls.map(call => filterRow(`${call.user_subject} ${call.operation_name} ${call.error_code}`, call.error_code ? "failed" : "running", `<td>${fmtTime(call.called_at)}</td><td>${escapeHtml(call.user_subject)}</td><td class="code">${escapeHtml(call.operation_name)}</td><td>${call.elapsed_ms} ms</td><td>${escapeHtml(call.error_code || "--")}</td>`));
  const violationRows = Object.entries(taskHub.isolation?.violations || {}).map(([name, count]) => `<tr><td class="code">${escapeHtml(name)}</td><td>${count}</td><td>${badge(count === 0 ? "succeeded" : "failed")}</td></tr>`);
  content.innerHTML = `<div class="metric-grid">
    ${metric("发布版本", data.release_id, "当前服务构建")}${metric("启动时间", fmtTime(data.started_at), "中心进程")}${metric("管理 API", data.admin_api, "独立认证域")}${metric("数据库", data.database, "SQLite WAL")}
    ${metric("保活租约", data.session_keepalive.activity_lease_seconds ? `${Math.round(data.session_keepalive.activity_lease_seconds / 86400)} 天` : "关闭", "仅真实活动续租")}${metric("活动端点", taskHub.summary.active_endpoints ?? "--", `${taskHub.summary.users ?? 0} 个用户`)}${metric("待投递", taskHub.summary.outstanding_deliveries ?? "--", `失败 ${taskHub.summary.failed_deliveries ?? 0}`)}${metric("隔离完整性", taskHub.isolation?.passed ? "通过" : "异常", `${taskHub.summary.isolation_violation_count ?? "--"} 项异常`, taskHub.isolation?.passed ? "" : "alert")}
    ${metric("活动链路", governance.active_traces, "执行中或等待用户")}${metric("开放事件", governance.open_incidents, `${governance.critical_incidents} 个 P0/P1`, governance.critical_incidents ? "alert" : "")}${metric("恢复动作", governance.running_recoveries, "当前执行中")}${metric("追踪阶段", governance.span_count ?? 0, `最近信号 ${fmtTime(governance.last_signal_at)}`)}
    ${metric("Workspace Gateway", workspaceGateway.configured ? workspaceGateway.target : "未配置", workspaceGateway.last_success_at ? `最近连通 ${fmtTime(workspaceGateway.last_success_at)}` : "尚无成功连接")}${metric("Gateway 最近错误", workspaceGateway.last_error_code || "无", workspaceGateway.last_error_at ? `${fmtTime(workspaceGateway.last_error_at)}${gatewayHasCurrentError ? "" : " · 已恢复"}` : "未记录错误", gatewayHasCurrentError ? "alert" : "")}
  </div>
  <div class="view-head section-spaced"><div><h2>已配置系统</h2><p>控制台只报告状态，不提供 systemd 重启或业务代操作。</p></div></div>${table(["系统 ID", "名称", "状态"], data.systems.map(system => `<tr><td class="code">${escapeHtml(system.system_id)}</td><td>${escapeHtml(system.label)}</td><td>${badge(system.configured ? "active" : "failed")}</td></tr>`))}
  <div class="view-head section-spaced"><div><h2>隔离完整性</h2><p>所有任务、时间线和通知都必须与所属用户和端点一致。</p></div><button class="button secondary small" data-open-view="coordination">打开多端任务</button></div>${table(["检查项", "异常数", "结果"], violationRows)}
  <div class="view-head section-spaced"><div><h2>协调调用即时耗时</h2><p>慢调用阈值 ${hostControl.slow_after_ms} ms；此即时统计随中心进程重启清零，持久化历史请查看链路追踪。</p></div><button class="button secondary small" data-open-view="traces">打开链路追踪</button></div>${filteredTable(["调用", "次数", "慢调用", "错误", "平均", "最大", "最近调用"], operationRows, "搜索调用名称", ["succeeded", "running", "failed"])}
  <div class="view-head section-spaced"><div><h2>最近慢调用</h2><p>仅保留工具名、用户标识、耗时和错误类型。</p></div></div>${filteredTable(["时间", "用户", "调用", "耗时", "错误"], slowRows, "搜索用户、调用或错误", ["running", "failed"])}
  <div class="toolbar section-spaced"><div><strong>管理账户</strong><div class="muted">管理员可执行控制动作，审计员仅可查看。</div></div>${state.account.role === "admin" ? '<button class="button primary" data-create-admin>新建账户</button>' : ""}</div>${filteredTable(["用户名", "角色", "状态", "初始密码", "最近登录", "创建时间"], accountRows, "搜索管理账户", ["active", "revoked"])}`;
}
async function renderAudit() {
  const data = await api("/api/audit?limit=400");
  const rows = data.items.map(item => { const result = item.result === "succeeded" ? "succeeded" : "failed"; return filterRow(`${item.actor_username} ${item.action} ${item.target_type} ${item.target_id} ${item.reason} ${item.request_ip}`, result, `<td>${fmtTime(item.created_at)}</td><td>${escapeHtml(item.actor_username || "匿名")}</td><td class="code">${escapeHtml(item.action)}</td><td>${escapeHtml(item.target_type || "--")}</td><td class="code">${shortId(item.target_id)}</td><td class="truncate" title="${escapeHtml(item.reason || "")}">${escapeHtml(item.reason || "--")}</td><td>${badge(result)}</td><td>${escapeHtml(item.request_ip || "--")}</td>`); });
  content.innerHTML = `<div class="view-head"><div><h2>独立管理审计</h2><p>管理事件仅追加写入，包含操作人、来源、原因和前后状态。</p></div></div>${filteredTable(["时间", "管理员", "动作", "对象类型", "对象 ID", "原因", "结果", "来源 IP"], rows, "搜索管理员、动作、对象或原因", ["succeeded", "failed"])}`;
}

function openModal({ kicker = "管理操作", title, body, submit = "确认", danger = false, action, locked = false }) {
  $("#modal-kicker").textContent = kicker; $("#modal-title").textContent = title; $("#modal-body").innerHTML = body;
  $("#modal-submit").textContent = submit; $("#modal-submit").className = `button ${danger ? "danger" : "primary"}`;
  $("#modal-cancel").classList.toggle("hidden", locked); $("#modal-close").classList.toggle("hidden", locked);
  $("#modal-error").textContent = ""; state.modalAction = action; modal.showModal();
}
function closeModal() { if (modal.open) modal.close(); state.modalAction = null; }
function reasonField(label = "操作原因") { return `<label>${escapeHtml(label)}<textarea name="reason" maxlength="500" required placeholder="说明本次管理操作的原因，将写入审计日志"></textarea></label>`; }
function openPasswordModal(locked = false) {
  openModal({ kicker: "账户安全", title: locked ? "修改初始密码" : "修改密码", submit: "更新密码", locked,
    body: `<label>当前密码<input name="current_password" type="password" autocomplete="current-password" required></label><label>新密码<input name="new_password" type="password" autocomplete="new-password" minlength="12" required></label><label>确认新密码<input name="confirm_password" type="password" autocomplete="new-password" minlength="12" required></label><p class="muted">至少 12 位，并使用大写、小写、数字、符号中的三类。</p>`,
    action: async form => { if (form.get("new_password") !== form.get("confirm_password")) throw new Error("两次输入的新密码不一致"); const result = await api("/api/account/password", { method: "POST", body: JSON.stringify({ current_password: form.get("current_password"), new_password: form.get("new_password") }) }); state.account = result.account; $("#password-banner").classList.add("hidden"); closeModal(); toast("密码已更新"); await loadView(state.view); }
  });
}
function openAdminAccount() {
  openModal({ title: "新建管理账户", submit: "创建账户", body: `<label>用户名<input name="username" pattern="[A-Za-z][A-Za-z0-9_.-]{2,63}" required></label><label>角色<select name="role"><option value="auditor">审计员（只读）</option><option value="admin">管理员</option></select></label>${reasonField()}`, action: async form => {
    const account = await api("/api/admin-accounts", { method: "POST", body: JSON.stringify({ username: form.get("username"), role: form.get("role"), reason: form.get("reason") }) });
    openModal({ kicker: "只显示一次", title: "账户已创建", submit: "完成", body: `<p>${escapeHtml(account.username)} 首次登录后必须修改初始密码。</p><div id="issued-secret" class="secret-box">${escapeHtml(account.bootstrap_password)}</div><button type="button" class="button secondary" data-copy-secret>复制初始密码</button>`, action: async () => { closeModal(); await loadView("runtime"); } });
  } });
}
function openIssueToken() {
  const groups = scopeGroups.map(group => `<fieldset class="scope-group"><legend>${escapeHtml(group.label)}</legend><div class="checkbox-grid">${group.items.map(scope => `<label class="check"><input type="checkbox" name="scope" value="${scope}" ${scope === "oa:read" ? "checked" : ""}><span><strong>${escapeHtml(statusText[scope] || scope.split(":").slice(-1)[0])}</strong><small>${escapeHtml(scope)}</small></span></label>`).join("")}</div></fieldset>`).join("");
  openModal({ title: "签发 MCP Token", submit: "签发", body: `<label>用户标识<input name="user_subject" required></label><div><strong>各系统预期主体</strong><p class="muted">已有绑定可以留空；首次开通某个系统时必须填写该系统识别到的账号或主体。</p></div><label>OA 主体<input name="principal_oa" maxlength="256"></label><label>泰华主体<input name="principal_taihua" maxlength="256"></label><label>语雀主体<input name="principal_yuque" maxlength="256"></label><label>标签<input name="label" maxlength="120"></label><label>有效小时数<input name="ttl_hours" type="number" min="1" max="2160" value="720" required></label><div class="scope-groups"><strong>权限范围</strong>${groups}</div>${reasonField()}`,
    action: async form => { const scopes = form.getAll("scope"); const principal_bindings = Object.fromEntries(["oa", "taihua", "yuque"].map(system => [system, String(form.get(`principal_${system}`) || "").trim()]).filter(([, principal]) => principal)); const issued = await api("/api/tokens", { method: "POST", body: JSON.stringify({ user_subject: form.get("user_subject"), principal_bindings, label: form.get("label"), ttl_hours: Number(form.get("ttl_hours")), scopes, reason: form.get("reason") }) }); openModal({ kicker: "只显示一次", title: "Token 已签发", submit: "完成", body: `<p>请将密钥配置到可信 MCP 客户端。关闭后无法再次查看。</p><div id="issued-secret" class="secret-box">${escapeHtml(issued.token_secret)}</div><button type="button" class="button secondary" data-copy-secret>复制密钥</button>`, action: async () => { closeModal(); await loadView("users"); } }); }
  });
}
function openRebindSession(target) {
  openModal({ title: "修改系统身份绑定", submit: "修改并清除登录态", danger: true, body: `<label>预期下游主体<input name="expected_principal_ref" maxlength="256" value="${escapeHtml(target.dataset.expectedPrincipal || "")}" required></label><p class="muted">只影响这一用户在当前系统中的会话。修改后必须重新登录。</p>${reasonField()}`, action: async form => { await api(`/api/sessions/${target.dataset.rebindSession}/rebind`, { method: "POST", body: JSON.stringify({ expected_principal_ref: form.get("expected_principal_ref"), reason: form.get("reason") }) }); closeModal(); toast("系统身份绑定已更新"); await loadView("sessions"); } });
}
function openPause({ scopeType, scopeValue, version = "*", title }) {
  openModal({ title, submit: "暂停写入", danger: true, body: `<p>读取能力不受影响；新的准备动作和已经授权但尚未提交的写操作都会被阻断。</p>${reasonField()}`, action: async form => { await api("/api/policies/pause", { method: "POST", body: JSON.stringify({ scope_type: scopeType, scope_value: scopeValue, capability_version: version, reason: form.get("reason") }) }); closeModal(); toast("写暂停策略已生效"); await loadView(state.view); } });
}
function openReasonAction({ title, submit, danger = false, request }) {
  openModal({ title, submit, danger, body: reasonField(), action: async form => { await request(form.get("reason")); closeModal(); toast(`${title}已完成`); await loadView(state.view); } });
}
function openRuntimeRecovery({ actionType, targetId, title, submit, danger = false }) {
  openReasonAction({ title, submit, danger, request: reason => api("/api/recovery", { method: "POST", body: JSON.stringify({ action_type: actionType, target_id: targetId, reason, idempotency_key: crypto.randomUUID() }) }) });
}
async function openSessionHistory(sessionId) {
  const data = await api(`/api/sessions/${sessionId}/events?limit=100`);
  const rows = data.items.map(item => `<tr><td>${fmtTime(item.created_at)}</td><td>${escapeHtml(item.source)}</td><td>${escapeHtml(item.event_type)}</td><td>${escapeHtml(item.previous_state || "--")} → ${escapeHtml(item.new_state || "--")}</td><td class="truncate" title="${escapeHtml(item.reason || "")}">${escapeHtml(item.reason || "--")}</td></tr>`);
  openModal({ kicker: "SESSION HISTORY", title: "会话状态历史", submit: "关闭", body: `<p class="muted">保留失效、延期检查、重新登录等原因；后续成功登录不会覆盖旧记录。</p>${table(["时间", "来源", "事件", "状态变化", "原因"], rows)}`, action: async () => closeModal() });
}

$("#login-form").addEventListener("submit", async event => {
  event.preventDefault(); $("#login-error").textContent = ""; const loginForm = event.currentTarget; const form = new FormData(loginForm);
  try { const result = await api("/api/login", { method: "POST", body: JSON.stringify({ username: form.get("username"), password: form.get("password") }) }); loginForm.reset(); showApp(result.account); if (result.account.must_change_password) openPasswordModal(true); else await loadView("overview"); }
  catch (error) { $("#login-error").textContent = error.message; }
});
$("#nav").addEventListener("click", event => { const button = event.target.closest("button[data-view]"); if (button) loadView(button.dataset.view); });
$("#refresh-button").addEventListener("click", () => loadView(state.view));
$("#account-button").addEventListener("click", () => $("#account-menu").classList.toggle("hidden"));
$("#change-password-button").addEventListener("click", () => { $("#account-menu").classList.add("hidden"); openPasswordModal(false); });
$("#banner-password-button").addEventListener("click", () => openPasswordModal(true));
$("#logout-button").addEventListener("click", async () => { try { await api("/api/logout", { method: "POST", body: "{}" }); } finally { showLogin(); } });
$("#modal-close").addEventListener("click", closeModal); $("#modal-cancel").addEventListener("click", closeModal);
$("#modal-form").addEventListener("submit", async event => { event.preventDefault(); if (!state.modalAction) return; $("#modal-error").textContent = ""; $("#modal-submit").disabled = true; try { await state.modalAction(new FormData(event.currentTarget)); } catch (error) { $("#modal-error").textContent = error.message; } finally { $("#modal-submit").disabled = false; } });
content.addEventListener("input", event => { if (event.target.matches("[data-filter-search]")) applyFilter(event.target); });
content.addEventListener("change", event => { if (event.target.matches("[data-filter-status]")) applyFilter(event.target); });
content.addEventListener("click", async event => {
  const tab = event.target.closest("[data-coordination-tab]"); if (tab) { selectCoordinationTab(tab.dataset.coordinationTab); return; }
  const target = event.target.closest("button"); if (!target) return;
  if (target.dataset.openView) loadView(target.dataset.openView);
  else if (target.dataset.traceDetail) await openTraceDetail(target.dataset.traceDetail);
  else if (target.dataset.incidentAction) openReasonAction({ title: `${target.textContent.trim()}运行事件`, submit: target.textContent.trim(), danger: target.dataset.incidentAction === "suppress", request: reason => api(`/api/incidents/${target.dataset.incidentId}/${target.dataset.incidentAction}`, { method: "POST", body: JSON.stringify({ reason }) }) });
  else if (target.matches("[data-evaluate-runtime]")) openRuntimeRecovery({ actionType: "evaluate_runtime", targetId: "runtime", title: "立即评估运行状态", submit: "开始评估" });
  else if (target.dataset.recoveryAction) openRuntimeRecovery({ actionType: target.dataset.recoveryAction, targetId: target.dataset.recoveryTarget, title: target.dataset.recoveryAction === "retry_delivery" ? "重新投递通知" : "归档投递记录", submit: target.dataset.recoveryAction === "retry_delivery" ? "重新投递" : "归档", danger: target.dataset.recoveryAction === "archive_delivery" });
  else if (target.matches("[data-issue-token]")) openIssueToken();
  else if (target.matches("[data-create-admin]")) openAdminAccount();
  else if (target.dataset.pauseUser) openPause({ scopeType: "user", scopeValue: target.dataset.pauseUser, title: `暂停 ${target.dataset.pauseUser} 的写入` });
  else if (target.dataset.pauseCapability) openPause({ scopeType: "capability", scopeValue: target.dataset.pauseCapability, version: target.dataset.version, title: "暂停具体能力" });
  else if (target.dataset.pauseSystem) openPause({ scopeType: "system", scopeValue: target.dataset.pauseSystem, title: "暂停系统写入" });
  else if (target.matches("[data-global-pause]")) openPause({ scopeType: "global", scopeValue: "*", title: "全局暂停所有写入" });
  else if (target.dataset.revokeToken) openReasonAction({ title: "撤销 Token", submit: "撤销", danger: true, request: reason => api(`/api/tokens/${target.dataset.revokeToken}/revoke`, { method: "POST", body: JSON.stringify({ reason }) }) });
  else if (target.dataset.checkSession) openReasonAction({ title: "实时检查会话", submit: "检查", request: reason => api(`/api/sessions/${target.dataset.checkSession}/check`, { method: "POST", body: JSON.stringify({ reason }) }) });
  else if (target.dataset.sessionHistory) await openSessionHistory(target.dataset.sessionHistory);
  else if (target.dataset.rebindSession) openRebindSession(target);
  else if (target.dataset.invalidateSession) openReasonAction({ title: "使会话失效", submit: "确认失效", danger: true, request: reason => api(`/api/sessions/${target.dataset.invalidateSession}/invalidate`, { method: "POST", body: JSON.stringify({ reason }) }) });
  else if (target.dataset.resumePolicy) openReasonAction({ title: "恢复写入", submit: "恢复", request: reason => api(`/api/policies/${target.dataset.resumePolicy}/resume`, { method: "POST", body: JSON.stringify({ reason }) }) });
});
modal.addEventListener("click", event => { if (event.target.matches("[data-copy-secret]")) navigator.clipboard.writeText($("#issued-secret").textContent).then(() => toast("密钥已复制"), () => toast("复制失败，请手动选择", true)); });
initialize();
