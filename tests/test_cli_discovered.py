import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from tempfile import TemporaryDirectory


class CliDiscoveredTests(unittest.TestCase):
    def test_cli_discovered_list_and_show_saved_api(self):
        with TemporaryDirectory() as tmp:
            self._write_api(tmp)

            listed = self._run_cli(tmp, "discovered", "list", "oa")
            shown = self._run_cli(tmp, "discovered", "show", "oa", "template-section")

            apis = json.loads(listed.stdout)
            api = json.loads(shown.stdout)
            self.assertEqual(apis[0]["name"], "template-section")
            self.assertEqual(apis[0]["tool_name"], "oa__discovered__template_section")
            self.assertEqual(apis[0]["data_shape"], "Data.items[]")
            self.assertEqual(api["request"]["url"], "http://oa.example.test/ajax.do")

    def test_cli_discovered_run_posts_to_daemon(self):
        seen_payloads = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])).decode("utf-8"))
                seen_payloads.append(body)
                response = {"ok": True, "result": {"name": body["args"]["name"]}}
                payload = json.dumps(response).encode("utf-8")
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

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "discovered",
                "run",
                "oa",
                "template-section",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        self.assertEqual(json.loads(result.stdout)["result"], {"name": "template-section"})
        self.assertEqual(
            seen_payloads,
            [
                {
                    "system": "oa",
                    "command": "discovered_run",
                    "args": {"name": "template-section"},
                    "timeout_seconds": 30.0,
                }
            ],
        )

    def test_cli_discovered_run_posts_confirm_flag_to_daemon(self):
        seen_payloads = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])).decode("utf-8"))
                seen_payloads.append(body)
                response = {"ok": True, "result": {"confirmed": body["args"]["confirm"]}}
                payload = json.dumps(response).encode("utf-8")
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

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "discovered",
                "run",
                "oa",
                "submit",
                "--confirm",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        self.assertEqual(json.loads(result.stdout)["result"], {"confirmed": True})
        self.assertEqual(
            seen_payloads[0]["args"],
            {"name": "submit", "confirm": True},
        )

    def test_cli_discovered_run_posts_json_arguments_to_daemon(self):
        seen_payloads = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])).decode("utf-8"))
                seen_payloads.append(body)
                response = {"ok": True, "result": {"keyword": body["args"]["keyword"]}}
                payload = json.dumps(response).encode("utf-8")
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

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "discovered",
                "run",
                "oa",
                "search",
                "--json",
                '{"keyword":"budget","page":2}',
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        self.assertEqual(json.loads(result.stdout)["result"], {"keyword": "budget"})
        self.assertEqual(
            seen_payloads[0]["args"],
            {"name": "search", "keyword": "budget", "page": 2},
        )

    def test_tool_manifest_includes_discovered_tools(self):
        with TemporaryDirectory() as tmp:
            self._write_api(tmp)

            result = self._run_cli(tmp, "tool", "manifest", "oa")
            manifest = json.loads(result.stdout)
            tools = {tool["name"]: tool for tool in manifest["tools"]}

            self.assertIn("oa__discovered__template_section", tools)
            self.assertEqual(
                tools["oa__discovered__template_section"]["metadata"]["command"],
                "discovered_run",
            )
            self.assertEqual(
                tools["oa__discovered__template_section"]["metadata"]["discovered_api"],
                "template-section",
            )

    def test_cli_mcp_serve_lists_discovered_tools(self):
        with TemporaryDirectory() as tmp:
            self._write_api(tmp)
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
            self.assertIn("oa__discovered__template_section", tools)

    def _write_api(self, home: str) -> None:
        api_dir = Path(home) / "discovered" / "oa" / "apis"
        api_dir.mkdir(parents=True)
        (api_dir / "template-section.json").write_text(
            json.dumps(
                {
                    "schema_version": "bscli.discovered_api.v1",
                    "name": "template-section",
                    "system": "oa",
                    "description": "Template section projection",
                    "request": {"method": "GET", "url": "http://oa.example.test/ajax.do"},
                    "inspection": {
                        "data_shape": "Data.items[]",
                        "item_count": 36,
                        "sample_fields": ["title", "link"],
                    },
                }
            ),
            encoding="utf-8",
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
