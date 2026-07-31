const state = {
  account: null,
  activeView: "chat",
  tasks: [],
  selectedTaskId: null,
  enrollmentTimer: null,
  chatTimer: null,
  taskListTimer: null,
  eventSource: null,
  timelineCursor: 0,
  liveMessages: new Map(),
  historyMessages: new Map(),
  localMessages: new Map(),
  syncedMessages: new Map(),
  taskCards: new Map(),
  taskCardMeta: new Map(),
  taskSyncTimers: new Map(),
  toasts: new Map(),
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
  loadTasks();
  loadChat().finally(() => {
    openTimelineStream();
    loadGatewayStatus();
  });
}

async function logout() {
  try {
    await api("/api/logout", { method: "POST", body: {}, csrf: true });
  } catch {}
  state.eventSource?.close();
  clearTimeout(state.chatTimer);
  clearTimeout(state.taskListTimer);
  state.taskSyncTimers.forEach((timer) => clearTimeout(timer));
  state.taskSyncTimers.clear();
  state.historyMessages.clear();
  state.localMessages.clear();
  state.syncedMessages.clear();
  state.taskCards.clear();
  state.taskCardMeta.clear();
  state.timelineCursor = 0;
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
    const [history, timeline] = await Promise.all([
      api("/api/chat/history?limit=120"),
      api("/api/timeline?limit=240"),
    ]);
    state.historyMessages.clear();
    history.messages.forEach((message, index) => {
      const key = historyMessageKey(message, index);
      const item = messageElement(message);
      setTimelineNode(item, {
        key,
        createdAt: message.timestamp,
        order: index,
      });
      state.historyMessages.set(key, item);
    });
    state.localMessages.clear();
    timeline.items.forEach((entry) => ingestTimelineEntry(entry, false));
    state.timelineCursor = Math.max(
      state.timelineCursor,
      Number(timeline.cursor) || 0,
    );
    await hydrateTaskCards({ render: false });
    renderChatTimeline();
    container.scrollTop = container.scrollHeight;
  } catch (error) {
    if (container.childElementCount === 0) {
      container.replaceChildren(
        messageElement({
          role: "system",
          text: `智能体暂时不可用：${friendlyError(error)}`,
        }),
      );
    }
  }
}

function messageElement(message) {
  const item = document.createElement("article");
  item.className = `message ${message.role}`;
  const text = document.createElement("div");
  text.textContent = message.text;
  item.append(text);
  if (message.timestamp || message.sourceLabel) {
    const time = document.createElement("time");
    time.className = "message-meta";
    time.textContent = [
      message.sourceLabel,
      message.timestamp ? formatTime(message.timestamp) : null,
    ].filter(Boolean).join(" · ");
    item.append(time);
  }
  return item;
}

function historyMessageKey(message, index) {
  if (message.id) return `history:${message.id}`;
  return [
    "history",
    message.role,
    message.timestamp || index,
    textHash(message.text),
  ].join(":");
}

function textHash(value) {
  let hash = 2166136261;
  for (const character of String(value || "")) {
    hash ^= character.codePointAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16);
}

function setTimelineNode(node, { key, createdAt, order = 0 }) {
  const parsed = parseTimestampMilliseconds(createdAt);
  node.dataset.timelineKey = String(key);
  node.dataset.timelineAt = String(
    Number.isFinite(parsed) ? parsed : Number(order) || 0,
  );
  node.dataset.timelineOrder = String(Number(order) || 0);
}

function ingestTimelineEntry(entry, render = true) {
  const sequence = Number(entry?.sequence) || 0;
  state.timelineCursor = Math.max(state.timelineCursor, sequence);
  if (entry?.entry_type === "chat_message") {
    if (entry.source?.is_origin || !entry.entry_id || !entry.text) return;
    let item = state.syncedMessages.get(entry.entry_id);
    if (!item) {
      item = messageElement({
        role: entry.role === "user" ? "user" : "assistant",
        text: entry.text,
        timestamp: entry.created_at,
        sourceLabel: entry.source?.display_label || "其他端",
      });
      state.syncedMessages.set(entry.entry_id, item);
    }
    setTimelineNode(item, {
      key: `timeline:${entry.entry_id}`,
      createdAt: entry.created_at,
      order: sequence,
    });
    if (render) {
      renderChatTimeline();
      scrollChat();
    }
    return;
  }
  if (entry?.entry_type === "task_event") {
    const taskId = entry.task_id || entry.payload?.taskId;
    const eventType = entry.payload?.eventType;
    if (taskId && !state.taskCardMeta.has(taskId)) {
      state.taskCardMeta.set(taskId, {
        createdAt: entry.created_at,
        sequence,
      });
    }
    if (render) scheduleTaskSync(taskId, eventType);
  }
}

