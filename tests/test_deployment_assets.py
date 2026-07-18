from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeploymentAssetTests(unittest.TestCase):
    def test_systemd_service_cannot_import_legacy_app_source(self) -> None:
        unit = (ROOT / "deploy/systemd/agentbridge.service").read_text(encoding="utf-8")

        self.assertIn("WorkingDirectory=/home/guomao/agentbridge\n", unit)
        self.assertNotIn("WorkingDirectory=/home/guomao/agentbridge/app", unit)
        self.assertIn("venv/bin/python -P -m bscli.cli.main", unit)

    def test_deployment_installs_unit_and_checks_runtime_module_source(self) -> None:
        script = (ROOT / "scripts/Deploy-AgentBridge.ps1").read_text(encoding="utf-8")

        for marker in (
            "systemd-analyze verify",
            "systemctl daemon-reload",
            "service did not stabilize on the release unit",
            "service resolves unexpected bscli module",
            "$smokeScript -Check Release",
        ):
            self.assertIn(marker, script)

    def test_release_smoke_requires_new_write_tools(self) -> None:
        smoke = (ROOT / "scripts/agentbridge-mcp-smoke.mjs").read_text(encoding="utf-8")

        for tool in (
            "oa_missed_punch_prepare",
            "oa_missed_punch_save_draft",
            "oa_missed_punch_approval_prepare",
            "oa_missed_punch_approve",
            "oa_meeting_create_prepare",
            "oa_meeting_create",
        ):
            self.assertIn(tool, smoke)


if __name__ == "__main__":
    unittest.main()