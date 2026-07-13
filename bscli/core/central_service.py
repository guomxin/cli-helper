from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import threading
from typing import Callable, Iterator

from bscli.adapters.seeyon_central import (
    SeeyonCentralAdapter,
    SeeyonLoginRequired,
    build_central_capability_registry,
)
from bscli.adapters.seeyon_business_trip import (
    BUSINESS_TRIP_FIELD_CARD_SCHEMA,
    BUSINESS_TRIP_PREPARE_CAPABILITY,
    BUSINESS_TRIP_SAVE_CAPABILITY,
    BusinessTripContractMismatch,
    BusinessTripOutcomeUnknown,
    prepare_business_trip_draft,
    save_business_trip_draft,
)
from bscli.browser.central import CentralBrowserWorker
from bscli.core.auth_challenges import AuthChallengeStore
from bscli.core.capability import CapabilityRegistry
from bscli.core.capability_runtime import (
    CapabilityContext,
    CapabilityEngine,
    OutcomeUnknown,
    RequiresUserAction,
)
from bscli.core.operations import OperationStore
from bscli.core.field_submissions import (
    FieldSubmissionAccessDenied,
    FieldSubmissionIntegrityError,
    FieldSubmissionNotFound,
    FieldSubmissionStateError,
    FieldSubmissionStore,
)
from bscli.core.session_secrets import SessionStateStore
from bscli.core.sessions import SessionRegistry
from bscli.core.write_authorizations import (
    WriteAuthorizationAccessDenied,
    WriteAuthorizationNotFound,
    WriteAuthorizationStateError,
    WriteAuthorizationStore,
)


WorkerFactory = Callable[[dict, SeeyonCentralAdapter], CentralBrowserWorker]


