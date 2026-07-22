from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
from typing import Callable, Iterator

from bscli.adapters.seeyon_central import (
    SeeyonCentralAdapter,
    SeeyonLoginRequired,
    SeeyonSessionCheckUnavailable,
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
from bscli.adapters.seeyon_business_trip_submit import (
    BUSINESS_TRIP_SUBMIT_CAPABILITY,
    BUSINESS_TRIP_SUBMIT_FIELD_CARD_SCHEMA,
    BUSINESS_TRIP_SUBMIT_PREPARE_CAPABILITY,
    prepare_business_trip_submission,
    submit_business_trip_request,
)
from bscli.adapters.seeyon_leave import (
    LEAVE_FIELD_CARD_SCHEMA,
    LEAVE_PREPARE_CAPABILITY,
    LEAVE_SAVE_CAPABILITY,
    LeaveContractMismatch,
    LeaveOutcomeUnknown,
    prepare_leave_draft,
    save_leave_draft,
)
from bscli.adapters.seeyon_leave_submit import (
    LEAVE_SUBMIT_CAPABILITY,
    LEAVE_SUBMIT_FIELD_CARD_SCHEMA,
    LEAVE_SUBMIT_PREPARE_CAPABILITY,
    prepare_leave_submission,
    submit_leave_request,
)
from bscli.adapters.seeyon_meeting import (
    MEETING_CREATE_CAPABILITY,
    MEETING_FIELD_CARD_SCHEMA,
    MEETING_PREPARE_CAPABILITY,
    MeetingContractMismatch,
    MeetingOutcomeUnknown,
    build_meeting_field_card_schema,
    create_meeting,
    prepare_meeting_create,
)
from bscli.adapters.seeyon_missed_punch import (
    MISSED_PUNCH_APPROVAL_FIELD_CARD_SCHEMA,
    MISSED_PUNCH_APPROVAL_PREPARE_CAPABILITY,
    MISSED_PUNCH_APPROVE_CAPABILITY,
    MISSED_PUNCH_FIELD_CARD_SCHEMA,
    MISSED_PUNCH_PREPARE_CAPABILITY,
    MISSED_PUNCH_SAVE_CAPABILITY,
    MissedPunchContractMismatch,
    MissedPunchOutcomeUnknown,
    approve_missed_punch_request,
    prepare_missed_punch_approval,
    prepare_missed_punch_draft,
    save_missed_punch_draft,
)
from bscli.adapters.seeyon_pending_actions import (
    EFFICIENCY_DATA_APPROVAL_FIELD_CARD_SCHEMA,
    EFFICIENCY_DATA_APPROVAL_PREPARE_CAPABILITY,
    EFFICIENCY_DATA_APPROVE_CAPABILITY,
    STANDARD_COLLABORATION_APPROVAL_FIELD_CARD_SCHEMA,
    STANDARD_COLLABORATION_APPROVAL_PREPARE_CAPABILITY,
    STANDARD_COLLABORATION_APPROVE_CAPABILITY,
    TRAVEL_EXPENSE_APPROVAL_FIELD_CARD_SCHEMA,
    TRAVEL_EXPENSE_APPROVAL_PREPARE_CAPABILITY,
    TRAVEL_EXPENSE_APPROVE_CAPABILITY,
    WEEKLY_REPORT_ACKNOWLEDGEMENT_FIELD_CARD_SCHEMA,
    WEEKLY_REPORT_ACKNOWLEDGEMENT_PREPARE_CAPABILITY,
    WEEKLY_REPORT_ACKNOWLEDGE_CAPABILITY,
    PendingActionContractMismatch,
    PendingActionOutcomeUnknown,
    acknowledge_weekly_report,
    approve_efficiency_data,
    approve_standard_collaboration,
    approve_travel_expense,
    prepare_efficiency_data_approval,
    prepare_standard_collaboration_approval,
    prepare_travel_expense_approval,
    prepare_weekly_report_acknowledgement,
)
from bscli.adapters.seeyon_submit_phases import SeeyonBusinessValidationRequired
from bscli.adapters.seeyon_workflow_revoke import (
    WORKFLOW_REVOKE_CAPABILITY,
    WORKFLOW_REVOKE_FIELD_CARD_SCHEMA,
    WORKFLOW_REVOKE_PREPARE_CAPABILITY,
    WorkflowRevokeContractMismatch,
    WorkflowRevokeOutcomeUnknown,
    prepare_workflow_revoke,
    revoke_workflow,
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
from bscli.core.interactions import (
    InteractionIntegrityError,
    InteractionStore,
    build_interaction_envelope,
)
from bscli.core.session_secrets import (
    SessionSecretError,
    SessionStateAccessDenied,
    SessionStateStore,
)
from bscli.core.sessions import SessionRegistry
from bscli.core.write_authorizations import (
    WriteAuthorizationAccessDenied,
    WriteAuthorizationNotFound,
    WriteAuthorizationStateError,
    WriteAuthorizationStore,
)


WorkerFactory = Callable[[dict, SeeyonCentralAdapter], CentralBrowserWorker]
TRUSTED_WRITE_INTERACTION_TTL_SECONDS = 1800

_TRUSTED_WRITE_DEFINITIONS = {
    BUSINESS_TRIP_PREPARE_CAPABILITY: {
        "commit_capability": BUSINESS_TRIP_SAVE_CAPABILITY,
        "field_schema": BUSINESS_TRIP_FIELD_CARD_SCHEMA,
        "context_fields": (),
        "prepare_function": "prepare_business_trip_draft",
        "commit_function": "save_business_trip_draft",
        "contract_error": BusinessTripContractMismatch,
        "outcome_error": BusinessTripOutcomeUnknown,
        "field_message": "Business-trip fields must be entered in the trusted field card.",
        "authorization_message": "The business-trip draft plan requires confirmation in the trusted action card.",
    },
    BUSINESS_TRIP_SUBMIT_PREPARE_CAPABILITY: {
        "commit_capability": BUSINESS_TRIP_SUBMIT_CAPABILITY,
        "field_schema": BUSINESS_TRIP_SUBMIT_FIELD_CARD_SCHEMA,
        "context_fields": (),
        "prepare_function": "prepare_business_trip_submission",
        "commit_function": "submit_business_trip_request",
        "contract_error": BusinessTripContractMismatch,
        "outcome_error": BusinessTripOutcomeUnknown,
        "field_message": "Business-trip fields must be entered in the trusted field card.",
        "authorization_message": "The business-trip submission plan requires confirmation in the trusted action card.",
    },
    LEAVE_PREPARE_CAPABILITY: {
        "commit_capability": LEAVE_SAVE_CAPABILITY,
        "field_schema": LEAVE_FIELD_CARD_SCHEMA,
        "context_fields": (),
        "prepare_function": "prepare_leave_draft",
        "commit_function": "save_leave_draft",
        "contract_error": LeaveContractMismatch,
        "outcome_error": LeaveOutcomeUnknown,
        "field_message": "Leave-request fields must be entered in the trusted field card.",
        "authorization_message": "The leave draft plan requires confirmation in the trusted action card.",
    },
    LEAVE_SUBMIT_PREPARE_CAPABILITY: {
        "commit_capability": LEAVE_SUBMIT_CAPABILITY,
        "field_schema": LEAVE_SUBMIT_FIELD_CARD_SCHEMA,
        "context_fields": (),
        "prepare_function": "prepare_leave_submission",
        "commit_function": "submit_leave_request",
        "contract_error": LeaveContractMismatch,
        "outcome_error": LeaveOutcomeUnknown,
        "field_message": "Leave-request fields must be entered in the trusted field card.",
        "authorization_message": "The leave submission plan requires confirmation in the trusted action card.",
    },
    MISSED_PUNCH_PREPARE_CAPABILITY: {
        "commit_capability": MISSED_PUNCH_SAVE_CAPABILITY,
        "field_schema": MISSED_PUNCH_FIELD_CARD_SCHEMA,
        "context_fields": (),
        "prepare_function": "prepare_missed_punch_draft",
        "commit_function": "save_missed_punch_draft",
        "contract_error": MissedPunchContractMismatch,
        "outcome_error": MissedPunchOutcomeUnknown,
        "field_message": "Missed-punch fields must be entered in the trusted field card.",
        "authorization_message": "The missed-punch draft plan requires confirmation in the trusted action card.",
    },
    MISSED_PUNCH_APPROVAL_PREPARE_CAPABILITY: {
        "commit_capability": MISSED_PUNCH_APPROVE_CAPABILITY,
        "field_schema": MISSED_PUNCH_APPROVAL_FIELD_CARD_SCHEMA,
        "context_fields": ("affair_id",),
        "prepare_function": "prepare_missed_punch_approval",
        "commit_function": "approve_missed_punch_request",
        "contract_error": MissedPunchContractMismatch,
        "outcome_error": MissedPunchOutcomeUnknown,
        "field_message": "The missed-punch approval opinion must be entered in the trusted field card.",
        "authorization_message": "The missed-punch approval plan requires confirmation in the trusted action card.",
    },
    MEETING_PREPARE_CAPABILITY: {
        "commit_capability": MEETING_CREATE_CAPABILITY,
        "field_schema": MEETING_FIELD_CARD_SCHEMA,
        "field_schema_function": "build_meeting_field_card_schema",
        "context_fields": (),
        "prepare_function": "prepare_meeting_create",
        "commit_function": "create_meeting",
        "contract_error": MeetingContractMismatch,
        "outcome_error": MeetingOutcomeUnknown,
        "field_message": "Meeting fields must be entered in the trusted field card.",
        "authorization_message": "The meeting-create plan requires confirmation in the trusted action card.",
    },
    WORKFLOW_REVOKE_PREPARE_CAPABILITY: {
        "commit_capability": WORKFLOW_REVOKE_CAPABILITY,
        "field_schema": WORKFLOW_REVOKE_FIELD_CARD_SCHEMA,
        "context_fields": ("affair_id",),
        "prepare_function": "prepare_workflow_revoke",
        "commit_function": "revoke_workflow",
        "contract_error": WorkflowRevokeContractMismatch,
        "outcome_error": WorkflowRevokeOutcomeUnknown,
        "field_message": "The workflow revoke comment must be entered in the trusted field card.",
        "authorization_message": "The workflow revoke plan requires confirmation in the trusted action card.",
    },
}

_TRUSTED_WRITE_DEFINITIONS.update(
    {
        EFFICIENCY_DATA_APPROVAL_PREPARE_CAPABILITY: {
            "commit_capability": EFFICIENCY_DATA_APPROVE_CAPABILITY,
            "field_schema": EFFICIENCY_DATA_APPROVAL_FIELD_CARD_SCHEMA,
            "context_fields": ("affair_id",),
            "prepare_function": "prepare_efficiency_data_approval",
            "commit_function": "approve_efficiency_data",
            "contract_error": PendingActionContractMismatch,
            "outcome_error": PendingActionOutcomeUnknown,
            "field_message": "The efficiency-data opinion must be entered in the trusted field card.",
            "authorization_message": "The efficiency-data approval requires trusted confirmation.",
        },
        TRAVEL_EXPENSE_APPROVAL_PREPARE_CAPABILITY: {
            "commit_capability": TRAVEL_EXPENSE_APPROVE_CAPABILITY,
            "field_schema": TRAVEL_EXPENSE_APPROVAL_FIELD_CARD_SCHEMA,
            "context_fields": ("affair_id",),
            "prepare_function": "prepare_travel_expense_approval",
            "commit_function": "approve_travel_expense",
            "contract_error": PendingActionContractMismatch,
            "outcome_error": PendingActionOutcomeUnknown,
            "field_message": "The travel-expense opinion must be entered in the trusted field card.",
            "authorization_message": "The travel-expense approval requires trusted confirmation.",
        },
        WEEKLY_REPORT_ACKNOWLEDGEMENT_PREPARE_CAPABILITY: {
            "commit_capability": WEEKLY_REPORT_ACKNOWLEDGE_CAPABILITY,
            "field_schema": WEEKLY_REPORT_ACKNOWLEDGEMENT_FIELD_CARD_SCHEMA,
            "context_fields": ("affair_id",),
            "prepare_function": "prepare_weekly_report_acknowledgement",
            "commit_function": "acknowledge_weekly_report",
            "contract_error": PendingActionContractMismatch,
            "outcome_error": PendingActionOutcomeUnknown,
            "field_message": "The weekly-report opinion must be entered in the trusted field card.",
            "authorization_message": "The weekly-report acknowledgement requires trusted confirmation.",
        },
        STANDARD_COLLABORATION_APPROVAL_PREPARE_CAPABILITY: {
            "commit_capability": STANDARD_COLLABORATION_APPROVE_CAPABILITY,
            "field_schema": STANDARD_COLLABORATION_APPROVAL_FIELD_CARD_SCHEMA,
            "context_fields": ("affair_id",),
            "prepare_function": "prepare_standard_collaboration_approval",
            "commit_function": "approve_standard_collaboration",
            "contract_error": PendingActionContractMismatch,
            "outcome_error": PendingActionOutcomeUnknown,
            "field_message": "The collaboration opinion must be entered in the trusted field card.",
            "authorization_message": "The collaboration approval requires trusted confirmation.",
        },
    }
)

_TRUSTED_WRITE_COMMITS = {

    definition["commit_capability"]: (prepare_capability, definition)
    for prepare_capability, definition in _TRUSTED_WRITE_DEFINITIONS.items()
}

_CAPABILITY_SCOPES = {
    BUSINESS_TRIP_PREPARE_CAPABILITY: frozenset({"oa:write:draft"}),
    BUSINESS_TRIP_SAVE_CAPABILITY: frozenset({"oa:write:draft"}),
    BUSINESS_TRIP_SUBMIT_PREPARE_CAPABILITY: frozenset({"oa:write:submit"}),
    BUSINESS_TRIP_SUBMIT_CAPABILITY: frozenset({"oa:write:submit"}),
    LEAVE_PREPARE_CAPABILITY: frozenset({"oa:write:draft"}),
    LEAVE_SAVE_CAPABILITY: frozenset({"oa:write:draft"}),
    LEAVE_SUBMIT_PREPARE_CAPABILITY: frozenset({"oa:write:submit"}),
    LEAVE_SUBMIT_CAPABILITY: frozenset({"oa:write:submit"}),
    MISSED_PUNCH_PREPARE_CAPABILITY: frozenset({"oa:write:draft"}),
    MISSED_PUNCH_SAVE_CAPABILITY: frozenset({"oa:write:draft"}),
    MISSED_PUNCH_APPROVAL_PREPARE_CAPABILITY: frozenset({"oa:write:approval"}),
    MISSED_PUNCH_APPROVE_CAPABILITY: frozenset({"oa:write:approval"}),
    MEETING_PREPARE_CAPABILITY: frozenset({"oa:write:meeting"}),
    MEETING_CREATE_CAPABILITY: frozenset({"oa:write:meeting"}),
    EFFICIENCY_DATA_APPROVAL_PREPARE_CAPABILITY: frozenset({"oa:write:approval"}),
    EFFICIENCY_DATA_APPROVE_CAPABILITY: frozenset({"oa:write:approval"}),
    TRAVEL_EXPENSE_APPROVAL_PREPARE_CAPABILITY: frozenset({"oa:write:approval"}),
    TRAVEL_EXPENSE_APPROVE_CAPABILITY: frozenset({"oa:write:approval"}),
    WEEKLY_REPORT_ACKNOWLEDGEMENT_PREPARE_CAPABILITY: frozenset({"oa:write:approval"}),
    WEEKLY_REPORT_ACKNOWLEDGE_CAPABILITY: frozenset({"oa:write:approval"}),
    STANDARD_COLLABORATION_APPROVAL_PREPARE_CAPABILITY: frozenset({"oa:write:approval"}),
    STANDARD_COLLABORATION_APPROVE_CAPABILITY: frozenset({"oa:write:approval"}),
    WORKFLOW_REVOKE_PREPARE_CAPABILITY: frozenset({"oa:write:revoke"}),
    WORKFLOW_REVOKE_CAPABILITY: frozenset({"oa:write:revoke"}),
}


def _prefill_trusted_field_schema(schema: dict, arguments: dict) -> dict:
    selected = deepcopy(schema)
    for field in selected.get("fields") or []:
        if not isinstance(field, dict) or "value" in field:
            continue
        name = str(field.get("name") or "")
        if name and name in arguments and arguments[name] is not None:
            field["value"] = arguments[name]
    return selected


def capability_required_scopes(capability_name: str) -> frozenset[str]:
    try:
        return _CAPABILITY_SCOPES[capability_name]
    except KeyError as exc:
        raise KeyError(f"write capability has no MCP scope policy: {capability_name}") from exc


class CentralCapabilityService:
    def __init__(
        self,
        *,
        home: Path | str,
        base_url: str,
        registry: CapabilityRegistry | None = None,
        worker_factory: WorkerFactory | None = None,
        session_state_store: SessionStateStore | None = None,
        trusted_card_base_url: str = "http://127.0.0.1:8780",
    ) -> None:
        self.home = Path(home)
        self.db_path = self.home / "agentbridge.db"
        self.registry = registry or build_central_capability_registry()
        self.operations = OperationStore(self.db_path)
        self.sessions = SessionRegistry(self.db_path, self.home / "profiles")
        self.session_states = session_state_store or SessionStateStore(
            self.home / "session-secrets"
        )
        self.challenges = AuthChallengeStore(self.db_path)
        self.field_submissions = FieldSubmissionStore(self.db_path)
        self.write_authorizations = WriteAuthorizationStore(self.db_path)
        self.interactions = InteractionStore(self.db_path)
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
            self._record_user_activity(user_subject=user_subject, system_id="oa")
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
                "statusSource": "registry",
                "checkedAt": None,
            }
        if system_id != "oa" or session["state"] != "active":
            return {
                **session_response(session),
                "statusSource": "registry",
                "checkedAt": None,
            }

        live_check = self._reuse_active_session(
            user_subject=user_subject,
            session=session,
            record_verification=False,
            record_activity=True,
        )
        checked_at = _utc_now()
        if live_check is None:
            return {
                **session_response(self.sessions.get(session["session_id"])),
                "statusSource": "live",
                "checkedAt": checked_at,
            }
        if live_check.get("status") == "succeeded":
            return {
                **live_check["session"],
                "statusSource": "live",
                "checkedAt": checked_at,
            }
        return {
            **live_check,
            "session": session_response(self.sessions.get(session["session_id"])),
            "statusSource": "live",
            "checkedAt": checked_at,
        }

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

        if session["state"] == "active":
            reuse_response = self._reuse_active_session(
                user_subject=user_subject,
                session=session,
            )
            if reuse_response is not None:
                return reuse_response
            session = self.sessions.get(session["session_id"])

        return self._create_login_challenge(
            session=session,
            expected_principal_ref=expected,
            card_base_url=card_base_url,
            ttl_seconds=ttl_seconds,
        )

    def _reuse_active_session(
        self,
        *,
        user_subject: str,
        session: dict,
        record_verification: bool = True,
        record_activity: bool = True,
    ) -> dict | None:
        with self._session_lock(session["session_id"]):
            session = self.sessions.get(session["session_id"])
            if session["state"] != "active":
                return None
            try:
                state = self.session_states.load(session["session_id"])
            except SessionStateAccessDenied:
                return _session_runtime_mismatch_response(user_subject, session)
            except SessionSecretError:
                return _session_state_unavailable_response(user_subject, session)
            if state is None:
                self.sessions.mark_expired(
                    session["session_id"],
                    "Encrypted session state is missing.",
                )
                return None

            try:
                with self.worker_factory(session, self.adapter) as worker:
                    worker.restore_session_state(state)
                    probe = self.adapter.probe_session(worker)
                    self.session_states.save(
                        session["session_id"],
                        worker.capture_session_state(),
                    )
            except SeeyonLoginRequired as exc:
                self.sessions.mark_expired(session["session_id"], str(exc))
                self.session_states.delete(session["session_id"])
                return None
            except SeeyonSessionCheckUnavailable as exc:
                if record_activity:
                    session = self.sessions.touch_activity(session["session_id"])
                return _session_check_unavailable_response(
                    user_subject,
                    session,
                    diagnostics=str(exc),
                )
            except SessionStateAccessDenied:
                return _session_runtime_mismatch_response(user_subject, session)
            except SessionSecretError:
                return _session_state_unavailable_response(user_subject, session)
            except Exception:
                if record_activity:
                    session = self.sessions.touch_activity(session["session_id"])
                return _session_check_unavailable_response(user_subject, session)

            if record_verification:
                session = self.sessions.activate(
                    session["session_id"],
                    observed_principal_ref=session.get("downstream_principal_ref"),
                )
            elif record_activity:
                session = self.sessions.touch_activity(session["session_id"])
            else:
                session = self.sessions.get(session["session_id"])
            return {
                "protocolVersion": "0.1",
                "status": "succeeded",
                "sessionId": session["session_id"],
                "session": session_response(session),
                "result": {
                    "authenticated": True,
                    "templateCount": probe["template_count"],
                    "transport": probe["transport"],
                    "browserBridgeUsed": probe["browser_bridge_used"],
                },
                "nextAction": None,
                "reused": True,
            }

    def run_session_keepalive_cycle(
        self,
        *,
        activity_lease_seconds: float,
        now: datetime | None = None,
    ) -> dict:
        if activity_lease_seconds <= 0:
            raise ValueError("activity lease must be positive")
        checked_at = _as_utc(now or datetime.now(timezone.utc))
        active_sessions = self.sessions.list_active(system_id="oa")
        summary = {
            "checkedAt": checked_at.isoformat(),
            "activeSessions": len(active_sessions),
            "eligibleSessions": 0,
            "keptAlive": 0,
            "expired": 0,
            "deferred": 0,
            "outsideLease": 0,
            "inactive": 0,
        }
        lease = timedelta(seconds=activity_lease_seconds)
        for session in active_sessions:
            last_activity = _parse_utc(session.get("updated_at"))
            if last_activity is None or checked_at - last_activity > lease:
                summary["outsideLease"] += 1
                continue
            summary["eligibleSessions"] += 1
            response = self._reuse_active_session(
                user_subject=session["user_subject"],
                session=session,
                record_verification=False,
                record_activity=False,
            )
            if response is None:
                current = self.sessions.get(session["session_id"])
                if current["state"] == "expired":
                    summary["expired"] += 1
                else:
                    summary["inactive"] += 1
            elif response.get("status") == "succeeded":
                summary["keptAlive"] += 1
            else:
                summary["deferred"] += 1
        return summary

    def _create_login_challenge(
        self,
        *,
        session: dict,
        expected_principal_ref: str,
        card_base_url: str,
        ttl_seconds: int,
    ) -> dict:
        contract = self.adapter.authentication_contract()
        challenge, reused = self.challenges.create_or_reuse(
            user_subject=session["user_subject"],
            system_id=session["system_id"],
            system_name=contract["system_name"],
            session_id=session["session_id"],
            expected_principal_ref=expected_principal_ref,
            origin=contract["origin"],
            page_fingerprint=contract["page_fingerprint"],
            nonce=None,
            fields=contract["fields"],
            card_base_url=card_base_url,
            ttl_seconds=ttl_seconds,
        )
        interaction = self._credential_interaction(challenge)
        return {
            "protocolVersion": "0.1",
            "status": "requires_user_action",
            "sessionId": session["session_id"],
            "challenge": challenge_response(challenge),
            "nextAction": {
                "type": "open_authentication_card",
                "interactionId": interaction["interactionId"],
                "challengeId": challenge["challenge_id"],
                "cardUrl": challenge["card_url"],
                "interaction": interaction,
            },
            "interaction": interaction,
            "reused": reused,
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

    def get_interaction(self, *, user_subject: str, interaction_id: str) -> dict:
        _record, _resource, interaction = self._load_interaction(
            user_subject=user_subject,
            interaction_id=interaction_id,
        )
        return {
            "protocolVersion": "0.1",
            "interaction": interaction,
        }

    def interaction_required_scopes(
        self,
        *,
        user_subject: str,
        interaction_id: str,
    ) -> frozenset[str]:
        record = self.interactions.get(
            interaction_id,
            user_subject=user_subject,
        )
        resume_spec = record.get("resume_spec")
        if not isinstance(resume_spec, dict) or resume_spec.get("kind") != "capability":
            return frozenset({"oa:read"})
        capability_name = str(resume_spec.get("capability") or "")
        try:
            spec = self.registry.get(capability_name)
        except KeyError as exc:
            raise InteractionIntegrityError(
                "interaction resume capability is not registered"
            ) from exc
        if spec.effect == "read":
            return frozenset({"oa:read"})
        try:
            return capability_required_scopes(capability_name)
        except KeyError as exc:
            raise InteractionIntegrityError(str(exc)) from exc

    def resume_interaction(
        self,
        *,
        user_subject: str,
        interaction_id: str,
        idempotency_key: str | None = None,
    ) -> dict:
        record, resource, interaction = self._load_interaction(
            user_subject=user_subject,
            interaction_id=interaction_id,
        )
        if interaction["state"] in {
            "declined",
            "expired",
            "failed",
            "superseded",
        }:
            return _interaction_not_ready_response(interaction)
        if interaction["resume"]["completed"]:
            operation_id = resource.get("consume_operation_id") or resource.get(
                "commit_operation_id"
            )
            operation = self.operations.get(operation_id) if operation_id else None
            return {
                "protocolVersion": "0.1",
                "status": "already_resumed",
                "interaction": interaction,
                "operation": operation_response(operation) if operation else None,
            }

        if not interaction["resume"]["ready"]:
            return _interaction_not_ready_response(interaction)

        resume_spec = record["resume_spec"]
        if resume_spec["kind"] == "session_ready":
            session = self.sessions.get(record["session_id"])
            if session["state"] != "active":
                return _interaction_not_ready_response(
                    interaction,
                    code="SESSION_NOT_ACTIVE",
                    message="The authenticated session is no longer active.",
                )
            return {
                "protocolVersion": "0.1",
                "status": "succeeded",
                "interaction": interaction,
                "result": {"session": session_response(session)},
                "nextAction": {"type": "retry_original_request"},
            }

        if resume_spec["kind"] != "capability":
            raise InteractionIntegrityError("unsupported interaction resume kind")
        session = self.sessions.get(record["session_id"])
        resume_epoch = session.get("last_verified_at") or session["updated_at"]
        response = self.invoke(
            user_subject=user_subject,
            capability_name=resume_spec["capability"],
            arguments=resume_spec["arguments"],
            idempotency_key=idempotency_key
            or f"interaction-resume:{record['interaction_id']}:{resume_epoch}",
        )
        return {
            **response,
            "resumedFromInteractionId": record["interaction_id"],
        }

    def _load_interaction(
        self,
        *,
        user_subject: str,
        interaction_id: str,
    ) -> tuple[dict, dict, dict]:
        record = self.interactions.get(
            interaction_id,
            user_subject=user_subject,
        )
        interaction_type = record["interaction_type"]
        if interaction_type == "credential":
            resource = self.challenges.get(record["resource_id"])
        elif interaction_type == "business_input":
            resource = self.field_submissions.get(record["resource_id"])
        elif interaction_type == "execution_authorization":
            resource = self.write_authorizations.get(record["resource_id"])
        else:
            raise InteractionIntegrityError("unsupported interaction type")
        if any(
            (
                resource["user_subject"] != record["user_subject"],
                resource["system_id"] != record["system_id"],
                resource["session_id"] != record["session_id"],
            )
        ):
            raise InteractionIntegrityError(
                "interaction binding does not match its trusted resource"
            )
        return record, resource, build_interaction_envelope(record, resource)

    def _credential_interaction(self, challenge: dict) -> dict:
        record = self.interactions.register(
            interaction_type="credential",
            user_subject=challenge["user_subject"],
            system_id=challenge["system_id"],
            session_id=challenge["session_id"],
            operation_id=None,
            resource_id=challenge["challenge_id"],
            title=f"登录{challenge['system_name']}",
            message="请在 AgentBridge 安全页面完成登录，凭据不会经过智能体。",
            display={
                "systemName": challenge["system_name"],
                "expectedPrincipalRef": challenge.get("expected_principal_ref"),
            },
            resume_spec={
                "kind": "session_ready",
                "systemId": challenge["system_id"],
            },
            created_at=challenge["created_at"],
            expires_at=challenge["expires_at"],
        )
        return build_interaction_envelope(record, challenge)

    def _business_input_interaction(self, submission: dict) -> dict:
        schema = submission["form_schema"]
        record = self.interactions.register(
            interaction_type="business_input",
            user_subject=submission["user_subject"],
            system_id=submission["system_id"],
            session_id=submission["session_id"],
            operation_id=submission["create_operation_id"],
            resource_id=submission["submission_id"],
            title=str(schema.get("title") or "填写业务信息"),
            message=str(
                schema.get("notice")
                or "请在 AgentBridge 安全页面填写业务信息。"
            ),
            display={
                "systemName": schema.get("system"),
                "effect": schema.get("effect"),
                "fieldCount": len(schema.get("fields") or []),
            },
            resume_spec={
                "kind": "capability",
                "capability": submission["capability_name"],
                "arguments": {
                    **dict(schema.get("_agentbridge_resume_arguments") or {}),
                    "input_submission_id": submission["submission_id"],
                },
            },
            created_at=submission["created_at"],
            expires_at=submission["expires_at"],
        )
        return build_interaction_envelope(record, submission)

    def _execution_authorization_interaction(self, authorization: dict) -> dict:
        summary = authorization["summary"]
        record = self.interactions.register(
            interaction_type="execution_authorization",
            user_subject=authorization["user_subject"],
            system_id=authorization["system_id"],
            session_id=authorization["session_id"],
            operation_id=authorization["prepare_operation_id"],
            resource_id=authorization["authorization_id"],
            title=str(summary.get("title") or "确认执行计划"),
            message="请核对冻结计划并决定是否允许 AgentBridge 执行。",
            display={
                "systemName": summary.get("system"),
                "effect": summary.get("effect"),
                "fieldCount": len(summary.get("fields") or []),
            },
            resume_spec={
                "kind": "capability",
                "capability": authorization["capability_name"],
                "arguments": {
                    "authorization_id": authorization["authorization_id"],
                },
            },
            created_at=authorization["created_at"],
            expires_at=authorization["expires_at"],
        )
        return build_interaction_envelope(record, authorization)

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
            try:
                state = self.session_states.load(session["session_id"])
            except SessionStateAccessDenied as exc:
                raise _session_runtime_mismatch_action(user_subject, session) from exc
            except SessionSecretError as exc:
                raise _session_state_unavailable_action(user_subject, session) from exc
            if state is None:
                expired_session = self.sessions.mark_expired(
                    session["session_id"],
                    "Encrypted session state is missing.",
                )
                raise login_required_action(user_subject, expired_session)
            prepare_definition = _TRUSTED_WRITE_DEFINITIONS.get(capability_name)
            commit_definition = _TRUSTED_WRITE_COMMITS.get(capability_name)
            field_submission = None
            effective_arguments = arguments
            try:
                dynamic_field_schema = None
                if (
                    prepare_definition is not None
                    and not str(arguments.get("input_submission_id") or "").strip()
                    and prepare_definition.get("field_schema_function")
                ):
                    schema_function = globals().get(
                        str(prepare_definition["field_schema_function"])
                    )
                    if not callable(schema_function):
                        raise RuntimeError("trusted field schema function is unavailable")
                    with self.worker_factory(session, self.adapter) as worker:
                        worker.restore_session_state(state)
                        dynamic_field_schema = schema_function(
                            self.adapter,
                            worker,
                            arguments,
                        )
                        self.session_states.save(
                            session["session_id"],
                            worker.capture_session_state(),
                        )
                if prepare_definition is not None:
                    field_submission, effective_arguments = self._resolve_trusted_field_input(
                        context=context,
                        session=session,
                        arguments=arguments,
                        definition=prepare_definition,
                        form_schema=dynamic_field_schema,
                    )
                with self.worker_factory(session, self.adapter) as worker:
                    worker.restore_session_state(state)
                    if prepare_definition is not None:
                        result = self._prepare_trusted_write(
                            context=context,
                            session=session,
                            worker=worker,
                            arguments=effective_arguments,
                            field_submission=field_submission,
                            definition=prepare_definition,
                        )
                    elif commit_definition is not None:
                        prepare_capability, definition = commit_definition
                        result = self._commit_trusted_write(
                            context=context,
                            session=session,
                            worker=worker,
                            arguments=arguments,
                            prepare_capability=prepare_capability,
                            definition=definition,
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
            except SeeyonSessionCheckUnavailable as exc:
                raise _session_check_unavailable_action(
                    user_subject,
                    session,
                    diagnostics=str(exc),
                ) from exc

    def _prepare_trusted_write(
        self,
        *,
        context: CapabilityContext,
        session: dict,
        worker: CentralBrowserWorker,
        arguments: dict,
        field_submission: dict,
        definition: dict,
    ) -> dict:
        prepare_function = globals().get(str(definition["prepare_function"]))
        if not callable(prepare_function):
            raise RuntimeError("trusted write prepare function is unavailable")
        prepared = prepare_function(self.adapter, worker, arguments)
        resume_arguments = dict(
            field_submission.get("form_schema", {}).get("_agentbridge_resume_arguments")
            or {}
        )
        plan = {
            **prepared["plan"],
            "user_subject": session["user_subject"],
            "prepare_capability": context.spec.name,
            "resume_arguments": resume_arguments,
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
        commit_spec = self.registry.get(str(definition["commit_capability"]))
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
            ttl_seconds=TRUSTED_WRITE_INTERACTION_TTL_SECONDS,
        )
        interaction = self._execution_authorization_interaction(authorization)
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
            str(definition["authorization_message"]),
            next_action={
                "type": "open_write_authorization_card",
                "interactionId": interaction["interactionId"],
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
                    "capability": commit_spec.name,
                    "arguments": {"authorization_id": authorization["authorization_id"]},
                },
                "interaction": interaction,
            },
        )

    def _resolve_trusted_field_input(
        self,
        *,
        context: CapabilityContext,
        session: dict,
        arguments: dict,
        definition: dict,
        form_schema: dict | None = None,
    ) -> tuple[dict, dict]:
        submission_id = str(arguments.get("input_submission_id") or "").strip()
        context_arguments = {
            name: arguments[name]
            for name in definition.get("context_fields") or ()
            if name in arguments
        }
        if len(context_arguments) != len(definition.get("context_fields") or ()):
            raise ValueError("trusted field input is missing its workflow target context")
        if not submission_id:
            selected_schema = (
                form_schema if form_schema is not None else definition["field_schema"]
            )
            submission_schema = {
                **_prefill_trusted_field_schema(selected_schema, arguments),
                "_agentbridge_resume_arguments": context_arguments,
            }
            submission = self.field_submissions.create(
                user_subject=session["user_subject"],
                system_id=session["system_id"],
                session_id=session["session_id"],
                capability_name=context.spec.name,
                capability_version=context.spec.version,
                create_operation_id=context.operation_id,
                form_schema=submission_schema,
                card_base_url=self.trusted_card_base_url,
                ttl_seconds=TRUSTED_WRITE_INTERACTION_TTL_SECONDS,
            )
            raise self._field_input_required(submission, definition)
        try:
            submission = self.field_submissions.get(submission_id, include_values=True)
        except (FieldSubmissionNotFound, FieldSubmissionIntegrityError) as exc:
            raise self._field_input_unavailable(
                "not_found",
                context.spec.name,
                context_arguments,
            ) from exc
        bindings_match = all(
            (
                submission["user_subject"] == session["user_subject"],
                submission["system_id"] == session["system_id"],
                submission["session_id"] == session["session_id"],
                submission["capability_name"] == context.spec.name,
                submission["capability_version"] == context.spec.version,
                submission.get("form_schema", {}).get("_agentbridge_resume_arguments")
                == context_arguments,
            )
        )
        if not bindings_match:
            raise self._field_input_unavailable(
                "binding_mismatch",
                context.spec.name,
                context_arguments,
            )
        if submission["state"] == "pending":
            raise self._field_input_required(submission, definition)
        if submission["state"] != "submitted" or not isinstance(submission.get("values"), dict):
            raise self._field_input_unavailable(
                submission["state"],
                context.spec.name,
                context_arguments,
            )
        return submission, {**context_arguments, **submission["values"]}

    def _field_input_required(self, submission: dict, definition: dict) -> RequiresUserAction:
        interaction = self._business_input_interaction(submission)
        resume_arguments = {
            **dict(
                submission.get("form_schema", {}).get("_agentbridge_resume_arguments")
                or {}
            ),
            "input_submission_id": submission["submission_id"],
        }
        return RequiresUserAction(
            "FIELD_INPUT_REQUIRED",
            str(definition["field_message"]),
            next_action={
                "type": "open_field_input_card",
                "interactionId": interaction["interactionId"],
                "inputSubmissionId": submission["submission_id"],
                "cardUrl": submission["card_url"],
                "expiresAt": submission["expires_at"],
                "then": {
                    "capability": submission["capability_name"],
                    "arguments": resume_arguments,
                },
                "interaction": interaction,
            },
        )

    @staticmethod
    def _field_input_unavailable(
        state: str,
        prepare_capability: str,
        resume_arguments: dict,
    ) -> RequiresUserAction:
        return RequiresUserAction(
            "FIELD_INPUT_UNAVAILABLE",
            f"The trusted field submission is unavailable: {state}.",
            next_action={
                "type": "prepare_again",
                "capability": prepare_capability,
                "arguments": dict(resume_arguments),
            },
        )

    def _commit_trusted_write(
        self,
        *,
        context: CapabilityContext,
        session: dict,
        worker: CentralBrowserWorker,
        arguments: dict,
        prepare_capability: str,
        definition: dict,
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
            interaction = self._execution_authorization_interaction(authorization)
            raise RequiresUserAction(
                "WRITE_AUTHORIZATION_REQUIRED",
                "The trusted action card has not been approved.",
                next_action={
                    "type": "open_write_authorization_card",
                    "interactionId": interaction["interactionId"],
                    "authorizationId": authorization_id,
                    "cardUrl": authorization["card_url"],
                    "planHash": authorization["plan_hash"],
                    "expiresAt": authorization["expires_at"],
                    "interaction": interaction,
                },
            )
        plan = authorization["plan"]
        if authorization["state"] != "approved":
            raise RequiresUserAction(
                "WRITE_AUTHORIZATION_UNAVAILABLE",
                f"The write authorization is {authorization['state']}.",
                next_action={
                    "type": "prepare_again",
                    "capability": prepare_capability,
                    "arguments": dict(plan.get("resume_arguments") or {}),
                },
            )
        if not self._trusted_write_session_binding_matches(plan, session):
            raise ValueError("the OA session changed after the write plan was authorized")

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

        commit_function = globals().get(str(definition["commit_function"]))
        if not callable(commit_function):
            raise RuntimeError("trusted write commit function is unavailable")
        try:
            return commit_function(
                self.adapter,
                worker,
                plan,
                enter_commit_boundary=enter_commit_boundary,
            )
        except SeeyonBusinessValidationRequired as exc:
            validation = exc.validation
            continued_plan = deepcopy(plan)
            existing_validations = continued_plan.get("business_validation_overrides")
            if not isinstance(existing_validations, list):
                legacy_validation = continued_plan.get("business_validation_override")
                existing_validations = (
                    [dict(legacy_validation)]
                    if isinstance(legacy_validation, dict)
                    else []
                )
            existing_validations = [
                dict(item) for item in existing_validations if isinstance(item, dict)
            ]
            if validation["fingerprint"] in {
                item.get("fingerprint") for item in existing_validations
            }:
                raise ValueError("the OA confirmation was already authorized") from exc
            if len(existing_validations) >= 5:
                raise ValueError("too many chained OA confirmations") from exc
            continued_plan.pop("business_validation_override", None)
            continued_plan["business_validation_overrides"] = [
                *existing_validations,
                dict(validation),
            ]
            continued_summary = deepcopy(authorization["summary"])
            original_title = str(
                continued_summary.get("title") or "OA 写操作"
            ).strip()
            continued_summary.update(
                {
                    "title": f"确认 OA 提示并继续{original_title}",
                    "effect": "仅在再次出现完全相同的 OA 提示时继续执行已授权操作",
                    "authorization_notice": (
                        "OA 返回了一条可继续的提交提示。授权后，AgentBridge "
                        "仅在再次出现完全相同的提示时点击“继续”并完成正式提交。"
                    ),
                    "authorize_label": "确认警告并继续提交",
                }
            )
            continued_summary["fields"] = [
                *list(continued_summary.get("fields") or []),
                {"label": "OA 提交提示", "value": validation["message"]},
            ]
            continued_authorization = self.write_authorizations.create(
                user_subject=session["user_subject"],
                system_id=session["system_id"],
                session_id=session["session_id"],
                capability_name=context.spec.name,
                capability_version=context.spec.version,
                prepare_operation_id=context.operation_id,
                plan=continued_plan,
                summary=continued_summary,
                card_base_url=self.trusted_card_base_url,
                ttl_seconds=TRUSTED_WRITE_INTERACTION_TTL_SECONDS,
            )
            interaction = self._execution_authorization_interaction(
                continued_authorization
            )
            raise RequiresUserAction(
                "OA_BUSINESS_VALIDATION_CONFIRMATION_REQUIRED",
                "OA returned a continuable business-validation warning.",
                next_action={
                    "type": "open_write_authorization_card",
                    "interactionId": interaction["interactionId"],
                    "authorizationId": continued_authorization["authorization_id"],
                    "cardUrl": continued_authorization["card_url"],
                    "planHash": continued_authorization["plan_hash"],
                    "expiresAt": continued_authorization["expires_at"],
                    "display": {
                        "title": continued_summary["title"],
                        "effect": continued_summary["effect"],
                        "fieldCount": len(continued_summary["fields"]),
                        "validationCode": validation["code"],
                    },
                    "then": {
                        "capability": context.spec.name,
                        "arguments": {
                            "authorization_id": continued_authorization[
                                "authorization_id"
                            ]
                        },
                    },
                    "interaction": interaction,
                },
            ) from exc
        except definition["outcome_error"] as exc:
            raise OutcomeUnknown("RESULT_UNKNOWN", str(exc)) from exc
        except definition["contract_error"] as exc:
            raise ValueError(str(exc)) from exc
        except (WriteAuthorizationAccessDenied, WriteAuthorizationStateError) as exc:
            raise ValueError(str(exc)) from exc

    @staticmethod
    def _trusted_write_session_binding_matches(plan: dict, session: dict) -> bool:
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

    def _record_user_activity(self, *, user_subject: str, system_id: str) -> None:
        session = self.sessions.find(user_subject=user_subject, system_id=system_id)
        if session is None or session["state"] != "active":
            return
        with self._session_lock(session["session_id"]):
            current = self.sessions.get(session["session_id"])
            if current["state"] == "active":
                self.sessions.touch_activity(current["session_id"])

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


def _interaction_not_ready_response(
    interaction: dict,
    *,
    code: str | None = None,
    message: str | None = None,
) -> dict:
    state = interaction["state"]
    pending = state in {"pending", "processing"}
    effective_code = code or {
        "pending": "INTERACTION_PENDING",
        "processing": "INTERACTION_PROCESSING",
        "declined": "INTERACTION_DECLINED",
        "expired": "INTERACTION_EXPIRED",
        "superseded": "INTERACTION_SUPERSEDED",
        "failed": "INTERACTION_FAILED",
    }.get(state, "INTERACTION_UNAVAILABLE")
    effective_message = message or (
        "The trusted user interaction has not completed yet."
        if pending
        else f"The trusted user interaction is unavailable: {state}."
    )
    return {
        "protocolVersion": "0.1",
        "status": "requires_user_action" if pending else "failed",
        "error": {
            "code": effective_code,
            "message": effective_message,
        },
        "interaction": interaction,
        "nextAction": {
            "type": "wait_for_interaction" if pending else "start_again",
            "interactionId": interaction["interactionId"],
        },
    }


def _session_runtime_mismatch_action(
    user_subject: str,
    session: dict,
) -> RequiresUserAction:
    return RequiresUserAction(
        "SESSION_RUNTIME_MISMATCH",
        (
            "The encrypted OA session is bound to a different Windows security "
            "context. Retry through the central runtime that created the session; "
            "do not request a new login card."
        ),
        next_action=_session_runtime_next_action(
            user_subject,
            session,
            action_type="retry_via_bound_central_runtime",
        ),
    )


def _session_state_unavailable_action(
    user_subject: str,
    session: dict,
) -> RequiresUserAction:
    return RequiresUserAction(
        "SESSION_STATE_UNAVAILABLE",
        (
            "The encrypted OA session state cannot be read safely. Retry through "
            "the bound central runtime or ask an administrator to repair the "
            "session store before reauthentication."
        ),
        next_action=_session_runtime_next_action(
            user_subject,
            session,
            action_type="repair_session_runtime",
        ),
    )


def _session_runtime_mismatch_response(user_subject: str, session: dict) -> dict:
    return _session_action_response(
        _session_runtime_mismatch_action(user_subject, session),
        session,
    )


def _session_state_unavailable_response(user_subject: str, session: dict) -> dict:
    return _session_action_response(
        _session_state_unavailable_action(user_subject, session),
        session,
    )


def _session_check_unavailable_action(
    user_subject: str,
    session: dict,
    *,
    diagnostics: str | None = None,
) -> RequiresUserAction:
    detail = f" Diagnostic: {diagnostics}" if diagnostics else ""
    return RequiresUserAction(
        "SESSION_CHECK_UNAVAILABLE",
        (
            "OA session validity could not be checked. Retry later through the "
            f"same central runtime; credentials are not required yet.{detail}"
        ),
        next_action=_session_runtime_next_action(
            user_subject,
            session,
            action_type="retry_session_check",
        ),
    )


def _session_check_unavailable_response(
    user_subject: str,
    session: dict,
    *,
    diagnostics: str | None = None,
) -> dict:
    action = _session_check_unavailable_action(
        user_subject,
        session,
        diagnostics=diagnostics,
    )
    return _session_action_response(action, session)


def _session_action_response(action: RequiresUserAction, session: dict) -> dict:
    return {
        "protocolVersion": "0.1",
        "status": "requires_user_action",
        "sessionId": session["session_id"],
        "error": {
            "code": action.code,
            "message": action.message,
        },
        "nextAction": action.next_action,
        "reused": False,
    }


def _session_runtime_next_action(
    user_subject: str,
    session: dict,
    *,
    action_type: str,
) -> dict:
    return {
        "type": action_type,
        "system": "oa",
        "userSubject": user_subject,
        "sessionId": session["session_id"],
        "sessionState": session["state"],
        "sessionPreserved": True,
    }


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
        "lastActivityAt": session.get("updated_at"),
        "lastError": session.get("last_error"),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def operation_response(operation: dict) -> dict:
    next_action = operation.get("next_action")
    interaction = (
        next_action.get("interaction")
        if isinstance(next_action, dict)
        and isinstance(next_action.get("interaction"), dict)
        else None
    )
    return {
        "operationId": operation["operation_id"],
        "requestId": operation["request_id"],
        "userSubject": operation["user_subject"],
        "capability": operation["capability_name"],
        "capabilityVersion": operation["capability_version"],
        "status": operation["status"],
        "result": operation.get("result"),
        "error": operation.get("error"),
        "nextAction": next_action,
        "interaction": interaction,
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