function renderChatTimeline() {
  const container = $("#chat-messages");
  const stable = [
    ...state.historyMessages.values(),
    ...state.localMessages.values(),
    ...state.syncedMessages.values(),
    ...state.taskCards.values(),
  ].filter(Boolean);
  stable.sort((left, right) => {
    const time =
      Number(left.dataset.timelineAt) - Number(right.dataset.timelineAt);
    if (time !== 0) return time;
    const order =
      Number(left.dataset.timelineOrder) -
      Number(right.dataset.timelineOrder);
    if (order !== 0) return order;
    return String(left.dataset.timelineKey).localeCompare(
      String(right.dataset.timelineKey),
    );
  });
  const live = [...state.liveMessages.values()]
    .map((item) => item.item)
    .filter((item) => item?.isConnected);
  if (stable.length === 0 && live.length === 0) {
    container.replaceChildren(
      messageElement({
        role: "system",
        text: "开始一个新的工作会话",
      }),
    );
    return;
  }
  container.replaceChildren(...stable, ...live);
}

async function sendChat(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const textarea = form.elements.message;
  const message = textarea.value.trim();
  if (!message) return;
  textarea.value = "";
  await executeChatMessage(message, form);
}

async function executeChatMessage(message, form = $("#chat-form")) {
  const idempotencyKey = crypto.randomUUID();
  const local = messageElement({ role: "user", text: message });
  setTimelineNode(local, {
    key: `local:${idempotencyKey}`,
    createdAt: new Date().toISOString(),
    order: Number.MAX_SAFE_INTEGER - 1,
  });
  state.localMessages.set(idempotencyKey, local);
  renderChatTimeline();
  scrollChat();
  setBusy(form, true);
  const live = ensureLiveMessage(idempotencyKey);
  live.requestMessage = message;
  addLiveProgress(idempotencyKey, "正在连接智能体", "active");
  try {
    await consumeChatStream({ message, idempotencyKey });
    scheduleChatRefresh(500, 1);
  } catch (error) {
    if (!error.rendered) {
      renderRunFailure(
        error.runId || idempotencyKey,
        friendlyError(error),
        false,
        message,
      );
      toast(friendlyError(error), true, error.code || "chat-failed");
    }
  } finally {
    setBusy(form, false);
    form.elements.message?.focus();
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
  let runId = idempotencyKey;
  let hadToolActivity = false;
  let terminalFailure = null;
  for (;;) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split("\n\n");
    buffer = done ? "" : blocks.pop() || "";
    for (const block of blocks) {
      const event = parseSseBlock(block);
      if (!event) continue;
      if (event.name === "accepted" && event.data.runId) {
        runId = event.data.runId;
        adoptLiveMessage(idempotencyKey, runId, message);
        addLiveProgress(runId, "请求已交给智能体", "active");
      } else if (event.name === "progress") {
        if (event.data.kind === "tool") hadToolActivity = true;
        handleChatProgress(event.data);
      } else if (event.name === "chat") {
        if (["error", "aborted"].includes(event.data.state)) {
          terminalFailure = event.data;
        } else {
          handleChatDelta(event.data);
        }
      } else if (event.name === "stream-error") {
        streamError = event.data.code || "GATEWAY_STREAM_FAILED";
      }
    }
    if (done) break;
  }
  if (streamError) {
    const error = new Error(streamError);
    error.code = streamError;
    error.runId = runId;
    throw error;
  }
  if (terminalFailure) {
    const safeToRetry =
      terminalFailure.state === "error" && !hadToolActivity;
    const text = agentFailureMessage(
      terminalFailure.text,
      safeToRetry,
      terminalFailure.state,
    );
    renderRunFailure(runId, text, safeToRetry, message);
    const error = new Error(text);
    error.code =
      terminalFailure.state === "aborted"
        ? "AGENT_RUN_ABORTED"
        : "AGENT_RUN_FAILED";
    error.runId = runId;
    error.rendered = true;
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
  const actions = document.createElement("div");
  actions.className = "live-actions";
  item.append(progress, text, actions);
  $("#chat-messages").append(item);
  live = {
    item,
    progress,
    text,
    actions,
    rows: new Map(),
    requestMessage: null,
  };
  state.liveMessages.set(runId, live);
  scrollChat();
  return live;
}

function adoptLiveMessage(previousRunId, runId, requestMessage) {
  if (previousRunId === runId) return ensureLiveMessage(runId);
  const previous = state.liveMessages.get(previousRunId);
  const existing = state.liveMessages.get(runId);
  if (existing?.item?.isConnected) return existing;
  if (!previous?.item?.isConnected) return ensureLiveMessage(runId);
  state.liveMessages.delete(previousRunId);
  previous.requestMessage = requestMessage;
  state.liveMessages.set(runId, previous);
  return previous;
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
    live.actions.replaceChildren();
    live.item.classList.remove("live-message");
    state.liveMessages.delete(payload.runId);
    scheduleChatRefresh(500, 1);
  }
  scrollChat();
}

