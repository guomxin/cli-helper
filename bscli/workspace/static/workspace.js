const state = {
  account: null,
  activeView: "chat",
  tasks: [],
  selectedTaskId: null,
  enrollmentTimer: null,
  chatTimer: null,
  taskListTimer: null,
  eventSource: null,
  timelineReconnectTimer: null,
  timelineReconcileTimer: null,
  timelineReconcileActive: false,
  clientVersionTimer: null,
  clientVersionCheckActive: false,
  timelineCursor: 0,
  liveMessages: new Map(),
  activeStreams: new Map(),
  historyMessages: new Map(),
  localMessages: new Map(),
  syncedMessages: new Map(),
  taskCards: new Map(),
  taskCardMeta: new Map(),
  taskSyncTimers: new Map(),
  toasts: new Map(),
  composerAttachments: [],
};

const MAX_COMPOSER_IMAGES = 4;
const MAX_COMPOSER_IMAGE_BYTES = 6 * 1024 * 1024;
const MAX_COMPOSER_IMAGES_TOTAL_BYTES = 12 * 1024 * 1024;
const SUPPORTED_COMPOSER_IMAGE_TYPES = new Set([
  "image/jpeg",
  "image/png",
  "image/webp",
]);

const CLIENT_VERSION =
  document.querySelector('meta[name="agentbridge-workspace-version"]')
    ?.content || "";

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
  $("#attach-image").addEventListener("click", () => {
    $("#image-input").click();
  });
  $("#image-input").addEventListener("change", async (event) => {
    await addComposerFiles(event.currentTarget.files);
    event.currentTarget.value = "";
  });
  const messageInput = $("#chat-form textarea[name='message']");
  messageInput.addEventListener("paste", handleComposerPaste);
  const composer = $("#chat-form");
  composer.addEventListener("dragover", handleComposerDragOver);
  composer.addEventListener("dragleave", handleComposerDragLeave);
  composer.addEventListener("drop", handleComposerDrop);
  $("#refresh-chat").addEventListener("click", loadChat);
  $("#refresh-tasks").addEventListener("click", loadTasks);
  $("#active-only").addEventListener("change", loadTasks);
  $("#refresh-endpoints").addEventListener("click", loadEndpoints);
  $$(".nav-item").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
  document.addEventListener("visibilitychange", refreshWorkspaceState);
  window.addEventListener("focus", refreshWorkspaceState);
  $("#image-viewer-close").addEventListener("click", closeImageViewer);
  $("#image-viewer-backdrop").addEventListener("click", closeImageViewer);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("#image-viewer").hidden) {
      closeImageViewer();
    }
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
  stopWorkspaceObservers();
  state.account = account;
  $("#auth-view").hidden = true;
  $("#app-view").hidden = false;
  $("#account-name").textContent = account.username;
  switchView("chat");
  loadTasks();
  loadChat().finally(() => {
    openTimelineStream();
    startWorkspaceObservers();
    loadGatewayStatus();
  });
}

