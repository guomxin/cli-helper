from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import io
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
import urllib.request
from urllib.parse import urlparse

from bscli.adapters.seeyon import SEEYON_OA_URL, build_seeyon_profile, register_seeyon_commands
from bscli.adapters.seeyon_central import (
    SeeyonCentralAdapter,
    SeeyonLoginRequired,
    build_central_capability_registry,
)
from bscli.adapters.seeyon_home import (
    parse_navigation_inventory,
    parse_pending_list,
    parse_template_list,
)
from bscli.adapters.seeyon_write import (
    append_oa_write_audit,
    build_oa_write_plan,
    list_write_action_specs,
    sanitize_oa_write_plan_for_audit,
)
from bscli.auth.card import TrustedAuthApplication
from bscli.auth.server import serve_auth_cards, validate_auth_server_config
from bscli.broker.credential import CredentialBroker
from bscli.browser.central import CentralBrowserWorker
from bscli.core.auth_challenges import AuthChallengeStore, ChallengeNotFound
from bscli.core.capability_runtime import CapabilityEngine, RequiresUserAction
from bscli.core.config import ConfigStore, SystemProfile
from bscli.core.discovered import DiscoveredApi, DiscoveredApiStore
from bscli.core.operations import OperationConflictError, OperationStore
from bscli.core.registry import CommandRegistry
from bscli.core.session_secrets import SessionStateStore
from bscli.core.sessions import SessionRegistry
from bscli.core.tool_manifest import export_tool_manifest
from bscli.core.trace import TraceStore
from bscli.daemon.app import DAEMON_TOKEN_FILENAME, serve
from bscli.mcp.server import BscliMcpServer


DEFAULT_OA_WRITE_SMOKE_KEYWORD = "__BSCLI_NO_MATCH_VALIDATION__"
OA_MEETING_CREATE_URL = "http://10.10.50.110/seeyon/meeting.do?method=editor&showTab=true"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = ConfigStore(Path(args.home))

    if args.area == "system":
        return handle_system(args, store)
    if args.area == "trace":
        return handle_trace(args, Path(args.home))
    if args.area == "daemon":
        return handle_daemon(args, Path(args.home))
    if args.area == "explore":
        return handle_explore(args)
    if args.area == "command":
        return handle_command(args)
    if args.area == "discovered":
        return handle_discovered(args, Path(args.home))
    if args.area == "capability":
        return handle_capability(args, Path(args.home))
    if args.area == "session":
        return handle_central_session(args, Path(args.home))
    if args.area == "auth":
        return handle_auth(args, Path(args.home))
    if args.area == "operation":
        return handle_operation(args, Path(args.home))
    if args.area == "adapter":
        return handle_adapter(args)
    if args.area == "tool":
        return handle_tool(args)
    if args.area == "mcp":
        return handle_mcp(args)
    if args.area == "oa":
        return handle_oa(args, Path(args.home))
    parser.error("missing command")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bscli")
    parser.add_argument(
        "--home",
        default=str(Path.home() / ".bscli"),
        help="BSCLI state directory",
    )
    subparsers = parser.add_subparsers(dest="area")

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

    trace = subparsers.add_parser("trace")
    trace_sub = trace.add_subparsers(dest="action", required=True)
    trace_sub.add_parser("list")
    show = trace_sub.add_parser("show")
    show.add_argument("run_id")

    daemon = subparsers.add_parser("daemon")
    daemon_sub = daemon.add_subparsers(dest="action", required=True)
    serve_cmd = daemon_sub.add_parser("serve")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8765)
    status_cmd = daemon_sub.add_parser("status")
    status_cmd.add_argument("--daemon-url", default="http://127.0.0.1:8765")

    explore = subparsers.add_parser("explore")
    explore_sub = explore.add_subparsers(dest="action", required=True)
    snapshot = explore_sub.add_parser("dom-snapshot")
    snapshot.add_argument("system")
    snapshot.add_argument("--selector", default="body")
    snapshot.add_argument("--daemon-url", default="http://127.0.0.1:8765")

    command = subparsers.add_parser("command")
    command_sub = command.add_subparsers(dest="action", required=True)
    list_cmd = command_sub.add_parser("list")
    list_cmd.add_argument("system", nargs="?")
    run = command_sub.add_parser("run")
    run.add_argument("system")
    run.add_argument("command")
    run.add_argument("--json", default="{}")
    run.add_argument("--timeout", type=float, default=30.0)
    run.add_argument("--daemon-url", default="http://127.0.0.1:8765")

    discovered = subparsers.add_parser("discovered")
    discovered_sub = discovered.add_subparsers(dest="action", required=True)
    discovered_list = discovered_sub.add_parser("list")
    discovered_list.add_argument("system")
    discovered_show = discovered_sub.add_parser("show")
    discovered_show.add_argument("system")
    discovered_show.add_argument("name")
    discovered_run = discovered_sub.add_parser("run")
    discovered_run.add_argument("system")
    discovered_run.add_argument("name")
    discovered_run.add_argument("--json", default="{}")
    discovered_run.add_argument("--confirm", action="store_true")
    discovered_run.add_argument("--timeout", type=float, default=30.0)
    discovered_run.add_argument("--daemon-url", default="http://127.0.0.1:8765")

    tool = subparsers.add_parser("tool")
    tool_sub = tool.add_subparsers(dest="action", required=True)
    manifest = tool_sub.add_parser("manifest")
    manifest.add_argument("system", nargs="?")

    mcp = subparsers.add_parser("mcp")
    mcp_sub = mcp.add_subparsers(dest="action", required=True)
    mcp_serve = mcp_sub.add_parser("serve")
    mcp_serve.add_argument("--daemon-url", default="http://127.0.0.1:8765")
    mcp_serve.add_argument("--once", action="store_true", help="Handle one JSON-RPC line then exit")

    oa = subparsers.add_parser("oa")
    oa_sub = oa.add_subparsers(dest="oa_area", required=True)
    _build_oa_parser(oa_sub)

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
    auth_serve.add_argument("--base-url")
    auth_serve.add_argument("--login-timeout", type=float, default=45)

    operation = subparsers.add_parser("operation")
    operation_sub = operation.add_subparsers(dest="action", required=True)
    operation_get = operation_sub.add_parser("get")
    operation_get.add_argument("operation_id")
    operation_list = operation_sub.add_parser("list")
    operation_list.add_argument("--user-subject")
    operation_list.add_argument("--limit", type=int, default=100)

    adapter = subparsers.add_parser("adapter")
    adapter_sub = adapter.add_subparsers(dest="action", required=True)
    parse_home = adapter_sub.add_parser("parse-seeyon-home")
    parse_home.add_argument("--kind", choices=["navigation", "pending", "templates"], required=True)
    parse_home.add_argument("--html-file", required=True)
    parse_home.add_argument("--base-url", default="http://10.10.50.110/seeyon/main.do?method=main")

    return parser


