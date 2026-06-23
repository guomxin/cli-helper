import unittest

from bscli.adapters.seeyon import register_seeyon_commands
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


if __name__ == "__main__":
    unittest.main()
