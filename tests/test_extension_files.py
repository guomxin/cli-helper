import json
from pathlib import Path
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
        self.assertIn("dealSubmitFunc", background)
        self.assertIn("content_deal_comment", background)
        self.assertNotIn("findSeeyonCommentElement();", background)


if __name__ == "__main__":
    unittest.main()
