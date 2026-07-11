from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bscli.broker.credential import CredentialBroker
from bscli.core.auth_challenges import AuthChallengeStore, ChallengeAccessDenied
from bscli.core.session_secrets import SessionStateStore
from bscli.core.sessions import SessionRegistry


class CredentialBrokerTests(unittest.TestCase):
    def test_broker_uses_credentials_in_memory_and_activates_matching_session(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = SessionRegistry(root / "agentbridge.db", root / "profiles")
            session = sessions.get_or_create(
                user_subject="user-a",
                system_id="oa",
                expected_principal_ref="Alice",
            )
            challenges = AuthChallengeStore(root / "agentbridge.db")
            challenge = _create_challenge(challenges, session)
            csrf = challenges.issue_csrf(challenge["challenge_id"])
            adapter = FakeLoginAdapter(observed_principal="Alice")
            worker = FakeWorker()
            session_states = SessionStateStore(root / "session-secrets", protector=ReversingProtector())
            broker = CredentialBroker(
                challenge_store=challenges,
                session_registry=sessions,
                session_state_store=session_states,
                adapter_factory=lambda _challenge: adapter,
                worker_factory=lambda _session, _adapter: worker,
            )
            credentials = {"username": "alice.login", "password": "not-persisted"}

            result = broker.authenticate(
                challenge_id=challenge["challenge_id"],
                csrf_token=csrf,
                csrf_cookie=csrf,
                credentials=credentials,
            )

            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(sessions.get(session["session_id"])["state"], "active")
            self.assertEqual(challenges.get(challenge["challenge_id"])["state"], "succeeded")
            self.assertEqual(adapter.received_username, "alice.login")
            self.assertEqual(adapter.received_password, "not-persisted")
            self.assertEqual(credentials, {})
            self.assertIsNotNone(session_states.load(session["session_id"]))
            self.assertNotIn(b"not-persisted", (root / "agentbridge.db").read_bytes())
            self.assertNotIn(b"not-persisted", session_states.path_for(session["session_id"]).read_bytes())

    def test_broker_rejects_csrf_before_credentials_reach_adapter(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = SessionRegistry(root / "agentbridge.db", root / "profiles")
            session = sessions.get_or_create(
                user_subject="user-a",
                system_id="oa",
                expected_principal_ref="Alice",
            )
            challenges = AuthChallengeStore(root / "agentbridge.db")
            challenge = _create_challenge(challenges, session)
            csrf = challenges.issue_csrf(challenge["challenge_id"])
            adapter = FakeLoginAdapter(observed_principal="Alice")
            broker = CredentialBroker(
                challenge_store=challenges,
                session_registry=sessions,
                session_state_store=SessionStateStore(
                    root / "session-secrets",
                    protector=ReversingProtector(),
                ),
                adapter_factory=lambda _challenge: adapter,
                worker_factory=lambda _session, _adapter: FakeWorker(),
            )
            credentials = {"username": "alice", "password": "secret"}

            with self.assertRaises(ChallengeAccessDenied):
                broker.authenticate(
                    challenge_id=challenge["challenge_id"],
                    csrf_token=csrf,
                    csrf_cookie="wrong",
                    credentials=credentials,
                )

            self.assertIsNone(adapter.received_username)
            self.assertEqual(credentials, {})

    def test_broker_quarantines_session_when_observed_principal_differs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = SessionRegistry(root / "agentbridge.db", root / "profiles")
            session = sessions.get_or_create(
                user_subject="user-a",
                system_id="oa",
                expected_principal_ref="Alice",
            )
            challenges = AuthChallengeStore(root / "agentbridge.db")
            challenge = _create_challenge(challenges, session)
            csrf = challenges.issue_csrf(challenge["challenge_id"])
            broker = CredentialBroker(
                challenge_store=challenges,
                session_registry=sessions,
                session_state_store=SessionStateStore(
                    root / "session-secrets",
                    protector=ReversingProtector(),
                ),
                adapter_factory=lambda _challenge: FakeLoginAdapter(
                    observed_principal="Mallory"
                ),
                worker_factory=lambda _session, _adapter: FakeWorker(),
            )

            result = broker.authenticate(
                challenge_id=challenge["challenge_id"],
                csrf_token=csrf,
                csrf_cookie=csrf,
                credentials={"username": "mallory", "password": "secret"},
            )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error"]["code"], "PRINCIPAL_MISMATCH")
            self.assertEqual(sessions.get(session["session_id"])["state"], "quarantined")
            self.assertEqual(challenges.get(challenge["challenge_id"])["state"], "failed")


def _create_challenge(store: AuthChallengeStore, session: dict) -> dict:
    return store.create(
        user_subject=session["user_subject"],
        system_id=session["system_id"],
        session_id=session["session_id"],
        origin="http://oa.example.test",
        page_fingerprint="fake-login-v1",
        nonce="nonce",
        fields=FakeLoginAdapter.contract["fields"],
        card_base_url="http://127.0.0.1:8780",
        expected_principal_ref=session["expected_principal_ref"],
    )


class FakeLoginAdapter:
    contract = {
        "system_id": "oa",
        "system_name": "OA",
        "origin": "http://oa.example.test",
        "page_fingerprint": "fake-login-v1",
        "fields": [
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
    }

    def __init__(self, *, observed_principal: str) -> None:
        self.observed_principal = observed_principal
        self.received_username = None
        self.received_password = None

    def authentication_contract(self) -> dict:
        return self.contract

    def authenticate(self, _worker, credentials: dict, *, timeout_seconds: float) -> dict:
        self.received_username = credentials["username"]
        self.received_password = credentials["password"]
        return {
            "observed_principal_ref": self.observed_principal,
            "templates": {"count": 2, "transport": "central_http_session"},
        }


class FakeWorker:
    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return None

    def clear_session_state(self) -> None:
        return None

    def capture_session_state(self) -> dict:
        return {
            "cookies": [
                {
                    "name": "JSESSIONID",
                    "value": "cookie-secret",
                    "domain": "oa.example.test",
                    "path": "/",
                }
            ]
        }


class ReversingProtector:
    def protect(self, plaintext: bytes, *, context: bytes) -> bytes:
        return b"protected:" + context + b":" + plaintext[::-1]

    def unprotect(self, ciphertext: bytes, *, context: bytes) -> bytes:
        prefix = b"protected:" + context + b":"
        return ciphertext[len(prefix) :][::-1]


if __name__ == "__main__":
    unittest.main()
