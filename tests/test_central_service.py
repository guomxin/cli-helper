import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from unittest.mock import MagicMock

from bscli.adapters.seeyon_central import SeeyonLoginRequired
from bscli.core.central_service import CentralCapabilityService


BASE_URL = "http://oa.example.test/seeyon/main.do?method=main"


class CentralCapabilityServiceTests(unittest.TestCase):
    def test_invoke_restores_session_and_persists_operation(self):
        with TemporaryDirectory() as tmp:
            worker = FakeWorker()
            service = self._service(tmp, worker)
            self._activate(service)
            service.adapter.invoke_capability = MagicMock(
                return_value={"count": 1, "items": [{"title": "Pending"}]}
            )

            response = service.invoke(
                user_subject="user-a",
                capability_name="oa.workflow.pending.list",
                arguments={"limit": 5},
                idempotency_key="pending-1",
                request_id="request-1",
            )

            self.assertEqual(response["status"], "succeeded")
            self.assertEqual(response["requestId"], "request-1")
            self.assertEqual(response["result"]["count"], 1)
            self.assertEqual(worker.restored, {"cookies": []})
            operation = service.operations.get(response["operationId"])
            self.assertEqual(operation["user_subject"], "user-a")
            self.assertEqual(operation["status"], "succeeded")

    def test_login_expiry_is_shared_by_cli_and_future_mcp_callers(self):
        with TemporaryDirectory() as tmp:
            worker = FakeWorker()
            service = self._service(tmp, worker)
            session = self._activate(service)
            service.adapter.invoke_capability = MagicMock(
                side_effect=SeeyonLoginRequired("OA expired")
            )

            response = service.invoke(
                user_subject="user-a",
                capability_name="oa.workflow.done.list",
                arguments={},
            )

            self.assertEqual(response["status"], "requires_user_action")
            self.assertEqual(response["error"]["code"], "LOGIN_REQUIRED")
            self.assertEqual(response["nextAction"]["sessionState"], "expired")
            self.assertEqual(service.sessions.get(session["session_id"])["state"], "expired")
            self.assertIsNone(service.session_states.load(session["session_id"]))

    def test_start_login_uses_server_bound_expected_principal(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp, FakeWorker())
            service.sessions.get_or_create(
                user_subject="user-a",
                system_id="oa",
                expected_principal_ref="Alice",
            )

            response = service.start_login(
                user_subject="user-a",
                expected_principal_ref=None,
                card_base_url="http://127.0.0.1:8780",
                ttl_seconds=300,
            )

            self.assertEqual(response["status"], "requires_user_action")
            self.assertEqual(response["challenge"]["expectedPrincipalRef"], "Alice")
            self.assertTrue(response["nextAction"]["cardUrl"].startswith("http://127.0.0.1:8780/auth/"))

    def test_operation_lookup_does_not_cross_user_boundary(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp, FakeWorker())
            response = service.invoke(
                user_subject="user-a",
                capability_name="oa.template.list",
                arguments={},
                idempotency_key="login-required-record",
            )

            with self.assertRaisesRegex(KeyError, "operation not found"):
                service.get_operation(
                    user_subject="user-b",
                    operation_id=response["operationId"],
                )

    def test_same_user_browser_work_is_serialized_inside_service(self):
        with TemporaryDirectory() as tmp:
            service = CentralCapabilityService(
                home=Path(tmp),
                base_url=BASE_URL,
                worker_factory=lambda _session, _adapter: FakeWorker(),
            )
            self._activate(service)
            state_lock = threading.Lock()
            active = 0
            maximum_active = 0

            def invoke_adapter(_name, _worker, _arguments):
                nonlocal active, maximum_active
                with state_lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                time.sleep(0.05)
                with state_lock:
                    active -= 1
                return {"count": 0, "items": []}

            service.adapter.invoke_capability = invoke_adapter
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(
                        service.invoke,
                        user_subject="user-a",
                        capability_name="oa.workflow.pending.list",
                        arguments={},
                        idempotency_key=f"parallel-{index}",
                    )
                    for index in range(2)
                ]
                responses = [future.result() for future in futures]

        self.assertEqual(maximum_active, 1)
        self.assertTrue(all(response["status"] == "succeeded" for response in responses))

    @staticmethod
    def _service(tmp, worker):
        return CentralCapabilityService(
            home=Path(tmp),
            base_url=BASE_URL,
            worker_factory=lambda _session, _adapter: worker,
        )

    @staticmethod
    def _activate(service):
        session = service.sessions.get_or_create(
            user_subject="user-a",
            system_id="oa",
            expected_principal_ref="Alice",
        )
        session = service.sessions.activate(
            session["session_id"],
            observed_principal_ref="Alice",
        )
        service.session_states.save(session["session_id"], {"cookies": []})
        return session


class FakeWorker:
    def __init__(self):
        self.restored = None

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return None

    def restore_session_state(self, state):
        self.restored = state

    def capture_session_state(self):
        return {"cookies": []}


if __name__ == "__main__":
    unittest.main()
