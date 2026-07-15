import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from unittest.mock import MagicMock, patch

from bscli.adapters.seeyon_business_trip import BusinessTripOutcomeUnknown
from bscli.adapters.seeyon_central import (
    SeeyonLoginRequired,
    SeeyonSessionCheckUnavailable,
)
from bscli.core.central_service import CentralCapabilityService
from bscli.core.interactions import InteractionNotFound
from bscli.core.session_secrets import SessionStateAccessDenied


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

    def test_temporary_session_check_failure_preserves_active_session(self):
        with TemporaryDirectory() as tmp:
            worker = FakeWorker()
            service = self._service(tmp, worker)
            session = self._activate(service)
            service.adapter.invoke_capability = MagicMock(
                side_effect=SeeyonSessionCheckUnavailable(
                    "OA session check did not return JSON (HTTP 200, content_type=text/html)."
                )
            )

            response = service.invoke(
                user_subject="user-a",
                capability_name="oa.workflow.pending.list",
                arguments={},
            )

            self.assertEqual(response["status"], "requires_user_action")
            self.assertEqual(response["error"]["code"], "SESSION_CHECK_UNAVAILABLE")
            self.assertTrue(response["nextAction"]["sessionPreserved"])
            self.assertIn("HTTP 200", response["error"]["message"])
            self.assertEqual(service.sessions.get(session["session_id"])["state"], "active")
            self.assertIsNotNone(service.session_states.load(session["session_id"]))

    def test_session_status_live_checks_an_active_session(self):
        with TemporaryDirectory() as tmp:
            worker = FakeWorker()
            service = self._service(tmp, worker)
            session = self._activate(service)
            service.adapter.probe_session = MagicMock(
                return_value={
                    "authenticated": True,
                    "template_count": 118,
                    "transport": "central_http_session",
                    "browser_bridge_used": False,
                }
            )

            response = service.session_status(user_subject="user-a")

            self.assertEqual(response["status"], "active")
            self.assertEqual(response["statusSource"], "live")
            self.assertIsNotNone(response["checkedAt"])
            self.assertEqual(response["lastVerifiedAt"], session["last_verified_at"])
            self.assertEqual(
                response["lastVerifiedAt"],
                service.sessions.get(session["session_id"])["last_verified_at"],
            )
            self.assertEqual(worker.restored, {"cookies": []})
            service.adapter.probe_session.assert_called_once_with(worker)

    def test_session_status_reports_live_expiry_and_deletes_invalid_state(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp, FakeWorker())
            session = self._activate(service)
            service.adapter.probe_session = MagicMock(
                side_effect=SeeyonLoginRequired("OA expired")
            )

            response = service.session_status(user_subject="user-a")

            self.assertEqual(response["status"], "expired")
            self.assertEqual(response["statusSource"], "live")
            self.assertIsNotNone(response["checkedAt"])
            self.assertEqual(service.sessions.get(session["session_id"])["state"], "expired")
            self.assertIsNone(service.session_states.load(session["session_id"]))

    def test_session_status_returns_check_unavailable_without_deleting_state(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp, FakeWorker())
            session = self._activate(service)
            service.adapter.probe_session = MagicMock(
                side_effect=SeeyonSessionCheckUnavailable(
                    "OA session check received a temporary response (HTTP 503)."
                )
            )

            response = service.session_status(user_subject="user-a")

            self.assertEqual(response["status"], "requires_user_action")
            self.assertEqual(response["error"]["code"], "SESSION_CHECK_UNAVAILABLE")
            self.assertEqual(response["statusSource"], "live")
            self.assertEqual(response["session"]["status"], "active")
            self.assertTrue(response["nextAction"]["sessionPreserved"])
            self.assertIsNotNone(service.session_states.load(session["session_id"]))

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
            self.assertEqual(response["interaction"]["type"], "credential")
            self.assertEqual(
                response["interaction"]["interactionId"],
                response["nextAction"]["interactionId"],
            )

    def test_start_login_reuses_live_active_session_without_card(self):
        with TemporaryDirectory() as tmp:
            worker = FakeWorker()
            service = self._service(tmp, worker)
            session = self._activate(service)
            service.adapter.probe_session = MagicMock(
                return_value={
                    "authenticated": True,
                    "template_count": 118,
                    "transport": "central_http_session",
                    "browser_bridge_used": False,
                }
            )

            response = service.start_login(
                user_subject="user-a",
                expected_principal_ref="Alice",
                card_base_url="http://127.0.0.1:8780",
            )

            self.assertEqual(response["status"], "succeeded")
            self.assertTrue(response["reused"])
            self.assertNotIn("challenge", response)
            self.assertIsNone(response["nextAction"])
            self.assertEqual(response["result"]["templateCount"], 118)
            self.assertFalse(response["result"]["browserBridgeUsed"])
            self.assertEqual(worker.restored, {"cookies": []})
            self.assertEqual(
                service.sessions.get(session["session_id"])["state"],
                "active",
            )

    def test_completed_credential_interaction_resumes_to_active_session(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp, FakeWorker())
            started = service.start_login(
                user_subject="user-a",
                expected_principal_ref="Alice",
                card_base_url="http://127.0.0.1:8780",
            )
            challenge_id = started["challenge"]["challengeId"]
            csrf = service.challenges.issue_csrf(challenge_id)
            service.challenges.claim(
                challenge_id,
                csrf_token=csrf,
                csrf_cookie=csrf,
            )
            service.challenges.complete(
                challenge_id,
                result={"principal": "Alice"},
            )
            session = service.sessions.find(user_subject="user-a", system_id="oa")
            service.sessions.activate(
                session["session_id"],
                observed_principal_ref="Alice",
            )

            response = service.resume_interaction(
                user_subject="user-a",
                interaction_id=started["interaction"]["interactionId"],
            )

            self.assertEqual(response["status"], "succeeded")
            self.assertEqual(response["interaction"]["state"], "completed")
            self.assertEqual(response["nextAction"]["type"], "retry_original_request")
            self.assertEqual(response["result"]["session"]["status"], "active")

    def test_failed_credential_interaction_cannot_be_reported_as_resumed(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp, FakeWorker())
            started = service.start_login(
                user_subject="user-a",
                expected_principal_ref="Alice",
                card_base_url="http://127.0.0.1:8780",
            )
            challenge_id = started["challenge"]["challengeId"]
            csrf = service.challenges.issue_csrf(challenge_id)
            service.challenges.claim(
                challenge_id,
                csrf_token=csrf,
                csrf_cookie=csrf,
            )
            service.challenges.fail(
                challenge_id,
                code="LOGIN_REJECTED",
                message="OA rejected the login.",
            )

            response = service.resume_interaction(
                user_subject="user-a",
                interaction_id=started["interaction"]["interactionId"],
            )

            self.assertEqual(response["status"], "failed")
            self.assertEqual(response["error"]["code"], "INTERACTION_FAILED")
            self.assertEqual(response["nextAction"]["type"], "start_again")

    def test_start_login_creates_card_only_after_live_probe_reports_expired(self):
        with TemporaryDirectory() as tmp:
            worker = FakeWorker()
            service = self._service(tmp, worker)
            session = self._activate(service)
            service.adapter.probe_session = MagicMock(
                side_effect=SeeyonLoginRequired("OA expired")
            )

            response = service.start_login(
                user_subject="user-a",
                expected_principal_ref="Alice",
                card_base_url="http://127.0.0.1:8780",
            )

            self.assertEqual(response["status"], "requires_user_action")
            self.assertFalse(response["reused"])
            self.assertEqual(
                response["nextAction"]["type"],
                "open_authentication_card",
            )
            self.assertEqual(
                service.sessions.get(session["session_id"])["state"],
                "expired",
            )
            self.assertIsNone(service.session_states.load(session["session_id"]))

    def test_start_login_probe_failure_does_not_prompt_for_credentials(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp, FakeWorker())
            session = self._activate(service)
            service.adapter.probe_session = MagicMock(
                side_effect=TimeoutError("OA unavailable")
            )

            response = service.start_login(
                user_subject="user-a",
                expected_principal_ref="Alice",
                card_base_url="http://127.0.0.1:8780",
            )

            self.assertEqual(response["status"], "requires_user_action")
            self.assertEqual(response["error"]["code"], "SESSION_CHECK_UNAVAILABLE")
            self.assertEqual(
                response["nextAction"]["type"],
                "retry_session_check",
            )
            self.assertNotIn("challenge", response)
            self.assertEqual(
                service.sessions.get(session["session_id"])["state"],
                "active",
            )
            self.assertIsNotNone(service.session_states.load(session["session_id"]))

    def test_runtime_identity_mismatch_is_actionable_and_preserves_session(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp, FakeWorker())
            session = self._activate(service)
            inaccessible = InaccessibleSessionStateStore()
            service.session_states = inaccessible

            response = service.invoke(
                user_subject="user-a",
                capability_name="oa.template.list",
                arguments={},
            )

            self.assertEqual(response["status"], "requires_user_action")
            self.assertEqual(response["error"]["code"], "SESSION_RUNTIME_MISMATCH")
            self.assertEqual(
                response["nextAction"]["type"],
                "retry_via_bound_central_runtime",
            )
            self.assertTrue(response["nextAction"]["sessionPreserved"])
            self.assertEqual(
                service.sessions.get(session["session_id"])["state"],
                "active",
            )
            self.assertFalse(inaccessible.deleted)

    def test_start_login_runtime_mismatch_does_not_replace_session_with_card(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp, FakeWorker())
            session = self._activate(service)
            inaccessible = InaccessibleSessionStateStore()
            service.session_states = inaccessible

            response = service.start_login(
                user_subject="user-a",
                expected_principal_ref="Alice",
                card_base_url="http://127.0.0.1:8780",
            )

            self.assertEqual(response["status"], "requires_user_action")
            self.assertEqual(response["error"]["code"], "SESSION_RUNTIME_MISMATCH")
            self.assertNotIn("challenge", response)
            self.assertEqual(
                service.sessions.get(session["session_id"])["state"],
                "active",
            )
            self.assertFalse(inaccessible.deleted)

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

    def test_interaction_resume_completes_business_trip_without_duplicate_effects(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp, FakeWorker())
            self._activate(service)
            started = service.invoke(
                user_subject="user-a",
                capability_name="oa.business_trip.prepare",
                arguments={},
            )
            field_interaction = started["interaction"]
            self.assertEqual(field_interaction["type"], "business_input")
            self.assertEqual(field_interaction["state"], "pending")
            self.assertEqual(
                field_interaction["interactionId"],
                started["nextAction"]["interactionId"],
            )
            with self.assertRaises(InteractionNotFound):
                service.get_interaction(
                    user_subject="user-b",
                    interaction_id=field_interaction["interactionId"],
                )

            submission_id = started["nextAction"]["inputSubmissionId"]
            _submit_business_trip_fields(service, submission_id)
            completed_field = service.get_interaction(
                user_subject="user-a",
                interaction_id=field_interaction["interactionId"],
            )["interaction"]
            self.assertEqual(completed_field["state"], "completed")
            self.assertTrue(completed_field["resume"]["ready"])

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
                    "effect": "保存待发草稿",
                    "fields": [{"label": "事由", "value": "Test"}],
                },
            }
            with patch(
                "bscli.core.central_service.prepare_business_trip_draft",
                return_value=prepared_payload,
            ) as prepare_draft:
                prepared = service.resume_interaction(
                    user_subject="user-a",
                    interaction_id=field_interaction["interactionId"],
                )
                repeated_prepare = service.resume_interaction(
                    user_subject="user-a",
                    interaction_id=field_interaction["interactionId"],
                )

            self.assertEqual(prepared["status"], "requires_user_action")
            self.assertEqual(
                prepared["resumedFromInteractionId"],
                field_interaction["interactionId"],
            )
            self.assertEqual(prepared["interaction"]["type"], "execution_authorization")
            self.assertEqual(repeated_prepare["status"], "already_resumed")
            prepare_draft.assert_called_once()

            authorization_id = prepared["nextAction"]["authorizationId"]
            authorization_interaction = prepared["interaction"]
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
            ) as save_draft:
                committed = service.resume_interaction(
                    user_subject="user-a",
                    interaction_id=authorization_interaction["interactionId"],
                )
                repeated_commit = service.resume_interaction(
                    user_subject="user-a",
                    interaction_id=authorization_interaction["interactionId"],
                )

            self.assertEqual(committed["status"], "succeeded")
            self.assertTrue(committed["result"]["draft_saved"])
            self.assertEqual(repeated_commit["status"], "already_resumed")
            save_draft.assert_called_once()

    def test_interaction_resume_retries_after_session_is_reauthenticated(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp, FakeWorker())
            session = self._activate(service)
            started = service.invoke(
                user_subject="user-a",
                capability_name="oa.business_trip.prepare",
                arguments={},
            )
            submission_id = started["nextAction"]["inputSubmissionId"]
            interaction_id = started["interaction"]["interactionId"]
            _submit_business_trip_fields(service, submission_id)
            service.sessions.mark_expired(session["session_id"], "OA expired")

            blocked = service.resume_interaction(
                user_subject="user-a",
                interaction_id=interaction_id,
            )
            self.assertEqual(blocked["error"]["code"], "LOGIN_REQUIRED")

            service.sessions.activate(
                session["session_id"],
                observed_principal_ref="Alice",
            )
            service.session_states.save(session["session_id"], {"cookies": []})
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
            ) as prepare_draft:
                resumed = service.resume_interaction(
                    user_subject="user-a",
                    interaction_id=interaction_id,
                )

            self.assertEqual(resumed["status"], "requires_user_action")
            self.assertEqual(
                resumed["error"]["code"],
                "WRITE_AUTHORIZATION_REQUIRED",
            )
            prepare_draft.assert_called_once()

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


class InaccessibleSessionStateStore:
    def __init__(self):
        self.deleted = False

    def load(self, _session_id):
        raise SessionStateAccessDenied("different Windows security principal")

    def delete(self, _session_id):
        self.deleted = True


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
