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
        known_hosts = ROOT / "deploy/ssh/agentbridge_known_hosts"

        for marker in (
            "systemd-analyze verify",
            "systemctl daemon-reload",
            "service did not stabilize on the release unit",
            "service resolves unexpected bscli module",
            "UserKnownHostsFile=",
            "SSH known-hosts file was not found",
            "$smokeScript -Check Release",
        ):
            self.assertIn(marker, script)
        self.assertTrue(known_hosts.is_file())
        self.assertIn(
            "10.10.50.213 ssh-ed25519 ",
            known_hosts.read_text(encoding="ascii"),
        )

    def test_publish_entry_is_pinned_to_the_validated_release_path(self) -> None:
        script = (ROOT / "scripts/Publish-AgentBridge.ps1").read_text(
            encoding="utf-8"
        )
        github_known_hosts = ROOT / "deploy/ssh/github_known_hosts"

        for marker in (
            '"git@github.com:guomxin/cli-helper.git"',
            'Join-Path $repoRoot ".gitrepo"',
            '"Tracked files are modified. Commit the tested candidate before publishing."',
            "Assert-PrivateKeyReadable -Path $GitHubIdentityFile",
            "Assert-PrivateKeyReadable -Path $AgentBridgeIdentityFile",
            "& $validationScript -Mode Full",
            "SkipValidation = $true",
            "& $deployScript @deployParameters",
            "& $releaseAcceptanceScript @acceptanceParameters",
            'push --porcelain $RemoteName "HEAD:refs/heads/$BranchName"',
            '"ls-remote", "--exit-code"',
            "GitHub verification mismatch",
        ):
            self.assertIn(marker, script)
        self.assertLess(
            script.index("& $validationScript -Mode Full"),
            script.index("& $deployScript @deployParameters"),
        )
        self.assertLess(
            script.index("& $deployScript @deployParameters"),
            script.index("& $releaseAcceptanceScript @acceptanceParameters"),
        )
        self.assertLess(
            script.index("& $releaseAcceptanceScript @acceptanceParameters"),
            script.index("push --porcelain"),
        )
        self.assertTrue(github_known_hosts.is_file())
        self.assertIn(
            "github.com ssh-ed25519 ",
            github_known_hosts.read_text(encoding="ascii"),
        )

    def test_release_acceptance_uses_the_tracked_agentbridge_host_key(self) -> None:
        for path in (
            "scripts/Test-AgentBridgeReleaseAcceptance.ps1",
            "scripts/Test-AgentBridgeOmnichannelIsolation.ps1",
        ):
            script = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn("KnownHostsFile", script)
            self.assertIn("deploy\\ssh\\agentbridge_known_hosts", script)
            self.assertIn("UserKnownHostsFile=", script)

    def test_admin_console_is_deployed_with_tls_and_release_metadata(self) -> None:
        unit = (ROOT / "deploy/systemd/agentbridge.service").read_text(
            encoding="utf-8"
        )
        script = (ROOT / "scripts/Deploy-AgentBridge.ps1").read_text(
            encoding="utf-8"
        )

        for marker in (
            "EnvironmentFile=-/home/guomao/agentbridge/config/release.env",
            "--admin-host 10.10.50.213",
            "--admin-port 8782",
            "--admin-public-base-url https://10.10.50.213:8782",
            "--admin-tls-cert /home/guomao/agentbridge/config/tls/server.crt",
            "--admin-tls-key /home/guomao/agentbridge/config/tls/server.key",
        ):
            self.assertIn(marker, unit)
        for marker in (
            "AGENTBRIDGE_RELEASE_ID",
            'chmod 0640 "$root/config/release.env"',
        ):
            self.assertIn(marker, script)

    def test_smartlight_adapter_requires_explicit_plain_http_opt_in(self) -> None:
        unit = (ROOT / "deploy/systemd/agentbridge.service").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "--smartlight-base-url http://123.232.113.241:4101/smartlight",
            unit,
        )
        self.assertIn("--smartlight-allow-insecure-http", unit)
    def test_openclaw_restart_has_recovery_guardrails_and_warmup_gate(self) -> None:
        deploy = (ROOT / "scripts/Deploy-AgentBridge.ps1").read_text(
            encoding="utf-8"
        )
        warmup = (
            ROOT / "scripts/Test-OpenClawGatewayWarmup.ps1"
        ).read_text(encoding="utf-8")

        for marker in (
            "diagnostics.stuckSessionWarnMs",
            "diagnostics.stuckSessionAbortMs",
            "--batch-file",
            "gateway status --deep --require-rpc --json",
            "OpenClaw CLI and Gateway versions do not match",
            "OpenClaw Gateway reports plugin version drift",
            "$gatewayWarmupScript",
            'if ($warmup.status -ne "succeeded")',
        ):
            self.assertIn(marker, deploy)
        for marker in (
            'sessionKey = "agent:${AgentId}:agentbridge-release-warmup"',
            '--message $message',
            '--thinking off',
            '$status -ne "ok"',
            '$reply -ne "READY"',
            "HotPathMaximumSeconds",
        ):
            self.assertIn(marker, warmup)

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
            "oa_certificate_prepare_downloads",
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
            "oa_intellectual_property_declaration_approval_prepare",
            "oa_intellectual_property_declaration_approve",
            "oa_overtime_approval_prepare",
            "oa_overtime_approve",
            "oa_attendance_confirmation_prepare",
            "oa_attendance_confirm",
            "oa_weekly_report_acknowledgement_prepare",
            "oa_weekly_report_acknowledge",
            "oa_standard_collaboration_approval_prepare",
            "oa_standard_collaboration_approve",
            "oa_meeting_create_prepare",
            "oa_meeting_create",
            "smartlight_alarm_remark_update_prepare",
            "smartlight_alarm_remark_update",
            "smartlight_alarm_work_area_submit_prepare",
            "smartlight_alarm_work_area_submit",
            "smartlight_alarm_work_area_revoke_prepare",
            "smartlight_alarm_work_area_revoke",
            "smartlight_rtu_alarm_dispose_prepare",
            "smartlight_rtu_alarm_dispose",
        ):
            self.assertIn(tool, smoke)

        for check in (
            "SmartlightSessionStatus",
            "SmartlightOverview",
            "ToolCatalog",
        ):
            self.assertIn(check, smoke)
            self.assertIn(check, (ROOT / "scripts/Test-AgentBridgeMcp.ps1").read_text(
                encoding="utf-8"
            ))

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
            "smartlightUnexpectedTools",
            'effectiveCheck.kind === "session"',
            "payload?.isError",
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
            "prepare_intellectual_property_declaration_approval",
            "prepare_overtime_approval",
            "prepare_attendance_confirmation",
            "prepare_weekly_report_acknowledgement",
            "prepare_standard_collaboration_approval",
        ):
            self.assertIn(prepare_function, script)
        for forbidden_function in (
            "approve_missed_punch_request",
            "approve_efficiency_data",
            "approve_travel_expense",
            "approve_labor_contract_renewal",
            "approve_intellectual_property_declaration",
            "approve_overtime",
            "confirm_attendance",
            "acknowledge_weekly_report",
            "approve_standard_collaboration",
        ):
            self.assertNotIn(forbidden_function, script)
        self.assertIn('"write_controls_clicked": 0', script)
        self.assertIn('"collaboration_write_requests": 0', script)
        self.assertIn('"authorizations_created": 0', script)
        self.assertNotIn("state_store.save", script)

    def test_workspace_reverse_tunnel_is_loopback_only_and_persistent(self) -> None:
        tunnel = (ROOT / "scripts/Start-AgentBridgeWorkspaceTunnel.ps1").read_text(
            encoding="utf-8"
        )
        installer = (ROOT / "scripts/Install-AgentBridgeWorkspaceTunnel.ps1").read_text(
            encoding="utf-8"
        )
        unit = (ROOT / "deploy/systemd/agentbridge.service").read_text(
            encoding="utf-8"
        )

        for marker in (
            "ExitOnForwardFailure=yes",
            "ServerAliveInterval=30",
            "ServerAliveCountMax=3",
            '127.0.0.1:${RemotePort}:127.0.0.1:${LocalPort}',
        ):
            self.assertIn(marker, tunnel)
        self.assertIn("New-ScheduledTaskTrigger -AtLogOn", installer)
        self.assertIn("-WindowStyle Hidden", installer)
        self.assertIn("--workspace-gateway-url ws://127.0.0.1:18789", unit)
        self.assertNotIn("--workspace-gateway-url ws://10.90.20.210:18789", unit)

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
