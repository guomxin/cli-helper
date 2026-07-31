const state = {
  account: null,
  activeView: "chat",
  tasks: [],
  selectedTaskId: null,
  enrollmentTimer: null,
  chatTimer: null,
  eventSource: null,
  liveMessages: new Map(),
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

document.addEventListener("DOMContentLoaded", bootstrap);

async function bootstrap() {
  bindActions();
  try {
    const session = await api("/api/session");
    if (session.authenticated) {
      enterWorkspace(session.account);
    } else {
      showAuth();
      await restoreEnrollment();
    }
  } catch (error) {
    showAuth();
    showAuthError(error.message);
  }
}

function bindActions() {
  $("#login-tab").addEventListener("click", () => switchAuth("login"));
  $("#enroll-tab").addEventListener("click", () => switchAuth("enroll"));
  $("#login-form").addEventListener("submit", login);
  $("#start-link").addEventListener("click", startEnrollment);
  $("#enroll-complete").addEventListener("submit", completeEnrollment);
  $("#logout-button").addEventListener("click", logout);
  $("#chat-form").addEventListener("submit", sendChat);
  $("#refresh-chat").addEventListener("click", loadChat);
  $("#refresh-tasks").addEventListener("click", loadTasks);
  $("#active-only").addEventListener("change", loadTasks);
  $("#refresh-endpoints").addEventListener("click", loadEndpoints);
  $$(".nav-item").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
}

function showAuth() {
  $("#auth-view").hidden = false;
  $("#app-view").hidden = true;
}

function switchAuth(mode) {
  const login = mode === "login";
  $("#login-tab").classList.toggle("active", login);
  $("#enroll-tab").classList.toggle("active", !login);
  $("#login-tab").setAttribute("aria-selected", String(login));
  $("#enroll-tab").setAttribute("aria-selected", String(!login));
  $("#login-form").hidden = !login;
  $("#enroll-flow").hidden = login;
  showAuthError("");
}

async function login(event) {
  event.preventDefault();
  const loginForm = event.currentTarget;
  const form = new FormData(loginForm);
  setBusy(loginForm, true);
  showAuthError("");
  try {
    const result = await api("/api/login", {
      method: "POST",
      body: {
        username: form.get("username"),
        password: form.get("password"),
      },
    });
    enterWorkspace(result.account);
  } catch (error) {
    showAuthError(friendlyError(error));
  } finally {
    setBusy(loginForm, false);
  }
}

async function startEnrollment() {
  const button = $("#start-link");
  button.disabled = true;
  showAuthError("");
  try {
    const result = await api("/api/enrollment/start", {
      method: "POST",
      body: {},
    });
    renderEnrollmentPending(result);
    pollEnrollment();
  } catch (error) {
    showAuthError(friendlyError(error));
    button.disabled = false;
  }
}

async function restoreEnrollment() {
  try {
    const result = await api("/api/enrollment/status");
    if (!result.active) return;
    switchAuth("enroll");
    if (result.state === "confirmed") {
      renderEnrollmentConfirmed();
    } else if (result.state === "pending") {
      $("#enroll-start").hidden = false;
      $("#enroll-pending").hidden = true;
      $("#start-link").disabled = false;
      showAuthError("页面已刷新，请重新生成配对码。");
    }
  } catch {}
}

function renderEnrollmentPending(result) {
  $("#enroll-start").hidden = true;
  $("#enroll-pending").hidden = false;
  $("#enroll-complete").hidden = true;
  $("#link-code").textContent = result.linkCode;
  $("#link-command").textContent = `/agentbridge link ${result.linkCode}`;
  $("#link-status").textContent = "等待可信端确认";
}

function pollEnrollment() {
  clearInterval(state.enrollmentTimer);
  state.enrollmentTimer = setInterval(async () => {
    try {
      const result = await api("/api/enrollment/status");
      if (result.state === "confirmed") {
        clearInterval(state.enrollmentTimer);
        renderEnrollmentConfirmed();
      } else if (["expired", "consumed"].includes(result.state)) {
        clearInterval(state.enrollmentTimer);
        $("#link-status").textContent = "配对码已失效";
        $("#start-link").disabled = false;
      }
    } catch {}
  }, 1500);
}

function renderEnrollmentConfirmed() {
  $("#enroll-pending").hidden = true;
  $("#enroll-complete").hidden = false;
}

async function completeEnrollment(event) {
  event.preventDefault();
  const enrollmentForm = event.currentTarget;
  const form = new FormData(enrollmentForm);
  const password = String(form.get("password") || "");
  if (password !== String(form.get("passwordConfirm") || "")) {
    showAuthError("两次输入的密码不一致。");
    return;
  }
  setBusy(enrollmentForm, true);
  try {
    const result = await api("/api/enrollment/complete", {
      method: "POST",
      body: {
        username: form.get("username"),
        password,
      },
    });
    enterWorkspace(result.account);
  } catch (error) {
    showAuthError(friendlyError(error));
  } finally {
    setBusy(enrollmentForm, false);
  }
}

function enterWorkspace(account) {
  clearInterval(state.enrollmentTimer);
  state.account = account;
  $("#auth-view").hidden = true;
  $("#app-view").hidden = false;
  $("#account-name").textContent = account.username;
  switchView("chat");
  loadGatewayStatus();
  loadTasks();
  loadChat();
  openEventStream();
}

async function logout() {
  try {
    await api("/api/logout", { method: "POST", body: {}, csrf: true });
  } catch {}
  state.eventSource?.close();
  clearTimeout(state.chatTimer);
  state.account = null;
  location.reload();
}

function switchView(view) {
  state.activeView = view;
  $$(".nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === view);
  });
  $$(".workspace-view").forEach((item) => {
    item.hidden = item.id !== `view-${view}`;
  });
  if (view === "tasks") {
    $("#task-detail").classList.add("mobile-empty");
    loadTasks();
  }
  if (view === "endpoints") loadEndpoints();
}