class CentralCapabilityService:
    def __init__(
        self,
        *,
        home: Path | str,
        base_url: str,
        registry: CapabilityRegistry | None = None,
        worker_factory: WorkerFactory | None = None,
        trusted_card_base_url: str = "http://127.0.0.1:8780",
    ) -> None:
        self.home = Path(home)
        self.db_path = self.home / "agentbridge.db"
        self.registry = registry or build_central_capability_registry()
        self.operations = OperationStore(self.db_path)
        self.sessions = SessionRegistry(self.db_path, self.home / "profiles")
        self.session_states = SessionStateStore(self.home / "session-secrets")
        self.challenges = AuthChallengeStore(self.db_path)
        self.field_submissions = FieldSubmissionStore(self.db_path)
        self.write_authorizations = WriteAuthorizationStore(self.db_path)
        self.adapter = SeeyonCentralAdapter(base_url=base_url)
        self.worker_factory = worker_factory or self._default_worker_factory
        self.trusted_card_base_url = trusted_card_base_url
        self._locks_guard = threading.Lock()
        self._session_locks: dict[str, threading.Lock] = {}

    def list_capabilities(self, *, system: str | None = None) -> dict:
        return {
            "protocolVersion": "0.1",
            "capabilities": [spec.to_dict() for spec in self.registry.list(system=system)],
        }

    def describe_capability(self, name: str) -> dict:
        return {
            "protocolVersion": "0.1",
            "capability": self.registry.describe(name),
        }

    def invoke(
        self,
        *,
        user_subject: str,
        capability_name: str,
        arguments: dict,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> dict:
        engine = CapabilityEngine(registry=self.registry, operation_store=self.operations)
        spec = self.registry.get(capability_name)
        if spec.adapter == "seeyon-central":
            engine.register_handler(
                capability_name,
                lambda context, inputs: self._invoke_seeyon(
                    context=context,
                    user_subject=user_subject,
                    capability_name=capability_name,
                    arguments=inputs,
                ),
            )
        return engine.invoke(
            user_subject=user_subject,
            capability_name=capability_name,
            arguments=arguments,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )

    def session_status(self, *, user_subject: str, system_id: str = "oa") -> dict:
        session = self.sessions.find(user_subject=user_subject, system_id=system_id)
        if session is None:
            return {
                "protocolVersion": "0.1",
                "status": "not_found",
                "systemId": system_id,
                "userSubject": user_subject,
            }
        return session_response(session)

    def start_login(
        self,
        *,
        user_subject: str,
        expected_principal_ref: str | None,
        card_base_url: str,
        ttl_seconds: int = 300,
    ) -> dict:
        session = self.sessions.find(user_subject=user_subject, system_id="oa")
        if session is None:
            if not expected_principal_ref:
                raise ValueError("expected downstream principal is required for a new OA session")
            session = self.sessions.get_or_create(
                user_subject=user_subject,
                system_id="oa",
                expected_principal_ref=expected_principal_ref,
            )
        elif expected_principal_ref:
            session = self.sessions.get_or_create(
                user_subject=user_subject,
                system_id="oa",
                expected_principal_ref=expected_principal_ref,
            )
        expected = str(session.get("expected_principal_ref") or "").strip()
        if not expected:
            raise ValueError("expected downstream principal is not configured")

        contract = self.adapter.authentication_contract()
        challenge = self.challenges.create(
            user_subject=session["user_subject"],
            system_id=session["system_id"],
            system_name=contract["system_name"],
            session_id=session["session_id"],
            expected_principal_ref=expected,
            origin=contract["origin"],
            page_fingerprint=contract["page_fingerprint"],
            nonce=None,
            fields=contract["fields"],
            card_base_url=card_base_url,
            ttl_seconds=ttl_seconds,
        )
        return {
            "protocolVersion": "0.1",
            "status": "requires_user_action",
            "sessionId": session["session_id"],
            "challenge": challenge_response(challenge),
            "nextAction": {
                "type": "open_authentication_card",
                "challengeId": challenge["challenge_id"],
                "cardUrl": challenge["card_url"],
            },
        }

    def get_operation(self, *, user_subject: str, operation_id: str) -> dict:
        operation = self.operations.get(operation_id)
        if operation["user_subject"] != user_subject:
            raise KeyError(f"operation not found: {operation_id}")
        return {
            "protocolVersion": "0.1",
            "operation": operation_response(operation),
        }

    def list_operations(self, *, user_subject: str, limit: int = 100) -> dict:
        operations = self.operations.list(user_subject=user_subject, limit=limit)
        return {
            "protocolVersion": "0.1",
            "count": len(operations),
            "operations": [operation_response(operation) for operation in operations],
        }

    @contextmanager
    def authentication_worker(
        self,
        session: dict,
        adapter: SeeyonCentralAdapter,
    ) -> Iterator[CentralBrowserWorker]:
        with self._session_lock(session["session_id"]):
            with self.worker_factory(session, adapter) as worker:
                yield worker

    def _invoke_seeyon(
        self,
        *,
        context: CapabilityContext,
        user_subject: str,
        capability_name: str,
        arguments: dict,
    ) -> dict:
        session = self.sessions.find(user_subject=user_subject, system_id="oa")
        if session is None or session["state"] != "active":
            raise login_required_action(user_subject, session)

        with self._session_lock(session["session_id"]):
            session = self.sessions.get(session["session_id"])
            if session["state"] != "active":
                raise login_required_action(user_subject, session)
            state = self.session_states.load(session["session_id"])
            if state is None:
                expired_session = self.sessions.mark_expired(
                    session["session_id"],
                    "Encrypted session state is missing.",
                )
                raise login_required_action(user_subject, expired_session)
            business_trip_submission = None
            effective_arguments = arguments
            if capability_name == BUSINESS_TRIP_PREPARE_CAPABILITY:
                business_trip_submission, effective_arguments = (
                    self._resolve_business_trip_field_input(
                        context=context,
                        session=session,
                        arguments=arguments,
                    )
                )
            try:
                with self.worker_factory(session, self.adapter) as worker:
                    worker.restore_session_state(state)
                    if capability_name == BUSINESS_TRIP_PREPARE_CAPABILITY:
                        result = self._prepare_business_trip(
                            context=context,
                            session=session,
                            worker=worker,
                            arguments=effective_arguments,
                            field_submission=business_trip_submission,
                        )
                    elif capability_name == BUSINESS_TRIP_SAVE_CAPABILITY:
                        result = self._save_business_trip(
                            context=context,
                            session=session,
                            worker=worker,
                            arguments=arguments,
                        )
                    else:
                        result = self.adapter.invoke_capability(
                            capability_name,
                            worker,
                            arguments,
                        )
                    self.session_states.save(
                        session["session_id"],
                        worker.capture_session_state(),
                    )
                    return result
            except SeeyonLoginRequired as exc:
                expired_session = self.sessions.mark_expired(session["session_id"], str(exc))
                self.session_states.delete(session["session_id"])
                raise login_required_action(user_subject, expired_session) from exc

    def _prepare_business_trip(
        self,
        *,
        context: CapabilityContext,
        session: dict,
        worker: CentralBrowserWorker,
        arguments: dict,
        field_submission: dict,
    ) -> dict:
        prepared = prepare_business_trip_draft(self.adapter, worker, arguments)
        plan = {
            **prepared["plan"],
            "user_subject": session["user_subject"],
            "session_binding": {
                "session_id": session["session_id"],
                "expected_principal_ref": session.get("expected_principal_ref"),
                "downstream_principal_ref": session.get("downstream_principal_ref"),
                "last_verified_at": session.get("last_verified_at"),
            },
        }
        summary = {
            **prepared["summary"],
            "principal": session.get("downstream_principal_ref")
            or session.get("expected_principal_ref")
            or session["user_subject"],
        }
        commit_spec = self.registry.get(BUSINESS_TRIP_SAVE_CAPABILITY)
        authorization = self.write_authorizations.create(
            user_subject=session["user_subject"],
            system_id=session["system_id"],
            session_id=session["session_id"],
            capability_name=commit_spec.name,
            capability_version=commit_spec.version,
            prepare_operation_id=context.operation_id,
            plan=plan,
            summary=summary,
            card_base_url=self.trusted_card_base_url,
            ttl_seconds=900,
        )
        try:
            self.field_submissions.consume(
                field_submission["submission_id"],
                user_subject=session["user_subject"],
                system_id=session["system_id"],
                session_id=session["session_id"],
                capability_name=context.spec.name,
                capability_version=context.spec.version,
                consume_operation_id=context.operation_id,
            )
        except (
            FieldSubmissionAccessDenied,
            FieldSubmissionIntegrityError,
            FieldSubmissionStateError,
        ) as exc:
            raise ValueError(str(exc)) from exc
        raise RequiresUserAction(
            "WRITE_AUTHORIZATION_REQUIRED",
            "The business-trip draft plan requires confirmation in the trusted action card.",
            next_action={
                "type": "open_write_authorization_card",
                "authorizationId": authorization["authorization_id"],
                "cardUrl": authorization["card_url"],
                "planHash": authorization["plan_hash"],
                "expiresAt": authorization["expires_at"],
                "display": {
                    "title": summary.get("title"),
                    "effect": summary.get("effect"),
                    "fieldCount": len(summary.get("fields") or []),
                },
                "then": {
                    "capability": BUSINESS_TRIP_SAVE_CAPABILITY,
                    "arguments": {"authorization_id": authorization["authorization_id"]},
                },
            },
        )

    def _resolve_business_trip_field_input(
        self,
        *,
        context: CapabilityContext,
        session: dict,
        arguments: dict,
    ) -> tuple[dict, dict]:
        submission_id = str(arguments.get("input_submission_id") or "").strip()
        if not submission_id:
            submission = self.field_submissions.create(
                user_subject=session["user_subject"],
                system_id=session["system_id"],
                session_id=session["session_id"],
                capability_name=context.spec.name,
                capability_version=context.spec.version,
                create_operation_id=context.operation_id,
                form_schema=BUSINESS_TRIP_FIELD_CARD_SCHEMA,
                card_base_url=self.trusted_card_base_url,
                ttl_seconds=900,
            )
            raise self._field_input_required(submission)
        try:
            submission = self.field_submissions.get(submission_id, include_values=True)
        except (FieldSubmissionNotFound, FieldSubmissionIntegrityError) as exc:
            raise self._field_input_unavailable("not_found") from exc
        bindings_match = all(
            (
                submission["user_subject"] == session["user_subject"],
                submission["system_id"] == session["system_id"],
                submission["session_id"] == session["session_id"],
                submission["capability_name"] == context.spec.name,
                submission["capability_version"] == context.spec.version,
            )
        )
        if not bindings_match:
            raise self._field_input_unavailable("binding_mismatch")
        if submission["state"] == "pending":
            raise self._field_input_required(submission)
        if submission["state"] != "submitted" or not isinstance(submission.get("values"), dict):
            raise self._field_input_unavailable(submission["state"])
        return submission, submission["values"]

    @staticmethod
    def _field_input_required(submission: dict) -> RequiresUserAction:
        return RequiresUserAction(
            "FIELD_INPUT_REQUIRED",
            "Business-trip fields must be entered in the trusted field card.",
            next_action={
                "type": "open_field_input_card",
                "inputSubmissionId": submission["submission_id"],
                "cardUrl": submission["card_url"],
                "expiresAt": submission["expires_at"],
                "then": {
                    "capability": BUSINESS_TRIP_PREPARE_CAPABILITY,
                    "arguments": {"input_submission_id": submission["submission_id"]},
                },
            },
        )

    @staticmethod
    def _field_input_unavailable(state: str) -> RequiresUserAction:
        return RequiresUserAction(
            "FIELD_INPUT_UNAVAILABLE",
            f"The trusted field submission is unavailable: {state}.",
            next_action={
                "type": "prepare_again",
                "capability": BUSINESS_TRIP_PREPARE_CAPABILITY,
                "arguments": {},
            },
        )

    def _save_business_trip(
        self,
        *,
        context: CapabilityContext,
        session: dict,
        worker: CentralBrowserWorker,
        arguments: dict,
    ) -> dict:
        authorization_id = str(arguments.get("authorization_id") or "").strip()
        if not authorization_id:
            raise ValueError("authorization_id is required")
        try:
            authorization = self.write_authorizations.get(
                authorization_id,
                include_plan=True,
            )
        except WriteAuthorizationNotFound as exc:
            raise KeyError("write authorization not found") from exc
        if authorization["user_subject"] != session["user_subject"]:
            raise KeyError("write authorization not found")
        if authorization["state"] == "pending":
            raise RequiresUserAction(
                "WRITE_AUTHORIZATION_REQUIRED",
                "The trusted action card has not been approved.",
                next_action={
                    "type": "open_write_authorization_card",
                    "authorizationId": authorization_id,
                    "cardUrl": authorization["card_url"],
                    "planHash": authorization["plan_hash"],
                    "expiresAt": authorization["expires_at"],
                },
            )
        if authorization["state"] != "approved":
            raise RequiresUserAction(
                "WRITE_AUTHORIZATION_UNAVAILABLE",
                f"The write authorization is {authorization['state']}.",
                next_action={
                    "type": "prepare_again",
                    "capability": BUSINESS_TRIP_PREPARE_CAPABILITY,
                },
            )
        plan = authorization["plan"]
        if not self._business_trip_session_binding_matches(plan, session):
            raise ValueError("the OA session changed after the business-trip plan was authorized")

        def enter_commit_boundary() -> None:
            self.write_authorizations.consume(
                authorization_id,
                user_subject=session["user_subject"],
                system_id=session["system_id"],
                session_id=session["session_id"],
                capability_name=context.spec.name,
                capability_version=context.spec.version,
                commit_operation_id=context.operation_id,
            )

        try:
            return save_business_trip_draft(
                self.adapter,
                worker,
                plan,
                enter_commit_boundary=enter_commit_boundary,
            )
        except BusinessTripOutcomeUnknown as exc:
            raise OutcomeUnknown("RESULT_UNKNOWN", str(exc)) from exc
        except BusinessTripContractMismatch as exc:
            raise ValueError(str(exc)) from exc
        except (WriteAuthorizationAccessDenied, WriteAuthorizationStateError) as exc:
            raise ValueError(str(exc)) from exc

    @staticmethod
    def _business_trip_session_binding_matches(plan: dict, session: dict) -> bool:
        binding = plan.get("session_binding") if isinstance(plan.get("session_binding"), dict) else {}
        return all(
            (
                plan.get("user_subject") == session["user_subject"],
                binding.get("session_id") == session["session_id"],
                binding.get("expected_principal_ref") == session.get("expected_principal_ref"),
                binding.get("downstream_principal_ref") == session.get("downstream_principal_ref"),
                binding.get("last_verified_at") == session.get("last_verified_at"),
            )
        )

    @contextmanager
    def _session_lock(self, session_id: str) -> Iterator[None]:
        with self._locks_guard:
            lock = self._session_locks.setdefault(session_id, threading.Lock())
        with lock:
            yield

    @staticmethod
    def _default_worker_factory(session: dict, adapter: SeeyonCentralAdapter):
        return CentralBrowserWorker(
            profile_path=session["profile_path"],
            allowed_origins={adapter.origin},
            headless=True,
        )


def login_required_action(user_subject: str, session: dict | None) -> RequiresUserAction:
    return RequiresUserAction(
        "LOGIN_REQUIRED",
        "The central OA session is not active.",
        next_action={
            "type": "session_login",
            "system": "oa",
            "userSubject": user_subject,
            "sessionState": session["state"] if session else "not_found",
        },
    )


def session_response(session: dict) -> dict:
    return {
        "protocolVersion": "0.1",
        "status": session["state"],
        "sessionId": session["session_id"],
        "systemId": session["system_id"],
        "userSubject": session["user_subject"],
        "expectedPrincipalRef": session.get("expected_principal_ref"),
        "downstreamPrincipalRef": session.get("downstream_principal_ref"),
        "lastVerifiedAt": session.get("last_verified_at"),
        "lastError": session.get("last_error"),
    }


def operation_response(operation: dict) -> dict:
    return {
        "operationId": operation["operation_id"],
        "requestId": operation["request_id"],
        "userSubject": operation["user_subject"],
        "capability": operation["capability_name"],
        "capabilityVersion": operation["capability_version"],
        "status": operation["status"],
        "result": operation.get("result"),
        "error": operation.get("error"),
        "nextAction": operation.get("next_action"),
        "createdAt": operation["created_at"],
        "updatedAt": operation["updated_at"],
        "finishedAt": operation.get("finished_at"),
    }


def challenge_response(challenge: dict) -> dict:
    return {
        "challengeId": challenge["challenge_id"],
        "type": challenge["challenge_type"],
        "state": challenge["state"],
        "systemId": challenge["system_id"],
        "systemName": challenge["system_name"],
        "userSubject": challenge["user_subject"],
        "sessionId": challenge["session_id"],
        "expectedPrincipalRef": challenge.get("expected_principal_ref"),
        "origin": challenge["origin"],
        "cardUrl": challenge["card_url"],
        "expiresAt": challenge["expires_at"],
        "error": challenge.get("error"),
        "result": challenge.get("result"),
    }
