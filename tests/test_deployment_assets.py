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
            "oa_business_trip_prepare",
            "oa_business_trip_save_draft",
            "oa_business_trip_submit_prepare",
            "oa_business_trip_submit",
            "oa_leave_prepare",
            "oa_leave_save_draft",
            "oa_leave_submit_prepare",
            "oa_leave_submit",
            "oa_workflow_revoke_prepare",
            "oa_workflow_revoke",
            "oa_missed_punch_prepare",
            "oa_missed_punch_save_draft",
            "oa_missed_punch_approval_prepare",
            "oa_missed_punch_approve",
            "oa_efficiency_data_approval_prepare",
            "oa_efficiency_data_approve",
            "oa_travel_expense_approval_prepare",
            "oa_travel_expense_approve",
            "oa_weekly_report_acknowledgement_prepare",
            "oa_weekly_report_acknowledge",
            "oa_standard_collaboration_approval_prepare",
            "oa_standard_collaboration_approve",
            "oa_meeting_create_prepare",
            "oa_meeting_create",
        ):
            self.assertIn(tool, smoke)

    def test_openclaw_config_is_read_as_utf8(self) -> None:
        script = (ROOT / "scripts/Test-AgentBridgeMcp.ps1").read_text(encoding="utf-8")

        for marker in (
            "-Encoding UTF8",
            '"agentbridge-interactions"',
            '"identityBindings"',
            '"mcpUrl"',
        ):
            self.assertIn(marker, script)

    def test_pending_action_preflight_is_read_only_by_construction(self) -> None:
        script = (
            ROOT / "scripts/validate_oa_pending_actions_preflight.py"
        ).read_text(encoding="utf-8")

        for prepare_function in (
            "prepare_efficiency_data_approval",
            "prepare_travel_expense_approval",
            "prepare_weekly_report_acknowledgement",
            "prepare_standard_collaboration_approval",
        ):
            self.assertIn(prepare_function, script)
        for forbidden_function in (
            "approve_efficiency_data",
            "approve_travel_expense",
            "acknowledge_weekly_report",
            "approve_standard_collaboration",
        ):
            self.assertNotIn(forbidden_function, script)
        self.assertIn('"write_controls_clicked": 0', script)
        self.assertIn('"collaboration_write_requests": 0', script)
        self.assertIn('"authorizations_created": 0', script)
        self.assertNotIn("state_store.save", script)


if __name__ == "__main__":
    unittest.main()