def handle_capability(args: argparse.Namespace, home: Path) -> int:
    registry = build_central_capability_registry()
    if args.action == "list":
        print_json(
            {
                "protocolVersion": "0.1",
                "capabilities": [
                    spec.to_dict()
                    for spec in registry.list(system=getattr(args, "system", None))
                ],
            }
        )
        return 0
    if args.action == "describe":
        try:
            capability = registry.describe(args.name)
        except KeyError as exc:
            print_json(_central_cli_error("CAPABILITY_NOT_FOUND", str(exc)))
            return 2
        print_json({"protocolVersion": "0.1", "capability": capability})
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

    operation_store = OperationStore(_central_db_path(home))
    sessions = SessionRegistry(_central_db_path(home), _central_profile_root(home))
    session_states = SessionStateStore(_central_session_secret_root(home))
    engine = CapabilityEngine(registry=registry, operation_store=operation_store)
    if args.name == "oa.template.list":
        adapter = SeeyonCentralAdapter(base_url=_central_base_url(home, args.base_url))

        def list_templates(_context, _arguments):
            session = sessions.find(user_subject=args.user_subject, system_id="oa")
            if session is None or session["state"] != "active":
                raise _login_required_action(args.user_subject, session)
            state = session_states.load(session["session_id"])
            if state is None:
                sessions.mark_expired(session["session_id"], "Encrypted session state is missing.")
                raise _login_required_action(args.user_subject, session)
            try:
                with CentralBrowserWorker(
                    profile_path=session["profile_path"],
                    allowed_origins={adapter.origin},
                    headless=True,
                ) as worker:
                    worker.restore_session_state(state)
                    result = adapter.list_templates(worker)
                    session_states.save(session["session_id"], worker.capture_session_state())
                    return result
            except SeeyonLoginRequired as exc:
                sessions.mark_expired(session["session_id"], str(exc))
                session_states.delete(session["session_id"])
                raise _login_required_action(args.user_subject, session) from exc

        engine.register_handler(args.name, list_templates)

    try:
        response = engine.invoke(
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
    sessions = SessionRegistry(_central_db_path(home), _central_profile_root(home))
    if args.action == "status":
        session = sessions.find(user_subject=args.user_subject, system_id=args.system)
        if session is None:
            print_json(
                {
                    "protocolVersion": "0.1",
                    "status": "not_found",
                    "systemId": args.system,
                    "userSubject": args.user_subject,
                }
            )
        else:
            print_json(_session_response(session))
        return 0
    if args.action != "login":
        raise ValueError(f"unknown session action: {args.action}")
    if args.system != "oa":
        print_json(_central_cli_error("SYSTEM_NOT_SUPPORTED", f"central login is not implemented for {args.system}"))
        return 2

    session = sessions.get_or_create(
        user_subject=args.user_subject,
        system_id=args.system,
        expected_principal_ref=args.expected_principal,
    )
    adapter = SeeyonCentralAdapter(base_url=_central_base_url(home, args.base_url))
    contract = adapter.authentication_contract()
    challenge = AuthChallengeStore(_central_db_path(home)).create(
        user_subject=session["user_subject"],
        system_id=session["system_id"],
        system_name=contract["system_name"],
        session_id=session["session_id"],
        expected_principal_ref=session["expected_principal_ref"],
        origin=contract["origin"],
        page_fingerprint=contract["page_fingerprint"],
        nonce=None,
        fields=contract["fields"],
        card_base_url=args.card_base_url,
        ttl_seconds=args.challenge_ttl,
    )
    print_json(
        {
            "protocolVersion": "0.1",
            "status": "requires_user_action",
            "sessionId": session["session_id"],
            "challenge": _challenge_response(challenge),
            "nextAction": {
                "type": "open_authentication_card",
                "challengeId": challenge["challenge_id"],
                "cardUrl": challenge["card_url"],
            },
        }
    )
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
    print_json(
        {
            "protocolVersion": "0.1",
            "status": "serving",
            "service": "trusted_authentication_card",
            "listen": {"host": config.host, "port": config.port},
            "publicBaseUrl": config.public_base_url,
            "tls": config.tls_cert is not None,
        }
    )
    sys.stdout.flush()
    try:
        serve_auth_cards(config=config, application=application)
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


def _login_required_action(user_subject: str, session: dict | None) -> RequiresUserAction:
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


def _session_response(session: dict) -> dict:
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


def _operation_response(operation: dict) -> dict:
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


def _challenge_response(challenge: dict) -> dict:
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


def _central_cli_error(code: str, message: str) -> dict:
    return {
        "protocolVersion": "0.1",
        "status": "failed",
        "error": {"code": code, "message": message},
    }


def _build_oa_parser(oa_sub) -> None:
    status = oa_sub.add_parser("status")
    status.set_defaults(oa_command="session_status")
    _add_daemon_options(status)

    doctor = oa_sub.add_parser("doctor")
    doctor.set_defaults(oa_command="doctor")
    _add_daemon_options(doctor)
    _add_output_options(doctor)

    capabilities = oa_sub.add_parser("capabilities")
    capabilities.set_defaults(oa_command="capability_map")
    _add_daemon_options(capabilities)
    _add_output_options(capabilities)

    page = oa_sub.add_parser("page")
    page_sub = page.add_subparsers(dest="oa_action", required=True)
    snapshot = page_sub.add_parser("snapshot")
    snapshot.set_defaults(oa_command="current_page_snapshot")
    _add_daemon_options(snapshot)
    _add_output_options(snapshot)
    inventory = page_sub.add_parser("inventory")
    inventory.set_defaults(oa_command="page_inventory")
    _add_daemon_options(inventory)
    _add_output_options(inventory)
    script_smoke = page_sub.add_parser("script-smoke")
    script_smoke.set_defaults(oa_command="bridge_script_smoke")
    script_smoke.add_argument("--marker", default="bscli-page-script-smoke")
    _add_daemon_options(script_smoke)
    _add_output_options(script_smoke)

    nav = oa_sub.add_parser("nav")
    nav_sub = nav.add_subparsers(dest="oa_action", required=True)
    nav_list = nav_sub.add_parser("list")
    nav_list.set_defaults(oa_command="navigation_inventory")
    _add_daemon_options(nav_list)
    _add_output_options(nav_list)

    history = oa_sub.add_parser("history")
    history_sub = history.add_subparsers(dest="oa_action", required=True)
    _add_history_parser(history_sub)

    matter = oa_sub.add_parser("matter")
    matter_sub = matter.add_subparsers(dest="oa_matter_action", required=True)
    matter_profile = matter_sub.add_parser("profile")
    matter_profile.set_defaults(oa_command="matter_profile")
    matter_profile.add_argument(
        "--kind",
        choices=["sent", "done", "tracked", "all"],
        default="all",
        help="Historical collections to profile before matter grouping; defaults to all.",
    )
    matter_profile.add_argument("--keyword")
    _add_daemon_options(matter_profile)
    _add_output_options(matter_profile)
    matter_matrix = matter_sub.add_parser("matrix")
    matter_matrix.set_defaults(oa_command="matter_matrix")
    matter_matrix.add_argument(
        "--kind",
        choices=["sent", "done", "tracked", "all"],
        default="all",
        help="Historical collections to profile before building the matter capability matrix; defaults to all.",
    )
    matter_matrix.add_argument("--keyword")
    _add_daemon_options(matter_matrix)
    _add_output_options(matter_matrix)
    matter_inspect = matter_sub.add_parser("inspect")
    matter_inspect.set_defaults(oa_command="matter_inspect")
    matter_target = matter_inspect.add_mutually_exclusive_group(required=True)
    matter_target.add_argument("--id", dest="matter_id")
    matter_target.add_argument("--name", dest="matter_name")
    matter_inspect.add_argument(
        "--kind",
        choices=["sent", "done", "tracked", "all"],
        default="all",
        help="Historical collections to profile before matter lookup; defaults to all.",
    )
    matter_inspect.add_argument("--keyword")
    matter_inspect.add_argument("--with-launch", action="store_true", dest="with_launch")
    matter_inspect.add_argument("--settle-ms", type=int, dest="settle_ms")
    _add_daemon_options(matter_inspect)
    _add_output_options(matter_inspect)
    matter_preflight = matter_sub.add_parser("preflight")
    matter_preflight.set_defaults(oa_command="matter_preflight")
    matter_preflight_target = matter_preflight.add_mutually_exclusive_group(required=True)
    matter_preflight_target.add_argument("--id", dest="workflow_id")
    matter_preflight_target.add_argument("--keyword")
    matter_preflight.add_argument("--intent", required=True, choices=["approve", "archive"])
    matter_preflight.add_argument("--opinion", default="")
    matter_preflight.add_argument("--text-limit", type=int, dest="text_limit")
    _add_daemon_options(matter_preflight)
    _add_output_options(matter_preflight)
    matter_execute = matter_sub.add_parser("execute")
    matter_execute.set_defaults(oa_command="matter_execute")
    matter_execute_target = matter_execute.add_mutually_exclusive_group(required=True)
    matter_execute_target.add_argument("--id", dest="workflow_id")
    matter_execute_target.add_argument("--keyword")
    matter_execute_target.add_argument("--meeting-id", dest="meeting_id")
    matter_execute_target.add_argument("--source-url", dest="source_url")
    matter_execute.add_argument("--intent", required=True, choices=["approve", "archive", "join", "not_join", "pending"])
    matter_execute.add_argument("--opinion", default="")
    matter_execute.add_argument("--feedback", default="")
    matter_execute.add_argument("--text-limit", type=int, dest="text_limit")
    matter_execute.add_argument("--proxy-id", dest="proxy_id")
    matter_execute.add_argument("--verify-wait", type=float, dest="verify_wait")
    matter_execute.add_argument("--business-form-wait-ms", type=int, dest="business_form_wait_ms")
    matter_execute.add_argument("--script-timeout-ms", type=int, dest="script_timeout_ms")
    matter_execute.add_argument("--after-submit-wait-ms", type=int, dest="after_submit_wait_ms")
    matter_execute.add_argument("--confirm", action="store_true")
    _add_daemon_options(matter_execute)
    _add_output_options(matter_execute)
    for action, command in (
        ("launch-dry-run", "matter_launch_dry_run"),
        ("launch-save-draft", "matter_launch_save_draft"),
    ):
        matter_launch = matter_sub.add_parser(action)
        matter_launch.set_defaults(oa_command=command, oa_matter_action=action)
        matter_launch_target = matter_launch.add_mutually_exclusive_group(required=True)
        matter_launch_target.add_argument("--id", dest="matter_id")
        matter_launch_target.add_argument("--name", dest="matter_name")
        matter_launch.add_argument(
            "--field",
            action="append",
            default=[],
            help="Field assignment in name=value form; may be repeated.",
        )
        matter_launch.add_argument("--fields-json", default="{}", help="JSON object of field name/id/label to value.")
        matter_launch.add_argument(
            "--kind",
            choices=["sent", "done", "tracked", "all"],
            default="all",
            help="Historical collections to profile before matter lookup; defaults to all.",
        )
        matter_launch.add_argument("--settle-ms", type=int, dest="settle_ms")
        if action == "launch-save-draft":
            matter_launch.add_argument("--confirm", action="store_true")
            matter_launch.add_argument("--keep-tab", action="store_true", dest="keep_tab")
        _add_daemon_options(matter_launch)
        _add_output_options(matter_launch)

    detail = oa_sub.add_parser("detail")
    detail_sub = detail.add_subparsers(dest="oa_action", required=True)
    detail_read = detail_sub.add_parser("read")
    detail_read.set_defaults(oa_command="detail_read")
    detail_read.add_argument("--url", required=True)
    _add_detail_options(detail_read)
    _add_daemon_options(detail_read)
    _add_output_options(detail_read)
    for action in ("attachments", "workflow", "actions"):
        detail_projection = detail_sub.add_parser(action)
        detail_projection.set_defaults(oa_command="detail_read", oa_detail_projection=action)
        detail_projection.add_argument("--url", required=True)
        _add_daemon_options(detail_projection)
        _add_output_options(detail_projection)

    workflow = oa_sub.add_parser("workflow")
    workflow_sub = workflow.add_subparsers(dest="oa_action", required=True)
    _add_workflow_parser(workflow_sub)

    inbox = oa_sub.add_parser("inbox")
    inbox_sub = inbox.add_subparsers(dest="oa_action", required=True)
    _add_inbox_parser(inbox_sub)

    pending = oa_sub.add_parser("pending")
    pending_sub = pending.add_subparsers(dest="oa_action", required=True)
    _add_collection_parser(
        pending_sub,
        list_command="pending_list_api",
        show_command="pending_detail",
        show_arg_name="affair_id",
    )
    pending_submit = pending_sub.add_parser("submit")
    pending_submit.set_defaults(oa_pending_submit=True, oa_command="pending_list_api")
    pending_submit.add_argument("--keyword", required=True)
    pending_submit.add_argument("--action", required=True)
    pending_submit.add_argument("--opinion", default="")
    pending_submit.add_argument("--confirm", action="store_true")
    pending_submit.add_argument("--verify-wait", type=float, default=8.0)
    _add_daemon_options(pending_submit)
    _add_output_options(pending_submit)

    sent = oa_sub.add_parser("sent")
    sent_sub = sent.add_subparsers(dest="oa_action", required=True)
    _add_collection_parser(
        sent_sub,
        list_command="sent_list_api",
        show_command=None,
        show_arg_name=None,
    )

    template = oa_sub.add_parser("template")
    template_sub = template.add_subparsers(dest="oa_action", required=True)
    _add_collection_parser(
        template_sub,
        list_command="template_list_api",
        show_command="template_detail",
        show_arg_name="template_id",
        category_filter=True,
    )
    template_match = template_sub.add_parser("match")
    template_match.set_defaults(oa_command="template_match")
    template_match.add_argument(
        "--kind",
        choices=["sent", "done", "tracked", "all"],
        default="done",
        help="Historical collection to profile before matching; defaults to done.",
    )
    template_match.add_argument("--keyword")
    _add_daemon_options(template_match)
    _add_output_options(template_match)

    launch = oa_sub.add_parser("launch")
    launch_sub = launch.add_subparsers(dest="oa_action", required=True)
    launch_inspect = launch_sub.add_parser("inspect")
    launch_inspect.set_defaults(oa_command="launch_inspect")
    launch_inspect.add_argument("--template-id", dest="template_id")
    launch_inspect.add_argument("--url")
    launch_inspect.add_argument("--settle-ms", type=int, dest="settle_ms")
    _add_daemon_options(launch_inspect)
    _add_output_options(launch_inspect)
    for action, command in (("dry-run", "launch_dry_run"), ("save-draft", "launch_save_draft")):
        launch_write = launch_sub.add_parser(action)
        launch_write.set_defaults(oa_command=command)
        launch_write.add_argument("--template-id", dest="template_id")
        launch_write.add_argument("--url")
        launch_write.add_argument(
            "--field",
            action="append",
            default=[],
            help="Field assignment in name=value form; may be repeated.",
        )
        launch_write.add_argument("--fields-json", default="{}", help="JSON object of field name/id/label to value.")
        launch_write.add_argument("--settle-ms", type=int, dest="settle_ms")
        if action == "save-draft":
            launch_write.add_argument("--confirm", action="store_true")
            launch_write.add_argument("--keep-tab", action="store_true", dest="keep_tab")
        _add_daemon_options(launch_write)
        _add_output_options(launch_write)

    audit = oa_sub.add_parser("audit")
    audit_sub = audit.add_subparsers(dest="oa_audit_area", required=True)
    for area, kind in (("writes", "write_plans"), ("verifications", "write_verifications")):
        area_parser = audit_sub.add_parser(area)
        area_sub = area_parser.add_subparsers(dest="oa_audit_action", required=True)
        list_parser = area_sub.add_parser("list")
        list_parser.set_defaults(oa_audit_kind=kind, oa_audit_action="list")
        _add_output_options(list_parser)
        show_parser = area_sub.add_parser("show")
        show_parser.set_defaults(oa_audit_kind=kind, oa_audit_action="show")
        show_parser.add_argument("--index", type=int, required=True)
        _add_output_options(show_parser)
        search_parser = area_sub.add_parser("search")
        search_parser.set_defaults(oa_audit_kind=kind, oa_audit_action="search")
        search_parser.add_argument("--affair-id")
        search_parser.add_argument("--action")
        search_parser.add_argument("--status")
        _add_output_options(search_parser)

    probe = oa_sub.add_parser("probe")
    probe_sub = probe.add_subparsers(dest="oa_action", required=True)
    for action, command in (
        ("install", "network_probe_install"),
        ("logs", "network_log_snapshot"),
        ("candidates", "network_api_candidates"),
    ):
        probe_cmd = probe_sub.add_parser(action)
        probe_cmd.set_defaults(oa_command=command)
        _add_daemon_options(probe_cmd)
        _add_output_options(probe_cmd)

    api = oa_sub.add_parser("api")
    api_sub = api.add_subparsers(dest="oa_action", required=True)
    for action, command in (("inspect", "api_inspect"), ("replay", "api_replay")):
        api_cmd = api_sub.add_parser(action)
        api_cmd.set_defaults(oa_command=command)
        api_cmd.add_argument("--method", required=True)
        api_cmd.add_argument("--url", required=True)
        api_cmd.add_argument("--headers", default="{}")
        api_cmd.add_argument("--body")
        api_cmd.add_argument("--text-limit", type=int, dest="text_limit")
        _add_daemon_options(api_cmd)
        _add_output_options(api_cmd)
    api_save = api_sub.add_parser("save")
    api_save.set_defaults(oa_command="api_save")
    api_save.add_argument("name")
    api_save.add_argument("--method", required=True)
    api_save.add_argument("--url", required=True)
    api_save.add_argument("--description", default="")
    api_save.add_argument("--headers", default="{}")
    api_save.add_argument("--body")
    _add_daemon_options(api_save)
    _add_output_options(api_save)

    discovered = oa_sub.add_parser("discovered")
    discovered_sub = discovered.add_subparsers(dest="oa_action", required=True)
    discovered_list = discovered_sub.add_parser("list")
    discovered_list.set_defaults(oa_command="discovered_list")
    _add_output_options(discovered_list)
    discovered_show = discovered_sub.add_parser("show")
    discovered_show.set_defaults(oa_command="discovered_show")
    discovered_show.add_argument("name")
    _add_output_options(discovered_show)
    discovered_run = discovered_sub.add_parser("run")
    discovered_run.set_defaults(oa_command="discovered_run")
    discovered_run.add_argument("name")
    discovered_run.add_argument("--json", default="{}")
    discovered_run.add_argument("--confirm", action="store_true")
    _add_daemon_options(discovered_run)
    _add_output_options(discovered_run)

    meeting = oa_sub.add_parser("meeting")
    meeting_sub = meeting.add_subparsers(dest="oa_meeting_area", required=True)
    create = meeting_sub.add_parser("create")
    create_sub = create.add_subparsers(dest="oa_meeting_create_action", required=True)
    create_inspect = create_sub.add_parser("inspect")
    create_inspect.set_defaults(oa_meeting_create_mode="inspect")
    create_inspect.add_argument("--settle-ms", type=int, dest="settle_ms")
    _add_daemon_options(create_inspect)
    _add_output_options(create_inspect)
    create_dry_run = create_sub.add_parser("dry-run")
    create_dry_run.set_defaults(oa_meeting_create_mode="dry-run")
    create_dry_run.add_argument(
        "--field",
        action="append",
        default=[],
        help="Meeting field assignment in name=value form; may be repeated.",
    )
    create_dry_run.add_argument("--fields-json", default="{}", help="JSON object of meeting field name/id/label to value.")
    create_dry_run.add_argument("--settle-ms", type=int, dest="settle_ms")
    _add_daemon_options(create_dry_run)
    _add_output_options(create_dry_run)
    create_execute = create_sub.add_parser("execute")
    create_execute.set_defaults(oa_meeting_create_mode="execute")
    create_execute.add_argument("--subject", required=True)
    create_execute.add_argument("--room", required=True)
    create_execute.add_argument("--start", required=True)
    create_execute.add_argument("--end", required=True)
    create_execute.add_argument(
        "--attendee",
        action="append",
        default=[],
        help="Optional attendee id in OA source format; defaults to current user when omitted.",
    )
    create_execute.add_argument("--confirm", action="store_true")
    _add_daemon_options(create_execute)
    _add_output_options(create_execute)
    reply = meeting_sub.add_parser("reply")
    reply_sub = reply.add_subparsers(dest="oa_meeting_action", required=True)
    for mode in ("dry-run", "execute"):
        reply_cmd = reply_sub.add_parser(mode)
        reply_cmd.set_defaults(oa_meeting_reply_mode=mode)
        reply_cmd.add_argument("--id", required=True, help="Pending affair id to resolve to a meeting.")
        reply_cmd.add_argument("--meeting-id")
        reply_cmd.add_argument("--source-url", default="")
        reply_cmd.add_argument("--attitude", default="join", help="join, not_join, or pending.")
        reply_cmd.add_argument("--feedback", default="")
        if mode == "execute":
            reply_cmd.add_argument("--confirm", action="store_true")
            reply_cmd.add_argument("--verify-wait", type=float, default=2.0)
        _add_daemon_options(reply_cmd)
        _add_output_options(reply_cmd)

    write = oa_sub.add_parser("write")
    write_sub = write.add_subparsers(dest="oa_action", required=True)
    actions = write_sub.add_parser("actions")
    actions.set_defaults(oa_write_actions=True)
    _add_output_options(actions)
    smoke = write_sub.add_parser("smoke")
    smoke.set_defaults(oa_write_smoke=True)
    smoke.add_argument("--keyword", default=DEFAULT_OA_WRITE_SMOKE_KEYWORD)
    smoke.add_argument("--allow-custom-keyword", action="store_true")
    smoke.add_argument("--action", default="ContinueSubmit")
    smoke.add_argument("--opinion", default="read")
    smoke.add_argument("--verify-wait", type=float, default=0.0)
    _add_daemon_options(smoke)
    _add_output_options(smoke)
    capabilities = write_sub.add_parser("capabilities")
    capabilities.set_defaults(oa_write_capabilities=True)
    capabilities.add_argument(
        "--type",
        choices=["pending"],
        default="pending",
        dest="workflow_type",
        help="Workflow collection to inspect; currently pending is supported.",
    )
    capabilities.add_argument("--keyword")
    _add_daemon_options(capabilities)
    _add_output_options(capabilities)
    discover = write_sub.add_parser("discover")
    discover.set_defaults(oa_write_discover=True)
    discover.add_argument("--source", choices=["history", "launch"], default="history")
    discover.add_argument(
        "--kind",
        choices=["sent", "done", "tracked"],
        default="done",
        help="Historical collection to sample; defaults to done.",
    )
    discover.add_argument("--keyword")
    discover.add_argument("--template-id", dest="template_id")
    discover.add_argument("--url")
    discover.add_argument("--settle-ms", type=int, dest="settle_ms")
    discover.add_argument("--deep-limit", type=int, dest="deep_limit")
    discover.add_argument("--text-limit", type=int, dest="text_limit")
    _add_daemon_options(discover)
    _add_output_options(discover)
    endpoints = write_sub.add_parser("endpoints")
    endpoints.set_defaults(oa_write_endpoints=True)
    endpoints.add_argument("--affair-id", required=True)
    endpoints.add_argument("--action", required=True)
    endpoints.add_argument("--source-url", default="")
    _add_daemon_options(endpoints)
    _add_output_options(endpoints)
    preflight = write_sub.add_parser("preflight")
    preflight.set_defaults(oa_write_preflight=True)
    preflight.add_argument(
        "--type",
        choices=["pending"],
        default="pending",
        dest="workflow_type",
        help="Workflow collection to resolve; currently pending is supported.",
    )
    preflight.add_argument("--affair-id", required=True)
    preflight.add_argument("--action", required=True)
    preflight.add_argument("--opinion", default="")
    preflight.add_argument("--source-url", default="")
    _add_daemon_options(preflight)
    _add_output_options(preflight)
    prepare = write_sub.add_parser("prepare")
    prepare.set_defaults(oa_write_prepare=True)
    prepare.add_argument(
        "--type",
        choices=["pending"],
        default="pending",
        dest="workflow_type",
        help="Workflow collection to resolve; currently pending is supported.",
    )
    prepare.add_argument("--affair-id", required=True)
    prepare.add_argument("--action", required=True)
    prepare.add_argument("--opinion", default="")
    prepare.add_argument("--source-url", default="")
    prepare.add_argument("--text-limit", type=int, dest="text_limit")
    _add_daemon_options(prepare)
    _add_output_options(prepare)
    for mode in ("draft", "dry-run", "execute"):
        write_cmd = write_sub.add_parser(mode)
        write_cmd.set_defaults(oa_write_mode=mode)
        write_cmd.add_argument("--affair-id", required=True)
        write_cmd.add_argument("--action", required=True)
        write_cmd.add_argument("--opinion", default="")
        write_cmd.add_argument("--source-url", default="")
        if mode == "dry-run":
            _add_daemon_options(write_cmd)
        if mode == "execute":
            write_cmd.add_argument("--confirm", action="store_true")
            write_cmd.add_argument("--script-timeout-ms", type=int, dest="script_timeout_ms")
            write_cmd.add_argument("--business-form-wait-ms", type=int, dest="business_form_wait_ms")
            write_cmd.add_argument("--after-submit-wait-ms", type=int, dest="after_submit_wait_ms")
            _add_daemon_options(write_cmd)
        _add_output_options(write_cmd)


def _add_history_parser(subparsers) -> None:
    sections = subparsers.add_parser("sections")
    sections.set_defaults(oa_history_action="sections")
    sections.add_argument("--kind", choices=["sent", "done", "tracked"])
    _add_daemon_options(sections)
    _add_output_options(sections)

    for action in ("list", "search", "export"):
        parser = subparsers.add_parser(action)
        parser.set_defaults(oa_history_action=action)
        parser.add_argument(
            "--kind",
            choices=["sent", "done", "tracked"],
            default="done",
            help="Historical collection to read; defaults to done.",
        )
        if action == "search":
            parser.add_argument("--keyword", required=True)
        else:
            parser.add_argument("--keyword")
        _add_daemon_options(parser)
        _add_output_options(parser, default_format="csv" if action == "export" else "json")
    for action in ("profile", "clusters"):
        parser = subparsers.add_parser(action)
        parser.set_defaults(oa_history_action=action)
        parser.add_argument(
            "--kind",
            choices=["sent", "done", "tracked", "all"],
            default="done",
            help="Historical collection to profile; defaults to done.",
        )
        parser.add_argument("--keyword")
        _add_daemon_options(parser)
        _add_output_options(parser)


def _add_workflow_parser(subparsers) -> None:
    for action in ("list", "search", "export", "brief"):
        parser = subparsers.add_parser(action)
        parser.set_defaults(oa_workflow_action=action)
        _add_workflow_type_option(parser)
        if action == "search":
            parser.add_argument("--keyword", required=True)
        else:
            parser.add_argument("--keyword")
        _add_daemon_options(parser)
        _add_output_options(parser, default_format="csv" if action == "export" else "json")

    inspect = subparsers.add_parser("inspect")
    inspect.set_defaults(oa_workflow_action="inspect")
    inspect.add_argument("--url")
    inspect.add_argument("--id", dest="workflow_id")
    _add_workflow_type_option(inspect)
    _add_detail_options(inspect)
    _add_daemon_options(inspect)
    _add_output_options(inspect)

    evidence = subparsers.add_parser("evidence")
    evidence.set_defaults(oa_workflow_action="evidence")
    evidence.add_argument("--url")
    evidence.add_argument("--id", dest="workflow_id")
    _add_workflow_type_option(evidence)
    evidence.add_argument("--text-limit", type=int, dest="text_limit")
    _add_daemon_options(evidence)
    _add_output_options(evidence)

    timeline = subparsers.add_parser("timeline")
    timeline.set_defaults(oa_workflow_action="timeline")
    timeline.add_argument("--url")
    timeline.add_argument("--id", dest="workflow_id")
    _add_workflow_type_option(timeline)
    _add_daemon_options(timeline)
    _add_output_options(timeline)

    details = subparsers.add_parser("details")
    details.set_defaults(oa_workflow_action="details")
    _add_workflow_type_option(details)
    details.add_argument("--keyword")
    _add_detail_options(details)
    _add_daemon_options(details)
    _add_output_options(details, default_format="json")

    detail = subparsers.add_parser("detail")
    detail.set_defaults(oa_workflow_action="detail")
    detail.add_argument("--url")
    detail.add_argument("--id", dest="workflow_id")
    _add_workflow_type_option(detail)
    _add_detail_options(detail)
    _add_daemon_options(detail)
    _add_output_options(detail)

    for action in ("attachments", "opinions", "actions"):
        parser = subparsers.add_parser(action)
        parser.set_defaults(oa_workflow_action=action)
        parser.add_argument("--url")
        parser.add_argument("--id", dest="workflow_id")
        _add_workflow_type_option(parser)
        parser.add_argument("--keyword")
        _add_daemon_options(parser)
        _add_output_options(parser, default_format="json")


def _add_inbox_parser(subparsers) -> None:
    analyze = subparsers.add_parser("analyze")
    analyze.set_defaults(oa_inbox_action="analyze")
    _add_workflow_type_option(analyze)
    analyze.add_argument("--keyword")
    analyze.add_argument("--deep", action="store_true", help="Open detail pages for a limited number of items.")
    analyze.add_argument("--deep-limit", type=int, dest="deep_limit")
    analyze.add_argument("--text-limit", type=int, dest="text_limit")
    _add_daemon_options(analyze)
    _add_output_options(analyze)


def _add_workflow_type_option(parser) -> None:
    parser.add_argument(
        "--type",
        choices=["pending", "sent"],
        default="pending",
        dest="workflow_type",
        help="Workflow collection to read; defaults to pending.",
    )


def _add_collection_parser(
    subparsers,
    *,
    list_command: str,
    show_command: str | None,
    show_arg_name: str | None,
    category_filter: bool = False,
) -> None:
    for action in ("list", "search", "export"):
        parser = subparsers.add_parser(action)
        parser.set_defaults(oa_command=list_command)
        if action == "search":
            parser.add_argument("--keyword", required=True)
        else:
            parser.add_argument("--keyword")
        if category_filter:
            parser.add_argument("--category")
        _add_daemon_options(parser)
        _add_output_options(parser, default_format="csv" if action == "export" else "json")
    for action in ("details", "attachments", "workflow", "actions"):
        parser = subparsers.add_parser(action)
        parser.set_defaults(oa_command=list_command, oa_batch_kind=action)
        parser.add_argument("--keyword")
        _add_detail_options(parser)
        _add_daemon_options(parser)
        _add_output_options(parser, default_format="json")
    if show_command and show_arg_name:
        show = subparsers.add_parser("show")
        show.set_defaults(oa_command=show_command)
        show.add_argument(show_arg_name)
        _add_daemon_options(show)
        _add_output_options(show)


def _add_daemon_options(parser) -> None:
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8765")


def _add_output_options(parser, *, default_format: str = "json") -> None:
    parser.add_argument("--format", choices=["json", "table", "csv"], default=default_format)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--fields")


def _add_detail_options(parser) -> None:
    parser.add_argument("--include", default="title,text,fields,attachments,workflow")
    parser.add_argument("--text-limit", type=int, default=3000)


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


def handle_trace(args: argparse.Namespace, home: Path) -> int:
    trace_store = TraceStore(home / "trace.db")
    if args.action == "list":
        print_json(trace_store.list_runs())
        return 0
    if args.action == "show":
        print_json(trace_store.get_run(args.run_id))
        return 0
    raise ValueError(f"unknown trace action: {args.action}")


def handle_daemon(args: argparse.Namespace, home: Path) -> int:
    if args.action == "serve":
        serve(home, host=args.host, port=args.port)
        return 0
    if args.action == "status":
        print_json(
            {
                "daemon": get_json(f"{args.daemon_url}/health", home=home),
                "extension_clients": get_json(f"{args.daemon_url}/extension/clients", home=home)[
                    "clients"
                ],
            }
        )
        return 0
    raise ValueError(f"unknown daemon action: {args.action}")


def handle_explore(args: argparse.Namespace) -> int:
    if args.action == "dom-snapshot":
        payload = {
            "system": args.system,
            "selector": args.selector,
        }
        result = post_json(f"{args.daemon_url}/explore/dom-snapshot", payload, home=Path(args.home))
        print_json(result)
        return 0
    raise ValueError(f"unknown explore action: {args.action}")


def handle_command(args: argparse.Namespace) -> int:
    if args.action == "list":
        registry = CommandRegistry()
        register_seeyon_commands(registry)
        commands = [
            {
                "system": command.system,
                "name": command.name,
                "description": command.description,
                "access": command.access,
                "strategy": command.strategy,
                "risk": command.risk,
            }
            for command in registry.list(args.system)
        ]
        print_json(commands)
        return 0
    if args.action == "run":
        payload = {
            "system": args.system,
            "command": args.command,
            "args": json.loads(args.json),
            "timeout_seconds": args.timeout,
        }
        result = post_json(
            f"{args.daemon_url}/commands/run",
            payload,
            timeout=_daemon_client_timeout_seconds(float(args.timeout), args.command),
            home=Path(args.home),
        )
        print_json(result)
        return 0
    raise ValueError(f"unknown command action: {args.action}")


def handle_discovered(args: argparse.Namespace, home: Path) -> int:
    store = DiscoveredApiStore(home)
    if args.action == "list":
        print_json([_discovered_api_summary(api) for api in store.list_apis(args.system)])
        return 0
    if args.action == "show":
        print_json(store.load_api(args.system, args.name).raw)
        return 0
    if args.action == "run":
        run_args = _discovered_run_args_from_cli(args)
        if args.confirm:
            run_args["confirm"] = True
        result = post_json(
            f"{args.daemon_url}/commands/run",
            {
                "system": args.system,
                "command": "discovered_run",
                "args": run_args,
                "timeout_seconds": args.timeout,
            },
            home=home,
        )
        print_json(result)
        return 0
    raise ValueError(f"unknown discovered action: {args.action}")


def handle_tool(args: argparse.Namespace) -> int:
    if args.action == "manifest":
        registry = CommandRegistry()
        register_seeyon_commands(registry)
        discovered = []
        if args.system:
            discovered = DiscoveredApiStore(Path(args.home)).list_apis(args.system)
        print_json(export_tool_manifest(registry, system=args.system, discovered_apis=discovered))
        return 0
    raise ValueError(f"unknown tool action: {args.action}")


def handle_mcp(args: argparse.Namespace) -> int:
    if args.action == "serve":
        registry = CommandRegistry()
        register_seeyon_commands(registry)
        discovered = []
        try:
            discovered = DiscoveredApiStore(Path(args.home)).list_apis("oa")
        except ValueError:
            discovered = []
        server = BscliMcpServer(
            registry,
            command_runner=lambda system, command, arguments: run_command_via_daemon(
                args.daemon_url,
                system,
                command,
                arguments,
                home=Path(args.home),
            ),
            discovered_apis=discovered,
        )
        if args.once:
            line = sys.stdin.readline()
            if line:
                response = server.handle_request(json.loads(line))
                if response is not None:
                    print_json_compact(response)
            return 0
        server.serve_stdio()
        return 0
    raise ValueError(f"unknown mcp action: {args.action}")


def handle_oa(args: argparse.Namespace, home: Path) -> int:
    if getattr(args, "oa_audit_kind", None):
        return handle_oa_audit(args, home)
    if getattr(args, "oa_meeting_create_mode", None):
        return handle_oa_meeting_create(args)
    if getattr(args, "oa_meeting_reply_mode", None):
        return handle_oa_meeting_reply(args)
    if getattr(args, "oa_write_actions", False):
        return handle_oa_write_actions(args)
    if getattr(args, "oa_write_smoke", False):
        return handle_oa_write_smoke(args)
    if getattr(args, "oa_write_capabilities", False):
        return handle_oa_write_capabilities(args)
    if getattr(args, "oa_write_discover", False):
        return handle_oa_write_discover(args)
    if getattr(args, "oa_write_endpoints", False):
        return handle_oa_write_endpoints(args)
    if getattr(args, "oa_write_preflight", False):
        return handle_oa_write_preflight(args)
    if getattr(args, "oa_write_prepare", False):
        return handle_oa_write_prepare(args)
    if getattr(args, "oa_write_mode", None):
        return handle_oa_write(args, home)
    if getattr(args, "oa_pending_submit", False):
        return handle_oa_pending_submit(args)
    if getattr(args, "oa_inbox_action", None):
        return handle_oa_inbox(args)
    if getattr(args, "oa_history_action", None):
        return handle_oa_history(args)
    if getattr(args, "oa_matter_action", None):
        return handle_oa_matter(args)
    if getattr(args, "oa_workflow_action", None):
        return handle_oa_workflow(args)
    command = args.oa_command
    if command == "discovered_list":
        apis = [_discovered_api_summary(api) for api in DiscoveredApiStore(home).list_apis("oa")]
        emit_cli_value(_apply_collection_options(apis, args), args)
        return 0
    if command == "discovered_show":
        emit_cli_value(DiscoveredApiStore(home).load_api("oa", args.name).raw, args)
        return 0
    if getattr(args, "oa_batch_kind", None):
        return handle_oa_batch(args)

    command_args = _oa_command_args(args)
    response = run_oa_daemon_command(args, command, command_args)
    if not response.get("ok", False):
        print_json(response)
        return 0
    response = _project_detail_response(response, args)
    response = _apply_response_options(response, args)
    emit_cli_value(response, args)
    return 0


def handle_oa_audit(args: argparse.Namespace, home: Path) -> int:
    if args.oa_audit_kind == "write_plans":
        filename = "oa-write-plans.jsonl"
        kind = "write_plans"
    elif args.oa_audit_kind == "write_verifications":
        filename = "oa-write-verifications.jsonl"
        kind = "write_verifications"
    else:
        raise ValueError(f"unknown OA audit kind: {args.oa_audit_kind}")
    if args.oa_audit_action == "show":
        response = _oa_audit_show_result(home, filename=filename, kind=kind, index=args.index)
    elif args.oa_audit_action == "search":
        response = _oa_audit_search_result(home, filename=filename, kind=kind, args=args)
    else:
        response = _oa_audit_result(home, filename=filename, kind=kind)
    response = _apply_response_options(response, args)
    emit_cli_value(response, args)
    return 0


def handle_oa_history(args: argparse.Namespace) -> int:
    action = args.oa_history_action
    if action == "sections":
        response = run_oa_daemon_command(args, "history_sections", _history_daemon_args_from_cli(args))
    elif action in {"list", "search", "export"}:
        response = run_oa_daemon_command(args, "history_list", _history_daemon_args_from_cli(args))
    elif action in {"profile", "clusters"}:
        response = run_oa_daemon_command(args, "history_profile", _history_daemon_args_from_cli(args))
    else:
        raise ValueError(f"unknown history action: {action}")
    if not response.get("ok", False):
        emit_cli_value(response, args)
        return 0
    response = _apply_response_options(response, args)
    emit_cli_value(response, args)
    return 0


def handle_oa_matter(args: argparse.Namespace) -> int:
    action = args.oa_matter_action
    if action == "profile":
        response = run_oa_daemon_command(args, "matter_profile", _matter_daemon_args_from_cli(args))
    elif action == "matrix":
        response = run_oa_daemon_command(args, "matter_matrix", _matter_daemon_args_from_cli(args))
    elif action == "inspect":
        response = run_oa_daemon_command(args, "matter_inspect", _matter_daemon_args_from_cli(args))
    elif action == "preflight":
        response = run_oa_daemon_command(args, "matter_preflight", _matter_preflight_daemon_args_from_cli(args))
    elif action == "launch-dry-run":
        response = run_oa_daemon_command(args, "matter_launch_dry_run", _matter_launch_daemon_args_from_cli(args))
    elif action == "launch-save-draft":
        if not getattr(args, "confirm", False):
            emit_cli_value(
                {
                    "ok": False,
                    "requires_confirmation": True,
                    "confirmed": False,
                    "error": "oa matter launch-save-draft requires --confirm",
                    "result": {},
                },
                args,
            )
            return 0
        response = run_oa_daemon_command(args, "matter_launch_save_draft", _matter_launch_daemon_args_from_cli(args))
    elif action == "execute":
        if not getattr(args, "confirm", False):
            emit_cli_value(
                {
                    "ok": False,
                    "requires_confirmation": True,
                    "confirmed": False,
                    "error": "oa matter execute requires --confirm",
                    "result": {},
                },
                args,
            )
            return 0
        response = run_oa_daemon_command(args, "matter_execute", _matter_execute_daemon_args_from_cli(args))
    else:
        raise ValueError(f"unknown matter action: {action}")
    if not response.get("ok", False):
        emit_cli_value(response, args)
        return 0
    response = _project_matter_response(response, args)
    response = _apply_response_options(response, args)
    emit_cli_value(response, args)
    return 0


def handle_oa_inbox(args: argparse.Namespace) -> int:
    action = args.oa_inbox_action
    if action == "analyze":
        response = run_oa_daemon_command(args, "inbox_analyze", _inbox_daemon_args_from_cli(args))
        if not response.get("ok", False):
            emit_cli_value(response, args)
            return 0
        response = _apply_response_options(response, args)
        emit_cli_value(response, args)
        return 0
    raise ValueError(f"unknown inbox action: {action}")


def handle_oa_workflow(args: argparse.Namespace) -> int:
    action = args.oa_workflow_action
    if action in {"list", "search", "export"}:
        response = run_oa_daemon_command(args, "workflow_list", _workflow_daemon_args_from_cli(args))
        if not response.get("ok", False):
            print_json(response)
            return 0
        response = _apply_response_options(response, args)
        emit_cli_value(response, args)
        return 0
    if action == "brief":
        response = run_oa_daemon_command(args, "workflow_brief", _workflow_daemon_args_from_cli(args))
        if not response.get("ok", False):
            emit_cli_value(response, args)
            return 0
        response = _apply_response_options(response, args)
        emit_cli_value(response, args)
        return 0
    if action in {"inspect", "evidence", "timeline"}:
        command = {
            "inspect": "workflow_inspect",
            "evidence": "workflow_evidence",
            "timeline": "workflow_timeline",
        }[action]
        response = run_oa_daemon_command(args, command, _workflow_daemon_args_from_cli(args))
        if not response.get("ok", False):
            emit_cli_value(response, args)
            return 0
        response = _apply_response_options(response, args)
        emit_cli_value(response, args)
        return 0
    if action == "details":
        return _run_oa_workflow_batch(args, "details")
    if action == "detail":
        response = run_oa_daemon_command(args, "workflow_detail", _workflow_daemon_args_from_cli(args))
        if not response.get("ok", False):
            emit_cli_value(response, args)
            return 0
        response = _apply_response_options(response, args)
        emit_cli_value(response, args)
        return 0
    if action in {"attachments", "opinions", "actions"}:
        command = {
            "attachments": "workflow_attachments",
            "opinions": "workflow_opinions",
            "actions": "workflow_actions",
        }[action]
        response = run_oa_daemon_command(args, command, _workflow_daemon_args_from_cli(args))
        if not response.get("ok", False):
            emit_cli_value(response, args)
            return 0
        response = _apply_response_options(response, args)
        emit_cli_value(response, args)
        return 0
    raise ValueError(f"unknown workflow action: {action}")


def _inbox_daemon_args_from_cli(args: argparse.Namespace) -> dict:
    payload = {"type": args.workflow_type}
    keyword = getattr(args, "keyword", None)
    if keyword:
        payload["keyword"] = keyword
    if getattr(args, "limit", None) is not None:
        payload["limit"] = args.limit
    if getattr(args, "deep", False):
        payload["deep"] = True
    deep_limit = getattr(args, "deep_limit", None)
    if deep_limit is not None:
        payload["deep_limit"] = deep_limit
    text_limit = getattr(args, "text_limit", None)
    if text_limit is not None:
        payload["text_limit"] = text_limit
    return payload


def _history_daemon_args_from_cli(args: argparse.Namespace) -> dict:
    payload = {}
    kind = getattr(args, "kind", None)
    if kind:
        payload["kind"] = kind
    keyword = getattr(args, "keyword", None)
    if keyword:
        payload["keyword"] = keyword
    if getattr(args, "limit", None) is not None:
        payload["limit"] = args.limit
    return payload


def _matter_daemon_args_from_cli(args: argparse.Namespace) -> dict:
    payload = _history_daemon_args_from_cli(args)
    matter_id = getattr(args, "matter_id", None)
    if matter_id:
        payload["id"] = matter_id
    matter_name = getattr(args, "matter_name", None)
    if matter_name:
        payload["name"] = matter_name
    if getattr(args, "with_launch", False):
        payload["with_launch"] = True
    if getattr(args, "settle_ms", None) is not None:
        payload["settle_ms"] = args.settle_ms
    return payload


def _matter_preflight_daemon_args_from_cli(args: argparse.Namespace) -> dict:
    payload = {
        "intent": args.intent,
        "opinion": args.opinion,
    }
    workflow_id = getattr(args, "workflow_id", None)
    if workflow_id:
        payload["id"] = workflow_id
    keyword = getattr(args, "keyword", None)
    if keyword:
        payload["keyword"] = keyword
    if getattr(args, "limit", None) is not None:
        payload["limit"] = args.limit
    if getattr(args, "text_limit", None) is not None:
        payload["text_limit"] = args.text_limit
    return payload


def _matter_execute_daemon_args_from_cli(args: argparse.Namespace) -> dict:
    payload = {
        "intent": args.intent,
        "opinion": args.opinion,
        "confirm": True,
        "limit": getattr(args, "limit", None) if getattr(args, "limit", None) is not None else 1,
    }
    workflow_id = getattr(args, "workflow_id", None)
    if workflow_id:
        payload["id"] = workflow_id
    keyword = getattr(args, "keyword", None)
    if keyword:
        payload["keyword"] = keyword
    meeting_id = getattr(args, "meeting_id", None)
    if meeting_id:
        payload["meeting_id"] = meeting_id
    source_url = getattr(args, "source_url", None)
    if source_url:
        payload["source_url"] = source_url
    feedback = getattr(args, "feedback", "")
    if feedback:
        payload["feedback"] = feedback
    proxy_id = getattr(args, "proxy_id", None)
    if proxy_id:
        payload["proxy_id"] = proxy_id
    for key in (
        "text_limit",
        "verify_wait",
        "business_form_wait_ms",
        "script_timeout_ms",
        "after_submit_wait_ms",
    ):
        value = getattr(args, key, None)
        if value is not None:
            payload[key] = value
    return payload


def _matter_launch_daemon_args_from_cli(args: argparse.Namespace) -> dict:
    payload = _matter_daemon_args_from_cli(args)
    payload.pop("with_launch", None)
    payload["fields"] = _launch_fields_from_cli(args)
    if getattr(args, "confirm", False):
        payload["confirm"] = True
    if getattr(args, "keep_tab", False):
        payload["keep_tab"] = True
    return payload


def _write_discover_daemon_args_from_cli(args: argparse.Namespace) -> dict:
    payload = {
        "source": args.source,
    }
    if args.source == "history":
        payload["kind"] = args.kind
    keyword = getattr(args, "keyword", None)
    if keyword:
        payload["keyword"] = keyword
    if getattr(args, "limit", None) is not None:
        payload["limit"] = args.limit
    template_id = getattr(args, "template_id", None)
    if template_id:
        payload["template_id"] = template_id
    url = getattr(args, "url", None)
    if url:
        payload["url"] = url
    settle_ms = getattr(args, "settle_ms", None)
    if settle_ms is not None:
        payload["settle_ms"] = settle_ms
    deep_limit = getattr(args, "deep_limit", None)
    if deep_limit is not None:
        payload["deep_limit"] = deep_limit
    text_limit = getattr(args, "text_limit", None)
    if text_limit is not None:
        payload["text_limit"] = text_limit
    return payload


def _workflow_daemon_args_from_cli(args: argparse.Namespace) -> dict:
    payload = {"type": args.workflow_type}
    keyword = getattr(args, "keyword", None)
    if keyword:
        payload["keyword"] = keyword
    if getattr(args, "limit", None) is not None:
        payload["limit"] = args.limit
    workflow_id = getattr(args, "workflow_id", None)
    if workflow_id:
        payload["id"] = workflow_id
    url = getattr(args, "url", None)
    if url:
        payload["url"] = url
    include = getattr(args, "include", None)
    if include:
        payload["include"] = include
    text_limit = getattr(args, "text_limit", None)
    if text_limit is not None:
        payload["text_limit"] = text_limit
    return payload


def _run_oa_workflow_batch(args: argparse.Namespace, batch_kind: str) -> int:
    args.oa_command = _workflow_list_command(args.workflow_type)
    args.oa_batch_kind = batch_kind
    return handle_oa_batch(args)


def _run_oa_workflow_detail(
    args: argparse.Namespace,
    *,
    projection: str | None,
) -> int:
    source_item = None
    detail_url = getattr(args, "url", None) or ""
    workflow_id = getattr(args, "workflow_id", None)
    if not detail_url and workflow_id:
        resolved = _resolve_workflow_item_by_id(args, workflow_id)
        if not resolved.get("ok", False):
            emit_cli_value(resolved, args)
            return 0
        source_item = resolved["item"]
        detail_url = source_item.get("href") or ""
    if not detail_url:
        emit_cli_value(
            {
                "ok": False,
                "error": "oa workflow detail requires --url or --id",
                "result": {},
            },
            args,
        )
        return 0
    args.oa_command = "detail_read"
    args.oa_detail_projection = projection
    response = run_oa_daemon_command(args, "detail_read", {"url": detail_url})
    if not response.get("ok", False):
        print_json(response)
        return 0
    response = _project_detail_response(response, args)
    response = _apply_response_options(response, args)
    if source_item:
        response = _attach_workflow_source_item(response, source_item, projection=projection)
    emit_cli_value(response, args)
    return 0


def _resolve_workflow_item_by_id(args: argparse.Namespace, workflow_id: str) -> dict:
    list_response = run_oa_daemon_command(args, _workflow_list_command(args.workflow_type), {})
    if not list_response.get("ok", False):
        return {
            "ok": False,
            "error": list_response.get("error") or "workflow list failed",
            "source": list_response,
            "result": {},
        }
    result = list_response.get("result") or {}
    items = result.get("items") if isinstance(result, dict) else []
    if not isinstance(items, list):
        items = []
    for item in items:
        if isinstance(item, dict) and str(item.get("affair_id") or "") == str(workflow_id):
            if not item.get("href"):
                return {
                    "ok": False,
                    "error": f"workflow item has no detail href: {workflow_id}",
                    "item": item,
                    "result": {},
                }
            return {"ok": True, "item": item}
    return {
        "ok": False,
        "error": f"workflow id not found in {args.workflow_type} list: {workflow_id}",
        "result": {
            "type": args.workflow_type,
            "id": workflow_id,
            "searched_count": len(items),
        },
    }


def _attach_workflow_source_item(
    response: dict,
    source_item: dict,
    *,
    projection: str | None,
) -> dict:
    result = response.get("result")
    if not isinstance(result, dict):
        return response
    if projection is None:
        return {
            **response,
            "result": {
                "source_item": source_item,
                "detail": result,
            },
        }
    return {**response, "result": {**result, "source_item": source_item}}


def _workflow_list_command(workflow_type: str) -> str:
    mapping = {
        "pending": "pending_list_api",
        "sent": "sent_list_api",
    }
    try:
        return mapping[workflow_type]
    except KeyError as exc:
        raise ValueError(f"unsupported workflow type: {workflow_type}") from exc


def handle_oa_batch(args: argparse.Namespace) -> int:
    list_response = run_oa_daemon_command(args, args.oa_command, {})
    if not list_response.get("ok", False):
        print_json(list_response)
        return 0
    result = list_response.get("result") or {}
    source_items = result.get("items") if isinstance(result, dict) else []
    if not isinstance(source_items, list):
        source_items = []
    source_items = _apply_collection_options(source_items, args, apply_fields=False)

    batch_kind = args.oa_batch_kind
    detail_items = []
    indexed_items = []
    for source_item in source_items:
        if not isinstance(source_item, dict) or not source_item.get("href"):
            continue
        detail_response = run_oa_daemon_command(
            args,
            "detail_read",
            {"url": source_item["href"]},
        )
        detail = detail_response.get("result") if detail_response.get("ok") else {
            "error": detail_response.get("error") or "detail_read failed"
        }
        if not isinstance(detail, dict):
            detail = {"value": detail}
        if batch_kind == "details":
            detail_items.append(
                {
                    "source_item": source_item,
                    "detail": _project_detail(detail, args),
                }
            )
        elif batch_kind == "attachments":
            indexed_items.extend(_index_detail_entries(source_item, detail, "attachments"))
        elif batch_kind == "workflow":
            indexed_items.extend(_index_detail_entries(source_item, detail, "workflow"))
        elif batch_kind == "actions":
            indexed_items.extend(_index_detail_entries(source_item, detail, "actions"))

    items = detail_items if batch_kind == "details" else indexed_items
    response = {
        "ok": True,
        "result": {
            "source_count": len(source_items),
            "count": len(items),
            "items": items,
        },
    }
    response = _apply_response_options(response, args)
    emit_cli_value(response, args)
    return 0


def handle_oa_pending_submit(args: argparse.Namespace) -> int:
    if not getattr(args, "confirm", False):
        emit_cli_value(
            {
                "ok": False,
                "requires_confirmation": True,
                "confirmed": False,
                "error": "oa pending submit requires --confirm",
                "result": {"items": [], "submitted_count": 0, "target_count": 0},
            },
            args,
        )
        return 0

    response = run_oa_daemon_command(
        args,
        "pending_submit",
        {
            "keyword": args.keyword,
            "action": args.action,
            "opinion": args.opinion,
            "limit": args.limit,
            "confirm": True,
            "verify_wait": args.verify_wait,
        },
    )
    emit_cli_value(response, args)
    return 0


def handle_oa_meeting_reply(args: argparse.Namespace) -> int:
    if args.oa_meeting_reply_mode == "execute" and not getattr(args, "confirm", False):
        emit_cli_value(
            {
                "ok": False,
                "requires_confirmation": True,
                "confirmed": False,
                "error": "oa meeting reply execute requires --confirm",
                "result": {},
            },
            args,
        )
        return 0

    command_args = {
        "id": args.id,
        "attitude": args.attitude,
        "feedback": args.feedback,
    }
    if args.meeting_id:
        command_args["meeting_id"] = args.meeting_id
    if args.source_url:
        command_args["source_url"] = args.source_url
    if args.oa_meeting_reply_mode == "execute":
        command_args["confirm"] = True
        command_args["verify_wait"] = args.verify_wait
    response = run_oa_daemon_command(
        args,
        "meeting_reply_dry_run" if args.oa_meeting_reply_mode == "dry-run" else "meeting_reply_execute",
        command_args,
    )
    emit_cli_value(response, args)
    return 0


def handle_oa_meeting_create(args: argparse.Namespace) -> int:
    if args.oa_meeting_create_mode == "execute":
        if not getattr(args, "confirm", False):
            emit_cli_value(
                {
                    "ok": False,
                    "requires_confirmation": True,
                    "confirmed": False,
                    "error": "oa meeting create execute requires --confirm",
                    "result": {
                        "schema_version": "bscli.oa_meeting_create_plan.v1",
                        "safety": {"will_execute": False, "requires_confirmation": True},
                    },
                },
                args,
            )
            return 0
        command_args: dict[str, object] = {
            "subject": args.subject,
            "room": args.room,
            "start": args.start,
            "end": args.end,
            "confirm": True,
        }
        if args.attendee:
            command_args["attendees"] = args.attendee
        response = run_oa_daemon_command(args, "meeting_create_execute", command_args)
        emit_cli_value(response, args)
        return 0

    command_args: dict[str, object] = {"url": OA_MEETING_CREATE_URL}
    if getattr(args, "settle_ms", None) is not None:
        command_args["settle_ms"] = args.settle_ms
    command = "launch_inspect"
    if args.oa_meeting_create_mode == "dry-run":
        command = "launch_dry_run"
        command_args["fields"] = _launch_fields_from_cli(args)
    response = run_oa_daemon_command(args, command, command_args)
    emit_cli_value(response, args)
    return 0


def handle_oa_write_actions(args: argparse.Namespace) -> int:
    items = [asdict(spec) for spec in list_write_action_specs()]
    response = {
        "ok": True,
        "result": {
            "schema_version": "bscli.oa_write_actions.v1",
            "count": len(items),
            "items": items,
        },
    }
    response = _apply_response_options(response, args)
    emit_cli_value(response, args)
    return 0


def handle_oa_write_smoke(args: argparse.Namespace) -> int:
    keyword = str(args.keyword or "")
    if keyword != DEFAULT_OA_WRITE_SMOKE_KEYWORD and not args.allow_custom_keyword:
        emit_cli_value(
            {
                "ok": False,
                "error": "custom smoke keyword requires --allow-custom-keyword",
                "result": {
                    "schema_version": "bscli.oa_write_smoke.v1",
                    "checks": [],
                    "target_count": 0,
                    "submitted_count": 0,
                },
            },
            args,
        )
        return 0

    list_response = run_oa_daemon_command(args, "pending_list_api", {})
    if list_response.get("ok") is not True:
        emit_cli_value(
            {
                "ok": False,
                "error": list_response.get("error") or "pending list precheck failed",
                "source": list_response,
                "result": {
                    "schema_version": "bscli.oa_write_smoke.v1",
                    "checks": [{"name": "pending_no_match_precheck", "status": "failed"}],
                    "target_count": 0,
                    "submitted_count": 0,
                },
            },
            args,
        )
        return 0

    matches = _filter_oa_items_by_keyword(_items_from_oa_response(list_response), keyword)
    if args.limit is not None:
        matches = matches[: max(args.limit, 0)]
    if matches:
        emit_cli_value(
            {
                "ok": False,
                "error": "smoke keyword matched pending items; refusing confirmed write validation",
                "result": {
                    "schema_version": "bscli.oa_write_smoke.v1",
                    "checks": [{"name": "pending_no_match_precheck", "status": "failed"}],
                    "target_count": len(matches),
                    "submitted_count": 0,
                    "items": [_smoke_match_summary(item) for item in matches],
                },
            },
            args,
        )
        return 0

    submit_response = run_oa_daemon_command(
        args,
        "pending_submit",
        {
            "keyword": keyword,
            "action": args.action,
            "opinion": args.opinion,
            "limit": args.limit,
            "confirm": True,
            "verify_wait": args.verify_wait,
        },
    )
    result = submit_response.get("result") if isinstance(submit_response.get("result"), dict) else {}
    target_count = int(result.get("target_count") or 0)
    submitted_count = int(result.get("submitted_count") or 0)
    ok = submit_response.get("ok") is True and target_count == 0 and submitted_count == 0
    response = {
        "ok": ok,
        "error": "" if ok else submit_response.get("error") or "smoke validation did not prove confirmed no-op",
        "result": {
            "schema_version": "bscli.oa_write_smoke.v1",
            "keyword": keyword,
            "checks": [
                {"name": "pending_no_match_precheck", "status": "passed"},
                {
                    "name": "confirmed_pending_submit_noop",
                    "status": "passed" if ok else "failed",
                },
            ],
            "target_count": target_count,
            "submitted_count": submitted_count,
            "daemon_response": submit_response,
        },
    }
    emit_cli_value(response, args)
    return 0


def handle_oa_write_capabilities(args: argparse.Namespace) -> int:
    command_args = {"type": args.workflow_type}
    if args.keyword:
        command_args["keyword"] = args.keyword
    if args.limit is not None:
        command_args["limit"] = args.limit
    response = run_oa_daemon_command(args, "write_capabilities", command_args)
    response = _apply_response_options(response, args)
    emit_cli_value(response, args)
    return 0


def handle_oa_write_discover(args: argparse.Namespace) -> int:
    response = run_oa_daemon_command(args, "write_discover", _write_discover_daemon_args_from_cli(args))
    response = _apply_response_options(response, args)
    emit_cli_value(response, args)
    return 0


def handle_oa_write_endpoints(args: argparse.Namespace) -> int:
    command_args = {
        "affair_id": args.affair_id,
        "action": args.action,
    }
    if args.source_url:
        command_args["source_url"] = args.source_url
    response = run_oa_daemon_command(args, "write_endpoint_candidates", command_args)
    emit_cli_value(response, args)
    return 0


def handle_oa_write_preflight(args: argparse.Namespace) -> int:
    command_args = {
        "type": args.workflow_type,
        "affair_id": args.affair_id,
        "action": args.action,
        "opinion": args.opinion,
        "source_url": args.source_url,
    }
    response = run_oa_daemon_command(args, "write_preflight", command_args)
    emit_cli_value(response, args)
    return 0


def handle_oa_write_prepare(args: argparse.Namespace) -> int:
    command_args = {
        "type": args.workflow_type,
        "affair_id": args.affair_id,
        "action": args.action,
        "opinion": args.opinion,
        "source_url": args.source_url,
    }
    if getattr(args, "text_limit", None) is not None:
        command_args["text_limit"] = args.text_limit
    response = run_oa_daemon_command(args, "write_prepare", command_args)
    emit_cli_value(response, args)
    return 0


def handle_oa_write(args: argparse.Namespace, home: Path) -> int:
    plan = build_oa_write_plan(
        affair_id=args.affair_id,
        action=args.action,
        opinion=args.opinion,
        mode=args.oa_write_mode,
        source_url=args.source_url,
    )
    if args.oa_write_mode == "dry-run":
        response = run_oa_daemon_command(
            args,
            "write_dry_run",
            {
                "affair_id": args.affair_id,
                "action": args.action,
                "opinion": args.opinion,
                "source_url": args.source_url,
            },
        )
        emit_cli_value(response, args)
        return 0
    if args.oa_write_mode == "execute":
        if getattr(args, "confirm", False):
            command_args = {
                "affair_id": args.affair_id,
                "action": args.action,
                "opinion": args.opinion,
                "source_url": args.source_url,
                "confirm": True,
            }
            if getattr(args, "script_timeout_ms", None) is not None:
                command_args["script_timeout_ms"] = args.script_timeout_ms
            if getattr(args, "business_form_wait_ms", None) is not None:
                command_args["business_form_wait_ms"] = args.business_form_wait_ms
            if getattr(args, "after_submit_wait_ms", None) is not None:
                command_args["after_submit_wait_ms"] = args.after_submit_wait_ms
            response = run_oa_daemon_command(
                args,
                "write_execute",
                command_args,
            )
            emit_cli_value(response, args)
            return 0
        blocked = {
            "ok": False,
            "error": "oa write execute requires --confirm",
            "requires_confirmation": True,
            "confirmed": False,
            "plan": plan,
        }
        append_oa_write_audit(home, plan)
        emit_cli_value(blocked, args)
        return 0
    emit_cli_value(plan, args)
    return 0


def run_oa_daemon_command(
    args: argparse.Namespace,
    command: str,
    command_args: dict,
) -> dict:
    client_timeout = _daemon_client_timeout_seconds(float(args.timeout), command)
    return post_json(
        f"{args.daemon_url}/commands/run",
        {
            "system": "oa",
            "command": command,
            "args": command_args,
            "timeout_seconds": args.timeout,
        },
        timeout=client_timeout,
        home=Path(args.home),
    )


def _daemon_client_timeout_seconds(timeout_seconds: float, command: str) -> float:
    if command in {"write_prepare"}:
        return max(float(timeout_seconds) * 3 + 5, 10)
    if command in {"launch_save_draft", "matter_launch_save_draft", "write_execute", "pending_submit", "matter_execute"}:
        return max(float(timeout_seconds) * 2 + 20, 30)
    return max(float(timeout_seconds) + 5, 10)


def _oa_command_args(args: argparse.Namespace) -> dict:
    command = args.oa_command
    if command == "pending_detail":
        return {"affair_id": args.affair_id}
    if command == "template_detail":
        return {"template_id": args.template_id}
    if command == "template_list_api":
        payload = {}
        for key in ("keyword", "category", "limit"):
            if getattr(args, key, None) is not None:
                payload[key] = getattr(args, key)
        return payload
    if command == "template_match":
        return _history_daemon_args_from_cli(args)
    if command == "launch_inspect":
        payload = {}
        if getattr(args, "template_id", None):
            payload["template_id"] = args.template_id
        if getattr(args, "url", None):
            payload["url"] = args.url
        if getattr(args, "settle_ms", None) is not None:
            payload["settle_ms"] = args.settle_ms
        return payload
    if command in {"launch_dry_run", "launch_save_draft"}:
        payload = {}
        if getattr(args, "template_id", None):
            payload["template_id"] = args.template_id
        if getattr(args, "url", None):
            payload["url"] = args.url
        payload["fields"] = _launch_fields_from_cli(args)
        if command == "launch_save_draft" and getattr(args, "confirm", False):
            payload["confirm"] = True
        if command == "launch_save_draft" and getattr(args, "keep_tab", False):
            payload["keep_tab"] = True
        if getattr(args, "settle_ms", None) is not None:
            payload["settle_ms"] = args.settle_ms
        return payload
    if command == "detail_read":
        return {"url": args.url}
    if command == "bridge_script_smoke":
        return {"marker": args.marker}
    if command in {"api_inspect", "api_replay"}:
        return _api_args_from_oa_cli(args)
    if command == "api_save":
        api_args = _api_args_from_oa_cli(args)
        api_args["name"] = args.name
        api_args["description"] = args.description
        return api_args
    if command == "discovered_run":
        run_args = _discovered_run_args_from_cli(args)
        if args.confirm:
            run_args["confirm"] = True
        return run_args
    return {}


def _discovered_run_args_from_cli(args: argparse.Namespace) -> dict:
    extra = json.loads(getattr(args, "json", "{}") or "{}")
    if not isinstance(extra, dict):
        raise ValueError("--json must decode to an object")
    for reserved in ("name", "confirm"):
        if reserved in extra:
            raise ValueError(f"--json cannot contain reserved discovered argument: {reserved}")
    return {"name": args.name, **extra}


def _launch_fields_from_cli(args: argparse.Namespace) -> dict[str, str]:
    fields_json = json.loads(getattr(args, "fields_json", "{}") or "{}")
    if not isinstance(fields_json, dict):
        raise ValueError("--fields-json must decode to an object")
    fields = {str(key): "" if value is None else str(value) for key, value in fields_json.items()}
    for assignment in getattr(args, "field", []) or []:
        if "=" not in assignment:
            raise ValueError("--field must use name=value format")
        name, value = assignment.split("=", 1)
        name = name.strip()
        if not name:
            raise ValueError("--field name cannot be empty")
        fields[name] = value
    return fields


def _api_args_from_oa_cli(args: argparse.Namespace) -> dict:
    api_args = {
        "method": args.method.upper(),
        "url": args.url,
    }
    headers = json.loads(args.headers)
    if headers:
        api_args["headers"] = headers
    if args.body is not None:
        api_args["body"] = args.body
    text_limit = getattr(args, "text_limit", None)
    if text_limit is not None:
        api_args["max_text"] = text_limit
    return api_args


def _oa_audit_result(home: Path, *, filename: str, kind: str) -> dict:
    entries = _oa_audit_entries(home, filename=filename, kind=kind)
    return {
        "ok": True,
        "result": {
            "kind": kind,
            "path": str(home / "audit" / filename),
            "count": len(entries),
            "items": [entry["summary"] for entry in entries],
        },
    }


def _oa_audit_show_result(home: Path, *, filename: str, kind: str, index: int) -> dict:
    entries = _oa_audit_entries(home, filename=filename, kind=kind)
    for entry in entries:
        if entry["index"] == index:
            return {
                "ok": True,
                "result": {
                    "kind": kind,
                    "path": str(home / "audit" / filename),
                    "index": index,
                    "summary": entry["summary"],
                    "record": entry["record"],
                },
            }
    return {
        "ok": False,
        "error": f"OA audit row index not found: {index}",
        "result": {
            "kind": kind,
            "path": str(home / "audit" / filename),
            "index": index,
            "count": len(entries),
        },
    }


def _oa_audit_search_result(home: Path, *, filename: str, kind: str, args: argparse.Namespace) -> dict:
    entries = _oa_audit_entries(home, filename=filename, kind=kind)
    filtered = []
    for entry in entries:
        summary = entry["summary"]
        if args.affair_id and str(summary.get("affair_id") or "") != str(args.affair_id):
            continue
        if args.action and str(summary.get("action") or "") != str(args.action):
            continue
        if args.status and not _oa_audit_summary_has_status(summary, args.status):
            continue
        filtered.append(summary)
    return {
        "ok": True,
        "result": {
            "kind": kind,
            "path": str(home / "audit" / filename),
            "count": len(filtered),
            "items": filtered,
        },
    }


def _oa_audit_entries(home: Path, *, filename: str, kind: str) -> list[dict]:
    rows = list(reversed(_read_jsonl(home / "audit" / filename)))
    entries = []
    for index, row in enumerate(rows, start=1):
        record = _sanitize_oa_audit_row(row, kind=kind)
        summary = (
            _oa_write_audit_summary(record, index)
            if kind == "write_plans"
            else _oa_write_verification_summary(record, index)
        )
        entries.append({"index": index, "summary": summary, "record": record})
    return entries


def _sanitize_oa_audit_row(row: dict, *, kind: str) -> dict:
    if kind == "write_plans":
        return sanitize_oa_write_plan_for_audit(row)
    return json.loads(json.dumps(row, ensure_ascii=False))


def _oa_audit_summary_has_status(summary: dict, status: str) -> bool:
    needle = str(status or "")
    return any(
        str(summary.get(key) or "") == needle
        for key in ("precheck_status", "request_status", "verification_status")
    )


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            rows.append({"parse_error": True})
            continue
        rows.append(value if isinstance(value, dict) else {"value": value})
    return rows


def _oa_write_audit_summary(row: dict, index: int) -> dict:
    target = row.get("target") if isinstance(row.get("target"), dict) else {}
    action = row.get("action") if isinstance(row.get("action"), dict) else {}
    precheck = row.get("precheck") if isinstance(row.get("precheck"), dict) else {}
    safety = row.get("safety") if isinstance(row.get("safety"), dict) else {}
    opinion = row.get("opinion") if isinstance(row.get("opinion"), dict) else {}
    request = row.get("request") if isinstance(row.get("request"), dict) else {}
    return {
        "index": index,
        "created_at": row.get("created_at", ""),
        "mode": row.get("mode", ""),
        "affair_id": str(target.get("affair_id") or ""),
        "action": action.get("code", ""),
        "risk": action.get("risk", ""),
        "precheck_status": precheck.get("status", ""),
        "request_status": request.get("status", ""),
        "will_execute": safety.get("will_execute"),
        "dry_run_only": safety.get("dry_run_only"),
        "opinion_length": opinion.get("length", 0),
    }


def _oa_write_verification_summary(row: dict, index: int) -> dict:
    target = row.get("target") if isinstance(row.get("target"), dict) else {}
    action = row.get("action") if isinstance(row.get("action"), dict) else {}
    verification = row.get("verification") if isinstance(row.get("verification"), dict) else {}
    submit = row.get("submit") if isinstance(row.get("submit"), dict) else {}
    return {
        "index": index,
        "created_at": row.get("created_at", ""),
        "affair_id": str(target.get("affair_id") or ""),
        "action": action.get("code", ""),
        "verification_status": verification.get("status", ""),
        "verified": verification.get("verified"),
        "before_present": verification.get("before_present"),
        "after_present": verification.get("after_present"),
        "task_id": submit.get("task_id", ""),
    }


def _items_from_oa_response(response: dict) -> list[dict]:
    result = response.get("result")
    if not isinstance(result, dict):
        return []
    items = result.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _filter_oa_items_by_keyword(items: list[dict], keyword: str) -> list[dict]:
    if not keyword:
        return list(items)
    needle = keyword.lower()
    return [
        item
        for item in items
        if needle in json.dumps(item, ensure_ascii=False).lower()
    ]


def _smoke_match_summary(item: dict) -> dict:
    return {
        "title": item.get("title", ""),
        "affair_id": str(item.get("affair_id") or ""),
        "sender": item.get("sender", ""),
        "date": item.get("date", ""),
    }


def _apply_response_options(response: dict, args: argparse.Namespace) -> dict:
    result = response.get("result")
    if not isinstance(result, dict):
        return response
    items = result.get("items")
    if not isinstance(items, list):
        return response
    filtered = _apply_collection_options(items, args)
    updated_result = {**result, "items": filtered, "count": len(filtered)}
    return {**response, "result": updated_result}


def _project_matter_response(response: dict, args: argparse.Namespace) -> dict:
    if response.get("ok") is not True or getattr(args, "oa_matter_action", None) != "profile":
        return response
    result = response.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("matters"), list):
        return response
    return {
        **response,
        "result": {
            **result,
            "items": result["matters"],
            "count": len(result["matters"]),
        },
    }


