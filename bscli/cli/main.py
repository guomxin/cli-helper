from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

from bscli.adapters.seeyon_central import SeeyonCentralAdapter
from bscli.adapters.seeyon_home import (
    parse_navigation_inventory,
    parse_pending_list,
    parse_template_list,
)
from bscli.adapters.seeyon_system import SEEYON_OA_URL, build_seeyon_profile
from bscli.auth.action_card import TrustedActionApplication
from bscli.auth.card import TrustedAuthApplication
from bscli.auth.field_card import TrustedFieldApplication
from bscli.auth.server import serve_auth_cards, validate_auth_server_config
from bscli.broker.credential import CredentialBroker
from bscli.browser.central import CentralBrowserWorker
from bscli.core.auth_challenges import AuthChallengeStore, ChallengeNotFound
from bscli.core.central_service import (
    CentralCapabilityService,
    challenge_response as _challenge_response,
    operation_response as _operation_response,
)
from bscli.core.config import ConfigStore, SystemProfile
from bscli.core.field_submissions import FieldSubmissionStore
from bscli.core.interactions import InteractionIntegrityError, InteractionNotFound
from bscli.core.internal_pki import InternalCertificateAuthorityStore
from bscli.core.mcp_identities import McpIdentityTokenStore
from bscli.core.network_security import INSECURE_PRIVATE_HTTP_WARNING
from bscli.core.operations import OperationConflictError, OperationStore
from bscli.core.session_secrets import SessionStateStore, WindowsDpapiProtector
from bscli.core.sessions import SessionPrincipalMismatch, SessionRegistry
from bscli.core.write_authorizations import WriteAuthorizationStore
from bscli.mcp.central import (
    serve_central_mcp,
    validate_central_mcp_server_config,
)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    home = Path(args.home)

    if args.area == "system":
        return handle_system(args, ConfigStore(home))
    if args.area == "capability":
        return handle_capability(args, home)
    if args.area == "session":
        return handle_central_session(args, home)
    if args.area == "auth":
        return handle_auth(args, home)
    if args.area == "operation":
        return handle_operation(args, home)
    if args.area == "interaction":
        return handle_interaction(args, home)
    if args.area == "adapter":
        return handle_adapter(args)
    if args.area == "pki":
        return handle_pki(args)
    if args.area == "mcp":
        return handle_mcp(args)
    parser.error("missing command")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bscli")
    parser.add_argument(
        "--home",
        default=str(Path.home() / ".bscli"),
        help="AgentBridge state directory",
    )
    subparsers = parser.add_subparsers(dest="area", required=True)

    system = subparsers.add_parser("system")
    system_sub = system.add_subparsers(dest="action", required=True)
    add = system_sub.add_parser("add")
    add.add_argument("id")
    add.add_argument("--name", required=True)
    add.add_argument("--url", required=True)
    add.add_argument("--origin", action="append", dest="origins")
    status = system_sub.add_parser("status")
    status.add_argument("id")
    system_sub.add_parser("list")
    system_sub.add_parser("init-seeyon-oa")

    capability = subparsers.add_parser("capability")
    capability_sub = capability.add_subparsers(dest="action", required=True)
    capability_list = capability_sub.add_parser("list")
    capability_list.add_argument("--system")
    capability_describe = capability_sub.add_parser("describe")
    capability_describe.add_argument("name")
    capability_invoke = capability_sub.add_parser("invoke")
    capability_invoke.add_argument("name")
    capability_invoke.add_argument("--user-subject", required=True)
    capability_invoke.add_argument("--json", default="{}")
    capability_invoke.add_argument("--idempotency-key")
    capability_invoke.add_argument("--request-id")
    capability_invoke.add_argument("--base-url")
    capability_invoke.add_argument("--card-base-url", default="http://127.0.0.1:8780")

    session = subparsers.add_parser("session")
    session_sub = session.add_subparsers(dest="action", required=True)
    session_status = session_sub.add_parser("status")
    session_status.add_argument("--system", required=True)
    session_status.add_argument("--user-subject", required=True)
    session_login = session_sub.add_parser("login")
    session_login.add_argument("--system", required=True)
    session_login.add_argument("--user-subject", required=True)
    session_login.add_argument("--expected-principal", required=True)
    session_login.add_argument("--base-url")
    session_login.add_argument("--card-base-url", default="http://127.0.0.1:8780")
    session_login.add_argument("--challenge-ttl", type=int, default=300)

    auth = subparsers.add_parser("auth")
    auth_sub = auth.add_subparsers(dest="action", required=True)
    auth_status = auth_sub.add_parser("status")
    auth_status.add_argument("challenge_id")
    auth_serve = auth_sub.add_parser("serve")
    auth_serve.add_argument("--host", default="127.0.0.1")
    auth_serve.add_argument("--port", type=int, default=8780)
    auth_serve.add_argument("--public-base-url")
    auth_serve.add_argument("--tls-cert")
    auth_serve.add_argument("--tls-key")
    auth_serve.add_argument(
        "--allow-insecure-private-http",
        action="store_true",
        help="allow literal private-IP HTTP for a restricted PoC network",
    )
    auth_serve.add_argument("--base-url")
    auth_serve.add_argument("--login-timeout", type=float, default=45)

    operation = subparsers.add_parser("operation")
    operation_sub = operation.add_subparsers(dest="action", required=True)
    operation_get = operation_sub.add_parser("get")
    operation_get.add_argument("operation_id")
    operation_list = operation_sub.add_parser("list")
    operation_list.add_argument("--user-subject")
    operation_list.add_argument("--limit", type=int, default=100)

    interaction = subparsers.add_parser("interaction")
    interaction_sub = interaction.add_subparsers(dest="action", required=True)
    interaction_get = interaction_sub.add_parser("get")
    interaction_get.add_argument("interaction_id")
    interaction_get.add_argument("--user-subject", required=True)
    interaction_get.add_argument("--base-url")
    interaction_get.add_argument("--card-base-url", default="http://127.0.0.1:8780")
    interaction_resume = interaction_sub.add_parser("resume")
    interaction_resume.add_argument("interaction_id")
    interaction_resume.add_argument("--user-subject", required=True)
    interaction_resume.add_argument("--idempotency-key")
    interaction_resume.add_argument("--base-url")
    interaction_resume.add_argument(
        "--card-base-url",
        default="http://127.0.0.1:8780",
    )

    adapter = subparsers.add_parser("adapter")
    adapter_sub = adapter.add_subparsers(dest="action", required=True)
    parse_home = adapter_sub.add_parser("parse-seeyon-home")
    parse_home.add_argument(
        "--kind",
        choices=["navigation", "pending", "templates"],
        required=True,
    )
    parse_home.add_argument("--html-file", required=True)
    parse_home.add_argument(
        "--base-url",
        default="http://10.10.50.110/seeyon/main.do?method=main",
    )

    pki = subparsers.add_parser("pki")
    pki_sub = pki.add_subparsers(dest="action", required=True)
    pki_issue = pki_sub.add_parser("issue-server")
    pki_issue.add_argument("--ip", required=True)
    pki_issue.add_argument(
        "--state-dir",
        default=str(Path.home() / ".agentbridge" / "pki"),
        help="DPAPI-protected internal root CA state directory",
    )
    pki_issue.add_argument("--output-dir", required=True)
    pki_issue.add_argument("--root-common-name", default="AgentBridge Internal Root CA")
    pki_issue.add_argument("--root-valid-days", type=int, default=3650)
    pki_issue.add_argument("--server-valid-days", type=int, default=397)
    pki_issue.add_argument("--force", action="store_true")

    mcp = subparsers.add_parser("mcp")
    mcp_sub = mcp.add_subparsers(dest="action", required=True)
    mcp_central_serve = mcp_sub.add_parser("central-serve")
    mcp_central_serve.add_argument("--host", default="127.0.0.1")
    mcp_central_serve.add_argument("--port", type=int, default=8790)
    mcp_central_serve.add_argument("--public-base-url")
    mcp_central_serve.add_argument("--tls-cert")
    mcp_central_serve.add_argument("--tls-key")
    mcp_central_serve.add_argument("--auth-host", default="127.0.0.1")
    mcp_central_serve.add_argument("--auth-port", type=int, default=8780)
    mcp_central_serve.add_argument("--auth-public-base-url")
    mcp_central_serve.add_argument("--auth-tls-cert")
    mcp_central_serve.add_argument("--auth-tls-key")
    mcp_central_serve.add_argument(
        "--allow-insecure-private-http",
        action="store_true",
        help="allow literal private-IP HTTP for a restricted PoC network",
    )
    mcp_central_serve.add_argument("--base-url")
    mcp_central_serve.add_argument("--login-timeout", type=float, default=45)
    mcp_central_serve.add_argument(
        "--session-keepalive-interval",
        type=float,
        default=0,
        help="seconds between central OA keepalive probes; 0 disables keepalive",
    )
    mcp_central_serve.add_argument(
        "--session-keepalive-lease",
        type=float,
        default=28_800,
        help="maximum seconds to keep OA alive after the latest real user activity",
    )
    mcp_token = mcp_sub.add_parser("token")
    mcp_token_sub = mcp_token.add_subparsers(dest="token_action", required=True)
    mcp_token_issue = mcp_token_sub.add_parser("issue")
    mcp_token_issue.add_argument("--user-subject", required=True)
    mcp_token_issue.add_argument("--expected-principal", required=True)
    mcp_token_issue.add_argument("--label")
    mcp_token_issue.add_argument("--ttl-hours", type=int, default=24)
    mcp_token_issue.add_argument(
        "--scope",
        action="append",
        choices=["oa:read", "oa:write:draft"],
    )
    mcp_token_list = mcp_token_sub.add_parser("list")
    mcp_token_list.add_argument("--user-subject")
    mcp_token_list.add_argument("--limit", type=int, default=100)
    mcp_token_revoke = mcp_token_sub.add_parser("revoke")
    mcp_token_revoke.add_argument("token_id")

    return parser


