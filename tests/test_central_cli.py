import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from bscli.cli.main import main
from bscli.core.sessions import SessionRegistry


class CentralCliTests(unittest.TestCase):
    def test_capability_list_exposes_reads_and_governed_business_trip_draft(self):
        with TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as stdout:
            exit_code = main(["--home", tmp, "capability", "list"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["protocolVersion"], "0.1")
        self.assertEqual(len(payload["capabilities"]), 8)
        capabilities = {item["name"]: item for item in payload["capabilities"]}
        self.assertIn("oa.template.list", capabilities)
        self.assertIn("oa.workflow.pending.list", capabilities)
        self.assertEqual(
            capabilities["oa.business_trip.prepare"]["effect"],
            "reversible_write",
        )
        self.assertEqual(
            capabilities["oa.business_trip.save_draft"]["effect"],
            "reversible_write",
        )

    def test_session_status_returns_not_found_without_opening_browser(self):
        with TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as stdout:
            exit_code = main(
                [
                    "--home",
                    tmp,
                    "session",
                    "status",
                    "--system",
                    "oa",
                    "--user-subject",
                    "user-a",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "not_found")
        self.assertEqual(payload["systemId"], "oa")
        self.assertFalse((Path(tmp) / "profiles").exists())

    def test_capability_invoke_without_session_returns_login_action_and_operation(self):
        with TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()) as stdout:
                exit_code = main(
                    [
                        "--home",
                        tmp,
                        "capability",
                        "invoke",
                        "oa.template.list",
                        "--user-subject",
                        "user-a",
                        "--idempotency-key",
                        "first-list",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["status"], "requires_user_action")
            self.assertEqual(payload["error"]["code"], "LOGIN_REQUIRED")
            self.assertEqual(payload["nextAction"]["type"], "session_login")
            self.assertFalse((Path(tmp) / "profiles").exists())

            with redirect_stdout(io.StringIO()) as operation_stdout:
                operation_exit = main(
                    ["--home", tmp, "operation", "get", payload["operationId"]]
                )
            operation = json.loads(operation_stdout.getvalue())["operation"]
            self.assertEqual(operation_exit, 0)
            self.assertEqual(operation["status"], "requires_user_action")

    def test_workflow_capability_uses_generic_central_session_handler(self):
        with TemporaryDirectory() as tmp:
            service = MagicMock()
            service.invoke.return_value = {
                "protocolVersion": "0.1",
                "requestId": "request-1",
                "operationId": "operation-1",
                "status": "succeeded",
                "result": {"collection": "pending", "count": 0, "items": []},
                "error": None,
                "evidenceRefs": [],
                "nextAction": None,
                "reused": False,
            }

            with (
                patch("bscli.cli.main.CentralCapabilityService", return_value=service),
                redirect_stdout(io.StringIO()) as stdout,
            ):
                exit_code = main(
                    [
                        "--home",
                        tmp,
                        "capability",
                        "invoke",
                        "oa.workflow.pending.list",
                        "--user-subject",
                        "user-a",
                        "--json",
                        '{"limit": 5}',
                        "--idempotency-key",
                        "pending-generic-handler",
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "succeeded")
        service.invoke.assert_called_once_with(
            user_subject="user-a",
            capability_name="oa.workflow.pending.list",
            arguments={"limit": 5},
            idempotency_key="pending-generic-handler",
            request_id=None,
        )

    def test_expired_workflow_session_returns_login_action_and_deletes_secret(self):
        with TemporaryDirectory() as tmp:
            service = MagicMock()
            service.invoke.return_value = {
                "protocolVersion": "0.1",
                "requestId": "request-1",
                "operationId": "operation-1",
                "status": "requires_user_action",
                "result": None,
                "error": {"code": "LOGIN_REQUIRED", "message": "not active"},
                "evidenceRefs": [],
                "nextAction": {
                    "type": "session_login",
                    "system": "oa",
                    "userSubject": "user-a",
                    "sessionState": "expired",
                },
                "reused": False,
            }

            with (
                patch("bscli.cli.main.CentralCapabilityService", return_value=service),
                redirect_stdout(io.StringIO()) as stdout,
            ):
                exit_code = main(
                    [
                        "--home",
                        tmp,
                        "capability",
                        "invoke",
                        "oa.workflow.done.list",
                        "--user-subject",
                        "user-a",
                        "--idempotency-key",
                        "expired-workflow-session",
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "requires_user_action")
        self.assertEqual(payload["error"]["code"], "LOGIN_REQUIRED")
        self.assertEqual(payload["nextAction"]["sessionState"], "expired")

    def test_session_login_creates_authentication_card_challenge_without_browser(self):
        with TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as stdout:
            exit_code = main(
                [
                    "--home",
                    tmp,
                    "session",
                    "login",
                    "--system",
                    "oa",
                    "--user-subject",
                    "user-a",
                    "--expected-principal",
                    "Alice",
                    "--card-base-url",
                    "http://127.0.0.1:8780",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "requires_user_action")
        self.assertEqual(payload["challenge"]["type"], "legacy_form_login")
        self.assertEqual(payload["challenge"]["state"], "pending")
        self.assertEqual(payload["challenge"]["systemName"], "致远 OA")
        self.assertTrue(payload["challenge"]["cardUrl"].startswith("http://127.0.0.1:8780/auth/"))
        self.assertNotIn("password", json.dumps(payload).lower())

    def test_session_login_does_not_destroy_an_active_session_before_card_submission(self):
        with TemporaryDirectory() as tmp:
            sessions = SessionRegistry(Path(tmp) / "agentbridge.db", Path(tmp) / "profiles")
            session = sessions.get_or_create(
                user_subject="user-a",
                system_id="oa",
                expected_principal_ref="Alice",
            )
            sessions.activate(session["session_id"], observed_principal_ref="Alice")

            with redirect_stdout(io.StringIO()):
                exit_code = main(
                    [
                        "--home",
                        tmp,
                        "session",
                        "login",
                        "--system",
                        "oa",
                        "--user-subject",
                        "user-a",
                        "--expected-principal",
                        "Alice",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(sessions.get(session["session_id"])["state"], "active")


if __name__ == "__main__":
    unittest.main()
