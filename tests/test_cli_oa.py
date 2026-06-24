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

    def test_oa_pending_details_reads_list_then_each_detail_with_cropping(self):
        server, seen_payloads = self._start_daemon(
            [
                {
                    "ok": True,
                    "result": {
                        "items": [
                            {"title": "Budget approval", "href": "http://oa.example.test/detail/a1"},
                            {"title": "Travel request", "href": "http://oa.example.test/detail/a2"},
                        ]
                    },
                },
                {
                    "ok": True,
                    "result": {
                        "title": "Budget approval",
                        "text": "abcdefghij",
                        "fields": [{"name": "Applicant", "value": "Alice"}],
                        "attachments": [{"name": "budget.pdf"}],
                        "workflow": [{"text": "Opinion: approved"}],
                    },
                },
                {
                    "ok": True,
                    "result": {
                        "title": "Travel request",
                        "text": "klmnopqrst",
                        "fields": [{"name": "Applicant", "value": "Bob"}],
                        "attachments": [],
                        "workflow": [],
                    },
                },
            ]
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "pending",
                "details",
                "--limit",
                "2",
                "--include",
                "text,attachments",
                "--text-limit",
                "4",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual([call["command"] for call in seen_payloads], ["pending_list_api", "detail_read", "detail_read"])
        self.assertEqual(payload["result"]["count"], 2)
        self.assertEqual(payload["result"]["items"][0]["source_item"]["title"], "Budget approval")
        self.assertEqual(payload["result"]["items"][0]["detail"], {"text": "abcd", "attachments": [{"name": "budget.pdf"}]})
        self.assertEqual(payload["result"]["items"][1]["detail"], {"text": "klmn", "attachments": []})

    def test_oa_pending_attachments_indexes_detail_attachments(self):
        server, _seen_payloads = self._start_daemon(
            [
                {
                    "ok": True,
                    "result": {
                        "items": [
                            {"title": "Budget approval", "href": "http://oa.example.test/detail/a1"},
                            {"title": "Travel request", "href": "http://oa.example.test/detail/a2"},
                        ]
                    },
                },
                {"ok": True, "result": {"attachments": [{"name": "budget.pdf", "href": "http://oa.example.test/f1"}]}},
                {"ok": True, "result": {"attachments": [{"name": "ticket.png", "href": "http://oa.example.test/f2"}]}},
            ]
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "pending",
                "attachments",
                "--limit",
                "2",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["result"]["count"], 2)
        self.assertEqual(
            payload["result"]["items"][0],
            {
                "source_title": "Budget approval",
                "source_href": "http://oa.example.test/detail/a1",
                "name": "budget.pdf",
                "href": "http://oa.example.test/f1",
            },
        )

    def test_oa_sent_workflow_indexes_detail_workflow_as_csv(self):
        server, _seen_payloads = self._start_daemon(
            [
                {
                    "ok": True,
                    "result": {
                        "items": [
                            {"title": "Sent doc", "href": "http://oa.example.test/detail/s1"},
                        ]
                    },
                },
                {"ok": True, "result": {"workflow": [{"text": "Manager approved"}]}},
            ]
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "sent",
                "workflow",
                "--format",
                "csv",
                "--fields",
                "source_title,text",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        self.assertEqual(list(csv.DictReader(io.StringIO(result.stdout))), [{"source_title": "Sent doc", "text": "Manager approved"}])

    def test_oa_detail_attachments_projects_single_detail_attachments(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "title": "Seal request",
                    "attachments": [{"name": "seal-plan.pdf", "href": "http://oa.example.test/f1"}],
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
                "attachments",
                "--url",
                detail_url,
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "detail_read")
        self.assertEqual(payload["result"]["count"], 1)
        self.assertEqual(payload["result"]["items"][0]["name"], "seal-plan.pdf")

    def test_oa_detail_actions_projects_single_detail_actions(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "title": "Seal request",
                    "actions": [
                        {
                            "code": "ContinueSubmit",
                            "label": "提交",
                            "risk": "high",
                            "requires_confirmation": True,
                        }
                    ],
                },
            }
        )
        detail_url = "http://oa.example.test/detail?a=1"

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "detail",
                "actions",
                "--url",
                detail_url,
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "detail_read")
        self.assertEqual(payload["result"]["count"], 1)
        self.assertEqual(payload["result"]["items"][0]["code"], "ContinueSubmit")

    def test_oa_pending_actions_indexes_detail_actions(self):
        server, _seen_payloads = self._start_daemon(
            [
                {
                    "ok": True,
                    "result": {
                        "items": [
                            {"title": "Budget approval", "href": "http://oa.example.test/detail/a1"},
                        ]
                    },
                },
                {
                    "ok": True,
                    "result": {
                        "actions": [
                            {
                                "code": "ContinueSubmit",
                                "label": "提交",
                                "risk": "high",
                            }
                        ]
                    },
                },
            ]
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "pending",
                "actions",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["result"]["count"], 1)
        self.assertEqual(payload["result"]["items"][0]["source_title"], "Budget approval")
        self.assertEqual(payload["result"]["items"][0]["code"], "ContinueSubmit")

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

    def test_oa_write_draft_builds_non_executing_plan_without_daemon(self):
        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "write",
                "draft",
                "--affair-id",
                "affair-1",
                "--action",
                "ContinueSubmit",
                "--opinion",
                "同意",
            )

            payload = json.loads(result.stdout)
            audit_path = Path(tmp) / "audit" / "oa-write-plans.jsonl"
            audit_exists = audit_path.exists()

        self.assertEqual(payload["schema_version"], "bscli.oa_write_plan.v1")
        self.assertEqual(payload["mode"], "draft")
        self.assertFalse(payload["safety"]["will_execute"])
        self.assertTrue(payload["safety"]["requires_confirmation"])
        self.assertEqual(payload["target"]["affair_id"], "affair-1")
        self.assertEqual(payload["action"]["code"], "ContinueSubmit")
        self.assertEqual(payload["opinion"]["text"], "同意")
        self.assertEqual(payload["request"]["status"], "not_built")
        self.assertFalse(audit_exists)

    def test_oa_write_dry_run_records_sanitized_audit_plan(self):
        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "write",
                "dry-run",
                "--affair-id",
                "affair-1",
                "--action",
                "ContinueSubmit",
                "--opinion",
                "同意",
            )

            payload = json.loads(result.stdout)
            audit_path = Path(tmp) / "audit" / "oa-write-plans.jsonl"
            audit_rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(payload["mode"], "dry-run")
        self.assertFalse(payload["safety"]["will_execute"])
        self.assertEqual(payload["request"]["status"], "not_sent")
        self.assertEqual(len(audit_rows), 1)
        self.assertEqual(audit_rows[0]["target"]["affair_id"], "affair-1")
        self.assertEqual(audit_rows[0]["action"]["code"], "ContinueSubmit")
        self.assertEqual(audit_rows[0]["opinion"]["length"], 2)
        self.assertNotIn("同意", json.dumps(audit_rows[0], ensure_ascii=False))

    def test_oa_write_execute_without_confirm_stays_local_blocked(self):
        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "write",
                "execute",
                "--affair-id",
                "affair-1",
                "--action",
                "ContinueSubmit",
                "--opinion",
                "同意",
            )

        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "oa write execute requires --confirm")
        self.assertFalse(payload["plan"]["safety"]["will_execute"])

    def test_oa_write_execute_with_confirm_calls_daemon(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "run_id": "run-1",
                "task_id": "task-1",
                "result": {"submitted": True, "affair_id": "affair-1"},
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "write",
                "execute",
                "--affair-id",
                "affair-1",
                "--action",
                "ContinueSubmit",
                "--opinion",
                "approved",
                "--source-url",
                "http://oa.example.test/detail?affairId=affair-1",
                "--confirm",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(seen_payloads[0]["command"], "write_execute")
        self.assertEqual(seen_payloads[0]["args"]["affair_id"], "affair-1")
        self.assertEqual(seen_payloads[0]["args"]["action"], "ContinueSubmit")
        self.assertEqual(seen_payloads[0]["args"]["opinion"], "approved")
        self.assertEqual(seen_payloads[0]["args"]["source_url"], "http://oa.example.test/detail?affairId=affair-1")
        self.assertTrue(seen_payloads[0]["args"]["confirm"])

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
        responses = response if isinstance(response, list) else [response]

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])).decode("utf-8"))
                seen_payloads.append(body)
                index = min(len(seen_payloads) - 1, len(responses) - 1)
                payload = json.dumps(responses[index]).encode("utf-8")
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
