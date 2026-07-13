import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from unittest.mock import MagicMock, patch

from bscli.adapters.seeyon_business_trip import BusinessTripOutcomeUnknown
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

    def test_business_trip_prepare_requires_trusted_card_then_consumes_it_once(self):
        with TemporaryDirectory() as tmp:
            worker = FakeWorker()
            service = self._service(tmp, worker)
            self._activate(service)
            prepared_payload = {
                "plan": {
                    "business_intent": "save_business_trip_request_draft",
                    "target": {"template_id": "template-1"},
                    "form_contract": {"version": "v1", "fingerprint": "sha256:test"},
                    "exact_input": {"reason": "Test"},
                },
                "summary": {
                    "title": "保存出差申请草稿",
                    "system": "致远 OA",
                    "fields": [{"label": "事由", "value": "Test"}],
                },
            }
            with patch(
                "bscli.core.central_service.prepare_business_trip_draft",
                return_value=prepared_payload,
            ) as prepare_draft:
                started = service.invoke(
                    user_subject="user-a",
                    capability_name="oa.business_trip.prepare",
                    arguments={},
                    idempotency_key="business-trip-fields-1",
                )
                self.assertEqual(started["status"], "requires_user_action")
                self.assertEqual(started["error"]["code"], "FIELD_INPUT_REQUIRED")
                submission_id = started["nextAction"]["inputSubmissionId"]
                _submit_business_trip_fields(service, submission_id)
                prepared = service.invoke(
                    user_subject="user-a",
                    capability_name="oa.business_trip.prepare",
                    arguments={"input_submission_id": submission_id},
                    idempotency_key="business-trip-prepare-1",
                )

            self.assertEqual(prepared["status"], "requires_user_action")
            self.assertEqual(prepared["error"]["code"], "WRITE_AUTHORIZATION_REQUIRED")
            prepare_draft.assert_called_once()
            self.assertEqual(
                prepare_draft.call_args.args[2]["reason"],
                "Test",
            )
            self.assertNotIn("Test", str(prepared["nextAction"]))
            field_submission = service.field_submissions.get(submission_id)
            self.assertEqual(field_submission["state"], "consumed")
            authorization_id = prepared["nextAction"]["authorizationId"]
            authorization = service.write_authorizations.get(authorization_id)
            self.assertEqual(authorization["state"], "pending")
            self.assertEqual(
                prepared["nextAction"]["then"]["capability"],
                "oa.business_trip.save_draft",
            )

            csrf = service.write_authorizations.issue_csrf(authorization_id)
            service.write_authorizations.decide(
                authorization_id,
                decision="approve",
                csrf_token=csrf,
                csrf_cookie=csrf,
            )

            def save(_adapter, _worker, _plan, *, enter_commit_boundary):
                enter_commit_boundary()
                return {
                    "draft_saved": True,
                    "workflow_submitted": False,
                    "submitted_count": 0,
                    "verification": {"confirmed": True},
                }

            with patch(
                "bscli.core.central_service.save_business_trip_draft",
                side_effect=save,
            ):
                committed = service.invoke(
                    user_subject="user-a",
                    capability_name="oa.business_trip.save_draft",
                    arguments={"authorization_id": authorization_id},
                    idempotency_key="business-trip-save-1",
                )

            self.assertEqual(committed["status"], "succeeded")
            self.assertTrue(committed["result"]["draft_saved"])
            consumed = service.write_authorizations.get(authorization_id)
            self.assertEqual(consumed["state"], "consumed")
            self.assertEqual(consumed["commit_operation_id"], committed["operationId"])

    def test_business_trip_commit_before_approval_has_no_effect_and_unknown_is_durable(self):
        with TemporaryDirectory() as tmp:
            worker = FakeWorker()
            service = self._service(tmp, worker)
            self._activate(service)
            prepared_payload = {
                "plan": {
                    "business_intent": "save_business_trip_request_draft",
                    "target": {"template_id": "template-1"},
                    "form_contract": {"version": "v1", "fingerprint": "sha256:test"},
                    "exact_input": {"reason": "Test"},
                },
                "summary": {"title": "Draft", "fields": []},
            }
            with patch(
                "bscli.core.central_service.prepare_business_trip_draft",
                return_value=prepared_payload,
            ):
                started = service.invoke(
                    user_subject="user-a",
                    capability_name="oa.business_trip.prepare",
                    arguments={},
                )
                submission_id = started["nextAction"]["inputSubmissionId"]
                _submit_business_trip_fields(service, submission_id)
                prepared = service.invoke(
                    user_subject="user-a",
                    capability_name="oa.business_trip.prepare",
                    arguments={"input_submission_id": submission_id},
                )
            authorization_id = prepared["nextAction"]["authorizationId"]

            with patch("bscli.core.central_service.save_business_trip_draft") as save:
                blocked = service.invoke(
                    user_subject="user-a",
                    capability_name="oa.business_trip.save_draft",
                    arguments={"authorization_id": authorization_id},
                )
            self.assertEqual(blocked["status"], "requires_user_action")
            save.assert_not_called()

            csrf = service.write_authorizations.issue_csrf(authorization_id)
            service.write_authorizations.decide(
                authorization_id,
                decision="approve",
                csrf_token=csrf,
                csrf_cookie=csrf,
            )

            def uncertain(_adapter, _worker, _plan, *, enter_commit_boundary):
                enter_commit_boundary()
                raise BusinessTripOutcomeUnknown("readback failed")

            with patch(
                "bscli.core.central_service.save_business_trip_draft",
                side_effect=uncertain,
            ):
                unknown = service.invoke(
                    user_subject="user-a",
                    capability_name="oa.business_trip.save_draft",
                    arguments={"authorization_id": authorization_id},
                    idempotency_key="business-trip-unknown-1",
                )

            self.assertEqual(unknown["status"], "unknown")
            self.assertEqual(unknown["error"]["code"], "RESULT_UNKNOWN")
            self.assertEqual(
                service.operations.get(unknown["operationId"])["status"],
                "unknown",
            )

    def test_business_trip_field_submission_cannot_cross_users(self):
        with TemporaryDirectory() as tmp:
            worker = FakeWorker()
            service = self._service(tmp, worker)
            self._activate(service)
            started = service.invoke(
                user_subject="user-a",
                capability_name="oa.business_trip.prepare",
                arguments={},
            )
            submission_id = started["nextAction"]["inputSubmissionId"]
            _submit_business_trip_fields(service, submission_id)
            session = service.sessions.get_or_create(
                user_subject="user-b",
                system_id="oa",
                expected_principal_ref="Bob",
            )
            session = service.sessions.activate(
                session["session_id"],
                observed_principal_ref="Bob",
            )
            service.session_states.save(session["session_id"], {"cookies": []})

            response = service.invoke(
                user_subject="user-b",
                capability_name="oa.business_trip.prepare",
                arguments={"input_submission_id": submission_id},
            )

            self.assertEqual(response["status"], "requires_user_action")
            self.assertEqual(response["error"]["code"], "FIELD_INPUT_UNAVAILABLE")
            self.assertEqual(
                service.field_submissions.get(submission_id)["state"],
                "submitted",
            )

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


def _business_trip_arguments():
    return {
        "start_time": "2026-07-13 09:00",
        "end_time": "2026-07-13 18:00",
        "travel_mode": "火车",
        "origin": "济南",
        "destination": "青岛",
        "reason": "Test",
        "has_direct_supervisor": False,
    }


def _submit_business_trip_fields(service, submission_id):
    csrf = service.field_submissions.issue_csrf(submission_id)
    service.field_submissions.submit(
        submission_id,
        csrf_token=csrf,
        csrf_cookie=csrf,
        values=_business_trip_arguments(),
    )


if __name__ == "__main__":
    unittest.main()