function renderRunFailure(runId, text, canRetry, requestMessage) {
  const live = ensureLiveMessage(runId);
  live.item.classList.add("failed");
  live.text.textContent = text;
  live.actions.replaceChildren();
  addLiveProgress(runId, "处理未完成", "failed");
  if (canRetry && requestMessage) {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "secondary retry-command";
    retry.textContent = "重新发送";
    retry.addEventListener("click", async () => {
      retry.disabled = true;
      await executeChatMessage(requestMessage);
    });
    live.actions.append(retry);
  }
  scrollChat();
}

function agentFailureMessage(value, safeToRetry, state) {
  if (state === "aborted") return "智能体处理已停止。";
  const text = String(value || "").trim();
  const networkFailure =
    /network|connection|econnreset|before producing a reply/i.test(text);
  if (networkFailure && safeToRetry) {
    return "智能体网络连接中断，尚未调用业务工具。可安全地重新发送这条指令。";
  }
  if (networkFailure) {
    return "智能体网络连接中断。由于任务已经开始调用业务工具，请先查看任务状态再决定是否重试。";
  }
  return text || "智能体未能完成本次处理。";
}

function scrollChat() {
  const container = $("#chat-messages");
  container.scrollTop = container.scrollHeight;
}

async function hydrateTaskCards({ render = true } = {}) {
  try {
    const result = await api("/api/tasks?active_only=false&limit=30");
    const active = new Set(["active", "waiting_user", "running"]);
    const recentCutoff = Date.now() - 6 * 60 * 60 * 1000;
    const candidates = result.items
      .filter((task) => {
        const updatedAt = Date.parse(task.updated_at || "");
        return active.has(task.status) ||
          (Number.isFinite(updatedAt) && updatedAt >= recentCutoff);
      })
      .slice(0, 8)
      .reverse();
    const details = await Promise.allSettled(
      candidates.map((task) =>
        api(`/api/tasks/${encodeURIComponent(task.task_id)}`),
      ),
    );
    const candidateIds = new Set(candidates.map((task) => task.task_id));
    details.forEach((result) => {
      if (result.status === "fulfilled") {
        upsertTaskCard(result.value, { render: false });
      }
    });
    state.taskCards.forEach((card, taskId) => {
      if (!candidateIds.has(taskId)) {
        card.remove();
        state.taskCards.delete(taskId);
        state.taskCardMeta.delete(taskId);
      }
    });
    if (render) renderChatTimeline();
  } catch {}
}

function scheduleTaskSync(taskId, eventType) {
  if (!taskId) return;
  clearTimeout(state.taskSyncTimers.get(taskId));
  state.taskSyncTimers.set(
    taskId,
    setTimeout(async () => {
      state.taskSyncTimers.delete(taskId);
      await syncTaskCard(taskId);
    }, 220),
  );
  clearTimeout(state.taskListTimer);
  state.taskListTimer = setTimeout(loadTasks, 260);
  if (
    ["task.operation.failed", "task.operation.outcome_unknown"].includes(
      eventType,
    )
  ) {
    toast(
      eventType === "task.operation.outcome_unknown"
        ? "一项业务操作的最终结果需要核对。"
        : "一项业务操作执行失败。",
      true,
      `task:${taskId}:failure`,
    );
  }
}