def handle_pki(args: argparse.Namespace) -> int:
    if args.action != "issue-server":
        raise ValueError(f"unknown PKI action: {args.action}")
    if sys.platform != "win32":
        print_json(
            _central_cli_error(
                "PKI_PLATFORM_UNSUPPORTED",
                "internal CA issuance requires Windows DPAPI on the administrator workstation",
            )
        )
        return 2
    try:
        store = InternalCertificateAuthorityStore(
            Path(args.state_dir).expanduser(),
            WindowsDpapiProtector(),
        )
        result = store.issue_server_certificate(
            server_ip=args.ip,
            output_dir=Path(args.output_dir).expanduser(),
            root_common_name=args.root_common_name,
            root_valid_days=args.root_valid_days,
            server_valid_days=args.server_valid_days,
            force=args.force,
        )
    except (FileExistsError, OSError, ValueError) as exc:
        print_json(_central_cli_error("PKI_ISSUE_FAILED", str(exc)))
        return 2
    print_json(result.as_dict())
    return 0


def handle_system(args: argparse.Namespace, store: ConfigStore) -> int:
    if args.action == "add":
        origins = args.origins or [_origin_from_url(args.url)]
        profile = SystemProfile(
            id=args.id,
            name=args.name,
            base_url=args.url,
            allowed_origins=origins,
        )
        store.save_system(profile)
        print_json(asdict(profile))
        return 0
    if args.action == "status":
        print_json(asdict(store.load_system(args.id)))
        return 0
    if args.action == "list":
        print_json([asdict(profile) for profile in store.list_systems()])
        return 0
    if args.action == "init-seeyon-oa":
        profile = build_seeyon_profile()
        store.save_system(profile)
        print_json(asdict(profile))
        return 0
    raise ValueError(f"unknown system action: {args.action}")


