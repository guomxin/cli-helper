from __future__ import annotations

import argparse
from dataclasses import asdict
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

    adapter = subparsers.add_parser("adapter")
    adapter_sub = adapter.add_subparsers(dest="action", required=True)
    parse_home = adapter_sub.add_parser("parse-seeyon-home")
    parse_home.add_argument("--kind", choices=["navigation", "pending", "templates"], required=True)
    parse_home.add_argument("--html-file", required=True)
    parse_home.add_argument("--base-url", default="http://10.10.50.110/seeyon/main.do?method=main")

    return parser


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
        run_args = {"name": args.name}
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


def post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"content-type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
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