async function syncTaskCard(taskId) {
  try {
    const result = await api(`/api/tasks/${encodeURIComponent(taskId)}`);
    upsertTaskCard(result);
    if (state.selectedTaskId === taskId) renderTaskDetail(result);
  } catch (error) {
    if (error.code !== "TASK_NOT_FOUND") {
      toast(
        friendlyError(error),
        true,
        `task:${taskId}:${error.code || "sync"}`,
      );
    }
  }
}

function upsertTaskCard(result, { render = true } = {}) {
  const task = result?.task;
  if (!task?.task_id) return;
  const container = $("#chat-messages");
  const nearBottom =
    container.scrollHeight - container.scrollTop - container.clientHeight < 120;
  let card = state.taskCards.get(task.task_id);
  if (!card) {
    card = document.createElement("article");
    card.className = "message assistant application-card";
    card.dataset.taskId = task.task_id;
    state.taskCards.set(task.task_id, card);
  }
  const timelineMeta = state.taskCardMeta.get(task.task_id) || {
    createdAt: task.created_at,
    sequence: 0,
  };
  state.taskCardMeta.set(task.task_id, timelineMeta);
  setTimelineNode(card, {
    key: `task:${task.task_id}`,
    createdAt: timelineMeta.createdAt || task.created_at,
    order: timelineMeta.sequence || 0,
  });
  card.className =
    `message assistant application-card ${escapeClass(task.status)}`;
  card.replaceChildren();

  const header = document.createElement("div");
  header.className = "application-card-header";
  const heading = document.createElement("div");
  const eyebrow = document.createElement("span");
  eyebrow.className = "application-card-eyebrow";
  eyebrow.textContent = "AGENTBRIDGE 应用卡";
  const title = document.createElement("strong");
  title.className = "application-card-title";
  title.textContent = displayTaskTitle(task.title);
  heading.append(eyebrow, title);
  const status = document.createElement("span");
  status.className =
    `application-card-status ${escapeClass(task.status)}`;
  status.textContent = statusLabel(task.status);
  header.append(heading, status);
  card.append(header);

  const interaction = result.interaction;
  const description = document.createElement("p");
  description.className = "application-card-copy";
  const interactionActive =
    interaction &&
    ["pending", "processing"].includes(interaction.state);
  description.textContent = interactionActive
    ? interaction.message || taskCardStatusMessage(task.status)
    : taskCardStatusMessage(task.status);
  card.append(description);

  const facts = document.createElement("dl");
  facts.className = "application-card-facts";
  const systemName = interaction?.display?.systemName;
  const effect = interaction?.display?.effect;
  if (systemName) addDetail(facts, "系统", systemName);
  if (effect) addDetail(facts, "影响", effect);
  const latestEvent = result.events?.at(-1);
  if (latestEvent) {
    addDetail(
      facts,
      "最新进展",
      `${eventLabel(latestEvent.event_type)} · ${formatTime(latestEvent.created_at)}`,
    );
  }
  if (facts.childElementCount) card.append(facts);

  const actions = document.createElement("div");
  actions.className = "application-card-actions";
  const url = interaction?.presentation?.url;
  if (
    interactionActive &&
    typeof url === "string" &&
    /^https:\/\//.test(url)
  ) {
    const action = document.createElement("a");
    action.className = "primary";
    action.href = url;
    action.target = "_blank";
    action.rel = "noopener";
    action.textContent = interactionActionLabel(interaction.type);
    actions.append(action);
  }
  const progress = document.createElement("button");
  progress.type = "button";
  progress.className = "secondary";
  progress.textContent = "查看进度";
  progress.addEventListener("click", () => {
    switchView("tasks");
    loadTaskDetail(task.task_id);
  });
  actions.append(progress);
  card.append(actions);
  if (render) renderChatTimeline();
  if (nearBottom) scrollChat();
}

function taskCardStatusMessage(status) {
  return (
    {
      active: "任务已创建，等待智能体继续处理。",
      waiting_user: "任务正在等待你的填写或确认。",
      running: "智能体正在执行已确认的操作。",
      succeeded: "任务已经完成。",
      failed: "任务未能完成，请查看进度了解原因。",
      outcome_unknown: "最终结果未能确认，请先到业务系统核对。",
      canceled: "任务已取消。",
      expired: "任务交互已过期，请重新发起。",
    }[status] || "任务状态已更新。"
  );
}

