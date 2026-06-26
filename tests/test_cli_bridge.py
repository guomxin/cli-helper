import json
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from datetime import UTC, datetime, timedelta

from bscli.browser.bridge import ExtensionBridge
from bscli.adapters.seeyon import build_seeyon_profile, register_seeyon_commands
from bscli.cli.main import print_json
from bscli.cli.main import post_json
from bscli.core.registry import CommandRegistry


OA_URL = "http://10.10.50.110/seeyon/main.do?method=main"


class CliAndBridgeTests(unittest.TestCase):
    def test_cli_add_and_status_system(self):
        with TemporaryDirectory() as tmp:
            add = self._run_cli(
                tmp,
                "system",
                "add",
                "oa",
                "--name",
                "Seeyon OA",
                "--url",
                OA_URL,
            )
            added = json.loads(add.stdout)
            self.assertEqual(added["id"], "oa")
            self.assertEqual(added["allowed_origins"], ["http://10.10.50.110"])

            status = self._run_cli(tmp, "system", "status", "oa")
            loaded = json.loads(status.stdout)
            self.assertEqual(loaded["base_url"], OA_URL)

    def test_cli_init_seeyon_oa_profile(self):
        with TemporaryDirectory() as tmp:
            result = self._run_cli(tmp, "system", "init-seeyon-oa")
            profile = json.loads(result.stdout)

            self.assertEqual(profile["id"], "oa")
            self.assertEqual(profile["base_url"], OA_URL)

    def test_cli_command_list_shows_seeyon_commands(self):
        with TemporaryDirectory() as tmp:
            result = self._run_cli(tmp, "command", "list", "oa")
            commands = json.loads(result.stdout)

            self.assertEqual(
                [command["name"] for command in commands],
                [
                    "api_inspect",
                    "api_replay",
                    "api_save",
                    "capability_map",
                    "current_page_snapshot",
                    "detail_read",
                    "doctor",
                    "history_list",
                    "history_profile",
                    "history_sections",
                    "inbox_analyze",
                    "launch_dry_run",
                    "launch_inspect",
                    "launch_save_draft",
                    "meeting_reply_dry_run",
                    "meeting_reply_execute",
                    "navigation_inventory",
                    "network_api_candidates",
                    "network_log_snapshot",
                    "network_probe_install",
                    "page_inventory",
                    "pending_detail",
                    "pending_list",
                    "pending_list_api",
                    "pending_submit",
                    "sent_list_api",
                    "session_status",
                    "template_detail",
                    "template_list",
                    "template_list_api",
                    "template_match",
                    "workflow_actions",
                    "workflow_attachments",
                    "workflow_brief",
                    "workflow_detail",
                    "workflow_evidence",
                    "workflow_inspect",
                    "workflow_list",
                    "workflow_opinions",
                    "workflow_timeline",
                    "write_capabilities",
                    "write_discover",
                    "write_draft",
                    "write_dry_run",
                    "write_endpoint_candidates",
                    "write_execute",
                    "write_preflight",
                    "write_prepare",
                ],
            )

    def test_cli_tool_manifest_exports_agent_callable_tools(self):
        with TemporaryDirectory() as tmp:
            result = self._run_cli(tmp, "tool", "manifest", "oa")
            manifest = json.loads(result.stdout)
            tools = {tool["name"]: tool for tool in manifest["tools"]}

            self.assertEqual(manifest["schema_version"], "bscli.tool_manifest.v1")
            self.assertIn("oa__pending_list", tools)
            self.assertIn("oa__session_status", tools)
            self.assertEqual(tools["oa__session_status"]["metadata"]["command"], "session_status")
            self.assertEqual(tools["oa__pending_list"]["metadata"]["command"], "pending_list")
            self.assertEqual(
                tools["oa__template_detail"]["input_schema"]["required"],
                ["template_id"],
            )

    def test_cli_mcp_serve_handles_tools_list_over_stdio(self):
        with TemporaryDirectory() as tmp:
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            }
            result = self._run_cli_with_input(
                tmp,
                json.dumps(request) + "\n",
                "mcp",
                "serve",
                "--once",
            )
            response = json.loads(result.stdout)
            tools = {tool["name"]: tool for tool in response["result"]["tools"]}

            self.assertEqual(response["id"], 1)
            self.assertIn("oa__template_detail", tools)
            self.assertEqual(
                tools["oa__template_detail"]["inputSchema"]["required"],
                ["template_id"],
            )

    def test_print_json_falls_back_when_stdout_encoding_rejects_character(self):
        buffer = io.BytesIO()
        stdout = io.TextIOWrapper(buffer, encoding="gbk", errors="strict")

        with patch("sys.stdout", stdout):
            print_json({"text": "A\u00a0B"})
            stdout.flush()

        self.assertEqual(json.loads(buffer.getvalue().decode("gbk"))["text"], "A\u00a0B")

    def test_post_json_accepts_custom_timeout(self):
        seen_payloads = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])).decode("utf-8"))
                seen_payloads.append(body)
                payload = json.dumps({"ok": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        response = post_json(f"http://127.0.0.1:{server.server_port}", {"hello": "world"}, timeout=1)

        self.assertEqual(response, {"ok": True})
        self.assertEqual(seen_payloads, [{"hello": "world"}])

    def test_cli_adapter_parse_seeyon_home_templates(self):
        with TemporaryDirectory() as tmp:
            html_file = Path(tmp) / "home.html"
            html_file.write_text(
                """
                <div id="section_-6503951670357636432">
                  <table class="chessboardtable" title="Seal request">
                    <tr><td onclick="javascript:_openDataLink({'url':'/collaboration/collaboration.do?method=newColl&amp;templateId=-6511139737225050501'},event)">
                      <a>Seal request</a>
                    </td></tr>
                  </table>
                </div>
                """,
                encoding="utf-8",
            )

            result = self._run_cli(
                tmp,
                "adapter",
                "parse-seeyon-home",
                "--kind",
                "templates",
                "--html-file",
                str(html_file),
                "--base-url",
                OA_URL,
            )
            parsed = json.loads(result.stdout)

            self.assertEqual(parsed["count"], 1)
            self.assertEqual(parsed["items"][0]["template_id"], "-6511139737225050501")

    def test_cli_adapter_parse_seeyon_home_navigation(self):
        with TemporaryDirectory() as tmp:
            html_file = Path(tmp) / "home.html"
            html_file.write_text(
                """
                <ul>
                  <li id="spaceLi_5834172664846108460" title="个人空间"
                      onclick="javascript:vPortalMainFrameElements.topCenterNav.showNavigation(0,this)">
                    个人空间
                  </li>
                </ul>
                <ul>
                  <li class="lev1Li">
                    <div class="lev1Title navTitleName" title="待办事项"
                      onclick="javascript:onSeeyonTopNavMenuClick('/seeyon/collaboration/collaboration.do?method=listPending','-74745459456393211','mainfrm','F01_listPending',this)">
                      待办事项
                    </div>
                  </li>
                </ul>
                """,
                encoding="utf-8",
            )

            result = self._run_cli(
                tmp,
                "adapter",
                "parse-seeyon-home",
                "--kind",
                "navigation",
                "--html-file",
                str(html_file),
                "--base-url",
                OA_URL,
            )
            parsed = json.loads(result.stdout)

            self.assertEqual(parsed["portal_count"], 1)
            self.assertEqual(parsed["shortcut_count"], 1)
            self.assertEqual(parsed["shortcuts"][0]["name"], "待办事项")

    def test_extension_bridge_queues_tasks_and_accepts_results(self):
        bridge = ExtensionBridge()

        bridge.register_client("chrome-1", tab_id=12, url=OA_URL, title="OA")
        task_id = bridge.enqueue_task(
            system="oa",
            kind="dom_snapshot",
            payload={"selector": "body"},
        )

        tasks = bridge.poll_tasks("chrome-1")
        self.assertEqual(tasks[0]["id"], task_id)
        self.assertEqual(bridge.poll_tasks("chrome-1"), [])

        bridge.submit_result(
            client_id="chrome-1",
            task_id=task_id,
            ok=True,
            result={"title": "OA"},
        )

        result = bridge.get_result(task_id)
        self.assertEqual(result["result"], {"title": "OA"})

    def test_extension_bridge_routes_targeted_tasks_to_matching_client(self):
        bridge = ExtensionBridge()

        bridge.register_client(
            "wrong-tab",
            tab_id=11,
            url="http://example.test/",
            title="Other",
        )
        bridge.register_client("oa-tab", tab_id=12, url=OA_URL, title="OA")
        task_id = bridge.enqueue_task(
            system="oa",
            kind="html_snapshot",
            payload={},
            target_client_id="oa-tab",
        )

        self.assertEqual(bridge.poll_tasks("wrong-tab"), [])
        tasks = bridge.poll_tasks("oa-tab")

        self.assertEqual(tasks[0]["id"], task_id)
        self.assertEqual(tasks[0]["target_client_id"], "oa-tab")
        self.assertEqual(bridge.poll_tasks("oa-tab"), [])

    def test_extension_bridge_keeps_one_client_per_browser_tab(self):
        bridge = ExtensionBridge()

        bridge.register_client("chrome-1:tab:11", tab_id=11, url="http://example.test/", title="Other")
        bridge.register_client("chrome-1:tab:12", tab_id=12, url=OA_URL, title="OA")

        clients = bridge.list_clients()

        self.assertEqual({client["client_id"] for client in clients}, {"chrome-1:tab:11", "chrome-1:tab:12"})
        self.assertEqual({client["tab_id"] for client in clients}, {11, 12})

    def test_extension_bridge_prunes_stale_tab_clients(self):
        bridge = ExtensionBridge()
        bridge.register_client("stale-tab", tab_id=11, url=OA_URL, title="Old OA")
        bridge.register_client("fresh-tab", tab_id=12, url=OA_URL, title="Fresh OA")
        bridge.clients["stale-tab"].registered_at = (
            datetime.now(UTC) - timedelta(seconds=300)
        ).isoformat()

        clients = bridge.list_clients()

        self.assertEqual([client["client_id"] for client in clients], ["fresh-tab"])

    def test_seeyon_adapter_registers_initial_commands(self):
        profile = build_seeyon_profile()
        registry = CommandRegistry()

        register_seeyon_commands(registry)

        self.assertEqual(profile.id, "oa")
        self.assertEqual(profile.base_origin, "http://10.10.50.110")
        self.assertEqual(
            [command.name for command in registry.list("oa")],
                [
                    "api_inspect",
                    "api_replay",
                    "api_save",
                    "capability_map",
                    "current_page_snapshot",
                    "detail_read",
                    "doctor",
                    "history_list",
                    "history_profile",
                    "history_sections",
                    "inbox_analyze",
                    "launch_dry_run",
                    "launch_inspect",
                    "launch_save_draft",
                    "meeting_reply_dry_run",
                    "meeting_reply_execute",
                    "navigation_inventory",
                    "network_api_candidates",
                    "network_log_snapshot",
                    "network_probe_install",
                    "page_inventory",
                    "pending_detail",
                    "pending_list",
                    "pending_list_api",
                    "pending_submit",
                    "sent_list_api",
                    "session_status",
                    "template_detail",
                    "template_list",
                    "template_list_api",
                    "template_match",
                    "workflow_actions",
                    "workflow_attachments",
                    "workflow_brief",
                    "workflow_detail",
                    "workflow_evidence",
                    "workflow_inspect",
                    "workflow_list",
                    "workflow_opinions",
                    "workflow_timeline",
                    "write_capabilities",
                    "write_discover",
                    "write_draft",
                    "write_dry_run",
                    "write_endpoint_candidates",
                    "write_execute",
                    "write_preflight",
                    "write_prepare",
                ],
        )

    def _run_cli(self, home: str, *args: str) -> subprocess.CompletedProcess:
        return self._run_cli_with_input(home, None, *args)

    def _run_cli_with_input(
        self,
        home: str,
        stdin: str | None,
        *args: str,
    ) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd())
        return subprocess.run(
            [sys.executable, "-m", "bscli.cli.main", "--home", home, *args],
            cwd=Path.cwd(),
            env=env,
            text=True,
            input=stdin,
            capture_output=True,
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
