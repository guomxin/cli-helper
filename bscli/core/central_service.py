from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
from typing import Callable, Iterator

from bscli.adapters.seeyon_central import (
    SeeyonCentralAdapter,
    build_central_capability_registry,
)
from bscli.adapters.seeyon_documents import DOCUMENT_CERTIFICATE_SEARCH_CAPABILITY
from bscli.adapters.base import (
    AdapterBusinessRuleRejected,
    AdapterLoginRequired,
    AdapterSessionCheckUnavailable,
)
from bscli.adapters.taihua import (
    TAIHUA_ADAPTER_ID,
    TAIHUA_SYSTEM_ID,
    TAIHUA_WORK_LOG_CREATE_CAPABILITY,
    TAIHUA_WORK_LOG_CREATE_PREPARE_CAPABILITY,
    TAIHUA_WORK_LOG_FIELD_CARD_SCHEMA,
    TaihuaCentralAdapter,
    TaihuaWorkLogContractMismatch,
    TaihuaWorkLogOutcomeUnknown,
    build_taihua_capability_registry,
    commit_taihua_work_log_create,
    prepare_taihua_work_log_create,
)
from bscli.adapters.yuque import (
    YUQUE_ADAPTER_ID,
    YUQUE_SYSTEM_ID,
    YuqueCentralAdapter,
    build_yuque_capability_registry,
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
    LABOR_CONTRACT_RENEWAL_APPROVAL_FIELD_CARD_SCHEMA,
    LABOR_CONTRACT_RENEWAL_APPROVAL_PREPARE_CAPABILITY,
    LABOR_CONTRACT_RENEWAL_APPROVE_CAPABILITY,
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
    approve_labor_contract_renewal,
    approve_standard_collaboration,
    approve_travel_expense,
    prepare_efficiency_data_approval,
    prepare_labor_contract_renewal_approval,
    prepare_standard_collaboration_approval,
    prepare_travel_expense_approval,
    prepare_weekly_report_acknowledgement,
)
from bscli.adapters.seeyon_submit_phases import (
    SeeyonBusinessValidationRequired,
)
from bscli.adapters.seeyon_workflow_revoke import (
    WORKFLOW_REVOKE_CAPABILITY,
    WORKFLOW_REVOKE_FIELD_CARD_SCHEMA,
    WORKFLOW_REVOKE_PREPARE_CAPABILITY,
    WorkflowRevokeContractMismatch,
    WorkflowRevokeOutcomeUnknown,
    prepare_workflow_revoke,
    revoke_workflow,
)
from bscli.admin.stores import (
    GovernancePolicyDenied,
    GovernancePolicyStore,
)
from bscli.browser.central import AttachedCentralBrowserWorker, CentralBrowserWorker
from bscli.browser.http import CentralHttpWorker
from bscli.core.auth_challenges import AuthChallengeStore
from bscli.core.capability import CapabilityRegistry
from bscli.core.capability_runtime import (
    CapabilityRejected,
    CapabilityContext,
    CapabilityEngine,
    OutcomeUnknown,
    RequiresUserAction,
)
from bscli.core.operations import OperationStore
from bscli.core.document_downloads import (
    DocumentDownloadAccessDenied,
    DocumentDownloadIntegrityError,
    DocumentDownloadNotFound,
    DocumentDownloadStateError,
    DocumentDownloadStore,
)
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
from bscli.core.tasks import TaskHubStore, TaskIntegrityError, TaskNotFound
from bscli.core.write_authorizations import (
    WriteAuthorizationAccessDenied,
    WriteAuthorizationNotFound,
    WriteAuthorizationStateError,
    WriteAuthorizationStore,
)
from bscli.workspace.stores import WorkspaceStore


