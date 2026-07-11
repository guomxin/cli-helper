import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from bscli.cli.main import main


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


if __name__ == "__main__":
    unittest.main()
