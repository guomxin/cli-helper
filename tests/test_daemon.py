import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bscli.core.config import ConfigStore
from bscli.core.config import SystemProfile
from bscli.daemon.app import DaemonState


class DaemonTests(unittest.TestCase):
    def test_daemon_health_and_extension_task_flow(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))

            health = state.handle("GET", "/health")
            self.assertEqual(health.status, 200)
            self.assertEqual(health.body, {"ok": True})

            registered = state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )
            self.assertEqual(registered.body, {"ok": True})

            clients = state.handle("GET", "/extension/clients")
            self.assertEqual(clients.status, 200)
            self.assertEqual(clients.body["clients"][0]["client_id"], "chrome-1")
            self.assertEqual(clients.body["clients"][0]["title"], "OA")

            created = state.handle(
                "POST",
                "/explore/dom-snapshot",
                body={"system": "oa", "selector": "body"},
            )
            task_id = created.body["task_id"]

            tasks = state.handle(
                "GET",
                "/extension/tasks",
                query={"client_id": "chrome-1"},
            )
            self.assertEqual(tasks.body["tasks"][0]["id"], task_id)

            result = state.handle(
                "POST",
                "/extension/results",
                body={
                    "client_id": "chrome-1",
                    "task_id": task_id,
                    "ok": True,
                    "result": {"title": "OA", "text": "首页"},
                },
            )
            self.assertEqual(result.body, {"ok": True})

            fetched = state.handle("GET", f"/extension/results/{task_id}")
            self.assertEqual(fetched.body["result"], {"title": "OA", "text": "首页"})

    def test_daemon_returns_json_errors(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))

            response = state.handle("GET", "/missing")

            self.assertEqual(response.status, 404)
            self.assertEqual(json.loads(json.dumps(response.body))["error"], "not found")

    def test_run_session_status_does_not_require_extension_client(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "session_status",
                    "args": {},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["connected"], False)
            self.assertEqual(response.body["result"]["client_count"], 0)
            self.assertIn("open the OA tab", response.body["result"]["suggestions"][0])

    def test_run_session_status_reports_matching_oa_client(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "session_status",
                    "args": {},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["connected"], True)
            self.assertEqual(response.body["result"]["client_count"], 1)
            self.assertEqual(response.body["result"]["clients"][0]["client_id"], "chrome-1")
            self.assertEqual(response.body["result"]["clients"][0]["matches_system"], True)

    def test_run_command_routes_task_to_matching_oa_client(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "wrong-tab",
                    "tab_id": 6,
                    "url": "http://example.test/",
                    "title": "Other",
                },
            )
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "oa-tab",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )

            def extension_worker():
                wrong_tasks = []
                oa_tasks = []
                for _ in range(20):
                    wrong_tasks = state.handle(
                        "GET",
                        "/extension/tasks",
                        query={"client_id": "wrong-tab"},
                    ).body["tasks"]
                    oa_tasks = state.handle(
                        "GET",
                        "/extension/tasks",
                        query={"client_id": "oa-tab"},
                    ).body["tasks"]
                    if oa_tasks:
                        break
                    time.sleep(0.01)
                self.assertEqual(wrong_tasks, [])
                self.assertEqual(oa_tasks[0]["target_client_id"], "oa-tab")
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "oa-tab",
                        "task_id": oa_tasks[0]["id"],
                        "ok": True,
                        "result": {
                            "url": "http://10.10.50.110/seeyon/main.do?method=main",
                            "title": "Seeyon OA",
                            "text": "home",
                        },
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "current_page_snapshot",
                    "args": {},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["title"], "Seeyon OA")

    def test_run_command_uses_saved_system_profile_for_target_matching(self):
        with TemporaryDirectory() as tmp:
            store = ConfigStore(Path(tmp))
            store.save_system(
                SystemProfile(
                    id="oa",
                    name="Custom OA",
                    base_url="http://oa.example.test/app/main",
                    allowed_origins=["http://oa.example.test"],
                )
            )
            state = DaemonState(store)
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "internal-oa",
                    "tab_id": 7,
                    "url": "http://oa.example.test/app/main",
                    "title": "Custom OA",
                },
            )

            def extension_worker():
                tasks = []
                for _ in range(20):
                    tasks = state.handle(
                        "GET",
                        "/extension/tasks",
                        query={"client_id": "internal-oa"},
                    ).body["tasks"]
                    if tasks:
                        break
                    time.sleep(0.01)
                self.assertEqual(tasks[0]["target_client_id"], "internal-oa")
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "internal-oa",
                        "task_id": tasks[0]["id"],
                        "ok": True,
                        "result": {"title": "Custom OA"},
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "current_page_snapshot",
                    "args": {},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["title"], "Custom OA")

    def test_explore_dom_snapshot_routes_to_configured_system_client(self):
        with TemporaryDirectory() as tmp:
            store = ConfigStore(Path(tmp))
            store.save_system(
                SystemProfile(
                    id="crm",
                    name="CRM",
                    base_url="http://crm.example.test/home",
                    allowed_origins=["http://crm.example.test"],
                )
            )
            state = DaemonState(store)
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "wrong-tab",
                    "tab_id": 6,
                    "url": "http://example.test/",
                    "title": "Other",
                },
            )
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "crm-tab",
                    "tab_id": 7,
                    "url": "http://crm.example.test/dashboard",
                    "title": "CRM",
                },
            )

            created = state.handle(
                "POST",
                "/explore/dom-snapshot",
                body={"system": "crm", "selector": "body"},
            )

            self.assertEqual(created.status, 200)
            self.assertEqual(
                state.handle("GET", "/extension/tasks", query={"client_id": "wrong-tab"}).body[
                    "tasks"
                ],
                [],
            )
            tasks = state.handle("GET", "/extension/tasks", query={"client_id": "crm-tab"}).body[
                "tasks"
            ]
            self.assertEqual(tasks[0]["id"], created.body["task_id"])
            self.assertEqual(tasks[0]["target_client_id"], "crm-tab")

    def test_run_current_page_snapshot_waits_for_extension_result(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )

            def extension_worker():
                time.sleep(0.05)
                tasks = state.handle(
                    "GET",
                    "/extension/tasks",
                    query={"client_id": "chrome-1"},
                ).body["tasks"]
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": tasks[0]["id"],
                        "ok": True,
                        "result": {
                            "url": "http://10.10.50.110/seeyon/main.do?method=main",
                            "title": "Seeyon OA",
                            "text": "首页 待办事项",
                        },
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "current_page_snapshot",
                    "args": {},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["ok"], True)
            self.assertEqual(response.body["result"]["title"], "Seeyon OA")

    def test_run_current_page_snapshot_reports_timeout(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "current_page_snapshot",
                    "args": {},
                    "timeout_seconds": 0.01,
                },
            )

            self.assertEqual(response.status, 504)
            self.assertEqual(response.body["ok"], False)
            self.assertIn("timed out", response.body["error"])

    def test_run_command_reports_no_extension_client(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "page_inventory",
                    "args": {},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 409)
            self.assertEqual(response.body["ok"], False)
            self.assertIn("no Chrome extension client", response.body["error"])

    def test_run_page_inventory_uses_inventory_task(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )

            def extension_worker():
                time.sleep(0.05)
                tasks = state.handle(
                    "GET",
                    "/extension/tasks",
                    query={"client_id": "chrome-1"},
                ).body["tasks"]
                self.assertEqual(tasks[0]["kind"], "page_inventory")
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": tasks[0]["id"],
                        "ok": True,
                        "result": {
                            "url": "http://10.10.50.110/seeyon/main.do?method=main",
                            "title": "Seeyon OA",
                            "text": "首页 待办事项",
                            "buttons": [{"text": "查询"}],
                            "links": [{"text": "待办", "href": "/seeyon/pending"}],
                            "forms": [{"action": "/seeyon/search", "method": "post"}],
                            "resources": [{"name": "/seeyon/api/list", "initiatorType": "fetch"}],
                        },
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "page_inventory",
                    "args": {},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["ok"], True)
            self.assertEqual(response.body["result"]["buttons"][0]["text"], "查询")

    def test_run_network_probe_commands_use_network_task_kinds(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )

            def extension_worker(expected_kind, result):
                tasks = []
                for _ in range(20):
                    tasks = state.handle(
                        "GET",
                        "/extension/tasks",
                        query={"client_id": "chrome-1"},
                    ).body["tasks"]
                    if tasks:
                        break
                    time.sleep(0.01)
                self.assertEqual(tasks[0]["kind"], expected_kind)
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": tasks[0]["id"],
                        "ok": True,
                        "result": result,
                    },
                )

            worker = threading.Thread(
                target=extension_worker,
                args=("network_probe_install", {"installed": True}),
            )
            worker.start()
            install = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "network_probe_install",
                    "args": {},
                    "timeout_seconds": 1,
                },
            )
            worker.join()
            self.assertEqual(install.status, 200)
            self.assertEqual(install.body["result"], {"installed": True})

            worker = threading.Thread(
                target=extension_worker,
                args=(
                    "network_log_snapshot",
                    {
                        "records": [
                            {
                                "kind": "fetch",
                                "method": "POST",
                                "url": "http://10.10.50.110/seeyon/rest/foo",
                            }
                        ]
                    },
                ),
            )
            worker.start()
            snapshot = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "network_log_snapshot",
                    "args": {},
                    "timeout_seconds": 1,
                },
            )
            worker.join()
            self.assertEqual(snapshot.status, 200)
            self.assertEqual(snapshot.body["result"]["records"][0]["kind"], "fetch")

    def test_run_network_api_candidates_analyzes_network_log_snapshot(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )

            def extension_worker():
                tasks = []
                for _ in range(20):
                    tasks = state.handle(
                        "GET",
                        "/extension/tasks",
                        query={"client_id": "chrome-1"},
                    ).body["tasks"]
                    if tasks:
                        break
                    time.sleep(0.01)
                self.assertEqual(tasks[0]["kind"], "network_log_snapshot")
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": tasks[0]["id"],
                        "ok": True,
                        "result": {
                            "records": [
                                {
                                    "kind": "fetch",
                                    "method": "POST",
                                    "url": "http://10.10.50.110/seeyon/rest/pending/list",
                                    "status": 200,
                                    "requestBody": '{"page":1}',
                                }
                            ],
                            "resources": [],
                        },
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "network_api_candidates",
                    "args": {},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["candidates"][0]["path"], "/seeyon/rest/pending/list")

    def test_run_api_replay_uses_page_fetch_task(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )

            def extension_worker():
                tasks = []
                for _ in range(20):
                    tasks = state.handle(
                        "GET",
                        "/extension/tasks",
                        query={"client_id": "chrome-1"},
                    ).body["tasks"]
                    if tasks:
                        break
                    time.sleep(0.01)
                self.assertEqual(tasks[0]["kind"], "page_fetch")
                self.assertEqual(
                    tasks[0]["payload"],
                    {
                        "method": "POST",
                        "url": "/seeyon/rest/pending/list",
                        "headers": {"content-type": "application/json"},
                        "body": '{"page":1}',
                    },
                )
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": tasks[0]["id"],
                        "ok": True,
                        "result": {
                            "status": 200,
                            "ok": True,
                            "contentType": "application/json",
                            "json": {"data": [{"title": "待办"}]},
                        },
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "api_replay",
                    "args": {
                        "method": "POST",
                        "url": "/seeyon/rest/pending/list",
                        "headers": {"content-type": "application/json"},
                        "body": '{"page":1}',
                    },
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["status"], 200)
            self.assertEqual(response.body["result"]["json"]["data"][0]["title"], "待办")

    def test_run_pending_list_parses_html_snapshot_in_daemon(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )

            def extension_worker():
                tasks = []
                for _ in range(20):
                    tasks = state.handle(
                        "GET",
                        "/extension/tasks",
                        query={"client_id": "chrome-1"},
                    ).body["tasks"]
                    if tasks:
                        break
                    time.sleep(0.01)
                self.assertEqual(tasks[0]["kind"], "html_snapshot")
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": tasks[0]["id"],
                        "ok": True,
                        "result": {
                            "url": "http://10.10.50.110/seeyon/main.do?method=main",
                            "html": """
                            <div id="section_556815601453123423">
                              <table>
                                <tr>
                                  <td><a class="cellContentText" title="Weekly report"
                                    onclick="checkAndOpenLink('/collaboration/collaboration.do?method=summary&amp;affairId=abc-123')">
                                    <span class="titleText">Weekly report</span></a></td>
                                  <td>Alice</td>
                                  <td>Today</td>
                                  <td>Collaboration</td>
                                </tr>
                              </table>
                            </div>
                            """,
                        },
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "pending_list",
                    "args": {},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["count"], 1)
            self.assertEqual(response.body["result"]["items"][0]["sender"], "Alice")
            self.assertEqual(response.body["result"]["items"][0]["affair_id"], "abc-123")

    def test_run_pending_list_api_replays_discovered_section_api(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )
            pending_url = (
                "http://10.10.50.110/seeyon/ajax.do?method=ajaxAction"
                "&managerName=sectionManager&managerMethod=doProjection"
                "&arguments=%7B%22sectionBeanId%22%3A%22pendingSection%22%7D"
            )

            def extension_worker():
                seen_kinds = []
                for _ in range(2):
                    tasks = []
                    for _attempt in range(20):
                        tasks = state.handle(
                            "GET",
                            "/extension/tasks",
                            query={"client_id": "chrome-1"},
                        ).body["tasks"]
                        if tasks:
                            break
                        time.sleep(0.01)
                    self.assertEqual(len(tasks), 1)
                    task = tasks[0]
                    seen_kinds.append(task["kind"])
                    if task["kind"] == "network_log_snapshot":
                        state.handle(
                            "POST",
                            "/extension/results",
                            body={
                                "client_id": "chrome-1",
                                "task_id": task["id"],
                                "ok": True,
                                "result": {
                                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                                    "resources": [
                                        {
                                            "initiatorType": "xmlhttprequest",
                                            "name": pending_url,
                                        }
                                    ],
                                    "records": [],
                                },
                            },
                        )
                    elif task["kind"] == "page_fetch":
                        self.assertEqual(task["payload"]["url"], pending_url)
                        state.handle(
                            "POST",
                            "/extension/results",
                            body={
                                "client_id": "chrome-1",
                                "task_id": task["id"],
                                "ok": True,
                                "result": {
                                    "status": 200,
                                    "ok": True,
                                    "url": pending_url,
                                    "json": {
                                        "Name": "全部待办",
                                        "Data": {
                                            "dataCount": 1,
                                            "pageNo": 1,
                                            "rows": [
                                                {
                                                    "cells": [
                                                        {
                                                            "cellContentHTML": "Weekly report",
                                                            "id": "abc-123",
                                                            "linkURL": "/collaboration/collaboration.do?method=summary&affairId=abc-123&showTab=true",
                                                            "className": "ReadDifferFromNotRead",
                                                        },
                                                        {"cellContentHTML": "Alice"},
                                                        {"cellContentHTML": "Today"},
                                                        {"cellContentHTML": "Collaboration"},
                                                    ]
                                                }
                                            ],
                                        },
                                    },
                                },
                            },
                        )
                self.assertEqual(seen_kinds, ["network_log_snapshot", "page_fetch"])

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "pending_list_api",
                    "args": {},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["source"], "section_api")
            self.assertEqual(response.body["result"]["items"][0]["affair_id"], "abc-123")

    def test_run_sent_list_api_replays_discovered_section_api(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )
            sent_url = (
                "http://10.10.50.110/seeyon/ajax.do?method=ajaxAction"
                "&managerName=sectionManager&managerMethod=doProjection"
                "&arguments=%7B%22sectionBeanId%22%3A%22sentSection%22%7D"
            )

            def extension_worker():
                seen_kinds = []
                for _ in range(2):
                    tasks = []
                    for _attempt in range(20):
                        tasks = state.handle(
                            "GET",
                            "/extension/tasks",
                            query={"client_id": "chrome-1"},
                        ).body["tasks"]
                        if tasks:
                            break
                        time.sleep(0.01)
                    self.assertEqual(len(tasks), 1)
                    task = tasks[0]
                    seen_kinds.append(task["kind"])
                    if task["kind"] == "network_log_snapshot":
                        state.handle(
                            "POST",
                            "/extension/results",
                            body={
                                "client_id": "chrome-1",
                                "task_id": task["id"],
                                "ok": True,
                                "result": {
                                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                                    "resources": [
                                        {
                                            "initiatorType": "xmlhttprequest",
                                            "name": sent_url,
                                        }
                                    ],
                                    "records": [],
                                },
                            },
                        )
                    elif task["kind"] == "page_fetch":
                        self.assertEqual(task["payload"]["url"], sent_url)
                        state.handle(
                            "POST",
                            "/extension/results",
                            body={
                                "client_id": "chrome-1",
                                "task_id": task["id"],
                                "ok": True,
                                "result": {
                                    "status": 200,
                                    "ok": True,
                                    "url": sent_url,
                                    "json": {
                                        "Name": "已发事项",
                                        "Data": {
                                            "dataCount": 1,
                                            "pageNo": 1,
                                            "rows": [
                                                {
                                                    "cells": [
                                                        {
                                                            "cellContentHTML": "Seal request",
                                                            "id": "sent-123",
                                                            "linkURL": "/collaboration/collaboration.do?method=summary&openFrom=listSent&affairId=sent-123&showTab=true",
                                                        },
                                                        {"cellContentHTML": "已结束"},
                                                        {"cellContentHTML": "2026-06-15"},
                                                        {"cellContentHTML": "协同"},
                                                    ]
                                                }
                                            ],
                                        },
                                    },
                                },
                            },
                        )
                self.assertEqual(seen_kinds, ["network_log_snapshot", "page_fetch"])

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "sent_list_api",
                    "args": {},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["source"], "section_api")
            self.assertEqual(response.body["result"]["items"][0]["affair_id"], "sent-123")
            self.assertEqual(response.body["result"]["items"][0]["status"], "已结束")

    def test_run_template_list_api_replays_discovered_section_api(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )
            template_url = (
                "http://10.10.50.110/seeyon/ajax.do?method=ajaxAction"
                "&managerName=sectionManager&managerMethod=doProjection"
                "&arguments=%7B%22sectionBeanId%22%3A%22templeteSection%22%7D"
            )

            def extension_worker():
                seen_kinds = []
                for _ in range(2):
                    tasks = []
                    for _attempt in range(20):
                        tasks = state.handle(
                            "GET",
                            "/extension/tasks",
                            query={"client_id": "chrome-1"},
                        ).body["tasks"]
                        if tasks:
                            break
                        time.sleep(0.01)
                    self.assertEqual(len(tasks), 1)
                    task = tasks[0]
                    seen_kinds.append(task["kind"])
                    if task["kind"] == "network_log_snapshot":
                        state.handle(
                            "POST",
                            "/extension/results",
                            body={
                                "client_id": "chrome-1",
                                "task_id": task["id"],
                                "ok": True,
                                "result": {
                                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                                    "resources": [
                                        {
                                            "initiatorType": "xmlhttprequest",
                                            "name": template_url,
                                        }
                                    ],
                                    "records": [],
                                },
                            },
                        )
                    elif task["kind"] == "page_fetch":
                        self.assertEqual(task["payload"]["url"], template_url)
                        state.handle(
                            "POST",
                            "/extension/results",
                            body={
                                "client_id": "chrome-1",
                                "task_id": task["id"],
                                "ok": True,
                                "result": {
                                    "status": 200,
                                    "ok": True,
                                    "url": template_url,
                                    "json": {
                                        "Name": "我的模板",
                                        "Data": {
                                            "dataCount": 1,
                                            "pageNo": 1,
                                            "rows": [
                                                {
                                                    "cells": [
                                                        {
                                                            "cellContentHTML": "Seal request",
                                                            "id": "template-row-1",
                                                            "linkURL": "/collaboration/collaboration.do?method=newColl&from=templateNewColl&templateId=-6511139737225050501&showTab=true",
                                                        }
                                                    ]
                                                }
                                            ],
                                        },
                                    },
                                },
                            },
                        )
                self.assertEqual(seen_kinds, ["network_log_snapshot", "page_fetch"])

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "template_list_api",
                    "args": {},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["source"], "section_api")
            self.assertEqual(response.body["result"]["items"][0]["template_id"], "-6511139737225050501")

    def test_run_pending_detail_filters_parsed_html_snapshot(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )

            def extension_worker():
                tasks = []
                for _ in range(20):
                    tasks = state.handle(
                        "GET",
                        "/extension/tasks",
                        query={"client_id": "chrome-1"},
                    ).body["tasks"]
                    if tasks:
                        break
                    time.sleep(0.01)
                self.assertEqual(tasks[0]["kind"], "html_snapshot")
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": tasks[0]["id"],
                        "ok": True,
                        "result": {
                            "url": "http://10.10.50.110/seeyon/main.do?method=main",
                            "html": """
                            <div id="section_556815601453123423">
                              <table>
                                <tr>
                                  <td><a class="cellContentText" title="Weekly report"
                                    onclick="checkAndOpenLink('/collaboration/collaboration.do?method=summary&amp;affairId=abc-123')">
                                    <span class="titleText">Weekly report</span></a></td>
                                  <td>Alice</td>
                                  <td>Today</td>
                                  <td>Collaboration</td>
                                </tr>
                              </table>
                            </div>
                            """,
                        },
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "pending_detail",
                    "args": {"affair_id": "abc-123"},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["found"], True)
            self.assertEqual(response.body["result"]["item"]["affair_id"], "abc-123")

    def test_run_template_list_parses_html_snapshot_in_daemon(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )

            def extension_worker():
                tasks = []
                for _ in range(20):
                    tasks = state.handle(
                        "GET",
                        "/extension/tasks",
                        query={"client_id": "chrome-1"},
                    ).body["tasks"]
                    if tasks:
                        break
                    time.sleep(0.01)
                self.assertEqual(tasks[0]["kind"], "html_snapshot")
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": tasks[0]["id"],
                        "ok": True,
                        "result": {
                            "url": "http://10.10.50.110/seeyon/main.do?method=main",
                            "html": """
                            <div id="section_-6503951670357636432">
                              <table class="chessboardtable" title="Seal request">
                                <tr><td onclick="javascript:_openDataLink({'url':'/collaboration/collaboration.do?method=newColl&amp;templateId=-6511139737225050501'},event)">
                                  <a>Seal request</a>
                                </td></tr>
                              </table>
                            </div>
                            """,
                        },
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "template_list",
                    "args": {},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["count"], 1)
            self.assertEqual(response.body["result"]["items"][0]["template_id"], "-6511139737225050501")

    def test_run_template_detail_filters_parsed_html_snapshot(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )

            def extension_worker():
                tasks = []
                for _ in range(20):
                    tasks = state.handle(
                        "GET",
                        "/extension/tasks",
                        query={"client_id": "chrome-1"},
                    ).body["tasks"]
                    if tasks:
                        break
                    time.sleep(0.01)
                self.assertEqual(tasks[0]["kind"], "html_snapshot")
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": tasks[0]["id"],
                        "ok": True,
                        "result": {
                            "url": "http://10.10.50.110/seeyon/main.do?method=main",
                            "html": """
                            <div id="section_-6503951670357636432">
                              <table class="chessboardtable" title="Seal request">
                                <tr><td onclick="javascript:_openDataLink({'url':'/collaboration/collaboration.do?method=newColl&amp;templateId=-6511139737225050501'},event)">
                                  <a>Seal request</a>
                                </td></tr>
                              </table>
                              <table class="chessboardtable" title="Purchase approval">
                                <tr><td onclick="javascript:_openDataLink({'url':'/collaboration/collaboration.do?method=newColl&amp;templateId=3492618929488609812'},event)">
                                  <a>Purchase approval</a>
                                </td></tr>
                              </table>
                            </div>
                            """,
                        },
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "template_detail",
                    "args": {"template_id": "3492618929488609812"},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertTrue(response.body["result"]["found"])
            self.assertEqual(response.body["result"]["item"]["title"], "Purchase approval")
            self.assertEqual(response.body["result"]["item"]["template_id"], "3492618929488609812")

    def test_run_api_inspect_replays_api_and_returns_shape_summary(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )
            api_url = "http://10.10.50.110/seeyon/ajax.do?method=ajaxAction"

            def extension_worker():
                tasks = []
                for _ in range(20):
                    tasks = state.handle(
                        "GET",
                        "/extension/tasks",
                        query={"client_id": "chrome-1"},
                    ).body["tasks"]
                    if tasks:
                        break
                    time.sleep(0.01)
                self.assertEqual(tasks[0]["kind"], "page_fetch")
                self.assertEqual(tasks[0]["payload"]["url"], api_url)
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": tasks[0]["id"],
                        "ok": True,
                        "result": {
                            "status": 200,
                            "ok": True,
                            "url": api_url,
                            "contentType": "application/json",
                            "json": {
                                "Name": "My templates",
                                "Data": {
                                    "dataNum": 1,
                                    "items": [
                                        {
                                            "title": "Seal request",
                                            "link": "/collaboration.do?templateId=tpl-1",
                                            "openType": "4",
                                        }
                                    ],
                                },
                            },
                        },
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "api_inspect",
                    "args": {"method": "GET", "url": api_url},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["inspection"]["data_shape"], "Data.items[]")
            self.assertEqual(response.body["result"]["inspection"]["item_count"], 1)

    def test_run_detail_read_fetches_and_parses_detail_page(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )
            detail_url = "http://10.10.50.110/seeyon/collaboration/collaboration.do?method=summary&affairId=a1"

            def extension_worker():
                tasks = []
                for _ in range(20):
                    tasks = state.handle(
                        "GET",
                        "/extension/tasks",
                        query={"client_id": "chrome-1"},
                    ).body["tasks"]
                    if tasks:
                        break
                    time.sleep(0.01)
                self.assertEqual(tasks[0]["kind"], "rendered_html_snapshot")
                self.assertEqual(tasks[0]["payload"]["url"], detail_url)
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": tasks[0]["id"],
                        "ok": True,
                        "result": {
                            "url": detail_url,
                            "title": "Seal request",
                            "html": """
                            <h1>Seal request</h1>
                            <table><tr><th>Applicant</th><td>Alice</td></tr></table>
                            <a href="/seeyon/fileUpload.do?method=download&fileId=f1">seal-plan.pdf</a>
                            <div class="processLog">Opinion: approved</div>
                            """,
                        },
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "detail_read",
                    "args": {"url": detail_url},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["title"], "Seal request")
            self.assertEqual(response.body["result"]["fields"][0], {"name": "Applicant", "value": "Alice"})
            self.assertEqual(response.body["result"]["attachments"][0]["name"], "seal-plan.pdf")
            self.assertEqual(response.body["result"]["workflow"][0]["text"], "Opinion: approved")

    def test_run_api_save_replays_and_writes_discovered_api_file(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )
            api_url = "http://10.10.50.110/seeyon/ajax.do?method=ajaxAction"

            def extension_worker():
                tasks = []
                for _ in range(20):
                    tasks = state.handle(
                        "GET",
                        "/extension/tasks",
                        query={"client_id": "chrome-1"},
                    ).body["tasks"]
                    if tasks:
                        break
                    time.sleep(0.01)
                self.assertEqual(tasks[0]["kind"], "page_fetch")
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": tasks[0]["id"],
                        "ok": True,
                        "result": {
                            "status": 200,
                            "ok": True,
                            "url": api_url,
                            "json": {"Data": {"items": [{"title": "Seal request"}]}},
                        },
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "api_save",
                    "args": {
                        "name": "template-section",
                        "method": "GET",
                        "url": api_url,
                        "description": "Template section projection",
                    },
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            saved_path = Path(tmp) / "discovered" / "oa" / "apis" / "template-section.json"
            self.assertTrue(saved_path.exists())
            saved = json.loads(saved_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["name"], "template-section")
            self.assertEqual(saved["access"], "read")
            self.assertEqual(saved["risk"], "low")
            self.assertEqual(saved["request"]["url"], api_url)
            self.assertEqual(saved["inspection"]["data_shape"], "Data.items[]")
            self.assertEqual(response.body["result"]["saved_path"], str(saved_path))

    def test_run_discovered_api_uses_saved_request_metadata(self):
        with TemporaryDirectory() as tmp:
            api_dir = Path(tmp) / "discovered" / "oa" / "apis"
            api_dir.mkdir(parents=True)
            api_url = "http://10.10.50.110/seeyon/ajax.do?method=ajaxAction"
            (api_dir / "template-section.json").write_text(
                json.dumps(
                    {
                        "schema_version": "bscli.discovered_api.v1",
                        "name": "template-section",
                        "system": "oa",
                        "description": "Template section projection",
                        "request": {"method": "GET", "url": api_url, "headers": {}, "body": None},
                        "inspection": {"data_shape": "Data.items[]", "item_count": 36},
                    }
                ),
                encoding="utf-8",
            )
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )

            def extension_worker():
                tasks = []
                for _ in range(20):
                    tasks = state.handle(
                        "GET",
                        "/extension/tasks",
                        query={"client_id": "chrome-1"},
                    ).body["tasks"]
                    if tasks:
                        break
                    time.sleep(0.01)
                self.assertEqual(tasks[0]["kind"], "page_fetch")
                self.assertEqual(tasks[0]["payload"]["url"], api_url)
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": tasks[0]["id"],
                        "ok": True,
                        "result": {
                            "status": 200,
                            "ok": True,
                            "url": api_url,
                            "json": {"Data": {"items": [{"title": "Seal request"}]}},
                        },
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "discovered_run",
                    "args": {"name": "template-section"},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["api"]["name"], "template-section")
            self.assertEqual(response.body["result"]["inspection"]["data_shape"], "Data.items[]")
            self.assertEqual(response.body["result"]["replay"]["json"]["Data"]["items"][0]["title"], "Seal request")

    def test_run_command_records_trace_and_returns_run_id(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )

            def extension_worker():
                tasks = []
                for _ in range(20):
                    tasks = state.handle(
                        "GET",
                        "/extension/tasks",
                        query={"client_id": "chrome-1"},
                    ).body["tasks"]
                    if tasks:
                        break
                    time.sleep(0.01)
                self.assertEqual(tasks[0]["kind"], "dom_snapshot")
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": tasks[0]["id"],
                        "ok": True,
                        "result": {
                            "url": "http://10.10.50.110/seeyon/main.do?method=main",
                            "title": "OA",
                            "text": "home",
                        },
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "current_page_snapshot",
                    "args": {},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertRegex(response.body["run_id"], r"^[0-9a-f-]{36}$")
            trace = state.handle("GET", f"/extension/results/missing")
            self.assertEqual(trace.status, 404)
            runs = state.trace_store.list_runs()
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["id"], response.body["run_id"])
            self.assertEqual(runs[0]["command"], "current_page_snapshot")
            self.assertEqual(runs[0]["status"], "ok")
            self.assertEqual(runs[0]["result"]["ok"], True)
            self.assertNotIn("home", json.dumps(runs[0]["result"], ensure_ascii=False))

    def test_run_discovered_api_rejects_origin_outside_system_profile(self):
        with TemporaryDirectory() as tmp:
            api_dir = Path(tmp) / "discovered" / "oa" / "apis"
            api_dir.mkdir(parents=True)
            (api_dir / "external.json").write_text(
                json.dumps(
                    {
                        "schema_version": "bscli.discovered_api.v1",
                        "name": "external",
                        "system": "oa",
                        "request": {"method": "GET", "url": "http://example.test/api"},
                        "inspection": {"data_shape": "json{}"},
                    }
                ),
                encoding="utf-8",
            )
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "discovered_run",
                    "args": {"name": "external"},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 403)
            self.assertIn("origin", response.body["error"])
            self.assertEqual(state.trace_store.list_runs()[0]["status"], "error")

    def test_run_discovered_api_requires_confirmation_for_non_get(self):
        with TemporaryDirectory() as tmp:
            api_dir = Path(tmp) / "discovered" / "oa" / "apis"
            api_dir.mkdir(parents=True)
            (api_dir / "submit.json").write_text(
                json.dumps(
                    {
                        "schema_version": "bscli.discovered_api.v1",
                        "name": "submit",
                        "system": "oa",
                        "access": "write",
                        "risk": "medium",
                        "request": {
                            "method": "POST",
                            "url": "http://10.10.50.110/seeyon/ajax.do",
                        },
                        "inspection": {"data_shape": "json{}"},
                    }
                ),
                encoding="utf-8",
            )
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "discovered_run",
                    "args": {"name": "submit"},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 409)
            self.assertEqual(response.body["ok"], False)
            self.assertEqual(response.body["requires_confirmation"], True)
            self.assertIn("requires explicit confirmation", response.body["error"])
            self.assertEqual(
                state.handle("GET", "/extension/tasks", query={"client_id": "chrome-1"}).body[
                    "tasks"
                ],
                [],
            )
            self.assertEqual(state.trace_store.list_runs()[0]["status"], "error")

    def test_run_discovered_api_allows_confirmed_non_get(self):
        with TemporaryDirectory() as tmp:
            api_dir = Path(tmp) / "discovered" / "oa" / "apis"
            api_dir.mkdir(parents=True)
            api_url = "http://10.10.50.110/seeyon/ajax.do"
            (api_dir / "submit.json").write_text(
                json.dumps(
                    {
                        "schema_version": "bscli.discovered_api.v1",
                        "name": "submit",
                        "system": "oa",
                        "access": "write",
                        "risk": "medium",
                        "request": {
                            "method": "POST",
                            "url": api_url,
                            "headers": {"content-type": "application/json"},
                            "body": "{\"approved\":true}",
                        },
                        "inspection": {"data_shape": "json{}"},
                    }
                ),
                encoding="utf-8",
            )
            state = DaemonState(ConfigStore(Path(tmp)))
            state.handle(
                "POST",
                "/extension/register",
                body={
                    "client_id": "chrome-1",
                    "tab_id": 7,
                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                    "title": "OA",
                },
            )

            def extension_worker():
                tasks = []
                for _ in range(20):
                    tasks = state.handle(
                        "GET",
                        "/extension/tasks",
                        query={"client_id": "chrome-1"},
                    ).body["tasks"]
                    if tasks:
                        break
                    time.sleep(0.01)
                self.assertEqual(tasks[0]["kind"], "page_fetch")
                self.assertEqual(tasks[0]["payload"]["method"], "POST")
                self.assertEqual(tasks[0]["payload"]["url"], api_url)
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": tasks[0]["id"],
                        "ok": True,
                        "result": {
                            "status": 200,
                            "ok": True,
                            "url": api_url,
                            "json": {"saved": True},
                        },
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "discovered_run",
                    "args": {"name": "submit", "confirm": True},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["api"]["name"], "submit")
            self.assertEqual(response.body["result"]["api"]["access"], "write")
            self.assertEqual(response.body["result"]["replay"]["json"], {"saved": True})
            self.assertEqual(state.trace_store.list_runs()[0]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
