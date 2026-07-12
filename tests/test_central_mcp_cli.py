import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bscli.cli.main import main


class CentralMcpCliTests(unittest.TestCase):
    def test_identity_token_issue_shows_secret_once_and_binds_session(self):
        with TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()) as issued_stdout:
                exit_code = main(
                    [
                        "--home",
                        tmp,
                        "mcp",
                        "token",
                        "issue",
                        "--user-subject",
                        "user-a",
                        "--expected-principal",
                        "Alice",
                        "--label",
                        "desktop",
                        "--ttl-hours",
                        "12",
                    ]
                )
            issued = json.loads(issued_stdout.getvalue())

            with redirect_stdout(io.StringIO()) as list_stdout:
                list_exit = main(
                    ["--home", tmp, "mcp", "token", "list", "--user-subject", "user-a"]
                )
            listed = json.loads(list_stdout.getvalue())
            profiles_exists = (Path(tmp) / "profiles").exists()

        self.assertEqual(exit_code, 0)
        self.assertEqual(list_exit, 0)
        self.assertTrue(issued["bearerToken"].startswith("abmcp_"))
        self.assertEqual(issued["identityToken"]["expectedPrincipalRef"], "Alice")
        self.assertEqual(listed["count"], 1)
        self.assertNotIn("bearerToken", listed)
        self.assertNotIn(issued["bearerToken"], json.dumps(listed))
        self.assertTrue(profiles_exists)

    def test_identity_token_can_be_revoked(self):
        with TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()) as issued_stdout:
                main(
                    [
                        "--home",
                        tmp,
                        "mcp",
                        "token",
                        "issue",
                        "--user-subject",
                        "user-a",
                        "--expected-principal",
                        "Alice",
                    ]
                )
            token_id = json.loads(issued_stdout.getvalue())["identityToken"]["tokenId"]

            with redirect_stdout(io.StringIO()) as revoked_stdout:
                exit_code = main(
                    ["--home", tmp, "mcp", "token", "revoke", token_id]
                )

        revoked = json.loads(revoked_stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(revoked["status"], "revoked")
        self.assertEqual(revoked["identityToken"]["state"], "revoked")

    def test_central_server_starts_mcp_and_auth_card_in_one_runtime(self):
        with TemporaryDirectory() as tmp:
            with (
                patch("bscli.cli.main.serve_central_mcp") as serve,
                redirect_stdout(io.StringIO()) as stdout,
            ):
                exit_code = main(
                    [
                        "--home",
                        tmp,
                        "mcp",
                        "central-serve",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "8790",
                        "--auth-host",
                        "127.0.0.1",
                        "--auth-port",
                        "8780",
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["mcpUrl"], "http://127.0.0.1:8790/mcp")
        self.assertEqual(payload["authCardBaseUrl"], "http://127.0.0.1:8780")
        self.assertEqual(payload["authentication"], "bearer_identity_token")
        serve.assert_called_once()

    def test_central_server_rejects_same_mcp_and_auth_port(self):
        with TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as stdout:
            exit_code = main(
                [
                    "--home",
                    tmp,
                    "mcp",
                    "central-serve",
                    "--port",
                    "8790",
                    "--auth-port",
                    "8790",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["error"]["code"], "CENTRAL_MCP_CONFIG_INVALID")


if __name__ == "__main__":
    unittest.main()
