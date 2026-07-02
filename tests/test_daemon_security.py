from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bscli.daemon.app import (
    _is_allowed_daemon_host,
    _is_allowed_daemon_origin,
    _is_token_protected_path,
    _load_or_create_daemon_token,
)


class DaemonHttpSecurityTests(unittest.TestCase):
    def test_host_check_allows_localhost_only(self):
        self.assertTrue(_is_allowed_daemon_host("127.0.0.1:8765"))
        self.assertTrue(_is_allowed_daemon_host("localhost:8765"))
        self.assertTrue(_is_allowed_daemon_host("[::1]:8765"))
        self.assertFalse(_is_allowed_daemon_host("evil.example:8765"))

    def test_origin_check_blocks_web_pages_but_allows_extension(self):
        self.assertTrue(_is_allowed_daemon_origin(""))
        self.assertTrue(_is_allowed_daemon_origin("chrome-extension://abc123"))
        self.assertTrue(_is_allowed_daemon_origin("http://127.0.0.1:8765"))
        self.assertFalse(_is_allowed_daemon_origin("http://evil.example"))

    def test_token_generation_is_stable_for_home(self):
        with TemporaryDirectory() as tmp:
            first = _load_or_create_daemon_token(Path(tmp))
            second = _load_or_create_daemon_token(Path(tmp))

            self.assertEqual(first, second)
            self.assertGreaterEqual(len(first), 32)
            self.assertEqual((Path(tmp) / "daemon-token").read_text(encoding="utf-8"), first)

    def test_command_and_read_result_paths_require_token(self):
        self.assertTrue(_is_token_protected_path("POST", "/commands/run"))
        self.assertTrue(_is_token_protected_path("POST", "/explore/dom-snapshot"))
        self.assertTrue(_is_token_protected_path("GET", "/extension/clients"))
        self.assertTrue(_is_token_protected_path("GET", "/extension/results/task-1"))
        self.assertFalse(_is_token_protected_path("GET", "/health"))
        self.assertFalse(_is_token_protected_path("POST", "/extension/register"))
        self.assertFalse(_is_token_protected_path("GET", "/extension/tasks"))


if __name__ == "__main__":
    unittest.main()
