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
        self.assertIn("executeSeeyonWrite", background)
        self.assertIn("runSeeyonContinueSubmit", background)
        self.assertIn("submitClickFunc", background)
        self.assertIn("dealSubmitFunc", background)
        self.assertIn("content_deal_comment", background)
        self.assertIn("handlerVersion = \"continue-submit-v3-page-submit-click\"", background)
        self.assertIn("selectAgreeAttitude", background)
        self.assertIn("hidAttitudeCode", background)
        self.assertIn("submit_entry", background)
        self.assertIn("submit_scheduled: true", background)
        self.assertIn("window.setTimeout(runSubmit, 0)", background)
        self.assertNotIn("findSeeyonCommentElement();", background)

    def test_background_contains_launch_save_draft_executor_with_submit_guard(self):
        background = Path("extension/background.js").read_text(encoding="utf-8")

        self.assertIn("seeyon_launch_save_draft", background)
        self.assertIn("executeSeeyonLaunchSaveDraft", background)
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
