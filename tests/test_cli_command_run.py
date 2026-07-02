import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest

from bscli.cli.main import run_command_via_daemon


class CliCommandRunTests(unittest.TestCase):
    def test_cli_command_run_posts_to_daemon_and_prints_result(self):
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", "0"))
                captured["path"] = self.path
                captured["body"] = json.loads(self.rfile.read(length).decode("utf-8"))
                response = {
                    "ok": True,
                    "task_id": "task-1",
                    "result": {"title": "Seeyon OA", "text": "首页"},
                }
                payload = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd())
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "bscli.cli.main",
                "command",
                "run",
                "oa",
                "current_page_snapshot",
                "--timeout",
                "3",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertEqual(captured["path"], "/commands/run")
        self.assertEqual(
            captured["body"],
            {
                "system": "oa",
                "command": "current_page_snapshot",
                "args": {},
                "timeout_seconds": 3.0,
            },
        )
        self.assertEqual(json.loads(result.stdout)["result"]["title"], "Seeyon OA")

    def test_cli_command_run_sends_daemon_token_from_home(self):
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", "0"))
                self.rfile.read(length)
                captured["token"] = self.headers.get("x-bscli-token")
                response = {"ok": True, "result": {"text": "ok"}}
                payload = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        with self.subTest("token header"):
            from tempfile import TemporaryDirectory

            with TemporaryDirectory() as tmp:
                Path(tmp, "daemon-token").write_text("token-123", encoding="utf-8")
                env = os.environ.copy()
                env["PYTHONPATH"] = str(Path.cwd())
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "bscli.cli.main",
                        "--home",
                        tmp,
                        "command",
                        "run",
                        "oa",
                        "current_page_snapshot",
                        "--daemon-url",
                        f"http://127.0.0.1:{server.server_port}",
                    ],
                    cwd=Path.cwd(),
                    env=env,
                    text=True,
                    capture_output=True,
                    check=True,
                )

        self.assertEqual(captured["token"], "token-123")

    def test_run_command_via_daemon_raises_actionable_daemon_error_body(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                response = {
                    "ok": False,
                    "error": "no Chrome extension client connected",
                }
                payload = json.dumps(response).encode("utf-8")
                self.send_response(409)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        with self.assertRaisesRegex(RuntimeError, "no Chrome extension client connected"):
            run_command_via_daemon(
                f"http://127.0.0.1:{server.server_port}",
                "oa",
                "pending_list",
                {},
            )


if __name__ == "__main__":
    unittest.main()