def handle_capability(args: argparse.Namespace, home: Path) -> int:
    service = CentralCapabilityService(
        home=home,
        base_url=_central_base_url(home, getattr(args, "base_url", None)),
        trusted_card_base_url=getattr(args, "card_base_url", "http://127.0.0.1:8780"),
    )
    if args.action == "list":
        print_json(service.list_capabilities(system=getattr(args, "system", None)))
        return 0
    if args.action == "describe":
        try:
            response = service.describe_capability(args.name)
        except KeyError as exc:
            print_json(_central_cli_error("CAPABILITY_NOT_FOUND", str(exc)))
            return 2
        print_json(response)
        return 0
    if args.action != "invoke":
        raise ValueError(f"unknown capability action: {args.action}")

    try:
        arguments = json.loads(args.json)
    except json.JSONDecodeError as exc:
        print_json(_central_cli_error("INVALID_INPUT", f"--json is not valid JSON: {exc}"))
        return 2
    if not isinstance(arguments, dict):
        print_json(_central_cli_error("INVALID_INPUT", "--json must decode to an object"))
        return 2

    try:
        response = service.invoke(
            user_subject=args.user_subject,
            capability_name=args.name,
            arguments=arguments,
            idempotency_key=args.idempotency_key,
            request_id=args.request_id,
        )
    except KeyError as exc:
        print_json(_central_cli_error("CAPABILITY_NOT_FOUND", str(exc)))
        return 2
    except (OperationConflictError, ValueError) as exc:
        print_json(_central_cli_error("INVALID_REQUEST", str(exc)))
        return 2
    print_json(response)
    return 0 if response["status"] in {"succeeded", "requires_user_action"} else 1