async function loadGatewayStatus() {
  const element = $("#gateway-state");
  const dot = element.querySelector(".status-dot");
  try {
    const result = await api("/api/gateway");
    dot.className = `status-dot ${result.available ? "online" : "offline"}`;
    element.title = result.available
      ? `OpenClaw ${result.version || "已连接"}`
      : `OpenClaw 不可用：${result.code}`;
  } catch {
    dot.className = "status-dot offline";
  }
}

async function loadChat() {
  const container = $("#chat-messages");
  try {
    const result = await api("/api/chat/history?limit=120");
    state.liveMessages.clear();
    container.replaceChildren(
      ...result.messages.map((message) => messageElement(message)),
    );
    if (result.messages.length === 0) {
      container.append(
        messageElement({
          role: "system",
          text: "开始一个新的工作会话",
        }),
      );
    }
    container.scrollTop = container.scrollHeight;
  } catch (error) {
    container.replaceChildren(
      messageElement({
        role: "system",
        text: `智能体暂时不可用：${friendlyError(error)}`,
      }),
    );
  }
}

function messageElement(message) {
  const item = document.createElement("article");
  item.className = `message ${message.role}`;
  const text = document.createElement("div");
  text.textContent = message.text;
  item.append(text);
  if (message.timestamp) {
    const time = document.createElement("time");
    time.className = "message-meta";
    time.textContent = formatTime(message.timestamp);
    item.append(time);
  }
  return item;
}

async function sendChat(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const textarea = form.elements.message;
  const message = textarea.value.trim();
  if (!message) return;
  textarea.value = "";
  $("#chat-messages").append(messageElement({ role: "user", text: message }));
  $("#chat-messages").scrollTop = $("#chat-messages").scrollHeight;
  setBusy(form, true);
  const idempotencyKey = crypto.randomUUID();
  addLiveProgress(idempotencyKey, "正在连接智能体", "active");
  try {
    await consumeChatStream({ message, idempotencyKey });
    scheduleChatRefresh(500, 1);
  } catch (error) {
    addLiveProgress(idempotencyKey, "处理失败", "failed");
    toast(friendlyError(error), true);
  } finally {
    setBusy(form, false);
    textarea.focus();
  }
}

function scheduleChatRefresh(delay, attempts) {
  clearTimeout(state.chatTimer);
  state.chatTimer = setTimeout(async () => {
    await loadChat();
    if (attempts > 1) scheduleChatRefresh(1800, attempts - 1);
  }, delay);
}

