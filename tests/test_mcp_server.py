import json
import unittest

from bscli.adapters.seeyon import register_seeyon_commands
from bscli.core.discovered import DiscoveredApi
from bscli.core.registry import CommandRegistry
from bscli.mcp.server import BscliMcpServer


class McpServerTests(unittest.TestCase):
    def test_list_tools_returns_mcp_tool_definitions(self):
        server = self._server()

        result = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            }
        )

        self.assertEqual(result["id"], 1)
        tools = {tool["name"]: tool for tool in result["result"]["tools"]}
        self.assertIn("oa__pending_list", tools)
        self.assertIn("oa__session_status", tools)
        self.assertEqual(tools["oa__session_status"]["inputSchema"]["required"], [])
        self.assertEqual(
            tools["oa__template_detail"]["inputSchema"],
            {
                "type": "object",
                "properties": {"template_id": {"type": "string"}},
                "required": ["template_id"],
                "additionalProperties": False,
            },
        )
        self.assertEqual(
            tools["oa__template_detail"]["annotations"],
            {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        )

    def test_call_tool_maps_mcp_tool_to_bscli_command(self):
        calls = []

        def runner(system, command, arguments):
            calls.append((system, command, arguments))
            return {"found": True, "item": {"template_id": arguments["template_id"]}}

        server = self._server(runner=runner)

        result = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "oa__template_detail",
                    "arguments": {"template_id": "-6511139737225050501"},
                },
            }
        )

        self.assertEqual(calls, [("oa", "template_detail", {"template_id": "-6511139737225050501"})])
        self.assertEqual(result["id"], 2)
        self.assertEqual(result["result"]["structuredContent"]["found"], True)
        self.assertEqual(
            json.loads(result["result"]["content"][0]["text"]),
            {"found": True, "item": {"template_id": "-6511139737225050501"}},
        )

    def test_unknown_tool_returns_jsonrpc_error(self):
        server = self._server()

        result = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "oa__missing", "arguments": {}},
            }
        )

        self.assertEqual(result["error"]["code"], -32602)
        self.assertIn("Unknown BSCLI MCP tool", result["error"]["message"])

    def test_call_tool_rejects_missing_required_argument(self):
        calls = []
        server = self._server(runner=lambda *args: calls.append(args) or {})

        result = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "oa__template_detail", "arguments": {}},
            }
        )

        self.assertEqual(calls, [])
        self.assertEqual(result["error"]["code"], -32602)
        self.assertIn("Missing required argument 'template_id'", result["error"]["message"])

    def test_call_tool_rejects_unknown_argument(self):
        calls = []
        server = self._server(runner=lambda *args: calls.append(args) or {})

        result = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "oa__template_detail",
                    "arguments": {
                        "template_id": "-6511139737225050501",
                        "unexpected": "value",
                    },
                },
            }
        )

        self.assertEqual(calls, [])
        self.assertEqual(result["error"]["code"], -32602)
        self.assertIn("Unexpected argument 'unexpected'", result["error"]["message"])

    def test_call_tool_rejects_wrong_argument_type(self):
        calls = []
        server = self._server(runner=lambda *args: calls.append(args) or {})

        result = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "oa__template_detail",
                    "arguments": {"template_id": 123},
                },
            }
        )

        self.assertEqual(calls, [])
        self.assertEqual(result["error"]["code"], -32602)
        self.assertIn("Argument 'template_id' must be string", result["error"]["message"])

    def test_call_tool_runner_failure_returns_tool_error_result(self):
        def runner(_system, _command, _arguments):
            raise RuntimeError("no Chrome extension client connected")

        server = self._server(runner=runner)

        result = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "oa__pending_list", "arguments": {}},
            }
        )

        self.assertNotIn("error", result)
        self.assertEqual(result["id"], 7)
        self.assertTrue(result["result"]["isError"])
        self.assertEqual(
            result["result"]["structuredContent"]["error"],
            "no Chrome extension client connected",
        )
        self.assertIn("Start the BSCLI daemon", result["result"]["content"][0]["text"])

    def test_list_tools_includes_discovered_tools(self):
        server = self._server(discovered_apis=[self._discovered_template_api()])

        result = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/list",
                "params": {},
            }
        )

        tools = {tool["name"]: tool for tool in result["result"]["tools"]}
        self.assertIn("oa__discovered__template_section", tools)
        self.assertEqual(
            tools["oa__discovered__template_section"]["inputSchema"],
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        )
        self.assertEqual(
            tools["oa__discovered__template_section"]["annotations"],
            {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        )

    def test_call_discovered_tool_maps_to_discovered_run(self):
        calls = []

        def runner(system, command, arguments):
            calls.append((system, command, arguments))
            return {"api": {"name": arguments["name"]}}

        server = self._server(
            runner=runner,
            discovered_apis=[self._discovered_template_api()],
        )

        result = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": "oa__discovered__template_section",
                    "arguments": {},
                },
            }
        )

        self.assertEqual(calls, [("oa", "discovered_run", {"name": "template-section"})])
        self.assertEqual(result["result"]["structuredContent"], {"api": {"name": "template-section"}})

    def test_call_parameterized_discovered_tool_passes_arguments(self):
        calls = []

        def runner(system, command, arguments):
            calls.append((system, command, arguments))
            return {"api": {"name": arguments["name"]}, "keyword": arguments["keyword"]}

        server = self._server(
            runner=runner,
            discovered_apis=[
                DiscoveredApi(
                    system="oa",
                    name="search",
                    description="Search OA records",
                    access="read",
                    risk="low",
                    request={"method": "GET", "url": "http://oa.example.test/ajax.do?q={{keyword}}"},
                    parameters={"keyword": {"type": "string", "required": True}},
                    inspection={"data_shape": "Data.items[]"},
                    path=None,
                    raw={},
                )
            ],
        )

        result = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 18,
                "method": "tools/call",
                "params": {
                    "name": "oa__discovered__search",
                    "arguments": {"keyword": "budget"},
                },
            }
        )

        self.assertEqual(calls, [("oa", "discovered_run", {"name": "search", "keyword": "budget"})])
        self.assertEqual(result["result"]["structuredContent"]["keyword"], "budget")

    def test_call_discovered_tool_rejects_extra_arguments(self):
        calls = []
        server = self._server(
            runner=lambda *args: calls.append(args) or {},
            discovered_apis=[self._discovered_template_api()],
        )

        result = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "oa__discovered__template_section",
                    "arguments": {"unexpected": "value"},
                },
            }
        )

        self.assertEqual(calls, [])
        self.assertEqual(result["error"]["code"], -32602)
        self.assertIn("Unexpected argument 'unexpected'", result["error"]["message"])

    def test_risky_discovered_tool_requires_confirm_argument(self):
        calls = []
        server = self._server(
            runner=lambda *args: calls.append(args) or {},
            discovered_apis=[self._discovered_submit_api()],
        )

        listed = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/list",
                "params": {},
            }
        )
        tools = {tool["name"]: tool for tool in listed["result"]["tools"]}
        self.assertEqual(
            tools["oa__discovered__submit"]["inputSchema"]["required"],
            ["confirm"],
        )

        result = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {
                    "name": "oa__discovered__submit",
                    "arguments": {},
                },
            }
        )

        self.assertEqual(calls, [])
        self.assertEqual(result["error"]["code"], -32602)
        self.assertIn("Missing required argument 'confirm'", result["error"]["message"])

    def test_call_risky_discovered_tool_passes_confirm_to_discovered_run(self):
        calls = []

        def runner(system, command, arguments):
            calls.append((system, command, arguments))
            return {"api": {"name": arguments["name"]}, "confirmed": arguments["confirm"]}

        server = self._server(
            runner=runner,
            discovered_apis=[self._discovered_submit_api()],
        )

        result = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 13,
                "method": "tools/call",
                "params": {
                    "name": "oa__discovered__submit",
                    "arguments": {"confirm": True},
                },
            }
        )

        self.assertEqual(calls, [("oa", "discovered_run", {"name": "submit", "confirm": True})])
        self.assertEqual(result["result"]["structuredContent"]["confirmed"], True)

    def test_call_oa_write_execute_requires_confirm_argument(self):
        calls = []
        server = self._server(runner=lambda *args: calls.append(args) or {})

        result = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 14,
                "method": "tools/call",
                "params": {
                    "name": "oa__write_execute",
                    "arguments": {
                        "affair_id": "affair-1",
                        "action": "ContinueSubmit",
                    },
                },
            }
        )

        self.assertEqual(calls, [])
        self.assertEqual(result["error"]["code"], -32602)
        self.assertIn("Missing required argument 'confirm'", result["error"]["message"])

    def test_call_oa_write_dry_run_maps_to_daemon_command(self):
        calls = []

        def runner(system, command, arguments):
            calls.append((system, command, arguments))
            return {"mode": "dry-run", "safety": {"will_execute": False}}

        server = self._server(runner=runner)

        result = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 15,
                "method": "tools/call",
                "params": {
                    "name": "oa__write_dry_run",
                    "arguments": {
                        "affair_id": "affair-1",
                        "action": "ContinueSubmit",
                        "opinion": "approved",
                    },
                },
            }
        )

        self.assertEqual(
            calls,
            [
                (
                    "oa",
                    "write_dry_run",
                    {
                        "affair_id": "affair-1",
                        "action": "ContinueSubmit",
                        "opinion": "approved",
                    },
                )
            ],
        )
        self.assertFalse(result["result"]["structuredContent"]["safety"]["will_execute"])

    def test_call_oa_pending_submit_maps_to_daemon_command(self):
        calls = []

        def runner(system, command, arguments):
            calls.append((system, command, arguments))
            return {"target_count": 0, "submitted_count": 0, "items": []}

        server = self._server(runner=runner)

        result = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 16,
                "method": "tools/call",
                "params": {
                    "name": "oa__pending_submit",
                    "arguments": {
                        "keyword": "Weekly",
                        "action": "ContinueSubmit",
                        "opinion": "approved",
                        "limit": 3,
                        "confirm": True,
                        "verify_wait": 0,
                    },
                },
            }
        )

        self.assertEqual(
            calls,
            [
                (
                    "oa",
                    "pending_submit",
                    {
                        "keyword": "Weekly",
                        "action": "ContinueSubmit",
                        "opinion": "approved",
                        "limit": 3,
                        "confirm": True,
                        "verify_wait": 0,
                    },
                )
            ],
        )
        self.assertEqual(result["result"]["structuredContent"]["submitted_count"], 0)

    def test_call_oa_pending_submit_requires_confirm_argument(self):
        calls = []
        server = self._server(runner=lambda *args: calls.append(args) or {})

        result = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 17,
                "method": "tools/call",
                "params": {
                    "name": "oa__pending_submit",
                    "arguments": {
                        "keyword": "Weekly",
                        "action": "ContinueSubmit",
                    },
                },
            }
        )

        self.assertEqual(calls, [])
        self.assertEqual(result["error"]["code"], -32602)
        self.assertIn("Missing required argument 'confirm'", result["error"]["message"])

    def _server(self, runner=None, discovered_apis=None):
        registry = CommandRegistry()
        register_seeyon_commands(registry)
        return BscliMcpServer(
            registry,
            command_runner=runner or (lambda *_: {}),
            discovered_apis=discovered_apis,
        )

    def _discovered_template_api(self):
        return DiscoveredApi(
            system="oa",
            name="template-section",
            description="Template section projection",
            access="read",
            risk="low",
            request={"method": "GET", "url": "http://oa.example.test/ajax.do"},
            inspection={
                "data_shape": "Data.items[]",
                "item_count": 36,
                "sample_fields": ["title", "link"],
            },
            path=None,
            raw={},
        )

    def _discovered_submit_api(self):
        return DiscoveredApi(
            system="oa",
            name="submit",
            description="Submit one OA action",
            access="write",
            risk="medium",
            request={"method": "POST", "url": "http://oa.example.test/ajax.do"},
            inspection={"data_shape": "json{}"},
            path=None,
            raw={},
        )


if __name__ == "__main__":
    unittest.main()
