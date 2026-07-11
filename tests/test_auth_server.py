import http.client
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

from bscli.auth.card import TrustedAuthApplication
from bscli.auth.server import (
    _request_origin_allowed,
    create_auth_http_server,
    validate_auth_server_config,
)
from bscli.core.auth_challenges import AuthChallengeStore


class AuthServerConfigTests(unittest.TestCase):
    def test_loopback_server_can_use_http_for_local_poc(self):
        config = validate_auth_server_config(
            host="127.0.0.1",
            port=8780,
            public_base_url=None,
            tls_cert=None,
            tls_key=None,
        )

        self.assertEqual(config.public_base_url, "http://127.0.0.1:8780")
        self.assertFalse(config.secure_cookie)

    def test_non_loopback_server_requires_tls_and_https_public_url(self):
        with self.assertRaisesRegex(ValueError, "requires TLS"):
            validate_auth_server_config(
                host="0.0.0.0",
                port=8780,
                public_base_url="http://auth.example.test:8780",
                tls_cert=None,
                tls_key=None,
            )

        with TemporaryDirectory() as tmp:
            cert = Path(tmp) / "cert.pem"
            key = Path(tmp) / "key.pem"
            with self.assertRaisesRegex(ValueError, "must use HTTPS"):
                validate_auth_server_config(
                    host="0.0.0.0",
                    port=8780,
                    public_base_url="http://auth.example.test:8780",
                    tls_cert=cert,
                    tls_key=key,
                )

    def test_loopback_origin_aliases_are_treated_as_the_same_trusted_source(self):
        allowed_hosts = {"127.0.0.1", "localhost", "::1"}

        self.assertTrue(
            _request_origin_allowed(
                origin="http://localhost:8780",
                sec_fetch_site="same-origin",
                host_header="127.0.0.1:8780",
                expected_scheme="http",
                allowed_hosts=allowed_hosts,
            )
        )
        self.assertTrue(
            _request_origin_allowed(
                origin="http://[::1]:8780",
                sec_fetch_site="same-origin",
                host_header="localhost:8780",
                expected_scheme="http",
                allowed_hosts=allowed_hosts,
            )
        )

    def test_opaque_origin_requires_same_origin_fetch_metadata(self):
        parameters = {
            "origin": "null",
            "host_header": "127.0.0.1:8780",
            "expected_scheme": "http",
            "allowed_hosts": {"127.0.0.1", "localhost", "::1"},
        }

        self.assertTrue(
            _request_origin_allowed(sec_fetch_site="same-origin", **parameters)
        )
        self.assertFalse(
            _request_origin_allowed(sec_fetch_site="cross-site", **parameters)
        )
        self.assertFalse(_request_origin_allowed(sec_fetch_site=None, **parameters))

    def test_origin_check_rejects_untrusted_scheme_host_and_port(self):
        parameters = {
            "sec_fetch_site": "same-origin",
            "host_header": "127.0.0.1:8780",
            "expected_scheme": "http",
            "allowed_hosts": {"127.0.0.1", "localhost", "::1"},
        }

        self.assertFalse(
            _request_origin_allowed(
                origin="https://127.0.0.1:8780", **parameters
            )
        )
        self.assertFalse(
            _request_origin_allowed(
                origin="http://evil.example.test:8780", **parameters
            )
        )
        self.assertFalse(
            _request_origin_allowed(
                origin="http://127.0.0.1:9999", **parameters
            )
        )

    def test_http_server_serves_card_and_rejects_cross_origin_post(self):
        with TemporaryDirectory() as tmp:
            store = AuthChallengeStore(Path(tmp) / "agentbridge.db")
            challenge = store.create(
                user_subject="user-a",
                system_id="oa",
                system_name="致远 OA",
                session_id="session-a",
                expected_principal_ref="Alice",
                origin="http://oa.example.test",
                page_fingerprint="fingerprint",
                nonce="nonce",
                fields=[
                    {
                        "name": "username",
                        "label": "OA account",
                        "input_type": "text",
                        "autocomplete": "username",
                        "required": True,
                    },
                    {
                        "name": "password",
                        "label": "Password",
                        "input_type": "password",
                        "autocomplete": "current-password",
                        "required": True,
                    },
                ],
                card_base_url="http://127.0.0.1:0",
            )
            config = validate_auth_server_config(
                host="127.0.0.1",
                port=0,
                public_base_url="http://127.0.0.1:0",
                tls_cert=None,
                tls_key=None,
            )
            application = TrustedAuthApplication(
                challenge_store=store,
                broker=RejectingBroker(),
            )
            server = create_auth_http_server(config=config, application=application)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request("GET", f"/auth/{challenge['challenge_id']}")
                response = connection.getresponse()
                body = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertIn("致远 OA", body)
                self.assertEqual(response.getheader("Cache-Control"), "no-store")
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request(
                    "POST",
                    f"/auth/{challenge['challenge_id']}",
                    body="csrf_token=x&username=a&password=b",
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": "https://evil.example.test",
                    },
                )
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 403)
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


class RejectingBroker:
    def authenticate(self, **_kwargs):
        raise AssertionError("cross-origin request reached credential broker")


if __name__ == "__main__":
    unittest.main()
