const DAEMON_URL = "http://127.0.0.1:8765";
const POLL_INTERVAL_MS = 1500;

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

async function registerTab(baseClientId, tab) {
  const clientId = clientIdForTab(baseClientId, tab.id);
  await postJson("/extension/register", {
    client_id: clientId,
    tab_id: tab.id,
    url: tab.url,
    title: tab.title || "",
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
      const result = await executeSeeyonWrite(task.payload || {});
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
    await waitForTabComplete(tab.id, 20000);
    if (settleMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, settleMs));
    }
    const [injection] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: collectHtmlSnapshot,
    });
    return injection.result;
  } finally {
    if (tab.id) {
      await chrome.tabs.remove(tab.id).catch(() => {});
    }
  }
}

async function executeSeeyonWrite(payload) {
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
  const tab = await chrome.tabs.create({ url, active: false });
  try {
    await waitForTabComplete(tab.id, 30000);
    await new Promise((resolve) => setTimeout(resolve, Number(payload.settle_ms || 2000)));
    const [injection] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      func: runSeeyonContinueSubmit,
      args: [payload],
    });
    return injection.result;
  } finally {
    if (tab.id && payload.keep_tab !== true) {
      await chrome.tabs.remove(tab.id).catch(() => {});
    }
  }
}

async function runSeeyonContinueSubmit(payload) {
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
  if (typeof window.doZCDB !== "function") {
    throw new Error("Seeyon submit function doZCDB was not found on the detail page");
  }

  const records = [];
  const dialogs = [];
  const originalFetch = window.fetch;
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  const originalConfirm = window.confirm;
  const originalAlert = window.alert;

  const appendRecord = (record) => {
    records.push({
      ...record,
      url: String(record.url || "").slice(0, 2000),
      requestBody: record.requestBody == null ? null : String(record.requestBody).slice(0, 2000),
      responseText: record.responseText == null ? null : String(record.responseText).slice(0, 2000),
      error: record.error == null ? null : String(record.error).slice(0, 1000),
    });
    if (records.length > 100) {
      records.splice(0, records.length - 100);
    }
  };

  window.fetch = async function bscliWriteFetch(input, init = {}) {
    const method = (init && init.method) || (input && input.method) || "GET";
    const url = input && input.url ? input.url : input;
    try {
      const response = await originalFetch.apply(this, arguments);
      let responseText = null;
      try {
        responseText = await response.clone().text();
      } catch (_error) {
        responseText = null;
      }
      appendRecord({
        kind: "fetch",
        method,
        url,
        status: response.status,
        ok: response.ok,
        requestBody: init && init.body,
        responseText,
      });
      return response;
    } catch (error) {
      appendRecord({
        kind: "fetch",
        method,
        url,
        status: 0,
        ok: false,
        requestBody: init && init.body,
        error: error && error.message ? error.message : String(error),
      });
      throw error;
    }
  };

  XMLHttpRequest.prototype.open = function bscliWriteOpen(method, url) {
    this.__bscliWriteMethod = method || "GET";
    this.__bscliWriteUrl = url || "";
    return originalOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function bscliWriteSend(body) {
    this.addEventListener("loadend", () => {
      appendRecord({
        kind: "xmlhttprequest",
        method: this.__bscliWriteMethod || "GET",
        url: this.responseURL || this.__bscliWriteUrl || "",
        status: this.status,
        ok: this.status >= 200 && this.status < 400,
        requestBody: body,
        responseText: this.responseText,
      });
    });
    return originalSend.apply(this, arguments);
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
    const comment = findSeeyonCommentElement();
    if (!comment) {
      throw new Error("content_deal_comment was not found on the detail page");
    }
    const valueSetter = Object.getOwnPropertyDescriptor(comment.constructor.prototype, "value")?.set;
    if (valueSetter) {
      valueSetter.call(comment, opinion);
    } else {
      comment.value = opinion;
    }
    comment.dispatchEvent(new Event("input", { bubbles: true }));
    comment.dispatchEvent(new Event("change", { bubbles: true }));

    const returned = window.doZCDB();
    if (returned && typeof returned.then === "function") {
      await returned;
    }
    const deadline = Date.now() + Number(payload.wait_ms || 12000);
    while (Date.now() < deadline) {
      if (records.some((record) => String(record.url || "").includes("finishWorkItem"))) {
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  } finally {
    window.fetch = originalFetch;
    XMLHttpRequest.prototype.open = originalOpen;
    XMLHttpRequest.prototype.send = originalSend;
    window.confirm = originalConfirm;
    window.alert = originalAlert;
  }

  const finishRecords = records.filter((record) => String(record.url || "").includes("finishWorkItem"));
  const successfulFinish = finishRecords.some((record) => Number(record.status) >= 200 && Number(record.status) < 400);
  if (!successfulFinish) {
    throw new Error(`Seeyon finishWorkItem request was not observed or did not succeed; observed=${finishRecords.length}`);
  }
  return {
    submitted: true,
    affair_id: expectedAffairId,
    action: "ContinueSubmit",
    opinion_length: opinion.length,
    url: location.href,
    title: document.title,
    dialogs,
    records: finishRecords.slice(-5),
  };
}

function findSeeyonCommentElement() {
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
    text: text.slice(0, 20000),
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
      (panel.innerText || "").includes("全部待办")
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
