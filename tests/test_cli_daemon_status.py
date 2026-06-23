import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest


class CliDaemonStatusTests(unittest.TestCase):
    def test_cli_daemon_status_reads_health_and_extension_clients(self):
        seen_paths = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen_paths.append(self.path)
                if self.path == "/health":
                    response = {"ok": True}
                elif self.path == "/extension/clients":
                    response = {
                        "clients": [
                            {
                                "client_id": "chrome-1",
                                "title": "OA",
                                "url": "http://10.10.50.110/seeyon/main.do?method=main",
                            }
                        ]
                    }
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
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
                "daemon",
                "status",
                "--daemon-url",
                f"http://127.0.0.1:{server.server_port}",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

        status = json.loads(result.stdout)
        self.assertEqual(seen_paths, ["/health", "/extension/clients"])
        self.assertEqual(status["daemon"]["ok"], True)
        self.assertEqual(status["extension_clients"][0]["title"], "OA")


if __name__ == "__main__":
    unittest.main()
