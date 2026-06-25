import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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
