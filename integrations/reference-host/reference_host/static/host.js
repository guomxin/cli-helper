const state = {
  identity: null,
  tools: [],
  tasks: [],
  selectedTaskId: null,
  stream: null,
};

const elements = {
  hostState: document.querySelector("#host-state"),
  identity: document.querySelector("#identity-select"),
  tools: document.querySelector("#tool-select"),
  description: document.querySelector("#tool-description"),
  title: document.querySelector("#task-title"),
  arguments: document.querySelector("#tool-arguments"),
  run: document.querySelector("#run-button"),
  error: document.querySelector("#composer-error"),
  refresh: document.querySelector("#refresh-button"),
  recover: document.querySelector("#recover-button"),
  taskList: document.querySelector("#task-list"),
  detailTitle: document.querySelector("#detail-title"),
  taskStatus: document.querySelector("#task-status"),
  taskId: document.querySelector("#task-id"),
  interaction: document.querySelector("#interaction-panel"),
  artifacts: document.querySelector("#artifact-panel"),
  timeline: document.querySelector("#timeline"),
};

async function request(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = payload.error || {};
    throw new Error(error.message || error.code || `HTTP ${response.status}`);
  }
  return payload;
}

async function initialize() {
  try {
    const status = await request("/api/status");
    elements.hostState.textContent = `${status.host.implementation.name} ${status.host.implementation.version} · ${status.host.hostInstanceId}`;
    elements.identity.replaceChildren(...status.identities.map((identity) => {
      const option = document.createElement("option");
      option.value = identity.label;
      option.textContent = identity.label;
      return option;
    }));
    state.identity = elements.identity.value;
    await Promise.all([loadTools(), loadTasks()]);
  } catch (error) {
    elements.hostState.textContent = `连接失败：${error.message}`;
  }
}

async function loadTools() {
  if (!state.identity) return;
  const payload = await request(`/api/tools?identity=${encodeURIComponent(state.identity)}`);
  state.tools = payload.tools;
  elements.tools.replaceChildren(...state.tools.map((tool) => {
    const option = document.createElement("option");
    option.value = tool.name;
    option.textContent = tool.title || tool.name;
    return option;
  }));
  updateToolHelp();
}

function updateToolHelp() {
  const tool = state.tools.find((item) => item.name === elements.tools.value);
  elements.description.textContent = tool ? tool.description : "";
  if (!tool) return;
  const properties = tool.inputSchema?.properties || {};
  const sample = {};
  for (const [name, schema] of Object.entries(properties)) {
    if (schema.default !== undefined) sample[name] = schema.default;
  }
  elements.arguments.value = JSON.stringify(sample, null, 2);
}

async function loadTasks(selectNewest = false) {
  if (!state.identity) return;
  const payload = await request(`/api/tasks?identity=${encodeURIComponent(state.identity)}`);
  state.tasks = payload.tasks;
  renderTaskList();
  if (selectNewest && state.tasks[0]) {
    await selectTask(state.tasks[0].localTaskId);
  } else if (state.selectedTaskId) {
    const stillVisible = state.tasks.some((task) => task.localTaskId === state.selectedTaskId);
    if (stillVisible) await loadTask(state.selectedTaskId);
  }
}

function renderTaskList() {
  if (!state.tasks.length) {
    elements.taskList.innerHTML = '<p class="empty">暂无任务</p>';
    return;
  }
  elements.taskList.replaceChildren(...state.tasks.map((task) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `task-item${task.localTaskId === state.selectedTaskId ? " active" : ""}`;
    const title = document.createElement("strong");
    title.textContent = task.title;
    const meta = document.createElement("span");
    meta.textContent = `${statusLabel(task.status)} · ${formatTime(task.updatedAt)}`;
    button.append(title, meta);
    button.addEventListener("click", () => selectTask(task.localTaskId));
    return button;
  }));
}

async function selectTask(localTaskId) {
  state.selectedTaskId = localTaskId;
  renderTaskList();
  await loadTask(localTaskId);
  openStream(localTaskId);
}

async function loadTask(localTaskId) {
  const payload = await request(`/api/tasks/${encodeURIComponent(localTaskId)}`);
  renderTask(payload.task, payload.events);
}

function renderTask(task, events) {
  elements.detailTitle.textContent = task.title;
  elements.taskId.textContent = task.taskId || task.localTaskId;
  elements.taskStatus.textContent = statusLabel(task.status);
  elements.taskStatus.className = `status-badge ${task.status}`;
  renderInteraction(task.activeInteraction);
  renderArtifacts(task.artifacts || []);
  elements.timeline.replaceChildren(...events.map((event) => {
    const item = document.createElement("li");
    const summary = document.createElement("strong");
    summary.textContent = event.summary || event.kind;
    const meta = document.createElement("span");
    meta.textContent = `${formatTime(event.created_at)} · ${event.kind}`;
    item.append(summary, meta);
    return item;
  }));
  if (!events.length) elements.timeline.innerHTML = '<li class="empty">等待事件</li>';
}

