from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, UTC
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from bscli.adapters.seeyon import build_seeyon_profile
from bscli.browser.bridge import ExtensionBridge
from bscli.adapters.seeyon_home import (
    parse_navigation_inventory,
    parse_pending_list,
    parse_pending_projection,
    parse_sent_projection,
    parse_template_projection,
    parse_template_list,
)
from bscli.core.api_discovery import extract_api_candidates, inspect_api_response
from bscli.core.config import ConfigStore, SystemProfile
from bscli.core.discovered import DiscoveredApiStore
from bscli.core.trace import TraceStore

COMMAND_TASKS = {
    ("oa", "api_inspect"): "page_fetch",
    ("oa", "api_replay"): "page_fetch",
    ("oa", "api_save"): "page_fetch",
    ("oa", "current_page_snapshot"): "dom_snapshot",
    ("oa", "navigation_inventory"): "html_snapshot",
    ("oa", "network_api_candidates"): "network_log_snapshot",
    ("oa", "network_log_snapshot"): "network_log_snapshot",
    ("oa", "network_probe_install"): "network_probe_install",
    ("oa", "page_inventory"): "page_inventory",
    ("oa", "pending_detail"): "html_snapshot",
    ("oa", "pending_list"): "html_snapshot",
    ("oa", "pending_list_api"): "section_api_replay",
    ("oa", "sent_list_api"): "section_api_replay",
    ("oa", "template_detail"): "html_snapshot",
    ("oa", "template_list"): "html_snapshot",
    ("oa", "template_list_api"): "section_api_replay",
}


@dataclass(frozen=True)
class DaemonResponse:
    status: int
    body: dict[str, Any]


