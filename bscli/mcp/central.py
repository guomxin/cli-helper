from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import ipaddress
import logging
from pathlib import Path
import threading
from typing import Annotated, Any, Literal
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
from bscli.auth.action_card import TrustedActionApplication
from bscli.auth.card import TrustedAuthApplication
from bscli.auth.field_card import TrustedFieldApplication
from bscli.auth.server import AuthServerConfig, create_auth_http_server
from bscli.broker.credential import CredentialBroker
from bscli.core.central_service import CentralCapabilityService
from bscli.core.mcp_identities import McpIdentityTokenStore
from bscli.core.network_security import validate_insecure_private_http_endpoint
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


_LOGGER = logging.getLogger("uvicorn.error")


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
            name="agentbridge-oa-keepalive",
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
                    "AgentBridge OA keepalive cycle: active=%d eligible=%d "
                    "kept_alive=%d expired=%d deferred=%d outside_lease=%d",
                    summary["activeSessions"],
                    summary["eligibleSessions"],
                    summary["keptAlive"],
                    summary["expired"],
                    summary["deferred"],
                    summary["outsideLease"],
                )
            except Exception as exc:
                _LOGGER.warning(
                    "AgentBridge OA keepalive cycle failed: error=%s",
                    exc.__class__.__name__,
                )
            if self._stop_event.wait(self.interval_seconds):
                return


class StoredIdentityTokenVerifier(TokenVerifier):
    def __init__(self, store: McpIdentityTokenStore, *, resource: str) -> None:
        self.store = store
        self.resource = resource

    async def verify_token(self, token: str) -> AccessToken | None:
        identity = self.store.verify(token, required_scopes={"oa:read"})
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
        name="agentbridge_oa_mcp",
        instructions=(
            "Central Seeyon OA business capabilities. Caller identity comes from the "
            "authenticated Bearer token; never request or accept a user subject as tool input. "
            "Business-trip draft writes require a separate trusted action-card approval."
        ),
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(config.public_base_url),
            resource_server_url=AnyHttpUrl(config.mcp_url),
            required_scopes=["oa:read"],
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
            "App or private host adapter render it. If no app appears, call "
            "agentbridge_interaction_get with the returned interaction ID. Resume only "
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
        response = await asyncio.to_thread(
            service.invoke,
            user_subject=identity["user_subject"],
            capability_name=capability_name,
            arguments=arguments,
            idempotency_key=idempotency_key,
            request_id=str(ctx.request_id),
        )
        return package_interaction_result(response)

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
        name="oa_workflow_done_list",
        title="List Completed OA Workflows",
        description="List completed workflows for the authenticated OA user.",
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
        description="List tracked workflows for the authenticated OA user.",
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
        collection: Literal["pending", "done", "tracked"],
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
        collection: Literal["pending", "done", "tracked"],
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
        trip_days: Annotated[float | None, Field(ge=0, le=366)] = None,
        trip_hours: Annotated[float | None, Field(ge=0, le=8784)] = None,
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
            ("trip_days", trip_days),
            ("trip_hours", trip_hours),
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
        trip_days: Annotated[float | None, Field(ge=0, le=366)] = None,
        trip_hours: Annotated[float | None, Field(ge=0, le=8784)] = None,
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
            ("trip_days", trip_days),
            ("trip_hours", trip_hours),
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
        identity = _request_identity(identity_store)
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
        identity = _request_identity(identity_store)
        response = await asyncio.to_thread(
            service.start_login,
            user_subject=identity["user_subject"],
            expected_principal_ref=identity["expected_principal_ref"],
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

    return mcp


def serve_central_mcp(
    *,
    service: CentralCapabilityService,
    identity_store: McpIdentityTokenStore,
    mcp_config: CentralMcpServerConfig,
    auth_config: AuthServerConfig,
    login_timeout_seconds: float = 45,
    keepalive_interval_seconds: float = 0,
    keepalive_activity_lease_seconds: float = 28_800,
) -> None:
    broker = CredentialBroker(
        challenge_store=service.challenges,
        session_registry=service.sessions,
        session_state_store=service.session_states,
        adapter_factory=lambda _challenge: service.adapter,
        worker_factory=service.authentication_worker,
        login_timeout_seconds=login_timeout_seconds,
    )
    auth_application = TrustedAuthApplication(
        challenge_store=service.challenges,
        broker=broker,
    )
    action_application = TrustedActionApplication(
        authorization_store=service.write_authorizations,
    )
    field_application = TrustedFieldApplication(
        submission_store=service.field_submissions,
    )
    auth_server = create_auth_http_server(
        config=auth_config,
        application=auth_application,
        action_application=action_application,
        field_application=field_application,
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
    auth_thread.start()
    keepalive.start()
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
        keepalive.stop()
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
        required_scopes=required_scopes or {"oa:read"},
    )


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
