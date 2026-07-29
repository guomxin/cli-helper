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

    def test_deployment_cleans_stale_build_tree_before_wheel(self) -> None:
        script = (ROOT / "scripts/Deploy-AgentBridge.ps1").read_text(
            encoding="utf-8"
        )

        cleanup = "Remove-Item -LiteralPath $buildDirectory -Recurse -Force"
        wheel = "-m pip wheel"
        self.assertIn("Refusing to clean a build directory outside", script)
        self.assertIn(cleanup, script)
        self.assertLess(script.index(cleanup), script.index(wheel))

    def test_yuque_remote_login_uses_challenge_isolation_and_native_novnc(self) -> None:
        service = (ROOT / "deploy/systemd/agentbridge.service").read_text(
            encoding="utf-8"
        )
        deploy = (ROOT / "scripts/Deploy-AgentBridge.ps1").read_text(
            encoding="utf-8"
        )
        broker = (ROOT / "bscli/broker/remote_browser.py").read_text(
            encoding="utf-8"
        )

        for retired in (
            "Requires=agentbridge-xvfb.service",
            "JoinsNamespaceOf=agentbridge-xvfb.service",
            "Environment=DISPLAY=:99",
            "Environment=XAUTHORITY=",
        ):
            self.assertNotIn(retired, service)
        self.assertFalse(
            (ROOT / "deploy/systemd/agentbridge-xvfb.service").exists()
        )
        for marker in (
            "xvfb x11vnc novnc websockify xauth",
            "systemctl disable --now agentbridge-xvfb.service",
            "test -d /usr/share/novnc",
        ):
            self.assertIn(marker, deploy)
        for marker in (
            "-nolisten",
            '"tcp"',
            '"-localhost"',
            '"--token-plugin=TokenFile"',
            '"--remote-debugging-address=127.0.0.1"',
            'f"--remote-debugging-port={self.allocation.cdp_port}"',
        ):
            self.assertIn(marker, broker)
        for retired in ("--enable-automation", "--remote-debugging-pipe"):
            self.assertNotIn(retired, broker)

    def test_release_smoke_requires_new_write_tools(self) -> None:
        smoke = (ROOT / "scripts/agentbridge-mcp-smoke.mjs").read_text(encoding="utf-8")

        for tool in (
            "oa_certificate_search",
            "oa_certificate_prepare_download",
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
            "oa_labor_contract_renewal_approval_prepare",
            "oa_labor_contract_renewal_approve",
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

    def test_identity_isolation_smoke_selects_named_bindings_without_tokens(self) -> None:
        smoke = (ROOT / "scripts/Test-AgentBridgeMcp.ps1").read_text(encoding="utf-8")
        isolation = (
            ROOT / "scripts/Test-AgentBridgeIdentityIsolation.ps1"
        ).read_text(encoding="utf-8")
        node_smoke = (
            ROOT / "scripts/agentbridge-mcp-smoke.mjs"
        ).read_text(encoding="utf-8")

        for marker in (
            "IdentityLabel",
            "IdentityChannel",
            "IdentitySenderId",
            "did not resolve exactly one active binding",
        ):
            self.assertIn(marker, smoke)
        for marker in (
            "uniqueSubjects",
            "identity changed during the stability check",
            "session is not active",
        ):
            self.assertIn(marker, isolation)
        for marker in (
            "TaihuaSessionStatus",
            "OaPendingRead",
            "TaihuaMyLogs",
            "downstreamPrincipalRef",
        ):
            self.assertIn(marker, node_smoke)
        self.assertNotIn("Token =", isolation)
    def test_pending_action_preflight_is_read_only_by_construction(self) -> None:
        script = (
            ROOT / "scripts/validate_oa_pending_actions_preflight.py"
        ).read_text(encoding="utf-8")

        for prepare_function in (
            "prepare_missed_punch_approval",
            "prepare_efficiency_data_approval",
            "prepare_travel_expense_approval",
            "prepare_labor_contract_renewal_approval",
            "prepare_weekly_report_acknowledgement",
            "prepare_standard_collaboration_approval",
        ):
            self.assertIn(prepare_function, script)
        for forbidden_function in (
            "approve_missed_punch_request",
            "approve_efficiency_data",
            "approve_travel_expense",
            "approve_labor_contract_renewal",
            "acknowledge_weekly_report",
            "approve_standard_collaboration",
        ):
            self.assertNotIn(forbidden_function, script)
        self.assertIn('"write_controls_clicked": 0', script)
        self.assertIn('"collaboration_write_requests": 0', script)
        self.assertIn('"authorizations_created": 0', script)
        self.assertNotIn("state_store.save", script)

    def test_unit_document_probe_is_read_only_by_construction(self) -> None:
        script = (
            ROOT / "scripts/inspect_oa_unit_documents.py"
        ).read_text(encoding="utf-8")

        for marker in (
            '"downloads_started": 0',
            '"write_controls_clicked": 0',
            '"response_bodies_included": False',
            'method in {"DELETE", "PATCH", "PUT"}',
            "_BLOCKED_WRITE_MARKERS",
        ):
            self.assertIn(marker, script)
        for forbidden_function in (
            "expect_download",
            "state_store.save",
            "set_input_files",
            "request.post(",
            "request.put(",
        ):
            self.assertNotIn(forbidden_function, script)

if __name__ == "__main__":
    unittest.main()
