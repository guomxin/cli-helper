import asyncio
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bscli.core.config import ConfigStore, SystemProfile
from bscli.core.registry import CommandDefinition, CommandRegistry
from bscli.core.runtime import RuntimeContext, RuntimeEngine
from bscli.core.trace import TraceStore


class CoreTests(unittest.TestCase):
    def test_system_profile_requires_base_url_inside_allowed_origins(self):
        profile = SystemProfile(
            id="oa",
            name="Seeyon OA",
            base_url="http://10.10.50.110/seeyon/main.do?method=main",
            allowed_origins=["http://10.10.50.110"],
        )

        self.assertEqual(profile.base_origin, "http://10.10.50.110")

        with self.assertRaisesRegex(ValueError, "base_url origin"):
            SystemProfile(
                id="bad",
                name="Bad",
                base_url="https://example.com/app",
                allowed_origins=["https://other.example.com"],
            )

    def test_config_store_persists_system_profiles(self):
        with TemporaryDirectory() as tmp:
            store = ConfigStore(Path(tmp) / "config")
            profile = SystemProfile(
                id="oa",
                name="Seeyon OA",
                base_url="http://10.10.50.110/seeyon/main.do?method=main",
                allowed_origins=["http://10.10.50.110"],
            )

            store.save_system(profile)

            loaded = store.load_system("oa")
            self.assertEqual(loaded, profile)
            self.assertEqual(store.list_systems(), [profile])

    def test_registry_rejects_unknown_strategy_and_write_without_confirmation(self):
        registry = CommandRegistry()

        with self.assertRaisesRegex(ValueError, "unsupported strategy"):
            registry.register(
                CommandDefinition(
                    system="oa",
                    name="bad_strategy",
                    access="read",
                    strategy="made_up",
                    args_schema={},
                )
            )

        with self.assertRaisesRegex(ValueError, "write command"):
            registry.register(
                CommandDefinition(
                    system="oa",
                    name="submit",
                    access="write",
                    strategy="ui_workflow",
                    args_schema={},
                )
            )

    def test_trace_store_records_run_lifecycle(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "trace.db"
            trace_store = TraceStore(db_path)

            run_id = trace_store.start_run(
                system="oa",
                command="search_employee",
                args={"keyword": "张三"},
                access="read",
                strategy="daemon_api",
            )
            trace_store.finish_run(run_id, status="ok", result={"count": 1})

            run = trace_store.get_run(run_id)
            self.assertEqual(run["status"], "ok")
            self.assertEqual(run["result"], {"count": 1})

            conn = sqlite3.connect(db_path)
            try:
                count = conn.execute("select count(*) from runs").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(count, 1)

    def test_runtime_executes_daemon_api_command_and_traces(self):
        asyncio.run(self._runtime_executes_daemon_api_command_and_traces())

    async def _runtime_executes_daemon_api_command_and_traces(self):
        with TemporaryDirectory() as tmp:
            registry = CommandRegistry()
            registry.register(
                CommandDefinition(
                    system="oa",
                    name="search_employee",
                    access="read",
                    strategy="daemon_api",
                    args_schema={"keyword": {"type": "string", "required": True}},
                    api={
                        "method": "POST",
                        "path": "/api/hr/employees/search",
                        "body": {"keyword": "{{keyword}}"},
                    },
                    verify={"type": "json_path", "path": "$.data"},
                )
            )
            http = FakeHttpClient()
            trace_store = TraceStore(Path(tmp) / "trace.db")
            engine = RuntimeEngine(registry=registry, trace_store=trace_store)

            result = await engine.run(
                RuntimeContext(system="oa", http=http),
                command_name="search_employee",
                args={"keyword": "张三"},
            )

            self.assertEqual(
                result,
                {
                    "data": [
                        {"name": "张三", "department": "研发部", "phone": "10086"},
                    ]
                },
            )
            self.assertEqual(
                http.requests,
                [
                    {
                        "method": "POST",
                        "path": "/api/hr/employees/search",
                        "json_body": {"keyword": "张三"},
                        "headers": {},
                    }
                ],
            )

            traces = trace_store.list_runs()
            self.assertEqual(len(traces), 1)
            self.assertEqual(traces[0]["status"], "ok")
            self.assertEqual(json.loads(traces[0]["args_json"]), {"keyword": "张三"})


class FakeHttpClient:
    def __init__(self):
        self.requests = []

    async def request(self, method, path, *, json_body=None, headers=None):
        self.requests.append(
            {
                "method": method,
                "path": path,
                "json_body": json_body,
                "headers": headers or {},
            }
        )
        return {
            "data": [
                {"name": "张三", "department": "研发部", "phone": "10086"},
            ]
        }


if __name__ == "__main__":
    unittest.main()