def handle_central_session(args: argparse.Namespace, home: Path) -> int:
    service = CentralCapabilityService(
        home=home,
        base_url=_central_base_url(home, getattr(args, "base_url", None)),
    )
    if args.action == "status":
        print_json(service.session_status(user_subject=args.user_subject, system_id=args.system))
        return 0
    if args.action != "login":
        raise ValueError(f"unknown session action: {args.action}")
    if args.system != "oa":
        print_json(
            _central_cli_error(
                "SYSTEM_NOT_SUPPORTED",
                f"central login is not implemented for {args.system}",
            )
        )
        return 2

    response = service.start_login(
        user_subject=args.user_subject,
        expected_principal_ref=args.expected_principal,
        card_base_url=args.card_base_url,
        ttl_seconds=args.challenge_ttl,
    )
    print_json(response)
    return 0


def handle_auth(args: argparse.Namespace, home: Path) -> int:
    challenge_store = AuthChallengeStore(_central_db_path(home))
    if args.action == "status":
        try:
            challenge = challenge_store.get(args.challenge_id)
        except ChallengeNotFound as exc:
            print_json(_central_cli_error("CHALLENGE_NOT_FOUND", str(exc)))
            return 2
        print_json(
            {
                "protocolVersion": "0.1",
                "status": challenge["state"],
                "challenge": _challenge_response(challenge),
            }
        )
        return 0
    if args.action != "serve":
        raise ValueError(f"unknown auth action: {args.action}")

    try:
        config = validate_auth_server_config(
            host=args.host,
            port=args.port,
            public_base_url=args.public_base_url,
            tls_cert=args.tls_cert,
            tls_key=args.tls_key,
            allow_insecure_private_http=args.allow_insecure_private_http,
        )
    except ValueError as exc:
        print_json(_central_cli_error("AUTH_SERVER_CONFIG_INVALID", str(exc)))
        return 2

    base_url = _central_base_url(home, args.base_url)
    sessions = SessionRegistry(_central_db_path(home), _central_profile_root(home))
    session_states = SessionStateStore(_central_session_secret_root(home))

    def adapter_factory(_challenge: dict):
        return SeeyonCentralAdapter(base_url=base_url)

    def worker_factory(session: dict, adapter: SeeyonCentralAdapter):
        return CentralBrowserWorker(
            profile_path=session["profile_path"],
            allowed_origins={adapter.origin},
            headless=True,
        )

    broker = CredentialBroker(
        challenge_store=challenge_store,
        session_registry=sessions,
        session_state_store=session_states,
        adapter_factory=adapter_factory,
        worker_factory=worker_factory,
        login_timeout_seconds=args.login_timeout,
    )
    application = TrustedAuthApplication(challenge_store=challenge_store, broker=broker)
    action_application = TrustedActionApplication(
        authorization_store=WriteAuthorizationStore(_central_db_path(home))
    )
    field_application = TrustedFieldApplication(
        submission_store=FieldSubmissionStore(_central_db_path(home))
    )
    startup = {
        "protocolVersion": "0.1",
        "status": "serving",
        "service": "trusted_authentication_card",
        "cardTypes": ["authentication", "business_input", "write_authorization"],
        "listen": {"host": config.host, "port": config.port},
        "publicBaseUrl": config.public_base_url,
        "tls": config.tls_cert is not None,
        "insecurePrivateHttp": config.insecure_private_http,
    }
    if config.insecure_private_http:
        startup["securityWarning"] = INSECURE_PRIVATE_HTTP_WARNING
        print(INSECURE_PRIVATE_HTTP_WARNING, file=sys.stderr, flush=True)
    print_json(startup)
    sys.stdout.flush()
    try:
        serve_auth_cards(
            config=config,
            application=application,
            action_application=action_application,
            field_application=field_application,
        )
    except KeyboardInterrupt:
        return 0
    return 0


