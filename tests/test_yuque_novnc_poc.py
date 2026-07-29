from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bscli.tools.yuque_novnc_poc import _validated_config


class YuqueNoVncPocTests(unittest.TestCase):
    def test_configuration_is_private_bounded_and_origin_locked(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            chrome = self._file(root / "chrome")
            cert = self._file(root / "server.crt")
            key = self._file(root / "server.key")
            args = self._args(root / "runtime", chrome, cert, key)

            with patch("shutil.which", return_value="/usr/bin/tool"):
                config = _validated_config(args)

            self.assertEqual(config["listen_host"], "10.10.50.213")
            self.assertEqual(config["listen_port"], 8781)
            self.assertEqual(config["duration_seconds"], 900)
            self.assertEqual(config["target_url"], "https://tc-aiot.yuque.com/")

    def test_configuration_rejects_public_bindings_and_other_origins(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            chrome = self._file(root / "chrome")
            cert = self._file(root / "server.crt")
            key = self._file(root / "server.key")
            public = self._args(root / "runtime-a", chrome, cert, key)
            public.listen_host = "0.0.0.0"
            wrong_origin = self._args(root / "runtime-b", chrome, cert, key)
            wrong_origin.target_url = "https://www.yuque.com/"

            with patch("shutil.which", return_value="/usr/bin/tool"):
                with self.assertRaisesRegex(ValueError, "private IP"):
                    _validated_config(public)
                with self.assertRaisesRegex(ValueError, "registered Yuque origin"):
                    _validated_config(wrong_origin)

    @staticmethod
    def _file(path: Path) -> Path:
        path.write_text("test", encoding="utf-8")
        return path

    @staticmethod
    def _args(runtime: Path, chrome: Path, cert: Path, key: Path):
        return argparse.Namespace(
            runtime_dir=str(runtime),
            chrome_executable=str(chrome),
            tls_cert=str(cert),
            tls_key=str(key),
            listen_host="10.10.50.213",
            listen_port=8781,
            display=100,
            rfb_port=5901,
            duration_seconds=900,
            target_url="https://tc-aiot.yuque.com/",
        )


if __name__ == "__main__":
    unittest.main()