WorkerFactory = Callable[[dict, object], object]
TRUSTED_WRITE_INTERACTION_TTL_SECONDS = 1800
ACTIVITY_GATED_CLIENT_TYPES = {"openclaw-weixin", "wechat", "weixin"}

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
        LABOR_CONTRACT_RENEWAL_APPROVAL_PREPARE_CAPABILITY: {
            "commit_capability": LABOR_CONTRACT_RENEWAL_APPROVE_CAPABILITY,
            "field_schema": LABOR_CONTRACT_RENEWAL_APPROVAL_FIELD_CARD_SCHEMA,
            "context_fields": ("affair_id",),
            "prepare_function": "prepare_labor_contract_renewal_approval",
            "commit_function": "approve_labor_contract_renewal",
            "contract_error": PendingActionContractMismatch,
            "outcome_error": PendingActionOutcomeUnknown,
            "field_message": "The labor-contract renewal opinion must be entered in the trusted field card.",
            "authorization_message": "The labor-contract renewal approval requires trusted confirmation.",
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

_TRUSTED_WRITE_DEFINITIONS.update(
    {
        TAIHUA_WORK_LOG_CREATE_PREPARE_CAPABILITY: {
            "commit_capability": TAIHUA_WORK_LOG_CREATE_CAPABILITY,
            "field_schema": TAIHUA_WORK_LOG_FIELD_CARD_SCHEMA,
            "context_fields": (),
            "prepare_function": "prepare_taihua_work_log_create",
            "commit_function": "commit_taihua_work_log_create",
            "contract_error": TaihuaWorkLogContractMismatch,
            "outcome_error": TaihuaWorkLogOutcomeUnknown,
            "field_message": "工作日志字段必须在可信字段卡中核对。",
            "authorization_message": "泰华工作日志提交计划需要在可信授权卡中确认。",
        }
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
    LABOR_CONTRACT_RENEWAL_APPROVAL_PREPARE_CAPABILITY: frozenset({"oa:write:approval"}),
    LABOR_CONTRACT_RENEWAL_APPROVE_CAPABILITY: frozenset({"oa:write:approval"}),
    WEEKLY_REPORT_ACKNOWLEDGEMENT_PREPARE_CAPABILITY: frozenset({"oa:write:approval"}),
    WEEKLY_REPORT_ACKNOWLEDGE_CAPABILITY: frozenset({"oa:write:approval"}),
    STANDARD_COLLABORATION_APPROVAL_PREPARE_CAPABILITY: frozenset({"oa:write:approval"}),
    STANDARD_COLLABORATION_APPROVE_CAPABILITY: frozenset({"oa:write:approval"}),
    WORKFLOW_REVOKE_PREPARE_CAPABILITY: frozenset({"oa:write:revoke"}),
    WORKFLOW_REVOKE_CAPABILITY: frozenset({"oa:write:revoke"}),
    TAIHUA_WORK_LOG_CREATE_PREPARE_CAPABILITY: frozenset({"taihua:write:worklog"}),
    TAIHUA_WORK_LOG_CREATE_CAPABILITY: frozenset({"taihua:write:worklog"}),
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
        taihua_base_url: str | None = None,
        yuque_base_url: str | None = None,
        yuque_organization_id: int | None = None,
        registry: CapabilityRegistry | None = None,
        worker_factory: WorkerFactory | None = None,
        session_state_store: SessionStateStore | None = None,
        trusted_card_base_url: str = "http://127.0.0.1:8780",
        session_keepalive_lease_seconds: float | None = None,
    ) -> None:
        self.home = Path(home)
        self.db_path = self.home / "agentbridge.db"
        if registry is None:
            self.registry = build_central_capability_registry()
            for spec in build_taihua_capability_registry().list():
                self.registry.register(spec)
            for spec in build_yuque_capability_registry().list():
                self.registry.register(spec)
        else:
            self.registry = registry
        self.operations = OperationStore(self.db_path)
        self.sessions = SessionRegistry(self.db_path, self.home / "profiles")
        self.session_states = session_state_store or SessionStateStore(
            self.home / "session-secrets"
        )
        self.challenges = AuthChallengeStore(self.db_path)
        self.field_submissions = FieldSubmissionStore(self.db_path)
        self.document_downloads = DocumentDownloadStore(self.db_path)
        self.write_authorizations = WriteAuthorizationStore(self.db_path)
        self.interactions = InteractionStore(self.db_path)
        self.tasks = TaskHubStore(self.db_path)
        self.workspace = WorkspaceStore(self.db_path)
        self.governance_policies = GovernancePolicyStore(self.db_path)
        self.adapter = SeeyonCentralAdapter(base_url=base_url)
        self.worker_factory = worker_factory or self._default_worker_factory
        self._adapters_by_system: dict[str, object] = {"oa": self.adapter}
        self._worker_factories_by_system: dict[str, WorkerFactory] = {
            "oa": self.worker_factory
        }
        self._adapter_systems: dict[str, str] = {"seeyon-central": "oa"}
        if taihua_base_url:
            taihua_adapter = TaihuaCentralAdapter(base_url=taihua_base_url)
            self._adapters_by_system[TAIHUA_SYSTEM_ID] = taihua_adapter
            self._worker_factories_by_system[TAIHUA_SYSTEM_ID] = (
                self._default_http_worker_factory
            )
            self._adapter_systems[TAIHUA_ADAPTER_ID] = TAIHUA_SYSTEM_ID
        if yuque_base_url and yuque_organization_id:
            yuque_adapter = YuqueCentralAdapter(
                base_url=yuque_base_url,
                organization_id=yuque_organization_id,
            )
            self._adapters_by_system[YUQUE_SYSTEM_ID] = yuque_adapter
            self._worker_factories_by_system[YUQUE_SYSTEM_ID] = (
                self._default_browser_worker_factory
            )
            self._adapter_systems[YUQUE_ADAPTER_ID] = YUQUE_SYSTEM_ID
        self.trusted_card_base_url = trusted_card_base_url
        if (
            session_keepalive_lease_seconds is not None
            and session_keepalive_lease_seconds <= 0
        ):
            raise ValueError("session keepalive lease must be positive")
        self.session_keepalive_lease_seconds = session_keepalive_lease_seconds
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

    def adapter_for_system(self, system_id: str) -> object:
        runtime = self._runtime_for_system(system_id)
        if runtime is None:
            raise KeyError(f"central runtime is not configured for {system_id}")
        return runtime[0]

    def _runtime_for_system(
        self,
        system_id: str,
    ) -> tuple[object, WorkerFactory] | None:
        adapter = self._adapters_by_system.get(system_id)
        worker_factory = self._worker_factories_by_system.get(system_id)
        if adapter is None or worker_factory is None:
            return None
        return adapter, worker_factory

    @staticmethod
    def _raise_system_not_configured(system_id: str) -> dict:
        raise CapabilityRejected(
            "SYSTEM_NOT_CONFIGURED",
            f"The central runtime is not configured for {system_id}.",
        )

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
        system_id = self._adapter_systems.get(spec.adapter, spec.system)
        runtime = self._runtime_for_system(system_id)
        if runtime is None:
            engine.register_handler(
                capability_name,
                lambda _context, _inputs: self._raise_system_not_configured(system_id),
            )
        else:
            adapter, selected_worker_factory = runtime
            if capability_name != DOCUMENT_CERTIFICATE_SEARCH_CAPABILITY:
                self._record_user_activity(
                    user_subject=user_subject,
                    system_id=system_id,
                )
            engine.register_handler(
                capability_name,
                lambda context, inputs: self._invoke_adapter(
                    context=context,
                    user_subject=user_subject,
                    system_id=system_id,
                    adapter=adapter,
                    worker_factory=selected_worker_factory,
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
        if session["state"] != "active":
            return {
                **self._session_response(session),
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
                **self._session_response(self.sessions.get(session["session_id"])),
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
            "session": self._session_response(self.sessions.get(session["session_id"])),
            "statusSource": "live",
            "checkedAt": checked_at,
        }

    def inspect_session(self, *, user_subject: str, system_id: str) -> dict:
        """Run a live session check without extending the user's activity lease."""
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
        if session["state"] != "active":
            return {
                **self._session_response(session),
                "statusSource": "registry",
                "checkedAt": None,
            }
        live_check = self._reuse_active_session(
            user_subject=user_subject,
            session=session,
            record_verification=False,
            record_activity=False,
        )
        checked_at = _utc_now()
        if live_check is None:
            return {
                **self._session_response(self.sessions.get(session["session_id"])),
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
            "session": self._session_response(self.sessions.get(session["session_id"])),
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
        system_id: str = "oa",
    ) -> dict:
        runtime = self._runtime_for_system(system_id)
        if runtime is None:
            raise ValueError(f"central login is not configured for {system_id}")
        session = self.sessions.find(user_subject=user_subject, system_id=system_id)
        if session is None:
            if not expected_principal_ref:
                raise ValueError(
                    "expected downstream principal is required for a new session"
                )
            session = self.sessions.get_or_create(
                user_subject=user_subject,
                system_id=system_id,
                expected_principal_ref=expected_principal_ref,
            )
        elif expected_principal_ref:
            session = self.sessions.get_or_create(
                user_subject=user_subject,
                system_id=system_id,
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
        record_keepalive: bool = False,
    ) -> dict | None:
        runtime = self._runtime_for_system(session["system_id"])
        if runtime is None:
            return _session_check_unavailable_response(
                user_subject,
                session,
                diagnostics=f"system is not configured: {session['system_id']}",
            )
        adapter, selected_worker_factory = runtime
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
                with selected_worker_factory(session, adapter) as worker:
                    worker.restore_session_state(state)
                    probe = adapter.probe_session(worker)
                    self.session_states.save(
                        session["session_id"],
                        worker.capture_session_state(),
                    )
            except AdapterLoginRequired as exc:
                self.sessions.mark_expired(session["session_id"], str(exc))
                self.session_states.delete(session["session_id"])
                return None
            except AdapterSessionCheckUnavailable as exc:
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
            elif record_keepalive:
                session = self.sessions.record_keepalive(session["session_id"])
            else:
                session = self.sessions.get(session["session_id"])
            return {
                "protocolVersion": "0.1",
                "status": "succeeded",
                "sessionId": session["session_id"],
                "session": self._session_response(session),
                "result": {
                    "authenticated": True,
                    "templateCount": probe.get("template_count"),
                    "transport": probe["transport"],
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
        active_sessions = self.sessions.list_active()
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
            if self._runtime_for_system(session["system_id"]) is None:
                summary["inactive"] += 1
                continue
            last_activity = _parse_utc(session.get("last_user_activity_at"))
            if last_activity is None or checked_at - last_activity > lease:
                summary["outsideLease"] += 1
                continue
            summary["eligibleSessions"] += 1
            response = self._reuse_active_session(
                user_subject=session["user_subject"],
                session=session,
                record_verification=False,
                record_activity=False,
                record_keepalive=True,
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
        adapter = self.adapter_for_system(session["system_id"])
        contract = adapter.authentication_contract()
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
            challenge_type=(
                "interactive_browser_login"
                if contract.get("authentication_mode") == "interactive_browser"
                else "legacy_form_login"
            ),
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
        task_id = self.tasks.task_id_for_operation(
            operation_id,
            user_subject=user_subject,
        )
        return {
            "protocolVersion": "0.1",
            "operation": {
                **operation_response(operation),
                "taskId": task_id,
            },
        }

    def list_operations(self, *, user_subject: str, limit: int = 100) -> dict:
        operations = self.operations.list(user_subject=user_subject, limit=limit)
        return {
            "protocolVersion": "0.1",
            "count": len(operations),
            "operations": [
                {
                    **operation_response(operation),
                    "taskId": self.tasks.task_id_for_operation(
                        operation["operation_id"],
                        user_subject=user_subject,
                    ),
                }
                for operation in operations
            ],
        }

    def get_interaction(self, *, user_subject: str, interaction_id: str) -> dict:
        record, _resource, interaction = self._load_interaction(
            user_subject=user_subject,
            interaction_id=interaction_id,
        )
        task_id = self.tasks.task_id_for_interaction(
            interaction_id,
            user_subject=user_subject,
        )
        if task_id:
            self.tasks.link_interaction(
                task_id=task_id,
                user_subject=user_subject,
                interaction_record=record,
                interaction=interaction,
            )
        return {
            "protocolVersion": "0.1",
            "interaction": {
                **interaction,
                "taskId": task_id,
            },
        }

    def present_interaction(
        self,
        *,
        user_subject: str,
        agent_host: str,
        endpoint_key: str,
        interaction_id: str,
    ) -> dict:
        endpoint = self.tasks.endpoint_for_key(
            user_subject=user_subject,
            agent_host=agent_host,
            endpoint_key=endpoint_key,
        )
        record, resource, interaction = self._load_interaction(
            user_subject=user_subject,
            interaction_id=interaction_id,
        )
        task_id = self.tasks.task_id_for_interaction(
            interaction_id,
            user_subject=user_subject,
        )
        return {
            "protocolVersion": "0.1",
            "status": "succeeded",
            "interaction": {
                **self._present_interaction_for_endpoint(
                    record=record,
                    resource=resource,
                    interaction=interaction,
                    endpoint=endpoint,
                ),
                "taskId": task_id,
            },
            "endpoint": endpoint_response(endpoint),
        }

    def claim_host_notifications(
        self,
        *,
        user_subject: str,
        agent_host: str,
        endpoint_key: str,
        limit: int = 10,
        lease_seconds: int = 30,
    ) -> dict:
        endpoint = self.tasks.endpoint_for_key(
            user_subject=user_subject,
            agent_host=agent_host,
            endpoint_key=endpoint_key,
        )
        deliveries = self.tasks.claim_outbox(
            user_subject=user_subject,
            endpoint_id=endpoint["endpoint_id"],
            limit=limit,
            lease_seconds=lease_seconds,
        )
        notifications = []
        for delivery in deliveries:
            event = delivery["payload"]
            if delivery["payload_type"] == "timeline_message":
                notifications.append(
                    {
                        "deliveryId": delivery["delivery_id"],
                        "attemptCount": delivery["attempt_count"],
                        "task": None,
                        "event": None,
                        "deliveryMode": "timeline_message",
                        "interaction": None,
                        "message": _timeline_notification_message(event),
                        "timeline": event,
                    }
                )
                continue
            task = self.tasks.get_task(
                delivery["task_id"],
                user_subject=user_subject,
            )
            item = {
                "deliveryId": delivery["delivery_id"],
                "attemptCount": delivery["attempt_count"],
                "task": task_response(task),
                "event": event,
                "deliveryMode": "no_op",
                "interaction": None,
                "message": None,
            }
            if task["origin_endpoint_id"] == endpoint["endpoint_id"]:
                item["deliveryMode"] = "origin_handled"
            elif event.get("eventType") == "task.interaction.waiting":
                interaction_id = (event.get("payload") or {}).get(
                    "interactionId"
                )
                if interaction_id:
                    try:
                        record, resource, interaction = self._load_interaction(
                            user_subject=user_subject,
                            interaction_id=interaction_id,
                        )
                    except (KeyError, InteractionIntegrityError):
                        interaction = None
                    can_open_interaction = (
                        "trusted_interaction"
                        in set(endpoint.get("capabilities") or [])
                    )
                    if (
                        can_open_interaction
                        and interaction is not None
                        and interaction["state"] in {"pending", "processing"}
                    ):
                        item["deliveryMode"] = "trusted_interaction"
                        item["interaction"] = (
                            self._present_interaction_for_endpoint(
                                record=record,
                                resource=resource,
                                interaction=interaction,
                                endpoint=endpoint,
                            )
                        )
                    else:
                        item["deliveryMode"] = "status"
                        item["message"] = _task_notification_message(
                            task,
                            event,
                        )
            elif event.get("eventType") in {
                "task.created",
                "task.operation.linked",
                "task.operation.running",
                "task.interaction.completed",
                "task.interaction.expired",
                "task.interaction.failed",
                "task.interaction.superseded",
                "task.canceled",
                "task.operation.succeeded",
                "task.operation.failed",
                "task.operation.outcome_unknown",
            }:
                item["deliveryMode"] = "status"
                item["message"] = _task_notification_message(task, event)
            notifications.append(item)
        return {
            "protocolVersion": "0.1",
            "status": "succeeded",
            "count": len(notifications),
            "endpoint": endpoint_response(endpoint),
            "notifications": notifications,
        }

    def acknowledge_host_notification(
        self,
        *,
        user_subject: str,
        agent_host: str,
        endpoint_key: str,
        delivery_id: str,
        succeeded: bool,
        retry_after_seconds: int = 5,
        defer_until_activity: bool = False,
    ) -> dict:
        endpoint = self.tasks.endpoint_for_key(
            user_subject=user_subject,
            agent_host=agent_host,
            endpoint_key=endpoint_key,
        )
        if (
            defer_until_activity
            and str(endpoint.get("client_type") or "").lower()
            not in ACTIVITY_GATED_CLIENT_TYPES
        ):
            raise ValueError(
                "endpoint does not support activity-gated notification delivery"
            )
        delivery = self.tasks.acknowledge_outbox(
            user_subject=user_subject,
            endpoint_id=endpoint["endpoint_id"],
            delivery_id=delivery_id,
            succeeded=succeeded,
            retry_after_seconds=retry_after_seconds,
            defer_until_activity=defer_until_activity,
        )
        return {
            "protocolVersion": "0.1",
            "status": "succeeded",
            "delivery": {
                "deliveryId": delivery["delivery_id"],
                "state": delivery["state"],
                "attemptCount": delivery["attempt_count"],
                "nextAttemptAt": delivery["next_attempt_at"],
                "acknowledgedAt": delivery.get("acknowledged_at"),
            },
        }

    def ensure_host_task(
        self,
        *,
        user_subject: str,
        token_id: str,
        agent_host: str,
        host_task_key: str,
        endpoint_key: str,
        client_type: str,
        external_subject: str,
        conversation_ref: str,
        title: str,
        account_id: str | None = None,
        label: str | None = None,
        route: dict | None = None,
        capabilities: list[str] | None = None,
    ) -> dict:
        try:
            endpoint = self.tasks.endpoint_for_key(
                user_subject=user_subject,
                agent_host=agent_host,
                endpoint_key=endpoint_key,
            )
        except TaskNotFound:
            endpoint, endpoint_reused = self.tasks.ensure_endpoint(
                user_subject=user_subject,
                token_id=token_id,
                agent_host=agent_host,
                endpoint_key=endpoint_key,
                client_type=client_type,
                external_subject=external_subject,
                account_id=account_id,
                conversation_ref=conversation_ref,
                label=label,
                route=route,
                capabilities=capabilities,
            )
        else:
            endpoint_reused = True
            if endpoint["client_type"] == "web":
                if (
                    client_type != "web"
                    or endpoint["conversation_ref"] != conversation_ref
                ):
                    raise TaskIntegrityError(
                        "workspace task context does not match its registered endpoint"
                    )
            else:
                endpoint, endpoint_reused = self.tasks.ensure_endpoint(
                    user_subject=user_subject,
                    token_id=token_id,
                    agent_host=agent_host,
                    endpoint_key=endpoint_key,
                    client_type=client_type,
                    external_subject=external_subject,
                    account_id=account_id,
                    conversation_ref=conversation_ref,
                    label=label,
                    route=route,
                    capabilities=capabilities,
                )
        task, task_reused = self.tasks.ensure_task(
            user_subject=user_subject,
            agent_host=agent_host,
            host_task_key=host_task_key,
            origin_endpoint_id=endpoint["endpoint_id"],
            active_conversation_ref=conversation_ref,
            title=title,
        )
        return {
            "protocolVersion": "0.1",
            "status": "succeeded",
            "task": task_response(task),
            "endpoint": endpoint_response(endpoint),
            "reused": {
                "task": task_reused,
                "endpoint": endpoint_reused,
            },
        }

    def append_host_timeline_message(
        self,
        *,
        user_subject: str,
        token_id: str,
        agent_host: str,
        endpoint_key: str,
        client_type: str,
        external_subject: str,
        conversation_ref: str,
        message_key: str,
        role: str,
        text: str,
        account_id: str | None = None,
        label: str | None = None,
        route: dict | None = None,
        task_id: str | None = None,
    ) -> dict:
        endpoint, endpoint_reused = self.tasks.ensure_endpoint(
            user_subject=user_subject,
            token_id=token_id,
            agent_host=agent_host,
            endpoint_key=endpoint_key,
            client_type=client_type,
            external_subject=external_subject,
            account_id=account_id,
            conversation_ref=conversation_ref,
            label=label,
            route=route,
            capabilities=(
                ["workspace.timeline.read"]
                if client_type in {"web", "webchat"}
                else ["direct_status", "timeline_message"]
            ),
        )
        entry, entry_reused = self.tasks.append_timeline_message(
            user_subject=user_subject,
            source_endpoint_id=endpoint["endpoint_id"],
            message_key=message_key,
            role=role,
            text=text,
            task_id=task_id,
        )
        reactivated_deliveries = 0
        if (
            role == "user"
            and str(endpoint.get("client_type") or "").lower()
            in ACTIVITY_GATED_CLIENT_TYPES
        ):
            reactivated_deliveries = self.tasks.reactivate_deferred_outbox(
                user_subject=user_subject,
                endpoint_id=endpoint["endpoint_id"],
                delay_seconds=5,
            )
        return {
            "protocolVersion": "0.1",
            "status": "succeeded",
            "entry": timeline_entry_response(entry),
            "endpoint": endpoint_response(endpoint),
            "reused": {
                "entry": entry_reused,
                "endpoint": endpoint_reused,
            },
            "reactivatedDeliveries": reactivated_deliveries,
        }

    def observe_host_task(
        self,
        *,
        user_subject: str,
        task_id: str,
        operation_ids: list[str] | None = None,
        interaction_ids: list[str] | None = None,
    ) -> dict:
        task = self.tasks.get_task(task_id, user_subject=user_subject)
        linked_operations: list[str] = []
        linked_interactions: list[str] = []
        for operation_id in dict.fromkeys(operation_ids or []):
            operation = self.operations.get(operation_id)
            if operation["user_subject"] != user_subject:
                raise KeyError(f"operation not found: {operation_id}")
            task = self.tasks.link_operation(
                task_id=task_id,
                user_subject=user_subject,
                operation=operation,
            )
            linked_operations.append(operation_id)
        for interaction_id in dict.fromkeys(interaction_ids or []):
            record, _resource, interaction = self._load_interaction(
                user_subject=user_subject,
                interaction_id=interaction_id,
            )
            task = self.tasks.link_interaction(
                task_id=task_id,
                user_subject=user_subject,
                interaction_record=record,
                interaction=interaction,
            )
            linked_interactions.append(interaction_id)
        return {
            "protocolVersion": "0.1",
            "status": "succeeded",
            "task": task_response(task),
            "linked": {
                "operationIds": linked_operations,
                "interactionIds": linked_interactions,
            },
        }

    def recover_host_tasks(
        self,
        *,
        user_subject: str,
        agent_host: str,
        endpoint_key: str,
        limit: int = 100,
    ) -> dict:
        endpoint = self.tasks.endpoint_for_key(
            user_subject=user_subject,
            agent_host=agent_host,
            endpoint_key=endpoint_key,
        )
        recoveries = []
        for candidate in self.tasks.recovery_candidates(
            user_subject=user_subject,
            endpoint_id=endpoint["endpoint_id"],
            limit=limit,
        ):
            try:
                _record, _resource, interaction = self._load_interaction(
                    user_subject=user_subject,
                    interaction_id=candidate["interaction_id"],
                )
            except (KeyError, InteractionIntegrityError):
                continue
            if interaction["state"] not in {"pending", "processing"}:
                continue
            recoveries.append(
                {
                    "task": task_response(candidate["task"]),
                    "endpoint": endpoint_response(candidate["endpoint"]),
                    "interaction": {
                        **interaction,
                        "taskId": candidate["task"]["task_id"],
                    },
                }
            )
        return {
            "protocolVersion": "0.1",
            "status": "succeeded",
            "count": len(recoveries),
            "recoveries": recoveries,
        }

    def list_host_tasks(
        self,
        *,
        user_subject: str,
        agent_host: str,
        endpoint_key: str,
        active_only: bool = False,
        limit: int = 100,
    ) -> dict:
        endpoint = self.tasks.endpoint_for_key(
            user_subject=user_subject,
            agent_host=agent_host,
            endpoint_key=endpoint_key,
        )
        tasks = self.tasks.list_tasks(
            user_subject=user_subject,
            endpoint_id=endpoint["endpoint_id"],
            active_only=active_only,
            limit=limit,
        )
        return {
            "protocolVersion": "0.1",
            "status": "succeeded",
            "count": len(tasks),
            "endpoint": endpoint_response(endpoint),
            "tasks": [task_response(task) for task in tasks],
        }

    def resolve_host_task_continuation(
        self,
        *,
        user_subject: str,
        agent_host: str,
        endpoint_key: str,
        task_id: str | None = None,
        ordinal: int | None = None,
        source_client_type: str | None = None,
        cross_endpoint_only: bool = False,
        prefer_active: bool = True,
        prefer_latest: bool = False,
        reuse_selected: bool = True,
        allow_follow_up: bool = False,
        max_age_minutes: int = 1_440,
        limit: int = 8,
    ) -> dict:
        endpoint = self.tasks.endpoint_for_key(
            user_subject=user_subject,
            agent_host=agent_host,
            endpoint_key=endpoint_key,
        )
        selected_task_id = str(task_id or "").strip() or None
        selection_reason = "explicit_task_id" if selected_task_id else None

        if selected_task_id is None and ordinal is not None:
            pending = self.tasks.get_continuation(
                user_subject=user_subject,
                agent_host=agent_host,
                endpoint_id=endpoint["endpoint_id"],
            )
            candidates = (
                pending.get("candidate_task_ids", [])
                if pending and pending.get("state") == "awaiting_selection"
                else []
            )
            index = int(ordinal) - 1
            if index < 0 or index >= len(candidates):
                return {
                    "protocolVersion": "0.2",
                    "status": "not_found",
                    "reason": "continuation_choice_not_found",
                    "count": 0,
                    "candidates": [],
                }
            selected_task_id = candidates[index]
            selection_reason = "candidate_ordinal"

        if (
            selected_task_id is None
            and reuse_selected
            and source_client_type is None
        ):
            current = self.tasks.get_continuation(
                user_subject=user_subject,
                agent_host=agent_host,
                endpoint_id=endpoint["endpoint_id"],
            )
            if current and current.get("state") == "selected":
                selected_task_id = current.get("selected_task_id")
                selection_reason = "existing_selection"

        if selected_task_id is None:
            candidate_records = self.tasks.continuation_candidates(
                user_subject=user_subject,
                agent_host=agent_host,
                endpoint_id=endpoint["endpoint_id"],
                active_only=prefer_active,
                cross_endpoint_only=cross_endpoint_only,
                source_client_type=source_client_type,
                max_age_minutes=max_age_minutes,
                limit=limit,
            )
            if prefer_active and not candidate_records:
                candidate_records = self.tasks.continuation_candidates(
                    user_subject=user_subject,
                    agent_host=agent_host,
                    endpoint_id=endpoint["endpoint_id"],
                    active_only=False,
                    cross_endpoint_only=cross_endpoint_only,
                    source_client_type=source_client_type,
                    max_age_minutes=max_age_minutes,
                    limit=limit,
                )
            if not candidate_records:
                return {
                    "protocolVersion": "0.2",
                    "status": "not_found",
                    "reason": "continuation_task_not_found",
                    "count": 0,
                    "candidates": [],
                }
            if len(candidate_records) > 1 and not prefer_latest:
                continuation, reused = self.tasks.set_continuation_candidates(
                    user_subject=user_subject,
                    agent_host=agent_host,
                    endpoint_id=endpoint["endpoint_id"],
                    candidate_task_ids=[
                        item["task"]["task_id"] for item in candidate_records
                    ],
                    reason="multiple_candidates",
                )
                return {
                    "protocolVersion": "0.2",
                    "status": "ambiguous",
                    "reason": "multiple_continuation_tasks",
                    "count": len(candidate_records),
                    "continuation": continuation_response(continuation),
                    "candidates": [
                        task_continuation_candidate_response(item, index + 1)
                        for index, item in enumerate(candidate_records)
                    ],
                    "reused": reused,
                }
            selected_task_id = candidate_records[0]["task"]["task_id"]
            selection_reason = (
                "latest_relative_reference"
                if len(candidate_records) > 1
                else "single_candidate"
            )

        task = self._refresh_host_task_for_continuation(
            user_subject=user_subject,
            task_id=selected_task_id,
        )
        execution_mode = task_continuation_execution_mode(
            task,
            allow_follow_up=allow_follow_up,
        )
        continuation, task, selected_endpoint, reused = (
            self.tasks.select_continuation(
                user_subject=user_subject,
                agent_host=agent_host,
                endpoint_id=endpoint["endpoint_id"],
                task_id=selected_task_id,
                execution_mode=execution_mode,
                reason=selection_reason,
            )
        )
        snapshot = self._host_task_continuation_snapshot(
            user_subject=user_subject,
            task=task,
            endpoint=selected_endpoint,
        )
        presented_interaction = snapshot.pop("interaction", None)
        return {
            "protocolVersion": "0.2",
            "status": "selected",
            "count": 1,
            "task": task_response(task),
            "continuation": continuation_response(continuation),
            "snapshot": snapshot,
            "interaction": presented_interaction,
            "reused": reused,
        }

    def _refresh_host_task_for_continuation(
        self,
        *,
        user_subject: str,
        task_id: str,
    ) -> dict:
        task = self.tasks.get_task(task_id, user_subject=user_subject)
        operation_ids = (
            [task["current_operation_id"]]
            if task.get("current_operation_id")
            else []
        )
        interaction_ids = (
            [task["current_interaction_id"]]
            if task.get("current_interaction_id")
            else []
        )
        if operation_ids or interaction_ids:
            refreshed = self.observe_host_task(
                user_subject=user_subject,
                task_id=task_id,
                operation_ids=operation_ids,
                interaction_ids=interaction_ids,
            )
            return self.tasks.get_task(
                refreshed["task"]["taskId"],
                user_subject=user_subject,
            )
        return task

    def _host_task_continuation_snapshot(
        self,
        *,
        user_subject: str,
        task: dict,
        endpoint: dict,
    ) -> dict:
        origin = next(
            (
                item
                for item in self.tasks.list_endpoints(
                    user_subject=user_subject,
                    active_only=False,
                    limit=500,
                )
                if item["endpoint_id"] == task["origin_endpoint_id"]
            ),
            None,
        )
        operation = None
        if task.get("current_operation_id"):
            try:
                raw_operation = self.operations.get(
                    task["current_operation_id"]
                )
            except KeyError:
                raw_operation = None
            if raw_operation and raw_operation.get("user_subject") == user_subject:
                operation = {
                    "operationId": raw_operation["operation_id"],
                    "capability": raw_operation.get("capability_name"),
                    "status": raw_operation.get("status"),
                    "errorCode": raw_operation.get("error_code"),
                    "createdAt": raw_operation.get("created_at"),
                    "updatedAt": raw_operation.get("updated_at"),
                    "finishedAt": raw_operation.get("finished_at"),
                }
        interaction_summary = None
        presented_interaction = None
        if task.get("current_interaction_id"):
            try:
                _record, _resource, interaction = self._load_interaction(
                    user_subject=user_subject,
                    interaction_id=task["current_interaction_id"],
                )
            except (KeyError, InteractionIntegrityError):
                interaction = None
            if interaction:
                interaction_summary = {
                    "interactionId": interaction.get("interactionId"),
                    "type": interaction.get("type"),
                    "state": interaction.get("state"),
                    "systemId": interaction.get("systemId"),
                    "expiresAt": interaction.get("expiresAt"),
                }
                if interaction.get("state") in {"pending", "processing"}:
                    try:
                        presented = self.present_interaction(
                            user_subject=user_subject,
                            agent_host=endpoint["agent_host"],
                            endpoint_key=endpoint["endpoint_key"],
                            interaction_id=task["current_interaction_id"],
                        )
                    except (KeyError, RuntimeError):
                        presented = None
                    if presented:
                        presented_interaction = presented.get("interaction")
        events = self.tasks.list_events(
            task_id=task["task_id"],
            user_subject=user_subject,
            limit=12,
        )
        return {
            "summary": {
                "taskId": task["task_id"],
                "title": task["title"],
                "status": task["status"],
                "phase": task_continuation_phase(task),
                "origin": (
                    {
                        "clientType": origin.get("client_type"),
                        "label": origin.get("label"),
                    }
                    if origin
                    else None
                ),
                "operation": operation,
                "interaction": interaction_summary,
                "updatedAt": task["updated_at"],
                "finishedAt": task.get("finished_at"),
            },
            "recentEvents": [
                {
                    "eventType": event["event_type"],
                    "createdAt": event["created_at"],
                }
                for event in events[-8:]
            ],
            "interaction": presented_interaction,
        }

    def get_host_cross_endpoint_context(
        self,
        *,
        user_subject: str,
        agent_host: str,
        endpoint_key: str,
        max_age_minutes: int = 360,
        limit: int = 12,
    ) -> dict:
        endpoint = self.tasks.endpoint_for_key(
            user_subject=user_subject,
            agent_host=agent_host,
            endpoint_key=endpoint_key,
        )
        max_age_minutes = min(max(int(max_age_minutes), 1), 1_440)
        limit = min(max(int(limit), 1), 20)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        endpoints = {
            item["endpoint_id"]: item
            for item in self.tasks.list_endpoints(
                user_subject=user_subject,
                active_only=False,
                limit=500,
            )
        }
        selected = []
        for entry in reversed(
            self.tasks.list_timeline(
                user_subject=user_subject,
                limit=min(500, max(limit * 10, 100)),
            )
        ):
            if entry.get("entry_type") != "chat_message":
                continue
            if entry.get("source_endpoint_id") == endpoint["endpoint_id"]:
                continue
            created_at = _parse_utc(entry.get("created_at"))
            if created_at is None or created_at < cutoff:
                continue
            role = str(entry.get("role") or "").strip()
            text = str(entry.get("text") or "").strip()
            if role not in {"user", "assistant"} or not text:
                continue
            source = endpoints.get(entry.get("source_endpoint_id"))
            selected.append(
                {
                    **timeline_entry_response(entry),
                    "source": {
                        "clientType": (
                            source.get("client_type") if source else "unknown"
                        ),
                        "label": source.get("label") if source else None,
                    },
                }
            )
            if len(selected) >= limit:
                break
        selected.reverse()
        return {
            "protocolVersion": "0.1",
            "status": "succeeded",
            "endpoint": {
                "clientType": endpoint["client_type"],
                "label": endpoint.get("label"),
            },
            "maxAgeMinutes": max_age_minutes,
            "count": len(selected),
            "entries": selected,
        }

    def confirm_workspace_link(
        self,
        *,
        user_subject: str,
        token_id: str,
        agent_host: str,
        endpoint_key: str,
        client_type: str,
        external_subject: str,
        conversation_ref: str,
        link_code: str,
        account_id: str | None = None,
        label: str | None = None,
        route: dict | None = None,
    ) -> dict:
        endpoint, _reused = self.tasks.ensure_endpoint(
            user_subject=user_subject,
            token_id=token_id,
            agent_host=agent_host,
            endpoint_key=endpoint_key,
            client_type=client_type,
            external_subject=external_subject,
            account_id=account_id,
            conversation_ref=conversation_ref,
            label=label,
            route=route,
            capabilities=["workspace.link.confirm"],
        )
        link = self.workspace.confirm_link(
            link_code=link_code,
            user_subject=user_subject,
            approver_endpoint_id=endpoint["endpoint_id"],
        )
        return {
            "protocolVersion": "0.1",
            "status": "succeeded",
            "link": {
                "challengeId": link["challenge_id"],
                "state": link["state"],
                "expiresAt": link["expires_at"],
            },
        }

    def register_workspace_endpoint(self, *, account_id: str) -> dict:
        account = self.workspace.get_account(account_id)
        endpoint, reused = self.tasks.ensure_endpoint(
            user_subject=account["user_subject"],
            token_id=f"workspace-account:{account_id}",
            agent_host="openclaw",
            endpoint_key=account["endpoint_key"],
            client_type="web",
            external_subject=account_id,
            account_id=account_id,
            conversation_ref=account["openclaw_session_key"],
            label=f"Agent Workspace: {account['username']}",
            route={},
            capabilities=[
                "workspace.chat",
                "workspace.task.read",
                "workspace.interaction.open",
            ],
        )
        account = self.workspace.attach_endpoint(
            account_id=account_id,
            endpoint_id=endpoint["endpoint_id"],
        )
        return {
            "account": account,
            "endpoint": endpoint,
            "reused": reused,
        }

    def redeem_workspace_gateway_grant(
        self,
        *,
        user_subject: str,
        agent_host: str,
        endpoint_key: str,
        session_key: str,
        grant: str,
    ) -> dict:
        endpoint = self.tasks.endpoint_for_key(
            user_subject=user_subject,
            agent_host=agent_host,
            endpoint_key=endpoint_key,
        )
        if endpoint["client_type"] != "web":
            raise PermissionError(
                "workspace gateway binding requires a web endpoint"
            )
        redeemed = self.workspace.redeem_gateway_grant(
            grant=grant,
            user_subject=user_subject,
            endpoint_key=endpoint_key,
            session_key=session_key,
        )
        return {
            "protocolVersion": "0.1",
            "status": "succeeded",
            "binding": {
                "endpointKey": redeemed["endpoint_key"],
                "sessionKey": redeemed["session_key"],
            },
        }

    def resolve_workspace_gateway_session(
        self,
        *,
        user_subject: str,
        agent_host: str,
        session_key: str,
    ) -> dict:
        account = self.workspace.resolve_gateway_session(
            user_subject=user_subject,
            session_key=session_key,
        )
        endpoint = self.tasks.endpoint_for_key(
            user_subject=user_subject,
            agent_host=agent_host,
            endpoint_key=account["endpoint_key"],
        )
        if (
            endpoint["client_type"] != "web"
            or endpoint["endpoint_id"] != account["endpoint_id"]
        ):
            raise PermissionError(
                "workspace gateway session is not linked to an active web endpoint"
            )
        return {
            "protocolVersion": "0.1",
            "status": "succeeded",
            "binding": {
                "endpointKey": account["endpoint_key"],
                "sessionKey": account["openclaw_session_key"],
            },
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
            return frozenset({f"{record['system_id']}:read"})
        capability_name = str(resume_spec.get("capability") or "")
        try:
            spec = self.registry.get(capability_name)
        except KeyError as exc:
            raise InteractionIntegrityError(
                "interaction resume capability is not registered"
            ) from exc
        if spec.effect == "read":
            return frozenset({f"{spec.system}:read"})
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
                "result": {"session": self._session_response(session)},
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
        task_id = self.tasks.task_id_for_interaction(
            record["interaction_id"],
            user_subject=user_subject,
        )
        if task_id:
            operation_id = response.get("operationId")
            next_interaction = response.get("interaction")
            self.observe_host_task(
                user_subject=user_subject,
                task_id=task_id,
                operation_ids=[operation_id] if operation_id else [],
                interaction_ids=(
                    [next_interaction["interactionId"]]
                    if isinstance(next_interaction, dict)
                    and next_interaction.get("interactionId")
                    else []
                ),
            )
        return {
            **response,
            "resumedFromInteractionId": record["interaction_id"],
            "taskId": task_id,
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

    def _present_interaction_for_endpoint(
        self,
        *,
        record: dict,
        resource: dict,
        interaction: dict,
        endpoint: dict,
    ) -> dict:
        if record["interaction_type"] == "business_input":
            presentation = self.field_submissions.create_presentation(
                resource["submission_id"],
                user_subject=record["user_subject"],
                endpoint_id=endpoint["endpoint_id"],
            )
            return {
                **interaction,
                "presentation": {
                    **interaction["presentation"],
                    "url": presentation["card_url"],
                    "endpointId": endpoint["endpoint_id"],
                    "presentationId": presentation["presentation_id"],
                    "individualized": True,
                },
            }
        if record["interaction_type"] != "execution_authorization":
            return {
                **interaction,
                "presentation": {
                    **interaction["presentation"],
                    "endpointId": endpoint["endpoint_id"],
                    "individualized": False,
                },
            }
        presentation = self.write_authorizations.create_presentation(
            resource["authorization_id"],
            user_subject=record["user_subject"],
            endpoint_id=endpoint["endpoint_id"],
        )
        return {
            **interaction,
            "presentation": {
                **interaction["presentation"],
                "url": presentation["card_url"],
                "endpointId": endpoint["endpoint_id"],
                "presentationId": presentation["presentation_id"],
                "individualized": True,
            },
        }

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
        adapter: object,
    ) -> Iterator[object]:
        runtime = self._runtime_for_system(session["system_id"])
        if runtime is None:
            raise KeyError(
                f"central runtime is not configured for {session['system_id']}"
            )
        registered_adapter, worker_factory = runtime
        if registered_adapter is not adapter:
            raise ValueError("authentication adapter does not match the session system")
        with self._session_lock(session["session_id"]):
            with worker_factory(session, adapter) as worker:
                yield worker

    @contextmanager
    def remote_authentication_worker(
        self,
        session: dict,
        adapter: object,
        cdp_endpoint: str,
    ) -> Iterator[object]:
        runtime = self._runtime_for_system(session["system_id"])
        if runtime is None:
            raise KeyError(
                f"central runtime is not configured for {session['system_id']}"
            )
        registered_adapter, _worker_factory = runtime
        if registered_adapter is not adapter:
            raise ValueError("authentication adapter does not match the session system")
        contract = adapter.authentication_contract()
        allowed_origins = set(contract.get("allowed_origins") or ())
        allowed_origins.add(contract["origin"])
        with self._session_lock(session["session_id"]):
            with AttachedCentralBrowserWorker(
                cdp_endpoint=cdp_endpoint,
                allowed_origins=allowed_origins,
            ) as worker:
                yield worker

    def _invoke_adapter(
        self,
        *,
        context: CapabilityContext,
        user_subject: str,
        system_id: str,
        adapter: object,
        worker_factory: WorkerFactory,
        capability_name: str,
        arguments: dict,
    ) -> dict:
        self._assert_write_allowed(context=context, system_id=system_id)
        session = self.sessions.find(user_subject=user_subject, system_id=system_id)
        if session is None or session["state"] != "active":
            raise login_required_action(user_subject, system_id, session)

        document_search = capability_name == DOCUMENT_CERTIFICATE_SEARCH_CAPABILITY
        with self._session_lock(
            session["session_id"],
            wait_seconds=1.0 if document_search else None,
        ):
            session = self.sessions.get(session["session_id"])
            if session["state"] != "active":
                raise login_required_action(user_subject, system_id, session)
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
                raise login_required_action(user_subject, system_id, expired_session)
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
                    with worker_factory(session, adapter) as worker:
                        worker.restore_session_state(state)
                        dynamic_field_schema = schema_function(
                            adapter,
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
                with worker_factory(session, adapter) as worker:
                    worker.restore_session_state(state)
                    if prepare_definition is not None:
                        result = self._prepare_trusted_write(
                            context=context,
                            session=session,
                            adapter=adapter,
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
                            adapter=adapter,
                            worker=worker,
                            arguments=arguments,
                            prepare_capability=prepare_capability,
                            definition=definition,
                        )
                    else:
                        result = adapter.invoke_capability(
                            capability_name,
                            worker,
                            arguments,
                        )
                        if capability_name == DOCUMENT_CERTIFICATE_SEARCH_CAPABILITY:
                            result = self._materialize_document_downloads(
                                session=session,
                                result=result,
                            )
                    self.session_states.save(
                        session["session_id"],
                        worker.capture_session_state(),
                    )
                    if document_search:
                        self.sessions.touch_activity(session["session_id"])
                    return result
            except AdapterLoginRequired as exc:
                expired_session = self.sessions.mark_expired(session["session_id"], str(exc))
                self.session_states.delete(session["session_id"])
                raise login_required_action(user_subject, system_id, expired_session) from exc
            except AdapterSessionCheckUnavailable as exc:
                raise _session_check_unavailable_action(
                    user_subject,
                    session,
                    diagnostics=str(exc),
                ) from exc

    def fetch_document_download(self, record: dict) -> dict:
        session = self.sessions.get(record["session_id"])
        if any(
            (
                session["user_subject"] != record["user_subject"],
                session["system_id"] != record["system_id"],
                session["state"] != "active",
            )
        ):
            raise ValueError("document download session binding is invalid")
        runtime = self._runtime_for_system(session["system_id"])
        if runtime is None:
            raise ValueError("document download system is not configured")
        adapter, worker_factory = runtime
        with self._session_lock(session["session_id"]):
            state = self.session_states.load(session["session_id"])
            if state is None:
                raise ValueError("document download session state is unavailable")
            try:
                with worker_factory(session, adapter) as worker:
                    worker.restore_session_state(state)
                    payload = adapter.fetch_certificate_document(
                        worker,
                        record["document"],
                    )
                    self.session_states.save(
                        session["session_id"],
                        worker.capture_session_state(),
                    )
                    self.sessions.touch_activity(session["session_id"])
                    return payload
            except AdapterLoginRequired:
                self.sessions.mark_expired(
                    session["session_id"],
                    "OA session expired during certificate download.",
                )
                self.session_states.delete(session["session_id"])
                raise

    def prepare_document_download(
        self,
        *,
        user_subject: str,
        download_id: str,
    ) -> dict:
        try:
            existing = self.document_downloads.get(
                download_id,
                include_document=True,
            )
            if existing["user_subject"] != user_subject:
                raise DocumentDownloadAccessDenied(
                    "document download belongs to another user"
                )
            if existing["state"] == "ready":
                ready = existing
            else:
                record = self.document_downloads.claim_for_prepare(
                    download_id,
                    user_subject=user_subject,
                )
                try:
                    payload = self.fetch_document_download(record)
                    body = payload.get("body")
                    content_type = str(payload.get("content_type") or "")
                    ready = self.document_downloads.mark_ready(
                        download_id,
                        body=body,
                        content_type=content_type,
                    )
                except Exception:
                    try:
                        self.document_downloads.release(download_id)
                    except DocumentDownloadStateError:
                        pass
                    raise
        except DocumentDownloadNotFound:
            return _document_delivery_failure("DOWNLOAD_NOT_FOUND", retryable=False)
        except DocumentDownloadAccessDenied:
            return _document_delivery_failure("DOWNLOAD_ACCESS_DENIED", retryable=False)
        except DocumentDownloadIntegrityError:
            return _document_delivery_failure("DOWNLOAD_INTEGRITY_FAILED", retryable=False)
        except DocumentDownloadStateError:
            return _document_delivery_failure("DOWNLOAD_NOT_READY", retryable=True)
        except AdapterLoginRequired:
            return _document_delivery_failure("LOGIN_REQUIRED", retryable=True)
        except Exception as exc:
            return _document_delivery_failure(
                _document_download_error_code(exc),
                retryable=True,
            )
        return {
            "protocolVersion": "0.1",
            "schemaVersion": "agentbridge.document_delivery.v1",
            "status": "succeeded",
            "file": {
                "downloadId": ready["download_id"],
                "filename": ready["filename"],
                "contentType": ready["content_type"],
                "size": ready["prepared_size"],
                "mediaUrl": f"{ready['card_url']}/file",
                "expiresAt": ready["expires_at"],
            },
            "hostDelivery": {
                "mode": "direct_attachment",
                "oneFilePerMessage": True,
                "handledByHost": True,
            },
        }

    def _materialize_document_downloads(self, *, session: dict, result: dict) -> dict:
        public_result = {key: value for key, value in result.items() if key != "items"}
        public_items = []
        for source_item in result.get("items") or []:
            item = dict(source_item)
            reference = item.pop("_download_reference")
            grant = self.document_downloads.create(
                user_subject=session["user_subject"],
                system_id=session["system_id"],
                session_id=session["session_id"],
                document=reference,
                filename=item["filename"],
                document_type=item["document_type"],
                display_size=item.get("display_size") or "",
                card_base_url=self.trusted_card_base_url,
                ttl_seconds=600,
            )
            item["download_id"] = grant["download_id"]
            item["download_url"] = grant["card_url"]
            item["download_expires_at"] = grant["expires_at"]
            public_items.append(item)
        public_result["items"] = public_items
        return public_result
    def _prepare_trusted_write(
        self,
        *,
        context: CapabilityContext,
        session: dict,
        adapter: object,
        worker: object,
        arguments: dict,
        field_submission: dict,
        definition: dict,
    ) -> dict:
        prepare_function = globals().get(str(definition["prepare_function"]))
        if not callable(prepare_function):
            raise RuntimeError("trusted write prepare function is unavailable")
        self._assert_write_allowed(context=context, system_id=session["system_id"])
        prepared = prepare_function(adapter, worker, arguments)
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
        adapter: object,
        worker: object,
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
            raise ValueError(
                "the downstream session changed after the write plan was authorized"
            )

        self._assert_write_allowed(context=context, system_id=session["system_id"])

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
                adapter,
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
        except AdapterBusinessRuleRejected as exc:
            raise CapabilityRejected(
                exc.error_code,
                str(exc),
            ) from exc
        except definition["contract_error"] as exc:
            raise ValueError(str(exc)) from exc
        except (WriteAuthorizationAccessDenied, WriteAuthorizationStateError) as exc:
            raise ValueError(str(exc)) from exc

    def _assert_write_allowed(self, *, context: CapabilityContext, system_id: str) -> None:
        if context.spec.effect == "read":
            return
        try:
            self.governance_policies.assert_write_allowed(
                system_id=system_id,
                user_subject=context.user_subject,
                capability_name=context.spec.name,
                capability_version=context.spec.version,
            )
        except GovernancePolicyDenied as exc:
            policy = exc.policy
            raise CapabilityRejected(
                "WRITE_PAUSED",
                "Write operation is paused by governance policy "
                f"{policy['scope_type']}:{policy['scope_value']}. "
                f"Reason: {policy['reason']}",
            ) from exc
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
    def _session_lock(
        self,
        session_id: str,
        *,
        wait_seconds: float | None = None,
    ) -> Iterator[None]:
        with self._locks_guard:
            lock = self._session_locks.setdefault(session_id, threading.Lock())
        acquired = (
            lock.acquire()
            if wait_seconds is None
            else lock.acquire(timeout=max(0.0, wait_seconds))
        )
        if not acquired:
            raise CapabilityRejected(
                "SESSION_BUSY",
                "Another OA operation is using this user session. Retry once after it "
                "finishes; batch certificate titles through the names argument instead "
                "of launching parallel searches.",
            )
        try:
            yield
        finally:
            lock.release()

    def _record_user_activity(self, *, user_subject: str, system_id: str) -> None:
        session = self.sessions.find(user_subject=user_subject, system_id=system_id)
        if session is None or session["state"] != "active":
            return
        with self._session_lock(session["session_id"]):
            current = self.sessions.get(session["session_id"])
            if current["state"] == "active":
                self.sessions.touch_activity(current["session_id"])

    def _session_response(self, session: dict) -> dict:
        return session_response(
            session,
            activity_lease_seconds=self.session_keepalive_lease_seconds,
        )

    @staticmethod
    def _default_worker_factory(session: dict, adapter: SeeyonCentralAdapter):
        return CentralBrowserWorker(
            profile_path=session["profile_path"],
            allowed_origins={adapter.origin},
            headless=True,
        )

    @staticmethod
    def _default_browser_worker_factory(session: dict, adapter: object):
        return CentralBrowserWorker(
            profile_path=session["profile_path"],
            allowed_origins=set(
                getattr(adapter, "allowed_origins", None) or {adapter.origin}
            ),
            headless=True,
        )

    @staticmethod
    def _default_http_worker_factory(_session: dict, adapter: object):
        return CentralHttpWorker(allowed_origins={adapter.origin})


def login_required_action(
    user_subject: str,
    system_id: str,
    session: dict | None,
) -> RequiresUserAction:
    return RequiresUserAction(
        "LOGIN_REQUIRED",
        f"The central {system_id} session is not active.",
        next_action={
            "type": "session_login",
            "system": system_id,
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


def session_response(
    session: dict,
    *,
    activity_lease_seconds: float | None = None,
) -> dict:
    last_user_activity = session.get("last_user_activity_at")
    eligible_until = None
    eligible_at = None
    if activity_lease_seconds is not None and last_user_activity:
        activity_at = _parse_utc(last_user_activity)
        if activity_at is not None:
            eligible_at = activity_at + timedelta(seconds=activity_lease_seconds)
            eligible_until = eligible_at.isoformat()

    if activity_lease_seconds is None:
        keepalive_state = "not_configured"
    elif session["state"] != "active":
        keepalive_state = "inactive"
    elif eligible_at is None:
        keepalive_state = "activity_unknown"
    elif datetime.now(timezone.utc) <= eligible_at:
        keepalive_state = "eligible"
    else:
        keepalive_state = "outside_lease"
    return {
        "protocolVersion": "0.1",
        "status": session["state"],
        "sessionId": session["session_id"],
        "systemId": session["system_id"],
        "userSubject": session["user_subject"],
        "expectedPrincipalRef": session.get("expected_principal_ref"),
        "downstreamPrincipalRef": session.get("downstream_principal_ref"),
        "lastVerifiedAt": session.get("last_verified_at"),
        "lastActivityAt": last_user_activity,
        "lastUserActivityAt": last_user_activity,
        "lastKeepaliveAt": session.get("last_keepalive_at"),
        "keepaliveEligibleUntil": eligible_until,
        "keepaliveState": keepalive_state,
        "expiredAt": session.get("expired_at"),
        "lastError": session.get("last_error"),
    }


def _document_delivery_failure(error_code: str, *, retryable: bool) -> dict:
    return {
        "protocolVersion": "0.1",
        "schemaVersion": "agentbridge.document_delivery.v1",
        "status": "failed",
        "error": {
            "code": error_code,
            "message": "AgentBridge could not prepare the OA certificate attachment.",
            "retryable": retryable,
        },
    }


def _document_download_error_code(exc: Exception) -> str:
    name = exc.__class__.__name__
    normalized = "".join(
        character if character.isalnum() else "_"
        for character in name.upper()
    ).strip("_")
    return normalized[:80] or "DOCUMENT_PREPARATION_FAILED"


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


def task_response(task: dict) -> dict:
    return {
        "taskId": task["task_id"],
        "userSubject": task["user_subject"],
        "agentHost": task["agent_host"],
        "title": task["title"],
        "status": task["status"],
        "summary": task.get("summary") or {},
        "originEndpointId": task["origin_endpoint_id"],
        "currentOperationId": task.get("current_operation_id"),
        "currentInteractionId": task.get("current_interaction_id"),
        "activeConversationRef": task["active_conversation_ref"],
        "version": task["version"],
        "createdAt": task["created_at"],
        "updatedAt": task["updated_at"],
        "finishedAt": task.get("finished_at"),
    }


def continuation_response(continuation: dict) -> dict:
    return {
        "endpointId": continuation["endpoint_id"],
        "selectedTaskId": continuation.get("selected_task_id"),
        "candidateTaskIds": continuation.get("candidate_task_ids") or [],
        "state": continuation["state"],
        "executionMode": continuation["execution_mode"],
        "allowNewOperation": continuation.get("allow_new_operation") is True,
        "reason": continuation.get("reason"),
        "expiresAt": continuation["expires_at"],
        "version": continuation["version"],
        "updatedAt": continuation["updated_at"],
    }


def task_continuation_candidate_response(candidate: dict, ordinal: int) -> dict:
    task = candidate["task"]
    origin = candidate["origin_endpoint"]
    return {
        "ordinal": ordinal,
        "taskId": task["task_id"],
        "title": task["title"],
        "status": task["status"],
        "phase": task_continuation_phase(task),
        "origin": {
            "clientType": origin.get("client_type"),
            "label": origin.get("label"),
        },
        "updatedAt": task["updated_at"],
        "finishedAt": task.get("finished_at"),
    }


def task_continuation_phase(task: dict) -> str:
    status = str(task.get("status") or "")
    if status == "waiting_user":
        return "waiting_user"
    if status == "running":
        return "running"
    if status == "active":
        return "ready"
    return "terminal"


def task_continuation_execution_mode(
    task: dict,
    *,
    allow_follow_up: bool,
) -> str:
    status = str(task.get("status") or "")
    if status == "active":
        return "resume"
    if status == "succeeded" and allow_follow_up:
        return "follow_up"
    return "observe_only"


def endpoint_response(endpoint: dict) -> dict:
    return {
        "endpointId": endpoint["endpoint_id"],
        "userSubject": endpoint["user_subject"],
        "agentHost": endpoint["agent_host"],
        "clientType": endpoint["client_type"],
        "externalSubject": endpoint["external_subject"],
        "accountId": endpoint.get("account_id"),
        "conversationRef": endpoint["conversation_ref"],
        "label": endpoint.get("label"),
        "capabilities": endpoint.get("capabilities") or [],
        "route": endpoint.get("route") or {},
        "state": endpoint["state"],
        "createdAt": endpoint["created_at"],
        "updatedAt": endpoint["updated_at"],
        "lastSeenAt": endpoint["last_seen_at"],
    }


def timeline_entry_response(entry: dict) -> dict:
    return {
        "entryId": entry["entry_id"],
        "sequence": entry["sequence"],
        "entryType": entry["entry_type"],
        "taskId": entry.get("task_id"),
        "role": entry.get("role"),
        "text": entry.get("text"),
        "payload": entry.get("payload") or {},
        "createdAt": entry["created_at"],
    }


def _timeline_notification_message(entry: dict) -> str:
    source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
    client_type = str(source.get("clientType") or "unknown")
    source_label = {
        "web": "网页端",
        "webchat": "网页端",
        "telegram": "Telegram",
        "openclaw-weixin": "微信",
    }.get(client_type, str(source.get("label") or "其他端"))
    actor = "你" if entry.get("role") == "user" else "智能体"
    text = str(entry.get("text") or "").strip()
    return f"【{source_label} · {actor}】\n{text}"


_TASK_TITLE_LABELS = {
    "Prepare OA Efficiency-Data Approval": "OA 效能数据审批",
    "Prepare OA Travel-Expense Approval": "OA 差旅费审批",
    "Prepare OA Labor-Contract Renewal Approval": "OA 劳动合同续签审批",
    "Prepare OA Weekly-Report Acknowledgement": "OA 周报阅办",
    "Prepare OA Standard-Collaboration Approval": "OA 普通事项审批",
    "Prepare OA Workflow Revoke": "OA 流程撤销",
    "Prepare OA Business Trip Draft": "OA 出差申请草稿",
    "Prepare OA Business Trip Submission": "OA 出差申请提交",
    "Prepare OA Leave Draft": "OA 请假申请草稿",
    "Prepare OA Leave Submission": "OA 请假申请提交",
    "Prepare OA Missed-Punch Draft": "OA 补签申请草稿",
    "Prepare OA Missed-Punch Approval": "OA 补签申请审批",
    "Prepare OA Meeting Creation": "OA 会议创建",
    "Prepare Taihua Work Log": "泰华工作日志提交",
}


def _task_notification_message(task: dict, event: dict) -> str:
    event_type = str(event.get("eventType") or "")
    raw_title = str(task.get("title") or "AgentBridge 任务")
    title = _TASK_TITLE_LABELS.get(raw_title, raw_title)
    if event_type == "task.created":
        return f"{title}：任务已在另一端发起。"
    if event_type in {
        "task.operation.linked",
        "task.operation.running",
    }:
        return f"{title}：任务正在执行。"
    if event_type == "task.operation.requires_user_action":
        return f"{title}：任务正在等待用户填写或确认。"
    if event_type == "task.interaction.waiting":
        return f"{title}：任务正在等待用户填写或确认。"
    if event_type == "task.interaction.completed":
        return f"{title}：可信确认已完成。"
    if event_type == "task.operation.succeeded":
        return f"{title}：已完成。"
    if event_type == "task.canceled":
        return f"{title}：用户已取消本次操作。"
    if event_type == "task.interaction.expired":
        return f"{title}：可信确认已过期，请从智能体重新发起。"
    if event_type == "task.interaction.superseded":
        return f"{title}：当前交互已被更新，请使用最新卡片继续。"
    if event_type in {
        "task.interaction.failed",
        "task.operation.failed",
    }:
        return f"{title}：执行失败，请查看任务详情。"
    if event_type == "task.operation.outcome_unknown":
        return f"{title}：最终结果未能确认，请先在目标系统核对。"
    return f"{title}：任务状态已更新为 {task['status']}。"


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