async function logout() {
  try {
    await api("/api/logout", { method: "POST", body: {}, csrf: true });
  } catch {}
  stopWorkspaceObservers();
  clearTimeout(state.chatTimer);
  clearTimeout(state.taskListTimer);
  state.taskSyncTimers.forEach((timer) => clearTimeout(timer));
  state.taskSyncTimers.clear();
  state.historyMessages.clear();
  state.localMessages.clear();
  state.syncedMessages.clear();
  state.taskCards.clear();
  state.taskCardMeta.clear();
  state.composerAttachments = [];
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
    dismissTerminalLiveMessages();
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
  item.dataset.messageRole = String(message.role || "");
  item.dataset.messageTextHash = textHash(message.text);
  const text = document.createElement("div");
  text.textContent = message.text;
  item.append(text);
  if (Array.isArray(message.images) && message.images.length > 0) {
    const images = document.createElement("div");
    images.className = "message-image-list";
    message.images.forEach((image) => {
      const source = image.dataUrl || image.mediaUrl;
      const downloadSource = image.downloadUrl || source;
      const fileName = image.fileName || "附加图片";
      const open = document.createElement("button");
      open.type = "button";
      open.className = "message-image-button";
      open.title = `放大查看 ${fileName}`;
      open.setAttribute("aria-label", `放大查看 ${fileName}`);
      const preview = document.createElement("img");
      preview.src = source;
      preview.alt = fileName;
      preview.loading = "lazy";
      preview.addEventListener("error", () => {
        preview.classList.add("unavailable");
        preview.alt = `${fileName}（已不可用）`;
        open.disabled = true;
      });
      open.addEventListener("click", () => {
        openImageViewer({ source, downloadSource, fileName });
      });
      open.append(preview);
      images.append(open);
    });
    item.append(images);
  }
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

function openImageViewer({ source, downloadSource, fileName }) {
  if (!source) return;
  const viewer = $("#image-viewer");
  const image = $("#image-viewer-image");
  const caption = $("#image-viewer-caption");
  const download = $("#image-viewer-download");
  image.src = source;
  image.alt = fileName;
  caption.textContent = fileName;
  download.href = downloadSource || source;
  download.download = fileName;
  download.setAttribute("aria-label", `下载原图 ${fileName}`);
  viewer.hidden = false;
  document.body.classList.add("image-viewer-open");
  $("#image-viewer-close").focus();
}

function closeImageViewer() {
  const viewer = $("#image-viewer");
  if (viewer.hidden) return;
  viewer.hidden = true;
  $("#image-viewer-image").removeAttribute("src");
  $("#image-viewer-download").removeAttribute("href");
  document.body.classList.remove("image-viewer-open");
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
    if (entry.source?.is_origin) {
      reconcileOriginChatMessage(entry);
      return;
    }
    if (!entry.entry_id || !entry.text) return;
    let item = state.syncedMessages.get(entry.entry_id);
    if (!item) {
      item = messageElement({
        role: entry.role === "user" ? "user" : "assistant",
        text: entry.text,
        images: timelineEntryImages(entry),
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
    const interactionId = entry.payload?.payload?.interactionId;
    const cardKey = interactionId
      ? `${taskId}:interaction:${interactionId}`
      : `${taskId}:summary`;
    if (taskId && !state.taskCardMeta.has(cardKey)) {
      state.taskCardMeta.set(cardKey, {
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

async function handleComposerPaste(event) {
  const files = [...(event.clipboardData?.items || [])]
    .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter(Boolean);
  if (files.length === 0) return;
  event.preventDefault();
  await addComposerFiles(files);
}

function handleComposerDragOver(event) {
  if (![...(event.dataTransfer?.items || [])].some(
    (item) => item.kind === "file" && item.type.startsWith("image/"),
  )) {
    return;
  }
  event.preventDefault();
  event.currentTarget.classList.add("drag-active");
}

function handleComposerDragLeave(event) {
  if (!event.currentTarget.contains(event.relatedTarget)) {
    event.currentTarget.classList.remove("drag-active");
  }
}

async function handleComposerDrop(event) {
  event.currentTarget.classList.remove("drag-active");
  const files = [...(event.dataTransfer?.files || [])].filter((file) =>
    file.type.startsWith("image/"),
  );
  if (files.length === 0) return;
  event.preventDefault();
  await addComposerFiles(files);
}

async function addComposerFiles(fileList) {
  const available = MAX_COMPOSER_IMAGES - state.composerAttachments.length;
  if (available <= 0) {
    toast("一次最多添加 4 张图片。", true, "image-limit");
    return;
  }
  const files = [...(fileList || [])];
  if (files.length > available) {
    toast("一次最多添加 4 张图片。", true, "image-limit");
  }
  let totalBytes = state.composerAttachments.reduce(
    (total, attachment) => total + Number(attachment.size || 0),
    0,
  );
  for (const file of files.slice(0, available)) {
    const mimeType = normalizedImageType(file);
    if (!SUPPORTED_COMPOSER_IMAGE_TYPES.has(mimeType)) {
      toast("仅支持 JPEG、PNG 和 WebP 图片。", true, "image-type");
      continue;
    }
    if (!file.size || file.size > MAX_COMPOSER_IMAGE_BYTES) {
      toast("单张图片不能超过 6 MB。", true, "image-size");
      continue;
    }
    if (totalBytes + file.size > MAX_COMPOSER_IMAGES_TOTAL_BYTES) {
      toast("图片总大小不能超过 12 MB。", true, "image-total-size");
      continue;
    }
    try {
      state.composerAttachments.push(
        await readComposerImage(file, mimeType),
      );
      totalBytes += file.size;
    } catch {
      toast("图片读取失败，请重新选择。", true, "image-read");
    }
  }
  renderComposerAttachments();
}

function normalizedImageType(file) {
  const supplied = String(file?.type || "").toLowerCase();
  if (supplied === "image/jpg") return "image/jpeg";
  if (supplied) return supplied;
  const name = String(file?.name || "").toLowerCase();
  if (/\.jpe?g$/.test(name)) return "image/jpeg";
  if (/\.png$/.test(name)) return "image/png";
  if (/\.webp$/.test(name)) return "image/webp";
  return "";
}

function readComposerImage(file, mimeType) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("error", () => reject(reader.error));
    reader.addEventListener("load", () => {
      const dataUrl = String(reader.result || "");
      const separator = dataUrl.indexOf(",");
      const content = separator >= 0 ? dataUrl.slice(separator + 1) : "";
      if (!content) {
        reject(new Error("image content is empty"));
        return;
      }
      resolve({
        id: crypto.randomUUID(),
        fileName: String(file.name || "pasted-image").slice(0, 120),
        mimeType,
        content,
        dataUrl,
        size: file.size,
      });
    });
    reader.readAsDataURL(file);
  });
}

function renderComposerAttachments() {
  const container = $("#composer-attachments");
  container.replaceChildren();
  state.composerAttachments.forEach((attachment) => {
    const preview = document.createElement("div");
    preview.className = "attachment-preview";
    const image = document.createElement("img");
    image.src = attachment.dataUrl;
    image.alt = attachment.fileName;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "attachment-remove";
    remove.title = `移除 ${attachment.fileName}`;
    remove.setAttribute("aria-label", remove.title);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      state.composerAttachments = state.composerAttachments.filter(
        (item) => item.id !== attachment.id,
      );
      renderComposerAttachments();
    });
    preview.append(image, remove);
    container.append(preview);
  });
  container.hidden = state.composerAttachments.length === 0;
}

function clearComposerAttachments() {
  state.composerAttachments = [];
  renderComposerAttachments();
}

async function sendChat(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const textarea = form.elements.message;
  const attachments = state.composerAttachments.map((item) => ({ ...item }));
  const message =
    textarea.value.trim() ||
    (attachments.length > 0 ? "请处理附加图片中的内容。" : "");
  if (!message) return;
  textarea.value = "";
  clearComposerAttachments();
  await executeChatMessage(message, form, attachments);
}

async function executeChatMessage(
  message,
  form = $("#chat-form"),
  attachments = [],
) {
  dismissTerminalLiveMessages();
  const idempotencyKey = crypto.randomUUID();
  const local = messageElement({
    role: "user",
    text: message,
    images: attachments,
  });
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
    await consumeChatStream({ message, idempotencyKey, attachments });
    scheduleChatRefresh(500, 1);
  } catch (error) {
    if (!error.rendered) {
      const text = friendlyError(error);
      renderRunFailure(
        error.runId || idempotencyKey,
        text,
        error.safeToRetry === true,
        message,
        attachments,
      );
      toast(text, true, error.code || "chat-failed");
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

async function consumeChatStream({ message, idempotencyKey, attachments = [] }) {
  const activeStream = {
    controller: new AbortController(),
    requestMessage: message,
    requestAttachments: attachments,
    runIds: new Set(),
    terminal: false,
    timelineCompleted: false,
  };
  registerActiveStream(activeStream, idempotencyKey);
  let runId = idempotencyKey;
  let hadToolActivity = false;
  let terminalFailure = null;
  let streamFailure = null;
  let reader = null;
  try {
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
      signal: activeStream.controller.signal,
      body: JSON.stringify({
        message,
        idempotencyKey,
        attachments: attachments.map((item) => ({
          type: "image",
          mimeType: item.mimeType,
          fileName: item.fileName,
          content: item.content,
        })),
      }),
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
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let stopReading = false;
    readLoop:
    for (;;) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const blocks = buffer.split("\n\n");
      buffer = done ? "" : blocks.pop() || "";
      for (const block of blocks) {
        const event = parseSseBlock(block);
        if (!event) continue;
        if (event.name === "accepted" && event.data.runId) {
          const previousRunId = runId;
          runId = event.data.runId;
          registerActiveStream(activeStream, runId);
          adoptLiveMessage(previousRunId, runId, message);
          addLiveProgress(runId, "请求已交给智能体", "active");
        } else if (event.name === "progress") {
          if (event.data.kind === "tool") hadToolActivity = true;
          handleChatProgress(event.data);
        } else if (event.name === "chat") {
          if (["final", "error", "aborted"].includes(event.data.state)) {
            activeStream.terminal = true;
            stopReading = true;
          }
          if (["error", "aborted"].includes(event.data.state)) {
            terminalFailure = event.data;
          } else {
            handleChatDelta(event.data);
          }
        } else if (event.name === "stream-error") {
          streamFailure = event.data;
          stopReading = true;
        }
        if (stopReading) break readLoop;
      }
      if (done) break;
    }
    if (stopReading && reader) {
      await reader.cancel().catch(() => {});
    }
    if (streamFailure) {
      const code = streamFailure.code || "GATEWAY_STREAM_FAILED";
      const error = new Error(code);
      error.code = code;
      error.runId = runId;
      error.details = streamFailure.details || {};
      error.safeToRetry = streamFailure.safeToRetry === true;
      throw error;
    }
    if (terminalFailure) {
      const effectiveToolActivity =
        hadToolActivity || terminalFailure.hadToolActivity === true;
      const safeToRetry =
        terminalFailure.safeToRetry === true ||
        (terminalFailure.state === "error" && !effectiveToolActivity);
      const text = agentFailureMessage(
        terminalFailure.text,
        safeToRetry,
        terminalFailure.state,
      );
      renderRunFailure(runId, text, safeToRetry, message, attachments);
      const error = new Error(text);
      error.code =
        terminalFailure.state === "aborted"
          ? "AGENT_RUN_ABORTED"
          : "AGENT_RUN_FAILED";
      error.runId = runId;
      error.rendered = true;
      throw error;
    }
  } catch (error) {
    if (
      activeStream.timelineCompleted &&
      activeStream.controller.signal.aborted
    ) {
      return;
    }
    throw error;
  } finally {
    unregisterActiveStream(activeStream);
  }
}

function registerActiveStream(activeStream, runId) {
  if (!runId) return;
  activeStream.runIds.add(runId);
  state.activeStreams.set(runId, activeStream);
}

function unregisterActiveStream(activeStream) {
  activeStream.runIds.forEach((runId) => {
    if (state.activeStreams.get(runId) === activeStream) {
      state.activeStreams.delete(runId);
    }
  });
}

function reconcileOriginChatMessage(entry) {
  if (!entry?.text) return;
  const messageKey = String(entry.message_key || "");
  if (entry.role === "user") {
    const images = timelineEntryImages(entry);
    if (images.length === 0 || !entry.entry_id) return;
    const localPrefix = "workspace:user:";
    if (messageKey.startsWith(localPrefix)) {
      state.localMessages.delete(messageKey.slice(localPrefix.length));
    }
    removeMatchingHistoryMessage(entry);
    let item = state.syncedMessages.get(entry.entry_id);
    if (!item) {
      item = messageElement({
        role: "user",
        text: entry.text,
        images,
        timestamp: entry.created_at,
      });
      state.syncedMessages.set(entry.entry_id, item);
    }
    setTimelineNode(item, {
      key: `timeline:${entry.entry_id}`,
      createdAt: entry.created_at,
      order: Number(entry.sequence) || 0,
    });
    renderChatTimeline();
    return;
  }
  if (entry.role !== "assistant") return;
  const failurePrefix = "workspace:assistant:error:";
  const finalPrefix = "workspace:assistant:";
  const failed = messageKey.startsWith(failurePrefix);
  const runId = failed
    ? messageKey.slice(failurePrefix.length)
    : messageKey.startsWith(finalPrefix)
      ? messageKey.slice(finalPrefix.length)
      : "";
  const activeStream = state.activeStreams.get(runId);
  if (activeStream) {
    if (!activeStream.terminal) {
      activeStream.terminal = true;
      activeStream.timelineCompleted = true;
      if (failed) {
        renderRunFailure(
          runId,
          entry.text,
          false,
          activeStream.requestMessage,
          activeStream.requestAttachments,
        );
      } else {
        handleChatDelta({ runId, state: "final", text: entry.text });
      }
      activeStream.controller.abort();
    }
    return;
  }
  if (!entry.entry_id) return;
  removeMatchingHistoryMessage(entry);
  let item = state.syncedMessages.get(entry.entry_id);
  if (!item) {
    item = messageElement({
      role: "assistant",
      text: entry.text,
      timestamp: entry.created_at,
    });
    state.syncedMessages.set(entry.entry_id, item);
  }
  setTimelineNode(item, {
    key: `timeline:${entry.entry_id}`,
    createdAt: entry.created_at,
    order: Number(entry.sequence) || 0,
  });
  renderChatTimeline();
}

function timelineEntryImages(entry) {
  const attachments = Array.isArray(entry?.payload?.attachments)
    ? entry.payload.attachments
    : [];
  return attachments
    .filter(
      (attachment) =>
        attachment?.type === "image" &&
        typeof attachment.mediaUrl === "string" &&
        attachment.mediaUrl,
    )
    .sort(
      (left, right) =>
        Number(left.ordinal || 0) - Number(right.ordinal || 0),
    )
    .map((attachment) => ({
      mediaUrl: attachment.mediaUrl,
      downloadUrl: attachment.attachmentId
        ? `/api/timeline/attachments/${encodeURIComponent(attachment.attachmentId)}/download`
        : attachment.mediaUrl,
      fileName: attachment.fileName || "附加图片",
      mimeType: attachment.mimeType,
    }));
}

function removeMatchingHistoryMessage(entry) {
  const targetHash = textHash(entry.text);
  const targetAt = parseTimestampMilliseconds(entry.created_at);
  for (const [key, item] of state.historyMessages) {
    if (
      item.dataset.messageRole !== String(entry.role || "") ||
      item.dataset.messageTextHash !== targetHash
    ) {
      continue;
    }
    const historyAt = Number(item.dataset.timelineAt);
    if (
      Number.isFinite(targetAt) &&
      Number.isFinite(historyAt) &&
      Math.abs(historyAt - targetAt) > 5 * 60 * 1000
    ) {
      continue;
    }
    state.historyMessages.delete(key);
    return;
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

function renderRunFailure(
  runId,
  text,
  canRetry,
  requestMessage,
  requestAttachments = [],
) {
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
      await executeChatMessage(
        requestMessage,
        $("#chat-form"),
        requestAttachments,
      );
    });
    live.actions.append(retry);
  }
  scrollChat();
}

function dismissTerminalLiveMessages() {
  state.liveMessages.forEach((live, runId) => {
    if (!live?.item?.classList.contains("failed")) return;
    live.item.remove();
    state.liveMessages.delete(runId);
  });
}

function agentFailureMessage(value, safeToRetry, state) {
  const text = String(value || "").trim();
  if (state === "aborted" && text) return text;
  if (state === "aborted" && safeToRetry) {
    return "智能体处理已停止，尚未调用业务工具，可以安全地重新发送。";
  }
  if (state === "aborted") {
    return "智能体处理已停止；请先核对业务系统状态再决定是否重试。";
  }
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
    const [taskResponse, historyResponse] = await Promise.allSettled([
      api("/api/tasks?active_only=false&limit=30"),
      api("/api/artifacts/history?limit=20"),
    ]);
    if (taskResponse.status !== "fulfilled") {
      throw taskResponse.reason;
    }
    const result = taskResponse.value;
    const artifactHistory = historyResponse.status === "fulfilled"
      ? historyResponse.value.items || []
      : [];
    const active = new Set(["active", "waiting_user", "running"]);
    const recentCutoff = Date.now() - 6 * 60 * 60 * 1000;
    const recentCandidates = result.items
      .filter((task) => {
        const updatedAt = Date.parse(task.updated_at || "");
        return active.has(task.status) ||
          (Number.isFinite(updatedAt) && updatedAt >= recentCutoff);
      })
      .slice(0, 8);
    const recentIds = new Set(
      recentCandidates.map((task) => task.task_id),
    );
    const historicalDetails = new Map(
      artifactHistory
        .filter((detail) => detail?.task?.task_id)
        .map((detail) => [detail.task.task_id, detail]),
    );
    const candidates = [
      ...recentCandidates,
      ...artifactHistory
        .map((detail) => detail.task)
        .filter((task) => !recentIds.has(task.task_id)),
    ].reverse();
    const details = await Promise.allSettled(
      candidates.map((task) => {
        if (!recentIds.has(task.task_id)) {
          return Promise.resolve(historicalDetails.get(task.task_id));
        }
        return api(`/api/tasks/${encodeURIComponent(task.task_id)}`);
      }),
    );
    const candidateIds = new Set(candidates.map((task) => task.task_id));
    details.forEach((result) => {
      if (result.status === "fulfilled") {
        upsertTaskCard(result.value, { render: false });
      }
    });
    state.taskCards.forEach((card, cardKey) => {
      if (!candidateIds.has(card.dataset.taskId || cardKey)) {
        card.remove();
        state.taskCards.delete(cardKey);
        state.taskCardMeta.delete(cardKey);
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
  const interactions = Array.isArray(result.interactions) &&
    result.interactions.length
    ? result.interactions
    : result.interaction
      ? [result.interaction]
      : [];
  const variants = interactions.length
    ? interactions.map((interaction) => ({
        cardKey: `${task.task_id}:interaction:${interaction.interactionId}`,
        interaction,
      }))
    : [{ cardKey: `${task.task_id}:summary`, interaction: null }];
  const desiredKeys = new Set(variants.map((variant) => variant.cardKey));
  let nearBottom = false;
  variants.forEach((variant, index) => {
    nearBottom = upsertTaskCardVariant(
      result,
      variant.cardKey,
      variant.interaction,
      { showArtifacts: index === variants.length - 1 },
    ) || nearBottom;
  });
  state.taskCards.forEach((card, cardKey) => {
    if (
      card.dataset.taskId === task.task_id &&
      !desiredKeys.has(cardKey)
    ) {
      card.remove();
      state.taskCards.delete(cardKey);
      state.taskCardMeta.delete(cardKey);
    }
  });
  if (render) renderChatTimeline();
  if (nearBottom) scrollChat();
}

function upsertTaskCardVariant(
  result,
  cardKey,
  interaction,
  { showArtifacts = true } = {},
) {
  const task = result?.task;
  if (!task?.task_id) return;
  const container = $("#chat-messages");
  const nearBottom =
    container.scrollHeight - container.scrollTop - container.clientHeight < 120;
  let card = state.taskCards.get(cardKey);
  if (!card) {
    card = document.createElement("article");
    card.className = "message assistant application-card";
    card.dataset.taskId = task.task_id;
    card.dataset.interactionId = interaction?.interactionId || "";
    state.taskCards.set(cardKey, card);
  }
  const timelineMeta = state.taskCardMeta.get(cardKey) || {
    createdAt: interaction?.linkedAt || task.created_at,
    sequence: 0,
  };
  state.taskCardMeta.set(cardKey, timelineMeta);
  setTimelineNode(card, {
    key: `task:${cardKey}`,
    createdAt:
      timelineMeta.createdAt || interaction?.linkedAt || task.created_at,
    order: timelineMeta.sequence || 0,
  });
  const cardStatus = taskCardStatusForInteraction(
    interaction?.state,
    task.status,
  );
  card.className =
    `message assistant application-card ${escapeClass(cardStatus)}`;
  card.replaceChildren();

  const header = document.createElement("div");
  header.className = "application-card-header";
  const heading = document.createElement("div");
  const eyebrow = document.createElement("span");
  eyebrow.className = "application-card-eyebrow";
  eyebrow.textContent = "AGENTBRIDGE 应用卡";
  const title = document.createElement("strong");
  title.className = "application-card-title";
  title.textContent = interaction?.title || displayTaskTitle(task.title);
  heading.append(eyebrow, title);
  const status = document.createElement("span");
  status.className =
    `application-card-status ${escapeClass(cardStatus)}`;
  status.textContent = statusLabel(cardStatus);
  header.append(heading, status);
  card.append(header);

  const description = document.createElement("p");
  description.className = "application-card-copy";
  const interactionActive =
    interaction &&
    ["pending", "processing"].includes(interaction.state);
  description.textContent = interactionActive
    ? interaction.message || taskCardStatusMessage(cardStatus, task.summary)
    : taskCardStatusMessage(cardStatus, task.summary);
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
  if (showArtifacts) {
    appendArtifactList(card, result.artifacts, {
      compact: true,
      taskId: task.task_id,
    });
  }

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
  return nearBottom;
}

function taskCardStatusForInteraction(state, fallback) {
  return (
    {
      pending: "waiting_user",
      processing: "running",
      completed: "succeeded",
      declined: "canceled",
      expired: "expired",
      failed: "failed",
      superseded: "superseded",
    }[state] || fallback
  );
}

function taskCardStatusMessage(status, summary = null) {
  const deliveryMessage = taskCardArtifactDeliveryMessage(summary);
  if (deliveryMessage) return deliveryMessage;
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
      superseded: "任务已被更新的可信交互替换。",
    }[status] || "任务状态已更新。"
  );
}

function taskCardArtifactDeliveryMessage(summary) {
  const aggregate = summary?.artifactDeliveryAggregate;
  if (
    aggregate?.completionMeaning === "cross_endpoint_delivery_reported" &&
    String(aggregate.userMessage || "").trim()
  ) {
    return String(aggregate.userMessage).trim();
  }
  const delivery = summary?.artifactDelivery;
  if (!delivery || typeof delivery !== "object") return null;
  if (
    delivery.completionMeaning !== "endpoint_delivery_reported" ||
    !Number.isInteger(delivery.preparedCount)
  ) {
    return null;
  }
  const message = String(delivery.userMessage || "").trim();
  if (message) return message;
  const prepared = Math.max(0, delivery.preparedCount || 0);
  const attached = Math.max(0, delivery.attachmentSentCount || 0);
  const fallback = Math.max(0, delivery.fallbackLinkSentCount || 0);
  const failed = Math.max(0, delivery.failedCount || 0);
  const parts = [
    `${prepared} 份文件已准备`,
    `${attached} 份已作为附件发送`,
  ];
  if (fallback) parts.push(`${fallback} 份已改发下载链接`);
  if (failed) parts.push(`${failed} 份未能送达`);
  return `${parts.join("，")}。`;
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
      "Prepare and Deliver One OA Certificate Scan": "OA 证书文件交付",
      "Prepare and Deliver OA Certificate Scans": "OA 证书文件批量交付",
      "Search OA Certificate Scans": "OA 证书查询与下载",
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
  const actions = document.createElement("div");
  actions.className = "detail-heading-actions";
  const continueButton = document.createElement("button");
  continueButton.type = "button";
  continueButton.className = "primary";
  continueButton.textContent = "继续任务";
  continueButton.addEventListener("click", () =>
    continueTask(task, continueButton),
  );
  actions.append(continueButton);
  heading.append(back, headingCopy, actions);
  detail.append(heading);

  const metadata = document.createElement("dl");
  metadata.className = "detail-grid";
  addDetail(metadata, "来源", task.agent_host);
  addDetail(metadata, "创建时间", formatTime(task.created_at));
  addDetail(metadata, "更新时间", formatTime(task.updated_at));
  addDetail(metadata, "任务编号", task.task_id);
  detail.append(metadata);

  const interactions = Array.isArray(result.interactions)
    ? result.interactions
    : result.interaction
      ? [result.interaction]
      : [];
  interactions
    .filter((interaction) => {
      const url = interaction?.presentation?.url;
      return (
        ["pending", "processing"].includes(interaction?.state) &&
        typeof url === "string" &&
        /^https:\/\//.test(url)
      );
    })
    .forEach((interaction) => {
      const url = interaction.presentation.url;
      const band = document.createElement("div");
      band.className = "interaction-band";
      const label = document.createElement("span");
      label.textContent = interaction.title || interactionLabel(interaction.type);
      const link = document.createElement("a");
      link.className = "primary";
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "打开";
      band.append(label, link);
      detail.append(band);
    });

  appendArtifactList(detail, result.artifacts, { taskId: task.task_id });

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

async function continueTask(task, button) {
  if (!task?.task_id || button.disabled) return;
  button.disabled = true;
  try {
    const result = await api(
      `/api/tasks/${encodeURIComponent(task.task_id)}/continue`,
      { method: "POST", body: {}, csrf: true },
    );
    switchView("chat");
    await executeChatMessage(result.message);
  } catch (error) {
    toast(friendlyError(error), true, `task:${task.task_id}:continue`);
  } finally {
    button.disabled = false;
  }
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

function startWorkspaceObservers() {
  clearInterval(state.timelineReconcileTimer);
  clearInterval(state.clientVersionTimer);
  state.timelineReconcileTimer = setInterval(reconcileTimeline, 10000);
  state.clientVersionTimer = setInterval(checkClientVersion, 30000);
}

function stopWorkspaceObservers() {
  state.eventSource?.close();
  state.eventSource = null;
  clearTimeout(state.timelineReconnectTimer);
  clearInterval(state.timelineReconcileTimer);
  clearInterval(state.clientVersionTimer);
  state.timelineReconnectTimer = null;
  state.timelineReconcileTimer = null;
  state.clientVersionTimer = null;
  state.timelineReconcileActive = false;
  state.clientVersionCheckActive = false;
}

function refreshWorkspaceState() {
  if (!state.account || document.visibilityState === "hidden") return;
  reconcileTimeline();
  checkClientVersion();
  if (!state.eventSource) openTimelineStream();
}

async function reconcileTimeline() {
  if (!state.account || state.timelineReconcileActive) return;
  state.timelineReconcileActive = true;
  try {
    for (let page = 0; page < 3; page += 1) {
      const after = Math.max(Number(state.timelineCursor) || 0, 0);
      const result = await api(
        `/api/timeline?after=${encodeURIComponent(after)}&limit=200`,
      );
      const items = Array.isArray(result.items) ? result.items : [];
      items.forEach((entry) => ingestTimelineEntry(entry));
      if (items.length < 200) {
        state.timelineCursor = Math.max(
          state.timelineCursor,
          Number(result.cursor) || 0,
        );
        break;
      }
    }
  } catch {
  } finally {
    state.timelineReconcileActive = false;
  }
}

async function checkClientVersion() {
  if (!CLIENT_VERSION || state.clientVersionCheckActive) return;
  state.clientVersionCheckActive = true;
  try {
    const result = await api("/api/client-version");
    const runActive = state.activeStreams.size > 0;
    if (result.version && result.version !== CLIENT_VERSION && !runActive) {
      location.reload();
    }
  } catch {
  } finally {
    state.clientVersionCheckActive = false;
  }
}

function openTimelineStream() {
  if (!state.account) return;
  clearTimeout(state.timelineReconnectTimer);
  state.timelineReconnectTimer = null;
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
    if (state.eventSource !== source) return;
    source.close();
    state.eventSource = null;
    clearTimeout(state.timelineReconnectTimer);
    state.timelineReconnectTimer = setTimeout(openTimelineStream, 5000);
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
  if (error.code === "GATEWAY_RUN_TIMEOUT_ABORTED") {
    return error.details?.hadToolActivity === true
      ? "智能体运行超时，已停止后续处理；任务已经调用业务工具，请先核对业务系统结果。"
      : "智能体运行超时，已安全中止；本次尚未调用业务系统，可以重新发送。";
  }
  const labels = {
    LOGIN_FAILED: "用户名或密码不正确。",
    LOGIN_RATE_LIMITED: "登录尝试过多，请稍后再试。",
    WORKSPACE_LINK_INVALID: "配对尚未完成或已经失效。",
    WORKSPACE_CONFLICT: "该身份或用户名已经绑定网页账号。",
    GATEWAY_NOT_CONFIGURED: "智能体连接尚未配置。",
    WORKSPACE_RUN_IN_PROGRESS:
      "\u4e0a\u4e00\u6761\u7f51\u9875\u4efb\u52a1\u4ecd\u5728\u5904\u7406\uff0c\u672c\u6b21\u8bf7\u6c42\u6ca1\u6709\u6392\u961f\u3002",
    GATEWAY_RUN_TIMEOUT_ABORTED:
      "\u667a\u80fd\u4f53\u8fd0\u884c\u8d85\u65f6\uff0c\u5df2\u505c\u6b62\u540e\u7eed\u5904\u7406\u3002",
    GATEWAY_RUN_TIMEOUT_ABORT_UNCONFIRMED:
      "OpenClaw \u6682\u65f6\u65e0\u54cd\u5e94\uff0c\u672c\u6b21\u8bf7\u6c42\u5df2\u505c\u6b62\u7ee7\u7eed\u6392\u961f\u3002",
    GATEWAY_SESSION_NOT_IDLE:
      "\u4e0a\u4e00\u6761\u667a\u80fd\u4f53\u4efb\u52a1\u672a\u80fd\u53ca\u65f6\u7ed3\u675f\uff0c\u672c\u6b21\u8bf7\u6c42\u672a\u8fdb\u5165\u4e1a\u52a1\u7cfb\u7edf\u3002",
    GATEWAY_SESSION_STATE_UNAVAILABLE:
      "\u6682\u65f6\u65e0\u6cd5\u786e\u8ba4\u667a\u80fd\u4f53\u4f1a\u8bdd\u662f\u5426\u7a7a\u95f2\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002",
    GATEWAY_START_STALLED_ABORTED:
      "\u667a\u80fd\u4f53\u81ea\u52a8\u6062\u590d\u540e\u4ecd\u672a\u80fd\u542f\u52a8\uff0c\u5df2\u5b89\u5168\u4e2d\u6b62\u3002",
    GATEWAY_START_STALLED_ABORT_UNCONFIRMED:
      "\u667a\u80fd\u4f53\u542f\u52a8\u72b6\u6001\u65e0\u6cd5\u786e\u8ba4\uff0c\u5df2\u505c\u6b62\u81ea\u52a8\u6062\u590d\u3002",
    GATEWAY_START_RECOVERY_BLOCKED_TOOL_ACTIVITY:
      "智能体启动异常，但本轮已经触碰业务工具；已停止自动重放，请先核对业务系统状态。",
    GATEWAY_START_RECOVERY_EVIDENCE_UNAVAILABLE:
      "智能体启动异常，且无法确认是否已调用业务工具；已停止自动重放。",
    GATEWAY_TIMEOUT:
      "OpenClaw \u6682\u65f6\u65e0\u54cd\u5e94\uff0c\u672c\u6b21\u8bf7\u6c42\u672a\u7ee7\u7eed\u8fdb\u5165\u4e1a\u52a1\u7cfb\u7edf\u3002",
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
      superseded: "已被替换",
    }[status] || status
  );
}

function eventLabel(type) {
  return (
    {
      "task.created": "任务已创建",
      "task.operation.linked": "操作已关联",
      "task.operation.requires_user_action": "操作等待用户处理",
      "task.interaction.waiting": "等待用户处理",
      "task.interaction.completed": "可信交互已完成",
      "task.interaction.expired": "可信交互已过期",
      "task.interaction.failed": "可信交互失败",
      "task.interaction.superseded": "可信交互已更新",
      "task.operation.running": "操作执行中",
      "task.operation.succeeded": "操作成功",
      "task.operation.failed": "操作失败",
      "task.operation.outcome_unknown": "操作结果待核对",
      "task.canceled": "任务已取消",
      "task.artifact.ready": "任务文件已就绪",
      "task.artifact.delivery": "文件投递结果已回报",
      "task.artifact.refreshed": "文件下载已重新生成",
      "task.completed": "任务已完成",
    }[type] || type.replaceAll(".", " / ")
  );
}

function appendArtifactList(
  container,
  artifacts,
  { compact = false, taskId = null } = {},
) {
  const items = Array.isArray(artifacts) ? artifacts : [];
  if (!items.length) return;
  const section = document.createElement("section");
  section.className = `task-artifacts${compact ? " compact" : ""}`;
  const heading = document.createElement("strong");
  heading.className = "task-artifacts-heading";
  heading.textContent = "任务文件";
  section.append(heading);
  items.forEach((artifact) => {
    const row = document.createElement("div");
    row.className = "task-artifact";
    const copy = document.createElement("div");
    copy.className = "task-artifact-copy";
    const name = document.createElement("span");
    name.className = "task-artifact-name";
    name.textContent = artifact.filename || "未命名文件";
    const meta = document.createElement("span");
    meta.className = "task-artifact-meta";
    meta.textContent = artifact.state === "ready"
      ? `${formatBytes(artifact.byte_size)} · ${formatTime(artifact.expires_at)} 前可取用`
      : "下载链接已过期";
    copy.append(name, meta);
    row.append(copy);
    if (
      artifact.state === "ready" &&
      typeof artifact.download_url === "string" &&
      /^https:\/\//.test(artifact.download_url)
    ) {
      const download = document.createElement("a");
      download.className = "secondary task-artifact-download";
      download.href = artifact.download_url;
      download.target = "_blank";
      download.rel = "noopener";
      download.textContent = "下载";
      row.append(download);
    } else {
      const actions = document.createElement("div");
      actions.className = "task-artifact-actions";
      const state = document.createElement("span");
      state.className = "task-artifact-expired";
      state.textContent = "已过期";
      actions.append(state);
      if (
        artifact.artifact_type === "certificate_scan" &&
        taskId &&
        artifact.artifact_id
      ) {
        const reissue = document.createElement("button");
        reissue.type = "button";
        reissue.className = "secondary task-artifact-reissue";
        reissue.textContent = "重新生成下载";
        reissue.addEventListener("click", () =>
          reissueArtifact(taskId, artifact.artifact_id, reissue),
        );
        actions.append(reissue);
      }
      row.append(actions);
    }
    section.append(row);
  });
  container.append(section);
}

async function reissueArtifact(taskId, artifactId, button) {
  if (!taskId || !artifactId || button.disabled) return;
  button.disabled = true;
  button.textContent = "正在重新获取";
  try {
    const result = await api(
      `/api/tasks/${encodeURIComponent(taskId)}/artifacts/` +
        `${encodeURIComponent(artifactId)}/reissue`,
      { method: "POST", body: {}, csrf: true },
    );
    upsertTaskCard(result);
    if (state.selectedTaskId === taskId) renderTaskDetail(result);
    toast(
      "新的下载链接已生成，30 分钟内有效。",
      false,
      `artifact:${artifactId}:reissued`,
    );
  } catch (error) {
    toast(
      friendlyError(error),
      true,
      `artifact:${artifactId}:${error.code || "reissue"}`,
    );
  } finally {
    if (button.isConnected) {
      button.disabled = false;
      button.textContent = "重新生成下载";
    }
  }
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (!Number.isFinite(size) || size <= 0) return "未知大小";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
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