async function consumeChatStream({ message, idempotencyKey }) {
  const response = await fetch("/api/chat/send-stream", {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
      "X-AgentBridge-CSRF": cookieValue(
        "agentbridge_workspace_csrf",
      ),
    },
    credentials: "same-origin",
    body: JSON.stringify({ message, idempotencyKey }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const error = new Error(
      payload?.error?.message || payload?.error?.code || "请求失败",
    );
    error.code = payload?.error?.code;
    throw error;
  }
  if (!response.body) {
    const error = new Error("浏览器不支持流式响应");
    error.code = "STREAM_UNAVAILABLE";
    throw error;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let streamError = null;
  for (;;) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split("\n\n");
    buffer = done ? "" : blocks.pop() || "";
    for (const block of blocks) {
      const event = parseSseBlock(block);
      if (!event) continue;
      if (event.name === "accepted" && event.data.runId) {
        addLiveProgress(event.data.runId, "请求已交给智能体", "active");
      } else if (event.name === "progress") {
        handleChatProgress(event.data);
      } else if (event.name === "chat") {
        handleChatDelta(event.data);
      } else if (event.name === "stream-error") {
        streamError = event.data.code || "GATEWAY_STREAM_FAILED";
      }
    }
    if (done) break;
  }
  if (streamError) {
    const error = new Error(streamError);
    error.code = streamError;
    throw error;
  }
}

function parseSseBlock(block) {
  let name = "message";
  const data = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      name = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      data.push(line.slice(5).trimStart());
    }
  }
  if (data.length === 0) return null;
  try {
    const value = JSON.parse(data.join("\n"));
    return value && typeof value === "object"
      ? { name, data: value }
      : null;
  } catch {
    return null;
  }
}

function ensureLiveMessage(runId) {
  let live = state.liveMessages.get(runId);
  if (live?.item?.isConnected) return live;
  const item = document.createElement("article");
  item.className = "message assistant live-message";
  const progress = document.createElement("div");
  progress.className = "live-progress";
  const text = document.createElement("div");
  text.className = "live-text";
  item.append(progress, text);
  $("#chat-messages").append(item);
  live = { item, progress, text, rows: new Map() };
  state.liveMessages.set(runId, live);
  scrollChat();
  return live;
}

function handleChatProgress(payload) {
  if (!payload.runId) return;
  if (payload.kind === "preamble" && payload.text) {
    addLiveProgress(payload.runId, payload.text, "active");
    return;
  }
  if (payload.label) {
    const complete =
      payload.phase === "result" || payload.phase === "end";
    addLiveProgress(
      payload.runId,
      complete
        ? payload.label.replace(/^正在/, "已完成")
        : payload.label,
      complete
        ? "complete"
        : payload.phase === "error" || payload.phase === "aborted"
          ? "failed"
          : "active",
    );
  }
}

function addLiveProgress(runId, label, status) {
  const live = ensureLiveMessage(runId);
  const key = label.replace(/^(正在|已完成)/, "");
  let row = live.rows.get(key);
  if (!row) {
    row = document.createElement("div");
    row.className = "live-progress-row";
    const dot = document.createElement("span");
    dot.className = "live-progress-dot";
    const copy = document.createElement("span");
    row.append(dot, copy);
    live.progress.append(row);
    live.rows.set(key, row);
  }
  row.className = `live-progress-row ${status}`;
  row.lastElementChild.textContent = label;
  while (live.progress.childElementCount > 8) {
    const first = live.progress.firstElementChild;
    const firstLabel = first?.lastElementChild?.textContent || "";
    live.rows.delete(firstLabel.replace(/^(正在|已完成)/, ""));
    first?.remove();
  }
  scrollChat();
}

function handleChatDelta(payload) {
  if (!payload.runId) return;
  const live = ensureLiveMessage(payload.runId);
  if (typeof payload.text === "string") {
    live.text.textContent = payload.text;
  }
  if (payload.state === "final") {
    live.progress.replaceChildren();
    live.item.classList.remove("live-message");
    state.liveMessages.delete(payload.runId);
    scheduleChatRefresh(500, 1);
  } else if (["error", "aborted"].includes(payload.state)) {
    live.item.classList.add("failed");
    addLiveProgress(
      payload.runId,
      payload.state === "aborted" ? "处理已停止" : "处理失败",
      "failed",
    );
    scheduleChatRefresh(1000, 1);
  }
  scrollChat();
}

function scrollChat() {
  const container = $("#chat-messages");
  container.scrollTop = container.scrollHeight;
}

async function loadTasks() {
  try {
    const activeOnly = $("#active-only").checked;
    const result = await api(
      `/api/tasks?active_only=${activeOnly}&limit=150`,
    );
    state.tasks = result.items;
    renderTasks();
    const active = result.items.filter((task) =>
      ["active", "waiting_user", "running"].includes(task.status),
    ).length;
    $("#active-task-count").textContent = String(active);
  } catch (error) {
    toast(friendlyError(error), true);
  }
}