def _apply_collection_options(
    items: list,
    args: argparse.Namespace,
    *,
    apply_fields: bool = True,
) -> list:
    filtered = list(items)
    keyword = getattr(args, "keyword", None)
    if keyword:
        needle = keyword.lower()
        filtered = [
            item
            for item in filtered
            if needle in json.dumps(item, ensure_ascii=False).lower()
        ]
    category = getattr(args, "category", None)
    if category:
        needle = str(category).lower()
        filtered = [
            item
            for item in filtered
            if _item_matches_category(item, needle)
        ]
    limit = getattr(args, "limit", None)
    if limit is not None:
        filtered = filtered[: max(limit, 0)]
    fields = _fields_from_args(args) if apply_fields else []
    if fields:
        filtered = [
            {field: item.get(field) for field in fields if isinstance(item, dict)}
            for item in filtered
        ]
    return filtered


def _item_matches_category(item, needle: str) -> bool:
    if not isinstance(item, dict):
        return False
    values = [
        item.get("category_name"),
        item.get("category"),
        item.get("categoryName"),
        item.get("category_id"),
        item.get("categoryId"),
    ]
    return any(needle in str(value or "").lower() for value in values)


def _project_detail_response(response: dict, args: argparse.Namespace) -> dict:
    if response.get("ok") is not True or args.oa_command != "detail_read":
        return response
    detail = response.get("result")
    if not isinstance(detail, dict):
        return response
    projection = getattr(args, "oa_detail_projection", None)
    if projection in {"attachments", "workflow", "actions"}:
        items = detail.get(projection) if isinstance(detail.get(projection), list) else []
        return {
            **response,
            "result": {
                "title": detail.get("title", ""),
                "url": detail.get("url", ""),
                "count": len(items),
                "items": items,
            },
        }
    return {**response, "result": _project_detail(detail, args)}


