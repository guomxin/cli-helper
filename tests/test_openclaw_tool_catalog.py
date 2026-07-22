from __future__ import annotations

import json
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
