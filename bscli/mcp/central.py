from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import ipaddress
from pathlib import Path
import threading
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, Field
import uvicorn

from bscli.auth.card import TrustedAuthApplication
from bscli.auth.server import AuthServerConfig, create_auth_http_server
from bscli.broker.credential import CredentialBroker
from bscli.core.central_service import CentralCapabilityService
from bscli.core.mcp_identities import McpIdentityTokenStore


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
    if not loopback and cert is None:
        raise ValueError("non-loopback central MCP service requires TLS")
    if not loopback and not normalized.startswith("https://"):
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
            "Central, read-only Seeyon OA capabilities. Caller identity comes from the "
            "authenticated Bearer token; never request or accept a user subject as tool input."
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

    async def invoke(
        ctx: Context,
        capability_name: str,
        arguments: dict,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        identity = _request_identity(identity_store)
        return await asyncio.to_thread(
            service.invoke,
            user_subject=identity["user_subject"],
            capability_name=capability_name,
            arguments=arguments,
            idempotency_key=idempotency_key,
            request_id=str(ctx.request_id),
        )

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
        name="oa_session_status",
        title="Get OA Session Status",
        description="Get the authenticated caller's central OA session state.",
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
        title="Start OA Session Login",
        description=(
            "Create a short-lived trusted authentication card for the authenticated caller. "
            "Credentials are entered only in that card and never in MCP arguments."
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
        challenge_ttl_seconds: Annotated[int, Field(ge=30, le=900)] = 300,
    ) -> dict[str, Any]:
        identity = _request_identity(identity_store)
        return await asyncio.to_thread(
            service.start_login,
            user_subject=identity["user_subject"],
            expected_principal_ref=identity["expected_principal_ref"],
            card_base_url=auth_card_base_url,
            ttl_seconds=challenge_ttl_seconds,
        )

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
    auth_server = create_auth_http_server(config=auth_config, application=auth_application)
    auth_thread = threading.Thread(
        target=auth_server.serve_forever,
        kwargs={"poll_interval": 0.25},
        name="agentbridge-auth-card",
        daemon=True,
    )
    auth_thread.start()
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
        auth_server.shutdown()
        auth_server.server_close()
        auth_thread.join(timeout=5)


def _request_identity(store: McpIdentityTokenStore) -> dict:
    access_token = get_access_token()
    if access_token is None:
        raise PermissionError("MCP request is not authenticated")
    return store.resolve_client(access_token.client_id, required_scopes={"oa:read"})


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