function renderTasks() {
  const list = $("#task-list");
  list.replaceChildren();
  if (state.tasks.length === 0) {
    list.append(emptyState("没有符合条件的任务"));
    return;
  }
  state.tasks.forEach((task) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `task-row ${task.task_id === state.selectedTaskId ? "active" : ""}`;
    button.innerHTML = `
      <span class="task-status-bar ${escapeClass(task.status)}"></span>
      <span>
        <span class="task-title"></span>
        <span class="task-meta">
          <span>${statusLabel(task.status)}</span>
          <span>${formatTime(task.updated_at)}</span>
        </span>
      </span>`;
    button.querySelector(".task-title").textContent = task.title;
    button.addEventListener("click", () => loadTaskDetail(task.task_id));
    list.append(button);
  });
}

async function loadTaskDetail(taskId) {
  state.selectedTaskId = taskId;
  renderTasks();
  const detail = $("#task-detail");
  detail.classList.remove("mobile-empty");
  detail.replaceChildren(emptyState("正在读取任务"));
  try {
    const result = await api(`/api/tasks/${encodeURIComponent(taskId)}`);
    renderTaskDetail(result);
  } catch (error) {
    detail.replaceChildren(emptyState(friendlyError(error)));
  }
}

function renderTaskDetail(result) {
  const task = result.task;
  const detail = $("#task-detail");
  detail.replaceChildren();

  const heading = document.createElement("div");
  heading.className = "detail-heading";
  const back = document.createElement("button");
  back.type = "button";
  back.className = "icon-command mobile-detail-back";
  back.setAttribute("aria-label", "返回任务列表");
  back.title = "返回任务列表";
  back.textContent = "←";
  back.addEventListener("click", () => {
    detail.classList.add("mobile-empty");
  });
  const headingCopy = document.createElement("div");
  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = statusLabel(task.status);
  const title = document.createElement("h2");
  title.textContent = task.title;
  headingCopy.append(eyebrow, title);
  heading.append(back, headingCopy);
  detail.append(heading);

  const metadata = document.createElement("dl");
  metadata.className = "detail-grid";
  addDetail(metadata, "来源", task.agent_host);
  addDetail(metadata, "创建时间", formatTime(task.created_at));
  addDetail(metadata, "更新时间", formatTime(task.updated_at));
  addDetail(metadata, "任务编号", task.task_id);
  detail.append(metadata);

  const interaction = result.interaction;
  const url = interaction?.presentation?.url;
  if (
    interaction &&
    ["pending", "processing"].includes(interaction.state) &&
    typeof url === "string" &&
    /^https:\/\//.test(url)
  ) {
    const band = document.createElement("div");
    band.className = "interaction-band";
    const label = document.createElement("span");
    label.textContent = interactionLabel(interaction.type);
    const link = document.createElement("a");
    link.className = "primary";
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "打开";
    band.append(label, link);
    detail.append(band);
  }

  const timeline = document.createElement("div");
  timeline.className = "timeline";
  [...result.events].reverse().forEach((event) => {
    const item = document.createElement("div");
    item.className = "timeline-item";
    const dot = document.createElement("span");
    dot.className = "timeline-dot";
    const copy = document.createElement("div");
    copy.className = "timeline-copy";
    const eventTitle = document.createElement("strong");
    eventTitle.textContent = eventLabel(event.event_type);
    const time = document.createElement("time");
    time.textContent = formatTime(event.created_at);
    copy.append(eventTitle, time);
    item.append(dot, copy);
    timeline.append(item);
  });
  detail.append(timeline);
}

function addDetail(list, term, value) {
  const dt = document.createElement("dt");
  const dd = document.createElement("dd");
  dt.textContent = term;
  dd.textContent = value || "—";
  list.append(dt, dd);
}

