import unittest

from bscli.adapters.seeyon import register_seeyon_commands
from bscli.core.discovered import DiscoveredApi
from bscli.core.registry import CommandRegistry
from bscli.core.tool_manifest import export_tool_manifest


class ToolManifestTests(unittest.TestCase):
    def test_export_tool_manifest_converts_commands_to_agent_tools(self):
        registry = CommandRegistry()
        register_seeyon_commands(registry)

        manifest = export_tool_manifest(registry, system="oa")

        self.assertEqual(manifest["schema_version"], "bscli.tool_manifest.v1")
        tools = {tool["name"]: tool for tool in manifest["tools"]}
        self.assertIn("oa__pending_list", tools)
        self.assertIn("oa__doctor", tools)
        self.assertIn("oa__capability_map", tools)
        self.assertIn("oa__workflow_list", tools)
        self.assertIn("oa__workflow_inspect", tools)
        self.assertIn("oa__workflow_brief", tools)
        self.assertIn("oa__workflow_evidence", tools)
        self.assertIn("oa__workflow_timeline", tools)
        self.assertIn("oa__inbox_analyze", tools)
        self.assertIn("oa__workflow_opinions", tools)
        self.assertIn("oa__template_detail", tools)

        detail = tools["oa__template_detail"]
        self.assertEqual(detail["description"], "Read one Seeyon OA form template metadata from the current home page by template_id.")
        self.assertEqual(
            detail["input_schema"],
            {
                "type": "object",
                "properties": {
                    "template_id": {"type": "string"},
                },
                "required": ["template_id"],
                "additionalProperties": False,
            },
        )
        self.assertEqual(
            detail["metadata"],
            {
                "system": "oa",
                "command": "template_detail",
                "access": "read",
                "risk": "low",
                "strategy": "dom_read",
                "requires_confirmation": False,
            },
        )
        workflow_opinions = tools["oa__workflow_opinions"]
        self.assertEqual(
            workflow_opinions["input_schema"],
            {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "Workflow collection: pending or sent. Defaults to pending."},
                    "id": {"type": "string", "description": "Workflow affair_id to resolve from the selected collection."},
                    "url": {"type": "string", "description": "Rendered OA detail-page URL fallback."},
                    "keyword": {"type": "string", "description": "Keyword used when reading a batch of workflow opinions."},
                    "limit": {"type": "integer"},
                },
                "required": [],
                "additionalProperties": False,
            },
        )
        self.assertEqual(workflow_opinions["metadata"]["command"], "workflow_opinions")
        self.assertEqual(workflow_opinions["metadata"]["access"], "read")

        workflow_inspect = tools["oa__workflow_inspect"]
        self.assertEqual(
            workflow_inspect["input_schema"],
            {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "Workflow collection: pending or sent. Defaults to pending."},
                    "id": {"type": "string", "description": "Workflow affair_id to resolve from the selected collection."},
                    "url": {"type": "string", "description": "Rendered OA detail-page URL fallback."},
                    "include": {"type": "string"},
                    "text_limit": {"type": "integer"},
                },
                "required": [],
                "additionalProperties": False,
            },
        )
        self.assertEqual(workflow_inspect["metadata"]["access"], "read")

        inbox_analyze = tools["oa__inbox_analyze"]
        self.assertEqual(
            inbox_analyze["input_schema"],
            {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "Inbox workflow collection: pending or sent. Defaults to pending."},
                    "keyword": {"type": "string"},
                    "limit": {"type": "integer"},
                    "deep": {"type": "boolean", "description": "Open detail pages for a limited number of items when true."},
                    "deep_limit": {"type": "integer", "description": "Maximum number of detail pages to open in deep mode."},
                    "text_limit": {"type": "integer"},
                },
                "required": [],
                "additionalProperties": False,
            },
        )
        self.assertEqual(inbox_analyze["metadata"]["access"], "read")
        self.assertEqual(inbox_analyze["metadata"]["strategy"], "daemon_api")

        doctor = tools["oa__doctor"]
        self.assertEqual(doctor["input_schema"]["required"], [])
        self.assertEqual(doctor["metadata"]["strategy"], "daemon_api")

    def test_discovered_write_api_manifest_requires_confirm_argument(self):
        api = DiscoveredApi(
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

        manifest = export_tool_manifest(CommandRegistry(), system="oa", discovered_apis=[api])
        tool = manifest["tools"][0]

        self.assertEqual(tool["name"], "oa__discovered__submit")
        self.assertEqual(
            tool["input_schema"],
            {
                "type": "object",
                "properties": {
                    "confirm": {
                        "type": "boolean",
                        "description": "Must be true to explicitly confirm this non-read or higher-risk API call.",
                    }
                },
                "required": ["confirm"],
                "additionalProperties": False,
            },
        )
        self.assertEqual(tool["metadata"]["requires_confirmation"], True)
        self.assertEqual(tool["metadata"]["access"], "write")
        self.assertEqual(tool["metadata"]["risk"], "medium")

    def test_discovered_api_manifest_exports_parameter_schema(self):
        api = DiscoveredApi(
            system="oa",
            name="search",
            description="Search OA records",
            access="read",
            risk="low",
            request={"method": "GET", "url": "http://oa.example.test/ajax.do?q={{keyword}}"},
            parameters={
                "keyword": {
                    "type": "string",
                    "required": True,
                    "description": "Search keyword",
                },
                "page": {"type": "integer"},
            },
            inspection={"data_shape": "Data.items[]"},
            path=None,
            raw={},
        )

        manifest = export_tool_manifest(CommandRegistry(), system="oa", discovered_apis=[api])
        tool = manifest["tools"][0]

        self.assertEqual(tool["name"], "oa__discovered__search")
        self.assertEqual(
            tool["input_schema"],
            {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Search keyword",
                    },
                    "page": {"type": "integer"},
                },
                "required": ["keyword"],
                "additionalProperties": False,
            },
        )

    def test_oa_write_execute_tool_requires_confirm_argument(self):
        registry = CommandRegistry()
        register_seeyon_commands(registry)

        manifest = export_tool_manifest(registry, system="oa")
        tools = {tool["name"]: tool for tool in manifest["tools"]}
        tool = tools["oa__write_execute"]

        self.assertEqual(
            tool["input_schema"]["required"],
            ["affair_id", "action", "confirm"],
        )
        self.assertEqual(tool["input_schema"]["properties"]["confirm"]["type"], "boolean")
        self.assertEqual(tool["metadata"]["access"], "write")
        self.assertEqual(tool["metadata"]["risk"], "high")
        self.assertTrue(tool["metadata"]["requires_confirmation"])

    def test_oa_pending_submit_tool_requires_confirm_argument(self):
        registry = CommandRegistry()
        register_seeyon_commands(registry)

        manifest = export_tool_manifest(registry, system="oa")
        tools = {tool["name"]: tool for tool in manifest["tools"]}
        tool = tools["oa__pending_submit"]

        self.assertEqual(
            tool["input_schema"]["required"],
            ["keyword", "action", "confirm"],
        )
        self.assertEqual(tool["input_schema"]["properties"]["confirm"]["type"], "boolean")
        self.assertEqual(tool["metadata"]["access"], "write")
        self.assertEqual(tool["metadata"]["risk"], "high")
        self.assertEqual(tool["metadata"]["strategy"], "human_gate")
        self.assertTrue(tool["metadata"]["requires_confirmation"])

    def test_oa_write_capabilities_and_meeting_reply_tools_are_exported(self):
        registry = CommandRegistry()
        register_seeyon_commands(registry)

        manifest = export_tool_manifest(registry, system="oa")
        tools = {tool["name"]: tool for tool in manifest["tools"]}

        capabilities = tools["oa__write_capabilities"]
        self.assertEqual(capabilities["metadata"]["access"], "read")
        self.assertEqual(capabilities["metadata"]["risk"], "low")
        self.assertFalse(capabilities["metadata"]["requires_confirmation"])
        self.assertEqual(
            capabilities["input_schema"]["properties"]["type"]["description"],
            "Workflow collection to inspect; currently pending is the supported write target.",
        )

        dry_run = tools["oa__meeting_reply_dry_run"]
        self.assertEqual(dry_run["metadata"]["access"], "read")
        self.assertEqual(dry_run["metadata"]["strategy"], "daemon_api")
        self.assertEqual(dry_run["input_schema"]["required"], ["id"])

        execute = tools["oa__meeting_reply_execute"]
        self.assertEqual(execute["metadata"]["access"], "write")
        self.assertEqual(execute["metadata"]["strategy"], "human_gate")
        self.assertTrue(execute["metadata"]["requires_confirmation"])
        self.assertEqual(execute["input_schema"]["required"], ["id", "confirm"])
        self.assertEqual(execute["input_schema"]["properties"]["confirm"]["type"], "boolean")

        endpoints = tools["oa__write_endpoint_candidates"]
        self.assertEqual(endpoints["metadata"]["access"], "read")
        self.assertEqual(endpoints["metadata"]["risk"], "low")
        self.assertEqual(endpoints["input_schema"]["required"], ["affair_id", "action"])
        self.assertFalse(endpoints["metadata"]["requires_confirmation"])


if __name__ == "__main__":
    unittest.main()