function displayTaskTitle(value) {
  const title = String(value || "").trim();
  return (
    {
      "Prepare OA Efficiency-Data Approval": "OA 效能数据审批",
      "Prepare OA Travel-Expense Approval": "OA 差旅费审批",
      "Prepare OA Labor-Contract Renewal Approval": "OA 劳动合同续签审批",
      "Prepare OA Weekly-Report Acknowledgement": "OA 周报阅办",
      "Prepare OA Standard-Collaboration Approval": "OA 普通事项审批",
      "Prepare OA Workflow Revoke": "OA 流程撤销",
      "Prepare OA Business Trip Draft": "OA 出差申请草稿",
      "Prepare OA Business Trip Submission": "OA 出差申请提交",
      "Prepare OA Leave Draft": "OA 请假申请草稿",
      "Prepare OA Leave Submission": "OA 请假申请提交",
      "Prepare OA Missed-Punch Draft": "OA 补签申请草稿",
      "Prepare OA Missed-Punch Approval": "OA 补签申请审批",
      "Prepare OA Meeting Creation": "OA 会议创建",
      "Prepare Taihua Work Log": "泰华工作日志提交",
    }[title] ||
    title ||
    "AgentBridge 任务"
  );
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
    button.querySelector(".task-title").textContent =
      displayTaskTitle(task.title);
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
  title.textContent = displayTaskTitle(task.title);
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

function openTimelineStream() {
  state.eventSource?.close();
  const query = state.timelineCursor
    ? `?after=${encodeURIComponent(state.timelineCursor)}`
    : "";
  const source = new EventSource(`/api/timeline/stream${query}`);
  source.addEventListener("cursor", (event) => {
    state.timelineCursor = Math.max(
      state.timelineCursor,
      Number(event.lastEventId) || 0,
    );
  });
  source.addEventListener("timeline", (event) => {
    let payload = {};
    try {
      payload = JSON.parse(event.data || "{}");
    } catch {
      payload = {};
    }
    state.timelineCursor = Math.max(
      state.timelineCursor,
      Number(event.lastEventId) || Number(payload.sequence) || 0,
    );
    ingestTimelineEntry(payload);
  });
  source.onerror = () => {
    source.close();
    setTimeout(openTimelineStream, 5000);
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

function toast(message, isError = false, key = message) {
  const normalizedKey = String(key || message);
  let item = state.toasts.get(normalizedKey);
  if (!item?.isConnected) {
    item = document.createElement("div");
    state.toasts.set(normalizedKey, item);
    $("#toast-region").append(item);
  }
  item.className = `toast${isError ? " error" : ""}`;
  item.textContent = message;
  clearTimeout(item.dismissTimer);
  while ($("#toast-region").childElementCount > 2) {
    const oldest = $("#toast-region").firstElementChild;
    state.toasts.forEach((value, toastKey) => {
      if (value === oldest) state.toasts.delete(toastKey);
    });
    oldest?.remove();
  }
  item.dismissTimer = setTimeout(() => {
    item.remove();
    state.toasts.delete(normalizedKey);
  }, 5200);
}

function emptyState(text) {
  const item = document.createElement("div");
  item.className = "empty-state";
  item.textContent = text;
  return item;
}

function formatTime(value) {
  if (!value) return "—";
  const parsed = parseTimestampMilliseconds(value);
  if (!Number.isFinite(parsed)) return String(value);
  const date = new Date(parsed);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function parseTimestampMilliseconds(value) {
  const text = String(value ?? "").trim();
  if (!text) return Number.NaN;
  if (/^\d{10,13}$/.test(text)) {
    const numeric = Number(text);
    return numeric < 100_000_000_000 ? numeric * 1_000 : numeric;
  }
  return Date.parse(text);
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
      "task.interaction.expired": "可信交互已过期",
      "task.interaction.failed": "可信交互失败",
      "task.interaction.superseded": "可信交互已更新",
      "task.operation.running": "操作执行中",
      "task.operation.succeeded": "操作成功",
      "task.operation.failed": "操作失败",
      "task.operation.outcome_unknown": "操作结果待核对",
      "task.canceled": "任务已取消",
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

function interactionActionLabel(type) {
  return (
    {
      credential: "安全登录",
      business_input: "填写信息",
      execution_authorization: "核对并确认",
    }[type] || "继续处理"
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