async function loadEndpoints() {
  const body = $("#endpoint-list");
  try {
    const result = await api("/api/endpoints");
    body.replaceChildren();
    result.items.forEach((endpoint) => {
      const row = document.createElement("tr");
      const label = endpoint.display_label;
      row.innerHTML = `
        <td></td>
        <td>${endpointType(endpoint.client_type)}</td>
        <td><span class="state-label"><span class="status-dot ${endpoint.state === "active" ? "online" : "offline"}"></span>${endpoint.state === "active" ? "有效" : "已停用"}</span></td>
        <td>${formatTime(endpoint.last_seen_at)}</td>`;
      row.firstElementChild.textContent = label;
      body.append(row);
    });
    if (result.items.length === 0) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 4;
      cell.append(emptyState("暂无关联端点"));
      row.append(cell);
      body.append(row);
    }
  } catch (error) {
    toast(friendlyError(error), true);
  }
}

function openEventStream() {
  state.eventSource?.close();
  const source = new EventSource("/api/events/stream");
  source.addEventListener("task", (event) => {
    let payload = {};
    try {
      payload = JSON.parse(event.data || "{}");
    } catch {
      payload = {};
    }
    loadTasks();
    if (state.selectedTaskId === payload.task_id) {
      loadTaskDetail(state.selectedTaskId);
    }
    toast(
      payload.event_type === "task.interaction.waiting"
        ? "新的可信确认已可在网页端处理"
        : "任务状态已更新",
    );
  });
  source.onerror = () => {
    source.close();
    setTimeout(openEventStream, 5000);
  };
  state.eventSource = source;
}

async function api(path, options = {}) {
  const headers = { Accept: "application/json" };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (options.csrf) {
    headers["X-AgentBridge-CSRF"] = cookieValue(
      "agentbridge_workspace_csrf",
    );
  }
  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
    credentials: "same-origin",
    body:
      options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(
      payload?.error?.message || payload?.error?.code || "请求失败",
    );
    error.code = payload?.error?.code;
    throw error;
  }
  return payload;
}

function cookieValue(name) {
  const prefix = `${name}=`;
  return (
    document.cookie
      .split(";")
      .map((item) => item.trim())
      .find((item) => item.startsWith(prefix))
      ?.slice(prefix.length) || ""
  );
}

function setBusy(form, busy) {
  [...form.querySelectorAll("button, input, textarea")].forEach((element) => {
    element.disabled = busy;
  });
}

function showAuthError(message) {
  $("#auth-error").textContent = message || "";
}

function friendlyError(error) {
  const labels = {
    LOGIN_FAILED: "用户名或密码不正确。",
    LOGIN_RATE_LIMITED: "登录尝试过多，请稍后再试。",
    WORKSPACE_LINK_INVALID: "配对尚未完成或已经失效。",
    WORKSPACE_CONFLICT: "该身份或用户名已经绑定网页账号。",
    GATEWAY_NOT_CONFIGURED: "智能体连接尚未配置。",
    PAIRING_REQUIRED: "AgentBridge 服务器尚未获准连接 OpenClaw。",
    AUTHENTICATION_REQUIRED: "网页会话已失效，请重新登录。",
  };
  return labels[error.code] || error.message || "操作没有完成。";
}

function toast(message, isError = false) {
  const item = document.createElement("div");
  item.className = `toast${isError ? " error" : ""}`;
  item.textContent = message;
  $("#toast-region").append(item);
  setTimeout(() => item.remove(), 4200);
}

function emptyState(text) {
  const item = document.createElement("div");
  item.className = "empty-state";
  item.textContent = text;
  return item;
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function statusLabel(status) {
  return (
    {
      active: "进行中",
      waiting_user: "等待确认",
      running: "执行中",
      succeeded: "已完成",
      failed: "失败",
      outcome_unknown: "结果待核对",
      canceled: "已取消",
      expired: "已过期",
    }[status] || status
  );
}

function eventLabel(type) {
  return (
    {
      "task.created": "任务已创建",
      "task.operation.linked": "操作已关联",
      "task.interaction.pending": "等待用户处理",
      "task.interaction.completed": "可信交互已完成",
      "task.operation.succeeded": "操作成功",
      "task.operation.failed": "操作失败",
      "task.operation.outcome_unknown": "操作结果待核对",
    }[type] || type.replaceAll(".", " / ")
  );
}

function interactionLabel(type) {
  return (
    {
      credential: "需要完成安全登录",
      business_input: "需要补充业务信息",
      execution_authorization: "需要核对并确认",
    }[type] || "需要用户处理"
  );
}

function endpointType(type) {
  return (
    {
      telegram: "Telegram",
      "openclaw-weixin": "微信",
      web: "网页",
    }[type] || type
  );
}

function escapeClass(value) {
  return String(value || "").replace(/[^a-z0-9_-]/gi, "");
}