def handle_operation(args: argparse.Namespace, home: Path) -> int:
    store = OperationStore(_central_db_path(home))
    if args.action == "get":
        try:
            operation = store.get(args.operation_id)
        except KeyError as exc:
            print_json(_central_cli_error("OPERATION_NOT_FOUND", str(exc)))
            return 2
        print_json({"protocolVersion": "0.1", "operation": _operation_response(operation)})
        return 0
    if args.action == "list":
        operations = store.list(user_subject=args.user_subject, limit=args.limit)
        print_json(
            {
                "protocolVersion": "0.1",
                "count": len(operations),
                "operations": [_operation_response(operation) for operation in operations],
            }
        )
        return 0
    raise ValueError(f"unknown operation action: {args.action}")


def handle_interaction(args: argparse.Namespace, home: Path) -> int:
    service = CentralCapabilityService(
        home=home,
        base_url=_central_base_url(home, args.base_url),
        trusted_card_base_url=args.card_base_url,
    )
    try:
        if args.action == "get":
            response = service.get_interaction(
                user_subject=args.user_subject,
                interaction_id=args.interaction_id,
            )
        elif args.action == "resume":
            response = service.resume_interaction(
                user_subject=args.user_subject,
                interaction_id=args.interaction_id,
                idempotency_key=args.idempotency_key,
            )
        else:
            raise ValueError(f"unknown interaction action: {args.action}")
    except InteractionNotFound as exc:
        print_json(_central_cli_error("INTERACTION_NOT_FOUND", str(exc)))
        return 2
    except (InteractionIntegrityError, OperationConflictError, ValueError) as exc:
        print_json(_central_cli_error("INTERACTION_INVALID", str(exc)))
        return 2
    print_json(response)
    return 0 if response.get("status") in {
        None,
        "succeeded",
        "requires_user_action",
        "already_resumed",
    } else 1


