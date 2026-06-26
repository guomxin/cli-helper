import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlparse

from bscli.core.config import ConfigStore
from bscli.core.config import SystemProfile
from bscli.daemon.app import DaemonResponse, DaemonState


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

    def test_run_api_replay_passes_text_limit_to_page_fetch_task(self):
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
                self.assertEqual(tasks[0]["payload"]["max_text"], 120000)
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
                            "contentType": "text/html",
                            "text": "x" * 120000,
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
                        "method": "GET",
                        "url": "/seeyon/meeting.do?method=view",
                        "max_text": 120000,
                    },
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertEqual(len(response.body["result"]["text"]), 120000)

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

    def test_run_history_list_replays_sent_section_with_done_panel_id(self):
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
            section_id = "2760387438530088138"
            sent_panel_id = "-1871849768923019549"
            done_panel_id = "-5966659239995619621"
            sent_url = (
                "http://10.10.50.110/seeyon/ajax.do?method=ajaxAction"
                "&managerName=sectionManager&managerMethod=doProjection"
                "&arguments=%7B%22sectionBeanId%22%3A%22sentSection%22%2C"
                f"%22entityId%22%3A%22{section_id}%22%2C"
                f"%22panelId%22%3A%22{sent_panel_id}%22%7D"
            )

            def extension_worker():
                seen_kinds = []
                for _ in range(3):
                    tasks = []
                    for _attempt in range(40):
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
                    if task["kind"] == "html_snapshot":
                        state.handle(
                            "POST",
                            "/extension/results",
                            body={
                                "client_id": "chrome-1",
                                "task_id": task["id"],
                                "ok": True,
                                "result": {
                                    "url": "http://10.10.50.110/seeyon/main.do?method=main",
                                    "html": f"""
                                    <div id="section_{section_id}">
                                      <li id="sectionName_{sent_panel_id}" title="&#24050;&#21457;&#20107;&#39033;" class="current"
                                          onclick="javascript:changeTabAndReloadSection(&quot;{section_id}&quot;,&quot;{sent_panel_id}&quot;)">
                                        &#24050;&#21457;&#20107;&#39033;
                                      </li>
                                      <li id="sectionName_{done_panel_id}" title="&#24050;&#21150;&#20107;&#39033;"
                                          onclick="javascript:changeTabAndReloadSection(&quot;{section_id}&quot;,&quot;{done_panel_id}&quot;)">
                                        &#24050;&#21150;&#20107;&#39033;
                                      </li>
                                    </div>
                                    """,
                                },
                            },
                        )
                    elif task["kind"] == "network_log_snapshot":
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
                        fetched = task["payload"]["url"]
                        arguments = json.loads(parse_qs(urlparse(fetched).query)["arguments"][0])
                        self.assertEqual(arguments["sectionBeanId"], "sentSection")
                        self.assertEqual(arguments["entityId"], section_id)
                        self.assertEqual(arguments["panelId"], done_panel_id)
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
                                    "url": fetched,
                                    "json": {
                                        "Name": "done",
                                        "Data": {
                                            "dataCount": 1,
                                            "pageNo": 1,
                                            "rows": [
                                                {
                                                    "cells": [
                                                        {
                                                            "cellContentHTML": "Historical approval",
                                                            "id": "done-123",
                                                            "linkURL": "/collaboration/collaboration.do?method=summary&openFrom=listDone&affairId=done-123&showTab=true",
                                                        },
                                                        {"cellContentHTML": "Finished"},
                                                        {"cellContentHTML": "2026-06-20"},
                                                        {"cellContentHTML": "Collaboration"},
                                                    ]
                                                }
                                            ],
                                        },
                                    },
                                },
                            },
                        )
                self.assertEqual(seen_kinds, ["html_snapshot", "network_log_snapshot", "page_fetch"])

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "history_list",
                    "args": {"kind": "done"},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["kind"], "done")
            self.assertEqual(response.body["result"]["history_tab"]["tab_id"], done_panel_id)
            self.assertEqual(response.body["result"]["items"][0]["affair_id"], "done-123")
            self.assertEqual(response.body["result"]["items"][0]["history_kind"], "done")

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

    def test_run_workflow_list_filters_items(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []
            state._run_nested_oa_command = lambda command, args, timeout: calls.append((command, args, timeout)) or DaemonResponse(
                200,
                {
                    "ok": True,
                    "result": {
                        "items": [
                            {"title": "Weekly report", "affair_id": "a1"},
                            {"title": "Travel request", "affair_id": "a2"},
                        ]
                    },
                },
            )

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "workflow_list",
                    "args": {"type": "pending", "keyword": "weekly", "limit": 1},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 200)
            self.assertEqual([call[0] for call in calls], ["pending_list_api"])
            self.assertEqual(response.body["result"]["count"], 1)
            self.assertEqual(response.body["result"]["items"][0]["affair_id"], "a1")

    def test_run_workflow_list_falls_back_to_pending_dom_list_when_api_fails(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []
            responses = [
                DaemonResponse(502, {"ok": False, "error": "pendingSection API returned HTTP None"}),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "source": "home_dom",
                            "items": [
                                {"title": "Weekly report", "affair_id": "a1"},
                                {"title": "Travel request", "affair_id": "a2"},
                            ],
                        },
                    },
                ),
            ]

            def fake_nested(command, args, timeout):
                calls.append((command, args, timeout))
                return responses.pop(0)

            state._run_nested_oa_command = fake_nested

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "workflow_list",
                    "args": {"type": "pending", "keyword": "weekly"},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 200)
            self.assertEqual([call[0] for call in calls], ["pending_list_api", "pending_list"])
            self.assertEqual(response.body["result"]["source"], "home_dom_fallback")
            self.assertEqual(response.body["result"]["fallback"]["from"], "pending_list_api")
            self.assertEqual(response.body["result"]["count"], 1)
            self.assertEqual(response.body["result"]["items"][0]["affair_id"], "a1")

    def test_run_workflow_opinions_resolves_id_and_returns_structured_opinions(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []
            responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "items": [
                                {
                                    "title": "Weekly report",
                                    "affair_id": "a1",
                                    "href": "http://oa.example.test/detail/a1",
                                },
                                {
                                    "title": "Travel request",
                                    "affair_id": "a2",
                                    "href": "http://oa.example.test/detail/a2",
                                },
                            ]
                        },
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "title": "Weekly report",
                            "workflow": [
                                {
                                    "text": "Alice approved",
                                    "handler": "Alice",
                                    "opinion": "approved",
                                    "time": "",
                                }
                            ],
                        },
                    },
                ),
            ]

            def fake_nested(command, args, timeout):
                calls.append((command, args, timeout))
                return responses.pop(0)

            state._run_nested_oa_command = fake_nested

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "workflow_opinions",
                    "args": {"type": "pending", "id": "a1", "limit": 5},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 200)
            self.assertEqual([call[0] for call in calls], ["pending_list_api", "detail_read"])
            self.assertEqual(calls[1][1], {"url": "http://oa.example.test/detail/a1"})
            self.assertEqual(response.body["result"]["source_item"]["affair_id"], "a1")
            self.assertEqual(response.body["result"]["count"], 1)
            self.assertEqual(response.body["result"]["items"][0]["handler"], "Alice")

    def test_run_workflow_detail_reports_clear_missing_id_error(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state._run_nested_oa_command = lambda *_args: DaemonResponse(
                200,
                {
                    "ok": True,
                    "result": {
                        "items": [
                            {
                                "title": "Weekly report",
                                "affair_id": "a1",
                                "href": "http://oa.example.test/detail/a1",
                            }
                        ]
                    },
                },
            )

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "workflow_detail",
                    "args": {"type": "pending", "id": "missing"},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 404)
            self.assertFalse(response.body["ok"])
            self.assertIn("workflow id not found", response.body["error"])
            self.assertEqual(response.body["result"]["searched_count"], 1)
            self.assertIn("oa workflow list", response.body["suggestions"][0])

    def test_run_doctor_reports_session_and_static_capabilities(self):
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
                    "command": "doctor",
                    "args": {},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 200)
            self.assertTrue(response.body["result"]["daemon"]["ok"])
            self.assertTrue(response.body["result"]["session"]["connected"])
            self.assertIn("workflow.inspect", response.body["result"]["capabilities"]["read_names"])

    def test_run_capability_map_reports_read_write_and_discovered_groups(self):
        with TemporaryDirectory() as tmp:
            api_dir = Path(tmp) / "discovered" / "oa" / "apis"
            api_dir.mkdir(parents=True)
            (api_dir / "template-section.json").write_text(
                json.dumps(
                    {
                        "schema_version": "bscli.discovered_api.v1",
                        "name": "template-section",
                        "system": "oa",
                        "description": "Template section",
                        "access": "read",
                        "risk": "low",
                        "request": {"method": "GET", "url": "http://oa.example.test/ajax.do"},
                        "inspection": {"data_shape": "Data.items[]"},
                    }
                ),
                encoding="utf-8",
            )
            state = DaemonState(ConfigStore(Path(tmp)))

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "capability_map",
                    "args": {},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 200)
            result = response.body["result"]
            self.assertIn({"name": "workflow.inspect", "command": "workflow_inspect", "risk": "low"}, result["read"])
            self.assertIn("workflow.submit", result["write"]["executable"])
            self.assertIn("workflow.archive", result["write"]["dry_run_only"])
            self.assertEqual(result["discovered"][0]["name"], "template-section")

    def test_run_workflow_inspect_resolves_detail_and_summarizes_counts(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []
            responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "items": [
                                {
                                    "title": "Weekly report",
                                    "affair_id": "a1",
                                    "sender": "Alice",
                                    "date": "Today",
                                    "href": "http://oa.example.test/detail/a1",
                                    "read": False,
                                }
                            ]
                        },
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "title": "Weekly report",
                            "url": "http://oa.example.test/detail/a1",
                            "text": "abcdefghi",
                            "fields": [{"name": "Applicant", "value": "Alice"}],
                            "attachments": [{"name": "weekly.docx"}],
                            "workflow": [{"handler": "Bob", "text": "Reviewed"}],
                            "actions": [{"code": "ContinueSubmit", "risk": "high"}],
                        },
                    },
                ),
            ]

            def fake_nested(command, args, timeout):
                calls.append((command, args, timeout))
                return responses.pop(0)

            state._run_nested_oa_command = fake_nested

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "workflow_inspect",
                    "args": {"type": "pending", "id": "a1", "text_limit": 6},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 200)
            self.assertEqual([call[0] for call in calls], ["pending_list_api", "detail_read"])
            result = response.body["result"]
            self.assertEqual(result["source_item"]["affair_id"], "a1")
            self.assertEqual(result["detail"]["text"], "abcdef")
            self.assertEqual(result["summary"]["attachment_count"], 1)
            self.assertEqual(result["summary"]["opinion_count"], 1)
            self.assertEqual(result["summary"]["action_count"], 1)
            self.assertTrue(result["read_effect"]["detail_page_opened"])

    def test_run_workflow_inspect_resolves_id_through_pending_dom_fallback(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []
            responses = [
                DaemonResponse(502, {"ok": False, "error": "pendingSection API returned HTTP None"}),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "items": [
                                {
                                    "title": "Weekly report",
                                    "affair_id": "a1",
                                    "href": "http://oa.example.test/detail/a1",
                                }
                            ]
                        },
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "title": "Weekly report",
                            "text": "body",
                            "workflow": [],
                            "attachments": [],
                            "actions": [],
                        },
                    },
                ),
            ]

            def fake_nested(command, args, timeout):
                calls.append((command, args, timeout))
                return responses.pop(0)

            state._run_nested_oa_command = fake_nested

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "workflow_inspect",
                    "args": {"type": "pending", "id": "a1"},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 200)
            self.assertEqual([call[0] for call in calls], ["pending_list_api", "pending_list", "detail_read"])
            self.assertEqual(response.body["result"]["source_item"]["affair_id"], "a1")

    def test_run_workflow_brief_uses_list_only_and_marks_no_detail_read(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []
            state._run_nested_oa_command = lambda command, args, timeout: calls.append((command, args, timeout)) or DaemonResponse(
                200,
                {
                    "ok": True,
                    "result": {
                        "source": "section_api",
                        "items": [
                            {
                                "title": "Weekly report",
                                "affair_id": "a1",
                                "sender": "Alice",
                                "date": "Today",
                                "category": "协同",
                                "href": "http://oa.example.test/detail/a1",
                                "read": False,
                            },
                            {"title": "Travel", "affair_id": "a2"},
                        ],
                    },
                },
            )

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "workflow_brief",
                    "args": {"type": "pending", "keyword": "weekly", "limit": 1},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 200)
            self.assertEqual([call[0] for call in calls], ["workflow_list"])
            result = response.body["result"]
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["items"][0]["title"], "Weekly report")
            self.assertFalse(result["items"][0]["detail_read"])
            self.assertGreater(result["items"][0]["attention_score"], 0)
            self.assertIn("unread", result["items"][0]["attention_signals"])
            self.assertFalse(result["read_effect"]["detail_page_opened"])

    def test_run_inbox_analyze_list_only_ranks_items_without_detail_reads(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []

            def fake_nested(command, args, timeout):
                calls.append((command, args, timeout))
                return DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "type": "pending",
                            "source": "section_api",
                            "source_count": 2,
                            "count": 2,
                            "items": [
                                {
                                    "title": "Contract archive",
                                    "affair_id": "a1",
                                    "sender": "Alice",
                                    "date": "Today",
                                    "category": "Contract",
                                    "href": "http://oa.example.test/detail/a1",
                                    "read": False,
                                    "detail_read": False,
                                },
                                {
                                    "title": "General notice",
                                    "affair_id": "a2",
                                    "sender": "Bob",
                                    "date": "Yesterday",
                                    "category": "Notice",
                                    "read": True,
                                    "detail_read": False,
                                },
                            ],
                            "read_effect": {"detail_page_opened": False, "may_mark_read": False},
                        },
                    },
                )

            state._run_nested_oa_command = fake_nested

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "inbox_analyze",
                    "args": {"type": "pending", "limit": 2},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 200)
            self.assertEqual([call[0] for call in calls], ["workflow_brief"])
            result = response.body["result"]
            self.assertEqual(result["mode"], "list_only")
            self.assertEqual(result["deep_attempt_count"], 0)
            self.assertEqual(result["deep_count"], 0)
            self.assertFalse(result["read_effect"]["detail_page_opened"])
            self.assertEqual(result["items"][0]["affair_id"], "a1")
            self.assertGreater(result["items"][0]["attention_score"], result["items"][1]["attention_score"])
            self.assertIn("unread", result["items"][0]["attention_signals"])
            self.assertFalse(result["items"][0]["detail_read"])

    def test_run_inbox_analyze_deep_enriches_limited_items(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []

            def fake_nested(command, args, timeout):
                calls.append((command, args, timeout))
                if command == "workflow_brief":
                    return DaemonResponse(
                        200,
                        {
                            "ok": True,
                            "result": {
                                "type": "pending",
                                "source": "section_api",
                                "source_count": 2,
                                "count": 2,
                                "items": [
                                    {
                                        "title": "Contract archive",
                                        "affair_id": "a1",
                                        "sender": "Alice",
                                        "date": "Today",
                                        "category": "Contract",
                                        "href": "http://oa.example.test/detail/a1",
                                        "read": True,
                                        "detail_read": False,
                                    },
                                    {
                                        "title": "General notice",
                                        "affair_id": "a2",
                                        "sender": "Bob",
                                        "date": "Yesterday",
                                        "category": "Notice",
                                        "href": "http://oa.example.test/detail/a2",
                                        "read": True,
                                        "detail_read": False,
                                    },
                                ],
                                "read_effect": {"detail_page_opened": False, "may_mark_read": False},
                            },
                        },
                    )
                if command == "workflow_evidence":
                    return DaemonResponse(
                        200,
                        {
                            "ok": True,
                            "result": {
                                "source_item": {"title": "Contract archive", "affair_id": "a1"},
                                "evidence": {
                                    "identity": {"title": "Contract archive", "affair_id": "a1"},
                                    "body": {"text_excerpt": "Archive this contract", "text_length": 21, "truncated": False},
                                    "attachments": {"count": 1, "items": [{"name": "contract.pdf"}]},
                                    "opinions": {"count": 1, "items": [{"handler": "Bob"}]},
                                    "actions": {
                                        "count": 1,
                                        "high_risk_count": 1,
                                        "codes": ["Archive"],
                                        "items": [{"code": "Archive", "risk": "high"}],
                                    },
                                    "attention_signals": ["has_high_risk_actions", "has_attachments"],
                                },
                                "read_effect": {"detail_page_opened": True, "may_mark_read": True},
                            },
                        },
                    )
                raise AssertionError(command)

            state._run_nested_oa_command = fake_nested

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "inbox_analyze",
                    "args": {"type": "pending", "limit": 2, "deep": True, "deep_limit": 1, "text_limit": 120},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 200)
            self.assertEqual([call[0] for call in calls], ["workflow_brief", "workflow_evidence"])
            self.assertEqual(calls[1][1], {"type": "pending", "id": "a1", "text_limit": 120})
            result = response.body["result"]
            self.assertEqual(result["mode"], "deep")
            self.assertEqual(result["deep_attempt_count"], 1)
            self.assertEqual(result["deep_count"], 1)
            self.assertTrue(result["read_effect"]["detail_page_opened"])
            self.assertTrue(result["items"][0]["detail_read"])
            self.assertFalse(result["items"][1]["detail_read"])
            self.assertEqual(result["items"][0]["evidence_summary"]["actions"]["high_risk_count"], 1)
            self.assertIn("has_high_risk_actions", result["items"][0]["attention_signals"])

    def test_run_workflow_evidence_builds_decision_packet(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state._run_nested_oa_command = lambda command, args, timeout: DaemonResponse(
                200,
                {
                    "ok": True,
                    "result": {
                        "source_item": {
                            "title": "Contract archive",
                            "affair_id": "a1",
                            "sender": "Alice",
                            "href": "http://oa.example.test/detail/a1",
                        },
                        "detail": {
                            "title": "Contract archive",
                            "text": "0123456789",
                            "fields": [{"name": "Amount", "value": "100"}],
                            "attachments": [{"name": "contract.pdf"}],
                            "workflow": [{"handler": "Bob", "text": "Agree"}],
                            "actions": [{"code": "Archive", "risk": "high"}],
                        },
                    },
                },
            )

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "workflow_evidence",
                    "args": {"type": "pending", "id": "a1", "text_limit": 5},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 200)
            evidence = response.body["result"]["evidence"]
            self.assertEqual(evidence["identity"]["affair_id"], "a1")
            self.assertEqual(evidence["body"]["text_excerpt"], "01234")
            self.assertTrue(evidence["body"]["truncated"])
            self.assertEqual(evidence["attachments"]["count"], 1)
            self.assertEqual(evidence["opinions"]["count"], 1)
            self.assertEqual(evidence["actions"]["high_risk_count"], 1)
            self.assertEqual(evidence["actions"]["codes"], ["Archive"])
            self.assertIn("has_high_risk_actions", evidence["attention_signals"])

    def test_run_workflow_timeline_normalizes_workflow_entries(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state._run_nested_oa_command = lambda command, args, timeout: DaemonResponse(
                200,
                {
                    "ok": True,
                    "result": {
                        "source_item": {"title": "Weekly report", "affair_id": "a1"},
                        "count": 1,
                        "items": [
                            {
                                "node": "Manager",
                                "handler": "Alice",
                                "time": "2026-06-25",
                                "opinion": "同意",
                                "text": "Alice approved",
                            }
                        ],
                    },
                },
            )

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "workflow_timeline",
                    "args": {"type": "pending", "id": "a1", "limit": 3},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 200)
            result = response.body["result"]
            self.assertEqual(result["source_item"]["affair_id"], "a1")
            self.assertEqual(result["items"][0]["index"], 1)
            self.assertEqual(result["items"][0]["handler"], "Alice")
            self.assertEqual(result["items"][0]["node"], "Manager")
            self.assertEqual(result["items"][0]["text"], "Alice approved")

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

    def test_run_discovered_api_renders_parameterized_request(self):
        with TemporaryDirectory() as tmp:
            api_dir = Path(tmp) / "discovered" / "oa" / "apis"
            api_dir.mkdir(parents=True)
            (api_dir / "search.json").write_text(
                json.dumps(
                    {
                        "schema_version": "bscli.discovered_api.v1",
                        "name": "search",
                        "system": "oa",
                        "description": "Search projection",
                        "request": {
                            "method": "GET",
                            "url": "http://10.10.50.110/seeyon/ajax.do?q={{keyword}}&page={{page}}",
                        },
                        "parameters": {
                            "keyword": {"type": "string", "required": True},
                            "page": {"type": "integer"},
                        },
                        "inspection": {"data_shape": "Data.items[]"},
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
                self.assertEqual(
                    tasks[0]["payload"]["url"],
                    "http://10.10.50.110/seeyon/ajax.do?q=budget&page=2",
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
                            "url": tasks[0]["payload"]["url"],
                            "json": {"Data": {"items": []}},
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
                    "args": {"name": "search", "keyword": "budget", "page": 2},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            self.assertEqual(response.status, 200)
            self.assertEqual(
                response.body["result"]["request"]["url"],
                "http://10.10.50.110/seeyon/ajax.do?q=budget&page=2",
            )

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

    def test_run_oa_write_dry_run_records_sanitized_audit_without_browser_task(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []
            state._run_nested_oa_command = lambda command, args, timeout_seconds: calls.append((command, args, timeout_seconds)) or DaemonResponse(
                200,
                {
                    "ok": True,
                    "result": {
                        "title": "Weekly report",
                        "actions": [{"code": "ContinueSubmit", "label": "提交", "risk": "high"}],
                    },
                },
            )

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "write_dry_run",
                    "args": {
                        "affair_id": "affair-1",
                        "action": "ContinueSubmit",
                        "opinion": "approved",
                        "source_url": "http://oa.example.test/detail?affairId=affair-1",
                    },
                    "timeout_seconds": 1,
                },
            )

            audit_path = Path(tmp) / "audit" / "oa-write-plans.jsonl"
            audit_rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["mode"], "dry-run")
            self.assertFalse(response.body["result"]["safety"]["will_execute"])
            self.assertEqual(response.body["result"]["request"]["status"], "not_sent")
            self.assertEqual(len(audit_rows), 1)
            self.assertEqual(audit_rows[0]["target"]["affair_id"], "affair-1")
            self.assertNotIn("approved", json.dumps(audit_rows[0], ensure_ascii=False))
            self.assertEqual([call[0] for call in calls], ["detail_read"])
            self.assertEqual(state.bridge.pending_tasks, [])
            self.assertEqual(state.trace_store.list_runs()[0]["status"], "ok")

    def test_run_oa_meeting_reply_dry_run_prechecks_current_reply(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            state._resolve_oa_meeting_reply_target = lambda args, timeout_seconds: (
                {
                    "affair_id": "affair-1",
                    "meeting_id": "meeting-1",
                    "proxy_id": "",
                    "source_url": "http://oa.example.test/meeting.do?meetingId=meeting-1",
                    "source_item": {"title": "Water meeting", "affair_id": "affair-1"},
                },
                None,
            )
            state._run_oa_meeting_view = lambda meeting_id, proxy_id, timeout_seconds: DaemonResponse(
                200,
                {
                    "ok": True,
                    "result": {
                        "meetingAuth": {"showReply": True, "showReplyAttitude": True},
                        "meetingVo": {"title": "Water meeting", "state": 10, "roomState": 1},
                        "myReply": {"feedbackFlag": -100, "feedbackName": "not replied", "userName": "Tester"},
                    },
                },
            )

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "meeting_reply_dry_run",
                    "args": {"id": "affair-1", "attitude": "join", "feedback": "will attend"},
                    "timeout_seconds": 1,
                },
            )

            audit_path = Path(tmp) / "audit" / "oa-meeting-replies.jsonl"
            audit_rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
            plan = response.body["result"]
            self.assertEqual(response.status, 200)
            self.assertTrue(plan["precheck"]["passed"])
            self.assertEqual(plan["target"]["meeting_id"], "meeting-1")
            self.assertEqual(plan["action"], {"code": "join", "label": "参加", "feedbackFlag": 1})
            self.assertEqual(plan["current_reply"]["feedbackFlag"], -100)
            self.assertFalse(plan["safety"]["will_execute"])
            self.assertNotIn("will attend", json.dumps(audit_rows[0], ensure_ascii=False))

    def test_run_oa_write_discover_aggregates_history_detail_actions(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []
            responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "kind": "done",
                            "source_count": 2,
                            "items": [
                                {
                                    "title": "Done weekly",
                                    "affair_id": "done-1",
                                    "href": "http://oa.example.test/detail/done-1",
                                },
                                {
                                    "title": "Done archive",
                                    "affair_id": "done-2",
                                    "href": "http://oa.example.test/detail/done-2",
                                },
                            ],
                        },
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "title": "Done weekly",
                            "actions": [
                                {"code": "ContinueSubmit", "label": "Submit", "risk": "high"},
                                {"code": "Archive", "label": "Archive", "risk": "high"},
                            ],
                        },
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "title": "Done archive",
                            "actions": [
                                {"code": "Archive", "label": "Archive", "risk": "high"},
                            ],
                        },
                    },
                ),
            ]

            def fake_nested(command, args, timeout_seconds):
                calls.append((command, args, timeout_seconds))
                return responses.pop(0)

            state._run_nested_oa_command = fake_nested

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "write_discover",
                    "args": {"source": "history", "kind": "done", "limit": 5, "deep_limit": 2},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 200)
            self.assertEqual([call[0] for call in calls], ["history_list", "detail_read", "detail_read"])
            self.assertEqual(calls[0][1], {"kind": "done", "limit": 5})
            result = response.body["result"]
            self.assertEqual(result["schema_version"], "bscli.oa_write_discovery.v1")
            self.assertEqual(result["source"], "history")
            self.assertEqual(result["kind"], "done")
            self.assertEqual(result["detail_attempt_count"], 2)
            actions = {item["code"]: item for item in result["actions"]}
            self.assertEqual(actions["Archive"]["seen_count"], 2)
            self.assertFalse(actions["Archive"]["execute_allowed"])
            self.assertEqual(actions["ContinueSubmit"]["seen_count"], 1)
            self.assertEqual(result["items"][0]["actions"][0]["code"], "ContinueSubmit")
            self.assertEqual(result["read_effect"]["detail_page_opened"], True)

    def test_run_history_profile_clusters_high_frequency_history(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []
            responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "kind": "done",
                            "source_count": 3,
                            "items": [
                                {
                                    "title": "[Seal] Seal Request - Alice",
                                    "status": "finished",
                                    "date": "2026-06-01",
                                    "category": "Collaboration",
                                    "affair_id": "done-1",
                                    "href": "http://oa.example.test/detail/done-1",
                                    "history_kind": "done",
                                },
                                {
                                    "title": "[Seal] Seal Request - Bob",
                                    "status": "finished",
                                    "date": "2026-06-02",
                                    "category": "Collaboration",
                                    "affair_id": "done-2",
                                    "href": "http://oa.example.test/detail/done-2",
                                    "history_kind": "done",
                                },
                                {
                                    "title": "[Purchase] Contract Review - Carol",
                                    "status": "finished",
                                    "date": "2026-06-03",
                                    "category": "Contract",
                                    "affair_id": "done-3",
                                    "href": "http://oa.example.test/detail/done-3",
                                    "history_kind": "done",
                                },
                                {
                                    "title": "(\u81ea\u52a8\u53d1\u8d77)\u3010HR\u3011Monthly Attendance-Chris-2026-05",
                                    "status": "finished",
                                    "date": "2026-06-04",
                                    "category": "Collaboration",
                                    "affair_id": "done-4",
                                    "href": "http://oa.example.test/detail/done-4",
                                    "history_kind": "done",
                                },
                            ],
                        },
                    },
                )
            ]

            def fake_nested(command, args, timeout_seconds):
                calls.append((command, args, timeout_seconds))
                return responses.pop(0)

            state._run_nested_oa_command = fake_nested

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "history_profile",
                    "args": {"kind": "done", "limit": 20},
                    "timeout_seconds": 1,
                },
            )

            result = response.body["result"]
            self.assertEqual(response.status, 200)
            self.assertEqual([call[0] for call in calls], ["history_list"])
            self.assertEqual(calls[0][1], {"kind": "done", "limit": 20})
            self.assertEqual(result["schema_version"], "bscli.oa_history_profile.v1")
            self.assertEqual(result["source_count"], 4)
            self.assertEqual(result["cluster_count"], 3)
            self.assertEqual(result["clusters"][0]["title_pattern"], "[Seal] Seal Request")
            self.assertEqual(result["clusters"][0]["category_tag"], "Seal")
            self.assertEqual(result["clusters"][0]["count"], 2)
            self.assertEqual(result["clusters"][0]["date_range"], {"start": "2026-06-01", "end": "2026-06-02"})
            self.assertEqual(result["clusters"][0]["sample_items"][0]["affair_id"], "done-1")
            self.assertIn(
                "\u3010HR\u3011Monthly Attendance",
                [cluster["title_pattern"] for cluster in result["clusters"]],
            )

    def test_run_template_match_matches_history_clusters_to_template_candidates(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []
            responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "schema_version": "bscli.oa_history_profile.v1",
                            "source_count": 3,
                            "cluster_count": 1,
                            "clusters": [
                                {
                                    "cluster_id": "seal-request",
                                    "title_pattern": "[Seal] Seal Request",
                                    "category_tag": "Seal",
                                    "subject": "Seal Request",
                                    "count": 3,
                                    "sample_items": [{"title": "[Seal] Seal Request - Alice"}],
                                }
                            ],
                        },
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "items": [
                                {
                                    "title": "[Seal] Seal Request",
                                    "template_id": "tpl-seal",
                                    "href": "http://oa.example.test/new?templateId=tpl-seal",
                                },
                                {
                                    "title": "[Purchase] Contract Review",
                                    "template_id": "tpl-purchase",
                                    "href": "http://oa.example.test/new?templateId=tpl-purchase",
                                },
                            ]
                        },
                    },
                ),
            ]

            def fake_nested(command, args, timeout_seconds):
                calls.append((command, args, timeout_seconds))
                return responses.pop(0)

            state._run_nested_oa_command = fake_nested

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "template_match",
                    "args": {"kind": "done", "limit": 20},
                    "timeout_seconds": 1,
                },
            )

            result = response.body["result"]
            self.assertEqual(response.status, 200)
            self.assertEqual([call[0] for call in calls], ["history_profile", "template_list_api"])
            self.assertEqual(calls[0][1], {"kind": "done", "limit": 20})
            self.assertEqual(result["schema_version"], "bscli.oa_template_match.v1")
            self.assertEqual(result["clusters"][0]["match_status"], "matched")
            self.assertEqual(result["clusters"][0]["best_template"]["template_id"], "tpl-seal")
            self.assertGreaterEqual(result["clusters"][0]["candidates"][0]["score"], 0.8)
            self.assertIn("exact", result["clusters"][0]["candidates"][0]["evidence"])

    def test_run_matter_profile_builds_catalog_from_history_template_matches(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []

            def fake_nested(command, args, timeout_seconds):
                calls.append((command, args, timeout_seconds))
                self.assertEqual(command, "template_match")
                return DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "schema_version": "bscli.oa_template_match.v1",
                            "history_profile": {"kind": "all", "source_count": 4, "cluster_count": 2},
                            "template_count": 8,
                            "clusters": [
                                {
                                    "cluster_id": "seal-request",
                                    "title_pattern": "[Seal] Seal Request",
                                    "subject": "Seal Request",
                                    "category_tag": "Seal",
                                    "count": 3,
                                    "kinds": ["done", "sent"],
                                    "match_status": "matched",
                                    "best_template": {
                                        "template_id": "tpl-seal",
                                        "title": "[Seal] Seal Request",
                                        "href": "http://oa.example.test/new?templateId=tpl-seal",
                                        "score": 0.95,
                                    },
                                    "candidates": [{"template_id": "tpl-seal", "title": "[Seal] Seal Request", "score": 0.95}],
                                    "sample_items": [{"title": "[Seal] Seal Request - Alice", "affair_id": "done-1"}],
                                },
                                {
                                    "cluster_id": "hr",
                                    "title_pattern": "【HR】月度考勤确认单",
                                    "subject": "月度考勤确认单",
                                    "category_tag": "HR",
                                    "count": 1,
                                    "kinds": ["tracked"],
                                    "match_status": "unmatched",
                                    "best_template": {},
                                    "candidates": [],
                                    "sample_items": [{"title": "【HR】月度考勤确认单- Bob", "affair_id": "tracked-1"}],
                                },
                            ],
                        },
                    },
                )

            state._run_nested_oa_command = fake_nested

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "matter_profile",
                    "args": {"kind": "all", "keyword": "seal", "limit": 20},
                    "timeout_seconds": 1,
                },
            )

            result = response.body["result"]
            self.assertEqual(response.status, 200)
            self.assertEqual(calls[0][1], {"kind": "all", "keyword": "seal", "limit": 20})
            self.assertEqual(result["schema_version"], "bscli.oa_matter_profile.v1")
            self.assertEqual(result["matter_count"], 2)
            self.assertEqual(result["matched_template_count"], 1)
            self.assertEqual(result["matters"][0]["matter_id"], "seal-request")
            self.assertEqual(result["matters"][0]["template"]["template_id"], "tpl-seal")
            self.assertEqual(result["matters"][0]["available_actions"][0]["command"], "launch_save_draft")
            self.assertTrue(result["matters"][0]["available_actions"][0]["requires_confirmation"])
            self.assertEqual(result["matters"][1]["template"], {})
            self.assertRegex(result["matters"][1]["matter_id"], r"^matter-[a-f0-9]{10}$")
            self.assertEqual(result["matters"][1]["available_actions"][0]["status"], "blocked")

    def test_run_matter_inspect_resolves_matter_and_optionally_reads_launch_fields(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []
            responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "schema_version": "bscli.oa_matter_profile.v1",
                            "matters": [
                                {
                                    "matter_id": "seal-request",
                                    "name": "[Seal] Seal Request",
                                    "template": {
                                        "template_id": "tpl-seal",
                                        "title": "[Seal] Seal Request",
                                        "href": "http://oa.example.test/new?templateId=tpl-seal",
                                    },
                                    "available_actions": [{"command": "launch_save_draft", "status": "available"}],
                                }
                            ],
                        },
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "schema_version": "bscli.oa_launch_inspection.v1",
                            "template_id": "tpl-seal",
                            "fields": [{"name": "content_coll", "type": "textarea", "readonly": False}],
                            "buttons": [{"id": "saveDraft_a", "text": "Save draft"}],
                            "safety": {"submitted_count": 0},
                        },
                    },
                ),
            ]

            def fake_nested(command, args, timeout_seconds):
                calls.append((command, args, timeout_seconds))
                return responses.pop(0)

            state._run_nested_oa_command = fake_nested

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "matter_inspect",
                    "args": {"id": "seal-request", "kind": "all", "with_launch": True},
                    "timeout_seconds": 1,
                },
            )

            result = response.body["result"]
            self.assertEqual(response.status, 200)
            self.assertEqual([call[0] for call in calls], ["matter_profile", "launch_inspect"])
            self.assertEqual(calls[0][1], {"kind": "all"})
            self.assertEqual(calls[1][1], {"template_id": "tpl-seal"})
            self.assertEqual(result["schema_version"], "bscli.oa_matter_inspection.v1")
            self.assertEqual(result["matter"]["matter_id"], "seal-request")
            self.assertEqual(result["launch_inspection"]["fields"][0]["name"], "content_coll")
            self.assertIn("oa launch dry-run --template-id tpl-seal", result["next_steps"][0])

    def test_run_launch_inspect_resolves_template_and_reads_rendered_page_without_submit(self):
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
            template_url = "http://10.10.50.110/seeyon/collaboration/collaboration.do?method=newColl&templateId=tpl-1"
            nested_calls = []
            state._run_nested_oa_command = lambda command, args, timeout_seconds: nested_calls.append(
                (command, args, timeout_seconds)
            ) or DaemonResponse(
                200,
                {
                    "ok": True,
                    "result": {
                        "items": [
                            {
                                "title": "Seal Request",
                                "template_id": "tpl-1",
                                "href": template_url,
                            }
                        ]
                    },
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
                self.assertEqual(tasks[0]["kind"], "rendered_html_snapshot")
                self.assertEqual(tasks[0]["payload"]["url"], template_url)
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": tasks[0]["id"],
                        "ok": True,
                        "result": {
                            "url": template_url,
                            "html": """
                            <html><body>
                              <h1 id="summarySubject">Seal Request</h1>
                              <form><input name="subject"><button id="ContinueSubmit">Submit</button></form>
                              <script>var jsonArrBase = '[{"codes":["ContinueSubmit"],"label":"Submit","id":"ContinueSubmit"}]';</script>
                            </body></html>
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
                    "command": "launch_inspect",
                    "args": {"template_id": "tpl-1", "settle_ms": 0},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            result = response.body["result"]
            self.assertEqual(response.status, 200)
            self.assertEqual(nested_calls[0][0], "template_list_api")
            self.assertEqual(result["schema_version"], "bscli.oa_launch_inspection.v1")
            self.assertEqual(result["template_id"], "tpl-1")
            self.assertEqual(result["template"]["title"], "Seal Request")
            self.assertEqual(result["actions"][0]["code"], "ContinueSubmit")
            self.assertFalse(result["safety"]["execute_allowed"])
            self.assertEqual(result["safety"]["submitted_count"], 0)

    def test_run_oa_write_discover_from_launch_aggregates_inspection_actions(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []

            def fake_nested(command, args, timeout_seconds):
                calls.append((command, args, timeout_seconds))
                return DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "schema_version": "bscli.oa_launch_inspection.v1",
                            "template_id": "tpl-1",
                            "template": {"title": "Seal Request"},
                            "title": "Seal Request",
                            "url": "http://oa.example.test/new?templateId=tpl-1",
                            "actions": [{"code": "ContinueSubmit", "label": "Submit", "risk": "high"}],
                            "buttons": [
                                {
                                    "id": "ContinueSubmit",
                                    "text": "Submit",
                                    "risk": "high",
                                    "action_like": True,
                                }
                            ],
                            "safety": {"execute_allowed": False, "submitted_count": 0},
                        },
                    },
                )

            state._run_nested_oa_command = fake_nested

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "write_discover",
                    "args": {"source": "launch", "template_id": "tpl-1"},
                    "timeout_seconds": 1,
                },
            )

            result = response.body["result"]
            self.assertEqual(response.status, 200)
            self.assertEqual([call[0] for call in calls], ["launch_inspect"])
            self.assertEqual(calls[0][1], {"source": "launch", "template_id": "tpl-1"})
            self.assertEqual(result["schema_version"], "bscli.oa_write_discovery.v1")
            self.assertEqual(result["source"], "launch")
            self.assertEqual(result["actions"][0]["code"], "ContinueSubmit")
            self.assertFalse(result["actions"][0]["execute_allowed"])
            self.assertEqual(result["items"][0]["button_candidates"][0]["id"], "ContinueSubmit")
            self.assertEqual(result["read_effect"]["launch_page_opened"], True)
            self.assertEqual(result["read_effect"]["submitted_count"], 0)

    def test_run_oa_launch_dry_run_validates_fields_without_sending(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []

            def fake_nested(command, args, timeout_seconds):
                calls.append((command, args, timeout_seconds))
                return DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "schema_version": "bscli.oa_launch_inspection.v1",
                            "template_id": "tpl-1",
                            "title": "Seal Request",
                            "url": "http://oa.example.test/new?templateId=tpl-1",
                            "fields": [
                                {"name": "subject", "id": "subject", "label": "Subject", "disabled": False, "readonly": False},
                                {"name": "remark", "id": "remark", "label": "Remark", "disabled": False, "readonly": False},
                            ],
                            "buttons": [{"id": "saveDraft_a", "text": "保存待发", "risk": "medium"}],
                            "safety": {"execute_allowed": False, "submitted_count": 0},
                        },
                    },
                )

            state._run_nested_oa_command = fake_nested

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "launch_dry_run",
                    "args": {"template_id": "tpl-1", "fields": {"subject": "Draft subject", "remark": "Hello"}},
                    "timeout_seconds": 1,
                },
            )

            result = response.body["result"]
            self.assertEqual(response.status, 200)
            self.assertEqual([call[0] for call in calls], ["launch_inspect"])
            self.assertEqual(result["schema_version"], "bscli.oa_launch_draft_plan.v1")
            self.assertEqual(result["mode"], "dry-run")
            self.assertFalse(result["safety"]["will_execute"])
            self.assertEqual(result["action"]["code"], "SaveDraft")
            self.assertEqual(result["governance"]["verification_method"], "draft_save_scheduled_ack")
            self.assertEqual(result["target"]["template_id"], "tpl-1")
            self.assertEqual(result["fields"][0]["name"], "subject")
            self.assertEqual(result["fields"][0]["length"], len("Draft subject"))
            self.assertNotIn("Draft subject", json.dumps(result, ensure_ascii=False))
            self.assertEqual(result["safety"]["submitted_count"], 0)

    def test_run_oa_launch_save_draft_requires_confirm_before_opening_page(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []
            state._run_nested_oa_command = lambda command, args, timeout_seconds: calls.append(
                (command, args, timeout_seconds)
            ) or DaemonResponse(500, {"ok": False, "error": "should not inspect before confirm"})

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "launch_save_draft",
                    "args": {"template_id": "tpl-1", "fields": {"subject": "Draft subject"}},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 409)
            self.assertEqual(calls, [])
            self.assertTrue(response.body["requires_confirmation"])
            self.assertEqual(response.body["result"]["mode"], "save-draft")
            self.assertEqual(response.body["result"]["safety"]["submitted_count"], 0)

    def test_run_oa_launch_save_draft_with_confirm_dispatches_extension_task_and_audits_redacted(self):
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

            def fake_nested(command, args, timeout_seconds):
                self.assertEqual(command, "launch_inspect")
                return DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "schema_version": "bscli.oa_launch_inspection.v1",
                            "template_id": "tpl-1",
                            "title": "Seal Request",
                            "url": "http://oa.example.test/new?templateId=tpl-1",
                            "fields": [
                                {"name": "subject", "id": "subject", "label": "Subject", "disabled": False, "readonly": False}
                            ],
                            "buttons": [{"id": "saveDraft_a", "text": "保存待发", "risk": "medium"}],
                            "safety": {"execute_allowed": False, "submitted_count": 0},
                        },
                    },
                )

            state._run_nested_oa_command = fake_nested
            seen_tasks = []

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
                seen_tasks.extend(tasks)
                self.assertEqual(tasks[0]["kind"], "seeyon_launch_save_draft")
                self.assertEqual(tasks[0]["payload"]["fields"], {"subject": "Draft subject"})
                self.assertEqual(tasks[0]["payload"]["template_id"], "tpl-1")
                self.assertEqual(tasks[0]["payload"]["script_timeout_ms"], 10000)
                self.assertTrue(tasks[0]["payload"]["confirm"])
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": tasks[0]["id"],
                        "ok": True,
                        "result": {
                            "draft_saved": True,
                            "save_attempt_mode": "scheduled_fill_click_ack",
                            "action": "SaveDraft",
                            "clicked": {"id": "saveDraft_a", "text": "保存待发"},
                            "scheduled_fields": [{"name": "subject", "length": len("Draft subject")}],
                            "submitted_count": 0,
                            "url": "http://oa.example.test/new?templateId=tpl-1",
                            "title": "Seal Request",
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
                    "command": "launch_save_draft",
                    "args": {"template_id": "tpl-1", "fields": {"subject": "Draft subject"}, "confirm": True},
                    "timeout_seconds": 1,
                },
            )
            worker.join()

            result = response.body["result"]
            self.assertEqual(response.status, 200)
            self.assertEqual(seen_tasks[0]["kind"], "seeyon_launch_save_draft")
            self.assertTrue(result["draft_saved"])
            self.assertEqual(result["submitted_count"], 0)
            self.assertEqual(result["save_attempt_mode"], "scheduled_fill_click_ack")
            self.assertEqual(result["plan"]["governance"]["verification_method"], "draft_save_scheduled_ack")
            self.assertEqual(result["plan"]["safety"]["submitted_count"], 0)
            audit_text = (Path(tmp) / "audit" / "oa-launch-drafts.jsonl").read_text(encoding="utf-8")
            self.assertIn('"schema_version": "bscli.oa_launch_draft_plan.v1"', audit_text)
            self.assertNotIn("Draft subject", audit_text)

    def test_run_oa_write_capabilities_reports_workflow_and_meeting_actions(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            nested_calls = []
            nested_responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "type": "pending",
                            "items": [
                                {
                                    "title": "Weekly report",
                                    "affair_id": "affair-1",
                                    "href": "http://oa.example.test/detail?affairId=affair-1",
                                    "state": "pending",
                                },
                                {
                                    "title": "Water meeting",
                                    "affair_id": "affair-2",
                                    "href": "http://oa.example.test/meeting.do?method=view&meetingId=meeting-2",
                                },
                            ],
                        },
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "title": "Weekly report",
                            "actions": [
                                {"code": "Track", "label": "track", "risk": "medium"},
                                {"code": "ContinueSubmit", "label": "submit", "risk": "high"},
                            ],
                        },
                    },
                ),
            ]

            def fake_nested(command, args, timeout_seconds):
                nested_calls.append((command, args, timeout_seconds))
                return nested_responses.pop(0)

            state._run_nested_oa_command = fake_nested
            state._run_oa_meeting_view = lambda meeting_id, proxy_id, timeout_seconds: DaemonResponse(
                200,
                {
                    "ok": True,
                    "result": {
                        "meetingAuth": {"showReply": True, "showReplyAttitude": True},
                        "meetingVo": {"title": "Water meeting", "state": 10, "roomState": 1},
                        "myReply": {"feedbackFlag": -100, "feedbackName": "not replied"},
                    },
                },
            )

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "write_capabilities",
                    "args": {"type": "pending", "limit": 2},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 200)
            self.assertTrue(response.body["ok"])
            self.assertEqual([call[0] for call in nested_calls], ["workflow_list", "detail_read"])
            items = response.body["result"]["items"]
            self.assertEqual(response.body["result"]["count"], 2)
            self.assertEqual(items[0]["category"], "workflow")
            self.assertEqual(items[0]["supported_write_actions"][0]["name"], "workflow.submit")
            self.assertEqual(items[0]["supported_write_actions"][0]["daemon_commands"]["execute"], "write_execute")
            self.assertEqual(items[0]["verification_method"], "pending_disappearance")
            self.assertEqual(items[1]["category"], "meeting")
            self.assertEqual(items[1]["current_state"]["feedbackFlag"], -100)
            self.assertEqual(items[1]["supported_write_actions"][0]["name"], "meeting.reply")
            self.assertEqual(items[1]["supported_write_actions"][0]["daemon_commands"]["execute"], "meeting_reply_execute")
            self.assertEqual(items[1]["verification_method"], "meeting_reply_readback")

    def test_run_oa_write_capabilities_reports_archive_as_dry_run_only(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            nested_responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "type": "pending",
                            "items": [
                                {
                                    "title": "Contract archive",
                                    "affair_id": "archive-1",
                                    "href": "http://oa.example.test/detail?affairId=archive-1",
                                }
                            ],
                        },
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "title": "Contract archive",
                            "actions": [
                                {"code": "Archive", "label": "处理后归档", "risk": "high"},
                                {"code": "Opinion", "label": "意见", "risk": "medium"},
                            ],
                        },
                    },
                ),
            ]
            state._run_nested_oa_command = lambda *_args: nested_responses.pop(0)

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "write_capabilities",
                    "args": {"type": "pending"},
                    "timeout_seconds": 1,
                },
            )

            item = response.body["result"]["items"][0]
            self.assertEqual(response.status, 200)
            self.assertEqual(item["supported_write_actions"], [])
            self.assertEqual(item["capability_status"], "dry_run_only")
            self.assertEqual(item["unpromoted_write_actions"][0]["name"], "workflow.archive")
            self.assertEqual(item["unpromoted_write_actions"][0]["action"], "Archive")
            self.assertTrue(item["unpromoted_write_actions"][0]["dry_run_allowed"])
            self.assertFalse(item["unpromoted_write_actions"][0]["execute_allowed"])
            self.assertIn("oa write dry-run --affair-id archive-1 --action Archive", item["unpromoted_write_actions"][0]["dry_run_command"])
            self.assertIn("execution mapping", item["unpromoted_write_actions"][0]["promotion_requirements"][0])
            self.assertIn("execute not promoted", item["blocked_reasons"][0])

    def test_run_oa_meeting_reply_execute_posts_and_verifies_reply(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            posts = []
            views = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "meetingAuth": {"showReply": True, "showReplyAttitude": True},
                            "meetingVo": {"title": "Water meeting", "state": 10, "roomState": 1},
                            "myReply": {"feedbackFlag": -100, "feedbackName": "not replied", "userName": "Tester"},
                        },
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "meetingAuth": {"showReply": True, "showReplyAttitude": True},
                            "meetingVo": {"title": "Water meeting", "state": 10, "roomState": 1},
                            "myReply": {"feedbackFlag": 1, "feedbackName": "参加", "userName": "Tester"},
                        },
                    },
                ),
            ]
            state._resolve_oa_meeting_reply_target = lambda args, timeout_seconds: (
                {
                    "affair_id": "affair-1",
                    "meeting_id": "meeting-1",
                    "proxy_id": "",
                    "source_url": "http://oa.example.test/meeting.do?meetingId=meeting-1",
                    "source_item": {"title": "Water meeting", "affair_id": "affair-1"},
                },
                None,
            )
            state._run_oa_meeting_view = lambda meeting_id, proxy_id, timeout_seconds: views.pop(0)

            def fake_post(meeting_id, proxy_id, attitude, feedback, timeout_seconds):
                posts.append((meeting_id, proxy_id, attitude, feedback, timeout_seconds))
                return DaemonResponse(200, {"ok": True, "result": {"json": {"success": True}}})

            state._post_oa_meeting_reply = fake_post

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "meeting_reply_execute",
                    "args": {
                        "id": "affair-1",
                        "attitude": "join",
                        "feedback": "will attend",
                        "confirm": True,
                        "verify_wait": 0,
                    },
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 200)
            self.assertEqual(posts, [("meeting-1", "", 1, "will attend", 1.0)])
            self.assertTrue(response.body["result"]["submitted"])
            self.assertEqual(response.body["result"]["verification"]["status"], "matched")

    def test_run_oa_meeting_reply_execute_requires_confirm(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "meeting_reply_execute",
                    "args": {"id": "affair-1", "attitude": "join"},
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 409)
            self.assertTrue(response.body["requires_confirmation"])

    def test_run_oa_write_dry_run_prechecks_target_action_and_records_report(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []
            responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "items": [
                                {
                                    "title": "Weekly report",
                                    "affair_id": "affair-1",
                                    "href": "http://oa.example.test/detail?affairId=affair-1",
                                }
                            ]
                        },
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "title": "Weekly report",
                            "actions": [
                                {"code": "Track", "label": "跟踪", "risk": "medium"},
                                {"code": "ContinueSubmit", "label": "提交", "risk": "high"},
                            ],
                        },
                    },
                ),
            ]

            def fake_nested(command, args, timeout_seconds):
                calls.append((command, args, timeout_seconds))
                return responses.pop(0)

            state._run_nested_oa_command = fake_nested

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "write_dry_run",
                    "args": {
                        "affair_id": "affair-1",
                        "action": "ContinueSubmit",
                        "opinion": "approved",
                    },
                    "timeout_seconds": 1,
                },
            )

            audit_path = Path(tmp) / "audit" / "oa-write-plans.jsonl"
            audit_rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
            plan = response.body["result"]
            self.assertEqual(response.status, 200)
            self.assertTrue(response.body["ok"])
            self.assertEqual([call[0] for call in calls], ["pending_list_api", "detail_read"])
            self.assertEqual(plan["target"]["source_item"]["affair_id"], "affair-1")
            self.assertEqual(plan["target"]["source_url"], "http://oa.example.test/detail?affairId=affair-1")
            self.assertEqual(plan["precheck"]["status"], "passed")
            self.assertEqual([check["name"] for check in plan["checks"]], ["target_resolved", "detail_read", "action_available"])
            self.assertTrue(all(check["passed"] for check in plan["checks"]))
            self.assertEqual(plan["action"], {"code": "ContinueSubmit", "label": "提交", "risk": "high"})
            self.assertFalse(plan["safety"]["will_execute"])
            self.assertEqual(plan["missing"], [])
            self.assertEqual(audit_rows[0]["precheck"]["status"], "passed")
            self.assertNotIn("approved", json.dumps(audit_rows[0], ensure_ascii=False))

    def test_run_oa_write_preflight_reports_ready_without_executing(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []
            responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "items": [
                                {
                                    "title": "Weekly report",
                                    "affair_id": "affair-1",
                                    "href": "http://oa.example.test/detail?affairId=affair-1",
                                }
                            ]
                        },
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "title": "Weekly report",
                            "url": "http://oa.example.test/detail?affairId=affair-1",
                            "actions": [{"code": "ContinueSubmit", "label": "Submit", "risk": "high"}],
                        },
                    },
                ),
            ]

            def fake_nested(command, args, timeout_seconds):
                calls.append((command, args, timeout_seconds))
                return responses.pop(0)

            state._run_nested_oa_command = fake_nested

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "write_preflight",
                    "args": {
                        "type": "pending",
                        "affair_id": "affair-1",
                        "action": "ContinueSubmit",
                        "opinion": "approved",
                    },
                    "timeout_seconds": 1,
                },
            )

            audit_path = Path(tmp) / "audit" / "oa-write-plans.jsonl"
            audit_rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
            result = response.body["result"]
            self.assertEqual(response.status, 200)
            self.assertTrue(response.body["ok"])
            self.assertEqual([call[0] for call in calls], ["pending_list_api", "detail_read"])
            self.assertEqual(result["schema_version"], "bscli.oa_write_preflight.v1")
            self.assertEqual(result["decision"]["status"], "ready_for_execute")
            self.assertTrue(result["decision"]["execute_allowed"])
            self.assertTrue(result["decision"]["requires_confirmation"])
            self.assertFalse(result["plan"]["safety"]["will_execute"])
            self.assertNotIn("approved", json.dumps(result, ensure_ascii=False))
            self.assertNotIn("approved", json.dumps(audit_rows[0], ensure_ascii=False))
            self.assertEqual(state.bridge.pending_tasks, [])
            self.assertIn("--confirm", result["execution_contract"]["execute_command_template"])

    def test_run_oa_write_preflight_reports_dry_run_only_for_archive(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "items": [
                                {
                                    "title": "Contract archive",
                                    "affair_id": "archive-1",
                                    "href": "http://oa.example.test/detail?affairId=archive-1",
                                }
                            ]
                        },
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "title": "Contract archive",
                            "url": "http://oa.example.test/detail?affairId=archive-1",
                            "actions": [{"code": "Archive", "label": "Archive", "risk": "high"}],
                        },
                    },
                ),
            ]
            state._run_nested_oa_command = lambda *_args: responses.pop(0)

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "write_preflight",
                    "args": {"affair_id": "archive-1", "action": "Archive"},
                    "timeout_seconds": 1,
                },
            )

            result = response.body["result"]
            self.assertEqual(response.status, 200)
            self.assertEqual(result["decision"]["status"], "dry_run_only")
            self.assertFalse(result["decision"]["execute_allowed"])
            self.assertTrue(result["decision"]["dry_run_allowed"])
            self.assertEqual(result["decision"]["verification_method"], "not_promoted")
            self.assertIn("execute not promoted for Archive", result["decision"]["blocked_reasons"])
            self.assertEqual(result["execution_contract"]["execute_command_template"], "")
            self.assertFalse(result["probe_policy"]["automatic_network_probe"])
            self.assertEqual(state.bridge.pending_tasks, [])

    def test_run_oa_write_preflight_reports_blocked_when_action_missing(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "items": [
                                {
                                    "title": "Weekly report",
                                    "affair_id": "affair-1",
                                    "href": "http://oa.example.test/detail?affairId=affair-1",
                                }
                            ]
                        },
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "title": "Weekly report",
                            "actions": [{"code": "Track", "label": "Track", "risk": "medium"}],
                        },
                    },
                ),
            ]
            state._run_nested_oa_command = lambda *_args: responses.pop(0)

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "write_preflight",
                    "args": {"affair_id": "affair-1", "action": "ContinueSubmit"},
                    "timeout_seconds": 1,
                },
            )

            result = response.body["result"]
            self.assertEqual(response.status, 200)
            self.assertTrue(response.body["ok"])
            self.assertEqual(result["decision"]["status"], "blocked")
            self.assertFalse(result["decision"]["execute_allowed"])
            self.assertIn("target action is not available", result["decision"]["blocked_reasons"][0])
            self.assertEqual(result["plan"]["precheck"]["status"], "blocked")
            self.assertEqual(result["execution_contract"]["execute_command_template"], "")
            self.assertEqual(state.bridge.pending_tasks, [])

    def test_run_oa_write_prepare_builds_evidence_and_preflight_packet_without_executing(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []

            def fake_nested(command, args, timeout_seconds):
                calls.append((command, args, timeout_seconds))
                if command == "workflow_evidence":
                    return DaemonResponse(
                        200,
                        {
                            "ok": True,
                            "result": {
                                "source_item": {
                                    "title": "Big data expert notice",
                                    "affair_id": "affair-1",
                                },
                                "evidence": {
                                    "identity": {
                                        "title": "Big data expert notice",
                                        "affair_id": "affair-1",
                                    },
                                    "body": {
                                        "text_excerpt": "Please review expert pool notice",
                                        "text_length": 32,
                                        "truncated": False,
                                    },
                                    "actions": {
                                        "count": 1,
                                        "high_risk_count": 1,
                                        "codes": ["ContinueSubmit"],
                                    },
                                    "attention_signals": ["has_candidate_actions"],
                                },
                                "read_effect": {"detail_page_opened": True, "may_mark_read": True},
                            },
                        },
                    )
                if command == "write_preflight":
                    return DaemonResponse(
                        200,
                        {
                            "ok": True,
                            "result": {
                                "schema_version": "bscli.oa_write_preflight.v1",
                                "target": {"affair_id": "affair-1"},
                                "action": {"code": "ContinueSubmit", "risk": "high"},
                                "decision": {
                                    "status": "ready_for_execute",
                                    "execute_allowed": True,
                                    "requires_confirmation": True,
                                    "verification_method": "pending_disappearance",
                                    "blocked_reasons": [],
                                },
                                "execution_contract": {
                                    "will_execute": False,
                                    "execute_command_template": "oa write execute --affair-id affair-1 --action ContinueSubmit --opinion <opinion> --confirm",
                                },
                                "plan": {
                                    "target": {"affair_id": "affair-1"},
                                    "opinion": {"length": 8},
                                    "safety": {"will_execute": False},
                                },
                            },
                        },
                    )
                raise AssertionError(command)

            state._run_nested_oa_command = fake_nested

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "write_prepare",
                    "args": {
                        "type": "pending",
                        "affair_id": "affair-1",
                        "action": "ContinueSubmit",
                        "opinion": "approved",
                        "text_limit": 200,
                    },
                    "timeout_seconds": 1,
                },
            )

            result = response.body["result"]
            self.assertEqual(response.status, 200)
            self.assertTrue(response.body["ok"])
            self.assertEqual([call[0] for call in calls], ["workflow_evidence", "write_preflight"])
            self.assertEqual(calls[0][1], {"type": "pending", "id": "affair-1", "text_limit": 200})
            self.assertEqual(calls[1][1]["action"], "ContinueSubmit")
            self.assertEqual(result["schema_version"], "bscli.oa_write_prepare.v1")
            self.assertEqual(result["preflight"]["decision"]["status"], "ready_for_execute")
            self.assertEqual(result["next_steps"]["status"], "needs_human_confirmation")
            self.assertIn("--confirm", result["next_steps"]["execute_command_template"])
            self.assertNotIn("approved", json.dumps(result, ensure_ascii=False))
            self.assertEqual(state.bridge.pending_tasks, [])

    def test_run_oa_matter_preflight_maps_business_intent_without_executing(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []

            def fake_nested(command, args, timeout_seconds):
                calls.append((command, args, timeout_seconds))
                if command == "workflow_list":
                    return DaemonResponse(
                        200,
                        {
                            "ok": True,
                            "result": {
                                "type": "pending",
                                "items": [
                                    {
                                        "title": "(自动发起)【综合】周报发送流程-25周",
                                        "affair_id": "affair-1",
                                        "href": "http://oa.example.test/detail?affairId=affair-1",
                                    }
                                ],
                            },
                        },
                    )
                if command == "workflow_evidence":
                    return DaemonResponse(
                        200,
                        {
                            "ok": True,
                            "result": {
                                "type": "pending",
                                "source_item": {
                                    "title": "(自动发起)【综合】周报发送流程-25周",
                                    "affair_id": "affair-1",
                                    "href": "http://oa.example.test/detail?affairId=affair-1",
                                },
                                "evidence": {
                                    "identity": {
                                        "title": "(自动发起)【综合】周报发送流程-25周",
                                        "affair_id": "affair-1",
                                        "url": "http://oa.example.test/detail?affairId=affair-1",
                                    },
                                    "actions": {
                                        "codes": ["ContinueSubmit", "Archive"],
                                        "items": [
                                            {
                                                "code": "ContinueSubmit",
                                                "label": "提交",
                                                "risk": "high",
                                            },
                                            {
                                                "code": "Archive",
                                                "label": "处理后归档",
                                                "risk": "high",
                                            },
                                        ],
                                    },
                                },
                                "read_effect": {"detail_page_opened": True, "may_mark_read": True},
                            },
                        },
                    )
                raise AssertionError(command)

            state._run_nested_oa_command = fake_nested

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "matter_preflight",
                    "args": {"keyword": "周报", "intent": "approve", "opinion": "已阅", "limit": 1},
                    "timeout_seconds": 1,
                },
            )

            result = response.body["result"]
            self.assertEqual(response.status, 200)
            self.assertTrue(response.body["ok"])
            self.assertEqual([call[0] for call in calls], ["workflow_list", "workflow_evidence"])
            self.assertEqual(calls[0][1], {"type": "pending", "keyword": "周报", "limit": 1})
            self.assertEqual(calls[1][1], {"type": "pending", "id": "affair-1"})
            self.assertEqual(result["schema_version"], "bscli.oa_matter_intent_preflight.v1")
            self.assertEqual(result["scene"], "received_pending")
            self.assertEqual(result["intent"]["code"], "approve")
            self.assertEqual(result["binding"]["action"], "ContinueSubmit")
            self.assertEqual(result["decision"]["status"], "ready_for_execute")
            self.assertEqual(result["decision"]["verification_method"], "pending_disappearance")
            self.assertFalse(result["execution_contract"]["will_execute"])
            self.assertNotIn("已阅", json.dumps(result, ensure_ascii=False))
            self.assertEqual(state.bridge.pending_tasks, [])

    def test_run_oa_write_dry_run_adds_promotion_evidence_for_archive(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "items": [
                                {
                                    "title": "Contract archive",
                                    "affair_id": "archive-1",
                                    "href": "http://oa.example.test/detail?affairId=archive-1",
                                }
                            ]
                        },
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "title": "Contract archive",
                            "url": "http://oa.example.test/detail?affairId=archive-1",
                            "actions": [
                                {
                                    "code": "Archive",
                                    "label": "处理后归档",
                                    "risk": "high",
                                    "source": "jsonArrBase",
                                }
                            ],
                            "write_hints": {
                                "csrf_tokens": [{"name": "CSRFTOKEN", "value_present": True}],
                                "hidden_fields": [
                                    {"name": "contentAffairId", "value_present": True},
                                    {"name": "summaryId", "value_present": True},
                                ],
                                "endpoint_candidates": [
                                    {
                                        "url": "http://oa.example.test/collaboration/collaboration.do?method=finishWorkItem",
                                        "method": "UNKNOWN",
                                        "risk": "high",
                                        "source": "rendered_html",
                                        "tested": False,
                                    }
                                ],
                            },
                        },
                    },
                ),
            ]
            state._run_nested_oa_command = lambda *_args: responses.pop(0)

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "write_dry_run",
                    "args": {
                        "affair_id": "archive-1",
                        "action": "Archive",
                        "opinion": "",
                    },
                    "timeout_seconds": 1,
                },
            )

            evidence = response.body["result"]["promotion"]["evidence"]
            self.assertEqual(response.status, 200)
            self.assertEqual(evidence["action_present"], True)
            self.assertEqual(evidence["available_action"]["source"], "jsonArrBase")
            self.assertEqual(evidence["write_hints"]["hidden_fields"][0]["name"], "contentAffairId")
            self.assertEqual(evidence["write_hints"]["endpoint_candidates"][0]["tested"], False)
            self.assertFalse(evidence["execute_allowed"])
            self.assertEqual(evidence["verification_method"], "not_promoted")
            self.assertIn("post-write verification method", evidence["missing_for_execute"])

    def test_run_oa_write_endpoint_candidates_classifies_archive_hints_without_calling_them(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []
            responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "items": [
                                {
                                    "title": "Contract archive",
                                    "affair_id": "archive-1",
                                    "href": "http://oa.example.test/detail?affairId=archive-1",
                                }
                            ]
                        },
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "title": "Contract archive",
                            "url": "http://oa.example.test/detail?affairId=archive-1",
                            "actions": [{"code": "Archive", "label": "处理后归档", "risk": "high"}],
                            "write_hints": {
                                "endpoint_candidates": [
                                    {
                                        "url": "http://oa.example.test/seeyon/supervise/supervise.do?method=saveOrUpdateSupervise",
                                        "method": "UNKNOWN",
                                        "risk": "high",
                                        "source": "rendered_html",
                                        "tested": False,
                                    },
                                    {
                                        "url": "http://oa.example.test/seeyon/collaboration/collaboration.do?method=finishWorkItem&operation=archive",
                                        "method": "UNKNOWN",
                                        "risk": "high",
                                        "source": "rendered_html",
                                        "tested": False,
                                    },
                                ],
                            },
                        },
                    },
                ),
            ]

            def fake_nested(command, args, timeout_seconds):
                calls.append((command, args, timeout_seconds))
                return responses.pop(0)

            state._run_nested_oa_command = fake_nested

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "write_endpoint_candidates",
                    "args": {"affair_id": "archive-1", "action": "Archive"},
                    "timeout_seconds": 1,
                },
            )

            result = response.body["result"]
            candidates = result["endpoint_candidates"]
            self.assertEqual(response.status, 200)
            self.assertTrue(response.body["ok"])
            self.assertEqual([call[0] for call in calls], ["pending_list_api", "detail_read"])
            self.assertEqual(result["action"], {"code": "Archive", "label": "处理后归档", "risk": "high"})
            self.assertEqual(candidates[0]["classification"], "auxiliary_supervise")
            self.assertEqual(candidates[0]["relation_to_action"], "unlikely_direct_archive")
            self.assertFalse(candidates[0]["safe_to_call"])
            self.assertEqual(candidates[1]["classification"], "possible_archive_completion")
            self.assertEqual(candidates[1]["relation_to_action"], "possible")
            self.assertFalse(candidates[1]["safe_to_call"])
            self.assertEqual(result["probe_policy"]["automatic_network_probe"], "disabled")
            self.assertIn("no endpoint candidate was called", result["probe_policy"]["reason"])

    def test_run_oa_write_dry_run_blocks_when_target_action_is_missing(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "items": [
                                {
                                    "title": "Weekly report",
                                    "affair_id": "affair-1",
                                    "href": "http://oa.example.test/detail?affairId=affair-1",
                                }
                            ]
                        },
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "title": "Weekly report",
                            "actions": [{"code": "Track", "label": "跟踪", "risk": "medium"}],
                        },
                    },
                ),
            ]

            state._run_nested_oa_command = lambda *_args: responses.pop(0)

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "write_dry_run",
                    "args": {
                        "affair_id": "affair-1",
                        "action": "ContinueSubmit",
                        "opinion": "approved",
                    },
                    "timeout_seconds": 1,
                },
            )

            plan = response.body["result"]
            self.assertEqual(response.status, 409)
            self.assertFalse(response.body["ok"])
            self.assertIn("target action is not available", response.body["error"])
            self.assertEqual(plan["precheck"]["status"], "blocked")
            self.assertIn("action:ContinueSubmit", plan["missing"])
            self.assertEqual(plan["checks"][-1]["name"], "action_available")
            self.assertFalse(plan["checks"][-1]["passed"])
            self.assertEqual(state.bridge.pending_tasks, [])

    def test_run_oa_write_execute_without_confirm_stays_blocked(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "write_execute",
                    "args": {
                        "affair_id": "affair-1",
                        "action": "ContinueSubmit",
                        "opinion": "approved",
                    },
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 409)
            self.assertEqual(response.body["ok"], False)
            self.assertEqual(response.body["result"]["mode"], "execute")
            self.assertEqual(response.body["result"]["request"]["status"], "blocked")
            self.assertFalse(response.body["result"]["safety"]["will_execute"])
            self.assertEqual(state.bridge.pending_tasks, [])
            self.assertEqual(state.trace_store.list_runs()[0]["status"], "error")

    def test_run_oa_write_execute_with_confirm_dispatches_extension_task(self):
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

            nested_responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "title": "Weekly report",
                            "actions": [{"code": "ContinueSubmit", "label": "提交", "risk": "high"}],
                        },
                    },
                ),
                DaemonResponse(200, {"ok": True, "result": {"items": []}}),
            ]
            state._run_nested_oa_command = lambda *_args: nested_responses.pop(0)
            seen_tasks = []

            def extension_worker():
                deadline = time.time() + 2
                tasks = None
                while time.time() < deadline:
                    tasks = state.handle("GET", "/extension/tasks", query={"client_id": "chrome-1"})
                    if tasks.body["tasks"]:
                        break
                    time.sleep(0.02)
                seen_tasks.extend(tasks.body["tasks"])
                task_id = tasks.body["tasks"][0]["id"]
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": task_id,
                        "ok": True,
                        "result": {"submitted": True, "affair_id": "affair-1"},
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "write_execute",
                    "args": {
                        "affair_id": "affair-1",
                        "action": "ContinueSubmit",
                        "opinion": "approved",
                        "source_url": "http://oa.example.test/detail?affairId=affair-1",
                        "confirm": True,
                    },
                    "timeout_seconds": 2,
                },
            )
            worker.join()

            audit_path = Path(tmp) / "audit" / "oa-write-plans.jsonl"
            audit_rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(response.status, 200)
            self.assertTrue(response.body["ok"])
            self.assertEqual(response.body["result"]["submitted"], True)
            self.assertEqual(seen_tasks[0]["kind"], "seeyon_write_execute")
            self.assertEqual(seen_tasks[0]["payload"]["affair_id"], "affair-1")
            self.assertEqual(seen_tasks[0]["payload"]["action"], "ContinueSubmit")
            self.assertEqual(seen_tasks[0]["payload"]["opinion"], "approved")
            self.assertTrue(seen_tasks[0]["payload"]["confirm"])
            self.assertEqual(len(audit_rows), 1)
            self.assertTrue(audit_rows[0]["safety"]["will_execute"])
            self.assertFalse(audit_rows[0]["safety"]["dry_run_only"])
            self.assertNotIn("approved", json.dumps(audit_rows[0], ensure_ascii=False))
            self.assertEqual(state.trace_store.list_runs()[0]["status"], "ok")

    def test_run_oa_write_execute_with_confirm_prechecks_and_verifies_disappearance(self):
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
            nested_calls = []
            nested_responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "title": "Weekly report",
                            "actions": [{"code": "ContinueSubmit", "label": "提交", "risk": "high"}],
                        },
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "run_id": "run-verify",
                        "task_id": "task-verify",
                        "result": {"items": []},
                    },
                ),
            ]

            def fake_nested(command, args, timeout_seconds):
                nested_calls.append((command, args, timeout_seconds))
                return nested_responses.pop(0)

            state._run_nested_oa_command = fake_nested
            seen_tasks = []

            def extension_worker():
                deadline = time.time() + 2
                tasks = None
                while time.time() < deadline:
                    tasks = state.handle("GET", "/extension/tasks", query={"client_id": "chrome-1"})
                    if tasks.body["tasks"]:
                        break
                    time.sleep(0.02)
                seen_tasks.extend(tasks.body["tasks"])
                task_id = tasks.body["tasks"][0]["id"]
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": task_id,
                        "ok": True,
                        "result": {"submitted": True, "affair_id": "affair-1"},
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "write_execute",
                    "args": {
                        "affair_id": "affair-1",
                        "action": "ContinueSubmit",
                        "opinion": "approved",
                        "source_url": "http://oa.example.test/detail?affairId=affair-1",
                        "confirm": True,
                    },
                    "timeout_seconds": 2,
                },
            )
            worker.join()

            audit_path = Path(tmp) / "audit" / "oa-write-verifications.jsonl"
            audit_rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(response.status, 200)
            self.assertTrue(response.body["ok"])
            self.assertEqual([call[0] for call in nested_calls], ["detail_read", "pending_list_api"])
            self.assertEqual(nested_calls[0][1], {"url": "http://oa.example.test/detail?affairId=affair-1"})
            self.assertEqual(seen_tasks[0]["kind"], "seeyon_write_execute")
            self.assertEqual(response.body["result"]["verification"]["status"], "disappeared")
            self.assertTrue(response.body["result"]["verification"]["verified"])
            self.assertEqual(audit_rows[0]["verification"]["status"], "disappeared")

    def test_run_oa_write_execute_reports_error_when_item_is_still_pending_after_submit(self):
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
            nested_responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "title": "Weekly report",
                            "actions": [{"code": "ContinueSubmit", "label": "提交", "risk": "high"}],
                        },
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "items": [
                                {
                                    "title": "Weekly report",
                                    "affair_id": "affair-1",
                                    "href": "http://oa.example.test/detail?affairId=affair-1",
                                }
                            ]
                        },
                    },
                ),
            ]
            state._run_nested_oa_command = lambda *_args: nested_responses.pop(0)

            def extension_worker():
                deadline = time.time() + 2
                tasks = None
                while time.time() < deadline:
                    tasks = state.handle("GET", "/extension/tasks", query={"client_id": "chrome-1"})
                    if tasks.body["tasks"]:
                        break
                    time.sleep(0.02)
                task_id = tasks.body["tasks"][0]["id"]
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": task_id,
                        "ok": True,
                        "result": {"submitted": True, "affair_id": "affair-1"},
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "write_execute",
                    "args": {
                        "affair_id": "affair-1",
                        "action": "ContinueSubmit",
                        "opinion": "approved",
                        "source_url": "http://oa.example.test/detail?affairId=affair-1",
                        "confirm": True,
                    },
                    "timeout_seconds": 2,
                },
            )
            worker.join()

            self.assertEqual(response.status, 502)
            self.assertFalse(response.body["ok"])
            self.assertEqual(response.body["result"]["verification"]["status"], "still_pending")
            self.assertIn("post-submit verification failed", response.body["error"])

    def test_run_oa_write_execute_rejects_empty_extension_result(self):
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

            state._run_nested_oa_command = lambda *_args: DaemonResponse(
                200,
                {
                    "ok": True,
                    "result": {
                        "title": "Weekly report",
                        "actions": [{"code": "ContinueSubmit", "label": "提交", "risk": "high"}],
                    },
                },
            )

            def extension_worker():
                deadline = time.time() + 2
                tasks = None
                while time.time() < deadline:
                    tasks = state.handle("GET", "/extension/tasks", query={"client_id": "chrome-1"})
                    if tasks.body["tasks"]:
                        break
                    time.sleep(0.02)
                task_id = tasks.body["tasks"][0]["id"]
                state.handle(
                    "POST",
                    "/extension/results",
                    body={
                        "client_id": "chrome-1",
                        "task_id": task_id,
                        "ok": True,
                        "result": None,
                    },
                )

            worker = threading.Thread(target=extension_worker)
            worker.start()
            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "write_execute",
                    "args": {
                        "affair_id": "affair-1",
                        "action": "ContinueSubmit",
                        "opinion": "approved",
                        "source_url": "http://oa.example.test/detail?affairId=affair-1",
                        "confirm": True,
                    },
                    "timeout_seconds": 2,
                },
            )
            worker.join()

            self.assertEqual(response.status, 502)
            self.assertFalse(response.body["ok"])
            self.assertEqual(response.body["error"], "extension write task returned no submission confirmation")
            self.assertEqual(state.trace_store.list_runs()[0]["status"], "error")

    def test_run_oa_pending_submit_executes_each_item_and_records_verification_audit(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []
            responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "items": [
                                {
                                    "title": "Weekly 24",
                                    "affair_id": "a24",
                                    "href": "http://oa.example.test/detail?affairId=a24",
                                },
                                {
                                    "title": "Weekly 23",
                                    "affair_id": "a23",
                                    "href": "http://oa.example.test/detail?affairId=a23",
                                },
                            ]
                        },
                    },
                ),
                DaemonResponse(200, {"ok": True, "result": {"actions": [{"code": "ContinueSubmit"}]}}),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "task_id": "submit-24",
                        "run_id": "run-submit-24",
                        "result": {"submitted": True, "affair_id": "a24"},
                    },
                ),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "run_id": "run-verify-24",
                        "result": {
                            "items": [
                                {
                                    "title": "Weekly 23",
                                    "affair_id": "a23",
                                    "href": "http://oa.example.test/detail?affairId=a23",
                                }
                            ]
                        },
                    },
                ),
                DaemonResponse(200, {"ok": True, "result": {"actions": [{"code": "ContinueSubmit"}]}}),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "task_id": "submit-23",
                        "run_id": "run-submit-23",
                        "result": {"submitted": True, "affair_id": "a23"},
                    },
                ),
                DaemonResponse(200, {"ok": True, "run_id": "run-verify-23", "result": {"items": []}}),
            ]

            def fake_nested(command, args, timeout_seconds):
                calls.append((command, args, timeout_seconds))
                return responses.pop(0)

            state._run_nested_oa_command = fake_nested

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "pending_submit",
                    "args": {
                        "keyword": "Weekly",
                        "action": "ContinueSubmit",
                        "opinion": "approved",
                        "limit": 2,
                        "confirm": True,
                        "verify_wait": 0,
                    },
                    "timeout_seconds": 1,
                },
            )

            audit_path = Path(tmp) / "audit" / "oa-write-verifications.jsonl"
            audit_rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(response.status, 200)
            self.assertTrue(response.body["ok"])
            self.assertEqual(response.body["result"]["target_count"], 2)
            self.assertEqual(response.body["result"]["submitted_count"], 2)
            self.assertFalse(response.body["result"]["stopped"])
            self.assertEqual(response.body["result"]["items"][0]["verification"]["status"], "disappeared")
            self.assertEqual(
                [call[0] for call in calls],
                [
                    "pending_list_api",
                    "detail_read",
                    "write_execute",
                    "pending_list_api",
                    "detail_read",
                    "write_execute",
                    "pending_list_api",
                ],
            )
            self.assertEqual(calls[2][1]["affair_id"], "a24")
            self.assertEqual(calls[2][1]["opinion"], "approved")
            self.assertTrue(calls[2][1]["confirm"])
            self.assertEqual(len(audit_rows), 2)
            self.assertEqual(audit_rows[0]["target"]["affair_id"], "a24")
            self.assertEqual(audit_rows[0]["verification"]["status"], "disappeared")
            self.assertNotIn("approved", json.dumps(audit_rows, ensure_ascii=False))
            self.assertEqual(state.trace_store.list_runs()[0]["status"], "ok")

    def test_run_oa_pending_submit_requires_confirm_before_nested_calls(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            calls = []
            state._run_nested_oa_command = lambda *args: calls.append(args)

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "pending_submit",
                    "args": {
                        "keyword": "Weekly",
                        "action": "ContinueSubmit",
                        "opinion": "approved",
                    },
                    "timeout_seconds": 1,
                },
            )

            self.assertEqual(response.status, 409)
            self.assertFalse(response.body["ok"])
            self.assertTrue(response.body["requires_confirmation"])
            self.assertEqual(calls, [])
            self.assertEqual(state.trace_store.list_runs()[0]["status"], "error")

    def test_run_oa_pending_submit_stops_when_post_submit_verification_fails(self):
        with TemporaryDirectory() as tmp:
            state = DaemonState(ConfigStore(Path(tmp)))
            responses = [
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "items": [
                                {
                                    "title": "Weekly 24",
                                    "affair_id": "a24",
                                    "href": "http://oa.example.test/detail?affairId=a24",
                                }
                            ]
                        },
                    },
                ),
                DaemonResponse(200, {"ok": True, "result": {"actions": [{"code": "ContinueSubmit"}]}}),
                DaemonResponse(
                    200,
                    {
                        "ok": True,
                        "task_id": "submit-24",
                        "result": {"submitted": True, "affair_id": "a24"},
                    },
                ),
                DaemonResponse(500, {"ok": False, "error": "pending list failed"}),
            ]

            state._run_nested_oa_command = lambda *_args: responses.pop(0)

            response = state.handle(
                "POST",
                "/commands/run",
                body={
                    "system": "oa",
                    "command": "pending_submit",
                    "args": {
                        "keyword": "Weekly",
                        "action": "ContinueSubmit",
                        "opinion": "approved",
                        "confirm": True,
                        "verify_wait": 0,
                    },
                    "timeout_seconds": 1,
                },
            )

            audit_path = Path(tmp) / "audit" / "oa-write-verifications.jsonl"
            audit_rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(response.status, 200)
            self.assertFalse(response.body["ok"])
            self.assertEqual(response.body["result"]["submitted_count"], 0)
            self.assertTrue(response.body["result"]["stopped"])
            self.assertEqual(response.body["result"]["items"][0]["verification"]["status"], "verify_failed")
            self.assertFalse(response.body["result"]["items"][0]["verification"]["verified"])
            self.assertIn("pending list failed", response.body["result"]["items"][0]["verification"]["error"])
            self.assertEqual(audit_rows[0]["verification"]["status"], "verify_failed")


if __name__ == "__main__":
    unittest.main()
