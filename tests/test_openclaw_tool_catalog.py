from __future__ import annotations

import json
import unittest
from pathlib import Path

from bscli.mcp.central import AGENT_FACING_TOOL_SCOPE_REQUIREMENTS
from tools.export_openclaw_agentbridge_catalog import build_catalog


REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = (
    REPO_ROOT
    / "integrations"
    / "openclaw-agentbridge"
    / "lib"
    / "agentbridge-tools.json"
)


class OpenClawToolCatalogTests(unittest.TestCase):
    def test_committed_catalog_matches_current_mcp_tools(self) -> None:
        committed = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

        self.assertEqual(committed, build_catalog())
        self.assertFalse(
            any(
                tool["name"].startswith("agentbridge_host_")
                for tool in committed["tools"]
            )
        )
        expected_agent_tools = {
            tool["name"]
            for tool in committed["tools"]
            if tool.get("annotations", {}).get("readOnlyHint") is True
            or tool["name"].endswith("_prepare")
            or tool["name"].endswith("_session_login")
        }
        self.assertEqual(
            set(AGENT_FACING_TOOL_SCOPE_REQUIREMENTS),
            expected_agent_tools,
        )


if __name__ == "__main__":
    unittest.main()