def _project_detail(detail: dict, args: argparse.Namespace) -> dict:
    include = _include_from_args(args)
    projected = {}
    for key in include:
        if key not in detail:
            continue
        value = detail[key]
        if key == "text" and isinstance(value, str):
            limit = max(getattr(args, "text_limit", 3000), 0)
            value = value[:limit]
        projected[key] = value
    return projected


def _include_from_args(args: argparse.Namespace) -> list[str]:
    value = getattr(args, "include", "title,text,fields,attachments,workflow")
    return [part.strip() for part in value.split(",") if part.strip()]


def _index_detail_entries(source_item: dict, detail: dict, key: str) -> list[dict]:
    entries = detail.get(key)
    if not isinstance(entries, list):
        return []
    indexed = []
    for entry in entries:
        row = {
            "source_title": source_item.get("title", ""),
            "source_href": source_item.get("href", ""),
        }
        if isinstance(entry, dict):
            row.update(entry)
        else:
            row["text"] = str(entry)
        indexed.append(row)
    return indexed


def emit_cli_value(value, args: argparse.Namespace) -> None:
    output_format = getattr(args, "format", "json")
    if output_format == "json":
        print_json(value)
        return
    items = _items_for_output(value)
    if output_format == "csv":
        print_csv(items, _fields_from_args(args))
        return
    print_table(items, _fields_from_args(args))


