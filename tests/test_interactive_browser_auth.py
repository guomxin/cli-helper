from __future__ import annotations

import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import time
import unittest
from urllib.parse import urlencode

from bscli.adapters.base import AdapterLoginRequired
from bscli.auth.interactive_browser import TrustedInteractiveBrowserApplication
from bscli.broker.interactive_browser import InteractiveBrowserBroker
from bscli.core.auth_challenges import AuthChallengeStore
from bscli.core.sessions import SessionRegistry


class TrustedInteractiveBrowserTests(unittest.TestCase):
    def test_card_starts_data_blind_remote_browser_ui(self):
        with TemporaryDirectory() as tmp:
            store = AuthChallengeStore(Path(tmp) / "agentbridge.db")
            challenge = _challenge(store)
            broker = StubInteractiveBroker()
            app = TrustedInteractiveBrowserApplication(
                challenge_store=store,
                broker=broker,
            )

            page = app.get_card(challenge["challenge_id"], secure_cookie=True)

            html = page.body.decode("utf-8")
            self.assertEqual(page.status, 200)
            self.assertIn("启动安全登录", html)
            self.assertIn("受控浏览器", html)
            self.assertIn("interactive/frame", html)
            self.assertIn("interactive/event", html)
            self.assertNotIn('name="password"', html)
            self.assertNotIn('name="otp"', html)
            self.assertIn("connect-src 'self'", page.headers["Content-Security-Policy"])
            self.assertIn("img-src blob:", page.headers["Content-Security-Policy"])
            self.assertIn("Secure", page.headers["Set-Cookie"])

    def test_start_claims_csrf_and_returns_control_token_only_to_card(self):
        with TemporaryDirectory() as tmp:
            store = AuthChallengeStore(Path(tmp) / "agentbridge.db")
            challenge = _challenge(store)
            broker = StubInteractiveBroker()
            app = TrustedInteractiveBrowserApplication(
                challenge_store=store,
                broker=broker,
            )
            page = app.get_card(challenge["challenge_id"], secure_cookie=False)
            html = page.body.decode("utf-8")
            csrf = re.search(r'const csrfToken = "([^"]+)"', html).group(1)
            cookie = page.headers["Set-Cookie"].split(";", 1)[0].split("=", 1)[1]

            response = app.start(
                challenge["challenge_id"],
                body=urlencode({"csrf_token": csrf}).encode("utf-8"),
                content_type="application/x-www-form-urlencoded",
                csrf_cookie=cookie,
            )

            payload = json.loads(response.body)
            self.assertEqual(response.status, 202)
            self.assertEqual(payload["controlToken"], "short-lived-control")
            self.assertEqual(broker.started["challenge_id"], challenge["challenge_id"])
            self.assertEqual(broker.started["csrf_token"], csrf)

    def test_broker_executes_events_on_owner_thread_and_saves_verified_session(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AuthChallengeStore(root / "agentbridge.db")
            sessions = SessionRegistry(root / "agentbridge.db", root / "profiles")
            session = sessions.get_or_create(
                user_subject="user-a",
                system_id="yuque",
                expected_principal_ref="辛国茂",
            )
            challenge = _challenge(store, session_id=session["session_id"])
            state_store = FakeStateStore()
            adapter = FakeInteractiveAdapter()
            worker = FakeInteractiveWorker()
            broker = InteractiveBrowserBroker(
                challenge_store=store,
                session_registry=sessions,
                session_state_store=state_store,
                adapter_factory=lambda _challenge: adapter,
                worker_factory=lambda _session, _adapter: worker,
                login_timeout_seconds=30,
            )
            csrf = store.issue_csrf(challenge["challenge_id"])

            started = broker.start(
                challenge_id=challenge["challenge_id"],
                csrf_token=csrf,
                csrf_cookie=csrf,
            )
            broker.send_event(
                challenge_id=challenge["challenge_id"],
                control_token=started["controlToken"],
                event={"type": "pointer_down", "payload": {"x": 40, "y": 80}},
            )
            broker.send_event(
                challenge_id=challenge["challenge_id"],
                control_token=started["controlToken"],
                event={"type": "pointer_move", "payload": {"x": 350, "y": 80}},
            )
            broker.send_event(
                challenge_id=challenge["challenge_id"],
                control_token=started["controlToken"],
                event={"type": "pointer_up", "payload": {"x": 410, "y": 80}},
            )
            broker.send_event(
                challenge_id=challenge["challenge_id"],
                control_token=started["controlToken"],
                event={"type": "type_text", "payload": {"text": "654321"}},
            )
            deadline = time.monotonic() + 5
            while store.get(challenge["challenge_id"])["state"] == "processing":
                if time.monotonic() >= deadline:
                    self.fail("interactive login did not complete")
                time.sleep(0.05)

            completed = store.get(challenge["challenge_id"])
            active = sessions.get(session["session_id"])
            self.assertEqual(completed["state"], "succeeded")
            self.assertEqual(active["state"], "active")
            self.assertEqual(active["downstream_principal_ref"], "辛国茂")
            self.assertEqual(state_store.saved, {"cookies": [{"name": "session"}]})
            self.assertTrue(worker.page.mouse.events)
            self.assertEqual(worker.page.keyboard.inserted, ["654321"])
            broker.shutdown()


class StubInteractiveBroker:
    def __init__(self) -> None:
        self.started = None

    def start(self, **kwargs):
        self.started = kwargs
        return {
            "status": "processing",
            "challengeId": kwargs["challenge_id"],
            "controlToken": "short-lived-control",
            "viewport": {"width": 430, "height": 760},
        }


class FakeStateStore:
    def __init__(self) -> None:
        self.saved = None

    def delete(self, _session_id: str) -> None:
        self.saved = None

    def save(self, _session_id: str, value: dict) -> None:
        self.saved = value


class FakeInteractiveAdapter:
    def authentication_contract(self) -> dict:
        return {
            "system_id": "yuque",
            "system_name": "部门信息库",
            "origin": "https://tc-aiot.yuque.com",
            "page_fingerprint": "yuque-interactive-login-v1",
            "authentication_mode": "interactive_browser",
            "fields": [],
            "interactive": {"viewport": {"width": 430, "height": 760}},
        }

    def begin_interactive_login(self, worker, *, timeout_seconds: float) -> dict:
        del timeout_seconds
        worker.page_url = "https://tc-aiot.yuque.com/login"
        return {"url": worker.page_url}

    def probe_session(self, worker) -> dict:
        if not worker.page.logged_in:
            raise AdapterLoginRequired("not logged in")
        return {
            "authenticated": True,
            "observed_principal_ref": "辛国茂",
            "transport": "central_browser_cookie",
        }


class FakeInteractiveWorker:
    def __init__(self) -> None:
        self.page = FakePage()
        self.page_url = "about:blank"
        self.owner_thread = None

    def __enter__(self):
        import threading

        self.owner_thread = threading.get_ident()
        self.page.owner_thread = self.owner_thread
        return self

    def __exit__(self, *_args):
        return None

    def clear_session_state(self) -> None:
        return None

    def capture_session_state(self) -> dict:
        return {"cookies": [{"name": "session"}]}


class FakePage:
    def __init__(self) -> None:
        self.logged_in = False
        self.owner_thread = None
        self.mouse = FakeMouse(self)
        self.keyboard = FakeKeyboard(self)
        self.viewport = None

    def set_viewport_size(self, viewport: dict) -> None:
        self._assert_owner()
        self.viewport = viewport

    def screenshot(self, *, type: str) -> bytes:
        self._assert_owner()
        if type != "png":
            raise AssertionError("unexpected screenshot type")
        return b"\x89PNG\r\n\x1a\n"

    def _assert_owner(self) -> None:
        import threading

        if threading.get_ident() != self.owner_thread:
            raise AssertionError("browser action did not run on the owner thread")


class FakeMouse:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.events = []

    def move(self, x: int, y: int) -> None:
        self.page._assert_owner()
        self.events.append(("move", x, y))

    def down(self) -> None:
        self.page._assert_owner()
        self.events.append(("down",))

    def up(self) -> None:
        self.page._assert_owner()
        self.events.append(("up",))

    def click(self, x: int, y: int) -> None:
        self.page._assert_owner()
        self.events.append(("click", x, y))

    def wheel(self, x: int, y: int) -> None:
        self.page._assert_owner()
        self.events.append(("wheel", x, y))


class FakeKeyboard:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.inserted = []

    def insert_text(self, value: str) -> None:
        self.page._assert_owner()
        self.inserted.append(value)
        if value == "654321":
            self.page.logged_in = True

    def press(self, key: str) -> None:
        self.page._assert_owner()
        self.inserted.append(f"<{key}>")


def _challenge(
    store: AuthChallengeStore,
    *,
    session_id: str = "session-a",
) -> dict:
    return store.create(
        user_subject="user-a",
        system_id="yuque",
        session_id=session_id,
        origin="https://tc-aiot.yuque.com",
        page_fingerprint="yuque-interactive-login-v1",
        nonce="nonce",
        fields=[],
        card_base_url="https://10.10.50.213:8780",
        system_name="部门信息库",
        expected_principal_ref="辛国茂",
        ttl_seconds=600,
        challenge_type="interactive_browser_login",
    )


if __name__ == "__main__":
    unittest.main()
