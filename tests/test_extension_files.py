import json
from pathlib import Path
import shutil
import subprocess
import unittest


class ExtensionFilesTests(unittest.TestCase):
    def test_manifest_declares_minimum_bridge_permissions(self):
        manifest = json.loads(Path("extension/manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["manifest_version"], 3)
        self.assertIn("scripting", manifest["permissions"])
        self.assertIn("tabs", manifest["permissions"])
        self.assertIn("http://127.0.0.1:8765/*", manifest["host_permissions"])
        self.assertIn("http://10.10.50.110/*", manifest["host_permissions"])

    def test_extension_scripts_exist(self):
        self.assertTrue(Path("extension/background.js").exists())
        self.assertTrue(Path("extension/content.js").exists())

    def test_background_js_syntax_is_valid_when_node_is_available(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available")
        result = subprocess.run(
            [node, "--check", "extension/background.js"],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_background_waits_for_readable_page_when_tab_never_completes(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available")
        script = r"""
const fs = require("fs");
const vm = require("vm");

const listeners = [];
const sandbox = {
  console,
  URL,
  crypto: { randomUUID: () => "uuid" },
  setTimeout,
  clearTimeout,
  setInterval: () => 0,
  fetch: async () => ({ json: async () => ({ tasks: [] }) }),
  chrome: {
    storage: { local: { get: async () => ({}), set: async () => undefined } },
    runtime: { lastError: null, onInstalled: { addListener: () => undefined } },
    tabs: {
      query: async () => [],
      onActivated: { addListener: () => undefined },
      onUpdated: {
        addListener: (listener) => listeners.push(listener),
        removeListener: (listener) => {
          const index = listeners.indexOf(listener);
          if (index >= 0) listeners.splice(index, 1);
        },
      },
      get: (tabId, callback) => callback({ id: tabId, status: "loading" }),
    },
    scripting: {
      executeScript: async () => [{ result: { readyState: "interactive" } }],
    },
  },
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync("extension/background.js", "utf8"), sandbox);
(async () => {
  if (typeof sandbox.waitForTabReadable !== "function") {
    throw new Error("waitForTabReadable is not exported in background context");
  }
  await sandbox.waitForTabReadable(7, 100, 10);
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
"""
        result = subprocess.run(
            [node, "-e", script],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_background_prefers_direct_deal_submit_for_inform_nodes(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available")
        script = r"""
const fs = require("fs");
const vm = require("vm");

const sandbox = {
  console,
  URL,
  crypto: { randomUUID: () => "uuid" },
  setTimeout,
  clearTimeout,
  setInterval: () => 0,
  fetch: async () => ({ json: async () => ({ tasks: [] }) }),
  chrome: {
    storage: { local: { get: async () => ({}), set: async () => undefined } },
    runtime: { lastError: null, onInstalled: { addListener: () => undefined } },
    tabs: {
      query: async () => [],
      onActivated: { addListener: () => undefined },
      onUpdated: { addListener: () => undefined, removeListener: () => undefined },
      get: (tabId, callback) => callback({ id: tabId, status: "complete" }),
    },
    scripting: { executeScript: async () => [{ result: {} }] },
  },
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync("extension/background.js", "utf8"), sandbox);

const entries = {
  submitClickFunc: () => "submit",
  dealSubmitFunc: () => "deal",
  contentCallbackDealSubmit: () => "callback",
};
const informEntry = sandbox.chooseSeeyonSubmitEntry(entries, { node_policy: "inform", node_policy_name: "知会" });
if (informEntry.name !== "dealSubmitFunc" || informEntry.reason !== "inform_node_direct_deal_submit") {
  throw new Error(`expected inform node to use dealSubmitFunc, got ${JSON.stringify(informEntry)}`);
}
const normalEntry = sandbox.chooseSeeyonSubmitEntry(entries, { node_policy: "collaboration" });
if (normalEntry.name !== "submitClickFunc" || normalEntry.reason !== "default_submit_click") {
  throw new Error(`expected normal node to use submitClickFunc, got ${JSON.stringify(normalEntry)}`);
}
"""
        result = subprocess.run(
            [node, "-e", script],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_continue_submit_injected_function_is_self_contained_for_inform_nodes(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available")
        script = r"""
const fs = require("fs");
const vm = require("vm");

const backgroundSandbox = {
  console,
  URL,
  crypto: { randomUUID: () => "uuid" },
  setTimeout,
  clearTimeout,
  setInterval: () => 0,
  fetch: async () => ({ json: async () => ({ tasks: [] }) }),
  chrome: {
    storage: { local: { get: async () => ({}), set: async () => undefined } },
    runtime: { lastError: null, onInstalled: { addListener: () => undefined } },
    tabs: {
      query: async () => [],
      onActivated: { addListener: () => undefined },
      onUpdated: { addListener: () => undefined, removeListener: () => undefined },
      get: (tabId, callback) => callback({ id: tabId, status: "complete" }),
    },
    scripting: { executeScript: async () => [{ result: {} }] },
  },
};
vm.createContext(backgroundSandbox);
vm.runInContext(fs.readFileSync("extension/background.js", "utf8"), backgroundSandbox);

class FakeEvent {
  constructor(type) {
    this.type = type;
  }
}

class FakeElement {
  constructor(value = "") {
    this._value = value;
    this.events = [];
    this.attrs = {};
  }
  get value() {
    return this._value;
  }
  set value(nextValue) {
    this._value = String(nextValue || "");
  }
  dispatchEvent(event) {
    this.events.push(event.type);
  }
  focus() {
    this.focused = true;
  }
  getAttribute(name) {
    return this.attrs[name] || "";
  }
}

const comment = new FakeElement("");
const hiddenAttitudeCode = new FakeElement("");
const hiddenAttitude = new FakeElement("");
const nodeAttitude = new FakeElement("");
let dealSubmitCalled = false;
const pageSandbox = {
  console,
  URLSearchParams,
  location: { href: "http://oa.example.test/detail?affairId=affair-1", search: "?affairId=affair-1" },
  Event: FakeEvent,
  setTimeout: (fn) => {
    fn();
    return 1;
  },
  document: {
    title: "OA Detail",
    body: { innerText: "" },
    querySelector: (selector) => {
      if (selector === "#affairId") return new FakeElement("affair-1");
      if (selector === "#subject") return new FakeElement("周报发送流程");
      if (selector === "#title") return null;
      if (selector === "#content_deal_comment" || selector === "textarea[name='content_deal_comment']") return comment;
      if (selector === "textarea#content" || selector === "textarea[name='content']") return null;
      if (selector === "#hidAttitudeCode") return hiddenAttitudeCode;
      if (selector === "#hidAttitude") return hiddenAttitude;
      if (selector === "#nodeattitude") return nodeAttitude;
      if (selector === "#zwIframe") return null;
      return null;
    },
    querySelectorAll: () => [],
  },
  nodePolicy: "inform",
  nodePolicyName: "\u77e5\u4f1a",
  affairId: "affair-1",
  confirm: () => true,
  alert: () => undefined,
  dealSubmitFunc: () => {
    dealSubmitCalled = true;
  },
};
pageSandbox.window = pageSandbox;
comment.ownerDocument = { defaultView: pageSandbox };
hiddenAttitudeCode.ownerDocument = { defaultView: pageSandbox };
hiddenAttitude.ownerDocument = { defaultView: pageSandbox };
nodeAttitude.ownerDocument = { defaultView: pageSandbox };
vm.createContext(pageSandbox);

const payload = {
  affair_id: "affair-1",
  action: "ContinueSubmit",
  opinion: "已阅",
  confirm: true,
};
pageSandbox.payload = payload;
const source = backgroundSandbox.runSeeyonContinueSubmit.toString();
const result = vm.runInContext(`(${source})(payload)`, pageSandbox);
if (!dealSubmitCalled) {
  throw new Error("expected serialized injected function to call dealSubmitFunc");
}
if (result.submit_entry !== "dealSubmitFunc" || result.submit_entry_reason !== "inform_node_direct_deal_submit") {
  throw new Error(`expected inform submit to use dealSubmitFunc, got ${JSON.stringify(result)}`);
}
if (comment.value !== "已阅") {
  throw new Error(`expected opinion to be filled, got ${comment.value}`);
}
if (!pageSandbox.__bscliContinueSubmitLast || pageSandbox.__bscliContinueSubmitLast.submit_entry !== "dealSubmitFunc") {
  throw new Error(`expected scheduled submit outcome, got ${JSON.stringify(pageSandbox.__bscliContinueSubmitLast)}`);
}
"""
        result = subprocess.run(
            [node, "-e", script],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_background_runs_page_script_source_in_page_world(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available")
        script = r"""
const fs = require("fs");
const vm = require("vm");

const backgroundSandbox = {
  console,
  URL,
  crypto: { randomUUID: () => "uuid" },
  setTimeout,
  clearTimeout,
  setInterval: () => 0,
  fetch: async () => ({ json: async () => ({ tasks: [] }) }),
  chrome: {
    storage: { local: { get: async () => ({}), set: async () => undefined } },
    runtime: { lastError: null, onInstalled: { addListener: () => undefined } },
    tabs: {
      query: async () => [],
      onActivated: { addListener: () => undefined },
      onUpdated: { addListener: () => undefined, removeListener: () => undefined },
      get: (tabId, callback) => callback({ id: tabId, status: "complete" }),
    },
    scripting: { executeScript: async () => [{ result: {} }] },
  },
};
vm.createContext(backgroundSandbox);
vm.runInContext(fs.readFileSync("extension/background.js", "utf8"), backgroundSandbox);

const pageSandbox = {
  console,
  document: { title: "OA Detail" },
  location: { href: "http://oa.example.test/detail" },
};
pageSandbox.window = pageSandbox;
pageSandbox.payload = {
  script_name: "probe.script",
  script_source: `
function bscliPageScript(payload) {
  window.__bscliProbeResult = { value: payload.value, title: document.title };
  return { ok: true, value: payload.value, title: document.title };
}
`,
  script_payload: { value: "from-daemon" },
};
vm.createContext(pageSandbox);
const source = backgroundSandbox.runPageScriptSource.toString();
const result = vm.runInContext(`(${source})(payload)`, pageSandbox);
if (!result.ok || result.value !== "from-daemon" || result.title !== "OA Detail") {
  throw new Error(`unexpected page script result: ${JSON.stringify(result)}`);
}
if (!pageSandbox.__bscliProbeResult || pageSandbox.__bscliProbeResult.value !== "from-daemon") {
  throw new Error(`page global was not updated: ${JSON.stringify(pageSandbox.__bscliProbeResult)}`);
}
"""
        result = subprocess.run(
            [node, "-e", script],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_launch_save_draft_executor_prefers_daemon_supplied_page_script(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available")
        script = r"""
const fs = require("fs");
const vm = require("vm");

let executeScriptOptions = [];
const sandbox = {
  console,
  URL,
  URLSearchParams,
  crypto: { randomUUID: () => "uuid" },
  setTimeout,
  clearTimeout,
  setInterval: () => 0,
  fetch: async () => ({ json: async () => ({ tasks: [] }) }),
  chrome: {
    storage: { local: { get: async () => ({}), set: async () => undefined } },
    runtime: { lastError: null, onInstalled: { addListener: () => undefined } },
    tabs: {
      query: async () => [],
      create: async () => ({ id: 77 }),
      remove: async () => undefined,
      onActivated: { addListener: () => undefined },
      onUpdated: { addListener: () => undefined, removeListener: () => undefined },
      get: (tabId, callback) => callback({ id: tabId, status: "complete" }),
    },
    scripting: {
      executeScript: async (options) => {
        executeScriptOptions.push(options);
        if (options.func && options.func.name === "runPageScriptSource") {
          return [{ result: { draft_saved: true, script_runner: true, submitted_count: 0 } }];
        }
        if (options.func && options.func.name === "collectPageScriptOutcome") {
          return [{ result: { ok: true, action: "SaveDraft" } }];
        }
        throw new Error(`unexpected injected function: ${options.func && options.func.name}`);
      },
    },
  },
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync("extension/background.js", "utf8"), sandbox);
(async () => {
  const result = await sandbox.executeSeeyonLaunchSaveDraft({
    confirm: true,
    url: "http://oa.example.test/new?templateId=tpl-1",
    script_name: "seeyon.launch_save_draft.v1",
    script_source: "function bscliPageScript(payload) { return { draft_saved: true, submitted_count: 0 }; }",
    outcome_key: "__bscliLaunchSaveDraftLast",
    keep_tab: false,
    settle_ms: 0,
    after_save_wait_ms: 0,
  });
  if (!result.draft_saved || result.script_runner !== true) {
    throw new Error(`unexpected launch save result: ${JSON.stringify(result)}`);
  }
  const injected = executeScriptOptions.find((item) => item.func && item.func.name === "runPageScriptSource");
  if (!injected) {
    throw new Error(`runPageScriptSource was not used: ${executeScriptOptions.map((item) => item.func && item.func.name).join(",")}`);
  }
  if (injected.args[0].script_name !== "seeyon.launch_save_draft.v1") {
    throw new Error(`script_name was not forwarded: ${JSON.stringify(injected.args[0])}`);
  }
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
"""
        result = subprocess.run(
            [node, "-e", script],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_background_rendered_snapshot_collects_all_frames(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available")
        script = r"""
const fs = require("fs");
const vm = require("vm");

const sandbox = {
  console,
  URL,
  crypto: { randomUUID: () => "uuid" },
  setTimeout,
  clearTimeout,
  setInterval: () => 0,
  fetch: async () => ({ json: async () => ({ tasks: [] }) }),
  chrome: {
    storage: { local: { get: async () => ({}), set: async () => undefined } },
    runtime: { lastError: null, onInstalled: { addListener: () => undefined } },
    tabs: {
      query: async () => [],
      create: async () => ({ id: 7 }),
      remove: async () => undefined,
      onActivated: { addListener: () => undefined },
      onUpdated: { addListener: () => undefined, removeListener: () => undefined },
      get: (tabId, callback) => callback({ id: tabId, status: "complete" }),
    },
    scripting: {
      executeScript: async (options) => {
        if (options.target && options.target.allFrames) {
          return [
            { frameId: 0, result: { url: "http://oa.example.test/top", title: "Top", html: "<html>top</html>" } },
            { frameId: 2, result: { url: "http://oa.example.test/frame", title: "Frame", html: "<html>frame</html>" } },
          ];
        }
        return [{ frameId: 0, result: { url: "http://oa.example.test/top", title: "Top", html: "<html>top</html>" } }];
      },
    },
  },
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync("extension/background.js", "utf8"), sandbox);
(async () => {
  const result = await sandbox.collectRenderedHtmlSnapshot({ url: "http://oa.example.test/top", settle_ms: 0 });
  if (!Array.isArray(result.frames) || result.frames.length !== 2) {
    throw new Error(`expected two rendered frames, got ${JSON.stringify(result.frames)}`);
  }
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
"""
        result = subprocess.run(
            [node, "-e", script],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_background_contains_page_inventory_collector(self):
        background = Path("extension/background.js").read_text(encoding="utf-8")

        self.assertIn("page_inventory", background)
        self.assertIn("collectPageInventory", background)
        self.assertIn("performance.getEntriesByType", background)

    def test_background_contains_html_snapshot_collector(self):
        background = Path("extension/background.js").read_text(encoding="utf-8")

        self.assertIn("html_snapshot", background)
        self.assertIn("collectHtmlSnapshot", background)
        self.assertIn("document.documentElement.outerHTML", background)

    def test_background_contains_network_probe(self):
        background = Path("extension/background.js").read_text(encoding="utf-8")

        self.assertIn("network_probe_install", background)
        self.assertIn("network_log_snapshot", background)
        self.assertIn("installNetworkProbe", background)
        self.assertIn("XMLHttpRequest.prototype.open", background)
        self.assertIn("window.fetch", background)

    def test_background_contains_page_fetch_task(self):
        background = Path("extension/background.js").read_text(encoding="utf-8")

        self.assertIn("page_fetch", background)
        self.assertIn("runPageFetch", background)
        self.assertIn("credentials: \"include\"", background)
        self.assertIn("max_text", background)

    def test_background_contains_page_script_runner(self):
        background = Path("extension/background.js").read_text(encoding="utf-8")

        self.assertIn("page_script_execute", background)
        self.assertIn("executePageScriptTask", background)
        self.assertIn("runPageScriptSource", background)
        self.assertIn("script_source", background)
        self.assertIn("bscliPageScript", background)
        self.assertIn("outcome_key", background)

    def test_background_contains_oa_pending_list_collector(self):
        background = Path("extension/background.js").read_text(encoding="utf-8")

        self.assertIn("oa_pending_list", background)
        self.assertIn("collectOaPendingList", background)
        self.assertIn("affairId", background)

    def test_background_contains_oa_pending_detail_collector(self):
        background = Path("extension/background.js").read_text(encoding="utf-8")

        self.assertIn("oa_pending_detail", background)
        self.assertIn("collectOaPendingDetail", background)
        self.assertIn("affair_id", background)

    def test_background_contains_oa_template_list_collector(self):
        background = Path("extension/background.js").read_text(encoding="utf-8")

        self.assertIn("oa_template_list", background)
        self.assertIn("collectOaTemplateList", background)
        self.assertIn("templateId", background)

    def test_background_contains_seeyon_write_executor(self):
        background = Path("extension/background.js").read_text(encoding="utf-8")

        self.assertIn("seeyon_write_execute", background)
        self.assertIn("BACKGROUND_VERSION", background)
        self.assertIn("extension_version", background)
        self.assertIn("executeSeeyonWrite", background)
        self.assertIn("runPageScriptSource", background)
        self.assertIn("script_source", background)
        self.assertIn("runSeeyonContinueSubmit", background)
        self.assertIn("submitClickFunc", background)
        self.assertIn("dealSubmitFunc", background)
        self.assertIn("content_deal_comment", background)
        self.assertIn("handlerVersion = \"continue-submit-v5-cap4-outer-wait\"", background)
        self.assertIn("selectAgreeAttitude", background)
        self.assertIn("fillCap4InterviewApproval", background)
        self.assertIn("waitSeeyonWriteBusinessFormReady", background)
        self.assertIn("collectSeeyonWriteBusinessFormReadiness", background)
        self.assertIn("business_form_wait_ms", background)
        self.assertIn("cap4_wait_attempts", background)
        self.assertIn("textContent", background)
        self.assertIn("frame_html_length", background)
        self.assertIn("field0038_present", background)
        self.assertIn("field0041_present", background)
        self.assertIn("function runSeeyonContinueSubmit", background)
        self.assertNotIn("async function runSeeyonContinueSubmit", background)
        self.assertIn("#zwIframe", background)
        self.assertIn("#field0038_id", background)
        self.assertIn("#field0041_id", background)
        self.assertIn("hidAttitudeCode", background)
        self.assertIn("submit_entry", background)
        self.assertIn("business_form", background)
        self.assertIn("submit_scheduled: true", background)
        self.assertIn("/extension/task-events", background)
        self.assertIn("detail_tab_created", background)
        self.assertIn("injecting_submit_script", background)
        self.assertIn("Seeyon write detail tab timed out before becoming readable", background)
        self.assertIn("Seeyon write execute script timed out before scheduling submit", background)
        self.assertIn("window.setTimeout(runSubmit, 0)", background)
        self.assertNotIn("findSeeyonCommentElement();", background)

    def test_background_contains_launch_save_draft_executor_with_submit_guard(self):
        background = Path("extension/background.js").read_text(encoding="utf-8")

        self.assertIn("seeyon_launch_save_draft", background)
        self.assertIn("executeSeeyonLaunchSaveDraft", background)
        self.assertIn("runPageScriptSource", background)
        self.assertIn("script_source", background)
        self.assertIn("runSeeyonLaunchSaveDraft", background)
        self.assertIn("saveDraft", background)
        self.assertIn("sendId_a", background)
        self.assertIn("ContinueSubmit", background)
        self.assertIn("submitted_count: 0", background)
        self.assertIn("click_scheduled: true", background)
        self.assertIn("handlerVersion = \"launch-save-draft-v3-scheduled-fill-click\"", background)
        self.assertIn("script_timeout_ms", background)
        self.assertIn("withTimeout(", background)
        self.assertIn("scheduled_fields", background)
        self.assertIn("freshSaveDraftControl.click()", background)
        self.assertIn("window.__bscliLaunchSaveDraftLast", background)
        self.assertIn("value.includes(\"\\u53d1\\u9001\")", background)
        self.assertNotIn("function findSeeyonLaunchField", background)


if __name__ == "__main__":
    unittest.main()