def _items_for_output(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        result = value.get("result")
        if isinstance(result, dict) and isinstance(result.get("items"), list):
            return result["items"]
        if isinstance(value.get("items"), list):
            return value["items"]
        if isinstance(result, dict):
            return [result]
    return [value]


def _fields_from_args(args: argparse.Namespace) -> list[str]:
    value = getattr(args, "fields", None)
    if not value:
        return []
    return [field.strip() for field in value.split(",") if field.strip()]


def print_csv(items: list, fields: list[str] | None = None) -> None:
    rows = [item if isinstance(item, dict) else {"value": item} for item in items]
    fieldnames = fields or _fieldnames_from_rows(rows)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _cell_value(row.get(key)) for key in fieldnames})
    print(output.getvalue(), end="")


def print_table(items: list, fields: list[str] | None = None) -> None:
    rows = [item if isinstance(item, dict) else {"value": item} for item in items]
    fieldnames = fields or _fieldnames_from_rows(rows)
    if not fieldnames:
        return
    table_rows = [
        [str(_cell_value(row.get(field, ""))) for field in fieldnames]
        for row in rows
    ]
    widths = [
        max(len(field), *(len(row[index]) for row in table_rows)) if table_rows else len(field)
        for index, field in enumerate(fieldnames)
    ]
    print("  ".join(field.ljust(widths[index]) for index, field in enumerate(fieldnames)))
    print("  ".join("-" * width for width in widths))
    for row in table_rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def _fieldnames_from_rows(rows: list[dict]) -> list[str]:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    return fieldnames