class DaemonState:
    def __init__(self, config_store: ConfigStore):
        self.config_store = config_store
        self.bridge = ExtensionBridge()
        self.trace_store = TraceStore(config_store.root / "trace.db")

    def handle(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> DaemonResponse:
        query = query or {}
        body = body or {}
        try:
            return self._handle(method.upper(), path, query=query, body=body)
        except KeyError as exc:
            return DaemonResponse(404, {"error": str(exc)})
        except ValueError as exc:
            return DaemonResponse(400, {"error": str(exc)})

    def _handle(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str],
        body: dict[str, Any],
    ) -> DaemonResponse:
        if method == "GET" and path == "/health":
            return DaemonResponse(200, {"ok": True})

        if method == "GET" and path == "/extension/clients":
            return DaemonResponse(200, {"clients": self.bridge.list_clients()})

        if method == "POST" and path == "/extension/register":
            self.bridge.register_client(
                body["client_id"],
                tab_id=int(body["tab_id"]),
                url=body["url"],
                title=body.get("title", ""),
            )
            return DaemonResponse(200, {"ok": True})

        if method == "GET" and path == "/extension/tasks":
            client_id = query.get("client_id")
            if not client_id:
                raise ValueError("client_id is required")
            return DaemonResponse(200, {"tasks": self.bridge.poll_tasks(client_id)})

        if method == "POST" and path == "/extension/results":
            self.bridge.submit_result(
                client_id=body["client_id"],
                task_id=body["task_id"],
                ok=bool(body["ok"]),
                result=body.get("result"),
                error=body.get("error"),
            )
            return DaemonResponse(200, {"ok": True})

        if method == "GET" and path.startswith("/extension/results/"):
            task_id = path.removeprefix("/extension/results/")
            return DaemonResponse(200, self.bridge.get_result(task_id))

        if method == "POST" and path == "/explore/dom-snapshot":
            target_client_id = self._select_client_id_for_system(body["system"])
            if target_client_id is None:
                return DaemonResponse(
                    409,
                    {
                        "ok": False,
                        "error": f"no browser client is currently registered for system: {body['system']}; open the matching system tab and wait for the extension to register it",
                    },
                )
            task_id = self.bridge.enqueue_task(
                system=body["system"],
                kind="dom_snapshot",
                payload={"selector": body.get("selector", "body")},
                target_client_id=target_client_id,
            )
            return DaemonResponse(200, {"task_id": task_id})

        if method == "POST" and path == "/commands/run":
            return self._run_command_with_trace(body)

        return DaemonResponse(404, {"error": "not found"})

    def _run_command_with_trace(self, body: dict[str, Any]) -> DaemonResponse:
        system = body.get("system") or ""
        command = body.get("command") or ""
        args = body.get("args") or {}
        metadata = self._trace_metadata(system, command, args)
        run_id = self.trace_store.start_run(
            system=system,
            command=command,
            args=args,
            access=metadata["access"],
            strategy=metadata["strategy"],
        )
        try:
            response = self._run_command(body)
        except Exception as exc:
            self.trace_store.finish_run(run_id, status="error", error=str(exc))
            raise
        response_body = {**response.body, "run_id": run_id}
        if 200 <= response.status < 400 and response_body.get("ok", True) is not False:
            self.trace_store.finish_run(
                run_id,
                status="ok",
                result=_trace_result_summary(response_body),
            )
        else:
            self.trace_store.finish_run(
                run_id,
                status="error",
                result=_trace_result_summary(response_body),
                error=str(response_body.get("error") or f"HTTP {response.status}"),
            )
        return DaemonResponse(response.status, response_body)

    def _run_command(self, body: dict[str, Any]) -> DaemonResponse:
        system = body.get("system")
        command = body.get("command")
        if (system, command) == ("oa", "session_status"):
            return DaemonResponse(
                200,
                {
                    "ok": True,
                    "task_id": None,
                    "result": self._session_status(),
                },
            )
        if command == "discovered_run":
            return self._run_discovered_command(body)
        task_kind = COMMAND_TASKS.get((system, command))
        if task_kind is None:
            raise ValueError(f"unsupported command: {system}.{command}")
        if not self.bridge.list_clients():
            return DaemonResponse(
                409,
                {
                    "ok": False,
                    "error": "no Chrome extension client connected; start Chrome, load the BSCLI extension, and open the OA tab",
                },
            )
        target_client_id = self._select_client_id_for_system(system)
        if target_client_id is None:
            return DaemonResponse(
                409,
                {
                    "ok": False,
                    "error": f"no browser client is currently registered for system: {system}; open the matching system tab and wait for the extension to register it",
                },
            )

        args = body.get("args") or {}
        timeout_seconds = float(body.get("timeout_seconds", 30))
        if command == "pending_list_api":
            return self._run_section_api_command(
                system,
                target_client_id,
                timeout_seconds,
                section_bean_id="pendingSection",
                parser=parse_pending_projection,
                command_name="pending_list_api",
            )
        if command == "sent_list_api":
            return self._run_section_api_command(
                system,
                target_client_id,
                timeout_seconds,
                section_bean_id="sentSection",
                parser=parse_sent_projection,
                command_name="sent_list_api",
            )
        if command == "template_list_api":
            return self._run_section_api_command(
                system,
                target_client_id,
                timeout_seconds,
                section_bean_id="templeteSection",
                parser=parse_template_projection,
                command_name="template_list_api",
            )
        if command == "api_inspect":
            return self._run_api_inspect_command(system, target_client_id, args, timeout_seconds)
        if command == "api_save":
            return self._run_api_save_command(system, target_client_id, args, timeout_seconds)
        if command == "api_replay":
            payload = {
                "method": args.get("method", "GET").upper(),
                "url": args["url"],
                "headers": args.get("headers", {}),
                "body": args.get("body"),
            }
        else:
            payload = {"selector": args.get("selector", "body")}
        task_id = self.bridge.enqueue_task(
            system=system,
            kind=task_kind,
            payload=payload,
            target_client_id=target_client_id,
        )
        result = self.bridge.wait_for_result(task_id, timeout_seconds=timeout_seconds)
        if result is None:
            return DaemonResponse(
                504,
                {
                    "ok": False,
                    "task_id": task_id,
                    "error": f"command timed out waiting for Chrome extension result: {system}.{command}",
                },
            )
        if not result["ok"]:
            return DaemonResponse(
                500,
                {
                    "ok": False,
                    "task_id": task_id,
                    "error": result.get("error") or "extension task failed",
                },
            )
        command_result = result["result"]
        if command == "network_api_candidates":
            command_result = {
                "candidates": extract_api_candidates(command_result),
                "source": command_result,
            }
        elif command == "navigation_inventory":
            command_result = self._parse_navigation_inventory(command_result)
        elif command == "pending_list":
            command_result = self._parse_pending_list(command_result)
        elif command == "pending_detail":
            command_result = self._parse_pending_detail(command_result, args["affair_id"])
        elif command == "template_detail":
            command_result = self._parse_template_detail(command_result, args["template_id"])
        elif command == "template_list":
            command_result = self._parse_template_list(command_result)
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": task_id,
                "result": command_result,
            },
        )

    def _parse_pending_list(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        return parse_pending_list(
            snapshot.get("html", ""),
            base_url=snapshot.get("url") or "",
        )

    def _run_section_api_command(
        self,
        system: str,
        target_client_id: str,
        timeout_seconds: float,
        *,
        section_bean_id: str,
        parser,
        command_name: str,
    ) -> DaemonResponse:
        snapshot_task_id = self.bridge.enqueue_task(
            system=system,
            kind="network_log_snapshot",
            payload={},
            target_client_id=target_client_id,
        )
        snapshot_result = self.bridge.wait_for_result(
            snapshot_task_id,
            timeout_seconds=timeout_seconds,
        )
        if snapshot_result is None:
            return DaemonResponse(
                504,
                {
                    "ok": False,
                    "task_id": snapshot_task_id,
                    "error": f"command timed out waiting for network log snapshot: oa.{command_name}",
                },
            )
        if not snapshot_result["ok"]:
            return DaemonResponse(
                500,
                {
                    "ok": False,
                    "task_id": snapshot_task_id,
                    "error": snapshot_result.get("error") or "network log snapshot failed",
                },
            )
        section_url = self._find_section_resource_url(
            snapshot_result.get("result") or {},
            section_bean_id,
        )
        if not section_url:
            return DaemonResponse(
                409,
                {
                    "ok": False,
                    "task_id": snapshot_task_id,
                    "error": f"{section_bean_id} API candidate not found; refresh the OA home page or run DOM fallback",
                },
            )
        fetch_task_id = self.bridge.enqueue_task(
            system=system,
            kind="page_fetch",
            payload={"method": "GET", "url": section_url, "headers": {}, "body": None},
            target_client_id=target_client_id,
        )
        fetch_result = self.bridge.wait_for_result(
            fetch_task_id,
            timeout_seconds=timeout_seconds,
        )
        if fetch_result is None:
            return DaemonResponse(
                504,
                {
                    "ok": False,
                    "task_id": fetch_task_id,
                    "error": f"command timed out waiting for API replay: oa.{command_name}",
                },
            )
        if not fetch_result["ok"]:
            return DaemonResponse(
                500,
                {
                    "ok": False,
                    "task_id": fetch_task_id,
                    "error": fetch_result.get("error") or "API replay failed",
                },
            )
        replay = fetch_result.get("result") or {}
        if not replay.get("ok"):
            return DaemonResponse(
                502,
                {
                    "ok": False,
                    "task_id": fetch_task_id,
                    "error": f"{section_bean_id} API returned HTTP {replay.get('status')}",
                },
            )
        projection = replay.get("json")
        if not isinstance(projection, dict):
            return DaemonResponse(
                502,
                {
                    "ok": False,
                    "task_id": fetch_task_id,
                    "error": f"{section_bean_id} API response was not JSON",
                },
            )
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": fetch_task_id,
                "result": parser(
                    projection,
                    base_url=replay.get("url") or "",
                ),
            },
        )

    def _run_api_inspect_command(
        self,
        system: str,
        target_client_id: str,
        args: dict[str, Any],
        timeout_seconds: float,
    ) -> DaemonResponse:
        replay_response = self._run_page_fetch(
            system,
            target_client_id,
            args,
            timeout_seconds,
        )
        if replay_response.status != 200:
            return replay_response
        replay = replay_response.body["result"]
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": replay_response.body["task_id"],
                "result": {
                    "request": self._api_request_from_args(args),
                    "inspection": inspect_api_response(replay),
                },
            },
        )

    def _run_discovered_command(self, body: dict[str, Any]) -> DaemonResponse:
        system = body.get("system")
        args = body.get("args") or {}
        name = str(args.get("name") or "")
        if not name:
            raise ValueError("name is required")
        if not self.bridge.list_clients():
            return DaemonResponse(
                409,
                {
                    "ok": False,
                    "error": "no Chrome extension client connected; start Chrome, load the BSCLI extension, and open the target system tab",
                },
            )
        target_client_id = self._select_client_id_for_system(system)
        if target_client_id is None:
            return DaemonResponse(
                409,
                {
                    "ok": False,
                    "error": f"no browser client is currently registered for system: {system}; open the matching system tab and wait for the extension to register it",
                },
            )
        api = DiscoveredApiStore(self.config_store.root).load_api(system, name)
        policy_error = self._validate_discovered_api_policy(system, api)
        if policy_error:
            return DaemonResponse(403, {"ok": False, "error": policy_error})
        replay_response = self._run_page_fetch(
            system,
            target_client_id,
            api.request,
            float(body.get("timeout_seconds", 30)),
        )
        if replay_response.status != 200:
            return replay_response
        replay = replay_response.body["result"]
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": replay_response.body["task_id"],
                "result": {
                    "api": {
                        "system": api.system,
                        "name": api.name,
                        "description": api.description,
                        "tool_name": api.tool_name,
                        "access": api.access,
                        "risk": api.risk,
                    },
                    "request": api.request,
                    "inspection": inspect_api_response(replay),
                    "replay": replay,
                },
            },
        )

    def _run_api_save_command(
        self,
        system: str,
        target_client_id: str,
        args: dict[str, Any],
        timeout_seconds: float,
    ) -> DaemonResponse:
        name = _safe_discovery_name(str(args.get("name") or ""))
        if not name:
            raise ValueError("name is required")
        replay_response = self._run_page_fetch(
            system,
            target_client_id,
            args,
            timeout_seconds,
        )
        if replay_response.status != 200:
            return replay_response
        replay = replay_response.body["result"]
        inspection = inspect_api_response(replay)
        output_dir = self.config_store.root / "discovered" / system / "apis"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{name}.json"
        saved = {
            "schema_version": "bscli.discovered_api.v1",
            "name": name,
            "system": system,
            "description": str(args.get("description") or ""),
            "access": "read" if self._api_request_from_args(args)["method"] == "GET" else "write",
            "risk": "low" if self._api_request_from_args(args)["method"] == "GET" else "medium",
            "created_at": datetime.now(UTC).isoformat(),
            "request": self._api_request_from_args(args),
            "inspection": inspection,
        }
        output_path.write_text(
            json.dumps(saved, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": replay_response.body["task_id"],
                "result": {
                    "saved_path": str(output_path),
                    "api": saved,
                    "inspection": inspection,
                },
            },
        )

    def _run_page_fetch(
        self,
        system: str,
        target_client_id: str,
        args: dict[str, Any],
        timeout_seconds: float,
    ) -> DaemonResponse:
        payload = self._api_request_from_args(args)
        task_id = self.bridge.enqueue_task(
            system=system,
            kind="page_fetch",
            payload=payload,
            target_client_id=target_client_id,
        )
        result = self.bridge.wait_for_result(task_id, timeout_seconds=timeout_seconds)
        if result is None:
            return DaemonResponse(
                504,
                {
                    "ok": False,
                    "task_id": task_id,
                    "error": "command timed out waiting for API replay",
                },
            )
        if not result["ok"]:
            return DaemonResponse(
                500,
                {
                    "ok": False,
                    "task_id": task_id,
                    "error": result.get("error") or "API replay failed",
                },
            )
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": task_id,
                "result": result["result"],
            },
        )

    def _api_request_from_args(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "method": args.get("method", "GET").upper(),
            "url": args["url"],
            "headers": args.get("headers", {}),
            "body": args.get("body"),
        }

    def _validate_discovered_api_policy(self, system: str, api) -> str:
        request = api.request or {}
        method = str(request.get("method") or "GET").upper()
        if api.access != "read" or api.risk != "low" or method != "GET":
            return "discovered API runtime is read-only in v1; only low-risk GET APIs are allowed"
        profile = self._load_system_profile(system)
        parsed = urlparse(str(request.get("url") or ""))
        if parsed.scheme and parsed.netloc:
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if profile is None or origin not in profile.allowed_origins:
                return f"discovered API origin is not allowed for system {system}: {origin}"
        return ""

    def _trace_metadata(self, system: str, command: str, args: dict[str, Any]) -> dict[str, str]:
        if command == "discovered_run":
            try:
                api = DiscoveredApiStore(self.config_store.root).load_api(system, str(args.get("name") or ""))
                return {"access": api.access, "strategy": "page_fetch"}
            except Exception:
                return {"access": "read", "strategy": "page_fetch"}
        if command == "session_status":
            return {"access": "read", "strategy": "daemon_api"}
        task_kind = COMMAND_TASKS.get((system, command), "unknown")
        strategy = "page_fetch" if task_kind == "section_api_replay" else task_kind
        return {"access": "read", "strategy": strategy}

    def _find_section_resource_url(self, snapshot: dict[str, Any], section_bean_id: str) -> str:
        encoded_marker = f"sectionBeanId%22%3A%22{section_bean_id}"
        plain_marker = f'"sectionBeanId":"{section_bean_id}"'
        for resource in snapshot.get("resources") or []:
            name = resource.get("name", "")
            if (
                "managerName=sectionManager" in name
                and "managerMethod=doProjection" in name
                and (encoded_marker in name or plain_marker in name)
            ):
                return name
        return ""

    def _parse_navigation_inventory(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        return parse_navigation_inventory(
            snapshot.get("html", ""),
            base_url=snapshot.get("url") or "",
        )

    def _parse_pending_detail(self, snapshot: dict[str, Any], affair_id: str) -> dict[str, Any]:
        pending = self._parse_pending_list(snapshot)
        item = next(
            (
                entry
                for entry in pending.get("items", [])
                if str(entry.get("affair_id", "")) == str(affair_id)
            ),
            None,
        )
        return {"found": item is not None, "item": item, "count": pending.get("count", 0)}

    def _parse_template_list(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        return parse_template_list(
            snapshot.get("html", ""),
            base_url=snapshot.get("url") or "",
        )

    def _parse_template_detail(self, snapshot: dict[str, Any], template_id: str) -> dict[str, Any]:
        templates = self._parse_template_list(snapshot)
        item = next(
            (
                entry
                for entry in templates.get("items", [])
                if str(entry.get("template_id", "")) == str(template_id)
            ),
            None,
        )
        return {"found": item is not None, "item": item, "count": templates.get("count", 0)}

    def _session_status(self) -> dict[str, Any]:
        profile = self._load_system_profile("oa")
        clients = []
        for client in self.bridge.list_clients():
            matches_system = self._client_matches_system(client, profile)
            clients.append({**client, "matches_system": matches_system})
        matched = [client for client in clients if client["matches_system"]]
        suggestions = []
        if not matched:
            suggestions = [
                "Start Chrome, load the BSCLI extension, and open the OA tab: http://10.10.50.110/seeyon/main.do?method=main",
                "After login, run: python -m bscli.cli.main --home .bscli daemon status",
            ]
        return {
            "connected": bool(matched),
            "client_count": len(matched),
            "clients": clients,
            "suggestions": suggestions,
        }

    def _select_client_id_for_system(self, system: str | None) -> str | None:
        profile = self._load_system_profile(system)
        if profile is None:
            return None
        for client in self.bridge.list_clients():
            if self._client_matches_system(client, profile):
                return client["client_id"]
        return None

    def _load_system_profile(self, system: str | None) -> SystemProfile | None:
        if not system:
            return None
        try:
            return self.config_store.load_system(system)
        except KeyError:
            if system == "oa":
                return build_seeyon_profile()
            return None

    def _client_matches_system(
        self,
        client: dict[str, Any],
        profile: SystemProfile | None,
    ) -> bool:
        if profile is None:
            return False
        parsed = urlparse(client.get("url", ""))
        if not parsed.scheme or not parsed.netloc:
            return False
        origin = f"{parsed.scheme}://{parsed.netloc}"
        return origin in profile.allowed_origins


def serve(home: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    state = DaemonState(ConfigStore(home))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._serve()

        def do_POST(self) -> None:
            self._serve()

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _serve(self) -> None:
            parsed = urlparse(self.path)
            query = {
                key: values[-1]
                for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
            }
            request_body = self._read_json_body()
            response = state.handle(
                self.command,
                parsed.path,
                query=query,
                body=request_body,
            )
            payload = json.dumps(response.body, ensure_ascii=False).encode("utf-8")
            self.send_response(response.status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _read_json_body(self) -> dict[str, Any]:
            content_length = int(self.headers.get("content-length", "0"))
            if content_length == 0:
                return {}
            raw = self.rfile.read(content_length)
            return json.loads(raw.decode("utf-8"))

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"BSCLI daemon listening on http://{host}:{port}")
    server.serve_forever()


def _safe_discovery_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip())
    return value.strip(".-_")


def _trace_result_summary(response_body: dict[str, Any]) -> dict[str, Any]:
    result = response_body.get("result")
    summary: dict[str, Any] = {
        "ok": response_body.get("ok"),
        "task_id": response_body.get("task_id"),
    }
    if response_body.get("error"):
        summary["error"] = response_body["error"]
    if isinstance(result, dict):
        summary["result_keys"] = sorted(result.keys())
        for key in ("count", "total", "name", "source", "connected", "client_count"):
            if key in result:
                summary[key] = result[key]
        if isinstance(result.get("items"), list):
            summary["item_count"] = len(result["items"])
        inspection = result.get("inspection")
        if isinstance(inspection, dict):
            summary["inspection"] = {
                key: inspection.get(key)
                for key in ("response_type", "data_shape", "item_count", "status")
                if key in inspection
            }
        api = result.get("api")
        if isinstance(api, dict):
            summary["api"] = {
                key: api.get(key)
                for key in ("system", "name", "access", "risk", "tool_name")
                if key in api
            }
    elif result is not None:
        summary["result_type"] = type(result).__name__
    return summary
