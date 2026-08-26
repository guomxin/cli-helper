from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
import json
import logging
from pathlib import Path
import threading
from time import perf_counter
from typing import Annotated, Any, Literal, Mapping
from urllib.parse import urlparse

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.resources import FunctionResource
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, Field
import uvicorn

from bscli.adapters.seeyon_business_trip import (
    BUSINESS_TRIP_PREPARE_CAPABILITY,
    BUSINESS_TRIP_SAVE_CAPABILITY,
)
from bscli.adapters.seeyon_business_trip_submit import (
    BUSINESS_TRIP_SUBMIT_CAPABILITY,
    BUSINESS_TRIP_SUBMIT_PREPARE_CAPABILITY,
)
from bscli.adapters.seeyon_leave import (
    LEAVE_PREPARE_CAPABILITY,
    LEAVE_SAVE_CAPABILITY,
)
from bscli.adapters.seeyon_leave_submit import (
    LEAVE_SUBMIT_CAPABILITY,
    LEAVE_SUBMIT_PREPARE_CAPABILITY,
)
from bscli.adapters.seeyon_meeting import (
    MEETING_CREATE_CAPABILITY,
    MEETING_PREPARE_CAPABILITY,
)
from bscli.adapters.seeyon_missed_punch import (
    MISSED_PUNCH_APPROVAL_PREPARE_CAPABILITY,
    MISSED_PUNCH_APPROVE_CAPABILITY,
    MISSED_PUNCH_PREPARE_CAPABILITY,
    MISSED_PUNCH_SAVE_CAPABILITY,
)
from bscli.adapters.seeyon_pending_actions import (
    ATTENDANCE_CONFIRMATION_PREPARE_CAPABILITY,
    ATTENDANCE_CONFIRM_CAPABILITY,
    EFFICIENCY_DATA_APPROVAL_PREPARE_CAPABILITY,
    EFFICIENCY_DATA_APPROVE_CAPABILITY,
    INTELLECTUAL_PROPERTY_DECLARATION_APPROVAL_PREPARE_CAPABILITY,
    INTELLECTUAL_PROPERTY_DECLARATION_APPROVE_CAPABILITY,
    LABOR_CONTRACT_RENEWAL_APPROVAL_PREPARE_CAPABILITY,
    LABOR_CONTRACT_RENEWAL_APPROVE_CAPABILITY,
    OVERTIME_APPROVAL_PREPARE_CAPABILITY,
    OVERTIME_APPROVE_CAPABILITY,
    RESIGNATION_APPROVAL_PREPARE_CAPABILITY,
    RESIGNATION_APPROVE_CAPABILITY,
    STANDARD_COLLABORATION_APPROVAL_PREPARE_CAPABILITY,
    STANDARD_COLLABORATION_APPROVE_CAPABILITY,
    TRAVEL_EXPENSE_APPROVAL_PREPARE_CAPABILITY,
    TRAVEL_EXPENSE_APPROVE_CAPABILITY,
    WEEKLY_REPORT_ACKNOWLEDGEMENT_PREPARE_CAPABILITY,
    WEEKLY_REPORT_ACKNOWLEDGE_CAPABILITY,
)
from bscli.adapters.seeyon_workflow_revoke import (
    WORKFLOW_REVOKE_CAPABILITY,
    WORKFLOW_REVOKE_PREPARE_CAPABILITY,
)
from bscli.adapters.taihua import (
    TAIHUA_MY_LOGS_CAPABILITY,
    TAIHUA_PROJECT_SEARCH_CAPABILITY,
    TAIHUA_TEAM_LOGS_CAPABILITY,
    TAIHUA_WORK_LOG_CREATE_CAPABILITY,
    TAIHUA_WORK_LOG_CREATE_PREPARE_CAPABILITY,
)
from bscli.adapters.smartlight import (
    SMARTLIGHT_ALARM_ANALYSIS_CAPABILITY,
    SMARTLIGHT_ALARM_LIST_CAPABILITY,
    SMARTLIGHT_ALARM_REMARK_GET_CAPABILITY,
    SMARTLIGHT_ALARM_WORK_AREA_REVOKE_CAPABILITY,
    SMARTLIGHT_ALARM_WORK_AREA_REVOKE_PREPARE_CAPABILITY,
    SMARTLIGHT_ALARM_WORK_AREA_SUBMIT_CAPABILITY,
    SMARTLIGHT_ALARM_WORK_AREA_SUBMIT_PREPARE_CAPABILITY,
    SMARTLIGHT_ALARM_REMARK_UPDATE_CAPABILITY,
    SMARTLIGHT_ALARM_REMARK_UPDATE_PREPARE_CAPABILITY,
    SMARTLIGHT_ASSET_DETAIL_CAPABILITY,
    SMARTLIGHT_ASSET_SEARCH_CAPABILITY,
    SMARTLIGHT_ENERGY_ANALYSIS_CAPABILITY,
    SMARTLIGHT_ENERGY_RECORD_LIST_CAPABILITY,
    SMARTLIGHT_INSPECTION_LOG_LIST_CAPABILITY,
    SMARTLIGHT_INSPECTION_TASK_DETAIL_CAPABILITY,
    SMARTLIGHT_INSPECTION_TASK_LIST_CAPABILITY,
    SMARTLIGHT_LAMP_SURVEY_RECORDS_CAPABILITY,
    SMARTLIGHT_LAMPPOST_LIST_CAPABILITY,
    SMARTLIGHT_LAMP_ALARM_ANALYSIS_CAPABILITY,
    SMARTLIGHT_LAMP_ALARM_LIST_CAPABILITY,
    SMARTLIGHT_LAMP_STATUS_LIST_CAPABILITY,
    SMARTLIGHT_LEAKAGE_ANALYSIS_CAPABILITY,
    SMARTLIGHT_LEAKAGE_SUMMARY_CAPABILITY,
    SMARTLIGHT_OVERVIEW_CAPABILITY,
    SMARTLIGHT_OFF_HOURS_CURRENT_LIST_CAPABILITY,
    SMARTLIGHT_REPORT_EXPORT_CAPABILITY,
    SMARTLIGHT_RTU_LEAKAGE_ALARM_LIST_CAPABILITY,
    SMARTLIGHT_RTU_LEAKAGE_ANALYSIS_CAPABILITY,
    SMARTLIGHT_RTU_STATUS_LIST_CAPABILITY,
    SMARTLIGHT_RTU_SURVEY_RECORDS_CAPABILITY,
    SMARTLIGHT_RUNTIME_OVERVIEW_CAPABILITY,
    SMARTLIGHT_MAINTENANCE_RECORD_LIST_CAPABILITY,
    SMARTLIGHT_RTU_ALARM_DISPOSE_CAPABILITY,
    SMARTLIGHT_RTU_ALARM_DISPOSE_PREPARE_CAPABILITY,
)
from bscli.adapters.yuque import (
    YUQUE_DOCUMENT_CATALOG_CAPABILITY,
    YUQUE_DOCUMENT_READ_CAPABILITY,
    YUQUE_DOCUMENT_SEARCH_CAPABILITY,
    YUQUE_PUBLIC_BOOKS_CAPABILITY,
)
from bscli.admin.application import AdminControlPlane
from bscli.admin.server import (
    AdminServerConfig,
    create_admin_http_server,
)
from bscli.auth.action_card import TrustedActionApplication
from bscli.auth.card import TrustedAuthApplication
from bscli.auth.field_card import TrustedFieldApplication
from bscli.auth.interactive_browser import TrustedInteractiveBrowserApplication
from bscli.auth.document_download import TrustedDocumentDownloadApplication
from bscli.auth.timeline_attachment import TrustedTimelineAttachmentApplication
from bscli.auth.server import AuthServerConfig, create_auth_http_server
from bscli.broker.credential import CredentialBroker
from bscli.broker.remote_browser import (
    RemoteBrowserConfig,
    RemoteInteractiveBrowserBroker,
)
from bscli.core.central_service import CentralCapabilityService
from bscli.core.mcp_identities import McpIdentityTokenStore
from bscli.core.network_security import validate_insecure_private_http_endpoint
from bscli.core.runtime_diagnostics import HOST_CONTROL_DIAGNOSTICS
from bscli.mcp.presentation import (
    MCP_APP_MIME_TYPE,
    MCP_APP_RESOURCE_URI,
    MCP_PROFILE_RESOURCE_URI,
    build_server_profile,
    interaction_tool_meta,
    load_mcp_app_html,
    package_interaction_result,
    server_profile_json,
)
from bscli.workspace.application import WorkspaceApplication
from bscli.workspace.gateway import OpenClawGatewayClient
from bscli.workspace.server import (
    WorkspaceServerConfig,
    create_workspace_http_server,
)


_LOGGER = logging.getLogger("uvicorn.error")
HOST_CONTEXT_META_KEY = "io.agentbridge/host"
TASK_CONTEXT_META_KEY = "io.agentbridge/task"


AGENT_FACING_TOOL_SCOPE_REQUIREMENTS: Mapping[str, frozenset[str]] = {
    "agentbridge_server_profile": frozenset(),
    "agentbridge_interaction_get": frozenset(),
    "agentbridge_operation_get": frozenset(),
    "agentbridge_operation_list": frozenset(),
    "oa_template_list": frozenset({"oa:read"}),
    "oa_certificate_search": frozenset({"oa:read"}),
    "oa_certificate_prepare_download": frozenset({"oa:read"}),
    "oa_certificate_prepare_downloads": frozenset({"oa:read"}),
    "oa_workflow_pending_list": frozenset({"oa:read"}),
    "oa_workflow_sent_list": frozenset({"oa:read"}),
    "oa_workflow_done_list": frozenset({"oa:read"}),
    "oa_workflow_tracked_list": frozenset({"oa:read"}),
    "oa_workflow_detail_get": frozenset({"oa:read"}),
    "oa_workflow_opinions_list": frozenset({"oa:read"}),
    "oa_session_status": frozenset({"oa:read"}),
    "oa_session_login": frozenset({"oa:read"}),
    "oa_efficiency_data_approval_prepare": frozenset({"oa:write:approval"}),
    "oa_travel_expense_approval_prepare": frozenset({"oa:write:approval"}),
    "oa_labor_contract_renewal_approval_prepare": frozenset(
        {"oa:write:approval"}
    ),
    "oa_intellectual_property_declaration_approval_prepare": frozenset(
        {"oa:write:approval"}
    ),
    "oa_overtime_approval_prepare": frozenset({"oa:write:approval"}),
    "oa_resignation_approval_prepare": frozenset({"oa:write:approval"}),
    "oa_attendance_confirmation_prepare": frozenset(
        {"oa:write:approval"}
    ),
    "oa_weekly_report_acknowledgement_prepare": frozenset(
        {"oa:write:approval"}
    ),
    "oa_standard_collaboration_approval_prepare": frozenset(
        {"oa:write:approval"}
    ),
    "oa_workflow_revoke_prepare": frozenset({"oa:write:revoke"}),
    "oa_business_trip_prepare": frozenset({"oa:write:draft"}),
    "oa_business_trip_submit_prepare": frozenset({"oa:write:submit"}),
    "oa_leave_prepare": frozenset({"oa:write:draft"}),
    "oa_leave_submit_prepare": frozenset({"oa:write:submit"}),
    "oa_missed_punch_prepare": frozenset({"oa:write:draft"}),
    "oa_missed_punch_approval_prepare": frozenset({"oa:write:approval"}),
    "oa_meeting_create_prepare": frozenset({"oa:write:meeting"}),
    "taihua_work_log_my_list": frozenset({"taihua:read"}),
    "taihua_work_log_team_list": frozenset({"taihua:read"}),
    "taihua_project_search": frozenset({"taihua:read"}),
    "taihua_work_log_create_prepare": frozenset({"taihua:write:worklog"}),
    "taihua_session_status": frozenset({"taihua:read"}),
    "taihua_session_login": frozenset({"taihua:read"}),
    "smartlight_system_overview": frozenset({"smartlight:read"}),
    "smartlight_runtime_overview": frozenset({"smartlight:read"}),
    "smartlight_rtu_status_list": frozenset({"smartlight:read"}),
    "smartlight_lamp_status_list": frozenset({"smartlight:read"}),
    "smartlight_lamp_alarm_list": frozenset({"smartlight:read"}),
    "smartlight_lamp_alarm_analysis": frozenset({"smartlight:read"}),
    "smartlight_rtu_survey_records": frozenset({"smartlight:read"}),
    "smartlight_energy_record_list": frozenset({"smartlight:read"}),
    "smartlight_energy_analysis": frozenset({"smartlight:read"}),
    "smartlight_lamp_survey_records": frozenset({"smartlight:read"}),
    "smartlight_rtu_leakage_alarm_list": frozenset({"smartlight:read"}),
    "smartlight_rtu_leakage_analysis": frozenset({"smartlight:read"}),
    "smartlight_off_hours_current_list": frozenset({"smartlight:read"}),
    "smartlight_inspection_log_list": frozenset({"smartlight:read"}),
    "smartlight_maintenance_record_list": frozenset({"smartlight:read"}),
    "smartlight_lamppost_list": frozenset({"smartlight:read"}),
    "smartlight_alarm_list": frozenset({"smartlight:read"}),
    "smartlight_alarm_remark_get": frozenset({"smartlight:read"}),
    "smartlight_inspection_task_list": frozenset({"smartlight:read"}),
    "smartlight_leakage_summary": frozenset({"smartlight:read"}),
    "smartlight_asset_search": frozenset({"smartlight:read"}),
    "smartlight_asset_detail": frozenset({"smartlight:read"}),
    "smartlight_alarm_analysis": frozenset({"smartlight:read"}),
    "smartlight_inspection_task_detail": frozenset({"smartlight:read"}),
    "smartlight_leakage_analysis": frozenset({"smartlight:read"}),
    "smartlight_report_export": frozenset({"smartlight:read"}),
    "smartlight_alarm_remark_update_prepare": frozenset(
        {"smartlight:write:alarm_remark"}
    ),
    "smartlight_alarm_work_area_submit_prepare": frozenset(
        {"smartlight:write:alarm_work_area_submit"}
    ),
    "smartlight_alarm_work_area_revoke_prepare": frozenset(
        {"smartlight:write:alarm_work_area_revoke"}
    ),
    "smartlight_rtu_alarm_dispose_prepare": frozenset(
        {"smartlight:write:alarm_disposition"}
    ),
    "smartlight_session_status": frozenset({"smartlight:read"}),
    "smartlight_session_login": frozenset({"smartlight:read"}),
    "yuque_public_books_list": frozenset({"yuque:read"}),
    "yuque_document_catalog": frozenset({"yuque:read"}),
    "yuque_document_search": frozenset({"yuque:read"}),
    "yuque_document_read": frozenset({"yuque:read"}),
    "yuque_session_status": frozenset({"yuque:read"}),
    "yuque_session_login": frozenset({"yuque:read"}),
}


def agent_facing_tools_for_scopes(scopes: list[str] | set[str]) -> list[str]:
    granted = set(scopes)
    return [
        name
        for name, required in AGENT_FACING_TOOL_SCOPE_REQUIREMENTS.items()
        if required.issubset(granted)
    ]


async def _run_host_control(
    operation_name: str,
    operation: Any,
    *,
    warn_after_seconds: float = 1.0,
    **kwargs: Any,
) -> Any:
    user_subject = str(kwargs.get("user_subject") or "unknown")
    started_at = perf_counter()
    error_code = None
    try:
        return await asyncio.to_thread(operation, **kwargs)
    except Exception as exc:
        error_code = exc.__class__.__name__
        raise
    finally:
        elapsed_seconds = perf_counter() - started_at
        elapsed_ms = round(elapsed_seconds * 1000)
        HOST_CONTROL_DIAGNOSTICS.record(
            operation_name=operation_name,
            user_subject=user_subject,
            elapsed_ms=elapsed_ms,
            error_code=error_code,
        )
        if elapsed_seconds >= warn_after_seconds:
            _LOGGER.warning(
                "AgentBridge host control slow: tool=%s elapsed_ms=%d "
                "user_subject=%s",
                operation_name,
                elapsed_ms,
                user_subject,
            )


@dataclass(frozen=True)
class CentralMcpServerConfig:
    host: str
    port: int
    public_base_url: str
    tls_cert: Path | None
    tls_key: Path | None

    @property
    def mcp_url(self) -> str:
        return f"{self.public_base_url}/mcp"

    @property
    def insecure_private_http(self) -> bool:
        return self.tls_cert is None and not _is_loopback_host(self.host)