function renderInteraction(interaction) {
  if (!interaction) {
    elements.interaction.classList.add("hidden");
    elements.interaction.replaceChildren();
    return;
  }
  elements.interaction.classList.remove("hidden");
  const title = document.createElement("h3");
  title.textContent = "等待可信交互";
  const text = document.createElement("p");
  text.textContent = "在 AgentBridge 受控页面完成登录、字段填写或授权，完成后本任务会自动续办。";
  const link = document.createElement("a");
  link.className = "action-button";
  link.href = interaction.openPath;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = "打开可信页面";
  elements.interaction.replaceChildren(title, text, link);
}

function renderArtifacts(artifacts) {
  if (!artifacts.length) {
    elements.artifacts.classList.add("hidden");
    elements.artifacts.replaceChildren();
    return;
  }
  elements.artifacts.classList.remove("hidden");
  const title = document.createElement("h3");
  title.textContent = "任务文件";
  const list = document.createElement("div");
  list.className = "artifact-list";
  for (const artifact of artifacts) {
    const row = document.createElement("div");
    row.className = "artifact-row";
    const name = document.createElement("strong");
    name.textContent = `${artifact.fileName} · ${artifact.status}`;
    row.append(name);
    if (artifact.status === "READY") {
      const download = document.createElement("a");
      download.className = "action-button secondary";
      download.href = artifact.downloadPath;
      download.target = "_blank";
      download.rel = "noopener noreferrer";
      download.textContent = "下载";
      row.append(download);
    }
    if (artifact.regenerable) {
      const reissue = document.createElement("button");
      reissue.type = "button";
      reissue.className = "action-button secondary";
      reissue.textContent = "重新生成";
      reissue.addEventListener("click", () => reissueArtifact(artifact.reissuePath));
      row.append(reissue);
    }
    list.append(row);
  }
  elements.artifacts.replaceChildren(title, list);
}

function openStream(localTaskId) {
  if (state.stream) state.stream.close();
  const stream = new EventSource(`/api/tasks/${encodeURIComponent(localTaskId)}/stream`);
  state.stream = stream;
  const refresh = async () => {
    if (state.selectedTaskId !== localTaskId) return;
    await loadTask(localTaskId);
    await loadTasks();
  };
  stream.addEventListener("task-event", refresh);
  stream.addEventListener("task-complete", async () => {
    stream.close();
    await refresh();
  });
}

async function runTask() {
  elements.error.textContent = "";
  let argumentsValue;
  try {
    argumentsValue = JSON.parse(elements.arguments.value || "{}");
    if (!argumentsValue || Array.isArray(argumentsValue) || typeof argumentsValue !== "object") {
      throw new Error("参数必须是 JSON 对象");
    }
  } catch (error) {
    elements.error.textContent = error.message;
    return;
  }
  elements.run.disabled = true;
  try {
    const payload = await request("/api/tasks", {
      method: "POST",
      body: JSON.stringify({
        identityLabel: state.identity,
        toolName: elements.tools.value,
        arguments: argumentsValue,
        title: elements.title.value.trim() || undefined,
      }),
    });
    await loadTasks();
    await selectTask(payload.task.localTaskId);
  } catch (error) {
    elements.error.textContent = error.message;
  } finally {
    elements.run.disabled = false;
  }
}

async function reissueArtifact(path) {
  try {
    await request(path, { method: "POST", body: "{}" });
    await loadTask(state.selectedTaskId);
  } catch (error) {
    elements.error.textContent = error.message;
  }
}

function statusLabel(status) {
  return ({
    starting: "启动中",
    running: "执行中",
    waiting_user: "等待用户",
    recovering: "恢复中",
    observe_only: "只读观察",
    succeeded: "已完成",
    failed: "失败",
    canceled: "已取消",
    unknown: "结果未知",
  })[status] || status;
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

elements.identity.addEventListener("change", async () => {
  state.identity = elements.identity.value;
  state.selectedTaskId = null;
  if (state.stream) state.stream.close();
  await Promise.all([loadTools(), loadTasks()]);
});
elements.tools.addEventListener("change", updateToolHelp);
elements.run.addEventListener("click", runTask);
elements.refresh.addEventListener("click", () => loadTasks());
elements.recover.addEventListener("click", async () => {
  elements.recover.disabled = true;
  try {
    await request("/api/recover", { method: "POST", body: "{}" });
    await loadTasks();
  } finally {
    elements.recover.disabled = false;
  }
});

initialize();