def handle_mcp(args: argparse.Namespace) -> int:
    home = Path(args.home)
    if args.action == "token":
        store = McpIdentityTokenStore(_central_db_path(home))
        if args.token_action == "issue":
            if args.ttl_hours < 1 or args.ttl_hours > 24 * 90:
                print_json(
                    _central_cli_error(
                        "INVALID_INPUT",
                        "--ttl-hours must be between 1 and 2160",
                    )
                )
                return 2
            try:
                sessions = SessionRegistry(_central_db_path(home), _central_profile_root(home))
                sessions.get_or_create(
                    user_subject=args.user_subject,
                    system_id="oa",
                    expected_principal_ref=args.expected_principal,
                )
                token = store.issue(
                    user_subject=args.user_subject,
                    expected_principal_ref=args.expected_principal,
                    label=args.label,
                    scopes=sorted({"oa:read", *(args.scope or [])}),
                    ttl_seconds=args.ttl_hours * 3600,
                )
            except (ValueError, SessionPrincipalMismatch) as exc:
                print_json(_central_cli_error("IDENTITY_BINDING_INVALID", str(exc)))
                return 2
            secret = token.pop("token")
            print_json(
                {
                    "protocolVersion": "0.1",
                    "status": "issued",
                    "identityToken": _mcp_identity_response(token),
                    "bearerToken": secret,
                    "warning": (
                        "The bearer token is shown once. Store it only in the trusted "
                        "MCP client configuration."
                    ),
                }
            )
            return 0
        if args.token_action == "list":
            try:
                tokens = store.list(user_subject=args.user_subject, limit=args.limit)
            except ValueError as exc:
                print_json(_central_cli_error("INVALID_INPUT", str(exc)))
                return 2
            print_json(
                {
                    "protocolVersion": "0.1",
                    "count": len(tokens),
                    "identityTokens": [_mcp_identity_response(token) for token in tokens],
                }
            )
            return 0
        if args.token_action == "revoke":
            try:
                token = store.revoke(args.token_id)
            except KeyError as exc:
                print_json(_central_cli_error("TOKEN_NOT_FOUND", str(exc)))
                return 2
            print_json(
                {
                    "protocolVersion": "0.1",
                    "status": "revoked",
                    "identityToken": _mcp_identity_response(token),
                }
            )
            return 0
        raise ValueError(f"unknown MCP token action: {args.token_action}")

    if args.action != "central-serve":
        raise ValueError(f"unknown mcp action: {args.action}")
    try:
        if args.login_timeout < 1 or args.login_timeout > 300:
            raise ValueError("--login-timeout must be between 1 and 300 seconds")
        if args.session_keepalive_interval < 0:
            raise ValueError("--session-keepalive-interval cannot be negative")
        if args.session_keepalive_interval and not (
            60 <= args.session_keepalive_interval <= 1_800
        ):
            raise ValueError(
                "--session-keepalive-interval must be 0 or between 60 and 1800 seconds"
            )
        if not 60 <= args.session_keepalive_lease <= 604_800:
            raise ValueError(
                "--session-keepalive-lease must be between 60 and 604800 seconds"
            )
        if (
            args.session_keepalive_interval
            and args.session_keepalive_lease < args.session_keepalive_interval
        ):
            raise ValueError(
                "--session-keepalive-lease cannot be shorter than the keepalive interval"
            )
        mcp_config = validate_central_mcp_server_config(
            host=args.host,
            port=args.port,
            public_base_url=args.public_base_url,
            tls_cert=args.tls_cert,
            tls_key=args.tls_key,
            allow_insecure_private_http=args.allow_insecure_private_http,
        )
        auth_config = validate_auth_server_config(
            host=args.auth_host,
            port=args.auth_port,
            public_base_url=args.auth_public_base_url,
            tls_cert=args.auth_tls_cert,
            tls_key=args.auth_tls_key,
            allow_insecure_private_http=args.allow_insecure_private_http,
        )
        if mcp_config.port == auth_config.port:
            raise ValueError("central MCP and authentication card services must use different ports")
    except ValueError as exc:
        print_json(_central_cli_error("CENTRAL_MCP_CONFIG_INVALID", str(exc)))
        return 2
    service = CentralCapabilityService(
        home=home,
        base_url=_central_base_url(home, args.base_url),
        trusted_card_base_url=auth_config.public_base_url,
    )
    identity_store = McpIdentityTokenStore(_central_db_path(home))
    insecure_private_http = (
        mcp_config.insecure_private_http or auth_config.insecure_private_http
    )
    startup = {
        "protocolVersion": "0.1",
        "status": "serving",
        "service": "agentbridge_oa_mcp",
        "mcpUrl": mcp_config.mcp_url,
        "authCardBaseUrl": auth_config.public_base_url,
        "transport": "streamable_http",
        "stateless": True,
        "authentication": "bearer_identity_token",
        "insecurePrivateHttp": insecure_private_http,
        "sessionKeepalive": {
            "enabled": args.session_keepalive_interval > 0,
            "intervalSeconds": args.session_keepalive_interval,
            "activityLeaseSeconds": args.session_keepalive_lease,
        },
    }
    if insecure_private_http:
        startup["securityWarning"] = INSECURE_PRIVATE_HTTP_WARNING
        print(INSECURE_PRIVATE_HTTP_WARNING, file=sys.stderr, flush=True)
    print_json(startup)
    sys.stdout.flush()
    try:
        serve_central_mcp(
            service=service,
            identity_store=identity_store,
            mcp_config=mcp_config,
            auth_config=auth_config,
            login_timeout_seconds=args.login_timeout,
            keepalive_interval_seconds=args.session_keepalive_interval,
            keepalive_activity_lease_seconds=args.session_keepalive_lease,
        )
    except KeyboardInterrupt:
        return 0
    return 0