class CentralSessionKeepalive:
    def __init__(
        self,
        service: CentralCapabilityService,
        *,
        interval_seconds: float,
        activity_lease_seconds: float,
        initial_delay_seconds: float = 1,
    ) -> None:
        if interval_seconds < 0:
            raise ValueError("keepalive interval cannot be negative")
        if activity_lease_seconds <= 0:
            raise ValueError("keepalive activity lease must be positive")
        if interval_seconds > 0 and activity_lease_seconds < interval_seconds:
            raise ValueError("keepalive activity lease cannot be shorter than its interval")
        self.service = service
        self.interval_seconds = interval_seconds
        self.activity_lease_seconds = activity_lease_seconds
        self.initial_delay_seconds = initial_delay_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self.interval_seconds > 0

    def start(self) -> None:
        if not self.enabled:
            return
        if self._thread is not None:
            raise RuntimeError("central session keepalive is already started")
        self._thread = threading.Thread(
            target=self._run,
            name="agentbridge-session-keepalive",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _run(self) -> None:
        if self._stop_event.wait(max(0, self.initial_delay_seconds)):
            return
        while not self._stop_event.is_set():
            try:
                summary = self.service.run_session_keepalive_cycle(
                    activity_lease_seconds=self.activity_lease_seconds,
                )
                _LOGGER.info(
                    "AgentBridge session keepalive cycle: active=%d eligible=%d "
                    "kept_alive=%d expired=%d deferred=%d outside_lease=%d",
                    summary["activeSessions"],
                    summary["eligibleSessions"],
                    summary["keptAlive"],
                    summary["expired"],
                    summary["deferred"],
                    summary["outsideLease"],
                )
                if summary.get("issues"):
                    _LOGGER.warning(
                        "AgentBridge session keepalive issues: %s",
                        json.dumps(
                            summary["issues"],
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    )
            except Exception as exc:
                _LOGGER.warning(
                    "AgentBridge session keepalive cycle failed: error=%s",
                    exc.__class__.__name__,
                )
                try:
                    self.service.runtime_governance.record_signal(
                        signal_type="session.keepalive.worker",
                        source="central_session_keepalive",
                        status="failed",
                        value={"errorCode": exc.__class__.__name__},
                    )
                except Exception:
                    pass
            if self._stop_event.wait(self.interval_seconds):
                return


class CentralRuntimeGovernanceWorker:
    def __init__(
        self,
        service: CentralCapabilityService,
        *,
        interval_seconds: float = 60,
        initial_delay_seconds: float = 5,
    ) -> None:
        self.service = service
        self.interval_seconds = max(float(interval_seconds), 15)
        self.initial_delay_seconds = max(float(initial_delay_seconds), 0)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("central runtime governance worker is already started")
        self._thread = threading.Thread(
            target=self._run,
            name="agentbridge-runtime-governance",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def run_cycle(self) -> dict[str, Any]:
        task_diagnostics = self.service.tasks.runtime_diagnostics()
        evaluation = self.service.runtime_governance.evaluate_incidents(
            task_diagnostics=task_diagnostics,
        )
        slo = self.service.runtime_governance.refresh_slo_rollups(hours=24)
        observations = self.service.runtime_governance.capture_observation_snapshots(
            slo=slo,
            minimum_interval_minutes=60,
        )
        retention = self.service.runtime_governance.prune()
        return {
            "evaluation": evaluation,
            "slo": slo,
            "observations": observations,
            "retention": retention,
        }

    def _run(self) -> None:
        if self._stop_event.wait(self.initial_delay_seconds):
            return
        while not self._stop_event.is_set():
            try:
                result = self.run_cycle()
                _LOGGER.info(
                    "AgentBridge runtime governance cycle: open=%d observed=%d resolved=%d",
                    result["evaluation"]["open"],
                    result["evaluation"]["observed"],
                    result["evaluation"]["resolved"],
                )
            except Exception as exc:
                _LOGGER.warning(
                    "AgentBridge runtime governance cycle failed: error=%s",
                    exc.__class__.__name__,
                )
                try:
                    self.service.runtime_governance.record_signal(
                        signal_type="runtime.governance.worker",
                        source="central_runtime_governance",
                        status="failed",
                        value={"errorCode": exc.__class__.__name__},
                    )
                except Exception:
                    pass
            if self._stop_event.wait(self.interval_seconds):
                return


class StoredIdentityTokenVerifier(TokenVerifier):
    def __init__(self, store: McpIdentityTokenStore, *, resource: str) -> None:
        self.store = store
        self.resource = resource

    async def verify_token(self, token: str) -> AccessToken | None:
        identity = self.store.verify(token)
        if identity is None:
            return None
        expires_at = int(datetime.fromisoformat(identity["expires_at"]).timestamp())
        return AccessToken(
            token=identity["token_id"],
            client_id=identity["token_id"],
            scopes=identity["scopes"],
            expires_at=expires_at,
            resource=self.resource,
        )


def validate_central_mcp_server_config(
    *,
    host: str,
    port: int,
    public_base_url: str | None,
    tls_cert: str | Path | None,
    tls_key: str | Path | None,
    allow_insecure_private_http: bool = False,
) -> CentralMcpServerConfig:
    if port < 1 or port > 65535:
        raise ValueError("central MCP server port is invalid")
    cert = Path(tls_cert).resolve() if tls_cert else None
    key = Path(tls_key).resolve() if tls_key else None
    if (cert is None) != (key is None):
        raise ValueError("both central MCP TLS certificate and key are required")
    loopback = _is_loopback_host(host)
    if public_base_url is None:
        if not loopback:
            raise ValueError("non-loopback central MCP service requires a public base URL")
        public_base_url = f"http://127.0.0.1:{port}"
    normalized = _normalize_public_base_url(public_base_url)
    if not loopback and cert is None and not allow_insecure_private_http:
        raise ValueError("non-loopback central MCP service requires TLS")
    if not loopback and cert is None:
        validate_insecure_private_http_endpoint(
            host=host,
            port=port,
            public_base_url=normalized,
            service_name="central MCP service",
        )
    elif not loopback and not normalized.startswith("https://"):
        raise ValueError("non-loopback central MCP public URL must use HTTPS")
    if cert is not None and not normalized.startswith("https://"):
        raise ValueError("TLS central MCP service must use an HTTPS public URL")
    return CentralMcpServerConfig(
        host=host,
        port=port,
        public_base_url=normalized,
        tls_cert=cert,
        tls_key=key,
    )


def _register_pending_action_tools(
    mcp: FastMCP,
    invoke,
    *,
    prepare_tool_name: str,
    prepare_title: str,
    prepare_description: str,
    prepare_capability: str,
    commit_tool_name: str,
    commit_title: str,
    commit_description: str,
    commit_capability: str,
) -> None:
    @mcp.tool(
        name=prepare_tool_name,
        title=prepare_title,
        meta=interaction_tool_meta(),
        description=prepare_description,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def prepare_pending_action(
        ctx: Context,
        affair_id: Annotated[str, Field(min_length=1, max_length=256)],
        opinion: Annotated[str | None, Field(max_length=1000)] = None,
        input_submission_id: Annotated[
            str | None,
            Field(min_length=32, max_length=128),
        ] = None,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"affair_id": affair_id}
        if opinion is not None:
            arguments["opinion"] = opinion
        if input_submission_id is not None:
            arguments["input_submission_id"] = input_submission_id
        return await invoke(
            ctx,
            prepare_capability,
            arguments,
            idempotency_key,
            {"oa:write:approval"},
        )

    @mcp.tool(
        name=commit_tool_name,
        title=commit_title,
        meta=interaction_tool_meta(),
        description=commit_description,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def commit_pending_action(
        ctx: Context,
        authorization_id: Annotated[str, Field(min_length=32, max_length=128)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            commit_capability,
            {"authorization_id": authorization_id},
            idempotency_key,
            {"oa:write:approval"},
        )

def create_central_mcp_server(
    *,
    service: CentralCapabilityService,
    identity_store: McpIdentityTokenStore,
    config: CentralMcpServerConfig,
    auth_card_base_url: str,
) -> FastMCP:
    origin = _origin(config.public_base_url)
    netloc = urlparse(config.public_base_url).netloc
    verifier = StoredIdentityTokenVerifier(identity_store, resource=config.mcp_url)
    mcp = FastMCP(
        name="agentbridge_central_mcp",
        instructions=(
            "Central legacy-system business capabilities. Caller identity comes from the "
            "authenticated Bearer token; never request or accept a user subject as tool input. "
            "Every controlled write requires trusted field and authorization interactions."
        ),
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(config.public_base_url),
            resource_server_url=AnyHttpUrl(config.mcp_url),
            required_scopes=[],
        ),
        host=config.host,
        port=config.port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[netloc],
            allowed_origins=[origin],
        ),
    )
    read_annotations = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
    profile = build_server_profile(mcp_url=config.mcp_url)

    @mcp.resource(
        MCP_PROFILE_RESOURCE_URI,
        name="agentbridge_server_profile",
        title="AgentBridge Server Profile",
        description="Machine-readable remote MCP, interaction, and client-footprint profile.",
        mime_type="application/json",
    )
    def agentbridge_server_profile_resource() -> str:
        return server_profile_json(mcp_url=config.mcp_url)

    app_resource = FunctionResource.from_function(
        load_mcp_app_html,
        uri=MCP_APP_RESOURCE_URI,
        name="agentbridge_trusted_interaction",
        title="AgentBridge Trusted Interaction",
        description="Host-rendered trusted interaction surface for AgentBridge cards.",
        mime_type="text/html",
    )
    # FastMCP 1.23 rejects MIME parameters even though MCP Apps requires this profile.
    object.__setattr__(app_resource, "mime_type", MCP_APP_MIME_TYPE)
    mcp.add_resource(app_resource)

    @mcp.prompt(
        name="agentbridge_oa_operator",
        title="Operate OA through AgentBridge",
        description="Concise operating rules for agent hosts without an installed Skill.",
    )
    def agentbridge_oa_operator() -> str:
        return (
            "Use AgentBridge OA tools with the authenticated server-bound identity. "
            "Never ask the user to send OA passwords, business form values, or approval "
            "decisions in chat. When a result requires trusted interaction, let an MCP "
            "App or private host adapter render it. The host has already received the card, "
            "so do not call agentbridge_interaction_get again in the same turn. Only when "
            "the user reports in a later turn that no card appeared may you fetch it again. "
            "Resume only "
            "after resume.ready is true. For meeting preparation, forward scheduling values "
            "already supplied by the user and never invent missing values; AgentBridge checks "
            "live room availability before opening a prefilled card. Writes remain "
            "prepare -> authorize -> commit -> verify."
        )

    async def invoke(
        ctx: Context,
        capability_name: str,
        arguments: dict,
        idempotency_key: str | None,
        required_scopes: set[str] | None = None,
    ) -> dict[str, Any]:
        identity = _request_identity(
            identity_store,
            required_scopes=required_scopes or {"oa:read"},
        )
        request_id = str(ctx.request_id)
        runtime_context = _request_runtime_context(ctx)
        mcp_started_at = datetime.now(timezone.utc).isoformat()
        mcp_started = perf_counter()
        task_id = _request_task_id(ctx)
        if task_id:
            await asyncio.to_thread(
                service.observe_host_task,
                user_subject=identity["user_subject"],
                task_id=task_id,
                operation_ids=[],
                interaction_ids=[],
            )
        try:
            response = await asyncio.to_thread(
                service.invoke,
                user_subject=identity["user_subject"],
                capability_name=capability_name,
                arguments=arguments,
                idempotency_key=idempotency_key,
                request_id=request_id,
                task_id=task_id,
                host_type=runtime_context["hostType"],
                host_instance_id=runtime_context.get("hostInstanceId"),
                host_run_id=runtime_context.get("hostRunId"),
                origin_endpoint_id=runtime_context.get("endpointId"),
            )
        except Exception as exc:
            trace = service.runtime_governance.trace_for_request(
                request_id,
                user_subject=identity["user_subject"],
            )
            if trace is not None:
                failure_code = getattr(exc, "code", None) or exc.__class__.__name__
                service.runtime_governance.record_stage_once(
                    trace_id=trace["trace_id"],
                    stage="mcp.request",
                    status="failed",
                    error_code=str(failure_code)[:120],
                    started_at=mcp_started_at,
                    duration_ms=round((perf_counter() - mcp_started) * 1000),
                    metadata={"requestId": request_id},
                )
            if task_id:
                try:
                    await asyncio.to_thread(
                        service.fail_host_task,
                        user_subject=identity["user_subject"],
                        task_id=task_id,
                        error_code="MCP_TOOL_EXECUTION_FAILED",
                        message=str(exc) or exc.__class__.__name__,
                        causation_ref=str(ctx.request_id),
                    )
                except Exception:
                    logging.getLogger(__name__).exception(
                        "AgentBridge could not close a failed host task"
                    )
            raise
        if task_id:
            operation_id = response.get("operationId")
            interaction = response.get("interaction")
            await asyncio.to_thread(
                service.observe_host_task,
                user_subject=identity["user_subject"],
                task_id=task_id,
                operation_ids=[operation_id] if operation_id else [],
                interaction_ids=(
                    [interaction["interactionId"]]
                    if isinstance(interaction, dict)
                    and interaction.get("interactionId")
                    else []
                ),
            )
        trace_id = response.get("runtimeTraceId")
        if trace_id:
            service.runtime_governance.record_stage_once(
                trace_id=trace_id,
                stage="mcp.request",
                status=(
                    "unknown"
                    if response.get("status") == "unknown"
                    else "failed"
                    if response.get("status") == "failed"
                    else "succeeded"
                ),
                operation_id=response.get("operationId"),
                error_code=(response.get("error") or {}).get("code"),
                started_at=mcp_started_at,
                duration_ms=round((perf_counter() - mcp_started) * 1000),
                metadata={"requestId": request_id},
            )
        return package_interaction_result(response)

    pending_action_tools = (
        {
            "prepare_tool_name": "oa_efficiency_data_approval_prepare",
            "prepare_title": "Prepare OA Efficiency-Data Approval",
            "prepare_description": (
                "Bind one exact pending efficiency-data affair. Pass any opinion already "
                "supplied by the user; omitted opinion stays editable in the trusted card."
            ),
            "prepare_capability": EFFICIENCY_DATA_APPROVAL_PREPARE_CAPABILITY,
            "commit_tool_name": "oa_efficiency_data_approve",
            "commit_title": "Approve Authorized OA Efficiency Data",
            "commit_description": (
                "Consume one approved authorization and verify that the exact "
                "efficiency-data item leaves the pending collection."
            ),
            "commit_capability": EFFICIENCY_DATA_APPROVE_CAPABILITY,
        },
        {
            "prepare_tool_name": "oa_travel_expense_approval_prepare",
            "prepare_title": "Prepare OA Travel-Expense Approval",
            "prepare_description": (
                "Bind one exact pending travel-expense reimbursement. Pass any opinion "
                "already supplied; AgentBridge shows amount, key fields, and attachment count."
            ),
            "prepare_capability": TRAVEL_EXPENSE_APPROVAL_PREPARE_CAPABILITY,
            "commit_tool_name": "oa_travel_expense_approve",
            "commit_title": "Approve Authorized OA Travel Expense",
            "commit_description": (
                "Consume one approved authorization and verify that the exact "
                "travel-expense reimbursement leaves the pending collection."
            ),
            "commit_capability": TRAVEL_EXPENSE_APPROVE_CAPABILITY,
        },
        {
            "prepare_tool_name": "oa_labor_contract_renewal_approval_prepare",
            "prepare_title": "Prepare OA Labor-Contract Renewal Approval",
            "prepare_description": (
                "Bind one exact pending labor-contract renewal approval. AgentBridge "
                "freezes employee, contract term, evaluation, and renewal recommendation; "
                "pass any opinion already supplied by the user."
            ),
            "prepare_capability": LABOR_CONTRACT_RENEWAL_APPROVAL_PREPARE_CAPABILITY,
            "commit_tool_name": "oa_labor_contract_renewal_approve",
            "commit_title": "Approve Authorized OA Labor-Contract Renewal",
            "commit_description": (
                "Consume one approved authorization and verify that the exact "
                "labor-contract renewal item leaves the pending collection."
            ),
            "commit_capability": LABOR_CONTRACT_RENEWAL_APPROVE_CAPABILITY,
        },
        {
            "prepare_tool_name": (
                "oa_intellectual_property_declaration_approval_prepare"
            ),
            "prepare_title": (
                "Prepare OA Intellectual-Property Declaration Approval"
            ),
            "prepare_description": (
                "Bind one exact pending intellectual-property declaration. "
                "AgentBridge freezes applicant, declaration type and name, inventors, "
                "ownership, purpose, and application material; pass any opinion "
                "already supplied by the user."
            ),
            "prepare_capability": (
                INTELLECTUAL_PROPERTY_DECLARATION_APPROVAL_PREPARE_CAPABILITY
            ),
            "commit_tool_name": "oa_intellectual_property_declaration_approve",
            "commit_title": (
                "Approve Authorized OA Intellectual-Property Declaration"
            ),
            "commit_description": (
                "Consume one approved authorization and verify that the exact "
                "intellectual-property declaration leaves the pending collection."
            ),
            "commit_capability": (
                INTELLECTUAL_PROPERTY_DECLARATION_APPROVE_CAPABILITY
            ),
        },
        {
            "prepare_tool_name": "oa_overtime_approval_prepare",
            "prepare_title": "Prepare OA Overtime Approval",
            "prepare_description": (
                "Bind one exact pending overtime approval. AgentBridge freezes the "
                "applicant, requested and actual time ranges, reason, supervisor field, "
                "and duration; pass any opinion already supplied by the user."
            ),
            "prepare_capability": OVERTIME_APPROVAL_PREPARE_CAPABILITY,
            "commit_tool_name": "oa_overtime_approve",
            "commit_title": "Approve Authorized OA Overtime Request",
            "commit_description": (
                "Consume one approved authorization and verify that the exact "
                "overtime request leaves the pending collection."
            ),
            "commit_capability": OVERTIME_APPROVE_CAPABILITY,
        },
        {
            "prepare_tool_name": "oa_resignation_approval_prepare",
            "prepare_title": "Prepare OA Resignation Approval",
            "prepare_description": (
                "Bind one exact pending resignation request. AgentBridge validates the "
                "registered HR template and freezes employee, department, position, "
                "hire and resignation dates, certificate status, reason, and handwritten "
                "application; pass any opinion already supplied by the user."
            ),
            "prepare_capability": RESIGNATION_APPROVAL_PREPARE_CAPABILITY,
            "commit_tool_name": "oa_resignation_approve",
            "commit_title": "Approve Authorized OA Resignation Request",
            "commit_description": (
                "Consume one approved authorization and verify that the exact "
                "resignation request leaves the pending collection."
            ),
            "commit_capability": RESIGNATION_APPROVE_CAPABILITY,
        },
        {
            "prepare_tool_name": "oa_attendance_confirmation_prepare",
            "prepare_title": "Prepare OA Attendance Confirmation",
            "prepare_description": (
                "Bind one exact pending monthly attendance confirmation. AgentBridge "
                "validates the HR template before opening a card and freezes employee, "
                "attendance totals, and the OA-selected objection decision."
            ),
            "prepare_capability": ATTENDANCE_CONFIRMATION_PREPARE_CAPABILITY,
            "commit_tool_name": "oa_attendance_confirm",
            "commit_title": "Confirm Authorized OA Monthly Attendance",
            "commit_description": (
                "Consume one approved authorization, submit the frozen monthly "
                "attendance confirmation, and verify pending disappearance."
            ),
            "commit_capability": ATTENDANCE_CONFIRM_CAPABILITY,
        },
        {
            "prepare_tool_name": "oa_weekly_report_acknowledgement_prepare",
            "prepare_title": "Prepare OA Weekly-Report Acknowledgement",
            "prepare_description": (
                "Bind one exact pending weekly-report inform item. This is acknowledgement, "
                "not approval; pass any review opinion already supplied by the user."
            ),
            "prepare_capability": WEEKLY_REPORT_ACKNOWLEDGEMENT_PREPARE_CAPABILITY,
            "commit_tool_name": "oa_weekly_report_acknowledge",
            "commit_title": "Acknowledge Authorized OA Weekly Report",
            "commit_description": (
                "Consume one approved authorization, acknowledge the exact weekly-report "
                "inform item, and verify pending disappearance."
            ),
            "commit_capability": WEEKLY_REPORT_ACKNOWLEDGE_CAPABILITY,
        },
        {
            "prepare_tool_name": "oa_standard_collaboration_approval_prepare",
            "prepare_title": "Prepare OA Standard-Collaboration Approval",
            "prepare_description": (
                "Bind one exact ordinary collaboration affair outside registered HR, "
                "expense, procurement, seal, efficiency-data, and weekly-report profiles."
            ),
            "prepare_capability": STANDARD_COLLABORATION_APPROVAL_PREPARE_CAPABILITY,
            "commit_tool_name": "oa_standard_collaboration_approve",
            "commit_title": "Approve Authorized OA Standard Collaboration",
            "commit_description": (
                "Consume one approved authorization and verify that the exact ordinary "
                "collaboration item leaves the pending collection."
            ),
            "commit_capability": STANDARD_COLLABORATION_APPROVE_CAPABILITY,
        },
    )
    for tool_definition in pending_action_tools:
        _register_pending_action_tools(mcp, invoke, **tool_definition)

    @mcp.tool(
        name="agentbridge_server_profile",
        title="Get AgentBridge Server Profile",
        description=(
            "Describe this remote MCP endpoint, trusted-interaction delivery methods, "
            "client footprint, and write-safety boundary."
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def agentbridge_server_profile() -> dict[str, Any]:
        return profile

    @mcp.tool(
        name="oa_template_list",
        title="List OA Templates",
        description="List templates available to the authenticated OA user.",
        annotations=read_annotations,
        structured_output=True,
    )
    async def oa_template_list(
        ctx: Context,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(ctx, "oa.template.list", {}, idempotency_key)

    @mcp.tool(
        name="oa_certificate_search",
        title="Search OA Certificate Scans",
        description=(
            "Search one or up to 20 patent or software-copyright certificate scans "
            "in OA Document Center. When extracting titles from an image, read each row "
            "independently and preserve every visible software version. Prefer documents "
            "with separate name, version, and aliases fields; names remains available for "
            "older clients. Use one batch call and do not "
            "launch parallel searches for the same user. When the user says software "
            "copyright or 软著, set document_type=software_copyright_certificate. "
            "Software-copyright lookup removes a trailing version only for OA recall, "
            "tries a bracketed short name only when the formal name has no accessible "
            "match, then verifies the requested version against every returned title. "
            "If ambiguous_queries is non-empty, do not prepare any candidate for those "
            "queries; ask for the desired version instead. "
            "Use all only when the type is genuinely unknown. Exact matches rank first; "
            "each accessible result contains a short-lived trusted download ID and URL. "
            "When the user selects several results, call "
            "oa_certificate_prepare_downloads once with all download IDs. Use the singular "
            "prepare tool only for one result. Never write an ad-hoc download script or emit "
            "several MEDIA attachments in one model reply."
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def oa_certificate_search(
        ctx: Context,
        name: Annotated[str | None, Field(min_length=2, max_length=160)] = None,
        names: Annotated[list[str] | None, Field(min_length=1, max_length=20)] = None,
        documents: Annotated[
            list[dict[str, Any]] | None,
            Field(min_length=1, max_length=20),
        ] = None,
        document_type: Literal[
            "all",
            "patent_certificate",
            "software_copyright_certificate",
        ] = "all",
        limit: Annotated[int, Field(ge=1, le=20)] = 10,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments = {
            "document_type": document_type,
            "limit": limit,
        }
        if name is not None:
            arguments["name"] = name
        if names is not None:
            arguments["names"] = names
        if documents is not None:
            arguments["documents"] = documents
        return await invoke(
            ctx,
            "oa.document.certificate.search",
            arguments,
            idempotency_key,
        )
    @mcp.tool(
        name="oa_certificate_prepare_download",
        title="Prepare and Deliver One OA Certificate Scan",
        description=(
            "Fetch one certificate selected by oa_certificate_search into AgentBridge's "
            "short-lived cache. Call this once per download_id. The OpenClaw host adapter "
            "delivers the resulting file as one attachment message, so do not create local "
            "download scripts and do not repeat the returned media URL in a MEDIA line. "
            "Read hostDelivery in the returned result. When completionMeaning is "
            "endpoint_delivery_reported, report its exact attachment, fallback-link, "
            "and failure counts to the user. Otherwise only say the file is prepared "
            "or being sent; never assume endpoint delivery from preparation alone."
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def oa_certificate_prepare_download(
        ctx: Context,
        download_id: Annotated[str, Field(min_length=32, max_length=128)],
    ) -> dict[str, Any]:
        identity = _request_identity(
            identity_store,
            required_scopes={"oa:read"},
        )
        task_id = _request_task_id(ctx)
        if task_id:
            await asyncio.to_thread(
                service.observe_host_task,
                user_subject=identity["user_subject"],
                task_id=task_id,
                operation_ids=[],
                interaction_ids=[],
            )
        return await asyncio.to_thread(
            service.prepare_document_download,
            user_subject=identity["user_subject"],
            download_id=download_id,
            task_id=task_id,
        )

    @mcp.tool(
        name="oa_certificate_prepare_downloads",
        title="Prepare and Deliver OA Certificate Scans",
        description=(
            "Fetch 1 to 20 certificate results selected by oa_certificate_search in one "
            "central OA session. Pass every selected download ID in one call. AgentBridge "
            "reuses one browser worker where possible and the OpenClaw host delivers each "
            "original file in its own attachment message. Do not call the singular prepare "
            "tool for the same IDs and do not repeat media URLs in model output. Read "
            "hostDelivery in the returned result. When completionMeaning is "
            "endpoint_delivery_reported, report its exact attachment, fallback-link, "
            "and failure counts to the user. Otherwise only say the files are prepared "
            "or being sent; never assume endpoint delivery from preparation alone."
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def oa_certificate_prepare_downloads(
        ctx: Context,
        download_ids: Annotated[
            list[Annotated[str, Field(min_length=32, max_length=128)]],
            Field(min_length=1, max_length=20),
        ],
    ) -> dict[str, Any]:
        identity = _request_identity(
            identity_store,
            required_scopes={"oa:read"},
        )
        task_id = _request_task_id(ctx)
        if task_id:
            await asyncio.to_thread(
                service.observe_host_task,
                user_subject=identity["user_subject"],
                task_id=task_id,
                operation_ids=[],
                interaction_ids=[],
            )
        return await asyncio.to_thread(
            service.prepare_document_downloads,
            user_subject=identity["user_subject"],
            download_ids=download_ids,
            task_id=task_id,
        )

    @mcp.tool(
        name="oa_workflow_pending_list",
        title="List Pending OA Workflows",
        description="List pending workflows for the authenticated OA user.",
        annotations=read_annotations,
        structured_output=True,
    )
    async def oa_workflow_pending_list(
        ctx: Context,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments = {"limit": limit}
        if keyword:
            arguments["keyword"] = keyword
        return await invoke(ctx, "oa.workflow.pending.list", arguments, idempotency_key)

    @mcp.tool(
        name="oa_workflow_sent_list",
        title="List Sent OA Workflows",
        description=(
            "List workflows initiated by the authenticated OA user from the Sent page. "
            "This is distinct from workflows the user has handled (Done) or follows (Tracked). "
            "Use this list to resolve a concise request such as 'revoke the business-trip "
            "request I just submitted'. Select only one unique recent match; if several remain, "
            "ask for a human-readable title or date, never an affair ID or task ID."
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def oa_workflow_sent_list(
        ctx: Context,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments = {"limit": limit}
        if keyword:
            arguments["keyword"] = keyword
        return await invoke(ctx, "oa.workflow.sent.list", arguments, idempotency_key)

    @mcp.tool(
        name="oa_workflow_done_list",
        title="List Completed OA Workflows",
        description=(
            "List workflows already handled by the authenticated OA user from the Done page. "
            "This is distinct from workflows initiated by the user (Sent)."
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def oa_workflow_done_list(
        ctx: Context,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments = {"limit": limit}
        if keyword:
            arguments["keyword"] = keyword
        return await invoke(ctx, "oa.workflow.done.list", arguments, idempotency_key)

    @mcp.tool(
        name="oa_workflow_tracked_list",
        title="List Tracked OA Workflows",
        description=(
            "List workflows followed by the authenticated OA user from the Tracked page. "
            "This is distinct from Sent and Done workflows."
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def oa_workflow_tracked_list(
        ctx: Context,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments = {"limit": limit}
        if keyword:
            arguments["keyword"] = keyword
        return await invoke(ctx, "oa.workflow.tracked.list", arguments, idempotency_key)

    @mcp.tool(
        name="oa_workflow_detail_get",
        title="Get OA Workflow Detail",
        description=(
            "Get rendered business fields, text, attachments, and opinions for an opaque "
            "workflow affair ID returned by a list tool."
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def oa_workflow_detail_get(
        ctx: Context,
        collection: Literal["pending", "sent", "done", "tracked"],
        affair_id: Annotated[str, Field(min_length=1, max_length=256)],
        text_limit: Annotated[int, Field(ge=0, le=20000)] = 6000,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            "oa.workflow.detail.get",
            {
                "collection": collection,
                "affair_id": affair_id,
                "text_limit": text_limit,
            },
            idempotency_key,
        )

    @mcp.tool(
        name="oa_workflow_opinions_list",
        title="List OA Workflow Opinions",
        description="List structured opinions for an opaque workflow affair ID.",
        annotations=read_annotations,
        structured_output=True,
    )
    async def oa_workflow_opinions_list(
        ctx: Context,
        collection: Literal["pending", "sent", "done", "tracked"],
        affair_id: Annotated[str, Field(min_length=1, max_length=256)],
        limit: Annotated[int, Field(ge=1, le=100)] = 100,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            "oa.workflow.opinions.list",
            {"collection": collection, "affair_id": affair_id, "limit": limit},
            idempotency_key,
        )

    @mcp.tool(
        name="oa_workflow_revoke_prepare",
        title="Prepare OA Workflow Revoke",
        meta=interaction_tool_meta(),
        description=(
            "Prepare a separate revoke task for exactly one opaque affair ID resolved from "
            "the current conversation or a prior sent-workflow result. The user may simply "
            "say 'revoke the business-trip request I just submitted'; do not ask them for an "
            "affair ID, process ID, task ID, or exact technical title. If several sent items "
            "remain plausible, ask only for a human-readable title or date. Pass any revoke "
            "comment already supplied by the user. AgentBridge opens a prefilled trusted card, "
            "validates the exact active target, and creates separate revoke authorization."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def oa_workflow_revoke_prepare(
        ctx: Context,
        affair_id: Annotated[str, Field(min_length=1, max_length=256)],
        repeal_comment: Annotated[str | None, Field(max_length=100)] = None,
        input_submission_id: Annotated[
            str | None,
            Field(min_length=32, max_length=128),
        ] = None,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"affair_id": affair_id}
        if repeal_comment is not None:
            arguments["repeal_comment"] = repeal_comment
        if input_submission_id is not None:
            arguments["input_submission_id"] = input_submission_id
        return await invoke(
            ctx,
            WORKFLOW_REVOKE_PREPARE_CAPABILITY,
            arguments,
            idempotency_key,
            {"oa:write:revoke"},
        )

    @mcp.tool(
        name="oa_workflow_revoke",
        title="Revoke Authorized OA Workflow",
        meta=interaction_tool_meta(),
        description=(
            "Consume one approved authorization, revoke exactly one frozen sent workflow "
            "through OA's native action, and verify that the same workflow returned to "
            "wait-send with revoked state."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def oa_workflow_revoke(
        ctx: Context,
        authorization_id: Annotated[str, Field(min_length=32, max_length=128)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            WORKFLOW_REVOKE_CAPABILITY,
            {"authorization_id": authorization_id},
            idempotency_key,
            {"oa:write:revoke"},
        )
    @mcp.tool(
        name="oa_business_trip_prepare",
        title="Prepare OA Business Trip Draft",
        meta=interaction_tool_meta(),
        description=(
            "On the first call, pass every business-trip field already supplied by the user. "
            "AgentBridge opens a prefilled trusted card; omitted fields remain editable. After "
            "field submission it validates the live OA form and creates draft confirmation."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def oa_business_trip_prepare(
        ctx: Context,
        start_time: Annotated[str | None, Field(max_length=32)] = None,
        end_time: Annotated[str | None, Field(max_length=32)] = None,
        travel_mode: Literal["大巴", "火车", "飞机", "轮渡", "自驾车"] | None = None,
        origin: Annotated[str | None, Field(max_length=255)] = None,
        destination: Annotated[str | None, Field(max_length=255)] = None,
        reason: Annotated[str | None, Field(max_length=4000)] = None,
        has_direct_supervisor: bool | None = None,
        input_submission_id: Annotated[
            str | None,
            Field(min_length=32, max_length=128),
        ] = None,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {}
        for name, value in (
            ("start_time", start_time),
            ("end_time", end_time),
            ("travel_mode", travel_mode),
            ("origin", origin),
            ("destination", destination),
            ("reason", reason),
            ("has_direct_supervisor", has_direct_supervisor),
            ("input_submission_id", input_submission_id),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            BUSINESS_TRIP_PREPARE_CAPABILITY,
            arguments,
            idempotency_key,
            {"oa:write:draft"},
        )

    @mcp.tool(
        name="oa_business_trip_save_draft",
        title="Save Authorized OA Business Trip Draft",
        meta=interaction_tool_meta(),
        description=(
            "Consume one approved trusted authorization, save the frozen plan as a "
            "wait-send OA draft, and verify it by server reload. It never submits the workflow."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def oa_business_trip_save_draft(
        ctx: Context,
        authorization_id: Annotated[str, Field(min_length=32, max_length=128)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            BUSINESS_TRIP_SAVE_CAPABILITY,
            {"authorization_id": authorization_id},
            idempotency_key,
            {"oa:write:draft"},
        )

    @mcp.tool(
        name="oa_business_trip_submit_prepare",
        title="Prepare OA Business Trip Submission",
        meta=interaction_tool_meta(),
        description=(
            "On the first call, pass every business-trip field already supplied by the user. "
            "AgentBridge opens a prefilled trusted card, validates the live OA form and sent "
            "baseline after field submission, then creates formal-submit authorization."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def oa_business_trip_submit_prepare(
        ctx: Context,
        start_time: Annotated[str | None, Field(max_length=32)] = None,
        end_time: Annotated[str | None, Field(max_length=32)] = None,
        travel_mode: Literal["大巴", "火车", "飞机", "轮渡", "自驾车"] | None = None,
        origin: Annotated[str | None, Field(max_length=255)] = None,
        destination: Annotated[str | None, Field(max_length=255)] = None,
        reason: Annotated[str | None, Field(max_length=4000)] = None,
        has_direct_supervisor: bool | None = None,
        input_submission_id: Annotated[
            str | None,
            Field(min_length=32, max_length=128),
        ] = None,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {}
        for name, value in (
            ("start_time", start_time),
            ("end_time", end_time),
            ("travel_mode", travel_mode),
            ("origin", origin),
            ("destination", destination),
            ("reason", reason),
            ("has_direct_supervisor", has_direct_supervisor),
            ("input_submission_id", input_submission_id),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            BUSINESS_TRIP_SUBMIT_PREPARE_CAPABILITY,
            arguments,
            idempotency_key,
            {"oa:write:submit"},
        )

    @mcp.tool(
        name="oa_business_trip_submit",
        title="Submit Authorized OA Business Trip Request",
        meta=interaction_tool_meta(),
        description=(
            "Consume one approved authorization, formally send the frozen business-trip "
            "request into OA approval, and verify one new sent item."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def oa_business_trip_submit(
        ctx: Context,
        authorization_id: Annotated[str, Field(min_length=32, max_length=128)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            BUSINESS_TRIP_SUBMIT_CAPABILITY,
            {"authorization_id": authorization_id},
            idempotency_key,
            {"oa:write:submit"},
        )

    @mcp.tool(
        name="oa_leave_prepare",
        title="Prepare OA Leave Draft",
        meta=interaction_tool_meta(),
        description=(
            "On the first call, pass every supported leave field already supplied by the "
            "user. AgentBridge opens a prefilled trusted card; omitted fields remain editable. "
            "After field submission it validates OA and creates draft-save confirmation."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def oa_leave_prepare(
        ctx: Context,
        leave_type: Literal["年休", "事假", "调休"] | None = None,
        start_time: Annotated[str | None, Field(max_length=32)] = None,
        end_time: Annotated[str | None, Field(max_length=32)] = None,
        reason: Annotated[str | None, Field(max_length=4000)] = None,
        has_direct_supervisor: bool | None = None,
        input_submission_id: Annotated[
            str | None,
            Field(min_length=32, max_length=128),
        ] = None,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {}
        for name, value in (
            ("leave_type", leave_type),
            ("start_time", start_time),
            ("end_time", end_time),
            ("reason", reason),
            ("has_direct_supervisor", has_direct_supervisor),
            ("input_submission_id", input_submission_id),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            LEAVE_PREPARE_CAPABILITY,
            arguments,
            idempotency_key,
            {"oa:write:draft"},
        )

    @mcp.tool(
        name="oa_leave_save_draft",
        title="Save Authorized OA Leave Draft",
        meta=interaction_tool_meta(),
        description=(
            "Consume one approved authorization, save a wait-send leave draft, and "
            "verify it by server reload. It never submits the workflow."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def oa_leave_save_draft(
        ctx: Context,
        authorization_id: Annotated[str, Field(min_length=32, max_length=128)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            LEAVE_SAVE_CAPABILITY,
            {"authorization_id": authorization_id},
            idempotency_key,
            {"oa:write:draft"},
        )

    @mcp.tool(
        name="oa_leave_submit_prepare",
        title="Prepare OA Leave Submission",
        meta=interaction_tool_meta(),
        description=(
            "On the first call, pass every supported leave field already supplied by the "
            "user. AgentBridge opens a prefilled trusted card, validates OA and the sent "
            "baseline after field submission, then creates formal-submit authorization."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def oa_leave_submit_prepare(
        ctx: Context,
        leave_type: Literal["年休", "事假", "调休"] | None = None,
        start_time: Annotated[str | None, Field(max_length=32)] = None,
        end_time: Annotated[str | None, Field(max_length=32)] = None,
        reason: Annotated[str | None, Field(max_length=4000)] = None,
        has_direct_supervisor: bool | None = None,
        input_submission_id: Annotated[
            str | None,
            Field(min_length=32, max_length=128),
        ] = None,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {}
        for name, value in (
            ("leave_type", leave_type),
            ("start_time", start_time),
            ("end_time", end_time),
            ("reason", reason),
            ("has_direct_supervisor", has_direct_supervisor),
            ("input_submission_id", input_submission_id),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            LEAVE_SUBMIT_PREPARE_CAPABILITY,
            arguments,
            idempotency_key,
            {"oa:write:submit"},
        )

    @mcp.tool(
        name="oa_leave_submit",
        title="Submit Authorized OA Leave Request",
        meta=interaction_tool_meta(),
        description=(
            "Consume one approved authorization, formally send the frozen leave request "
            "into OA approval, and verify one new readable sent item."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def oa_leave_submit(
        ctx: Context,
        authorization_id: Annotated[str, Field(min_length=32, max_length=128)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            LEAVE_SUBMIT_CAPABILITY,
            {"authorization_id": authorization_id},
            idempotency_key,
            {"oa:write:submit"},
        )

    @mcp.tool(
        name="oa_missed_punch_prepare",
        title="Prepare OA Missed-Punch Draft",
        meta=interaction_tool_meta(),
        description=(
            "On the first call, pass every missed-punch field already supplied by the user. "
            "AgentBridge opens a prefilled trusted card; omitted fields remain editable. "
            "After field submission it validates OA and creates draft-save confirmation."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def oa_missed_punch_prepare(
        ctx: Context,
        start_time: Annotated[str | None, Field(max_length=32)] = None,
        end_time: Annotated[str | None, Field(max_length=32)] = None,
        location: Annotated[str | None, Field(max_length=255)] = None,
        reason_type: Literal["忘记打卡", "人脸识别有误", "其他"] | None = None,
        explanation: Annotated[str | None, Field(max_length=4000)] = None,
        input_submission_id: Annotated[
            str | None,
            Field(min_length=32, max_length=128),
        ] = None,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {}
        for name, value in (
            ("start_time", start_time),
            ("end_time", end_time),
            ("location", location),
            ("reason_type", reason_type),
            ("explanation", explanation),
            ("input_submission_id", input_submission_id),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            MISSED_PUNCH_PREPARE_CAPABILITY,
            arguments,
            idempotency_key,
            {"oa:write:draft"},
        )

    @mcp.tool(
        name="oa_missed_punch_save_draft",
        title="Save Authorized OA Missed-Punch Draft",
        meta=interaction_tool_meta(),
        description=(
            "Consume one approved authorization, save a wait-send missed-punch draft, "
            "and verify it by server reload. It never submits the workflow."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def oa_missed_punch_save_draft(
        ctx: Context,
        authorization_id: Annotated[str, Field(min_length=32, max_length=128)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            MISSED_PUNCH_SAVE_CAPABILITY,
            {"authorization_id": authorization_id},
            idempotency_key,
            {"oa:write:draft"},
        )

    @mcp.tool(
        name="oa_missed_punch_approval_prepare",
        title="Prepare OA Missed-Punch Approval",
        meta=interaction_tool_meta(),
        description=(
            "Bind one opaque pending affair ID and pass any approval opinion already supplied "
            "by the user. AgentBridge opens a prefilled trusted card, validates the exact "
            "missed-punch target, and creates separate approval confirmation."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def oa_missed_punch_approval_prepare(
        ctx: Context,
        affair_id: Annotated[str, Field(min_length=1, max_length=256)],
        opinion: Annotated[str | None, Field(max_length=1000)] = None,
        input_submission_id: Annotated[
            str | None,
            Field(min_length=32, max_length=128),
        ] = None,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"affair_id": affair_id}
        if opinion is not None:
            arguments["opinion"] = opinion
        if input_submission_id is not None:
            arguments["input_submission_id"] = input_submission_id
        return await invoke(
            ctx,
            MISSED_PUNCH_APPROVAL_PREPARE_CAPABILITY,
            arguments,
            idempotency_key,
            {"oa:write:approval"},
        )

    @mcp.tool(
        name="oa_missed_punch_approve",
        title="Approve Authorized OA Missed-Punch Request",
        meta=interaction_tool_meta(),
        description=(
            "Consume one approved authorization, approve the frozen missed-punch item, "
            "and verify that it left the pending collection."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def oa_missed_punch_approve(
        ctx: Context,
        authorization_id: Annotated[str, Field(min_length=32, max_length=128)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            MISSED_PUNCH_APPROVE_CAPABILITY,
            {"authorization_id": authorization_id},
            idempotency_key,
            {"oa:write:approval"},
        )

    @mcp.tool(
        name="oa_meeting_create_prepare",
        title="Prepare OA Meeting Creation",
        meta=interaction_tool_meta(),
        description=(
            "On the first call, pass any subject, requested room wording, start_time, and "
            "end_time already supplied by the user. AgentBridge checks live OA room "
            "availability before opening a prefilled card with real room options. After "
            "field submission it rechecks availability and creates a separate confirmation."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def oa_meeting_create_prepare(
        ctx: Context,
        subject: Annotated[str | None, Field(max_length=255)] = None,
        room: Annotated[str | None, Field(max_length=100)] = None,
        start_time: Annotated[str | None, Field(max_length=32)] = None,
        end_time: Annotated[str | None, Field(max_length=32)] = None,
        input_submission_id: Annotated[
            str | None,
            Field(min_length=32, max_length=128),
        ] = None,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {}
        for name, value in (
            ("subject", subject),
            ("room", room),
            ("start_time", start_time),
            ("end_time", end_time),
            ("input_submission_id", input_submission_id),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            MEETING_PREPARE_CAPABILITY,
            arguments,
            idempotency_key,
            {"oa:write:meeting"},
        )

    @mcp.tool(
        name="oa_meeting_create",
        title="Create Authorized OA Meeting",
        meta=interaction_tool_meta(),
        description=(
            "Consume one approved authorization, recheck room availability, create and "
            "send the meeting, then verify room-list and meeting-view readback."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def oa_meeting_create(
        ctx: Context,
        authorization_id: Annotated[str, Field(min_length=32, max_length=128)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            MEETING_CREATE_CAPABILITY,
            {"authorization_id": authorization_id},
            idempotency_key,
            {"oa:write:meeting"},
        )

    @mcp.tool(
        name="yuque_public_books_list",
        title="List Yuque Public Knowledge Bases",
        description="List knowledge bases visible in the authenticated department public area.",
        annotations=read_annotations,
        structured_output=True,
    )
    async def yuque_public_books_list(
        ctx: Context,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            YUQUE_PUBLIC_BOOKS_CAPABILITY,
            {},
            idempotency_key,
            {"yuque:read"},
        )

    @mcp.tool(
        name="yuque_document_catalog",
        title="List Yuque Documents",
        description=(
            "List, filter, sort, and page document metadata across all visible "
            "department knowledge bases, or restrict the result to one book."
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def yuque_document_catalog(
        ctx: Context,
        book: Annotated[str | None, Field(max_length=200)] = None,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        document_type: Annotated[str | None, Field(max_length=20)] = None,
        updated_after: Annotated[str | None, Field(max_length=40)] = None,
        updated_before: Annotated[str | None, Field(max_length=40)] = None,
        sort: Annotated[str, Field(max_length=20)] = "updated_desc",
        page: Annotated[int, Field(ge=1, le=1000)] = 1,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "sort": sort,
            "page": page,
            "limit": limit,
        }
        for name, value in (
            ("book", book),
            ("keyword", keyword),
            ("document_type", document_type),
            ("updated_after", updated_after),
            ("updated_before", updated_before),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            YUQUE_DOCUMENT_CATALOG_CAPABILITY,
            arguments,
            idempotency_key,
            {"yuque:read"},
        )

    @mcp.tool(
        name="yuque_document_search",
        title="Search Yuque Documents",
        description=(
            "Search every visible department knowledge base unless one book is "
            "specified. Search snippets are deliberately omitted so credentials in "
            "incidental matches do not enter agent context."
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def yuque_document_search(
        ctx: Context,
        query: Annotated[str, Field(min_length=1, max_length=500)],
        book: Annotated[str | None, Field(max_length=200)] = None,
        document_type: Annotated[str | None, Field(max_length=20)] = None,
        page: Annotated[int, Field(ge=1, le=1000)] = 1,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"query": query, "page": page, "limit": limit}
        if book is not None:
            arguments["book"] = book
        if document_type is not None:
            arguments["document_type"] = document_type
        return await invoke(
            ctx,
            YUQUE_DOCUMENT_SEARCH_CAPABILITY,
            arguments,
            idempotency_key,
            {"yuque:read"},
        )

    @mcp.tool(
        name="yuque_document_read",
        title="Read One Yuque Document",
        description=(
            "Read one explicitly selected Yuque Doc, Sheet, or Table as structured "
            "text. Headings, tables, image OCR, links, and attachment metadata are "
            "preserved; likely passwords, tokens, API keys, URL credentials, and "
            "private keys are always redacted. Omit book to resolve a unique document "
            "across all visible knowledge bases."
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def yuque_document_read(
        ctx: Context,
        document: Annotated[str, Field(min_length=1, max_length=500)],
        book: Annotated[str | None, Field(max_length=200)] = None,
        row_offset: Annotated[int, Field(ge=0, le=100000)] = 0,
        max_rows: Annotated[int, Field(ge=1, le=500)] = 100,
        max_chars: Annotated[int, Field(ge=500, le=50000)] = 12000,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "document": document,
            "row_offset": row_offset,
            "max_rows": max_rows,
            "max_chars": max_chars,
        }
        if book is not None:
            arguments["book"] = book
        return await invoke(
            ctx,
            YUQUE_DOCUMENT_READ_CAPABILITY,
            arguments,
            idempotency_key,
            {"yuque:read"},
        )
    @mcp.tool(
        name="yuque_session_status",
        title="Verify Yuque Session Status",
        description="Verify the caller's isolated central Yuque browser session.",
        annotations=read_annotations,
        structured_output=True,
    )
    async def yuque_session_status() -> dict[str, Any]:
        identity = _request_identity(identity_store, required_scopes={"yuque:read"})
        return await asyncio.to_thread(
            service.session_status,
            user_subject=identity["user_subject"],
            system_id="yuque",
        )

    @mcp.tool(
        name="yuque_session_login",
        title="Ensure Yuque Session Login",
        meta=interaction_tool_meta(),
        description=(
            "Reuse a valid Yuque session or open a trusted interactive browser card. "
            "Slider and SMS verification stay outside the model context."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def yuque_session_login(
        ctx: Context,
        challenge_ttl_seconds: Annotated[int, Field(ge=30, le=900)] = 900,
    ) -> dict[str, Any]:
        identity = _request_identity(identity_store, required_scopes={"yuque:read"})
        response = await asyncio.to_thread(
            service.start_login,
            user_subject=identity["user_subject"],
            expected_principal_ref=None,
            card_base_url=auth_card_base_url,
            ttl_seconds=challenge_ttl_seconds,
            system_id="yuque",
        )
        return package_interaction_result(response)
    @mcp.tool(
        name="taihua_work_log_my_list",
        title="List My Taihua Work Logs",
        description="List the authenticated user's Taihua work logs in one date range.",
        annotations=read_annotations,
        structured_output=True,
    )
    async def taihua_work_log_my_list(
        ctx: Context,
        start_date: Annotated[str | None, Field(max_length=10)] = None,
        end_date: Annotated[str | None, Field(max_length=10)] = None,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"limit": limit}
        for name, value in (
            ("start_date", start_date),
            ("end_date", end_date),
            ("keyword", keyword),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            TAIHUA_MY_LOGS_CAPABILITY,
            arguments,
            idempotency_key,
            {"taihua:read"},
        )

    @mcp.tool(
        name="taihua_work_log_team_list",
        title="List Taihua Team Work Logs",
        description=(
            "List team work logs within the authenticated Taihua user's data scope. "
            "Filter by member, department, watch group, keyword, one log date, or a "
            "closed date range. Date filters automatically use logDate view mode."
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def taihua_work_log_team_list(
        ctx: Context,
        keyword: Annotated[
            str | None,
            Field(max_length=200, description="Free-text log keyword, not a member name."),
        ] = None,
        log_date: Annotated[
            str | None,
            Field(max_length=10, description="One log date in YYYY-MM-DD format."),
        ] = None,
        start_date: Annotated[
            str | None,
            Field(max_length=10, description="Range start in YYYY-MM-DD format."),
        ] = None,
        end_date: Annotated[
            str | None,
            Field(max_length=10, description="Range end in YYYY-MM-DD format."),
        ] = None,
        member: Annotated[
            str | None,
            Field(max_length=200, description="Exact member full name or username."),
        ] = None,
        department: Annotated[
            str | None,
            Field(max_length=200, description="Exact department name."),
        ] = None,
        watch_group: Annotated[
            str | None,
            Field(max_length=200, description="Exact watch-group name."),
        ] = None,
        page: Annotated[int, Field(ge=1, le=10000)] = 1,
        size: Annotated[int, Field(ge=1, le=100)] = 20,
        view_mode: Literal["submittedAt", "logDate"] | None = None,
        dept_id: Annotated[int | None, Field(ge=1)] = None,
        member_id: Annotated[int | None, Field(ge=1)] = None,
        watch_group_id: Annotated[int | None, Field(ge=1)] = None,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"page": page, "size": size}
        for name, value in (
            ("keyword", keyword),
            ("log_date", log_date),
            ("start_date", start_date),
            ("end_date", end_date),
            ("member", member),
            ("department", department),
            ("watch_group", watch_group),
            ("view_mode", view_mode),
            ("dept_id", dept_id),
            ("member_id", member_id),
            ("watch_group_id", watch_group_id),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            TAIHUA_TEAM_LOGS_CAPABILITY,
            arguments,
            idempotency_key,
            {"taihua:read"},
        )

    @mcp.tool(
        name="taihua_project_search",
        title="Search Taihua Projects",
        description="Search projects available to the authenticated Taihua user.",
        annotations=read_annotations,
        structured_output=True,
    )
    async def taihua_project_search(
        ctx: Context,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"limit": limit}
        if keyword is not None:
            arguments["keyword"] = keyword
        return await invoke(
            ctx,
            TAIHUA_PROJECT_SEARCH_CAPABILITY,
            arguments,
            idempotency_key,
            {"taihua:read"},
        )

    @mcp.tool(
        name="taihua_work_log_create_prepare",
        title="Prepare Taihua Work Log",
        meta=interaction_tool_meta(),
        description=(
            "Open a prefilled trusted field card, validate the exact work-log fields, "
            "and freeze a submission plan. This tool does not write to Taihua."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def taihua_work_log_create_prepare(
        ctx: Context,
        log_date: Annotated[str | None, Field(max_length=10)] = None,
        hours: Annotated[float | None, Field(ge=0.5, le=16)] = None,
        project: Annotated[str | None, Field(max_length=255)] = None,
        content: Annotated[str | None, Field(max_length=4000)] = None,
        input_submission_id: Annotated[
            str | None,
            Field(min_length=32, max_length=128),
        ] = None,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {}
        for name, value in (
            ("log_date", log_date),
            ("hours", hours),
            ("project", project),
            ("content", content),
            ("input_submission_id", input_submission_id),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            TAIHUA_WORK_LOG_CREATE_PREPARE_CAPABILITY,
            arguments,
            idempotency_key,
            {"taihua:write:worklog"},
        )

    @mcp.tool(
        name="taihua_work_log_create",
        title="Create Authorized Taihua Work Log",
        meta=interaction_tool_meta(),
        description=(
            "Consume one approved authorization, create the exact Taihua work log, "
            "and verify it by authoritative readback."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def taihua_work_log_create(
        ctx: Context,
        authorization_id: Annotated[str, Field(min_length=32, max_length=128)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            TAIHUA_WORK_LOG_CREATE_CAPABILITY,
            {"authorization_id": authorization_id},
            idempotency_key,
            {"taihua:write:worklog"},
        )

    @mcp.tool(
        name="taihua_session_status",
        title="Verify Taihua Session Status",
        description="Verify the authenticated caller's central Taihua token session.",
        annotations=read_annotations,
        structured_output=True,
    )
    async def taihua_session_status() -> dict[str, Any]:
        identity = _request_identity(
            identity_store,
            required_scopes={"taihua:read"},
        )
        return await asyncio.to_thread(
            service.session_status,
            user_subject=identity["user_subject"],
            system_id="taihua",
        )

    @mcp.tool(
        name="taihua_session_login",
        title="Ensure Taihua Session Login",
        meta=interaction_tool_meta(),
        description=(
            "Reuse or refresh a valid Taihua token session. When login is required, "
            "create a trusted credential card; credentials never enter MCP arguments."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def taihua_session_login(
        ctx: Context,
        challenge_ttl_seconds: Annotated[int, Field(ge=30, le=900)] = 300,
    ) -> dict[str, Any]:
        identity = _request_identity(
            identity_store,
            required_scopes={"taihua:read"},
        )
        response = await asyncio.to_thread(
            service.start_login,
            user_subject=identity["user_subject"],
            expected_principal_ref=None,
            card_base_url=auth_card_base_url,
            ttl_seconds=challenge_ttl_seconds,
            system_id="taihua",
        )
        return package_interaction_result(response)

    @mcp.tool(
        name="smartlight_system_overview",
        title="照明系统概览",
        description="汇总可见控制柜，并分别返回可检索灯杆数和地图明细灯杆数。",
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_system_overview(
        ctx: Context,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            SMARTLIGHT_OVERVIEW_CAPABILITY,
            {},
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_runtime_overview",
        title="查看照明实时运行概览",
        description=(
            "读取当前 RTU、单灯控制器和灯具的运行快照。回答在线、离线、停电、"
            "开灯等当前状态时使用本工具；登记资产数量请使用 smartlight_system_overview "
            "或 smartlight_asset_search，不得混用两个统计口径。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_runtime_overview(
        ctx: Context,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            SMARTLIGHT_RUNTIME_OVERVIEW_CAPABILITY,
            {},
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_rtu_status_list",
        title="查询 RTU 运行状态",
        description=(
            "查询照明用途 RTU 的当前运行状态，可筛选在线、离线、电源停电、未启用"
            "和当前有告警的设备。结果属于运行页面口径，不代表全部登记资产。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_rtu_status_list(
        ctx: Context,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        state: Annotated[
            Literal["all", "online", "offline", "power_off", "disabled"], Field()
        ] = "all",
        alarm_only: Annotated[bool, Field()] = False,
        work_area: Annotated[str | None, Field(max_length=200)] = None,
        group: Annotated[str | None, Field(max_length=200)] = None,
        model: Annotated[str | None, Field(max_length=200)] = None,
        page: Annotated[int, Field(ge=1, le=10000)] = 1,
        size: Annotated[int, Field(ge=1, le=100)] = 20,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "state": state,
            "alarm_only": alarm_only,
            "page": page,
            "size": size,
        }
        for name, value in (
            ("keyword", keyword),
            ("work_area", work_area),
            ("group", group),
            ("model", model),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            SMARTLIGHT_RTU_STATUS_LIST_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_lamp_status_list",
        title="查询单灯运行状态",
        description=(
            "查询单灯控制器在线状态、灯具开关和响应中真实存在的电压、电流、功率"
            "等数据。abnormal 表示响应中带单灯告警，不等同于漏电。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_lamp_status_list(
        ctx: Context,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        controller_state: Annotated[
            Literal["all", "online", "offline"], Field()
        ] = "all",
        lamp_state: Annotated[
            Literal["all", "on", "off", "abnormal"], Field()
        ] = "all",
        alarm_only: Annotated[bool, Field()] = False,
        street: Annotated[str | None, Field(max_length=200)] = None,
        cabinet: Annotated[str | None, Field(max_length=200)] = None,
        work_area: Annotated[str | None, Field(max_length=200)] = None,
        page: Annotated[int, Field(ge=1, le=10000)] = 1,
        size: Annotated[int, Field(ge=1, le=100)] = 20,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "controller_state": controller_state,
            "lamp_state": lamp_state,
            "alarm_only": alarm_only,
            "page": page,
            "size": size,
        }
        for name, value in (
            ("keyword", keyword),
            ("street", street),
            ("cabinet", cabinet),
            ("work_area", work_area),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            SMARTLIGHT_LAMP_STATUS_LIST_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_lamp_alarm_list",
        title="查询单灯告警",
        description=(
            "分页查询单灯告警，例如异常亮灯。相对时间直接传 last_days。该数据源不是"
            "漏电专表；查询真正的漏电报警请用 smartlight_alarm_list/analysis 并筛选"
            "告警类型，查询漏电电流请用 smartlight_rtu_survey_records。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_lamp_alarm_list(
        ctx: Context,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        alarm_type: Annotated[str | None, Field(max_length=200)] = None,
        alarm_state: Annotated[
            Literal["all", "current", "non_current"], Field()
        ] = "all",
        road: Annotated[str | None, Field(max_length=200)] = None,
        work_area: Annotated[str | None, Field(max_length=200)] = None,
        cabinet: Annotated[str | None, Field(max_length=200)] = None,
        start_date: Annotated[str | None, Field(max_length=10)] = None,
        end_date: Annotated[str | None, Field(max_length=10)] = None,
        last_days: Annotated[int | None, Field(ge=1, le=3660)] = None,
        page: Annotated[int, Field(ge=1, le=10000)] = 1,
        size: Annotated[int, Field(ge=1, le=100)] = 20,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "alarm_state": alarm_state,
            "page": page,
            "size": size,
        }
        for name, value in (
            ("keyword", keyword),
            ("alarm_type", alarm_type),
            ("road", road),
            ("work_area", work_area),
            ("cabinet", cabinet),
            ("start_date", start_date),
            ("end_date", end_date),
            ("last_days", last_days),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            SMARTLIGHT_LAMP_ALARM_LIST_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_lamp_alarm_analysis",
        title="分析单灯告警",
        description=(
            "对最多 500 条单灯告警做日期趋势及告警类型、灯杆、道路、状态排行。"
            "未传日期时默认最近 30 天；结果始终标记 alarmSource=single_lamp。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_lamp_alarm_analysis(
        ctx: Context,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        alarm_type: Annotated[str | None, Field(max_length=200)] = None,
        alarm_state: Annotated[
            Literal["all", "current", "non_current"], Field()
        ] = "all",
        road: Annotated[str | None, Field(max_length=200)] = None,
        work_area: Annotated[str | None, Field(max_length=200)] = None,
        cabinet: Annotated[str | None, Field(max_length=200)] = None,
        start_date: Annotated[str | None, Field(max_length=10)] = None,
        end_date: Annotated[str | None, Field(max_length=10)] = None,
        last_days: Annotated[int | None, Field(ge=1, le=3660)] = None,
        top_n: Annotated[int, Field(ge=1, le=20)] = 10,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"alarm_state": alarm_state, "top_n": top_n}
        for name, value in (
            ("keyword", keyword),
            ("alarm_type", alarm_type),
            ("road", road),
            ("work_area", work_area),
            ("cabinet", cabinet),
            ("start_date", start_date),
            ("end_date", end_date),
            ("last_days", last_days),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            SMARTLIGHT_LAMP_ALARM_ANALYSIS_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_rtu_survey_records",
        title="查询 RTU 巡测记录",
        description=(
            "读取指定 RTU 的电压、电流、温湿度、开关量和漏电电流历史。优先传精确"
            "rtu_id；只传 rtu_keyword 时必须唯一命中。默认最近 24 小时，最长 7 天。"
            "巡测记录是设备遥测历史，不是巡检任务。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_rtu_survey_records(
        ctx: Context,
        rtu_id: Annotated[str | None, Field(max_length=200)] = None,
        rtu_keyword: Annotated[str | None, Field(max_length=200)] = None,
        start_time: Annotated[str | None, Field(max_length=19)] = None,
        end_time: Annotated[str | None, Field(max_length=19)] = None,
        abnormal_only: Annotated[bool, Field()] = False,
        page: Annotated[int, Field(ge=1, le=10000)] = 1,
        size: Annotated[int, Field(ge=1, le=100)] = 20,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "abnormal_only": abnormal_only,
            "page": page,
            "size": size,
        }
        for name, value in (
            ("rtu_id", rtu_id),
            ("rtu_keyword", rtu_keyword),
            ("start_time", start_time),
            ("end_time", end_time),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            SMARTLIGHT_RTU_SURVEY_RECORDS_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_energy_record_list",
        title="查询照明 RTU 用电记录",
        description=(
            "读取用电量记录页面按日返回的 RTU 用电显示值。默认最近 7 天，最长 92 天；"
            "下游未返回单位时会明确提示，不猜测单位，也不通过累计读数差分造数。"
            "连续追问沿用已有时间、设备和筛选条件；空结果不得自行扩大范围。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_energy_record_list(
        ctx: Context,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        device_id: Annotated[str | None, Field(max_length=200)] = None,
        start_date: Annotated[str | None, Field(max_length=10)] = None,
        end_date: Annotated[str | None, Field(max_length=10)] = None,
        last_days: Annotated[int | None, Field(ge=1, le=92)] = None,
        page: Annotated[int, Field(ge=1, le=10000)] = 1,
        size: Annotated[int, Field(ge=1, le=100)] = 20,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"page": page, "size": size}
        for name, value in (
            ("keyword", keyword),
            ("device_id", device_id),
            ("start_date", start_date),
            ("end_date", end_date),
            ("last_days", last_days),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            SMARTLIGHT_ENERGY_RECORD_LIST_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_energy_analysis",
        title="分析照明 RTU 用电",
        description=(
            "分析用电量页面按日直接返回的显示值，给出趋势和 RTU 排行。默认最近 30 天，"
            "最长 366 天，最多分析 500 台 RTU；不把缺失值或单位不明的数据强行汇总。"
            "连续追问沿用已有时间、设备和筛选条件；空结果不得自行扩大范围。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_energy_analysis(
        ctx: Context,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        device_id: Annotated[str | None, Field(max_length=200)] = None,
        start_date: Annotated[str | None, Field(max_length=10)] = None,
        end_date: Annotated[str | None, Field(max_length=10)] = None,
        last_days: Annotated[int | None, Field(ge=1, le=366)] = None,
        top_n: Annotated[int, Field(ge=1, le=20)] = 10,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"top_n": top_n}
        for name, value in (
            ("keyword", keyword),
            ("device_id", device_id),
            ("start_date", start_date),
            ("end_date", end_date),
            ("last_days", last_days),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            SMARTLIGHT_ENERGY_ANALYSIS_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_lamp_survey_records",
        title="查询单灯巡测记录",
        description=(
            "查询灯杆和单灯控制器的历史遥测，默认最近 24 小时，最长 7 天。"
            "这是设备巡测数据，不是人员巡检任务或巡检日志。连续追问沿用已有时间、"
            "设备和筛选条件；上一结果为空时直接说明没有可展开记录，不得自行扩大范围。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_lamp_survey_records(
        ctx: Context,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        lamp_post_id: Annotated[str | None, Field(max_length=200)] = None,
        start_time: Annotated[str | None, Field(max_length=19)] = None,
        end_time: Annotated[str | None, Field(max_length=19)] = None,
        page: Annotated[int, Field(ge=1, le=10000)] = 1,
        size: Annotated[int, Field(ge=1, le=100)] = 20,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"page": page, "size": size}
        for name, value in (
            ("keyword", keyword),
            ("lamp_post_id", lamp_post_id),
            ("start_time", start_time),
            ("end_time", end_time),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            SMARTLIGHT_LAMP_SURVEY_RECORDS_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_rtu_leakage_alarm_list",
        title="查询 RTU 支路漏电报警",
        description=(
            "读取漏电管理页面的真实 RTU 支路漏电报警，默认最近 30 天，最长 366 天。"
            "普通自然语言“漏电”应优先选择本工具，不得选择旧的单灯告警兼容入口。"
            "连续追问沿用已有时间、设备和筛选条件；空结果不得自行扩大范围。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_rtu_leakage_alarm_list(
        ctx: Context,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        alarm_state: Annotated[Literal["all", "current", "cleared"], Field()] = "all",
        start_date: Annotated[str | None, Field(max_length=10)] = None,
        end_date: Annotated[str | None, Field(max_length=10)] = None,
        last_days: Annotated[int | None, Field(ge=1, le=366)] = None,
        page: Annotated[int, Field(ge=1, le=10000)] = 1,
        size: Annotated[int, Field(ge=1, le=100)] = 20,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "alarm_state": alarm_state,
            "page": page,
            "size": size,
        }
        for name, value in (
            ("keyword", keyword),
            ("start_date", start_date),
            ("end_date", end_date),
            ("last_days", last_days),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            SMARTLIGHT_RTU_LEAKAGE_ALARM_LIST_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_rtu_leakage_analysis",
        title="分析 RTU 支路漏电报警",
        description=(
            "在最多 500 条真实 RTU 支路漏电记录上分析日期趋势、RTU、控制箱和支路排行。"
            "未返回电流单位或阈值时会保留为空，不自行推断。连续追问沿用已有时间、"
            "设备和筛选条件；空结果不得自行扩大范围。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_rtu_leakage_analysis(
        ctx: Context,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        alarm_state: Annotated[Literal["all", "current", "cleared"], Field()] = "all",
        start_date: Annotated[str | None, Field(max_length=10)] = None,
        end_date: Annotated[str | None, Field(max_length=10)] = None,
        last_days: Annotated[int | None, Field(ge=1, le=366)] = None,
        top_n: Annotated[int, Field(ge=1, le=20)] = 10,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"alarm_state": alarm_state, "top_n": top_n}
        for name, value in (
            ("keyword", keyword),
            ("start_date", start_date),
            ("end_date", end_date),
            ("last_days", last_days),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            SMARTLIGHT_RTU_LEAKAGE_ANALYSIS_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_off_hours_current_list",
        title="查询关灯时段电流",
        description=(
            "查询关灯时段电流页面记录，默认最近 24 小时，最长 7 天。"
            "有电流记录不自动等同于漏电、偷电或设备故障。连续追问沿用已有时间、"
            "设备和筛选条件；空结果不得自行扩大范围。答复必须区分实际查询范围和"
            "currentPolicyWindow 当前开关灯策略快照；策略时间缺失或相同则说明无法据此"
            "确定有效关灯时段。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_off_hours_current_list(
        ctx: Context,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        start_time: Annotated[str | None, Field(max_length=19)] = None,
        end_time: Annotated[str | None, Field(max_length=19)] = None,
        page: Annotated[int, Field(ge=1, le=10000)] = 1,
        size: Annotated[int, Field(ge=1, le=100)] = 20,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"page": page, "size": size}
        for name, value in (
            ("keyword", keyword),
            ("start_time", start_time),
            ("end_time", end_time),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            SMARTLIGHT_OFF_HOURS_CURRENT_LIST_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_inspection_log_list",
        title="查询照明巡检日志统计",
        description=(
            "查询巡检日志页面的巡检组统计，默认最近 30 天，最长 366 天。"
            "该页面不返回逐人、逐设备打卡明细，因此不得把统计行描述成具体巡检事件。"
            "连续追问沿用已有时间、巡检组和筛选条件；空结果不得自行扩大范围。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_inspection_log_list(
        ctx: Context,
        plan_id: Annotated[str | None, Field(max_length=200)] = None,
        group: Annotated[str | None, Field(max_length=200)] = None,
        start_date: Annotated[str | None, Field(max_length=10)] = None,
        end_date: Annotated[str | None, Field(max_length=10)] = None,
        last_days: Annotated[int | None, Field(ge=1, le=366)] = None,
        page: Annotated[int, Field(ge=1, le=10000)] = 1,
        size: Annotated[int, Field(ge=1, le=100)] = 20,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"page": page, "size": size}
        for name, value in (
            ("plan_id", plan_id),
            ("group", group),
            ("start_date", start_date),
            ("end_date", end_date),
            ("last_days", last_days),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            SMARTLIGHT_INSPECTION_LOG_LIST_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_maintenance_record_list",
        title="查询照明检修记录",
        description=(
            "分页查询 RTU 和灯杆检修记录，可按设备编号、检修人员和设备类型筛选。"
            "默认最近 30 天，最长 366 天；本期不开放缺少稳定记录 ID 的统一详情。"
            "连续追问沿用已有时间、设备和筛选条件；空结果不得自行扩大范围。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_maintenance_record_list(
        ctx: Context,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        overhaul_user: Annotated[str | None, Field(max_length=200)] = None,
        device_type: Annotated[Literal["all", "rtu", "lamppost"], Field()] = "all",
        start_date: Annotated[str | None, Field(max_length=10)] = None,
        end_date: Annotated[str | None, Field(max_length=10)] = None,
        last_days: Annotated[int | None, Field(ge=1, le=366)] = None,
        page: Annotated[int, Field(ge=1, le=10000)] = 1,
        size: Annotated[int, Field(ge=1, le=100)] = 20,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "device_type": device_type,
            "page": page,
            "size": size,
        }
        for name, value in (
            ("keyword", keyword),
            ("overhaul_user", overhaul_user),
            ("start_date", start_date),
            ("end_date", end_date),
            ("last_days", last_days),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            SMARTLIGHT_MAINTENANCE_RECORD_LIST_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_lamppost_list",
        title="查询照明灯杆",
        description="List lamp posts visible to the authenticated lighting-system user.",
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_lamppost_list(
        ctx: Context,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        page: Annotated[int, Field(ge=1, le=10000)] = 1,
        size: Annotated[int, Field(ge=1, le=100)] = 20,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"page": page, "size": size}
        if keyword is not None:
            arguments["keyword"] = keyword
        return await invoke(
            ctx,
            SMARTLIGHT_LAMPPOST_LIST_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_alarm_list",
        title="查询照明 RTU 告警",
        description=(
            "按关键词查询 RTU 告警。严格规则：用户只说“最近/最新告警”“最近/最新"
            "一条”时必须使用 sort_by=occurred_at，按首次发生时间全局倒序；只有用户"
            "明确说“最近活动/变化/更新/处理”时才使用 sort_by=last_activity，按最近"
            "活动时间全局倒序，不得把普通“最近”自行解释为最近活动。latestGroup 会"
            "说明最新时间点是否存在并列告警；写操作不得仅靠"
            "并列记录的稳定顺序自动选目标。列表同时返回告警权重、所属工区和工区"
            "提交状态；汇总值是当前系统快照。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_alarm_list(
        ctx: Context,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        sort_by: Annotated[
            Literal["occurred_at", "last_activity"],
            Field(
                description=(
                    "普通“最近/最新告警”必须使用 occurred_at；仅在用户明确要求"
                    "最近活动、变化、更新或处理时间时使用 last_activity。"
                )
            ),
        ] = "occurred_at",
        page: Annotated[int, Field(ge=1, le=10000)] = 1,
        size: Annotated[int, Field(ge=1, le=100)] = 20,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "sort_by": sort_by,
            "page": page,
            "size": size,
        }
        if keyword is not None:
            arguments["keyword"] = keyword
        return await invoke(
            ctx,
            SMARTLIGHT_ALARM_LIST_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_alarm_remark_get",
        title="读取 RTU 告警备注",
        description=(
            "按精确 alarm_id 只读获取 RTU 告警的当前权威备注。不会打开字段卡或"
            "授权卡，也不会修改照明系统。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_alarm_remark_get(
        ctx: Context,
        alarm_id: Annotated[str, Field(min_length=1, max_length=200)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            SMARTLIGHT_ALARM_REMARK_GET_CAPABILITY,
            {"alarm_id": alarm_id},
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_inspection_task_list",
        title="查询照明巡检任务",
        description=(
            "查询巡检任务，可按任务名、计划名或状态筛选。状态码 1 表示待执行，"
            "2 表示执行中。progress 是下游系统独立给出的进度，必须原样展示；"
            "confirmedDeviceCount、lampPostCount 和 rtuCount 是独立设备计数，"
            "不得自行组合为完成数/总数或推导完成率。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_inspection_task_list(
        ctx: Context,
        task_name: Annotated[str | None, Field(max_length=200)] = None,
        plan_name: Annotated[str | None, Field(max_length=200)] = None,
        state: Annotated[int | str | None, Field()] = None,
        page: Annotated[int, Field(ge=1, le=10000)] = 1,
        size: Annotated[int, Field(ge=1, le=100)] = 20,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"page": page, "size": size}
        for name, value in (
            ("task_name", task_name),
            ("plan_name", plan_name),
            ("state", state),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            SMARTLIGHT_INSPECTION_TASK_LIST_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_leakage_summary",
        title="兼容旧入口：查询单灯告警",
        description=(
            "已弃用的兼容入口，实际数据源是单灯告警，并非漏电专表。新请求必须"
            "使用 smartlight_lamp_alarm_list；自然语言“漏电”不得选择本工具。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_leakage_summary(
        ctx: Context,
        start_date: Annotated[str | None, Field(max_length=10)] = None,
        end_date: Annotated[str | None, Field(max_length=10)] = None,
        last_days: Annotated[int | None, Field(ge=1, le=3660)] = None,
        page: Annotated[int, Field(ge=1, le=10000)] = 1,
        size: Annotated[int, Field(ge=1, le=100)] = 20,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"page": page, "size": size}
        if start_date is not None:
            arguments["start_date"] = start_date
        if end_date is not None:
            arguments["end_date"] = end_date
        if last_days is not None:
            arguments["last_days"] = last_days
        return await invoke(
            ctx,
            SMARTLIGHT_LEAKAGE_SUMMARY_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_asset_search",
        title="查询照明设施",
        description=(
            "统一查询控制柜、RTU 或灯杆。asset_type 使用 cabinet、rtu 或 "
            "lamppost；读取详情前先用本工具取得精确 asset_id。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_asset_search(
        ctx: Context,
        asset_type: Annotated[Literal["cabinet", "rtu", "lamppost"], Field()],
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        page: Annotated[int, Field(ge=1, le=10000)] = 1,
        size: Annotated[int, Field(ge=1, le=100)] = 20,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "asset_type": asset_type,
            "page": page,
            "size": size,
        }
        if keyword is not None:
            arguments["keyword"] = keyword
        return await invoke(
            ctx,
            SMARTLIGHT_ASSET_SEARCH_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_asset_detail",
        title="读取照明设施详情",
        description=(
            "按精确 asset_id 读取控制柜、RTU 或灯杆详情。asset_id 应来自 "
            "smartlight_asset_search；RTU 详情同时返回继电器和回路结构。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_asset_detail(
        ctx: Context,
        asset_type: Annotated[Literal["cabinet", "rtu", "lamppost"], Field()],
        asset_id: Annotated[str, Field(min_length=1, max_length=200)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            SMARTLIGHT_ASSET_DETAIL_CAPABILITY,
            {"asset_type": asset_type, "asset_id": asset_id},
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_alarm_analysis",
        title="分析照明 RTU 告警",
        description=(
            "分析指定日期或最近 N 天的 RTU 告警，返回状态、类型、设备排行和"
            "日期趋势。未传日期时默认最近 30 天；最多分析 500 条，truncated "
            "表示结果只是有界样本。time_field 可选 last_activity 或 occurred。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_alarm_analysis(
        ctx: Context,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        alarm_type: Annotated[str | None, Field(max_length=200)] = None,
        alarm_state: Annotated[
            Literal["all", "current", "cleared"], Field()
        ] = "all",
        time_field: Annotated[
            Literal["last_activity", "occurred"], Field()
        ] = "last_activity",
        start_date: Annotated[str | None, Field(max_length=10)] = None,
        end_date: Annotated[str | None, Field(max_length=10)] = None,
        last_days: Annotated[int | None, Field(ge=1, le=3660)] = None,
        top_n: Annotated[int, Field(ge=1, le=20)] = 10,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "alarm_state": alarm_state,
            "time_field": time_field,
            "top_n": top_n,
        }
        for name, value in (
            ("keyword", keyword),
            ("alarm_type", alarm_type),
            ("start_date", start_date),
            ("end_date", end_date),
            ("last_days", last_days),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            SMARTLIGHT_ALARM_ANALYSIS_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_inspection_task_detail",
        title="读取照明巡检任务详情",
        description=(
            "读取指定巡检任务的每日计划数、完成数和下游完成率；传 detail_date "
            "时再读取该日实际打卡记录。系统未提供未巡设备明细，不得根据数量差"
            "推断具体未巡设备。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_inspection_task_detail(
        ctx: Context,
        task_id: Annotated[str, Field(min_length=1, max_length=200)],
        start_date: Annotated[str | None, Field(max_length=10)] = None,
        end_date: Annotated[str | None, Field(max_length=10)] = None,
        detail_date: Annotated[str | None, Field(max_length=10)] = None,
        clockin_user: Annotated[str | None, Field(max_length=200)] = None,
        has_issues: Annotated[bool | None, Field()] = None,
        page: Annotated[int, Field(ge=1, le=10000)] = 1,
        size: Annotated[int, Field(ge=1, le=100)] = 20,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "task_id": task_id,
            "page": page,
            "size": size,
        }
        for name, value in (
            ("start_date", start_date),
            ("end_date", end_date),
            ("detail_date", detail_date),
            ("clockin_user", clockin_user),
            ("has_issues", has_issues),
        ):
            if value is not None:
                arguments[name] = value
        return await invoke(
            ctx,
            SMARTLIGHT_INSPECTION_TASK_DETAIL_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_leakage_analysis",
        title="兼容旧入口：分析单灯告警",
        description=(
            "已弃用的兼容入口，实际分析单灯告警。新请求必须使用 "
            "smartlight_lamp_alarm_analysis；自然语言“漏电”不得选择本工具。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_leakage_analysis(
        ctx: Context,
        start_date: Annotated[str | None, Field(max_length=10)] = None,
        end_date: Annotated[str | None, Field(max_length=10)] = None,
        last_days: Annotated[int | None, Field(ge=1, le=3660)] = None,
        top_n: Annotated[int, Field(ge=1, le=20)] = 10,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"top_n": top_n}
        if start_date is not None:
            arguments["start_date"] = start_date
        if end_date is not None:
            arguments["end_date"] = end_date
        if last_days is not None:
            arguments["last_days"] = last_days
        return await invoke(
            ctx,
            SMARTLIGHT_LEAKAGE_ANALYSIS_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_report_export",
        title="导出照明系统 CSV 报告",
        description=(
            "将照明系统只读数据导出为 UTF-8 CSV 附件。report_type 可选 "
            "告警、资产、巡检，以及四期新增的用电、单灯巡测、RTU 支路漏电、"
            "巡检日志统计和检修记录。设施清单必须传 asset_type；巡检报告必须传 "
            "task_id，传 detail_date 时导出当天打卡明细，否则导出每日进度。"
            "leakage_analysis 仅是单灯告警报告的旧兼容别名，不得用于自然语言"
            "漏电请求。单份最多 500 行，结果会明确标注截断。OpenClaw 会直接发送附件，"
            "不要重复输出 mediaUrl；Workspace 历史卡过期后可按原条件重新生成当前数据。"
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_report_export(
        ctx: Context,
        report_type: Annotated[
            Literal[
                "alarm_analysis",
                "lamp_alarm_analysis",
                "leakage_analysis",
                "asset_inventory",
                "inspection_progress",
                "energy_records",
                "energy_analysis",
                "lamp_survey_records",
                "rtu_leakage_alarms",
                "rtu_leakage_analysis",
                "inspection_logs",
                "maintenance_records",
            ],
            Field(),
        ],
        asset_type: Annotated[
            Literal["cabinet", "rtu", "lamppost"] | None,
            Field(),
        ] = None,
        keyword: Annotated[str | None, Field(max_length=200)] = None,
        alarm_type: Annotated[str | None, Field(max_length=200)] = None,
        alarm_state: Annotated[
            Literal["all", "current", "cleared"], Field()
        ] = "all",
        time_field: Annotated[
            Literal["last_activity", "occurred"], Field()
        ] = "last_activity",
        start_date: Annotated[str | None, Field(max_length=10)] = None,
        end_date: Annotated[str | None, Field(max_length=10)] = None,
        last_days: Annotated[int | None, Field(ge=1, le=3660)] = None,
        top_n: Annotated[int, Field(ge=1, le=20)] = 10,
        task_id: Annotated[str | None, Field(max_length=200)] = None,
        detail_date: Annotated[str | None, Field(max_length=10)] = None,
        clockin_user: Annotated[str | None, Field(max_length=200)] = None,
        has_issues: Annotated[bool | None, Field()] = None,
        device_id: Annotated[str | None, Field(max_length=200)] = None,
        lamp_post_id: Annotated[str | None, Field(max_length=200)] = None,
        start_time: Annotated[str | None, Field(max_length=19)] = None,
        end_time: Annotated[str | None, Field(max_length=19)] = None,
        plan_id: Annotated[str | None, Field(max_length=200)] = None,
        group: Annotated[str | None, Field(max_length=200)] = None,
        overhaul_user: Annotated[str | None, Field(max_length=200)] = None,
        device_type: Annotated[
            Literal["all", "rtu", "lamppost"] | None,
            Field(),
        ] = None,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"report_type": report_type}
        for name, value in (
            ("asset_type", asset_type),
            ("keyword", keyword),
            ("alarm_type", alarm_type),
            ("start_date", start_date),
            ("end_date", end_date),
            ("last_days", last_days),
            ("task_id", task_id),
            ("detail_date", detail_date),
            ("clockin_user", clockin_user),
            ("has_issues", has_issues),
            ("device_id", device_id),
            ("lamp_post_id", lamp_post_id),
            ("start_time", start_time),
            ("end_time", end_time),
            ("plan_id", plan_id),
            ("group", group),
            ("overhaul_user", overhaul_user),
            ("device_type", device_type),
        ):
            if value is not None:
                arguments[name] = value
        if report_type == "alarm_analysis":
            arguments["alarm_state"] = alarm_state
            arguments["time_field"] = time_field
            arguments["top_n"] = top_n
        elif report_type in {
            "leakage_analysis",
            "energy_analysis",
            "rtu_leakage_analysis",
        }:
            arguments["top_n"] = top_n
        return await invoke(
            ctx,
            SMARTLIGHT_REPORT_EXPORT_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:read"},
        )

    @mcp.tool(
        name="smartlight_alarm_remark_update_prepare",
        title="准备修改照明告警备注",
        meta=interaction_tool_meta(),
        description=(
            "为一条精确的 RTU 告警打开预填可信字段卡，读取当前备注并冻结修改"
            "计划；随后还需用户在独立授权卡中确认。本工具本身不修改照明系统。"
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def smartlight_alarm_remark_update_prepare(
        ctx: Context,
        alarm_id: Annotated[str, Field(min_length=1, max_length=200)],
        remark: Annotated[str | None, Field(max_length=500)] = None,
        input_submission_id: Annotated[
            str | None,
            Field(min_length=32, max_length=128),
        ] = None,
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"alarm_id": alarm_id}
        if remark is not None:
            arguments["remark"] = remark
        if input_submission_id is not None:
            arguments["input_submission_id"] = input_submission_id
        return await invoke(
            ctx,
            SMARTLIGHT_ALARM_REMARK_UPDATE_PREPARE_CAPABILITY,
            arguments,
            idempotency_key,
            {"smartlight:write:alarm_remark"},
        )

    @mcp.tool(
        name="smartlight_alarm_remark_update",
        title="执行已授权的照明告警备注修改",
        meta=interaction_tool_meta(),
        description=(
            "消费一份已批准的授权，修改冻结的 RTU 告警备注，并通过权威回读"
            "确认结果。该提交工具不应在授权前直接调用。"
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def smartlight_alarm_remark_update(
        ctx: Context,
        authorization_id: Annotated[str, Field(min_length=32, max_length=128)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            SMARTLIGHT_ALARM_REMARK_UPDATE_CAPABILITY,
            {"authorization_id": authorization_id},
            idempotency_key,
            {"smartlight:write:alarm_remark"},
        )

    @mcp.tool(
        name="smartlight_alarm_work_area_submit_prepare",
        title="准备提交 RTU 告警到工区",
        meta=interaction_tool_meta(),
        description=(
            "读取一条精确 RTU 告警并校验等级、状态和所属工区，随后直接打开"
            "可信授权卡。没有字段填写步骤；本工具本身不修改照明系统。"
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def smartlight_alarm_work_area_submit_prepare(
        ctx: Context,
        alarm_id: Annotated[str, Field(min_length=1, max_length=200)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            SMARTLIGHT_ALARM_WORK_AREA_SUBMIT_PREPARE_CAPABILITY,
            {"alarm_id": alarm_id},
            idempotency_key,
            {"smartlight:write:alarm_work_area_submit"},
        )

    @mcp.tool(
        name="smartlight_alarm_work_area_submit",
        title="执行已授权的 RTU 告警工区提交",
        meta=interaction_tool_meta(),
        description=(
            "消费已批准的单次授权，把冻结的 RTU 告警提交到所属工区，并回读"
            "isSubmitWorkArea=1。提交前不得直接调用。"
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def smartlight_alarm_work_area_submit(
        ctx: Context,
        authorization_id: Annotated[str, Field(min_length=32, max_length=128)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            SMARTLIGHT_ALARM_WORK_AREA_SUBMIT_CAPABILITY,
            {"authorization_id": authorization_id},
            idempotency_key,
            {"smartlight:write:alarm_work_area_submit"},
        )

    @mcp.tool(
        name="smartlight_alarm_work_area_revoke_prepare",
        title="准备撤回 RTU 告警工区提交",
        meta=interaction_tool_meta(),
        description=(
            "读取一条已提交工区的精确 RTU 告警，冻结当前状态并直接打开可信"
            "授权卡。没有字段填写步骤；本工具本身不修改照明系统。"
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def smartlight_alarm_work_area_revoke_prepare(
        ctx: Context,
        alarm_id: Annotated[str, Field(min_length=1, max_length=200)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            SMARTLIGHT_ALARM_WORK_AREA_REVOKE_PREPARE_CAPABILITY,
            {"alarm_id": alarm_id},
            idempotency_key,
            {"smartlight:write:alarm_work_area_revoke"},
        )

    @mcp.tool(
        name="smartlight_alarm_work_area_revoke",
        title="执行已授权的 RTU 告警工区撤回",
        meta=interaction_tool_meta(),
        description=(
            "消费已批准的单次授权，撤回冻结的 RTU 告警工区提交，并回读"
            "isSubmitWorkArea=0。授权前不得直接调用。"
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def smartlight_alarm_work_area_revoke(
        ctx: Context,
        authorization_id: Annotated[str, Field(min_length=32, max_length=128)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            SMARTLIGHT_ALARM_WORK_AREA_REVOKE_CAPABILITY,
            {"authorization_id": authorization_id},
            idempotency_key,
            {"smartlight:write:alarm_work_area_revoke"},
        )

    @mcp.tool(
        name="smartlight_rtu_alarm_dispose_prepare",
        title="准备处置 RTU 告警",
        meta=interaction_tool_meta(),
        description=(
            "读取一条精确 RTU 告警，确认状态可处置并直接打开不可逆授权卡。"
            "没有字段填写步骤；模糊的“处理”意图不应直接调用本工具。"
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def smartlight_rtu_alarm_dispose_prepare(
        ctx: Context,
        alarm_id: Annotated[str, Field(min_length=1, max_length=200)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            SMARTLIGHT_RTU_ALARM_DISPOSE_PREPARE_CAPABILITY,
            {"alarm_id": alarm_id},
            idempotency_key,
            {"smartlight:write:alarm_disposition"},
        )

    @mcp.tool(
        name="smartlight_rtu_alarm_dispose",
        title="执行已授权的 RTU 告警处置",
        meta=interaction_tool_meta(),
        description=(
            "消费已批准的单次授权，把冻结的 RTU 告警标记为已处置并回读"
            "conductStatue=3。目标系统没有已发现的撤销接口。"
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def smartlight_rtu_alarm_dispose(
        ctx: Context,
        authorization_id: Annotated[str, Field(min_length=32, max_length=128)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            SMARTLIGHT_RTU_ALARM_DISPOSE_CAPABILITY,
            {"authorization_id": authorization_id},
            idempotency_key,
            {"smartlight:write:alarm_disposition"},
        )

    @mcp.tool(
        name="smartlight_session_status",
        title="检查照明系统登录状态",
        description="Verify the authenticated caller's central Smartlight CAS/JWT session.",
        annotations=read_annotations,
        structured_output=True,
    )
    async def smartlight_session_status() -> dict[str, Any]:
        identity = _request_identity(
            identity_store,
            required_scopes={"smartlight:read"},
        )
        return await asyncio.to_thread(
            service.session_status,
            user_subject=identity["user_subject"],
            system_id="smartlight",
        )

    @mcp.tool(
        name="smartlight_session_login",
        title="登录照明实验室测试系统",
        meta=interaction_tool_meta(),
        description=(
            "Reuse a valid Smartlight session or open a trusted CAPTCHA login card. "
            "The password and verification code never enter MCP arguments."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def smartlight_session_login(
        ctx: Context,
        challenge_ttl_seconds: Annotated[int, Field(ge=30, le=900)] = 300,
    ) -> dict[str, Any]:
        identity = _request_identity(
            identity_store,
            required_scopes={"smartlight:read"},
        )
        response = await asyncio.to_thread(
            service.start_login,
            user_subject=identity["user_subject"],
            expected_principal_ref=None,
            card_base_url=auth_card_base_url,
            ttl_seconds=challenge_ttl_seconds,
            system_id="smartlight",
        )
        return package_interaction_result(response)

    @mcp.tool(
        name="oa_session_status",
        title="Verify OA Session Status",
        description=(
            "Verify the authenticated caller's active central OA session against OA. "
            "Non-active sessions are reported from the registry without asking for credentials."
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def oa_session_status() -> dict[str, Any]:
        identity = _request_identity(
            identity_store,
            required_scopes={"oa:read"},
        )
        return await asyncio.to_thread(
            service.session_status,
            user_subject=identity["user_subject"],
            system_id="oa",
        )

    @mcp.tool(
        name="oa_session_login",
        title="Ensure OA Session Login",
        meta=interaction_tool_meta(),
        description=(
            "Reuse and refresh a valid central OA session. Only when OA confirms "
            "that the session is no longer authenticated, create a short-lived "
            "trusted authentication card. Credentials are entered only in that "
            "card and never in MCP arguments."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def oa_session_login(
        ctx: Context,
        challenge_ttl_seconds: Annotated[int, Field(ge=30, le=900)] = 300,
    ) -> dict[str, Any]:
        identity = _request_identity(
            identity_store,
            required_scopes={"oa:read"},
        )
        response = await asyncio.to_thread(
            service.start_login,
            user_subject=identity["user_subject"],
            expected_principal_ref=None,
            card_base_url=auth_card_base_url,
            ttl_seconds=challenge_ttl_seconds,
        )
        return package_interaction_result(response)

    @mcp.tool(
        name="agentbridge_interaction_get",
        title="Get AgentBridge User Interaction",
        meta=interaction_tool_meta(),
        description=(
            "Read one host-independent trusted interaction envelope. Poll this tool "
            "until resume.ready is true; never collect credential or business-field "
            "values in the model conversation."
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def agentbridge_interaction_get(
        interaction_id: Annotated[str, Field(min_length=16, max_length=128)],
    ) -> dict[str, Any]:
        identity = _request_identity(identity_store)
        response = await asyncio.to_thread(
            service.get_interaction,
            user_subject=identity["user_subject"],
            interaction_id=interaction_id,
        )
        return package_interaction_result(response)

    @mcp.tool(
        name="agentbridge_interaction_resume",
        title="Resume Completed AgentBridge Interaction",
        meta=interaction_tool_meta(),
        description=(
            "Continue an interaction after the user completed its trusted surface. "
            "This tool cannot enter fields or approve a plan; it only consumes an "
            "already completed, user-bound interaction."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def agentbridge_interaction_resume(
        interaction_id: Annotated[str, Field(min_length=16, max_length=128)],
        idempotency_key: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        identity = _request_identity(identity_store)
        required_scopes = await asyncio.to_thread(
            service.interaction_required_scopes,
            user_subject=identity["user_subject"],
            interaction_id=interaction_id,
        )
        identity = _request_identity(
            identity_store,
            required_scopes=set(required_scopes),
        )
        response = await asyncio.to_thread(
            service.resume_interaction,
            user_subject=identity["user_subject"],
            interaction_id=interaction_id,
            idempotency_key=idempotency_key,
        )
        return package_interaction_result(response)

    @mcp.tool(
        name="agentbridge_operation_get",
        title="Get AgentBridge Operation",
        description="Get one operation owned by the authenticated caller.",
        annotations=read_annotations,
        structured_output=True,
    )
    async def agentbridge_operation_get(
        operation_id: Annotated[str, Field(min_length=1, max_length=128)],
    ) -> dict[str, Any]:
        identity = _request_identity(identity_store)
        return await asyncio.to_thread(
            service.get_operation,
            user_subject=identity["user_subject"],
            operation_id=operation_id,
        )

    @mcp.tool(
        name="agentbridge_operation_list",
        title="List AgentBridge Operations",
        description="List recent operations owned by the authenticated caller.",
        annotations=read_annotations,
        structured_output=True,
    )
    async def agentbridge_operation_list(
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        identity = _request_identity(identity_store)
        return await asyncio.to_thread(
            service.list_operations,
            user_subject=identity["user_subject"],
            limit=limit,
        )

    @mcp.tool(
        name="agentbridge_host_identity_profile",
        title="Resolve Host Identity Tool Access",
        description=(
            "Host-private authenticated identity and agent-tool access profile. "
            "It exposes no bearer secret or downstream credential."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def agentbridge_host_identity_profile(
        ctx: Context,
        agent_host: Annotated[str, Field(min_length=1, max_length=80)],
    ) -> dict[str, Any]:
        _require_host_context(ctx, agent_host=agent_host)
        identity = _request_identity(identity_store)
        allowed_tools = agent_facing_tools_for_scopes(identity["scopes"])
        return {
            "schemaVersion": "agentbridge.host-identity-profile.v1",
            "status": "succeeded",
            "identity": {
                "tokenId": identity["token_id"],
                "userSubject": identity["user_subject"],
                "label": identity.get("label"),
                "scopes": identity["scopes"],
                "expiresAt": identity["expires_at"],
            },
            "agentToolAccess": {
                "allowedToolNames": allowed_tools,
                "allowedToolCount": len(allowed_tools),
            },
        }

    @mcp.tool(
        name="agentbridge_host_task_ensure",
        title="Ensure Host-Owned AgentBridge Task",
        description=(
            "Host-private task continuity control. This tool requires trusted MCP "
            "request metadata and is not a model business capability."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def agentbridge_host_task_ensure(
        ctx: Context,
        agent_host: Annotated[str, Field(min_length=1, max_length=80)],
        host_task_key: Annotated[str, Field(min_length=1, max_length=1024)],
        endpoint_key: Annotated[str, Field(min_length=1, max_length=768)],
        client_type: Annotated[str, Field(min_length=1, max_length=80)],
        external_subject: Annotated[str, Field(min_length=1, max_length=768)],
        conversation_ref: Annotated[str, Field(min_length=1, max_length=1024)],
        title: Annotated[str, Field(min_length=1, max_length=240)],
        account_id: Annotated[str | None, Field(max_length=512)] = None,
        label: Annotated[str | None, Field(max_length=120)] = None,
        route: dict[str, Any] | None = None,
        capabilities: list[str] | None = None,
        task_scope: Annotated[
            str,
            Field(pattern="^(host_run|user_turn|independent)$"),
        ] = "host_run",
    ) -> dict[str, Any]:
        _require_host_context(ctx, agent_host=agent_host)
        identity = _request_identity(identity_store)
        return await _run_host_control(
            "agentbridge_host_task_ensure",
            service.ensure_host_task,
            user_subject=identity["user_subject"],
            token_id=identity["token_id"],
            agent_host=agent_host,
            host_task_key=host_task_key,
            endpoint_key=endpoint_key,
            client_type=client_type,
            external_subject=external_subject,
            conversation_ref=conversation_ref,
            title=title,
            account_id=account_id,
            label=label,
            route=route,
            capabilities=capabilities,
            task_scope=task_scope,
        )

    @mcp.tool(
        name="agentbridge_host_timeline_append",
        title="Append Host-Owned Cross-Endpoint Message",
        description=(
            "Host-private append-only message timeline control. It synchronizes "
            "non-sensitive user and assistant text across endpoints and is not a "
            "model business capability."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def agentbridge_host_timeline_append(
        ctx: Context,
        agent_host: Annotated[str, Field(min_length=1, max_length=80)],
        endpoint_key: Annotated[str, Field(min_length=1, max_length=768)],
        client_type: Annotated[str, Field(min_length=1, max_length=80)],
        external_subject: Annotated[str, Field(min_length=1, max_length=768)],
        conversation_ref: Annotated[str, Field(min_length=1, max_length=1024)],
        message_key: Annotated[str, Field(min_length=1, max_length=768)],
        role: Annotated[str, Field(pattern="^(user|assistant)$")],
        text: Annotated[str, Field(min_length=1, max_length=50_000)],
        account_id: Annotated[str | None, Field(max_length=512)] = None,
        label: Annotated[str | None, Field(max_length=120)] = None,
        route: dict[str, Any] | None = None,
        task_id: Annotated[str | None, Field(max_length=128)] = None,
    ) -> dict[str, Any]:
        _require_host_context(ctx, agent_host=agent_host)
        identity = _request_identity(identity_store)
        return await _run_host_control(
            "agentbridge_host_timeline_append",
            service.append_host_timeline_message,
            user_subject=identity["user_subject"],
            token_id=identity["token_id"],
            agent_host=agent_host,
            endpoint_key=endpoint_key,
            client_type=client_type,
            external_subject=external_subject,
            conversation_ref=conversation_ref,
            message_key=message_key,
            role=role,
            text=text,
            account_id=account_id,
            label=label,
            route=route,
            task_id=task_id,
        )

    @mcp.tool(
        name="agentbridge_host_task_observe",
        title="Observe Host-Owned AgentBridge Task",
        description=(
            "Host-private association control for trusted operation and interaction IDs."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def agentbridge_host_task_observe(
        ctx: Context,
        agent_host: Annotated[str, Field(min_length=1, max_length=80)],
        task_id: Annotated[str, Field(min_length=16, max_length=128)],
        operation_ids: list[str] | None = None,
        interaction_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        _require_host_context(ctx, agent_host=agent_host)
        identity = _request_identity(identity_store)
        return await asyncio.to_thread(
            service.observe_host_task,
            user_subject=identity["user_subject"],
            task_id=task_id,
            operation_ids=operation_ids,
            interaction_ids=interaction_ids,
        )

    @mcp.tool(
        name="agentbridge_host_task_finish",
        title="Finish Host-Owned AgentBridge Task",
        description=(
            "Host-private terminal task control for tool results that have no "
            "operation or trusted-interaction reference."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def agentbridge_host_task_finish(
        ctx: Context,
        agent_host: Annotated[str, Field(min_length=1, max_length=80)],
        task_id: Annotated[str, Field(min_length=16, max_length=128)],
        outcome: Annotated[str, Field(pattern="^(succeeded|failed)$")],
        reason: Annotated[str | None, Field(max_length=120)] = None,
        error_code: Annotated[str | None, Field(max_length=120)] = None,
        message: Annotated[str | None, Field(max_length=500)] = None,
        causation_ref: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        _require_host_context(ctx, agent_host=agent_host)
        identity = _request_identity(identity_store)
        return await _run_host_control(
            "agentbridge_host_task_finish",
            service.finish_host_task,
            user_subject=identity["user_subject"],
            task_id=task_id,
            outcome=outcome,
            reason=reason,
            error_code=error_code,
            message=message,
            causation_ref=causation_ref,
        )

    @mcp.tool(
        name="agentbridge_host_task_recovery_list",
        title="List Recoverable Host-Owned AgentBridge Tasks",
        description=(
            "Host-private restart recovery for pending trusted interactions."
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def agentbridge_host_task_recovery_list(
        ctx: Context,
        agent_host: Annotated[str, Field(min_length=1, max_length=80)],
        endpoint_key: Annotated[str, Field(min_length=1, max_length=768)],
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
        include_user_endpoints: bool = False,
    ) -> dict[str, Any]:
        _require_host_context(ctx, agent_host=agent_host)
        identity = _request_identity(identity_store)
        return await asyncio.to_thread(
            service.recover_host_tasks,
            user_subject=identity["user_subject"],
            agent_host=agent_host,
            endpoint_key=endpoint_key,
            limit=limit,
            include_user_endpoints=include_user_endpoints,
        )

    @mcp.tool(
        name="agentbridge_host_task_list",
        title="List Host-Owned AgentBridge Tasks",
        description=(
            "Host-private read-only diagnostics for tasks owned by one bound endpoint."
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def agentbridge_host_task_list(
        ctx: Context,
        agent_host: Annotated[str, Field(min_length=1, max_length=80)],
        endpoint_key: Annotated[str, Field(min_length=1, max_length=768)],
        active_only: bool = False,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        _require_host_context(ctx, agent_host=agent_host)
        identity = _request_identity(identity_store)
        return await asyncio.to_thread(
            service.list_host_tasks,
            user_subject=identity["user_subject"],
            agent_host=agent_host,
            endpoint_key=endpoint_key,
            active_only=active_only,
            limit=limit,
        )

    @mcp.tool(
        name="agentbridge_host_task_continuation_resolve",
        title="Resolve One Cross-Endpoint AgentBridge Task",
        description=(
            "Host-private task selection and continuation context. It binds one "
            "owned task to the authenticated endpoint, returns a non-sensitive "
            "snapshot, and never executes a business capability."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def agentbridge_host_task_continuation_resolve(
        ctx: Context,
        agent_host: Annotated[str, Field(min_length=1, max_length=80)],
        endpoint_key: Annotated[str, Field(min_length=1, max_length=768)],
        task_id: Annotated[str | None, Field(max_length=128)] = None,
        ordinal: Annotated[int | None, Field(ge=1, le=20)] = None,
        source_client_type: Annotated[
            str | None,
            Field(max_length=80),
        ] = None,
        cross_endpoint_only: bool = False,
        prefer_active: bool = True,
        prefer_latest: bool = False,
        reuse_selected: bool = True,
        allow_follow_up: bool = False,
        max_age_minutes: Annotated[int, Field(ge=1, le=10_080)] = 1_440,
        limit: Annotated[int, Field(ge=1, le=20)] = 8,
    ) -> dict[str, Any]:
        _require_host_context(ctx, agent_host=agent_host)
        identity = _request_identity(identity_store)
        return await _run_host_control(
            "agentbridge_host_task_continuation_resolve",
            service.resolve_host_task_continuation,
            user_subject=identity["user_subject"],
            agent_host=agent_host,
            endpoint_key=endpoint_key,
            task_id=task_id,
            ordinal=ordinal,
            source_client_type=source_client_type,
            cross_endpoint_only=cross_endpoint_only,
            prefer_active=prefer_active,
            prefer_latest=prefer_latest,
            reuse_selected=reuse_selected,
            allow_follow_up=allow_follow_up,
            max_age_minutes=max_age_minutes,
            limit=limit,
        )

    @mcp.tool(
        name="agentbridge_host_cross_endpoint_context",
        title="Read Recent Cross-Endpoint Agent Context",
        description=(
            "Host-private read-only access to recent non-sensitive timeline "
            "messages from the authenticated user's other endpoints."
        ),
        annotations=read_annotations,
        structured_output=True,
    )
    async def agentbridge_host_cross_endpoint_context(
        ctx: Context,
        agent_host: Annotated[str, Field(min_length=1, max_length=80)],
        endpoint_key: Annotated[str, Field(min_length=1, max_length=768)],
        max_age_minutes: Annotated[int, Field(ge=1, le=1_440)] = 360,
        limit: Annotated[int, Field(ge=1, le=20)] = 12,
    ) -> dict[str, Any]:
        _require_host_context(ctx, agent_host=agent_host)
        identity = _request_identity(identity_store)
        return await _run_host_control(
            "agentbridge_host_cross_endpoint_context",
            service.get_host_cross_endpoint_context,
            user_subject=identity["user_subject"],
            agent_host=agent_host,
            endpoint_key=endpoint_key,
            max_age_minutes=max_age_minutes,
            limit=limit,
        )

    @mcp.tool(
        name="agentbridge_host_interaction_present",
        title="Present Trusted Interaction for One Endpoint",
        description=(
            "Host-private trusted-interaction presentation. Execution "
            "authorizations receive an endpoint-specific one-use URL."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def agentbridge_host_interaction_present(
        ctx: Context,
        agent_host: Annotated[str, Field(min_length=1, max_length=80)],
        endpoint_key: Annotated[str, Field(min_length=1, max_length=768)],
        interaction_id: Annotated[str, Field(min_length=16, max_length=128)],
    ) -> dict[str, Any]:
        _require_host_context(ctx, agent_host=agent_host)
        identity = _request_identity(identity_store)
        return await asyncio.to_thread(
            service.present_interaction,
            user_subject=identity["user_subject"],
            agent_host=agent_host,
            endpoint_key=endpoint_key,
            interaction_id=interaction_id,
        )

    @mcp.tool(
        name="agentbridge_host_notification_claim",
        title="Claim Endpoint Notifications",
        description=(
            "Host-private lease for pending endpoint deliveries. It does not "
            "execute or resume business operations."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def agentbridge_host_notification_claim(
        ctx: Context,
        agent_host: Annotated[str, Field(min_length=1, max_length=80)],
        endpoint_key: Annotated[str, Field(min_length=1, max_length=768)],
        limit: Annotated[int, Field(ge=1, le=100)] = 10,
        lease_seconds: Annotated[int, Field(ge=5, le=300)] = 30,
    ) -> dict[str, Any]:
        _require_host_context(ctx, agent_host=agent_host)
        identity = _request_identity(identity_store)
        return await _run_host_control(
            "agentbridge_host_notification_claim",
            service.claim_host_notifications,
            user_subject=identity["user_subject"],
            agent_host=agent_host,
            endpoint_key=endpoint_key,
            limit=limit,
            lease_seconds=lease_seconds,
        )

    @mcp.tool(
        name="agentbridge_host_notification_ack",
        title="Acknowledge Endpoint Notification",
        description=(
            "Host-private delivery acknowledgement, bounded retry request, "
            "or activity-gated WeChat deferral."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def agentbridge_host_notification_ack(
        ctx: Context,
        agent_host: Annotated[str, Field(min_length=1, max_length=80)],
        endpoint_key: Annotated[str, Field(min_length=1, max_length=768)],
        delivery_id: Annotated[str, Field(min_length=16, max_length=128)],
        succeeded: bool,
        retry_after_seconds: Annotated[int, Field(ge=1, le=300)] = 5,
        defer_until_activity: bool = False,
    ) -> dict[str, Any]:
        _require_host_context(ctx, agent_host=agent_host)
        identity = _request_identity(identity_store)
        return await _run_host_control(
            "agentbridge_host_notification_ack",
            service.acknowledge_host_notification,
            user_subject=identity["user_subject"],
            agent_host=agent_host,
            endpoint_key=endpoint_key,
            delivery_id=delivery_id,
            succeeded=succeeded,
            retry_after_seconds=retry_after_seconds,
            defer_until_activity=defer_until_activity,
        )

    @mcp.tool(
        name="agentbridge_host_artifact_delivery_report",
        title="Report Host Artifact Delivery",
        description=(
            "Host-private idempotent delivery receipt for governed task artifacts. "
            "It records endpoint attachment, fallback-link, or failed outcomes and is "
            "not a model business capability."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def agentbridge_host_artifact_delivery_report(
        ctx: Context,
        agent_host: Annotated[str, Field(min_length=1, max_length=80)],
        task_id: Annotated[str, Field(min_length=16, max_length=128)],
        delivery_ref: Annotated[str, Field(min_length=1, max_length=512)],
        channel: Annotated[str, Field(min_length=1, max_length=80)],
        files: Annotated[list[dict[str, Any]], Field(min_length=1, max_length=20)],
    ) -> dict[str, Any]:
        _require_host_context(ctx, agent_host=agent_host)
        identity = _request_identity(identity_store)
        return await _run_host_control(
            "agentbridge_host_artifact_delivery_report",
            service.report_host_artifact_delivery,
            user_subject=identity["user_subject"],
            agent_host=agent_host,
            task_id=task_id,
            delivery_ref=delivery_ref,
            channel=channel,
            files=files,
        )

    @mcp.tool(
        name="agentbridge_host_workspace_link_confirm",
        title="Confirm Agent Workspace Identity Link",
        description=(
            "Host-private identity linking control. The authenticated MCP "
            "identity, not model-supplied text, owns the resulting web account."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def agentbridge_host_workspace_link_confirm(
        ctx: Context,
        agent_host: Annotated[str, Field(min_length=1, max_length=80)],
        endpoint_key: Annotated[str, Field(min_length=1, max_length=768)],
        client_type: Annotated[str, Field(min_length=1, max_length=80)],
        external_subject: Annotated[str, Field(min_length=1, max_length=768)],
        conversation_ref: Annotated[str, Field(min_length=1, max_length=1024)],
        link_code: Annotated[str, Field(min_length=8, max_length=16)],
        account_id: Annotated[str | None, Field(max_length=512)] = None,
        label: Annotated[str | None, Field(max_length=120)] = None,
        route: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _require_host_context(ctx, agent_host=agent_host)
        identity = _request_identity(identity_store)
        return await asyncio.to_thread(
            service.confirm_workspace_link,
            user_subject=identity["user_subject"],
            token_id=identity["token_id"],
            agent_host=agent_host,
            endpoint_key=endpoint_key,
            client_type=client_type,
            external_subject=external_subject,
            account_id=account_id,
            conversation_ref=conversation_ref,
            link_code=link_code,
            label=label,
            route=route,
        )

    @mcp.tool(
        name="agentbridge_host_workspace_session_bind",
        title="Bind an Agent Workspace Gateway Session",
        description=(
            "Host-private one-use proof that binds a web chat session to the "
            "authenticated AgentBridge identity."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def agentbridge_host_workspace_session_bind(
        ctx: Context,
        agent_host: Annotated[str, Field(min_length=1, max_length=80)],
        endpoint_key: Annotated[str, Field(min_length=1, max_length=768)],
        session_key: Annotated[str, Field(min_length=16, max_length=1024)],
        grant: Annotated[str, Field(min_length=32, max_length=256)],
        turn_ref: Annotated[str | None, Field(max_length=128)] = None,
    ) -> dict[str, Any]:
        _require_host_context(ctx, agent_host=agent_host)
        identity = _request_identity(identity_store)
        return await asyncio.to_thread(
            service.redeem_workspace_gateway_grant,
            user_subject=identity["user_subject"],
            agent_host=agent_host,
            endpoint_key=endpoint_key,
            session_key=session_key,
            grant=grant,
            turn_ref=turn_ref,
        )

    @mcp.tool(
        name="agentbridge_host_workspace_session_resolve",
        title="Resolve an Agent Workspace Gateway Session",
        description=(
            "Host-private read-only recovery of a web chat session binding "
            "from the authenticated AgentBridge identity."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def agentbridge_host_workspace_session_resolve(
        ctx: Context,
        agent_host: Annotated[str, Field(min_length=1, max_length=80)],
        session_key: Annotated[str, Field(min_length=16, max_length=1024)],
    ) -> dict[str, Any]:
        _require_host_context(ctx, agent_host=agent_host)
        identity = _request_identity(identity_store)
        return await asyncio.to_thread(
            service.resolve_workspace_gateway_session,
            user_subject=identity["user_subject"],
            agent_host=agent_host,
            session_key=session_key,
        )

    return mcp


def serve_central_mcp(
    *,
    service: CentralCapabilityService,
    identity_store: McpIdentityTokenStore,
    mcp_config: CentralMcpServerConfig,
    auth_config: AuthServerConfig,
    admin_config: AdminServerConfig | None = None,
    workspace_config: WorkspaceServerConfig | None = None,
    workspace_gateway: OpenClawGatewayClient | None = None,
    login_timeout_seconds: float = 45,
    keepalive_interval_seconds: float = 0,
    keepalive_activity_lease_seconds: float = 604_800,
) -> None:
    broker = CredentialBroker(
        challenge_store=service.challenges,
        session_registry=service.sessions,
        session_state_store=service.session_states,
        adapter_factory=lambda challenge: service.adapter_for_system(
            challenge["system_id"]
        ),
        worker_factory=service.authentication_worker,
        login_timeout_seconds=login_timeout_seconds,
    )
    auth_application = TrustedAuthApplication(
        challenge_store=service.challenges,
        broker=broker,
    )
    auth_origin = urlparse(auth_config.public_base_url)
    remote_browser_base_url = (
        f"{auth_origin.scheme}://{auth_origin.hostname}:8781"
    )
    interactive_broker = RemoteInteractiveBrowserBroker(
        challenge_store=service.challenges,
        session_registry=service.sessions,
        session_state_store=service.session_states,
        adapter_factory=lambda challenge: service.adapter_for_system(
            challenge["system_id"]
        ),
        worker_factory=service.remote_authentication_worker,
        config=RemoteBrowserConfig(
            runtime_root=(service.home / "remote-browser").resolve(),
            public_base_url=remote_browser_base_url,
            listen_host=auth_origin.hostname or auth_config.host,
            listen_port=8781,
            tls_cert=auth_config.tls_cert,
            tls_key=auth_config.tls_key,
            allow_insecure_private_http=auth_config.insecure_private_http,
        ),
        login_timeout_seconds=900,
    )
    interactive_application = TrustedInteractiveBrowserApplication(
        challenge_store=service.challenges,
        broker=interactive_broker,
    )
    action_application = TrustedActionApplication(
        authorization_store=service.write_authorizations,
    )
    field_application = TrustedFieldApplication(
        submission_store=service.field_submissions,
    )
    download_application = TrustedDocumentDownloadApplication(
        download_store=service.document_downloads,
        fetcher=service.fetch_document_download,
    )
    timeline_attachment_application = TrustedTimelineAttachmentApplication(
        attachment_store=service.timeline_attachments,
    )
    auth_server = create_auth_http_server(
        config=auth_config,
        application=auth_application,
        action_application=action_application,
        field_application=field_application,
        download_application=download_application,
        timeline_attachment_application=timeline_attachment_application,
        interactive_application=interactive_application,
    )
    admin_server = None
    admin_thread = None
    if admin_config is not None:
        control_plane = AdminControlPlane(
            service=service,
            identity_store=identity_store,
            workspace_gateway=workspace_gateway,
        )
        admin_server = create_admin_http_server(
            config=admin_config,
            control_plane=control_plane,
        )
        admin_thread = threading.Thread(
            target=admin_server.serve_forever,
            kwargs={"poll_interval": 0.25},
            name="agentbridge-admin",
            daemon=True,
        )
    workspace_server = None
    workspace_thread = None
    if workspace_config is not None:
        workspace_server = create_workspace_http_server(
            config=workspace_config,
            application=WorkspaceApplication(
                service=service,
                gateway=workspace_gateway,
            ),
        )
        workspace_thread = threading.Thread(
            target=workspace_server.serve_forever,
            kwargs={"poll_interval": 0.25},
            name="agentbridge-workspace",
            daemon=True,
        )
    auth_thread = threading.Thread(
        target=auth_server.serve_forever,
        kwargs={"poll_interval": 0.25},
        name="agentbridge-auth-card",
        daemon=True,
    )
    keepalive = CentralSessionKeepalive(
        service,
        interval_seconds=keepalive_interval_seconds,
        activity_lease_seconds=keepalive_activity_lease_seconds,
    )
    governance = CentralRuntimeGovernanceWorker(service)
    auth_thread.start()
    if admin_thread is not None:
        admin_thread.start()
    if workspace_thread is not None:
        workspace_thread.start()
    keepalive.start()
    governance.start()
    try:
        mcp = create_central_mcp_server(
            service=service,
            identity_store=identity_store,
            config=mcp_config,
            auth_card_base_url=auth_config.public_base_url,
        )
        uvicorn.run(
            mcp.streamable_http_app(),
            host=mcp_config.host,
            port=mcp_config.port,
            ssl_certfile=str(mcp_config.tls_cert) if mcp_config.tls_cert else None,
            ssl_keyfile=str(mcp_config.tls_key) if mcp_config.tls_key else None,
            access_log=False,
        )
    finally:
        governance.stop()
        keepalive.stop()
        interactive_broker.shutdown()
        if admin_server is not None:
            admin_server.shutdown()
            admin_server.server_close()
        if admin_thread is not None:
            admin_thread.join(timeout=5)
        if workspace_server is not None:
            workspace_server.shutdown()
            workspace_server.server_close()
        if workspace_thread is not None:
            workspace_thread.join(timeout=5)
        auth_server.shutdown()
        auth_server.server_close()
        auth_thread.join(timeout=5)


def _request_identity(
    store: McpIdentityTokenStore,
    *,
    required_scopes: set[str] | None = None,
) -> dict:
    access_token = get_access_token()
    if access_token is None:
        raise PermissionError("MCP request is not authenticated")
    return store.resolve_client(
        access_token.client_id,
        required_scopes=required_scopes,
    )


def _request_meta(ctx: Context) -> Mapping[str, Any]:
    value = getattr(ctx.request_context, "meta", None)
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(by_alias=True)
        if isinstance(dumped, Mapping):
            return dumped
    return {}


def _require_host_context(ctx: Context, *, agent_host: str) -> None:
    host_context = _request_meta(ctx).get(HOST_CONTEXT_META_KEY)
    if not isinstance(host_context, Mapping):
        raise PermissionError("trusted AgentBridge host context is required")
    if host_context.get("version") != "1":
        raise PermissionError("trusted AgentBridge host context version is invalid")
    if host_context.get("agentHost") != agent_host:
        raise PermissionError("trusted AgentBridge host context does not match")


def _request_task_id(ctx: Context) -> str | None:
    task_context = _request_meta(ctx).get(TASK_CONTEXT_META_KEY)
    if not isinstance(task_context, Mapping):
        return None
    task_id = task_context.get("taskId")
    if not isinstance(task_id, str):
        return None
    normalized = task_id.strip()
    if len(normalized) < 16 or len(normalized) > 128:
        return None
    return normalized


def _request_runtime_context(ctx: Context) -> dict[str, str | None]:
    meta = _request_meta(ctx)
    host_context = meta.get(HOST_CONTEXT_META_KEY)
    task_context = meta.get(TASK_CONTEXT_META_KEY)
    host = host_context if isinstance(host_context, Mapping) else {}
    task = task_context if isinstance(task_context, Mapping) else {}

    def text_value(value: Any, maximum: int) -> str | None:
        if not isinstance(value, (str, int)):
            return None
        normalized = str(value).strip()
        return normalized[:maximum] if normalized else None

    return {
        "hostType": text_value(host.get("agentHost"), 80) or "unknown",
        "hostInstanceId": text_value(host.get("hostInstanceId"), 160),
        "hostRunId": (
            text_value(task.get("hostRunId"), 256)
            or text_value(task.get("toolCallId"), 256)
        ),
        "endpointId": text_value(task.get("endpointId"), 128),
    }


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _normalize_public_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("central MCP public base URL must be http(s)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("central MCP public base URL is invalid")
    if parsed.path not in {"", "/"}:
        raise ValueError("central MCP public base URL must not include a path")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _origin(value: str) -> str:
    parsed = urlparse(value)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
