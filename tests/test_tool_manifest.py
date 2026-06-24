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


if __name__ == "__main__":
    unittest.main()