def handle_adapter(args: argparse.Namespace) -> int:
    if args.action == "parse-seeyon-home":
        html = Path(args.html_file).read_text(encoding="utf-8")
        if args.kind == "pending":
            print_json(parse_pending_list(html, base_url=args.base_url))
            return 0
        if args.kind == "navigation":
            print_json(parse_navigation_inventory(html, base_url=args.base_url))
            return 0
        if args.kind == "templates":
            print_json(parse_template_list(html, base_url=args.base_url))
            return 0
    raise ValueError(f"unknown adapter action: {args.action}")


def _mcp_identity_response(token: dict) -> dict:
    return {
        "tokenId": token["token_id"],
        "userSubject": token["user_subject"],
        "expectedPrincipalRef": token["expected_principal_ref"],
        "label": token.get("label"),
        "scopes": token["scopes"],
        "state": token["state"],
        "createdAt": token["created_at"],
        "expiresAt": token["expires_at"],
        "lastUsedAt": token.get("last_used_at"),
        "revokedAt": token.get("revoked_at"),
    }


def _central_db_path(home: Path) -> Path:
    return home / "agentbridge.db"


def _central_profile_root(home: Path) -> Path:
    return home / "profiles"


def _central_session_secret_root(home: Path) -> Path:
    return home / "session-secrets"


def _central_base_url(home: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    try:
        return ConfigStore(home).load_system("oa").base_url
    except KeyError:
        return SEEYON_OA_URL


def _central_cli_error(code: str, message: str) -> dict:
    return {
        "protocolVersion": "0.1",
        "status": "failed",
        "error": {"code": code, "message": message},
    }


def print_json(value) -> None:
    try:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    except UnicodeEncodeError:
        print(json.dumps(value, ensure_ascii=True, indent=2))


def _origin_from_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("url must include scheme and host")
    return f"{parsed.scheme}://{parsed.netloc}"


if __name__ == "__main__":
    raise SystemExit(main())
