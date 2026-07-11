import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from bscli.cli.main import main
from bscli.core.sessions import SessionRegistry


class CentralCliTests(unittest.TestCase):
    def test_capability_list_exposes_central_template_capability(self):
        with TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as stdout:
            exit_code = main(["--home", tmp, "capability", "list"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["protocolVersion"], "0.1")
        self.assertEqual(payload["capabilities"][0]["name"], "oa.template.list")

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
