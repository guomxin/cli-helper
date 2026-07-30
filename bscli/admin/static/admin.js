"use strict";

const state = { account: null, view: "overview", modalAction: null };
const titles = {
  overview: ["CONTROL PLANE", "运行总览"],
  users: ["IDENTITY", "用户与令牌"],
  sessions: ["DOWNSTREAM", "系统会话"],
  capabilities: ["GOVERNANCE", "能力与策略"],
  operations: ["EXECUTION", "操作记录"],
  interactions: ["TRUSTED UX", "可信交互"],
  runtime: ["RUNTIME", "系统运行"],
  audit: ["ADMIN AUDIT", "管理审计"],
};
const statusText = {
  active: "有效", revoked: "已撤销", expired: "已过期", quarantined: "已隔离",
  awaiting_login: "待登录", new: "未登录", succeeded: "成功", failed: "失败",
  unknown: "结果未知", requires_user_action: "等待用户", running: "执行中",
  pending: "待处理", submitted: "已填写", approved: "已授权", rejected: "已拒绝",
  consumed: "已使用", superseded: "已替换", paused: "已暂停", available: "可用",
};
const statusClass = value => ["active", "succeeded", "approved", "submitted"].includes(value) ? "ok" :
  ["failed", "unknown", "expired", "quarantined", "revoked", "rejected"].includes(value) ? "bad" :
  ["pending", "awaiting_login", "requires_user_action", "paused"].includes(value) ? "warn" :
  ["running"].includes(value) ? "info" : "neutral";

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
function shortId(value) { return value ? `${escapeHtml(value.slice(0, 8))}…` : "--"; }
function badge(value) { return `<span class="status ${statusClass(value)}">${escapeHtml(statusText[value] || value || "未知")}</span>`; }
function empty(message) { return `<div class="empty">${escapeHtml(message)}</div>`; }
function table(headers, rows) {
  if (!rows.length) return empty("暂无记录");
  return `<div class="table-shell"><table><thead><tr>${headers.map(item => `<th>${escapeHtml(item)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}
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
  } catch (error) {
    showLogin();
  }
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
      overview: renderOverview, users: renderUsers, sessions: renderSessions,
      capabilities: renderCapabilities, operations: renderOperations,
      interactions: renderInteractions, runtime: renderRuntime, audit: renderAudit,
    };
    await renderers[view]();
    $("#freshness").textContent = `刷新于 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
  } catch (error) {
    if (error.status === 401) { showLogin(); return; }
    if (error.code === "PASSWORD_CHANGE_REQUIRED") { openPasswordModal(true); return; }
    content.innerHTML = empty(`读取失败：${error.message}`);
  }
}

async function renderOverview() {
  const data = await api("/api/overview");
  $("#sidebar-release").textContent = data.runtime.release_id;
  const summary = data.summary;
  content.innerHTML = `
    <div class="metric-grid">
      ${metric("接入用户", summary.users, "已绑定的智能体身份")}
      ${metric("有效令牌", summary.active_tokens, "MCP 调用凭证")}
      ${metric("活动会话", summary.active_sessions, "下游登录状态")}
      ${metric("写暂停", summary.paused_policies, "当前生效策略", summary.paused_policies ? "alert" : "")}
      ${metric("24h 异常", summary.failed_operations_24h, `${summary.operations_24h} 次操作`, summary.failed_operations_24h ? "alert" : "")}
    </div>
    <div class="split">
      <section class="panel"><div class="panel-head"><h3>系统状态</h3><span>按中心会话注册表统计</span></div>
        <div class="system-list">${data.systems.map(system => `
          <div class="system-row"><div class="system-name"><strong>${escapeHtml(system.label)}</strong><span>${escapeHtml(system.system_id)}</span></div>
          <div class="system-stat"><strong>${system.active_sessions}</strong><span>活动</span></div>
          <div class="system-stat"><strong>${system.attention_sessions}</strong><span>需关注</span></div>
          <div class="system-stat"><strong>${system.total_sessions}</strong><span>总计</span></div></div>`).join("")}</div>
      </section>
      <section class="panel"><div class="panel-head"><h3>生效中的写暂停</h3><span>${data.paused_policies.length} 条</span></div>
        ${data.paused_policies.length ? `<div class="system-list">${data.paused_policies.map(policy => `
          <div class="system-row policy-row"><div class="system-name"><strong>${escapeHtml(policy.scope_type)} · ${escapeHtml(policy.scope_value)}</strong><span>${escapeHtml(policy.reason)}</span></div>${badge(policy.state)}</div>`).join("")}</div>` : empty("没有生效中的写暂停")}
      </section>
    </div>
    <div class="view-head section-spaced-sm"><div><h2>最近操作</h2><p>仅展示运行元数据，不展示业务字段和值。</p></div></div>
    ${operationTable(data.recent_operations)}
  `;
}
function metric(label, value, hint, extra = "") { return `<div class="metric ${extra}"><span class="label">${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><span class="hint">${escapeHtml(hint)}</span></div>`; }

function principalBindingSummary(user) {
  return Object.entries(user.principal_bindings || {}).map(([system, binding]) => {
    const principal = binding.verified || binding.expected || "--";
    return `${escapeHtml(system)}: ${escapeHtml(principal)}`;
  }).join("<br>") || "--";
}
async function renderUsers() {
  const [users, tokens] = await Promise.all([api("/api/users"), api("/api/tokens")]);
  const userRows = users.items.map(user => `<tr><td><strong>${escapeHtml(user.user_subject)}</strong></td><td>${principalBindingSummary(user)}</td><td>${user.active_token_count} / ${user.token_count}</td><td>${Object.entries(user.sessions).map(([system, value]) => `${escapeHtml(system)} ${badge(value)}`).join(" ") || "--"}</td><td><div class="actions">${state.account.role === "admin" ? `<button class="button secondary small" data-pause-user="${escapeHtml(user.user_subject)}">暂停写入</button>` : ""}</div></td></tr>`);
  const tokenRows = tokens.items.map(token => `<tr><td class="code">${shortId(token.token_id)}</td><td>${escapeHtml(token.label || "未命名")}</td><td>${escapeHtml(token.user_subject)}</td><td class="truncate">${escapeHtml(token.scopes.join(", "))}</td><td>${badge(token.state)}</td><td>${fmtTime(token.expires_at)}</td><td><div class="actions">${state.account.role === "admin" && token.state === "active" ? `<button class="button secondary small" data-revoke-token="${token.token_id}">撤销</button>` : ""}</div></td></tr>`);
  content.innerHTML = `<div class="toolbar"><div><strong>身份绑定</strong><div class="muted">令牌密钥只在签发时显示一次。</div></div>${state.account.role === "admin" ? '<button class="button primary" data-issue-token>签发令牌</button>' : ""}</div>
    ${table(["用户标识", "各系统下游主体", "有效 / 全部令牌", "系统会话", ""], userRows)}
    <div class="view-head section-spaced"><div><h2>MCP Token</h2><p>管理员看不到任何已签发令牌的密钥。</p></div></div>
    ${table(["Token ID", "标签", "用户", "权限范围", "状态", "到期时间", ""], tokenRows)}`;
}

async function renderSessions() {
  const data = await api("/api/sessions");
  const rows = data.items.map(session => `<tr><td>${escapeHtml(session.user_subject)}</td><td>${escapeHtml(session.system_id)}</td><td>${escapeHtml(session.expected_principal_ref || "--")}</td><td>${escapeHtml(session.downstream_principal_ref || "--")}</td><td>${badge(session.state)}</td><td>${fmtTime(session.last_verified_at)}</td><td>${fmtTime(session.last_keepalive_at)}</td><td class="truncate" title="${escapeHtml(session.last_error || "")}">${escapeHtml(session.last_error || "--")}</td><td><div class="actions">${state.account.role === "admin" ? `<button class="button secondary small" data-check-session="${session.session_id}">实时检查</button><button class="button secondary small" data-rebind-session="${session.session_id}" data-expected-principal="${escapeHtml(session.expected_principal_ref || "")}">修改绑定</button>${session.state === "active" ? `<button class="button secondary small" data-invalidate-session="${session.session_id}">失效</button>` : ""}` : ""}</div></td></tr>`);
  content.innerHTML = `<div class="view-head"><div><h2>用户 × 系统会话矩阵</h2><p>身份按系统独立绑定。修改绑定会清除该系统登录态并要求重新登录，不影响其他系统。</p></div></div>${table(["用户", "系统", "预期主体", "已验证主体", "状态", "最近验证", "最近保活", "错误", ""], rows)}`;
}

async function renderCapabilities() {
  const [capabilities, policies] = await Promise.all([api("/api/capabilities"), api("/api/policies")]);
  const policyRows = policies.items.map(policy => `<tr><td>${escapeHtml(policy.scope_type)}</td><td class="code">${escapeHtml(policy.scope_value)}</td><td>${escapeHtml(policy.capability_version)}</td><td>${badge(policy.state)}</td><td class="truncate" title="${escapeHtml(policy.reason)}">${escapeHtml(policy.reason)}</td><td>${escapeHtml(policy.updated_by)}</td><td>${fmtTime(policy.updated_at)}</td><td><div class="actions">${state.account.role === "admin" && policy.state === "paused" ? `<button class="button secondary small" data-resume-policy="${policy.policy_id}">恢复</button>` : ""}</div></td></tr>`);
  const capabilityRows = capabilities.items.map(item => `<tr><td class="code">${escapeHtml(item.name)}</td><td>${escapeHtml(item.system)}</td><td>${escapeHtml(item.effect)}</td><td>${escapeHtml(item.version)}</td><td>${item.effect === "read" ? badge("available") : item.paused_by.length ? badge("paused") : badge("active")}</td><td><div class="actions">${state.account.role === "admin" && item.effect !== "read" ? `<button class="button secondary small" data-pause-capability="${escapeHtml(item.name)}" data-version="${escapeHtml(item.version)}">暂停</button>` : ""}</div></td></tr>`);
  content.innerHTML = `<div class="toolbar"><div><strong>写操作控制</strong><div class="muted">可按全局、系统、用户或具体能力暂停，不影响读取能力。</div></div>${state.account.role === "admin" ? '<div class="actions"><button class="button secondary" data-pause-system="oa">暂停 OA</button><button class="button secondary" data-pause-system="taihua">暂停泰华</button><button class="button danger" data-global-pause>全局暂停写入</button></div>' : ""}</div>
    ${table(["范围", "对象", "版本", "状态", "原因", "操作人", "更新时间", ""], policyRows)}
    <div class="view-head section-spaced"><div><h2>能力目录</h2><p>暂停策略在准备与最终提交边界都会重新检查。</p></div></div>
    ${table(["能力", "系统", "效果", "版本", "状态", ""], capabilityRows)}`;
}

async function renderOperations() {
  const data = await api("/api/operations?limit=300");
  content.innerHTML = `<div class="view-head"><div><h2>能力执行记录</h2><p>不展示输入摘要、结果正文和业务字段。</p></div></div>${operationTable(data.items)}`;
}
function operationTable(items) {
  const rows = items.map(item => `<tr><td class="code">${shortId(item.operation_id)}</td><td>${escapeHtml(item.user_subject)}</td><td class="code">${escapeHtml(item.capability_name)}</td><td>${badge(item.status)}</td><td class="truncate" title="${escapeHtml(item.error_message || "")}">${escapeHtml(item.error_code || "--")}</td><td>${fmtTime(item.created_at)}</td><td>${fmtTime(item.finished_at)}</td></tr>`);
  return table(["Operation ID", "用户", "能力", "状态", "错误", "开始", "结束"], rows);
}

async function renderInteractions() {
  const data = await api("/api/interactions?limit=300");
  const rows = data.items.map(item => `<tr><td class="code">${shortId(item.interaction_id)}</td><td>${escapeHtml(item.user_subject)}</td><td>${escapeHtml(item.system_id)}</td><td>${escapeHtml(item.interaction_type)}</td><td class="truncate">${escapeHtml(item.title)}</td><td>${badge(item.state)}</td><td>${fmtTime(item.created_at)}</td><td>${fmtTime(item.expires_at)}</td></tr>`);
  content.innerHTML = `<div class="view-head"><div><h2>可信交互时间线</h2><p>只看交互状态；不展示卡片 URL、字段值、密码或授权计划。</p></div></div>${table(["Interaction ID", "用户", "系统", "类型", "标题", "状态", "创建", "到期"], rows)}`;
}

async function renderRuntime() {
  const [data, accounts] = await Promise.all([api("/api/runtime"), api("/api/admin-accounts")]);
  const accountRows = accounts.items.map(account => `<tr><td>${escapeHtml(account.username)}</td><td>${escapeHtml(account.role)}</td><td>${badge(account.state)}</td><td>${account.must_change_password ? badge("pending") : badge("active")}</td><td>${fmtTime(account.last_login_at)}</td><td>${fmtTime(account.created_at)}</td></tr>`);
  content.innerHTML = `<div class="metric-grid">
    ${metric("发布版本", data.release_id, "当前服务构建")}
    ${metric("启动时间", fmtTime(data.started_at), "中心进程")}
    ${metric("管理 API", data.admin_api, "独立认证域")}
    ${metric("数据库", data.database, "SQLite WAL")}
    ${metric("保活租约", data.session_keepalive.activity_lease_seconds ? `${Math.round(data.session_keepalive.activity_lease_seconds / 86400)} 天` : "关闭", "仅真实活动续租")}
  </div><div class="view-head section-spaced"><div><h2>已配置系统</h2><p>控制台只报告状态，不提供 systemd 重启或业务代操作。</p></div></div>
  ${table(["系统 ID", "名称", "状态"], data.systems.map(system => `<tr><td class="code">${escapeHtml(system.system_id)}</td><td>${escapeHtml(system.label)}</td><td>${badge(system.configured ? "active" : "failed")}</td></tr>`))}
  <div class="toolbar section-spaced"><div><strong>管理账户</strong><div class="muted">管理员可执行控制动作，审计员仅可查看。</div></div>${state.account.role === "admin" ? '<button class="button primary" data-create-admin>新建账户</button>' : ""}</div>
  ${table(["用户名", "角色", "状态", "初始密码", "最近登录", "创建时间"], accountRows)}`;
}
async function renderAudit() {
  const data = await api("/api/audit?limit=400");
  const rows = data.items.map(item => `<tr><td>${fmtTime(item.created_at)}</td><td>${escapeHtml(item.actor_username || "匿名")}</td><td class="code">${escapeHtml(item.action)}</td><td>${escapeHtml(item.target_type || "--")}</td><td class="code">${shortId(item.target_id)}</td><td class="truncate" title="${escapeHtml(item.reason || "")}">${escapeHtml(item.reason || "--")}</td><td>${badge(item.result === "succeeded" ? "succeeded" : "failed")}</td><td>${escapeHtml(item.request_ip || "--")}</td></tr>`);
  content.innerHTML = `<div class="view-head"><div><h2>独立管理审计</h2><p>管理事件仅追加写入，包含操作人、来源、原因和前后状态。</p></div></div>${table(["时间", "管理员", "动作", "对象类型", "对象 ID", "原因", "结果", "来源 IP"], rows)}`;
}

function openModal({ kicker = "管理操作", title, body, submit = "确认", danger = false, action, locked = false }) {
  $("#modal-kicker").textContent = kicker;
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = body;
  $("#modal-submit").textContent = submit;
  $("#modal-submit").className = `button ${danger ? "danger" : "primary"}`;
  $("#modal-cancel").classList.toggle("hidden", locked);
  $("#modal-close").classList.toggle("hidden", locked);
  $("#modal-error").textContent = "";
  state.modalAction = action;
  modal.showModal();
}
function closeModal() { if (modal.open) modal.close(); state.modalAction = null; }
function reasonField(label = "操作原因") { return `<label>${escapeHtml(label)}<textarea name="reason" maxlength="500" required placeholder="说明本次管理操作的原因，将写入审计日志"></textarea></label>`; }
function openPasswordModal(locked = false) {
  openModal({
    kicker: "账户安全", title: locked ? "修改初始密码" : "修改密码", submit: "更新密码", locked,
    body: `<label>当前密码<input name="current_password" type="password" autocomplete="current-password" required></label><label>新密码<input name="new_password" type="password" autocomplete="new-password" minlength="12" required></label><label>确认新密码<input name="confirm_password" type="password" autocomplete="new-password" minlength="12" required></label><p class="muted">至少 12 位，并使用大写、小写、数字、符号中的三类。</p>`,
    action: async form => {
      if (form.get("new_password") !== form.get("confirm_password")) throw new Error("两次输入的新密码不一致");
      const result = await api("/api/account/password", { method: "POST", body: JSON.stringify({ current_password: form.get("current_password"), new_password: form.get("new_password") }) });
      state.account = result.account;
      $("#password-banner").classList.add("hidden");
      closeModal(); toast("密码已更新"); await loadView(state.view);
    }
  });
}
function openAdminAccount() {
  openModal({ title: "新建管理账户", submit: "创建账户", body: `<label>用户名<input name="username" pattern="[A-Za-z][A-Za-z0-9_.-]{2,63}" required></label><label>角色<select name="role"><option value="auditor">审计员（只读）</option><option value="admin">管理员</option></select></label>${reasonField()}`, action: async form => {
    const account = await api("/api/admin-accounts", { method: "POST", body: JSON.stringify({ username: form.get("username"), role: form.get("role"), reason: form.get("reason") }) });
    openModal({ kicker: "只显示一次", title: "账户已创建", submit: "完成", body: `<p>${escapeHtml(account.username)} 首次登录后必须修改初始密码。</p><div id="issued-secret" class="secret-box">${escapeHtml(account.bootstrap_password)}</div><button type="button" class="button secondary" data-copy-secret>复制初始密码</button>`, action: async () => { closeModal(); await loadView("runtime"); } });
  } });
}
function openIssueToken() {
  const checks = ["oa:read","oa:write:draft","oa:write:approval","oa:write:meeting","oa:write:submit","oa:write:revoke","taihua:read","taihua:write:worklog","yuque:read"].map(scope => `<label class="check"><input type="checkbox" name="scope" value="${scope}" ${scope === "oa:read" ? "checked" : ""}>${scope}</label>`).join("");
  openModal({ title: "签发 MCP Token", submit: "签发", body: `<label>用户标识<input name="user_subject" required></label><div><strong>各系统预期主体</strong><p class="muted">已有绑定可以留空；首次开通某个系统时必须填写该系统识别到的账号或主体。</p></div><label>OA 主体<input name="principal_oa" maxlength="256"></label><label>泰华主体<input name="principal_taihua" maxlength="256"></label><label>语雀主体<input name="principal_yuque" maxlength="256"></label><label>标签<input name="label" maxlength="120"></label><label>有效小时数<input name="ttl_hours" type="number" min="1" max="2160" value="720" required></label><div><strong>权限范围</strong><div class="checkbox-grid checkbox-spaced">${checks}</div></div>${reasonField()}`,
    action: async form => {
      const scopes = form.getAll("scope");
      const principal_bindings = Object.fromEntries(["oa", "taihua", "yuque"].map(system => [system, String(form.get(`principal_${system}`) || "").trim()]).filter(([, principal]) => principal));
      const issued = await api("/api/tokens", { method: "POST", body: JSON.stringify({ user_subject: form.get("user_subject"), principal_bindings, label: form.get("label"), ttl_hours: Number(form.get("ttl_hours")), scopes, reason: form.get("reason") }) });
      openModal({ kicker: "只显示一次", title: "Token 已签发", submit: "完成", body: `<p>请将密钥配置到可信 MCP 客户端。关闭后无法再次查看。</p><div id="issued-secret" class="secret-box">${escapeHtml(issued.token_secret)}</div><button type="button" class="button secondary" data-copy-secret>复制密钥</button>`, action: async () => { closeModal(); await loadView("users"); } });
    }
  });
}
function openRebindSession(target) {
  openModal({ title: "修改系统身份绑定", submit: "修改并清除登录态", danger: true, body: `<label>预期下游主体<input name="expected_principal_ref" maxlength="256" value="${escapeHtml(target.dataset.expectedPrincipal || "")}" required></label><p class="muted">只影响这一用户在当前系统中的会话。修改后必须重新登录。</p>${reasonField()}`, action: async form => {
    await api(`/api/sessions/${target.dataset.rebindSession}/rebind`, { method: "POST", body: JSON.stringify({ expected_principal_ref: form.get("expected_principal_ref"), reason: form.get("reason") }) });
    closeModal(); toast("系统身份绑定已更新"); await loadView("sessions");
  } });
}
function openPause({ scopeType, scopeValue, version = "*", title }) {
  openModal({ title, submit: "暂停写入", danger: true, body: `<p>读取能力不受影响；新的准备动作和已经授权但尚未提交的写操作都会被阻断。</p>${reasonField()}`, action: async form => { await api("/api/policies/pause", { method: "POST", body: JSON.stringify({ scope_type: scopeType, scope_value: scopeValue, capability_version: version, reason: form.get("reason") }) }); closeModal(); toast("写暂停策略已生效"); await loadView(state.view); } });
}
function openReasonAction({ title, submit, danger = false, request }) {
  openModal({ title, submit, danger, body: reasonField(), action: async form => { await request(form.get("reason")); closeModal(); toast(`${title}已完成`); await loadView(state.view); } });
}

$("#login-form").addEventListener("submit", async event => {
  event.preventDefault(); $("#login-error").textContent = "";
  const loginForm = event.currentTarget;
  const form = new FormData(loginForm);
  try {
    const result = await api("/api/login", { method: "POST", body: JSON.stringify({ username: form.get("username"), password: form.get("password") }) });
    loginForm.reset(); showApp(result.account);
    if (result.account.must_change_password) openPasswordModal(true); else await loadView("overview");
  } catch (error) { $("#login-error").textContent = error.message; }
});
$("#nav").addEventListener("click", event => { const button = event.target.closest("button[data-view]"); if (button) loadView(button.dataset.view); });
$("#refresh-button").addEventListener("click", () => loadView(state.view));
$("#account-button").addEventListener("click", () => $("#account-menu").classList.toggle("hidden"));
$("#change-password-button").addEventListener("click", () => { $("#account-menu").classList.add("hidden"); openPasswordModal(false); });
$("#banner-password-button").addEventListener("click", () => openPasswordModal(true));
$("#logout-button").addEventListener("click", async () => { try { await api("/api/logout", { method: "POST", body: "{}" }); } finally { showLogin(); } });
$("#modal-close").addEventListener("click", closeModal);
$("#modal-cancel").addEventListener("click", closeModal);
$("#modal-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!state.modalAction) return;
  $("#modal-error").textContent = ""; $("#modal-submit").disabled = true;
  try { await state.modalAction(new FormData(event.currentTarget)); }
  catch (error) { $("#modal-error").textContent = error.message; }
  finally { $("#modal-submit").disabled = false; }
});
content.addEventListener("click", async event => {
  const target = event.target.closest("button"); if (!target) return;
  if (target.matches("[data-issue-token]")) openIssueToken();
  else if (target.matches("[data-create-admin]")) openAdminAccount();
  else if (target.dataset.pauseUser) openPause({ scopeType: "user", scopeValue: target.dataset.pauseUser, title: `暂停 ${target.dataset.pauseUser} 的写入` });
  else if (target.dataset.pauseCapability) openPause({ scopeType: "capability", scopeValue: target.dataset.pauseCapability, version: target.dataset.version, title: "暂停具体能力" });
  else if (target.dataset.pauseSystem) openPause({ scopeType: "system", scopeValue: target.dataset.pauseSystem, title: "暂停系统写入" });
  else if (target.matches("[data-global-pause]")) openPause({ scopeType: "global", scopeValue: "*", title: "全局暂停所有写入" });
  else if (target.dataset.revokeToken) openReasonAction({ title: "撤销 Token", submit: "撤销", danger: true, request: reason => api(`/api/tokens/${target.dataset.revokeToken}/revoke`, { method: "POST", body: JSON.stringify({ reason }) }) });
  else if (target.dataset.checkSession) openReasonAction({ title: "实时检查会话", submit: "检查", request: reason => api(`/api/sessions/${target.dataset.checkSession}/check`, { method: "POST", body: JSON.stringify({ reason }) }) });
  else if (target.dataset.rebindSession) openRebindSession(target);
  else if (target.dataset.invalidateSession) openReasonAction({ title: "使会话失效", submit: "确认失效", danger: true, request: reason => api(`/api/sessions/${target.dataset.invalidateSession}/invalidate`, { method: "POST", body: JSON.stringify({ reason }) }) });
  else if (target.dataset.resumePolicy) openReasonAction({ title: "恢复写入", submit: "恢复", request: reason => api(`/api/policies/${target.dataset.resumePolicy}/resume`, { method: "POST", body: JSON.stringify({ reason }) }) });
});
modal.addEventListener("click", event => {
  if (event.target.matches("[data-copy-secret]")) {
    navigator.clipboard.writeText($("#issued-secret").textContent).then(() => toast("密钥已复制"), () => toast("复制失败，请手动选择", true));
  }
});
initialize();