def _cell_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, int | float | bool):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def run_command_via_daemon(
    daemon_url: str,
    system: str,
    command: str,
    arguments: dict,
    *,
    home: Path | None = None,
) -> dict:
    response = post_json(
        f"{daemon_url}/commands/run",
        {
            "system": system,
            "command": command,
            "args": arguments,
            "timeout_seconds": 30,
        },
        home=home,
    )
    if not response.get("ok"):
        raise RuntimeError(response.get("error") or f"daemon command failed: {system}.{command}")
    return response.get("result")


def _discovered_api_summary(api: DiscoveredApi) -> dict:
    inspection = api.inspection or {}
    return {
        "system": api.system,
        "name": api.name,
        "tool_name": api.tool_name,
        "description": api.description,
        "method": api.request.get("method", "GET"),
        "url": api.request.get("url", ""),
        "access": api.access,
        "risk": api.risk,
        "requires_confirmation": api.requires_confirmation,
        "data_shape": inspection.get("data_shape", ""),
        "item_count": inspection.get("item_count"),
        "sample_fields": inspection.get("sample_fields") or [],
    }


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


def post_json(
    url: str,
    payload: dict,
    *,
    timeout: float = 10,
    home: Path | None = None,
) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"content-type": "application/json; charset=utf-8"}
    headers.update(_daemon_token_headers(home))
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        finally:
            exc.close()
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            raise RuntimeError(f"HTTP {exc.code} from {url}: {body or exc.reason}") from exc


def get_json(url: str, *, home: Path | None = None) -> dict:
    request = urllib.request.Request(url, headers=_daemon_token_headers(home), method="GET")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _daemon_token_headers(home: Path | None) -> dict[str, str]:
    if home is None:
        home = Path.home() / ".bscli"
    token_path = Path(home) / DAEMON_TOKEN_FILENAME
    if not token_path.exists():
        return {}
    token = token_path.read_text(encoding="utf-8").strip()
    if not token:
        return {}
    return {"x-bscli-token": token}


def print_json(value) -> None:
    try:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    except UnicodeEncodeError:
        print(json.dumps(value, ensure_ascii=True, indent=2))


def print_json_compact(value) -> None:
    try:
        print(json.dumps(value, ensure_ascii=False))
    except UnicodeEncodeError:
        print(json.dumps(value, ensure_ascii=True))


def _origin_from_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("url must include scheme and host")
    return f"{parsed.scheme}://{parsed.netloc}"


if __name__ == "__main__":
    raise SystemExit(main())
