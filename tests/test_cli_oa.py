import csv
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from tempfile import TemporaryDirectory


class CliOaTests(unittest.TestCase):
    def test_oa_pending_list_maps_to_api_command_and_filters_items(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "run_id": "run-1",
                "result": {
                    "count": 2,
                    "items": [
                        {"title": "Budget approval", "affair_id": "a1"},
                        {"title": "Travel request", "affair_id": "a2"},
                    ],
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "pending",
                "list",
                "--keyword",
                "budget",
                "--limit",
                "1",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "pending_list_api")
        self.assertEqual(seen_payloads[0]["system"], "oa")
        self.assertEqual(payload["result"]["count"], 1)
        self.assertEqual(payload["result"]["items"][0]["title"], "Budget approval")

    def test_oa_template_show_maps_id_argument_to_template_detail(self):
        server, seen_payloads = self._start_daemon(
            {"ok": True, "result": {"found": True, "item": {"template_id": "tpl-1"}}}
        )

        with TemporaryDirectory() as tmp:
            self._run_cli(
                tmp,
                "oa",
                "template",
                "show",
                "tpl-1",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        self.assertEqual(
            seen_payloads[0],
            {
                "system": "oa",
                "command": "template_detail",
                "args": {"template_id": "tpl-1"},
                "timeout_seconds": 30.0,
            },
        )

    def test_oa_detail_read_maps_url_to_detail_read(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "title": "Seal request",
                    "attachments": [{"name": "seal-plan.pdf"}],
                    "workflow": [{"text": "Opinion: approved"}],
                },
            }
        )
        detail_url = "http://oa.example.test/detail?a=1"

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "detail",
                "read",
                "--url",
                detail_url,
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        self.assertEqual(json.loads(result.stdout)["result"]["title"], "Seal request")
        self.assertEqual(
            seen_payloads[0],
            {
                "system": "oa",
                "command": "detail_read",
                "args": {"url": detail_url},
                "timeout_seconds": 30.0,
            },
        )

    def test_oa_sent_export_csv_prints_items_as_csv(self):
        server, _seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "count": 1,
                    "items": [{"title": "Sent doc", "sender": "Alice", "affair_id": "s1"}],
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "sent",
                "export",
                "--format",
                "csv",
                "--fields",
                "title,affair_id",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        rows = list(csv.DictReader(io.StringIO(result.stdout)))
        self.assertEqual(rows, [{"title": "Sent doc", "affair_id": "s1"}])

    def test_oa_api_save_builds_request_arguments(self):
        server, seen_payloads = self._start_daemon({"ok": True, "result": {"saved_path": "x"}})

        with TemporaryDirectory() as tmp:
            self._run_cli(
                tmp,
                "oa",
                "api",
                "save",
                "pending-page",
                "--method",
                "GET",
                "--url",
                "http://oa.example.test/api",
                "--description",
                "Pending page",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        self.assertEqual(seen_payloads[0]["command"], "api_save")
        self.assertEqual(
            seen_payloads[0]["args"],
            {
                "name": "pending-page",
                "method": "GET",
                "url": "http://oa.example.test/api",
                "description": "Pending page",
            },
        )

    def test_oa_discovered_list_uses_local_store(self):
        with TemporaryDirectory() as tmp:
            api_dir = Path(tmp) / "discovered" / "oa" / "apis"
            api_dir.mkdir(parents=True)
            (api_dir / "template-section.json").write_text(
                json.dumps(
                    {
                        "schema_version": "bscli.discovered_api.v1",
                        "name": "template-section",
                        "system": "oa",
                        "request": {"method": "GET", "url": "http://oa.example.test/api"},
                        "inspection": {"data_shape": "Data.items[]"},
                    }
                ),
                encoding="utf-8",
            )

            result = self._run_cli(tmp, "oa", "discovered", "list")

        apis = json.loads(result.stdout)
        self.assertEqual(apis[0]["name"], "template-section")

    def _start_daemon(self, response):
        seen_payloads = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])).decode("utf-8"))
                seen_payloads.append(body)
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
        return server, seen_payloads

    def _run_cli(self, home: str, *args: str) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd())
        return subprocess.run(
            [sys.executable, "-m", "bscli.cli.main", "--home", home, *args],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
