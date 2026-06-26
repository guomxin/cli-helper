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

from bscli.cli.main import _daemon_client_timeout_seconds


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

    def test_oa_workflow_list_maps_pending_type_to_workflow_list(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "run_id": "run-1",
                "result": {
                    "count": 2,
                    "items": [
                        {"title": "Weekly report", "affair_id": "a1"},
                        {"title": "Travel request", "affair_id": "a2"},
                    ],
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "workflow",
                "list",
                "--type",
                "pending",
                "--keyword",
                "weekly",
                "--limit",
                "1",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "workflow_list")
        self.assertEqual(seen_payloads[0]["args"], {"type": "pending", "keyword": "weekly", "limit": 1})
        self.assertEqual(payload["result"]["count"], 1)
        self.assertEqual(payload["result"]["items"][0]["title"], "Weekly report")

    def test_oa_workflow_search_maps_sent_type_to_workflow_list(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "items": [
                        {"title": "Contract sent", "affair_id": "s1"},
                        {"title": "Weekly sent", "affair_id": "s2"},
                    ],
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "workflow",
                "search",
                "--type",
                "sent",
                "--keyword",
                "contract",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "workflow_list")
        self.assertEqual(seen_payloads[0]["args"], {"type": "sent", "keyword": "contract"})
        self.assertEqual(payload["result"]["count"], 1)
        self.assertEqual(payload["result"]["items"][0]["affair_id"], "s1")

    def test_oa_workflow_detail_reads_detail_page_by_url(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "title": "Weekly report",
                    "text": "abcd",
                },
            }
        )
        detail_url = "http://oa.example.test/detail?a=1"

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "workflow",
                "detail",
                "--url",
                detail_url,
                "--include",
                "title,text",
                "--text-limit",
                "4",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "workflow_detail")
        self.assertEqual(
            seen_payloads[0]["args"],
            {"type": "pending", "url": detail_url, "include": "title,text", "text_limit": 4},
        )
        self.assertEqual(payload["result"], {"title": "Weekly report", "text": "abcd"})

    def test_oa_workflow_opinions_projects_single_detail_workflow(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "title": "Weekly report",
                    "count": 2,
                    "items": [
                        {"text": "Manager approved", "node": "manager"},
                        {"text": "Finance approved", "node": "finance"},
                    ],
                },
            }
        )
        detail_url = "http://oa.example.test/detail?a=1"

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "workflow",
                "opinions",
                "--url",
                detail_url,
                "--limit",
                "1",
                "--fields",
                "text",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "workflow_opinions")
        self.assertEqual(seen_payloads[0]["args"], {"type": "pending", "url": detail_url, "limit": 1})
        self.assertEqual(payload["result"]["count"], 1)
        self.assertEqual(payload["result"]["items"], [{"text": "Manager approved"}])

    def test_oa_workflow_opinions_resolves_affair_id_from_pending_list(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "source_item": {
                        "title": "Weekly report",
                        "affair_id": "a1",
                        "href": "http://oa.example.test/detail/a1",
                    },
                    "count": 1,
                    "items": [{"text": "Manager approved"}],
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "workflow",
                "opinions",
                "--type",
                "pending",
                "--id",
                "a1",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual([entry["command"] for entry in seen_payloads], ["workflow_opinions"])
        self.assertEqual(seen_payloads[0]["args"], {"type": "pending", "id": "a1"})
        self.assertEqual(payload["result"]["source_item"]["affair_id"], "a1")
        self.assertEqual(payload["result"]["items"], [{"text": "Manager approved"}])

    def test_oa_workflow_detail_resolves_affair_id_from_sent_list(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "source_item": {
                        "title": "Sent doc",
                        "affair_id": "s1",
                        "href": "http://oa.example.test/detail/s1",
                    },
                    "detail": {
                        "title": "Sent doc",
                        "text": "abc",
                    },
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "workflow",
                "detail",
                "--type",
                "sent",
                "--id",
                "s1",
                "--include",
                "title,text",
                "--text-limit",
                "3",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual([entry["command"] for entry in seen_payloads], ["workflow_detail"])
        self.assertEqual(
            seen_payloads[0]["args"],
            {"type": "sent", "id": "s1", "include": "title,text", "text_limit": 3},
        )
        self.assertEqual(payload["result"]["source_item"]["affair_id"], "s1")
        self.assertEqual(payload["result"]["detail"], {"title": "Sent doc", "text": "abc"})

    def test_oa_workflow_opinions_batches_pending_detail_workflow(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "source_count": 1,
                    "count": 1,
                    "items": [{"source_title": "Weekly report", "text": "Manager approved"}],
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "workflow",
                "opinions",
                "--type",
                "pending",
                "--keyword",
                "weekly",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual([entry["command"] for entry in seen_payloads], ["workflow_opinions"])
        self.assertEqual(seen_payloads[0]["args"], {"type": "pending", "keyword": "weekly"})
        self.assertEqual(payload["result"]["count"], 1)
        self.assertEqual(payload["result"]["items"][0]["source_title"], "Weekly report")
        self.assertEqual(payload["result"]["items"][0]["text"], "Manager approved")

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

    def test_oa_api_replay_accepts_text_limit_argument(self):
        server, seen_payloads = self._start_daemon({"ok": True, "result": {"status": 200}})

        with TemporaryDirectory() as tmp:
            self._run_cli(
                tmp,
                "oa",
                "api",
                "replay",
                "--method",
                "GET",
                "--url",
                "http://oa.example.test/detail",
                "--text-limit",
                "120000",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        self.assertEqual(seen_payloads[0]["command"], "api_replay")
        self.assertEqual(
            seen_payloads[0]["args"],
            {
                "method": "GET",
                "url": "http://oa.example.test/detail",
                "max_text": 120000,
            },
        )

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

    def test_oa_meeting_reply_dry_run_calls_daemon_with_plain_arguments(self):
        server, seen_payloads = self._start_daemon(
            {"ok": True, "result": {"precheck": {"passed": True}}}
        )

        with TemporaryDirectory() as tmp:
            self._run_cli(
                tmp,
                "oa",
                "meeting",
                "reply",
                "dry-run",
                "--id",
                "affair-1",
                "--attitude",
                "join",
                "--feedback",
                "will attend",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        self.assertEqual(seen_payloads[0]["command"], "meeting_reply_dry_run")
        self.assertEqual(
            seen_payloads[0]["args"],
            {"id": "affair-1", "attitude": "join", "feedback": "will attend"},
        )

    def test_oa_write_capabilities_calls_daemon_with_plain_arguments(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "count": 1,
                    "items": [
                        {
                            "title": "Weekly report",
                            "affair_id": "affair-1",
                            "category": "workflow",
                            "supported_write_actions": [{"name": "workflow.submit"}],
                        }
                    ],
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "write",
                "capabilities",
                "--type",
                "pending",
                "--keyword",
                "weekly",
                "--limit",
                "1",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "write_capabilities")
        self.assertEqual(
            seen_payloads[0]["args"],
            {"type": "pending", "keyword": "weekly", "limit": 1},
        )
        self.assertEqual(payload["result"]["items"][0]["supported_write_actions"][0]["name"], "workflow.submit")

    def test_oa_history_list_calls_daemon_with_kind_and_filters(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "kind": "done",
                    "count": 1,
                    "items": [{"title": "Historical approval", "affair_id": "done-1"}],
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "history",
                "list",
                "--kind",
                "done",
                "--keyword",
                "approval",
                "--limit",
                "2",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "history_list")
        self.assertEqual(seen_payloads[0]["args"], {"kind": "done", "keyword": "approval", "limit": 2})
        self.assertEqual(payload["result"]["items"][0]["affair_id"], "done-1")

    def test_oa_history_profile_and_clusters_call_daemon_profile_command(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "schema_version": "bscli.oa_history_profile.v1",
                    "source_count": 2,
                    "cluster_count": 1,
                    "clusters": [{"title_pattern": "[Seal] Seal Request", "count": 2}],
                },
            }
        )

        with TemporaryDirectory() as tmp:
            profile = self._run_cli(
                tmp,
                "oa",
                "history",
                "profile",
                "--kind",
                "done",
                "--keyword",
                "seal",
                "--limit",
                "5",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )
            clusters = self._run_cli(
                tmp,
                "oa",
                "history",
                "clusters",
                "--kind",
                "done",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        self.assertEqual(json.loads(profile.stdout)["result"]["schema_version"], "bscli.oa_history_profile.v1")
        self.assertEqual(json.loads(clusters.stdout)["result"]["cluster_count"], 1)
        self.assertEqual(seen_payloads[0]["command"], "history_profile")
        self.assertEqual(seen_payloads[0]["args"], {"kind": "done", "keyword": "seal", "limit": 5})
        self.assertEqual(seen_payloads[1]["command"], "history_profile")
        self.assertEqual(seen_payloads[1]["args"], {"kind": "done"})

    def test_oa_template_match_calls_daemon_with_history_arguments(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "schema_version": "bscli.oa_template_match.v1",
                    "clusters": [{"match_status": "matched"}],
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "template",
                "match",
                "--kind",
                "done",
                "--keyword",
                "seal",
                "--limit",
                "5",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "template_match")
        self.assertEqual(seen_payloads[0]["args"], {"kind": "done", "keyword": "seal", "limit": 5})
        self.assertEqual(payload["result"]["schema_version"], "bscli.oa_template_match.v1")

    def test_oa_matter_profile_and_inspect_call_daemon_with_catalog_arguments(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "schema_version": "bscli.oa_matter_profile.v1",
                    "matters": [{"matter_id": "seal-request", "name": "[Seal] Seal Request"}],
                },
            }
        )

        with TemporaryDirectory() as tmp:
            profile = self._run_cli(
                tmp,
                "oa",
                "matter",
                "profile",
                "--kind",
                "all",
                "--keyword",
                "seal",
                "--limit",
                "5",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )
            inspect = self._run_cli(
                tmp,
                "oa",
                "matter",
                "inspect",
                "--id",
                "seal-request",
                "--kind",
                "all",
                "--with-launch",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )
            table = self._run_cli(
                tmp,
                "oa",
                "matter",
                "profile",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
                "--format",
                "table",
                "--fields",
                "matter_id,name",
            )

        self.assertEqual(json.loads(profile.stdout)["result"]["schema_version"], "bscli.oa_matter_profile.v1")
        self.assertEqual(json.loads(inspect.stdout)["result"]["schema_version"], "bscli.oa_matter_profile.v1")
        self.assertIn("seal-request", table.stdout)
        self.assertEqual(seen_payloads[0]["command"], "matter_profile")
        self.assertEqual(seen_payloads[0]["args"], {"kind": "all", "keyword": "seal", "limit": 5})
        self.assertEqual(seen_payloads[1]["command"], "matter_inspect")
        self.assertEqual(seen_payloads[1]["args"], {"id": "seal-request", "kind": "all", "with_launch": True})
        self.assertEqual(seen_payloads[2]["command"], "matter_profile")

    def test_oa_launch_inspect_calls_daemon_with_template_or_url(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "schema_version": "bscli.oa_launch_inspection.v1",
                    "template_id": "tpl-1",
                    "actions": [],
                    "safety": {"execute_allowed": False, "submitted_count": 0},
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "launch",
                "inspect",
                "--template-id",
                "tpl-1",
                "--settle-ms",
                "0",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "launch_inspect")
        self.assertEqual(seen_payloads[0]["args"], {"template_id": "tpl-1", "settle_ms": 0})
        self.assertEqual(payload["result"]["schema_version"], "bscli.oa_launch_inspection.v1")

    def test_oa_launch_dry_run_calls_daemon_with_fields(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "schema_version": "bscli.oa_launch_draft_plan.v1",
                    "mode": "dry-run",
                    "safety": {"will_execute": False, "submitted_count": 0},
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "launch",
                "dry-run",
                "--template-id",
                "tpl-1",
                "--field",
                "subject=Draft subject",
                "--field",
                "remark=Hello",
                "--settle-ms",
                "0",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "launch_dry_run")
        self.assertEqual(
            seen_payloads[0]["args"],
            {"template_id": "tpl-1", "fields": {"subject": "Draft subject", "remark": "Hello"}, "settle_ms": 0},
        )
        self.assertEqual(payload["result"]["schema_version"], "bscli.oa_launch_draft_plan.v1")

    def test_oa_launch_save_draft_requires_confirm_and_calls_daemon(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "requires_confirmation": True,
                "confirmed": True,
                "result": {
                    "draft_saved": True,
                    "submitted_count": 0,
                    "plan": {"schema_version": "bscli.oa_launch_draft_plan.v1"},
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "launch",
                "save-draft",
                "--url",
                "http://oa.example.test/new?templateId=tpl-1",
                "--field",
                "subject=Draft subject",
                "--confirm",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "launch_save_draft")
        self.assertEqual(
            seen_payloads[0]["args"],
            {
                "url": "http://oa.example.test/new?templateId=tpl-1",
                "fields": {"subject": "Draft subject"},
                "confirm": True,
            },
        )
        self.assertTrue(payload["result"]["draft_saved"])

    def test_oa_launch_save_draft_client_timeout_covers_nested_inspect(self):
        self.assertGreaterEqual(_daemon_client_timeout_seconds(60, "launch_save_draft"), 140)

    def test_oa_write_discover_calls_daemon_with_history_arguments(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "schema_version": "bscli.oa_write_discovery.v1",
                    "source": "history",
                    "kind": "done",
                    "actions": [],
                    "items": [],
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "write",
                "discover",
                "--source",
                "history",
                "--kind",
                "done",
                "--keyword",
                "archive",
                "--limit",
                "5",
                "--deep-limit",
                "2",
                "--text-limit",
                "800",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "write_discover")
        self.assertEqual(
            seen_payloads[0]["args"],
            {
                "source": "history",
                "kind": "done",
                "keyword": "archive",
                "limit": 5,
                "deep_limit": 2,
                "text_limit": 800,
            },
        )
        self.assertEqual(payload["result"]["schema_version"], "bscli.oa_write_discovery.v1")

    def test_oa_write_discover_calls_daemon_with_launch_arguments(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "schema_version": "bscli.oa_write_discovery.v1",
                    "source": "launch",
                    "actions": [],
                    "items": [],
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "write",
                "discover",
                "--source",
                "launch",
                "--template-id",
                "tpl-1",
                "--settle-ms",
                "0",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "write_discover")
        self.assertEqual(seen_payloads[0]["args"], {"source": "launch", "template_id": "tpl-1", "settle_ms": 0})
        self.assertEqual(payload["result"]["source"], "launch")

    def test_oa_write_capabilities_preserves_unpromoted_actions(self):
        server, _seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "count": 1,
                    "items": [
                        {
                            "title": "Contract archive",
                            "affair_id": "archive-1",
                            "category": "workflow",
                            "supported_write_actions": [],
                            "unpromoted_write_actions": [
                                {
                                    "name": "workflow.archive",
                                    "action": "Archive",
                                    "dry_run_allowed": True,
                                    "execute_allowed": False,
                                }
                            ],
                        }
                    ],
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "write",
                "capabilities",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["result"]["items"][0]["unpromoted_write_actions"][0]["name"], "workflow.archive")
        self.assertFalse(payload["result"]["items"][0]["unpromoted_write_actions"][0]["execute_allowed"])

    def test_oa_write_endpoints_calls_daemon_with_plain_arguments(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "action": {"code": "Archive"},
                    "endpoint_candidates": [
                        {
                            "url": "http://oa.example.test/seeyon/collaboration/collaboration.do?method=finishWorkItem",
                            "classification": "possible_archive_completion",
                            "safe_to_call": False,
                        }
                    ],
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "write",
                "endpoints",
                "--affair-id",
                "archive-1",
                "--action",
                "Archive",
                "--source-url",
                "http://oa.example.test/detail?affairId=archive-1",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "write_endpoint_candidates")
        self.assertEqual(
            seen_payloads[0]["args"],
            {
                "affair_id": "archive-1",
                "action": "Archive",
                "source_url": "http://oa.example.test/detail?affairId=archive-1",
            },
        )
        self.assertFalse(payload["result"]["endpoint_candidates"][0]["safe_to_call"])

    def test_oa_doctor_calls_daemon_doctor_command(self):
        server, seen_payloads = self._start_daemon(
            {"ok": True, "result": {"daemon": {"ok": True}, "session": {"connected": True}}}
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "doctor",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "doctor")
        self.assertEqual(seen_payloads[0]["args"], {})
        self.assertTrue(payload["result"]["session"]["connected"])

    def test_oa_capabilities_calls_daemon_capability_map_command(self):
        server, seen_payloads = self._start_daemon(
            {"ok": True, "result": {"read": [{"name": "workflow.list"}], "write": {"executable": []}}}
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "capabilities",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "capability_map")
        self.assertEqual(seen_payloads[0]["args"], {})
        self.assertEqual(payload["result"]["read"][0]["name"], "workflow.list")

    def test_oa_workflow_inspect_calls_daemon_with_detail_options(self):
        server, seen_payloads = self._start_daemon(
            {"ok": True, "result": {"summary": {"title": "Weekly report"}}}
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "workflow",
                "inspect",
                "--type",
                "pending",
                "--id",
                "a1",
                "--include",
                "title,text,workflow",
                "--text-limit",
                "80",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "workflow_inspect")
        self.assertEqual(
            seen_payloads[0]["args"],
            {"type": "pending", "id": "a1", "include": "title,text,workflow", "text_limit": 80},
        )
        self.assertEqual(payload["result"]["summary"]["title"], "Weekly report")

    def test_oa_workflow_brief_calls_daemon_without_opening_detail_options(self):
        server, seen_payloads = self._start_daemon(
            {"ok": True, "result": {"count": 1, "items": [{"title": "Weekly report"}]}}
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "workflow",
                "brief",
                "--type",
                "pending",
                "--keyword",
                "weekly",
                "--limit",
                "1",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "workflow_brief")
        self.assertEqual(seen_payloads[0]["args"], {"type": "pending", "keyword": "weekly", "limit": 1})
        self.assertEqual(payload["result"]["items"][0]["title"], "Weekly report")

    def test_oa_inbox_analyze_calls_daemon_list_only_by_default(self):
        server, seen_payloads = self._start_daemon(
            {"ok": True, "result": {"mode": "list_only", "items": [{"title": "Weekly report"}]}}
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "inbox",
                "analyze",
                "--type",
                "pending",
                "--keyword",
                "weekly",
                "--limit",
                "2",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "inbox_analyze")
        self.assertEqual(seen_payloads[0]["args"], {"type": "pending", "keyword": "weekly", "limit": 2})
        self.assertEqual(payload["result"]["mode"], "list_only")

    def test_oa_inbox_analyze_deep_passes_explicit_detail_budget(self):
        server, seen_payloads = self._start_daemon(
            {"ok": True, "result": {"mode": "deep", "deep_count": 1, "items": [{"title": "Weekly report"}]}}
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "inbox",
                "analyze",
                "--deep",
                "--deep-limit",
                "1",
                "--text-limit",
                "120",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "inbox_analyze")
        self.assertEqual(seen_payloads[0]["args"], {"type": "pending", "deep": True, "deep_limit": 1, "text_limit": 120})
        self.assertEqual(payload["result"]["mode"], "deep")

    def test_oa_workflow_evidence_and_timeline_call_daemon_with_id(self):
        server, seen_payloads = self._start_daemon(
            [
                {"ok": True, "result": {"evidence": {"identity": {"affair_id": "a1"}}}},
                {"ok": True, "result": {"count": 1, "items": [{"text": "Approved"}]}},
            ]
        )

        with TemporaryDirectory() as tmp:
            evidence = self._run_cli(
                tmp,
                "oa",
                "workflow",
                "evidence",
                "--id",
                "a1",
                "--text-limit",
                "120",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )
            timeline = self._run_cli(
                tmp,
                "oa",
                "workflow",
                "timeline",
                "--id",
                "a1",
                "--limit",
                "5",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        self.assertEqual(seen_payloads[0]["command"], "workflow_evidence")
        self.assertEqual(seen_payloads[0]["args"], {"type": "pending", "id": "a1", "text_limit": 120})
        self.assertEqual(seen_payloads[1]["command"], "workflow_timeline")
        self.assertEqual(seen_payloads[1]["args"], {"type": "pending", "id": "a1", "limit": 5})
        self.assertEqual(json.loads(evidence.stdout)["result"]["evidence"]["identity"]["affair_id"], "a1")
        self.assertEqual(json.loads(timeline.stdout)["result"]["items"][0]["text"], "Approved")

    def test_oa_meeting_reply_execute_requires_confirm_before_daemon_call(self):
        server, seen_payloads = self._start_daemon({"ok": True})

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "meeting",
                "reply",
                "execute",
                "--id",
                "affair-1",
                "--attitude",
                "join",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads, [])
        self.assertEqual(payload["ok"], False)
        self.assertTrue(payload["requires_confirmation"])

    def test_oa_meeting_reply_execute_with_confirm_calls_daemon(self):
        server, seen_payloads = self._start_daemon({"ok": True, "result": {"submitted": True}})

        with TemporaryDirectory() as tmp:
            self._run_cli(
                tmp,
                "oa",
                "meeting",
                "reply",
                "execute",
                "--id",
                "affair-1",
                "--attitude",
                "join",
                "--feedback",
                "will attend",
                "--confirm",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        self.assertEqual(seen_payloads[0]["command"], "meeting_reply_execute")
        self.assertEqual(
            seen_payloads[0]["args"],
            {
                "id": "affair-1",
                "attitude": "join",
                "feedback": "will attend",
                "confirm": True,
                "verify_wait": 2.0,
            },
        )

    def test_oa_write_dry_run_calls_daemon_for_prechecked_plan(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "schema_version": "bscli.oa_write_plan.v1",
                    "mode": "dry-run",
                    "target": {
                        "affair_id": "affair-1",
                        "source_item": {"affair_id": "affair-1"},
                    },
                    "action": {"code": "ContinueSubmit", "label": "提交", "risk": "high"},
                    "opinion": {"text": "同意", "length": 2},
                    "safety": {"will_execute": False},
                    "precheck": {"status": "passed"},
                    "checks": [{"name": "action_available", "passed": True}],
                    "missing": [],
                },
            }
        )

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
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

            payload = json.loads(result.stdout)
            audit_path = Path(tmp) / "audit" / "oa-write-plans.jsonl"
            audit_exists = audit_path.exists()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["mode"], "dry-run")
        self.assertFalse(payload["result"]["safety"]["will_execute"])
        self.assertEqual(payload["result"]["precheck"]["status"], "passed")
        self.assertFalse(audit_exists)
        self.assertEqual(seen_payloads[0]["command"], "write_dry_run")
        self.assertEqual(
            seen_payloads[0]["args"],
            {
                "affair_id": "affair-1",
                "action": "ContinueSubmit",
                "opinion": "同意",
                "source_url": "",
            },
        )

    def test_oa_write_preflight_calls_daemon_for_execution_decision(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "schema_version": "bscli.oa_write_preflight.v1",
                    "decision": {"status": "ready_for_execute", "execute_allowed": True},
                    "plan": {"opinion": {"length": 8}},
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "write",
                "preflight",
                "--affair-id",
                "affair-1",
                "--action",
                "ContinueSubmit",
                "--opinion",
                "approved",
                "--source-url",
                "http://oa.example.test/detail?affairId=affair-1",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "write_preflight")
        self.assertEqual(
            seen_payloads[0]["args"],
            {
                "type": "pending",
                "affair_id": "affair-1",
                "action": "ContinueSubmit",
                "opinion": "approved",
                "source_url": "http://oa.example.test/detail?affairId=affair-1",
            },
        )
        self.assertEqual(payload["result"]["decision"]["status"], "ready_for_execute")

    def test_oa_write_prepare_calls_daemon_for_task_packet(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "schema_version": "bscli.oa_write_prepare.v1",
                    "target": {"affair_id": "affair-1"},
                    "preflight": {"decision": {"status": "ready_for_execute"}},
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "write",
                "prepare",
                "--affair-id",
                "affair-1",
                "--action",
                "ContinueSubmit",
                "--opinion",
                "approved",
                "--text-limit",
                "200",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads[0]["command"], "write_prepare")
        self.assertEqual(
            seen_payloads[0]["args"],
            {
                "type": "pending",
                "affair_id": "affair-1",
                "action": "ContinueSubmit",
                "opinion": "approved",
                "source_url": "",
                "text_limit": 200,
            },
        )
        self.assertEqual(payload["result"]["schema_version"], "bscli.oa_write_prepare.v1")

    def test_write_prepare_uses_longer_client_timeout_for_composite_reads(self):
        from bscli.cli import main as cli_main

        self.assertEqual(cli_main._daemon_client_timeout_seconds(30, "write_prepare"), 95)
        self.assertEqual(cli_main._daemon_client_timeout_seconds(30, "write_preflight"), 35)

    def test_oa_audit_writes_and_verifications_list_local_sanitized_rows(self):
        with TemporaryDirectory() as tmp:
            audit_dir = Path(tmp) / "audit"
            audit_dir.mkdir(parents=True)
            (audit_dir / "oa-write-plans.jsonl").write_text(
                json.dumps(
                    {
                        "created_at": "2026-06-26T01:00:00+00:00",
                        "target": {"affair_id": "a1"},
                        "action": {"code": "ContinueSubmit"},
                        "opinion": {"length": 8},
                        "precheck": {"status": "passed"},
                        "safety": {"will_execute": False},
                        "request": {"payload_preview": {"opinionText": None}},
                    },
                    ensure_ascii=False,
                )
                + "\n"
                + json.dumps(
                    {
                        "created_at": "2026-06-26T01:05:00+00:00",
                        "target": {"affair_id": "a2"},
                        "action": {"code": "Archive"},
                        "opinion": {"length": 0},
                        "precheck": {"status": "passed"},
                        "safety": {"will_execute": False},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (audit_dir / "oa-write-verifications.jsonl").write_text(
                json.dumps(
                    {
                        "created_at": "2026-06-26T01:01:00+00:00",
                        "target": {"affair_id": "a1"},
                        "action": {"code": "ContinueSubmit"},
                        "verification": {"status": "disappeared", "verified": True},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            writes = self._run_cli(tmp, "oa", "audit", "writes", "list", "--limit", "1")
            verifications = self._run_cli(tmp, "oa", "audit", "verifications", "list")

        writes_payload = json.loads(writes.stdout)
        verifications_payload = json.loads(verifications.stdout)
        self.assertEqual(writes_payload["result"]["items"][0]["affair_id"], "a2")
        self.assertEqual(writes_payload["result"]["items"][0]["precheck_status"], "passed")
        self.assertNotIn("approved", writes.stdout)
        self.assertEqual(verifications_payload["result"]["items"][0]["verification_status"], "disappeared")

    def test_oa_audit_show_and_search_return_sanitized_recent_rows(self):
        with TemporaryDirectory() as tmp:
            audit_dir = Path(tmp) / "audit"
            audit_dir.mkdir(parents=True)
            (audit_dir / "oa-write-plans.jsonl").write_text(
                json.dumps(
                    {
                        "created_at": "2026-06-26T01:00:00+00:00",
                        "target": {"affair_id": "a1"},
                        "action": {"code": "ContinueSubmit"},
                        "opinion": {"text": "secret approval", "length": 15},
                        "precheck": {"status": "passed"},
                        "safety": {"will_execute": False},
                        "request": {"payload_preview": {"opinionText": "secret approval"}},
                    },
                    ensure_ascii=False,
                )
                + "\n"
                + json.dumps(
                    {
                        "created_at": "2026-06-26T01:05:00+00:00",
                        "target": {"affair_id": "a2"},
                        "action": {"code": "Archive"},
                        "opinion": {"length": 0},
                        "precheck": {"status": "passed"},
                        "safety": {"will_execute": False},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            shown = self._run_cli(tmp, "oa", "audit", "writes", "show", "--index", "2")
            searched = self._run_cli(
                tmp,
                "oa",
                "audit",
                "writes",
                "search",
                "--affair-id",
                "a1",
            )

        shown_payload = json.loads(shown.stdout)
        searched_payload = json.loads(searched.stdout)
        self.assertEqual(shown_payload["result"]["index"], 2)
        self.assertEqual(shown_payload["result"]["record"]["target"]["affair_id"], "a1")
        self.assertNotIn("secret approval", shown.stdout)
        self.assertEqual(searched_payload["result"]["count"], 1)
        self.assertEqual(searched_payload["result"]["items"][0]["affair_id"], "a1")
        self.assertNotIn("secret approval", searched.stdout)

    def test_oa_write_smoke_prechecks_no_match_before_confirmed_noop(self):
        server, seen_payloads = self._start_daemon(
            [
                {"ok": True, "result": {"items": [{"title": "Normal task", "affair_id": "a1"}]}},
                {
                    "ok": True,
                    "requires_confirmation": True,
                    "confirmed": True,
                    "result": {"target_count": 0, "submitted_count": 0, "stopped": False, "items": []},
                },
            ]
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "write",
                "smoke",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual([entry["command"] for entry in seen_payloads], ["pending_list_api", "pending_submit"])
        self.assertEqual(seen_payloads[1]["args"]["keyword"], "__BSCLI_NO_MATCH_VALIDATION__")
        self.assertTrue(seen_payloads[1]["args"]["confirm"])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["checks"][0]["status"], "passed")
        self.assertEqual(payload["result"]["submitted_count"], 0)

    def test_oa_write_smoke_refuses_if_keyword_matches_before_confirmed_call(self):
        server, seen_payloads = self._start_daemon(
            {"ok": True, "result": {"items": [{"title": "__BSCLI_NO_MATCH_VALIDATION__", "affair_id": "a1"}]}}
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "write",
                "smoke",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual([entry["command"] for entry in seen_payloads], ["pending_list_api"])
        self.assertFalse(payload["ok"])
        self.assertIn("matched pending items", payload["error"])
        self.assertEqual(payload["result"]["target_count"], 1)

    def test_oa_write_smoke_rejects_custom_keyword_without_override(self):
        server, seen_payloads = self._start_daemon({"ok": True, "result": {"items": []}})

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "write",
                "smoke",
                "--keyword",
                "real",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(seen_payloads, [])
        self.assertFalse(payload["ok"])
        self.assertIn("custom smoke keyword", payload["error"])

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

    def test_oa_write_draft_accepts_output_options(self):
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
                "approved",
                "--format",
                "json",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["mode"], "draft")
        self.assertEqual(payload["action"]["code"], "ContinueSubmit")

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

    def test_oa_pending_submit_executes_each_item_and_verifies_disappearance(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": True,
                "result": {
                    "target_count": 2,
                    "submitted_count": 2,
                    "stopped": False,
                    "items": [
                        {
                            "title": "Weekly 24",
                            "affair_id": "a24",
                            "verification": {"status": "disappeared"},
                        },
                        {
                            "title": "Weekly 23",
                            "affair_id": "a23",
                            "verification": {"status": "disappeared"},
                        },
                    ],
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "pending",
                "submit",
                "--keyword",
                "Weekly",
                "--action",
                "ContinueSubmit",
                "--opinion",
                "approved",
                "--limit",
                "2",
                "--confirm",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
                "--timeout",
                "1",
                "--verify-wait",
                "0",
            )

        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["submitted_count"], 2)
        self.assertEqual(payload["result"]["items"][0]["verification"]["status"], "disappeared")
        self.assertEqual(payload["result"]["items"][1]["verification"]["status"], "disappeared")
        self.assertEqual([entry["command"] for entry in seen_payloads], ["pending_submit"])
        self.assertEqual(seen_payloads[0]["args"]["keyword"], "Weekly")
        self.assertEqual(seen_payloads[0]["args"]["action"], "ContinueSubmit")
        self.assertEqual(seen_payloads[0]["args"]["opinion"], "approved")
        self.assertEqual(seen_payloads[0]["args"]["limit"], 2)
        self.assertEqual(seen_payloads[0]["args"]["verify_wait"], 0.0)
        self.assertTrue(seen_payloads[0]["args"]["confirm"])

    def test_oa_pending_submit_stops_when_item_is_still_pending_after_submit(self):
        server, seen_payloads = self._start_daemon(
            {
                "ok": False,
                "result": {
                    "target_count": 2,
                    "submitted_count": 0,
                    "stopped": True,
                    "items": [
                        {
                            "title": "Weekly 24",
                            "affair_id": "a24",
                            "verification": {"status": "still_pending"},
                        }
                    ],
                },
            }
        )

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "pending",
                "submit",
                "--keyword",
                "Weekly",
                "--action",
                "ContinueSubmit",
                "--opinion",
                "approved",
                "--limit",
                "2",
                "--confirm",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
                "--timeout",
                "1",
                "--verify-wait",
                "0",
            )

        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["result"]["submitted_count"], 0)
        self.assertEqual(payload["result"]["items"][0]["verification"]["status"], "still_pending")
        self.assertEqual(len(payload["result"]["items"]), 1)
        self.assertEqual([entry["command"] for entry in seen_payloads], ["pending_submit"])

    def test_oa_pending_submit_requires_confirm_before_daemon_calls(self):
        server, seen_payloads = self._start_daemon({"ok": True, "result": {"items": []}})

        with TemporaryDirectory() as tmp:
            result = self._run_cli(
                tmp,
                "oa",
                "pending",
                "submit",
                "--keyword",
                "Weekly",
                "--action",
                "ContinueSubmit",
                "--opinion",
                "approved",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            )

        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["requires_confirmation"])
        self.assertEqual(seen_payloads, [])

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
