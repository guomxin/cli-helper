const DAEMON_URL = "http://127.0.0.1:8765";
const POLL_INTERVAL_MS = 1500;
const BACKGROUND_VERSION = "background-v5-task-claim-state";

async function getClientId() {
  const existing = await chrome.storage.local.get("clientId");
  if (existing.clientId) {
    return existing.clientId;
  }
  const clientId = `chrome-${crypto.randomUUID()}`;
  await chrome.storage.local.set({ clientId });
  return clientId;
}

async function getBridgeTabs() {
  const tabs = await chrome.tabs.query({});
  return tabs.filter((tab) => tab.id && tab.url && isPageUrl(tab.url));
}

function isPageUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch (_error) {
    return false;
  }
}

function clientIdForTab(baseClientId, tabId) {
  return `${baseClientId}:tab:${tabId}`;
}

async function postJson(path, payload) {
  const response = await fetch(`${DAEMON_URL}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
  return await response.json();
}

async function getJson(path) {
  const response = await fetch(`${DAEMON_URL}${path}`);
  return await response.json();
}

async function postTaskEvent(clientId, task, stage, detail = {}) {
  try {
    await postJson("/extension/task-events", {
      client_id: clientId,
      task_id: task.id,
      stage,
      detail,
    });
  } catch (_error) {
    // Diagnostics must not prevent task execution.
  }
}

async function registerTab(baseClientId, tab) {
  const clientId = clientIdForTab(baseClientId, tab.id);
  await postJson("/extension/register", {
    client_id: clientId,
    tab_id: tab.id,
    url: tab.url,
    title: tab.title || "",
    extension_version: BACKGROUND_VERSION,
  });
  return { clientId, tab };
}

async function pollTasks() {
  try {
    const baseClientId = await getClientId();
    const tabs = await getBridgeTabs();
    if (!tabs.length) {
      return;
    }
    for (const tab of tabs) {
      const { clientId } = await registerTab(baseClientId, tab);
      const data = await getJson(`/extension/tasks?client_id=${encodeURIComponent(clientId)}`);
      for (const task of data.tasks || []) {
        await executeTask(clientId, tab.id, task);
      }
    }
  } catch (error) {
    // Daemon may not be running yet. Keep polling quietly.
  }
}

async function executeTask(clientId, tabId, task) {
  try {
    await postTaskEvent(clientId, task, "claimed", { kind: task.kind });
    let injection;
    if (task.kind === "dom_snapshot") {
      const selector = task.payload?.selector || "body";
      [injection] = await chrome.scripting.executeScript({
        target: { tabId },
        func: collectDomSnapshot,
        args: [selector],
      });
    } else if (task.kind === "page_inventory") {
      [injection] = await chrome.scripting.executeScript({
        target: { tabId },
        func: collectPageInventory,
      });
    } else if (task.kind === "html_snapshot") {
      [injection] = await chrome.scripting.executeScript({
        target: { tabId },
        func: collectHtmlSnapshot,
      });
    } else if (task.kind === "rendered_html_snapshot") {
      const result = await collectRenderedHtmlSnapshot(task.payload || {});
      injection = { result };
    } else if (task.kind === "seeyon_write_execute") {
      const result = await executeSeeyonWrite(task.payload || {}, (stage, detail) =>
        postTaskEvent(clientId, task, stage, detail),
      );
      injection = { result };
    } else if (task.kind === "seeyon_launch_save_draft") {
      const result = await executeSeeyonLaunchSaveDraft(task.payload || {});
      injection = { result };
    } else if (task.kind === "network_probe_install") {
      [injection] = await chrome.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        func: installNetworkProbe,
      });
    } else if (task.kind === "network_log_snapshot") {
      [injection] = await chrome.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        func: collectNetworkLogSnapshot,
      });
    } else if (task.kind === "page_fetch") {
      [injection] = await chrome.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        func: runPageFetch,
        args: [task.payload || {}],
      });
    } else if (task.kind === "oa_pending_list") {
      [injection] = await chrome.scripting.executeScript({
        target: { tabId },
        func: collectOaPendingList,
      });
    } else if (task.kind === "oa_pending_detail") {
      [injection] = await chrome.scripting.executeScript({
        target: { tabId },
        func: collectOaPendingDetail,
        args: [task.payload || {}],
      });
    } else if (task.kind === "oa_template_list") {
      [injection] = await chrome.scripting.executeScript({
        target: { tabId },
        func: collectOaTemplateList,
      });
    } else {
      throw new Error(`unsupported task kind: ${task.kind}`);
    }
    await postJson("/extension/results", {
      client_id: clientId,
      task_id: task.id,
      ok: true,
      result: injection.result,
    });
  } catch (error) {
    await postJson("/extension/results", {
      client_id: clientId,
      task_id: task.id,
      ok: false,
      error: String(error && error.message ? error.message : error),
    });
  }
}

function collectDomSnapshot(selector) {
  const element = document.querySelector(selector) || document.body;
  return {
    url: location.href,
    title: document.title,
    selector,
    text: (element.innerText || "").slice(0, 20000),
  };
}

function collectHtmlSnapshot() {
  return {
    url: location.href,
    title: document.title,
    html: document.documentElement.outerHTML,
  };
}

async function collectRenderedHtmlSnapshot(payload) {
  const url = payload.url;
  if (!url) {
    throw new Error("url is required");
  }
  const settleMs = Number(payload.settle_ms || 1500);
  const tab = await chrome.tabs.create({ url, active: false });
  try {
    await waitForTabReadable(tab.id, 20000);
    if (settleMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, settleMs));
    }
    const frameInjections = await chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: true },
      func: collectHtmlSnapshot,
    });
    const frames = frameInjections
      .map((injection) => ({
        frameId: injection.frameId ?? 0,
        ...(injection.result || {}),
      }))
      .filter((frame) => frame.html || frame.text || frame.url);
    const top = frames.find((frame) => frame.frameId === 0) || frames[0] || {};
    return {
      ...top,
      frames,
    };
  } finally {
    if (tab.id) {
      await chrome.tabs.remove(tab.id).catch(() => {});
    }
  }
}

async function executeSeeyonWrite(payload, reportEvent = async () => {}) {
  if (payload.confirm !== true) {
    throw new Error("confirm=true is required for seeyon_write_execute");
  }
  if (payload.action !== "ContinueSubmit") {
    throw new Error(`unsupported Seeyon write action: ${payload.action || ""}`);
  }
  const url = payload.source_url || payload.url;
  if (!url) {
    throw new Error("source_url is required for seeyon_write_execute");
  }
  await reportEvent("opening_detail_tab", { source_url: String(url).slice(0, 1000) });
  const tab = await chrome.tabs.create({ url, active: false });
  await reportEvent("detail_tab_created", { tab_id: tab.id || null });
  try {
    await reportEvent("waiting_detail_tab_readable", { tab_id: tab.id || null });
    await withTimeout(
      waitForTabReadable(tab.id, Number(payload.detail_timeout_ms || 30000)),
      Number(payload.detail_timeout_ms || 30000),
      "Seeyon write detail tab timed out before becoming readable",
    );
    await reportEvent("detail_tab_readable", { tab_id: tab.id || null });
    await new Promise((resolve) => setTimeout(resolve, Number(payload.settle_ms || 2000)));
    const scriptTimeoutMs = Number(payload.script_timeout_ms || 10000);
    await reportEvent("injecting_submit_script", { tab_id: tab.id || null, script_timeout_ms: scriptTimeoutMs });
    const [injection] = await withTimeout(
      chrome.scripting.executeScript({
        target: { tabId: tab.id },
        world: "MAIN",
        func: runSeeyonContinueSubmit,
        args: [payload],
      }),
      scriptTimeoutMs,
      "Seeyon write execute script timed out before scheduling submit",
    );
    if (!injection || !injection.result) {
      throw new Error("Seeyon submit script returned no result");
    }
    await reportEvent("submit_script_returned", {
      handler_version: injection.result.handler_version || "",
      submit_entry: injection.result.submit_entry || "",
    });
    await new Promise((resolve) => setTimeout(resolve, Number(payload.after_submit_wait_ms || 8000)));
    await reportEvent("after_submit_wait_complete", { tab_id: tab.id || null });
    return injection.result;
  } finally {
    if (tab.id && payload.keep_tab !== true) {
      await reportEvent("closing_detail_tab", { tab_id: tab.id });
      await chrome.tabs.remove(tab.id).catch(() => {});
    }
  }
}

async function executeSeeyonLaunchSaveDraft(payload) {
  if (payload.confirm !== true) {
    throw new Error("confirm=true is required for seeyon_launch_save_draft");
  }
  const url = payload.url || payload.source_url;
  if (!url) {
    throw new Error("url is required for seeyon_launch_save_draft");
  }
  const tab = await chrome.tabs.create({ url, active: false });
  try {
    await waitForTabReadable(tab.id, 30000);
    await new Promise((resolve) => setTimeout(resolve, Number(payload.settle_ms || 1500)));
    const scriptTimeoutMs = Number(payload.script_timeout_ms || 10000);
    const [injection] = await withTimeout(
      chrome.scripting.executeScript({
        target: { tabId: tab.id },
        world: "MAIN",
        func: runSeeyonLaunchSaveDraft,
        args: [payload],
      }),
      scriptTimeoutMs,
      "Seeyon launch save-draft script timed out before scheduling click",
    );
    if (!injection || !injection.result) {
      throw new Error("Seeyon launch save-draft script returned no result");
    }
    await new Promise((resolve) => setTimeout(resolve, Number(payload.after_save_wait_ms || 4000)));
    return injection.result;
  } finally {
    if (tab.id && payload.keep_tab !== true) {
      await chrome.tabs.remove(tab.id).catch(() => {});
    }
  }
}

async function runSeeyonContinueSubmit(payload) {
  const handlerVersion = "continue-submit-v3-page-submit-click";
  const expectedAffairId = String(payload.affair_id || "");
  const opinion = String(payload.opinion || "");
  if (!expectedAffairId) {
    throw new Error("affair_id is required");
  }
  if (payload.confirm !== true) {
    throw new Error("confirm=true is required");
  }
  if (payload.action !== "ContinueSubmit") {
    throw new Error(`unsupported Seeyon write action: ${payload.action || ""}`);
  }

  const pageAffairId =
    String(window.affairId || "") ||
    document.querySelector("#affairId")?.value ||
    new URLSearchParams(location.search).get("affairId") ||
    "";
  if (String(pageAffairId) !== expectedAffairId) {
    throw new Error(`affair_id mismatch: page=${pageAffairId || "(empty)"} expected=${expectedAffairId}`);
  }
  const submitEntry =
    typeof window.submitClickFunc === "function"
      ? { name: "submitClickFunc", fn: window.submitClickFunc }
      : typeof window.dealSubmitFunc === "function"
        ? { name: "dealSubmitFunc", fn: window.dealSubmitFunc }
        : window.$ && window.$.content && window.$.content.callback && typeof window.$.content.callback.dealSubmit === "function"
          ? { name: "$.content.callback.dealSubmit", fn: window.$.content.callback.dealSubmit }
          : null;
  if (!submitEntry) {
    throw new Error("Seeyon submit function submitClickFunc/dealSubmitFunc was not found on the detail page");
  }

  const dialogs = [];
  const findCommentElement = () => {
    const selectors = [
      "#content_deal_comment",
      "textarea[name='content_deal_comment']",
      "textarea#content",
      "textarea[name='content']",
    ];
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      if (element) {
        return element;
      }
    }
    return null;
  };
  const setElementValue = (element, value) => {
    const valueSetter = Object.getOwnPropertyDescriptor(element.constructor.prototype, "value")?.set;
    if (valueSetter) {
      valueSetter.call(element, value);
    } else {
      element.value = value;
    }
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const selectAgreeAttitude = () => {
    const result = {
      radio_selected: false,
      hidden_code_set: false,
      hidden_value_set: false,
      nodeattitude_set: false,
    };
    const radios = Array.from(document.querySelectorAll("input[type='radio'][name='attitude']"));
    const agreeRadio = radios.find((radio) => {
      const code = String(radio.getAttribute("code") || "").toLowerCase();
      const value = String(radio.value || "").toLowerCase();
      return code === "agree" || code === "haveread" || value === "agree" || value === "haveread";
    });
    if (agreeRadio) {
      agreeRadio.checked = true;
      agreeRadio.dispatchEvent(new Event("input", { bubbles: true }));
      agreeRadio.dispatchEvent(new Event("change", { bubbles: true }));
      result.radio_selected = true;
    }
    const attitudeCode = agreeRadio?.getAttribute("code") || agreeRadio?.value || "agree";
    const hiddenCode = document.querySelector("#hidAttitudeCode");
    if (hiddenCode) {
      setElementValue(hiddenCode, attitudeCode);
      result.hidden_code_set = true;
    }
    const hiddenValue = document.querySelector("#hidAttitude");
    if (hiddenValue) {
      setElementValue(hiddenValue, attitudeCode);
      result.hidden_value_set = true;
    }
    const nodeAttitude = document.querySelector("#nodeattitude");
    if (nodeAttitude) {
      setElementValue(nodeAttitude, attitudeCode);
      result.nodeattitude_set = true;
    }
    return result;
  };
  const originalConfirm = window.confirm;
  const originalAlert = window.alert;
  let dialogsRestored = false;
  const restoreDialogs = () => {
    if (dialogsRestored) {
      return;
    }
    dialogsRestored = true;
    window.confirm = originalConfirm;
    window.alert = originalAlert;
  };

  window.confirm = function bscliWriteConfirm(message) {
    dialogs.push({ type: "confirm", message: String(message || "").slice(0, 1000), accepted: true });
    return true;
  };
  window.alert = function bscliWriteAlert(message) {
    dialogs.push({ type: "alert", message: String(message || "").slice(0, 1000) });
    return undefined;
  };

  try {
    const comment = findCommentElement();
    if (!comment) {
      throw new Error("content_deal_comment was not found on the detail page");
    }
    setElementValue(comment, opinion);
    const attitude = selectAgreeAttitude();
    const runSubmit = () => {
      const outcome = {
        ok: true,
        handler_version: handlerVersion,
        submit_scheduled: true,
        affair_id: expectedAffairId,
        action: "ContinueSubmit",
        opinion_length: opinion.length,
        url: location.href,
        title: document.title,
        submit_entry: submitEntry.name,
        dialogs,
      };
      try {
        submitEntry.fn.call(window);
      } catch (error) {
        outcome.ok = false;
        outcome.error = String(error && error.message ? error.message : error);
        console.error("BSCLI scheduled Seeyon ContinueSubmit failed", error);
      } finally {
        window.__bscliContinueSubmitLast = outcome;
        setTimeout(restoreDialogs, Number(payload.dialog_restore_ms || 3000));
      }
    };

    window.setTimeout(runSubmit, 0);
    return {
      submitted: true,
      handler_version: handlerVersion,
      submit_scheduled: true,
      affair_id: expectedAffairId,
      action: "ContinueSubmit",
      opinion_length: opinion.length,
      url: location.href,
      title: document.title,
      attitude,
      submit_entry: submitEntry.name,
      dialogs: [],
    };
  } catch (error) {
    restoreDialogs();
    throw error;
  }
}

function runSeeyonLaunchSaveDraft(payload) {
  const handlerVersion = "launch-save-draft-v3-scheduled-fill-click";
  const compactText = (value, max) => String(value || "").replace(/\s+/g, " ").trim().slice(0, max);
  const normalizeLookup = (value) => String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
  const controlText = (element) => element.innerText || element.value || element.getAttribute("title") || element.getAttribute("aria-label") || "";
  const labelIndex = () => {
    const labels = new Map();
    for (const label of Array.from(document.querySelectorAll("label[for]"))) {
      labels.set(label.getAttribute("for") || "", compactText(label.innerText || "", 200));
    }
    return labels;
  };
  const looksLikeSaveDraft = (code, text) => {
    const value = `${code || ""} ${text || ""}`.toLowerCase();
    return (
      value.includes("savedraft") ||
      value.includes("save draft") ||
      value.includes("\u4fdd\u5b58\u5f85\u53d1") ||
      value.includes("\u4fdd\u5b58\u8349\u7a3f") ||
      value.includes("\u6682\u5b58\u5f85\u53d1")
    );
  };
  const looksLikeForbiddenSubmit = (code, text) => {
    const value = `${code || ""} ${text || ""}`.toLowerCase();
    return (
      value.includes("sendid_a") ||
      value.includes("continuesubmit") ||
      value.includes("submit") ||
      value.includes("send") ||
      value.includes("\u53d1\u9001") ||
      value.includes("\u63d0\u4ea4")
    );
  };
  const findSaveDraftControl = () => {
    const controls = Array.from(document.querySelectorAll("button,input[type=button],input[type=submit],a"));
    for (const control of controls) {
      const code = `${control.id || ""} ${control.getAttribute("name") || ""}`;
      const text = controlText(control);
      if (looksLikeSaveDraft(code, text) && !looksLikeForbiddenSubmit(code, text)) {
        return control;
      }
    }
    return null;
  };
  const findLaunchField = (key) => {
    const wanted = normalizeLookup(key);
    const labels = labelIndex();
    for (const element of Array.from(document.querySelectorAll("input,textarea,select"))) {
      const type = (element.getAttribute("type") || "").toLowerCase();
      if (["hidden", "button", "submit", "reset", "file"].includes(type)) {
        continue;
      }
      if (element.disabled || element.readOnly) {
        continue;
      }
      const label = labels.get(element.id || "") || "";
      const candidates = [element.getAttribute("name"), element.id, label];
      if (candidates.some((candidate) => normalizeLookup(candidate) === wanted)) {
        return element;
      }
    }
    return null;
  };
  const fillLaunchField = (element, value) => {
    const tag = element.tagName.toLowerCase();
    const text = String(value ?? "");
    if (tag === "select") {
      const option = Array.from(element.options || []).find((item) => item.value === text || compactText(item.text, 200) === text);
      if (!option) {
        throw new Error(`select option not found for ${element.getAttribute("name") || element.id || "(unnamed)"}`);
      }
      element.value = option.value;
    } else if ((element.getAttribute("type") || "").toLowerCase() === "checkbox") {
      element.checked = ["1", "true", "yes", "on", "checked"].includes(text.toLowerCase());
    } else if ((element.getAttribute("type") || "").toLowerCase() === "radio") {
      element.checked = element.value === text;
    } else {
      const valueSetter = Object.getOwnPropertyDescriptor(element.constructor.prototype, "value")?.set;
      if (valueSetter) {
        valueSetter.call(element, text);
      } else {
        element.value = text;
      }
    }
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const describeField = (key, value, element) => ({
    input_key: String(key),
    name: element.getAttribute("name") || element.id || String(key),
    id: element.id || "",
    tag: element.tagName.toLowerCase(),
    type: element.getAttribute("type") || "",
    length: String(value ?? "").length,
  });
  const describeControl = (control) => ({
    id: control.id || "",
    name: control.getAttribute("name") || "",
    text: compactText(controlText(control), 200),
  });

  if (payload.confirm !== true) {
    throw new Error("confirm=true is required");
  }
  const expectedTemplateId = String(payload.template_id || "");
  if (expectedTemplateId) {
    const pageTemplateId = new URLSearchParams(location.search).get("templateId") || "";
    if (pageTemplateId && pageTemplateId !== expectedTemplateId) {
      throw new Error(`template_id mismatch: page=${pageTemplateId} expected=${expectedTemplateId}`);
    }
  }
  const fields = payload.fields || {};
  if (!fields || typeof fields !== "object" || Array.isArray(fields)) {
    throw new Error("fields must be an object");
  }

  const scheduledFields = [];
  for (const [key, value] of Object.entries(fields)) {
    const element = findLaunchField(key);
    if (!element) {
      throw new Error(`launch field not found: ${key}`);
    }
    scheduledFields.push(describeField(key, value, element));
  }

  const saveDraftControl = findSaveDraftControl();
  if (!saveDraftControl) {
    throw new Error("saveDraft control was not found; refused to click sendId_a or ContinueSubmit");
  }
  const clicked = describeControl(saveDraftControl);

  setTimeout(() => {
    const dialogs = [];
    const originalConfirm = window.confirm;
    const originalAlert = window.alert;
    window.confirm = function bscliLaunchDraftConfirm(message) {
      dialogs.push({ type: "confirm", message: String(message || "").slice(0, 1000), accepted: true });
      return true;
    };
    window.alert = function bscliLaunchDraftAlert(message) {
      dialogs.push({ type: "alert", message: String(message || "").slice(0, 1000) });
      return undefined;
    };
    const outcome = {
      ok: true,
      handler_version: handlerVersion,
      action: "SaveDraft",
      save_attempt_mode: "scheduled_fill_click_ack",
      submitted_count: 0,
      started_at: new Date().toISOString(),
      url: location.href,
      title: document.title,
    };
    try {
      const filledFields = [];
      for (const [key, value] of Object.entries(fields)) {
        const element = findLaunchField(key);
        if (!element) {
          throw new Error(`launch field not found during scheduled fill: ${key}`);
        }
        fillLaunchField(element, value);
        filledFields.push(describeField(key, value, element));
      }
      const freshSaveDraftControl = findSaveDraftControl();
      if (!freshSaveDraftControl) {
        throw new Error("saveDraft control disappeared before scheduled click");
      }
      outcome.filled_fields = filledFields;
      outcome.clicked = describeControl(freshSaveDraftControl);
      outcome.clicked_at = new Date().toISOString();
      freshSaveDraftControl.click();
    } catch (error) {
      outcome.ok = false;
      outcome.error = String(error && error.message ? error.message : error);
      console.error("BSCLI scheduled Seeyon launch save-draft failed", error);
    } finally {
      outcome.dialogs = dialogs;
      window.__bscliLaunchSaveDraftLast = outcome;
      setTimeout(() => {
        window.confirm = originalConfirm;
        window.alert = originalAlert;
      }, Number(payload.dialog_restore_ms || 3000));
    }
  }, 0);

  return {
    draft_saved: true,
    handler_version: handlerVersion,
    save_attempt_mode: "scheduled_fill_click_ack",
    fill_scheduled: true,
    click_scheduled: true,
    action: "SaveDraft",
    clicked,
    scheduled_fields: scheduledFields,
    submitted_count: 0,
    url: location.href,
    title: document.title,
    dialogs: [],
  };
}


function withTimeout(promise, timeoutMs, message) {
  let timer;
  return Promise.race([
    promise.finally(() => clearTimeout(timer)),
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(message)), timeoutMs);
    }),
  ]);
}

async function waitForTabReadable(tabId, timeoutMs, probeIntervalMs = 250) {
  const deadline = Date.now() + timeoutMs;
  let lastError = "";
  while (Date.now() < deadline) {
    try {
      const tab = await getTab(tabId);
      if (tab && tab.status === "complete") {
        return;
      }
      if (await canReadTabDom(tabId)) {
        return;
      }
    } catch (error) {
      lastError = String(error && error.message ? error.message : error);
    }
    await new Promise((resolve) => setTimeout(resolve, Math.min(probeIntervalMs, Math.max(0, deadline - Date.now()))));
  }
  throw new Error(lastError ? `timed out waiting for rendered tab load: ${lastError}` : "timed out waiting for rendered tab load");
}

function getTab(tabId) {
  return new Promise((resolve, reject) => {
    chrome.tabs.get(tabId, (tab) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      resolve(tab);
    });
  });
}

async function canReadTabDom(tabId) {
  try {
    const [injection] = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => ({
        readyState: document.readyState,
        hasBody: Boolean(document.body),
      }),
    });
    const readyState = injection?.result?.readyState || "";
    return Boolean(injection?.result?.hasBody) || readyState === "interactive" || readyState === "complete";
  } catch (_error) {
    return false;
  }
}

function waitForTabComplete(tabId, timeoutMs) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error("timed out waiting for rendered tab load"));
    }, timeoutMs);
    const listener = (updatedTabId, changeInfo) => {
      if (updatedTabId === tabId && changeInfo.status === "complete") {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId, (tab) => {
      if (chrome.runtime.lastError) {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      if (tab && tab.status === "complete") {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    });
  });
}

function collectPageInventory() {
  const compactText = (value, max = 200) => (value || "").replace(/\s+/g, " ").trim().slice(0, max);
  const absoluteUrl = (value) => {
    if (!value) {
      return "";
    }
    try {
      return new URL(value, location.href).href;
    } catch (_error) {
      return value;
    }
  };

  const buttons = Array.from(document.querySelectorAll("button,input[type=button],input[type=submit],a[role=button]"))
    .slice(0, 200)
    .map((element) => ({
      text: compactText(element.innerText || element.value || element.getAttribute("title") || element.getAttribute("aria-label")),
      tag: element.tagName.toLowerCase(),
      id: element.id || "",
      name: element.getAttribute("name") || "",
      type: element.getAttribute("type") || "",
    }))
    .filter((item) => item.text || item.id || item.name);

  const links = Array.from(document.querySelectorAll("a[href]"))
    .slice(0, 300)
    .map((element) => ({
      text: compactText(element.innerText || element.getAttribute("title")),
      href: absoluteUrl(element.getAttribute("href")),
      id: element.id || "",
    }))
    .filter((item) => item.text || item.href);

  const forms = Array.from(document.forms)
    .slice(0, 100)
    .map((form) => ({
      id: form.id || "",
      name: form.getAttribute("name") || "",
      action: absoluteUrl(form.getAttribute("action")),
      method: (form.getAttribute("method") || "get").toLowerCase(),
      fields: Array.from(form.querySelectorAll("input,select,textarea"))
        .slice(0, 100)
        .map((field) => ({
          tag: field.tagName.toLowerCase(),
          name: field.getAttribute("name") || "",
          id: field.id || "",
          type: field.getAttribute("type") || "",
          placeholder: field.getAttribute("placeholder") || "",
        })),
    }));

  const resources = performance.getEntriesByType("resource")
    .filter((entry) => ["fetch", "xmlhttprequest", "beacon"].includes(entry.initiatorType))
    .slice(-200)
    .map((entry) => ({
      name: entry.name,
      initiatorType: entry.initiatorType,
      duration: Math.round(entry.duration),
      startTime: Math.round(entry.startTime),
    }));

  return {
    url: location.href,
    title: document.title,
    text: compactText(document.body?.innerText || "", 30000),
    buttons,
    links,
    forms,
    resources,
  };
}

function installNetworkProbe() {
  const stateKey = "__BSCLI_NETWORK_PROBE__";
  const logKey = "__BSCLI_NETWORK_LOG__";
  if (window[stateKey]) {
    return { installed: true, alreadyInstalled: true, records: (window[logKey] || []).length };
  }

  window[logKey] = window[logKey] || [];
  const appendRecord = (record) => {
    const safeRecord = {
      ...record,
      time: new Date().toISOString(),
      url: String(record.url || "").slice(0, 2000),
      requestBody: record.requestBody == null ? null : String(record.requestBody).slice(0, 4000),
      error: record.error == null ? null : String(record.error).slice(0, 1000),
    };
    window[logKey].push(safeRecord);
    if (window[logKey].length > 500) {
      window[logKey].splice(0, window[logKey].length - 500);
    }
  };

  const originalFetch = window.fetch;
  window.fetch = async function bscliFetch(input, init = {}) {
    const method = (init && init.method) || (input && input.method) || "GET";
    const url = input && input.url ? input.url : input;
    const startedAt = performance.now();
    try {
      const response = await originalFetch.apply(this, arguments);
      appendRecord({
        kind: "fetch",
        method,
        url,
        status: response.status,
        ok: response.ok,
        duration: Math.round(performance.now() - startedAt),
        requestBody: init && init.body,
      });
      return response;
    } catch (error) {
      appendRecord({
        kind: "fetch",
        method,
        url,
        status: 0,
        ok: false,
        duration: Math.round(performance.now() - startedAt),
        requestBody: init && init.body,
        error: error && error.message ? error.message : String(error),
      });
      throw error;
    }
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function bscliOpen(method, url) {
    this.__bscliMethod = method || "GET";
    this.__bscliUrl = url || "";
    return originalOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function bscliSend(body) {
    const startedAt = performance.now();
    this.addEventListener("loadend", () => {
      appendRecord({
        kind: "xmlhttprequest",
        method: this.__bscliMethod || "GET",
        url: this.responseURL || this.__bscliUrl || "",
        status: this.status,
        ok: this.status >= 200 && this.status < 400,
        duration: Math.round(performance.now() - startedAt),
        requestBody: body,
      });
    });
    return originalSend.apply(this, arguments);
  };

  window[stateKey] = true;
  return { installed: true, alreadyInstalled: false, records: window[logKey].length };
}

function collectNetworkLogSnapshot() {
  const records = Array.isArray(window.__BSCLI_NETWORK_LOG__) ? window.__BSCLI_NETWORK_LOG__ : [];
  const resources = performance.getEntriesByType("resource")
    .filter((entry) => ["fetch", "xmlhttprequest", "beacon"].includes(entry.initiatorType))
    .slice(-200)
    .map((entry) => ({
      name: entry.name,
      initiatorType: entry.initiatorType,
      duration: Math.round(entry.duration),
      startTime: Math.round(entry.startTime),
    }));
  return {
    url: location.href,
    title: document.title,
    records: records.slice(-200),
    resources,
  };
}

async function runPageFetch(payload) {
  const method = (payload.method || "GET").toUpperCase();
  const headers = payload.headers || {};
  const maxText = Math.max(0, Math.min(Number(payload.max_text || 20000), 1000000));
  const response = await fetch(payload.url, {
    method,
    headers,
    body: payload.body == null || method === "GET" || method === "HEAD" ? undefined : payload.body,
    credentials: "include",
  });
  const contentType = response.headers.get("content-type") || "";
  const text = await response.text();
  let json = null;
  if (contentType.includes("application/json")) {
    try {
      json = JSON.parse(text);
    } catch (_error) {
      json = null;
    }
  }
  return {
    url: response.url,
    status: response.status,
    ok: response.ok,
    contentType,
    json,
    text: text.slice(0, maxText),
  };
}

function collectOaPendingList() {
  const clean = (value, max = 300) => String(value || "").replace(/\s+/g, " ").trim().slice(0, max);
  const absoluteUrl = (value) => {
    try {
      return new URL(value, location.href).href;
    } catch (_error) {
      return value || "";
    }
  };
  const parseOnclickUrl = (onclick) => {
    const match = String(onclick || "").match(/checkAndOpenLink\('([^']+)'/);
    return match ? match[1].replace(/&amp;/g, "&") : "";
  };
  const parseQueryValue = (url, key) => {
    try {
      const parsed = new URL(url, location.href);
      return parsed.searchParams.get(key) || "";
    } catch (_error) {
      const match = String(url || "").match(new RegExp(`[?&]${key}=([^&]+)`));
      return match ? decodeURIComponent(match[1]) : "";
    }
  };

  const section =
    document.querySelector("#section_556815601453123423") ||
    Array.from(document.querySelectorAll(".sectionPanel")).find((panel) =>
      (panel.innerText || "").includes("鍏ㄩ儴寰呭姙")
    );
  if (!section) {
    return { count: 0, items: [], error: "pending section not found" };
  }

  const rows = Array.from(section.querySelectorAll("tbody tr, tr"));
  const items = rows
    .map((row, index) => {
      const cells = Array.from(row.querySelectorAll("td")).map((cell) => clean(cell.innerText, 300));
      const link = row.querySelector("a.cellContentText, a[onclick*='checkAndOpenLink']");
      const rawHref = parseOnclickUrl(link?.getAttribute("onclick"));
      const title = clean(
        row.querySelector(".titleText")?.innerText ||
          link?.getAttribute("title") ||
          cells[0] ||
          row.innerText,
        500
      );
      if (!title) {
        return null;
      }
      const compactCells = cells.filter(Boolean);
      return {
        index,
        title,
        sender: compactCells[1] || "",
        date: compactCells[2] || "",
        category: compactCells[3] || "",
        affair_id: parseQueryValue(rawHref, "affairId"),
        href: rawHref ? absoluteUrl(rawHref) : "",
        read: !!row.querySelector(".AlreadyRead"),
        raw_text: clean(row.innerText, 800),
      };
    })
    .filter(Boolean);

  return {
    count: items.length,
    items,
  };
}

function collectOaPendingDetail(payload) {
  const affairId = String(payload.affair_id || payload.affairId || "");
  if (!affairId) {
    return {
      found: false,
      error: "affair_id is required",
      item: null,
    };
  }
  const list = collectOaPendingList();
  const item = (list.items || []).find((entry) => String(entry.affair_id) === affairId) || null;
  return {
    found: !!item,
    item,
    count: list.count || 0,
  };
}

function collectOaTemplateList() {
  const clean = (value, max = 300) => String(value || "").replace(/\s+/g, " ").trim().slice(0, max);
  const absoluteUrl = (value) => {
    try {
      return new URL(value, location.href).href;
    } catch (_error) {
      return value || "";
    }
  };
  const parseOpenDataLinkUrl = (onclick) => {
    const match = String(onclick || "").match(/['"]url['"]\s*:\s*['"]([^'"]+)/);
    return match ? match[1].replace(/&amp;/g, "&") : "";
  };
  const parseQueryValue = (url, key) => {
    try {
      const parsed = new URL(url, location.href);
      return parsed.searchParams.get(key) || "";
    } catch (_error) {
      const match = String(url || "").match(new RegExp(`[?&]${key}=([^&]+)`));
      return match ? decodeURIComponent(match[1]) : "";
    }
  };

  const section =
    document.querySelector("#section_-6503951670357636432") ||
    Array.from(document.querySelectorAll(".sectionPanel, [id^='section_']")).find((panel) =>
      (panel.innerText || "").includes("\u6211\u7684\u6a21\u677f")
    );
  if (!section) {
    return { count: 0, items: [], error: "template section not found" };
  }

  const items = Array.from(section.querySelectorAll("table.chessboardtable, .chessboardtable"))
    .map((table, index) => {
      const link = table.querySelector("a[onclick], a") || table.querySelector("[onclick]");
      const clickable = link || table.querySelector("[onclick]") || table;
      const onclick = clickable.getAttribute("onclick") || table.getAttribute("onclick") || "";
      const rawHref = parseOpenDataLinkUrl(onclick);
      const title = clean(
        table.getAttribute("title") ||
          link?.getAttribute("title") ||
          link?.innerText ||
          table.innerText,
        500
      );
      if (!title) {
        return null;
      }
      return {
        index,
        title,
        template_id: parseQueryValue(rawHref, "templateId"),
        href: rawHref ? absoluteUrl(rawHref) : "",
        raw_href: rawHref,
        raw_text: clean(table.innerText, 800),
      };
    })
    .filter(Boolean);

  return {
    count: items.length,
    items,
  };
}

chrome.runtime.onInstalled.addListener(() => {
  pollTasks();
});

chrome.tabs.onActivated.addListener(() => {
  pollTasks();
});

chrome.tabs.onUpdated.addListener((_tabId, changeInfo) => {
  if (changeInfo.status === "complete") {
    pollTasks();
  }
});

setInterval(pollTasks, POLL_INTERVAL_MS);
