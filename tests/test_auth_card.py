import re
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urlencode

from bscli.auth.card import TrustedAuthApplication
from bscli.core.auth_challenges import AuthChallengeStore


class TrustedAuthCardTests(unittest.TestCase):
    def test_card_renders_registered_fields_and_strict_security_headers(self):
        with TemporaryDirectory() as tmp:
            store = AuthChallengeStore(Path(tmp) / "agentbridge.db")
            challenge = _challenge(store)
            app = TrustedAuthApplication(challenge_store=store, broker=FakeBroker())

            response = app.get_card(challenge["challenge_id"], secure_cookie=False)

            html = response.body.decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("致远 OA", html)
            self.assertIn('name="username"', html)
            self.assertIn('name="password"', html)
            self.assertIn('autocomplete="current-password"', html)
            self.assertNotIn("10.10.50.110/seeyon", html)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])
            self.assertIn("HttpOnly", response.headers["Set-Cookie"])
            self.assertIn("SameSite=Strict", response.headers["Set-Cookie"])
            self.assertIn("Max-Age=300", response.headers["Set-Cookie"])

    def test_card_submission_requires_matching_csrf_and_does_not_echo_password(self):
        with TemporaryDirectory() as tmp:
            store = AuthChallengeStore(Path(tmp) / "agentbridge.db")
            challenge = _challenge(store)
            broker = FakeBroker()
            app = TrustedAuthApplication(challenge_store=store, broker=broker)
            page = app.get_card(challenge["challenge_id"], secure_cookie=False)
            html = page.body.decode("utf-8")
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
            cookie = page.headers["Set-Cookie"].split(";", 1)[0].split("=", 1)[1]
            body = urlencode(
                {
                    "csrf_token": csrf,
                    "username": "alice.login",
                    "password": "card-secret",
                }
            ).encode("utf-8")

            response = app.submit_card(
                challenge["challenge_id"],
                body=body,
                content_type="application/x-www-form-urlencoded",
                csrf_cookie=cookie,
            )

            rendered = response.body.decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertEqual(broker.received["username"], "alice.login")
            self.assertEqual(broker.received["password"], "card-secret")
            self.assertNotIn("card-secret", rendered)
            self.assertIn("认证完成", rendered)

    def test_card_rejects_oversized_or_wrong_content_type(self):
        with TemporaryDirectory() as tmp:
            store = AuthChallengeStore(Path(tmp) / "agentbridge.db")
            challenge = _challenge(store)
            app = TrustedAuthApplication(challenge_store=store, broker=FakeBroker())

            wrong_type = app.submit_card(
                challenge["challenge_id"],
                body=b"{}",
                content_type="application/json",
                csrf_cookie="x",
            )
            oversized = app.submit_card(
                challenge["challenge_id"],
                body=b"x" * 17000,
                content_type="application/x-www-form-urlencoded",
                csrf_cookie="x",
            )

            self.assertEqual(wrong_type.status, 415)
            self.assertEqual(oversized.status, 413)

    def test_card_failure_shows_only_safe_error_code(self):
        with TemporaryDirectory() as tmp:
            store = AuthChallengeStore(Path(tmp) / "agentbridge.db")
            challenge = _challenge(store)
            broker = FakeBroker(
                result={
                    "status": "failed",
                    "error": {"code": "LOGIN_CONTRACT_MISMATCH"},
                }
            )
            app = TrustedAuthApplication(challenge_store=store, broker=broker)
            page = app.get_card(challenge["challenge_id"], secure_cookie=False)
            html = page.body.decode("utf-8")
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
            cookie = page.headers["Set-Cookie"].split(";", 1)[0].split("=", 1)[1]

            response = app.submit_card(
                challenge["challenge_id"],
                body=urlencode(
                    {
                        "csrf_token": csrf,
                        "username": "alice.login",
                        "password": "card-secret",
                    }
                ).encode("utf-8"),
                content_type="application/x-www-form-urlencoded",
                csrf_cookie=cookie,
            )

            rendered = response.body.decode("utf-8")
            self.assertEqual(response.status, 401)
            self.assertIn("LOGIN_CONTRACT_MISMATCH", rendered)
            self.assertNotIn("card-secret", rendered)


def _challenge(store: AuthChallengeStore) -> dict:
    return store.create(
        user_subject="user-a",
        system_id="oa",
        session_id="session-a",
        origin="http://10.10.50.110",
        page_fingerprint="seeyon-login-v1",
        nonce="nonce",
        fields=[
            {
                "name": "username",
                "label": "OA 账号",
                "input_type": "text",
                "autocomplete": "username",
                "required": True,
            },
            {
                "name": "password",
                "label": "密码",
                "input_type": "password",
                "autocomplete": "current-password",
                "required": True,
            },
        ],
        card_base_url="http://127.0.0.1:8780",
        system_name="致远 OA",
        expected_principal_ref="辛国茂",
    )


class FakeBroker:
    def __init__(self, *, result=None) -> None:
        self.received = None
        self.result = result or {"status": "succeeded"}

    def authenticate(self, *, challenge_id, csrf_token, csrf_cookie, credentials):
        self.received = dict(credentials)
        credentials.clear()
        return {**self.result, "challengeId": challenge_id}


if __name__ == "__main__":
    unittest.main()
