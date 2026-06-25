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

from bscli.adapters.seeyon import build_seeyon_profile, register_seeyon_commands
from bscli.adapters.seeyon_home import (
    parse_navigation_inventory,
    parse_pending_list,
    parse_template_list,
)
from bscli.adapters.seeyon_write import append_oa_write_audit, build_oa_write_plan
from bscli.core.config import ConfigStore, SystemProfile
from bscli.core.discovered import DiscoveredApi, DiscoveredApiStore
from bscli.core.registry import CommandRegistry
from bscli.core.tool_manifest import export_tool_manifest
from bscli.core.trace import TraceStore
from bscli.daemon.app import serve
from bscli.mcp.server import BscliMcpServer


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

    adapter = subparsers.add_parser("adapter")
    adapter_sub = adapter.add_subparsers(dest="action", required=True)
    parse_home = adapter_sub.add_parser("parse-seeyon-home")
    parse_home.add_argument("--kind", choices=["navigation", "pending", "templates"], required=True)
    parse_home.add_argument("--html-file", required=True)
    parse_home.add_argument("--base-url", default="http://10.10.50.110/seeyon/main.do?method=main")

    return parser


def _build_oa_parser(oa_sub) -> None:
    status = oa_sub.add_parser("status")
    status.set_defaults(oa_command="session_status")
    _add_daemon_options(status)

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

    nav = oa_sub.add_parser("nav")
    nav_sub = nav.add_subparsers(dest="oa_action", required=True)
    nav_list = nav_sub.add_parser("list")
    nav_list.set_defaults(oa_command="navigation_inventory")
    _add_daemon_options(nav_list)
    _add_output_options(nav_list)

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
    )

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

    write = oa_sub.add_parser("write")
    write_sub = write.add_subparsers(dest="oa_action", required=True)
    for mode in ("draft", "dry-run", "execute"):
        write_cmd = write_sub.add_parser(mode)
        write_cmd.set_defaults(oa_write_mode=mode)
        write_cmd.add_argument("--affair-id", required=True)
        write_cmd.add_argument("--action", required=True)
        write_cmd.add_argument("--opinion", default="")
        write_cmd.add_argument("--source-url", default="")
        if mode == "execute":
            write_cmd.add_argument("--confirm", action="store_true")
            _add_daemon_options(write_cmd)
        _add_output_options(write_cmd)


def _add_workflow_parser(subparsers) -> None:
    for action in ("list", "search", "export"):
        parser = subparsers.add_parser(action)
        parser.set_defaults(oa_workflow_action=action)
        _add_workflow_type_option(parser)
        if action == "search":
            parser.add_argument("--keyword", required=True)
        else:
            parser.add_argument("--keyword")
        _add_daemon_options(parser)
        _add_output_options(parser, default_format="csv" if action == "export" else "json")

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
) -> None:
    for action in ("list", "search", "export"):
        parser = subparsers.add_parser(action)
        parser.set_defaults(oa_command=list_command)
        if action == "search":
            parser.add_argument("--keyword", required=True)
        else:
            parser.add_argument("--keyword")
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
                "daemon": get_json(f"{args.daemon_url}/health"),
                "extension_clients": get_json(f"{args.daemon_url}/extension/clients")[
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
        result = post_json(f"{args.daemon_url}/explore/dom-snapshot", payload)
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
        result = post_json(f"{args.daemon_url}/commands/run", payload)
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
    if getattr(args, "oa_write_mode", None):
        return handle_oa_write(args, home)
    if getattr(args, "oa_pending_submit", False):
        return handle_oa_pending_submit(args)
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


def handle_oa_write(args: argparse.Namespace, home: Path) -> int:
    plan = build_oa_write_plan(
        affair_id=args.affair_id,
        action=args.action,
        opinion=args.opinion,
        mode=args.oa_write_mode,
        source_url=args.source_url,
    )
    if args.oa_write_mode == "dry-run":
        append_oa_write_audit(home, plan)
        emit_cli_value(plan, args)
        return 0
    if args.oa_write_mode == "execute":
        if getattr(args, "confirm", False):
            response = run_oa_daemon_command(
                args,
                "write_execute",
                {
                    "affair_id": args.affair_id,
                    "action": args.action,
                    "opinion": args.opinion,
                    "source_url": args.source_url,
                    "confirm": True,
                },
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
    return post_json(
        f"{args.daemon_url}/commands/run",
        {
            "system": "oa",
            "command": command,
            "args": command_args,
            "timeout_seconds": args.timeout,
        },
        timeout=max(float(args.timeout) + 5, 10),
    )


def _oa_command_args(args: argparse.Namespace) -> dict:
    command = args.oa_command
    if command == "pending_detail":
        return {"affair_id": args.affair_id}
    if command == "template_detail":
        return {"template_id": args.template_id}
    if command == "detail_read":
        return {"url": args.url}
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
    return api_args


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
) -> dict:
    response = post_json(
        f"{daemon_url}/commands/run",
        {
            "system": system,
            "command": command,
            "args": arguments,
            "timeout_seconds": 30,
        },
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


def post_json(url: str, payload: dict, *, timeout: float = 10) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"content-type": "application/json; charset=utf-8"},
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


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


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
