from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, UTC
from difflib import SequenceMatcher
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from bscli.adapters.seeyon import build_seeyon_profile, register_seeyon_commands
from bscli.browser.bridge import ExtensionBridge
from bscli.adapters.seeyon_home import (
    TEMPLATE_CENTER_API_URL,
    extract_history_sections,
    parse_launch_page,
    parse_oa_detail,
    parse_navigation_inventory,
    parse_pending_list,
    parse_pending_projection,
    parse_sent_projection,
    parse_template_center_response,
    parse_template_list,
)
from bscli.adapters.seeyon_matter_intent import build_matter_intent_preflight
from bscli.adapters.seeyon_write import (
    append_oa_launch_draft_audit,
    append_oa_write_audit,
    append_oa_write_verification_audit,
    build_oa_launch_draft_plan,
    build_oa_write_plan,
    build_oa_write_preflight,
    build_write_governance,
    classify_write_endpoint_candidates,
    is_dry_run_only_write_action,
    mark_oa_launch_draft_plan_for_execution,
    normalize_write_action,
    normalize_launch_field_values,
    write_action_promotion,
    write_action_type,
)
from bscli.core.api_discovery import extract_api_candidates, inspect_api_response
from bscli.core.config import ConfigStore, SystemProfile
from bscli.core.discovered import DiscoveredApiStore, render_discovered_request
from bscli.core.registry import CommandRegistry
from bscli.core.trace import TraceStore

COMMAND_TASKS = {
    ("oa", "api_inspect"): "page_fetch",
    ("oa", "api_replay"): "page_fetch",
    ("oa", "api_save"): "page_fetch",
    ("oa", "current_page_snapshot"): "dom_snapshot",
    ("oa", "detail_read"): "rendered_html_snapshot",
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
    ("oa", "template_list_api"): "page_fetch",
}

WORKFLOW_READ_COMMANDS = {
    "workflow_brief",
    "workflow_list",
    "workflow_detail",
    "workflow_evidence",
    "workflow_inspect",
    "workflow_opinions",
    "workflow_attachments",
    "workflow_actions",
    "workflow_timeline",
}

HISTORY_READ_COMMANDS = {
    "history_sections",
    "history_list",
    "history_profile",
}

INBOX_READ_COMMANDS = {
    "inbox_analyze",
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
        if system == "oa" and command == "doctor":
            return self._run_oa_doctor_command()
        if system == "oa" and command == "capability_map":
            return self._run_oa_capability_map_command()
        if system == "oa" and command in HISTORY_READ_COMMANDS:
            return self._run_oa_history_read_command(
                command,
                body.get("args") or {},
                timeout_seconds=float(body.get("timeout_seconds", 30)),
            )
        if system == "oa" and command == "template_match":
            return self._run_oa_template_match_command(
                body.get("args") or {},
                timeout_seconds=float(body.get("timeout_seconds", 30)),
            )
        if system == "oa" and command in {"matter_profile", "matter_inspect"}:
            return self._run_oa_matter_command(
                command,
                body.get("args") or {},
                timeout_seconds=float(body.get("timeout_seconds", 30)),
            )
        if system == "oa" and command == "matter_preflight":
            return self._run_oa_matter_preflight_command(
                body.get("args") or {},
                timeout_seconds=float(body.get("timeout_seconds", 30)),
            )
        if system == "oa" and command == "launch_inspect":
            return self._run_oa_launch_inspect_command(
                body.get("args") or {},
                timeout_seconds=float(body.get("timeout_seconds", 30)),
            )
        if system == "oa" and command in {"launch_dry_run", "launch_save_draft"}:
            return self._run_oa_launch_draft_command(
                command,
                body.get("args") or {},
                timeout_seconds=float(body.get("timeout_seconds", 30)),
            )
        if system == "oa" and command == "write_capabilities":
            return self._run_oa_write_capabilities_command(
                body.get("args") or {},
                timeout_seconds=float(body.get("timeout_seconds", 30)),
            )
        if system == "oa" and command == "write_discover":
            return self._run_oa_write_discover_command(
                body.get("args") or {},
                timeout_seconds=float(body.get("timeout_seconds", 30)),
            )
        if system == "oa" and command == "write_endpoint_candidates":
            return self._run_oa_write_endpoint_candidates_command(
                body.get("args") or {},
                timeout_seconds=float(body.get("timeout_seconds", 30)),
            )
        if system == "oa" and command == "write_preflight":
            return self._run_oa_write_preflight_command(
                body.get("args") or {},
                timeout_seconds=float(body.get("timeout_seconds", 30)),
            )
        if system == "oa" and command == "write_prepare":
            return self._run_oa_write_prepare_command(
                body.get("args") or {},
                timeout_seconds=float(body.get("timeout_seconds", 30)),
            )
        if system == "oa" and command in {"write_draft", "write_dry_run", "write_execute"}:
            return self._run_oa_write_plan_command(
                command,
                body.get("args") or {},
                timeout_seconds=float(body.get("timeout_seconds", 30)),
            )
        if system == "oa" and command == "pending_submit":
            return self._run_oa_pending_submit_command(
                body.get("args") or {},
                timeout_seconds=float(body.get("timeout_seconds", 30)),
            )
        if system == "oa" and command in {"meeting_reply_dry_run", "meeting_reply_execute"}:
            return self._run_oa_meeting_reply_command(
                command,
                body.get("args") or {},
                timeout_seconds=float(body.get("timeout_seconds", 30)),
            )
        if system == "oa" and command in INBOX_READ_COMMANDS:
            return self._run_oa_inbox_read_command(
                command,
                body.get("args") or {},
                timeout_seconds=float(body.get("timeout_seconds", 30)),
            )
        if system == "oa" and command in WORKFLOW_READ_COMMANDS:
            return self._run_oa_workflow_read_command(
                command,
                body.get("args") or {},
                timeout_seconds=float(body.get("timeout_seconds", 30)),
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
            return self._run_template_center_command(
                system,
                target_client_id,
                timeout_seconds,
            )
        if command == "api_inspect":
            return self._run_api_inspect_command(system, target_client_id, args, timeout_seconds)
        if command == "api_save":
            return self._run_api_save_command(system, target_client_id, args, timeout_seconds)
        if command == "detail_read":
            return self._run_detail_read_command(system, target_client_id, args, timeout_seconds)
        if command == "api_replay":
            payload = {
                "method": args.get("method", "GET").upper(),
                "url": args["url"],
                "headers": args.get("headers", {}),
                "body": args.get("body"),
            }
            if args.get("max_text") is not None:
                payload["max_text"] = int(args["max_text"])
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
        section_arguments: dict[str, Any] | None = None,
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
        section_url = _section_url_with_arguments(
            section_url,
            {"sectionBeanId": section_bean_id, **(section_arguments or {})},
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

    def _run_template_center_command(
        self,
        system: str,
        target_client_id: str,
        timeout_seconds: float,
    ) -> DaemonResponse:
        profile = self._load_system_profile(system)
        base_url = profile.base_url if profile is not None else ""
        replay_response = self._run_page_fetch(
            system,
            target_client_id,
            {
                "method": "GET",
                "url": urljoin(base_url, TEMPLATE_CENTER_API_URL),
                "headers": {},
                "body": None,
                "max_text": 1000000,
            },
            timeout_seconds,
        )
        if replay_response.status != 200:
            return replay_response
        replay = replay_response.body["result"]
        if not replay.get("ok"):
            return DaemonResponse(
                502,
                {
                    "ok": False,
                    "task_id": replay_response.body["task_id"],
                    "error": f"template center API returned HTTP {replay.get('status')}",
                },
            )
        payload = replay.get("json")
        if not isinstance(payload, dict):
            text = str(replay.get("text") or "")
            try:
                loaded = json.loads(text)
            except json.JSONDecodeError:
                loaded = None
            payload = loaded if isinstance(loaded, dict) else None
        if not isinstance(payload, dict):
            return DaemonResponse(
                502,
                {
                    "ok": False,
                    "task_id": replay_response.body["task_id"],
                    "error": "template center API response was not JSON",
                },
            )
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": replay_response.body["task_id"],
                "result": parse_template_center_response(
                    payload,
                    base_url=replay.get("url") or base_url,
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
        policy_response = self._validate_discovered_api_policy(
            system,
            api,
            confirmed=args.get("confirm") is True,
        )
        if policy_response:
            return policy_response
        request = render_discovered_request(api, args)
        replay_response = self._run_page_fetch(
            system,
            target_client_id,
            request,
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
                    "request": request,
                    "inspection": inspect_api_response(replay),
                    "replay": replay,
                },
            },
        )

    def _run_detail_read_command(
        self,
        system: str,
        target_client_id: str,
        args: dict[str, Any],
        timeout_seconds: float,
    ) -> DaemonResponse:
        task_id = self.bridge.enqueue_task(
            system=system,
            kind="rendered_html_snapshot",
            payload={"url": args["url"], "settle_ms": int(args.get("settle_ms", 1500))},
            target_client_id=target_client_id,
        )
        result = self.bridge.wait_for_result(task_id, timeout_seconds=timeout_seconds)
        if result is None:
            return DaemonResponse(
                504,
                {
                    "ok": False,
                    "task_id": task_id,
                    "error": "command timed out waiting for rendered detail page",
                },
            )
        if not result["ok"]:
            return DaemonResponse(
                500,
                {
                    "ok": False,
                    "task_id": task_id,
                    "error": result.get("error") or "rendered detail page failed",
                },
            )
        snapshot = result.get("result") or {}
        html = str(snapshot.get("html") or snapshot.get("text") or "")
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": task_id,
                "result": parse_oa_detail(html, base_url=snapshot.get("url") or args["url"]),
            },
        )

    def _run_oa_workflow_read_command(
        self,
        command: str,
        args: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> DaemonResponse:
        if command == "workflow_brief":
            return self._run_oa_workflow_brief_command(args, timeout_seconds)
        if command == "workflow_list":
            return self._run_oa_workflow_list_command(args, timeout_seconds)
        if command == "workflow_inspect":
            return self._run_oa_workflow_inspect_command(args, timeout_seconds)
        if command == "workflow_evidence":
            return self._run_oa_workflow_evidence_command(args, timeout_seconds)
        if command == "workflow_timeline":
            return self._run_oa_workflow_timeline_command(args, timeout_seconds)
        if command == "workflow_detail":
            return self._run_oa_workflow_detail_command(args, timeout_seconds)
        projection_key = {
            "workflow_opinions": "workflow",
            "workflow_attachments": "attachments",
            "workflow_actions": "actions",
        }[command]
        return self._run_oa_workflow_projection_command(
            args,
            projection_key=projection_key,
            timeout_seconds=timeout_seconds,
        )

    def _run_oa_inbox_read_command(
        self,
        command: str,
        args: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> DaemonResponse:
        if command == "inbox_analyze":
            return self._run_oa_inbox_analyze_command(args, timeout_seconds)
        raise ValueError(f"unsupported inbox command: {command}")

    def _run_oa_inbox_analyze_command(
        self,
        args: dict[str, Any],
        timeout_seconds: float,
    ) -> DaemonResponse:
        workflow_type = self._workflow_type(args)
        brief_args: dict[str, Any] = {"type": workflow_type}
        for key in ("keyword", "limit"):
            if args.get(key) is not None:
                brief_args[key] = args[key]
        brief_response = self._run_nested_oa_command("workflow_brief", brief_args, timeout_seconds)
        if brief_response.status != 200 or brief_response.body.get("ok") is False:
            return brief_response

        brief_result = brief_response.body.get("result") if isinstance(brief_response.body, dict) else {}
        if not isinstance(brief_result, dict):
            brief_result = {}
        source_items = self._workflow_items_from_result(brief_result)
        analyzed_items = [
            _inbox_analysis_item(index, item, workflow_type)
            for index, item in enumerate(source_items, start=1)
            if isinstance(item, dict)
        ]
        analyzed_items = _rank_inbox_items(analyzed_items)

        deep = args.get("deep") is True
        deep_count = 0
        deep_attempt_count = 0
        deep_errors = []
        if deep:
            deep_limit = _inbox_deep_limit_from_args(args)
            evidence_args_base = {"type": workflow_type}
            if args.get("text_limit") is not None:
                evidence_args_base["text_limit"] = _workflow_text_limit_from_args(args)
            for item in analyzed_items:
                if deep_count >= deep_limit:
                    break
                evidence_args = dict(evidence_args_base)
                affair_id = str(item.get("affair_id") or "").strip()
                href = str(item.get("href") or "").strip()
                if affair_id:
                    evidence_args["id"] = affair_id
                elif href:
                    evidence_args["url"] = href
                else:
                    continue
                deep_attempt_count += 1
                evidence_response = self._run_nested_oa_command("workflow_evidence", evidence_args, timeout_seconds)
                if evidence_response.status != 200 or evidence_response.body.get("ok") is False:
                    deep_errors.append(
                        {
                            "affair_id": affair_id,
                            "title": item.get("title", ""),
                            "error": evidence_response.body.get("error", f"HTTP {evidence_response.status}"),
                        }
                    )
                    continue
                evidence_result = evidence_response.body.get("result") if isinstance(evidence_response.body, dict) else {}
                if not isinstance(evidence_result, dict):
                    evidence_result = {}
                evidence = evidence_result.get("evidence") if isinstance(evidence_result.get("evidence"), dict) else {}
                _enrich_inbox_item_with_evidence(item, evidence)
                deep_count += 1
            analyzed_items = _rank_inbox_items(analyzed_items)

        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": brief_response.body.get("task_id"),
                "result": {
                    "type": workflow_type,
                    "mode": "deep" if deep else "list_only",
                    "source": brief_result.get("source") or "workflow_brief",
                    "source_count": brief_result.get("source_count", len(source_items)),
                    "count": len(analyzed_items),
                    "deep_attempt_count": deep_attempt_count,
                    "deep_count": deep_count,
                    "items": analyzed_items,
                    "deep_errors": deep_errors,
                    "read_effect": _workflow_read_effect(workflow_type, detail_page_opened=deep_attempt_count > 0),
                },
            },
        )

    def _run_oa_history_read_command(
        self,
        command: str,
        args: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> DaemonResponse:
        if command == "history_sections":
            return self._run_oa_history_sections_command(args, timeout_seconds)
        if command == "history_list":
            return self._run_oa_history_list_command(args, timeout_seconds)
        if command == "history_profile":
            return self._run_oa_history_profile_command(args, timeout_seconds)
        raise ValueError(f"unsupported history command: {command}")

    def _run_oa_history_sections_command(
        self,
        args: dict[str, Any],
        timeout_seconds: float,
    ) -> DaemonResponse:
        nav_response = self._run_nested_oa_command("navigation_inventory", {}, timeout_seconds)
        if nav_response.status != 200 or nav_response.body.get("ok") is False:
            return nav_response
        nav_result = nav_response.body.get("result") if isinstance(nav_response.body, dict) else {}
        if not isinstance(nav_result, dict):
            nav_result = {}
        projected = extract_history_sections(nav_result)
        kind = self._history_kind(args, default="")
        if kind:
            items = [item for item in projected["items"] if item.get("kind") == kind]
            projected = {**projected, "kind": kind, "count": len(items), "items": items}
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": nav_response.body.get("task_id"),
                "result": projected,
            },
        )

    def _run_oa_history_list_command(
        self,
        args: dict[str, Any],
        timeout_seconds: float,
    ) -> DaemonResponse:
        kind = self._history_kind(args)
        sections_response = self._run_oa_history_sections_command({"kind": kind}, timeout_seconds)
        if sections_response.status != 200 or sections_response.body.get("ok") is False:
            return sections_response
        sections_result = sections_response.body.get("result") if isinstance(sections_response.body, dict) else {}
        section_items = sections_result.get("items") if isinstance(sections_result, dict) else []
        if not isinstance(section_items, list):
            section_items = []
        history_tab = next((item for item in section_items if isinstance(item, dict) and item.get("kind") == kind), None)
        if history_tab is None:
            return DaemonResponse(
                404,
                {
                    "ok": False,
                    "error": f"history tab not found: {kind}",
                    "result": {"kind": kind, "count": 0, "items": []},
                    "suggestions": [
                        "Run: python -m bscli.cli.main --home .bscli oa history sections --format json",
                        "Refresh the OA home page if the historical section is not loaded.",
                    ],
                },
            )
        if not self.bridge.list_clients():
            return DaemonResponse(
                409,
                {
                    "ok": False,
                    "error": "no Chrome extension client connected; start Chrome, load the BSCLI extension, and open the OA tab",
                },
            )
        target_client_id = self._select_client_id_for_system("oa")
        if target_client_id is None:
            return DaemonResponse(
                409,
                {
                    "ok": False,
                    "error": "no browser client is currently registered for system: oa; open the matching system tab and wait for the extension to register it",
                },
            )
        list_response = self._run_section_api_command(
            "oa",
            target_client_id,
            timeout_seconds,
            section_bean_id=str(history_tab.get("section_bean_id") or "sentSection"),
            parser=parse_sent_projection,
            command_name="history_list",
            section_arguments={
                "entityId": history_tab.get("section_id", ""),
                "panelId": history_tab.get("tab_id", ""),
            },
        )
        if list_response.status != 200 or list_response.body.get("ok") is False:
            return list_response
        result = list_response.body.get("result") if isinstance(list_response.body, dict) else {}
        if not isinstance(result, dict):
            result = {}
        source_items = self._workflow_items_from_result(result)
        enriched = []
        for item in source_items:
            if not isinstance(item, dict):
                continue
            enriched.append(
                {
                    **item,
                    "history_kind": kind,
                    "history_name": history_tab.get("name", ""),
                    "history_tab_id": history_tab.get("tab_id", ""),
                }
            )
        filtered = self._filter_workflow_items(enriched, args)
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": list_response.body.get("task_id"),
                "result": {
                    "schema_version": "bscli.oa_history_list.v1",
                    "kind": kind,
                    "source": "history_section_api",
                    "base_source": result.get("source") or "section_api",
                    "name": result.get("name", ""),
                    "total": result.get("total"),
                    "page": result.get("page"),
                    "history_tab": history_tab,
                    "source_count": len(enriched),
                    "count": len(filtered),
                    "items": filtered,
                    "read_effect": _history_read_effect(kind, detail_page_opened=False),
                },
            },
        )

    def _run_oa_history_profile_command(
        self,
        args: dict[str, Any],
        timeout_seconds: float,
    ) -> DaemonResponse:
        kind = _history_profile_kind(args)
        kinds = ["done", "sent", "tracked"] if kind == "all" else [kind]
        list_results = []
        task_ids = []
        for one_kind in kinds:
            list_args: dict[str, Any] = {"kind": one_kind}
            for key in ("keyword", "limit"):
                if args.get(key) is not None:
                    list_args[key] = args[key]
            response = self._run_nested_oa_command("history_list", list_args, timeout_seconds)
            if response.status != 200 or response.body.get("ok") is False:
                return response
            if response.body.get("task_id"):
                task_ids.append(response.body.get("task_id"))
            result = response.body.get("result") if isinstance(response.body, dict) else {}
            if isinstance(result, dict):
                list_results.append(result)
        profile = _build_oa_history_profile(kind=kind, list_results=list_results)
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": task_ids[0] if task_ids else None,
                "result": profile,
            },
        )

    def _run_oa_template_match_command(
        self,
        args: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> DaemonResponse:
        profile_args = {}
        for key in ("kind", "keyword", "limit"):
            if args.get(key) is not None:
                profile_args[key] = args[key]
        profile_response = self._run_nested_oa_command("history_profile", profile_args, timeout_seconds)
        if profile_response.status != 200 or profile_response.body.get("ok") is False:
            return profile_response
        template_response = self._run_nested_oa_command("template_list_api", {}, timeout_seconds)
        if template_response.status != 200 or template_response.body.get("ok") is False:
            return template_response
        profile = profile_response.body.get("result") if isinstance(profile_response.body, dict) else {}
        templates = template_response.body.get("result") if isinstance(template_response.body, dict) else {}
        if not isinstance(profile, dict):
            profile = {}
        if not isinstance(templates, dict):
            templates = {}
        result = _build_oa_template_match(profile, templates)
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": template_response.body.get("task_id") or profile_response.body.get("task_id"),
                "result": result,
            },
        )

    def _run_oa_matter_command(
        self,
        command: str,
        args: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> DaemonResponse:
        profile_args = _matter_profile_args(args)
        if command == "matter_profile":
            match_response = self._run_nested_oa_command("template_match", profile_args, timeout_seconds)
            if match_response.status != 200 or match_response.body.get("ok") is False:
                return match_response
            match_result = match_response.body.get("result") if isinstance(match_response.body, dict) else {}
            if not isinstance(match_result, dict):
                match_result = {}
            return DaemonResponse(
                200,
                {
                    "ok": True,
                    "task_id": match_response.body.get("task_id"),
                    "result": _build_oa_matter_profile(match_result, profile_args),
                },
            )

        profile_response = self._run_nested_oa_command("matter_profile", profile_args, timeout_seconds)
        if profile_response.status != 200 or profile_response.body.get("ok") is False:
            return profile_response
        profile = profile_response.body.get("result") if isinstance(profile_response.body, dict) else {}
        if not isinstance(profile, dict):
            profile = {}
        matter = _find_oa_matter(profile.get("matters") or [], args)
        if matter is None:
            return DaemonResponse(
                404,
                {
                    "ok": False,
                    "error": "matter not found; use oa matter profile to list available matter ids and names",
                    "result": {
                        "schema_version": "bscli.oa_matter_inspection.v1",
                        "query": _matter_query(args),
                        "profile": {
                            "kind": profile.get("kind", ""),
                            "matter_count": profile.get("matter_count", 0),
                        },
                    },
                },
            )
        launch_inspection: dict[str, Any] = {}
        if args.get("with_launch") is True:
            template = matter.get("template") if isinstance(matter.get("template"), dict) else {}
            template_id = str(template.get("template_id") or "")
            if not template_id:
                return DaemonResponse(
                    409,
                    {
                        "ok": False,
                        "error": "matter has no matched template to inspect",
                        "result": _build_oa_matter_inspection(profile, matter, launch_inspection={}),
                    },
                )
            inspect_args: dict[str, Any] = {"template_id": template_id}
            if args.get("settle_ms") is not None:
                inspect_args["settle_ms"] = args.get("settle_ms")
            inspect_response = self._run_nested_oa_command("launch_inspect", inspect_args, timeout_seconds)
            if inspect_response.status != 200 or inspect_response.body.get("ok") is False:
                return inspect_response
            launch_result = inspect_response.body.get("result") if isinstance(inspect_response.body, dict) else {}
            if isinstance(launch_result, dict):
                launch_inspection = launch_result
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": profile_response.body.get("task_id"),
                "result": _build_oa_matter_inspection(profile, matter, launch_inspection=launch_inspection),
            },
        )

    def _run_oa_matter_preflight_command(
        self,
        args: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> DaemonResponse:
        workflow_id = str(args.get("id") or "").strip()
        if not workflow_id:
            list_args: dict[str, Any] = {"type": "pending"}
            for key in ("keyword", "limit"):
                if args.get(key) is not None:
                    list_args[key] = args[key]
            list_response = self._run_nested_oa_command("workflow_list", list_args, timeout_seconds)
            if list_response.status != 200 or list_response.body.get("ok") is False:
                return list_response
            list_result = list_response.body.get("result") if isinstance(list_response.body, dict) else {}
            if not isinstance(list_result, dict):
                list_result = {}
            source_items = self._workflow_items_from_result(list_result)
            if not source_items:
                return DaemonResponse(
                    404,
                    {
                        "ok": False,
                        "error": "no pending matter matched the preflight target",
                        "result": {
                            "schema_version": "bscli.oa_matter_intent_preflight.v1",
                            "scene": "received_pending",
                            "query": {
                                "keyword": str(args.get("keyword") or ""),
                                "limit": args.get("limit"),
                            },
                        },
                    },
                )
            source_item = source_items[0] if isinstance(source_items[0], dict) else {}
            workflow_id = str(source_item.get("affair_id") or "")
            if not workflow_id:
                return DaemonResponse(
                    409,
                    {
                        "ok": False,
                        "error": "matched pending matter has no affair_id",
                        "result": {"source_item": source_item},
                    },
                )
        evidence_args: dict[str, Any] = {"type": "pending", "id": workflow_id}
        if args.get("text_limit") is not None:
            evidence_args["text_limit"] = args.get("text_limit")
        evidence_response = self._run_nested_oa_command("workflow_evidence", evidence_args, timeout_seconds)
        if evidence_response.status != 200 or evidence_response.body.get("ok") is False:
            return evidence_response
        evidence_result = evidence_response.body.get("result") if isinstance(evidence_response.body, dict) else {}
        if not isinstance(evidence_result, dict):
            evidence_result = {}
        source_item = evidence_result.get("source_item") if isinstance(evidence_result.get("source_item"), dict) else {}
        evidence = evidence_result.get("evidence") if isinstance(evidence_result.get("evidence"), dict) else {}
        try:
            result = build_matter_intent_preflight(
                source_item=source_item,
                evidence=evidence,
                intent=str(args.get("intent") or ""),
                opinion=str(args.get("opinion") or ""),
            )
        except ValueError as exc:
            return DaemonResponse(
                400,
                {
                    "ok": False,
                    "error": str(exc),
                    "result": {
                        "schema_version": "bscli.oa_matter_intent_preflight.v1",
                        "scene": "received_pending",
                    },
                },
            )
        result["read_effect"] = evidence_result.get(
            "read_effect",
            _workflow_read_effect("pending", detail_page_opened=True),
        )
        return DaemonResponse(
            200,
            {
                "ok": True,
                "requires_confirmation": False,
                "confirmed": False,
                "result": result,
            },
        )

    def _run_oa_launch_inspect_command(
        self,
        args: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> DaemonResponse:
        template_id = str(args.get("template_id") or "").strip()
        url = str(args.get("url") or "").strip()
        template_item: dict[str, Any] = {}
        if not url and template_id:
            template_response = self._run_nested_oa_command("template_list_api", {}, timeout_seconds)
            if template_response.status != 200 or template_response.body.get("ok") is False:
                return template_response
            template_result = template_response.body.get("result") if isinstance(template_response.body, dict) else {}
            if not isinstance(template_result, dict):
                template_result = {}
            for item in template_result.get("items") or []:
                if isinstance(item, dict) and str(item.get("template_id") or "") == template_id:
                    template_item = item
                    url = str(item.get("href") or "")
                    break
            if not url:
                return DaemonResponse(
                    404,
                    {
                        "ok": False,
                        "error": f"template launch URL not found: {template_id}",
                        "result": {"schema_version": "bscli.oa_launch_inspection.v1", "template_id": template_id},
                    },
                )
        if not url:
            return DaemonResponse(
                400,
                {
                    "ok": False,
                    "error": "oa launch inspect requires template_id or url",
                    "result": {"schema_version": "bscli.oa_launch_inspection.v1"},
                },
            )
        if not self.bridge.list_clients():
            return DaemonResponse(
                409,
                {
                    "ok": False,
                    "error": "no Chrome extension client connected; start Chrome, load the BSCLI extension, and open the OA tab",
                },
            )
        target_client_id = self._select_client_id_for_system("oa")
        if target_client_id is None:
            return DaemonResponse(
                409,
                {
                    "ok": False,
                    "error": "no browser client is currently registered for system: oa; open the matching system tab and wait for the extension to register it",
                },
            )
        task_id = self.bridge.enqueue_task(
            system="oa",
            kind="rendered_html_snapshot",
            payload={"url": url, "settle_ms": int(args.get("settle_ms", 1500))},
            target_client_id=target_client_id,
        )
        result = self.bridge.wait_for_result(task_id, timeout_seconds=timeout_seconds)
        if result is None:
            return DaemonResponse(
                504,
                {
                    "ok": False,
                    "task_id": task_id,
                    "error": "command timed out waiting for rendered launch page",
                },
            )
        if not result["ok"]:
            return DaemonResponse(
                500,
                {
                    "ok": False,
                    "task_id": task_id,
                    "error": result.get("error") or "rendered launch page failed",
                },
            )
        snapshot = result.get("result") or {}
        html = str(snapshot.get("html") or snapshot.get("text") or "")
        inspected = parse_launch_page(html, base_url=snapshot.get("url") or url)
        if template_id:
            inspected["template_id"] = template_id
        if template_item:
            inspected["template"] = {
                "template_id": str(template_item.get("template_id") or template_id),
                "title": template_item.get("title", ""),
                "href": template_item.get("href", ""),
            }
        inspected["read_effect"] = _launch_read_effect(page_opened=True)
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": task_id,
                "result": inspected,
            },
        )

    def _run_oa_launch_draft_command(
        self,
        command: str,
        args: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> DaemonResponse:
        mode = "dry-run" if command == "launch_dry_run" else "save-draft"
        try:
            fields = normalize_launch_field_values(args.get("fields") or {})
        except ValueError as exc:
            return DaemonResponse(
                400,
                {
                    "ok": False,
                    "requires_confirmation": mode == "save-draft",
                    "error": str(exc),
                    "result": build_oa_launch_draft_plan(
                        template_id=str(args.get("template_id") or ""),
                        url=str(args.get("url") or ""),
                        fields={},
                        mode=mode,
                    ),
                },
            )
        if mode == "save-draft" and args.get("confirm") is not True:
            plan = build_oa_launch_draft_plan(
                template_id=str(args.get("template_id") or ""),
                url=str(args.get("url") or ""),
                fields=fields,
                mode=mode,
            )
            append_oa_launch_draft_audit(self.config_store.root, plan)
            return DaemonResponse(
                409,
                {
                    "ok": False,
                    "requires_confirmation": True,
                    "confirmed": False,
                    "error": "oa launch save-draft requires confirm=true",
                    "result": plan,
                },
            )

        inspect_args = {}
        for key in ("template_id", "url", "settle_ms"):
            if args.get(key) is not None:
                inspect_args[key] = args[key]
        inspect_response = self._run_nested_oa_command("launch_inspect", inspect_args, timeout_seconds)
        if inspect_response.status != 200 or inspect_response.body.get("ok") is False:
            return inspect_response
        inspection = inspect_response.body.get("result") if isinstance(inspect_response.body, dict) else {}
        if not isinstance(inspection, dict):
            inspection = {}
        plan = build_oa_launch_draft_plan(
            template_id=str(args.get("template_id") or ""),
            url=str(args.get("url") or ""),
            fields=fields,
            mode=mode,
            inspection=inspection,
        )
        append_oa_launch_draft_audit(self.config_store.root, plan)
        if plan.get("blocked_reasons"):
            return DaemonResponse(
                409,
                {
                    "ok": False,
                    "requires_confirmation": mode == "save-draft",
                    "confirmed": args.get("confirm") is True if mode == "save-draft" else False,
                    "error": "oa launch save-draft precheck blocked",
                    "result": plan,
                    "suggestions": [
                        "Run oa launch inspect for this template and use field names, ids, or labels from the inspection.",
                        "Confirm the launch page exposes a 保存待发/saveDraft control before executing.",
                    ],
                },
            )
        if mode == "dry-run":
            return DaemonResponse(
                200,
                {
                    "ok": True,
                    "task_id": inspect_response.body.get("task_id"),
                    "requires_confirmation": False,
                    "confirmed": False,
                    "result": plan,
                },
            )

        mark_oa_launch_draft_plan_for_execution(plan)
        append_oa_launch_draft_audit(self.config_store.root, plan)
        if not self.bridge.list_clients():
            return DaemonResponse(
                409,
                {
                    "ok": False,
                    "requires_confirmation": True,
                    "confirmed": True,
                    "error": "no Chrome extension client connected; start Chrome, load the BSCLI extension, and open the OA tab",
                    "result": plan,
                },
            )
        target_client_id = self._select_client_id_for_system("oa")
        if target_client_id is None:
            return DaemonResponse(
                409,
                {
                    "ok": False,
                    "requires_confirmation": True,
                    "confirmed": True,
                    "error": "no browser client is currently registered for system: oa; open the matching system tab and wait for the extension to register it",
                    "result": plan,
                },
            )
        task_id = self.bridge.enqueue_task(
            system="oa",
            kind="seeyon_launch_save_draft",
            payload={
                "template_id": plan.get("target", {}).get("template_id", ""),
                "url": plan.get("target", {}).get("url", ""),
                "fields": fields,
                "confirm": True,
                "settle_ms": int(args.get("settle_ms") if args.get("settle_ms") is not None else 1500),
                "script_timeout_ms": int(args.get("script_timeout_ms") if args.get("script_timeout_ms") is not None else 10000),
                "keep_tab": args.get("keep_tab") is True,
            },
            target_client_id=target_client_id,
        )
        result = self.bridge.wait_for_result(task_id, timeout_seconds=timeout_seconds)
        if result is None:
            return DaemonResponse(
                504,
                {
                    "ok": False,
                    "task_id": task_id,
                    "requires_confirmation": True,
                    "confirmed": True,
                    "error": "command timed out waiting for Chrome extension draft result: oa.launch_save_draft",
                    "result": plan,
                },
            )
        if not result["ok"]:
            return DaemonResponse(
                500,
                {
                    "ok": False,
                    "task_id": task_id,
                    "requires_confirmation": True,
                    "confirmed": True,
                    "error": result.get("error") or "extension launch save-draft task failed",
                    "result": plan,
                },
            )
        saved = result.get("result") or {}
        if not isinstance(saved, dict) or saved.get("draft_saved") is not True:
            return DaemonResponse(
                502,
                {
                    "ok": False,
                    "task_id": task_id,
                    "requires_confirmation": True,
                    "confirmed": True,
                    "error": "extension launch save-draft task returned no draft confirmation",
                    "result": plan,
                },
            )
        saved.setdefault("submitted_count", 0)
        saved.setdefault("plan", plan)
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": task_id,
                "requires_confirmation": True,
                "confirmed": True,
                "result": saved,
            },
        )

    def _run_oa_write_discover_command(
        self,
        args: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> DaemonResponse:
        source = str(args.get("source") or "history").strip().lower() or "history"
        if source == "launch":
            return self._run_oa_write_discover_from_launch(args, timeout_seconds=timeout_seconds)
        if source != "history":
            return DaemonResponse(
                400,
                {
                    "ok": False,
                    "error": f"unsupported write discovery source: {source}",
                    "result": {"schema_version": "bscli.oa_write_discovery.v1", "source": source, "actions": [], "items": []},
                },
            )
        kind = self._history_kind(args)
        list_args = {"kind": kind}
        for key in ("keyword", "limit"):
            if args.get(key) is not None:
                list_args[key] = args[key]
        list_response = self._run_nested_oa_command("history_list", list_args, timeout_seconds)
        if list_response.status != 200 or list_response.body.get("ok") is False:
            return list_response
        list_result = list_response.body.get("result") if isinstance(list_response.body, dict) else {}
        if not isinstance(list_result, dict):
            list_result = {}
        source_items = self._workflow_items_from_result(list_result)
        deep_limit = self._write_discover_deep_limit(args, len(source_items))
        discovered_items: list[dict[str, Any]] = []
        action_index: dict[str, dict[str, Any]] = {}
        errors = []
        detail_attempt_count = 0
        for item in source_items[:deep_limit]:
            if not isinstance(item, dict):
                continue
            href = str(item.get("href") or "").strip()
            row = {
                "title": item.get("title", ""),
                "affair_id": str(item.get("affair_id") or ""),
                "href": href,
                "history_kind": kind,
                "detail_read": False,
                "actions": [],
                "action_count": 0,
            }
            if not href:
                row["error"] = "history item has no detail href"
                discovered_items.append(row)
                continue
            detail_attempt_count += 1
            detail_response = self._run_nested_oa_command("detail_read", {"url": href}, timeout_seconds)
            if detail_response.status != 200 or detail_response.body.get("ok") is False:
                error = detail_response.body.get("error") or f"HTTP {detail_response.status}"
                row["error"] = error
                errors.append({"affair_id": row["affair_id"], "title": row["title"], "error": error})
                discovered_items.append(row)
                continue
            detail = detail_response.body.get("result") if isinstance(detail_response.body, dict) else {}
            if not isinstance(detail, dict):
                detail = {}
            actions = detail.get("actions") if isinstance(detail.get("actions"), list) else []
            summaries = [_summarize_oa_write_action(action) for action in actions]
            row.update(
                {
                    "detail_read": True,
                    "detail_title": detail.get("title", ""),
                    "actions": summaries,
                    "action_count": len(summaries),
                }
            )
            discovered_items.append(row)
            for summary in summaries:
                _merge_write_discovery_action(action_index, summary, row)
        actions = sorted(
            action_index.values(),
            key=lambda item: (-int(item.get("seen_count") or 0), str(item.get("code") or "")),
        )
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": list_response.body.get("task_id"),
                "result": {
                    "schema_version": "bscli.oa_write_discovery.v1",
                    "source": source,
                    "kind": kind,
                    "source_count": len(source_items),
                    "detail_attempt_count": detail_attempt_count,
                    "count": len(discovered_items),
                    "action_count": len(actions),
                    "actions": actions,
                    "items": discovered_items,
                    "errors": errors,
                    "read_effect": _history_read_effect(kind, detail_page_opened=detail_attempt_count > 0),
                },
            },
        )

    def _run_oa_write_discover_from_launch(
        self,
        args: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> DaemonResponse:
        inspect_args = {"source": "launch"}
        for key in ("template_id", "url", "settle_ms"):
            if args.get(key) is not None:
                inspect_args[key] = args[key]
        inspect_response = self._run_nested_oa_command("launch_inspect", inspect_args, timeout_seconds)
        if inspect_response.status != 200 or inspect_response.body.get("ok") is False:
            return inspect_response
        inspection = inspect_response.body.get("result") if isinstance(inspect_response.body, dict) else {}
        if not isinstance(inspection, dict):
            inspection = {}
        action_index: dict[str, dict[str, Any]] = {}
        actions = inspection.get("actions") if isinstance(inspection.get("actions"), list) else []
        summaries = [_summarize_oa_write_action(action) for action in actions if isinstance(action, dict)]
        workflow = {
            "title": inspection.get("title", ""),
            "affair_id": "",
            "href": inspection.get("url", ""),
            "history_kind": "",
        }
        for summary in summaries:
            _merge_write_discovery_action(action_index, summary, workflow)
        button_candidates = [
            button
            for button in inspection.get("buttons") or []
            if isinstance(button, dict) and button.get("action_like")
        ]
        for button in button_candidates:
            summary = {
                "code": str(button.get("id") or button.get("name") or button.get("text") or ""),
                "label": str(button.get("text") or button.get("id") or ""),
                "risk": str(button.get("risk") or "medium"),
            }
            _merge_write_discovery_action(action_index, summary, workflow)
        aggregated_actions = sorted(
            action_index.values(),
            key=lambda item: (-int(item.get("seen_count") or 0), str(item.get("code") or "")),
        )
        for action in aggregated_actions:
            action["execute_allowed"] = False
            action["promotion_status"] = "launch_discovery_only"
            blocked = action.get("blocked_reasons") if isinstance(action.get("blocked_reasons"), list) else []
            reason = "Launch-page discovery records candidates only; execution requires a separate confirmed write plan."
            if reason not in blocked:
                blocked.append(reason)
            action["blocked_reasons"] = blocked
        item = {
            "title": inspection.get("title", ""),
            "template_id": inspection.get("template_id", ""),
            "template": inspection.get("template") if isinstance(inspection.get("template"), dict) else {},
            "href": inspection.get("url", ""),
            "launch_inspected": True,
            "actions": summaries,
            "action_count": len(summaries),
            "button_candidates": button_candidates,
            "button_candidate_count": len(button_candidates),
            "safety": inspection.get("safety") if isinstance(inspection.get("safety"), dict) else {},
        }
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": inspect_response.body.get("task_id"),
                "result": {
                    "schema_version": "bscli.oa_write_discovery.v1",
                    "source": "launch",
                    "kind": "",
                    "source_count": 1,
                    "detail_attempt_count": 0,
                    "launch_attempt_count": 1,
                    "count": 1,
                    "action_count": len(aggregated_actions),
                    "actions": aggregated_actions,
                    "items": [item],
                    "errors": [],
                    "read_effect": _launch_read_effect(page_opened=True),
                },
            },
        )

    def _history_kind(self, args: dict[str, Any], *, default: str = "done") -> str:
        kind = str(args.get("kind") or default).strip().lower()
        aliases = {"track": "tracked", "tracking": "tracked", "follow": "tracked", "finished": "done"}
        kind = aliases.get(kind, kind)
        if kind not in {"", "sent", "done", "tracked"}:
            raise ValueError(f"unsupported history kind: {kind}")
        return kind

    def _write_discover_deep_limit(self, args: dict[str, Any], source_count: int) -> int:
        value = args.get("deep_limit")
        if value is None:
            return min(source_count, 10)
        try:
            return min(max(int(value), 0), source_count)
        except (TypeError, ValueError) as exc:
            raise ValueError("deep_limit must be an integer") from exc

    def _run_oa_workflow_list_command(
        self,
        args: dict[str, Any],
        timeout_seconds: float,
    ) -> DaemonResponse:
        workflow_type = self._workflow_type(args)
        list_command = self._workflow_list_command(workflow_type)
        list_response = self._run_nested_oa_command(list_command, {}, timeout_seconds)
        if list_response.status != 200 or list_response.body.get("ok") is False:
            if workflow_type != "pending":
                return list_response
            fallback_response = self._run_nested_oa_command("pending_list", {}, timeout_seconds)
            if fallback_response.status != 200 or fallback_response.body.get("ok") is False:
                return list_response
            fallback_result = fallback_response.body.get("result") if isinstance(fallback_response.body, dict) else {}
            if not isinstance(fallback_result, dict):
                fallback_result = {}
            source_items = self._workflow_items_from_result(fallback_result)
            filtered = self._filter_workflow_items(source_items, args)
            return DaemonResponse(
                200,
                {
                    "ok": True,
                    "task_id": fallback_response.body.get("task_id"),
                    "result": {
                        "type": workflow_type,
                        "source": "home_dom_fallback",
                        "name": fallback_result.get("name", ""),
                        "total": fallback_result.get("total"),
                        "page": fallback_result.get("page"),
                        "source_count": len(source_items),
                        "count": len(filtered),
                        "items": filtered,
                        "fallback": {
                            "from": list_command,
                            "reason": list_response.body.get("error", ""),
                        },
                    },
                },
            )
        result = list_response.body.get("result") if isinstance(list_response.body, dict) else {}
        if not isinstance(result, dict):
            result = {}
        source_items = self._workflow_items_from_result(result)
        filtered = self._filter_workflow_items(source_items, args)
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": list_response.body.get("task_id"),
                "result": {
                    "type": workflow_type,
                    "source": result.get("source") or list_command,
                    "name": result.get("name", ""),
                    "total": result.get("total"),
                    "page": result.get("page"),
                    "source_count": len(source_items),
                    "count": len(filtered),
                    "items": filtered,
                },
            },
        )

    def _run_oa_workflow_detail_command(
        self,
        args: dict[str, Any],
        timeout_seconds: float,
    ) -> DaemonResponse:
        detail_url = str(args.get("url") or "").strip()
        source_item = None
        if not detail_url:
            resolved, error_response = self._resolve_workflow_item_from_args(args, timeout_seconds)
            if error_response is not None:
                return error_response
            source_item = resolved
            detail_url = str(source_item.get("href") or "").strip()
        if not detail_url:
            return DaemonResponse(
                400,
                {
                    "ok": False,
                    "error": "oa workflow detail requires url or id",
                    "result": {},
                    "suggestions": [
                        "Run: python -m bscli.cli.main --home .bscli oa workflow list --type pending --fields title,affair_id,href",
                    ],
                },
            )
        detail_response = self._run_nested_oa_command("detail_read", {"url": detail_url}, timeout_seconds)
        if detail_response.status != 200 or detail_response.body.get("ok") is False:
            return detail_response
        detail = detail_response.body.get("result") if isinstance(detail_response.body, dict) else {}
        if not isinstance(detail, dict):
            detail = {}
        projected = self._project_workflow_detail(detail, args)
        if source_item is not None:
            result = {"source_item": source_item, "detail": projected}
        else:
            result = projected
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": detail_response.body.get("task_id"),
                "result": result,
            },
        )

    def _run_oa_workflow_brief_command(
        self,
        args: dict[str, Any],
        timeout_seconds: float,
    ) -> DaemonResponse:
        list_args = {"type": self._workflow_type(args)}
        for key in ("keyword", "limit"):
            if args.get(key) is not None:
                list_args[key] = args[key]
        list_response = self._run_nested_oa_command("workflow_list", list_args, timeout_seconds)
        if list_response.status != 200 or list_response.body.get("ok") is False:
            return list_response
        list_result = list_response.body.get("result") if isinstance(list_response.body, dict) else {}
        if not isinstance(list_result, dict):
            list_result = {}
        workflow_type = self._workflow_type(args)
        source_items = self._filter_workflow_items(self._workflow_items_from_result(list_result), args)
        brief_items = [_workflow_brief_item(item) for item in source_items if isinstance(item, dict)]
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": list_response.body.get("task_id"),
                "result": {
                    "type": workflow_type,
                    "source": list_result.get("source") or "workflow_list",
                    "source_count": list_result.get("source_count", len(source_items)),
                    "count": len(brief_items),
                    "items": brief_items,
                    "read_effect": _workflow_read_effect(workflow_type, detail_page_opened=False),
                },
            },
        )

    def _run_oa_workflow_inspect_command(
        self,
        args: dict[str, Any],
        timeout_seconds: float,
    ) -> DaemonResponse:
        workflow_type = self._workflow_type(args)
        detail_url = str(args.get("url") or "").strip()
        source_item = None
        if not detail_url:
            resolved, error_response = self._resolve_workflow_item_from_args(args, timeout_seconds)
            if error_response is not None:
                return error_response
            source_item = resolved
            detail_url = str(source_item.get("href") or "").strip()
        if not detail_url:
            return DaemonResponse(
                400,
                {
                    "ok": False,
                    "error": "oa workflow inspect requires url or id",
                    "result": {},
                },
            )
        detail_response = self._run_nested_oa_command("detail_read", {"url": detail_url}, timeout_seconds)
        if detail_response.status != 200 or detail_response.body.get("ok") is False:
            return detail_response
        detail = detail_response.body.get("result") if isinstance(detail_response.body, dict) else {}
        if not isinstance(detail, dict):
            detail = {}
        project_args = dict(args)
        project_args.setdefault("include", "title,text,fields,attachments,workflow,actions")
        projected = self._project_workflow_detail(detail, project_args)
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": detail_response.body.get("task_id"),
                "result": {
                    "type": workflow_type,
                    "source_item": source_item or {},
                    "detail": projected,
                    "summary": _workflow_detail_summary(source_item or {}, detail, args),
                    "read_effect": _workflow_read_effect(workflow_type, detail_page_opened=True),
                },
            },
        )

    def _run_oa_workflow_evidence_command(
        self,
        args: dict[str, Any],
        timeout_seconds: float,
    ) -> DaemonResponse:
        inspect_args = dict(args)
        inspect_args.setdefault("include", "title,text,fields,attachments,workflow,actions")
        inspect_response = self._run_nested_oa_command("workflow_inspect", inspect_args, timeout_seconds)
        if inspect_response.status != 200 or inspect_response.body.get("ok") is False:
            return inspect_response
        inspected = inspect_response.body.get("result") if isinstance(inspect_response.body, dict) else {}
        if not isinstance(inspected, dict):
            inspected = {}
        source_item = inspected.get("source_item") if isinstance(inspected.get("source_item"), dict) else {}
        detail = inspected.get("detail") if isinstance(inspected.get("detail"), dict) else {}
        summary = inspected.get("summary") if isinstance(inspected.get("summary"), dict) else {}
        workflow_type = self._workflow_type(args)
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": inspect_response.body.get("task_id"),
                "result": {
                    "type": workflow_type,
                    "source_item": source_item,
                    "evidence": _workflow_evidence_packet(source_item, detail, summary, args),
                    "read_effect": inspected.get(
                        "read_effect",
                        _workflow_read_effect(workflow_type, detail_page_opened=True),
                    ),
                },
            },
        )

    def _run_oa_workflow_timeline_command(
        self,
        args: dict[str, Any],
        timeout_seconds: float,
    ) -> DaemonResponse:
        opinion_response = self._run_nested_oa_command("workflow_opinions", dict(args), timeout_seconds)
        if opinion_response.status != 200 or opinion_response.body.get("ok") is False:
            return opinion_response
        result = opinion_response.body.get("result") if isinstance(opinion_response.body, dict) else {}
        if not isinstance(result, dict):
            result = {}
        items = result.get("items") if isinstance(result.get("items"), list) else []
        normalized = [_workflow_timeline_entry(index, item) for index, item in enumerate(items, start=1)]
        workflow_type = self._workflow_type(args)
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": opinion_response.body.get("task_id"),
                "result": {
                    "type": workflow_type,
                    "source_item": result.get("source_item", {}),
                    "count": len(normalized),
                    "items": normalized,
                    "read_effect": _workflow_read_effect(workflow_type, detail_page_opened=True),
                },
            },
        )

    def _run_oa_workflow_projection_command(
        self,
        args: dict[str, Any],
        *,
        projection_key: str,
        timeout_seconds: float,
    ) -> DaemonResponse:
        if str(args.get("url") or "").strip() or str(args.get("id") or "").strip():
            return self._run_oa_workflow_single_projection_command(
                args,
                projection_key=projection_key,
                timeout_seconds=timeout_seconds,
            )

        list_response = self._run_oa_workflow_list_command(args, timeout_seconds)
        if list_response.status != 200 or list_response.body.get("ok") is False:
            return list_response
        list_result = list_response.body.get("result") if isinstance(list_response.body, dict) else {}
        source_items = list_result.get("items") if isinstance(list_result, dict) else []
        if not isinstance(source_items, list):
            source_items = []

        indexed_items = []
        for source_item in source_items:
            if not isinstance(source_item, dict) or not source_item.get("href"):
                continue
            detail_response = self._run_nested_oa_command(
                "detail_read",
                {"url": source_item["href"]},
                timeout_seconds,
            )
            if detail_response.status != 200 or detail_response.body.get("ok") is False:
                continue
            detail = detail_response.body.get("result") if isinstance(detail_response.body, dict) else {}
            if not isinstance(detail, dict):
                continue
            indexed_items.extend(self._index_workflow_detail_entries(source_item, detail, projection_key))

        return DaemonResponse(
            200,
            {
                "ok": True,
                "result": {
                    "type": list_result.get("type") if isinstance(list_result, dict) else self._workflow_type(args),
                    "source_count": len(source_items),
                    "count": len(indexed_items),
                    "items": indexed_items,
                },
            },
        )

    def _run_oa_workflow_single_projection_command(
        self,
        args: dict[str, Any],
        *,
        projection_key: str,
        timeout_seconds: float,
    ) -> DaemonResponse:
        detail_url = str(args.get("url") or "").strip()
        source_item = None
        if not detail_url:
            resolved, error_response = self._resolve_workflow_item_from_args(args, timeout_seconds)
            if error_response is not None:
                return error_response
            source_item = resolved
            detail_url = str(source_item.get("href") or "").strip()
        if not detail_url:
            return DaemonResponse(
                400,
                {
                    "ok": False,
                    "error": "oa workflow projection requires url or id",
                    "result": {"items": [], "count": 0},
                },
            )
        detail_response = self._run_nested_oa_command("detail_read", {"url": detail_url}, timeout_seconds)
        if detail_response.status != 200 or detail_response.body.get("ok") is False:
            return detail_response
        detail = detail_response.body.get("result") if isinstance(detail_response.body, dict) else {}
        if not isinstance(detail, dict):
            detail = {}
        entries = detail.get(projection_key)
        if not isinstance(entries, list):
            entries = []
        limit = self._workflow_limit(args)
        if limit is not None:
            entries = entries[:limit]
        result = {
            "title": detail.get("title", ""),
            "url": detail.get("url", detail_url),
            "count": len(entries),
            "items": entries,
        }
        if source_item is not None:
            result["source_item"] = source_item
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": detail_response.body.get("task_id"),
                "result": result,
            },
        )

    def _resolve_workflow_item_from_args(
        self,
        args: dict[str, Any],
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], DaemonResponse | None]:
        workflow_id = str(args.get("id") or "").strip()
        if not workflow_id:
            return {}, DaemonResponse(
                400,
                {
                    "ok": False,
                    "error": "workflow id is required when url is not provided",
                    "result": {},
                },
            )
        workflow_type = self._workflow_type(args)
        list_response = self._run_oa_workflow_list_command({"type": workflow_type}, timeout_seconds)
        if list_response.status != 200 or list_response.body.get("ok") is False:
            return {}, list_response
        result = list_response.body.get("result") if isinstance(list_response.body, dict) else {}
        if not isinstance(result, dict):
            result = {}
        source_items = self._workflow_items_from_result(result)
        for item in source_items:
            if isinstance(item, dict) and str(item.get("affair_id") or "") == workflow_id:
                if not item.get("href"):
                    return {}, DaemonResponse(
                        409,
                        {
                            "ok": False,
                            "error": f"workflow item has no detail href: {workflow_id}",
                            "result": {"type": workflow_type, "id": workflow_id, "item": item},
                        },
                    )
                return item, None
        return {}, DaemonResponse(
            404,
            {
                "ok": False,
                "error": f"workflow id not found in {workflow_type} list: {workflow_id}",
                "result": {
                    "type": workflow_type,
                    "id": workflow_id,
                    "searched_count": len(source_items),
                },
                "suggestions": [
                    f"Run: python -m bscli.cli.main --home .bscli oa workflow list --type {workflow_type} --fields title,affair_id,href",
                    "Refresh the OA home page if the browser list is stale.",
                ],
            },
        )

    def _workflow_type(self, args: dict[str, Any]) -> str:
        workflow_type = str(args.get("type") or "pending").strip() or "pending"
        if workflow_type not in {"pending", "sent"}:
            raise ValueError(f"unsupported workflow type: {workflow_type}")
        return workflow_type

    def _workflow_list_command(self, workflow_type: str) -> str:
        return {"pending": "pending_list_api", "sent": "sent_list_api"}[workflow_type]

    def _workflow_items_from_result(self, result: dict[str, Any]) -> list:
        items = result.get("items")
        return items if isinstance(items, list) else []

    def _filter_workflow_items(self, items: list, args: dict[str, Any]) -> list:
        filtered = list(items)
        keyword = str(args.get("keyword") or "").strip()
        if keyword:
            needle = keyword.lower()
            filtered = [
                item
                for item in filtered
                if needle in json.dumps(item, ensure_ascii=False).lower()
            ]
        limit = self._workflow_limit(args)
        if limit is not None:
            filtered = filtered[:limit]
        return filtered

    def _workflow_limit(self, args: dict[str, Any]) -> int | None:
        value = args.get("limit")
        if value is None:
            return None
        try:
            return max(int(value), 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("limit must be an integer") from exc

    def _project_workflow_detail(self, detail: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        include = self._workflow_include(args)
        text_limit = self._workflow_text_limit(args)
        projected = {}
        for key in include:
            if key not in detail:
                continue
            value = detail[key]
            if key == "text" and isinstance(value, str):
                value = value[:text_limit]
            projected[key] = value
        return projected

    def _workflow_include(self, args: dict[str, Any]) -> list[str]:
        value = str(args.get("include") or "title,text,fields,attachments,workflow")
        return [part.strip() for part in value.split(",") if part.strip()]

    def _workflow_text_limit(self, args: dict[str, Any]) -> int:
        value = args.get("text_limit")
        if value is None:
            return 3000
        try:
            return max(int(value), 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("text_limit must be an integer") from exc

    def _index_workflow_detail_entries(
        self,
        source_item: dict[str, Any],
        detail: dict[str, Any],
        key: str,
    ) -> list[dict[str, Any]]:
        entries = detail.get(key)
        if not isinstance(entries, list):
            return []
        indexed = []
        for entry in entries:
            row = {
                "source_title": source_item.get("title", ""),
                "source_href": source_item.get("href", ""),
                "affair_id": source_item.get("affair_id", ""),
            }
            if isinstance(entry, dict):
                row.update(entry)
            else:
                row["text"] = str(entry)
            indexed.append(row)
        return indexed

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

    def _run_oa_doctor_command(self) -> DaemonResponse:
        capability_map = self._build_oa_capability_map()
        discovered = capability_map.get("discovered", [])
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": None,
                "result": {
                    "daemon": {
                        "ok": True,
                        "home": str(self.config_store.root),
                        "trace_store": str(self.config_store.root / "trace.db"),
                    },
                    "session": self._session_status(),
                    "capabilities": {
                        "read_count": len(capability_map["read"]),
                        "read_names": [item["name"] for item in capability_map["read"]],
                        "write": capability_map["write"],
                    },
                    "discovered": {
                        "count": len(discovered),
                        "items": discovered,
                    },
                },
            },
        )

    def _run_oa_capability_map_command(self) -> DaemonResponse:
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": None,
                "result": self._build_oa_capability_map(),
            },
        )

    def _build_oa_capability_map(self) -> dict[str, Any]:
        registry = CommandRegistry()
        register_seeyon_commands(registry)
        read = [
            {
                "name": _capability_name_from_command(command.name),
                "command": command.name,
                "risk": command.risk,
            }
            for command in registry.list("oa")
            if command.access == "read"
        ]
        discovered = []
        try:
            discovered = [
                {
                    "name": api.name,
                    "tool_name": api.tool_name,
                    "access": api.access,
                    "risk": api.risk,
                    "description": api.description,
                }
                for api in DiscoveredApiStore(self.config_store.root).list_apis("oa")
            ]
        except ValueError:
            discovered = []
        return {
            "read": read,
            "write": {
                "executable": ["workflow.submit", "meeting.reply", "workflow.launch.save_draft"],
                "dry_run_only": ["workflow.archive"],
                "blocked": ["workflow.delete", "workflow.revoke", "workflow.return", "workflow.upload"],
                "human_gate_commands": ["write_execute", "pending_submit", "meeting_reply_execute", "launch_save_draft"],
                "policy": "write actions require dry-run/precheck, explicit confirmation, read-back verification, and sanitized audit",
            },
            "discovered": discovered,
        }

    def _run_oa_write_capabilities_command(
        self,
        args: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> DaemonResponse:
        workflow_type = self._workflow_type(args)
        if workflow_type != "pending":
            return DaemonResponse(
                400,
                {
                    "ok": False,
                    "error": "write capabilities currently support only pending workflow items",
                    "result": {"type": workflow_type, "items": [], "count": 0},
                },
            )
        list_args = {"type": workflow_type}
        for key in ("keyword", "limit"):
            if args.get(key) is not None:
                list_args[key] = args[key]
        list_response = self._run_nested_oa_command("workflow_list", list_args, timeout_seconds)
        if list_response.status != 200 or list_response.body.get("ok") is False:
            return list_response
        result = list_response.body.get("result") if isinstance(list_response.body, dict) else {}
        if not isinstance(result, dict):
            result = {}
        source_items = self._workflow_items_from_result(result)
        capability_items = [
            self._oa_write_capability_for_item(item, timeout_seconds)
            for item in source_items
            if isinstance(item, dict)
        ]
        return DaemonResponse(
            200,
            {
                "ok": True,
                "task_id": list_response.body.get("task_id"),
                "result": {
                    "type": workflow_type,
                    "source_count": len(source_items),
                    "count": len(capability_items),
                    "items": capability_items,
                },
            },
        )

    def _oa_write_capability_for_item(
        self,
        item: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        category = _oa_write_category_for_item(item)
        row = {
            "category": category,
            "title": item.get("title", ""),
            "affair_id": str(item.get("affair_id") or ""),
            "href": item.get("href", ""),
            "current_state": _current_state_from_pending_item(item),
            "supported_write_actions": [],
            "unpromoted_write_actions": [],
            "discovered_write_actions": [],
            "verification_method": "",
        }
        if category == "meeting":
            row.update(self._oa_meeting_reply_capability(item, timeout_seconds))
            return row
        row.update(self._oa_workflow_submit_capability(item, timeout_seconds))
        return row

    def _oa_workflow_submit_capability(
        self,
        item: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        href = str(item.get("href") or "")
        if not href:
            return {
                "verification_method": "pending_disappearance",
                "capability_status": "blocked",
                "blocked_reasons": ["workflow item has no detail href"],
            }
        detail_response = self._run_nested_oa_command("detail_read", {"url": href}, timeout_seconds)
        if detail_response.status != 200 or detail_response.body.get("ok") is False:
            return {
                "verification_method": "pending_disappearance",
                "capability_status": "blocked",
                "blocked_reasons": [detail_response.body.get("error") or "detail page could not be read"],
            }
        detail = detail_response.body.get("result") if isinstance(detail_response.body, dict) else {}
        if not isinstance(detail, dict):
            detail = {}
        actions = detail.get("actions") if isinstance(detail.get("actions"), list) else []
        discovered = [_summarize_oa_write_action(action) for action in actions]
        supported = []
        unpromoted = _unpromoted_oa_write_action_capabilities(discovered, str(item.get("affair_id") or ""))
        if _find_oa_write_action(actions, "ContinueSubmit") is not None:
            affair_id = str(item.get("affair_id") or "")
            supported.append(
                {
                    "name": "workflow.submit",
                    "action": "ContinueSubmit",
                    "risk": "high",
                    "requires_confirmation": True,
                    "dry_run_command": f"oa write dry-run --affair-id {affair_id} --action ContinueSubmit",
                    "execute_command": f"oa write execute --affair-id {affair_id} --action ContinueSubmit --confirm",
                    "tool_names": ["oa__write_dry_run", "oa__write_execute"],
                    "daemon_commands": {"dry_run": "write_dry_run", "execute": "write_execute"},
                    "verification_method": "pending_disappearance",
                    "governance": build_write_governance(
                        "workflow.submit",
                        verification_method="pending_disappearance",
                    ),
                }
            )
        return {
            "current_state": {
                **_current_state_from_pending_item(item),
                "detail_title": detail.get("title", ""),
            },
            "supported_write_actions": supported,
            "unpromoted_write_actions": unpromoted,
            "discovered_write_actions": discovered,
            "verification_method": "pending_disappearance",
            "capability_status": "supported" if supported else "dry_run_only" if unpromoted else "read_only_or_unpromoted",
            "blocked_reasons": _blocked_reasons_from_unpromoted_actions(unpromoted),
            "promotion_requirements": _promotion_requirements_from_unpromoted_actions(unpromoted),
        }

    def _oa_meeting_reply_capability(
        self,
        item: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        href = str(item.get("href") or "")
        meeting_id = _query_param(href, "meetingId")
        proxy_id = _query_param(href, "proxyId")
        current_state = _current_state_from_pending_item(item)
        supported = []
        blocked_reasons = []
        if not meeting_id:
            blocked_reasons.append("meetingId was not found in the pending item href")
            return {
                "current_state": current_state,
                "verification_method": "meeting_reply_readback",
                "capability_status": "blocked",
                "blocked_reasons": blocked_reasons,
            }
        view_response = self._run_oa_meeting_view(meeting_id, proxy_id, timeout_seconds)
        if view_response.status != 200 or view_response.body.get("ok") is False:
            blocked_reasons.append(view_response.body.get("error") or "meeting view failed")
            return {
                "current_state": current_state,
                "verification_method": "meeting_reply_readback",
                "capability_status": "blocked",
                "blocked_reasons": blocked_reasons,
            }
        view = view_response.body.get("result") if isinstance(view_response.body, dict) else {}
        if not isinstance(view, dict):
            view = {}
        auth = view.get("meetingAuth") if isinstance(view.get("meetingAuth"), dict) else {}
        meeting = view.get("meetingVo") if isinstance(view.get("meetingVo"), dict) else {}
        reply = _project_oa_meeting_reply(view.get("myReply"))
        current_state = {
            **current_state,
            "meeting_id": meeting_id,
            "meeting_state": meeting.get("state"),
            "room_state": meeting.get("roomState"),
            **reply,
        }
        if auth.get("showReply") is True and auth.get("showReplyAttitude") is True:
            affair_id = str(item.get("affair_id") or "")
            supported.append(
                {
                    "name": "meeting.reply",
                    "attitudes": ["join", "not_join", "pending"],
                    "risk": "high",
                    "requires_confirmation": True,
                    "dry_run_command": f"oa meeting reply dry-run --id {affair_id} --attitude join",
                    "execute_command": f"oa meeting reply execute --id {affair_id} --attitude join --confirm",
                    "tool_names": ["oa__meeting_reply_dry_run", "oa__meeting_reply_execute"],
                    "daemon_commands": {"dry_run": "meeting_reply_dry_run", "execute": "meeting_reply_execute"},
                    "verification_method": "meeting_reply_readback",
                    "governance": build_write_governance(
                        "meeting.reply",
                        verification_method="meeting_reply_readback",
                    ),
                }
            )
        else:
            if auth.get("showReply") is not True:
                blocked_reasons.append("meeting reply is not available")
            if auth.get("showReplyAttitude") is not True:
                blocked_reasons.append("meeting attitude reply is not available")
        return {
            "current_state": current_state,
            "supported_write_actions": supported,
            "verification_method": "meeting_reply_readback",
            "capability_status": "supported" if supported else "read_only_or_blocked",
            "blocked_reasons": blocked_reasons,
        }

    def _run_oa_write_plan_command(
        self,
        command: str,
        args: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> DaemonResponse:
        mode = {
            "write_draft": "draft",
            "write_dry_run": "dry-run",
            "write_execute": "execute",
        }[command]
        plan = build_oa_write_plan(
            affair_id=str(args.get("affair_id") or ""),
            action=str(args.get("action") or ""),
            opinion=str(args.get("opinion") or ""),
            mode=mode,
            source_url=str(args.get("source_url") or ""),
        )
        if mode == "dry-run":
            precheck = self._precheck_oa_write_plan(plan, args, timeout_seconds)
            append_oa_write_audit(self.config_store.root, plan)
            if not precheck["passed"]:
                return DaemonResponse(
                    409,
                    {
                        "ok": False,
                        "requires_confirmation": False,
                        "error": precheck["error"],
                        "result": plan,
                        "suggestions": plan.get("suggestions", []),
                    },
                )
        if mode == "execute":
            if args.get("confirm") is not True:
                append_oa_write_audit(self.config_store.root, plan)
                return DaemonResponse(
                    409,
                    {
                        "ok": False,
                        "requires_confirmation": True,
                        "confirmed": False,
                        "error": "oa write execute requires confirm=true",
                        "result": plan,
                    },
                )
            if plan["action"]["code"] != "ContinueSubmit":
                append_oa_write_audit(self.config_store.root, plan)
                return DaemonResponse(
                    400,
                    {
                        "ok": False,
                        "requires_confirmation": True,
                        "confirmed": True,
                        "error": f"oa write execute only supports ContinueSubmit for now, got: {plan['action']['code']}",
                        "result": plan,
                    },
                )
            precheck = self._precheck_oa_write_plan(plan, args, timeout_seconds)
            if not precheck["passed"]:
                append_oa_write_audit(self.config_store.root, plan)
                return DaemonResponse(
                    409,
                    {
                        "ok": False,
                        "requires_confirmation": True,
                        "confirmed": True,
                        "error": precheck["error"],
                        "result": plan,
                        "suggestions": plan.get("suggestions", []),
                    },
                )
            _mark_oa_write_plan_for_execution(plan)
            append_oa_write_audit(self.config_store.root, plan)
            if not self.bridge.list_clients():
                return DaemonResponse(
                    409,
                    {
                        "ok": False,
                        "requires_confirmation": True,
                        "confirmed": True,
                        "error": "no Chrome extension client connected; start Chrome, load the BSCLI extension, and open the OA tab",
                        "result": plan,
                    },
                )
            target_client_id = self._select_client_id_for_system("oa")
            if target_client_id is None:
                return DaemonResponse(
                    409,
                    {
                        "ok": False,
                        "requires_confirmation": True,
                        "confirmed": True,
                        "error": "no browser client is currently registered for system: oa; open the matching system tab and wait for the extension to register it",
                        "result": plan,
                    },
                )
            task_id = self.bridge.enqueue_task(
                system="oa",
                kind="seeyon_write_execute",
                payload={
                    "affair_id": plan["target"]["affair_id"],
                    "action": plan["action"]["code"],
                    "opinion": plan["opinion"]["text"],
                    "source_url": plan["target"].get("source_url", ""),
                    "confirm": True,
                },
                target_client_id=target_client_id,
            )
            result = self.bridge.wait_for_result(task_id, timeout_seconds=timeout_seconds)
            if result is None:
                return DaemonResponse(
                    504,
                    {
                        "ok": False,
                        "task_id": task_id,
                        "requires_confirmation": True,
                        "confirmed": True,
                        "error": "command timed out waiting for Chrome extension write result: oa.write_execute",
                        "result": plan,
                    },
                )
            if not result["ok"]:
                return DaemonResponse(
                    500,
                    {
                        "ok": False,
                        "task_id": task_id,
                        "requires_confirmation": True,
                        "confirmed": True,
                        "error": result.get("error") or "extension write task failed",
                        "result": plan,
                    },
                )
            submitted = result.get("result") or {}
            if not isinstance(submitted, dict) or submitted.get("submitted") is not True:
                return DaemonResponse(
                    502,
                    {
                        "ok": False,
                        "task_id": task_id,
                        "requires_confirmation": True,
                        "confirmed": True,
                        "error": "extension write task returned no submission confirmation",
                        "result": plan,
                    },
                )
            if isinstance(submitted, dict):
                submitted.setdefault("plan", plan)
                verification = self._verify_oa_write_submission(
                    plan,
                    submit={
                        "ok": True,
                        "task_id": task_id,
                        "error": None,
                    },
                    timeout_seconds=timeout_seconds,
                )
                submitted["verification"] = verification
                if verification.get("status") != "disappeared":
                    return DaemonResponse(
                        502,
                        {
                            "ok": False,
                            "task_id": task_id,
                            "requires_confirmation": True,
                            "confirmed": True,
                            "error": verification.get("error") or f"post-submit verification failed: {verification.get('status')}",
                            "result": submitted,
                        },
                    )
            return DaemonResponse(
                200,
                {
                    "ok": True,
                    "task_id": task_id,
                    "requires_confirmation": True,
                    "confirmed": True,
                    "result": submitted,
                },
            )
        return DaemonResponse(200, {"ok": True, "task_id": None, "result": plan})

    def _run_oa_write_endpoint_candidates_command(
        self,
        args: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> DaemonResponse:
        plan = build_oa_write_plan(
            affair_id=str(args.get("affair_id") or ""),
            action=str(args.get("action") or ""),
            opinion="",
            mode="dry-run",
            source_url=str(args.get("source_url") or ""),
        )
        precheck = self._precheck_oa_write_plan(plan, args, timeout_seconds)
        evidence = plan.get("promotion", {}).get("evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        endpoint_candidates = evidence.get("endpoint_analysis")
        if not isinstance(endpoint_candidates, list):
            write_hints = evidence.get("write_hints") if isinstance(evidence.get("write_hints"), dict) else {}
            endpoint_candidates = classify_write_endpoint_candidates(
                write_hints.get("endpoint_candidates"),
                action=str(plan.get("action", {}).get("code") or ""),
            )
        result = {
            "target": plan.get("target", {}),
            "action": plan.get("action", {}),
            "precheck": plan.get("precheck", {}),
            "promotion": plan.get("promotion", {}),
            "write_hints": evidence.get("write_hints", {}),
            "endpoint_candidates": endpoint_candidates,
            "probe_policy": _oa_endpoint_probe_policy(),
        }
        if not precheck["passed"]:
            return DaemonResponse(
                409,
                {
                    "ok": False,
                    "requires_confirmation": False,
                    "error": precheck["error"],
                    "result": result,
                    "suggestions": plan.get("suggestions", []),
                },
            )
        return DaemonResponse(
            200,
            {
                "ok": True,
                "requires_confirmation": False,
                "result": result,
            },
        )

    def _run_oa_write_preflight_command(
        self,
        args: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> DaemonResponse:
        plan = build_oa_write_plan(
            affair_id=str(args.get("affair_id") or ""),
            action=str(args.get("action") or ""),
            opinion=str(args.get("opinion") or ""),
            mode="dry-run",
            source_url=str(args.get("source_url") or ""),
        )
        precheck = self._precheck_oa_write_plan(plan, args, timeout_seconds)
        append_oa_write_audit(self.config_store.root, plan)
        result = build_oa_write_preflight(
            plan,
            precheck_passed=precheck["passed"],
            precheck_error=precheck.get("error", ""),
        )
        return DaemonResponse(
            200,
            {
                "ok": True,
                "requires_confirmation": False,
                "confirmed": False,
                "result": result,
            },
        )

    def _run_oa_write_prepare_command(
        self,
        args: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> DaemonResponse:
        workflow_type = self._workflow_type({"type": args.get("type") or "pending"})
        affair_id = str(args.get("affair_id") or "").strip()
        source_url = str(args.get("source_url") or "").strip()
        evidence_args: dict[str, Any] = {"type": workflow_type}
        if affair_id:
            evidence_args["id"] = affair_id
        elif source_url:
            evidence_args["url"] = source_url
        if args.get("text_limit") is not None:
            evidence_args["text_limit"] = _workflow_text_limit_from_args(args)
        evidence_response = self._run_nested_oa_command("workflow_evidence", evidence_args, timeout_seconds)
        if evidence_response.status != 200 or evidence_response.body.get("ok") is False:
            return evidence_response
        preflight_args = {
            "type": workflow_type,
            "affair_id": affair_id,
            "action": str(args.get("action") or ""),
            "opinion": str(args.get("opinion") or ""),
            "source_url": source_url,
        }
        preflight_response = self._run_nested_oa_command("write_preflight", preflight_args, timeout_seconds)
        if preflight_response.status != 200 or preflight_response.body.get("ok") is False:
            return preflight_response
        evidence_result = evidence_response.body.get("result") if isinstance(evidence_response.body, dict) else {}
        if not isinstance(evidence_result, dict):
            evidence_result = {}
        preflight = preflight_response.body.get("result") if isinstance(preflight_response.body, dict) else {}
        if not isinstance(preflight, dict):
            preflight = {}
        result = _oa_write_prepare_packet(
            workflow_type=workflow_type,
            evidence_result=evidence_result,
            preflight=preflight,
            action=str(args.get("action") or ""),
            opinion=str(args.get("opinion") or ""),
        )
        return DaemonResponse(
            200,
            {
                "ok": True,
                "requires_confirmation": False,
                "confirmed": False,
                "result": result,
            },
        )

    def _precheck_oa_write_plan(
        self,
        plan: dict[str, Any],
        args: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        checks = []
        missing = []
        blocked_reasons = []
        suggestions = []
        target = plan.setdefault("target", {})
        affair_id = str(target.get("affair_id") or args.get("affair_id") or "").strip()
        action_code = str(plan.get("action", {}).get("code") or args.get("action") or "").strip()
        source_url = str(target.get("source_url") or args.get("source_url") or "").strip()

        if not affair_id:
            missing.append("affair_id")
            blocked_reasons.append("affair_id is required")
            checks.append(_oa_write_check("target_resolved", False, "affair_id is required"))
        if not action_code:
            missing.append("action")
            blocked_reasons.append("action is required")
            checks.append(_oa_write_check("action_available", False, "action is required"))
        if missing:
            return self._finish_oa_write_precheck(
                plan,
                checks=checks,
                missing=missing,
                blocked_reasons=blocked_reasons,
                suggestions=["Provide affair_id and action before running dry-run or execute."],
            )

        source_item = None
        if source_url:
            target["source_url"] = source_url
            checks.append(_oa_write_check("target_resolved", True, "source_url provided"))
        else:
            workflow_type = str(args.get("type") or "pending")
            source_item, error_response = self._resolve_workflow_item_from_args(
                {"type": workflow_type, "id": affair_id},
                timeout_seconds,
            )
            if error_response is not None:
                missing.append(f"target:{affair_id}")
                blocked_reasons.append(error_response.body.get("error") or "workflow target not found")
                suggestions.extend(error_response.body.get("suggestions") or [])
                checks.append(
                    _oa_write_check(
                        "target_resolved",
                        False,
                        error_response.body.get("error") or "workflow target not found",
                    )
                )
                return self._finish_oa_write_precheck(
                    plan,
                    checks=checks,
                    missing=missing,
                    blocked_reasons=blocked_reasons,
                    suggestions=suggestions,
                )
            source_url = str(source_item.get("href") or "").strip()
            target["source_url"] = source_url
            target["source_item"] = source_item
            checks.append(_oa_write_check("target_resolved", True, "workflow target resolved from pending list"))

        detail_response = self._run_nested_oa_command("detail_read", {"url": source_url}, timeout_seconds)
        if detail_response.status != 200 or detail_response.body.get("ok") is False:
            missing.append("detail")
            blocked_reasons.append(detail_response.body.get("error") or "detail page could not be read")
            checks.append(
                _oa_write_check(
                    "detail_read",
                    False,
                    detail_response.body.get("error") or "detail page could not be read",
                    run_id=detail_response.body.get("run_id"),
                    task_id=detail_response.body.get("task_id"),
                )
            )
            return self._finish_oa_write_precheck(
                plan,
                checks=checks,
                missing=missing,
                blocked_reasons=blocked_reasons,
                suggestions=["Open or refresh the OA pending page, then rerun the dry-run."],
            )

        detail = detail_response.body.get("result") if isinstance(detail_response.body, dict) else {}
        if not isinstance(detail, dict):
            detail = {}
        checks.append(
            _oa_write_check(
                "detail_read",
                True,
                "detail page read successfully",
                run_id=detail_response.body.get("run_id"),
                task_id=detail_response.body.get("task_id"),
            )
        )
        actions = detail.get("actions") if isinstance(detail.get("actions"), list) else []
        available_action = _find_oa_write_action(actions, action_code)
        if available_action is None:
            missing.append(f"action:{action_code}")
            blocked_reasons.append(f"target action is not available: {action_code}")
            checks.append(
                _oa_write_check(
                    "action_available",
                    False,
                    f"target action is not available: {action_code}",
                    available_actions=[_summarize_oa_write_action(action) for action in actions],
                )
            )
            return self._finish_oa_write_precheck(
                plan,
                checks=checks,
                missing=missing,
                blocked_reasons=blocked_reasons,
                suggestions=["Run: python -m bscli.cli.main --home .bscli oa workflow actions --type pending --id <affair_id>"],
                detail=detail,
                actions=actions,
            )

        normalized_action = _summarize_oa_write_action(available_action)
        if normalized_action.get("code"):
            plan["action"] = normalized_action
        checks.append(
            _oa_write_check(
                "action_available",
                True,
                f"target action is available: {action_code}",
                action=normalized_action,
            )
        )
        return self._finish_oa_write_precheck(
            plan,
            checks=checks,
            missing=[],
            blocked_reasons=[],
            suggestions=[],
            detail=detail,
            actions=actions,
        )

    def _finish_oa_write_precheck(
        self,
        plan: dict[str, Any],
        *,
        checks: list[dict[str, Any]],
        missing: list[str],
        blocked_reasons: list[str],
        suggestions: list[str],
        detail: dict[str, Any] | None = None,
        actions: list | None = None,
    ) -> dict[str, Any]:
        passed = not missing and all(check.get("passed") for check in checks)
        plan["checks"] = checks
        plan["missing"] = missing
        plan["blocked"] = not passed
        plan["blocked_reasons"] = blocked_reasons
        plan["suggestions"] = suggestions
        precheck = {
            "status": "passed" if passed else "blocked",
            "checked_at": datetime.now(UTC).isoformat(),
            "action_count": len(actions or []),
        }
        if detail is not None:
            precheck["detail"] = {
                "title": detail.get("title", ""),
                "url": detail.get("url") or plan.get("target", {}).get("source_url", ""),
            }
        if actions is not None:
            precheck["available_actions"] = [_summarize_oa_write_action(action) for action in actions]
        plan["precheck"] = precheck
        if detail is not None:
            _attach_oa_write_promotion_evidence(plan, detail=detail, actions=actions or [])
        if not passed:
            request = plan.setdefault("request", {})
            request["status"] = "blocked"
            request["reason"] = "; ".join(blocked_reasons) or "write precheck blocked"
            safety = plan.setdefault("safety", {})
            safety["will_execute"] = False
            safety["dry_run_only"] = True
        return {
            "passed": passed,
            "error": "; ".join(blocked_reasons) or "write precheck blocked",
        }

    def _verify_oa_write_submission(
        self,
        plan: dict[str, Any],
        *,
        submit: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        affair_id = str(plan.get("target", {}).get("affair_id") or "")
        verify_response = self._run_nested_oa_command("pending_list_api", {}, timeout_seconds)
        if verify_response.status != 200 or verify_response.body.get("ok") is False:
            verification = _pending_submit_verification(
                "verify_failed",
                affair_id=affair_id,
                before_present=True,
                after_present=True,
                run_id=verify_response.body.get("run_id"),
                task_id=verify_response.body.get("task_id"),
            )
            verification["error"] = verify_response.body.get("error") or "pending list verification failed"
        else:
            verify_items = _pending_items_from_response_body(verify_response.body)
            still_pending = _item_present_by_affair_id(verify_items, affair_id)
            verification = _pending_submit_verification(
                "still_pending" if still_pending else "disappeared",
                affair_id=affair_id,
                before_present=True,
                after_present=still_pending,
                run_id=verify_response.body.get("run_id"),
                task_id=verify_response.body.get("task_id"),
            )
        append_oa_write_verification_audit(
            self.config_store.root,
            affair_id=affair_id,
            action=str(plan.get("action", {}).get("code") or ""),
            source_url=str(plan.get("target", {}).get("source_url") or ""),
            verification=verification,
            submit=submit,
        )
        return verification

    def _run_oa_pending_submit_command(
        self,
        args: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> DaemonResponse:
        if args.get("confirm") is not True:
            return DaemonResponse(
                409,
                {
                    "ok": False,
                    "requires_confirmation": True,
                    "confirmed": False,
                    "error": "oa pending submit requires confirm=true",
                    "result": {"items": [], "submitted_count": 0, "target_count": 0},
                },
            )

        action = str(args.get("action") or "")
        keyword = str(args.get("keyword") or "")
        opinion = str(args.get("opinion") or "")
        verify_wait = max(float(args.get("verify_wait") or 0.0), 0.0)

        list_response = self._run_nested_oa_command("pending_list_api", {}, timeout_seconds)
        if list_response.status != 200 or list_response.body.get("ok") is False:
            return DaemonResponse(
                list_response.status,
                {
                    "ok": False,
                    "error": list_response.body.get("error") or "pending list failed",
                    "source": list_response.body,
                    "result": {"items": [], "submitted_count": 0, "target_count": 0},
                },
            )

        source_items = _apply_pending_submit_filters(
            _pending_items_from_response_body(list_response.body),
            keyword=keyword,
            limit=args.get("limit"),
        )
        results = []
        submitted_count = 0

        for item in source_items:
            affair_id = str(item.get("affair_id") or "")
            href = str(item.get("href") or "")
            row = {
                "title": item.get("title", ""),
                "affair_id": affair_id,
                "href": href,
                "action": action,
                "submit": None,
                "verification": _pending_submit_verification(
                    "not_checked",
                    affair_id=affair_id,
                    before_present=True,
                    after_present=True,
                ),
            }
            if not affair_id or not href:
                row["verification"] = _pending_submit_verification(
                    "invalid_item",
                    affair_id=affair_id,
                    before_present=bool(affair_id),
                    after_present=True,
                )
                results.append(row)
                self._append_pending_submit_verification_audit(row)
                break

            detail_response = self._run_nested_oa_command(
                "detail_read",
                {"url": href},
                timeout_seconds,
            )
            actions = (
                detail_response.body.get("result", {}).get("actions")
                if detail_response.body.get("ok")
                else []
            )
            if not _action_available(actions, action):
                row["verification"] = _pending_submit_verification(
                    "action_missing",
                    affair_id=affair_id,
                    before_present=True,
                    after_present=True,
                )
                row["detail_error"] = detail_response.body.get("error")
                results.append(row)
                self._append_pending_submit_verification_audit(row)
                break

            submit_response = self._run_nested_oa_command(
                "write_execute",
                {
                    "affair_id": affair_id,
                    "action": action,
                    "opinion": opinion,
                    "source_url": href,
                    "confirm": True,
                },
                timeout_seconds,
            )
            row["submit"] = {
                "ok": submit_response.body.get("ok") is True,
                "task_id": submit_response.body.get("task_id"),
                "run_id": submit_response.body.get("run_id"),
                "error": submit_response.body.get("error"),
            }
            if submit_response.body.get("ok") is not True:
                row["verification"] = _pending_submit_verification(
                    "submit_failed",
                    affair_id=affair_id,
                    before_present=True,
                    after_present=True,
                )
                results.append(row)
                self._append_pending_submit_verification_audit(row)
                break

            if verify_wait:
                time.sleep(verify_wait)
            verify_response = self._run_nested_oa_command("pending_list_api", {}, timeout_seconds)
            if verify_response.status != 200 or verify_response.body.get("ok") is False:
                row["verification"] = _pending_submit_verification(
                    "verify_failed",
                    affair_id=affair_id,
                    before_present=True,
                    after_present=True,
                    run_id=verify_response.body.get("run_id"),
                    task_id=verify_response.body.get("task_id"),
                )
                row["verification"]["error"] = verify_response.body.get("error") or "pending list verification failed"
                results.append(row)
                self._append_pending_submit_verification_audit(row)
                break
            verify_items = _pending_items_from_response_body(verify_response.body)
            still_pending = _item_present_by_affair_id(verify_items, affair_id)
            row["verification"] = _pending_submit_verification(
                "still_pending" if still_pending else "disappeared",
                affair_id=affair_id,
                before_present=True,
                after_present=still_pending,
                run_id=verify_response.body.get("run_id"),
                task_id=verify_response.body.get("task_id"),
            )
            results.append(row)
            self._append_pending_submit_verification_audit(row)
            if still_pending:
                break
            submitted_count += 1

        ok = submitted_count == len(source_items)
        return DaemonResponse(
            200,
            {
                "ok": ok,
                "requires_confirmation": True,
                "confirmed": True,
                "result": {
                    "target_count": len(source_items),
                    "submitted_count": submitted_count,
                    "stopped": not ok,
                    "items": results,
                },
            },
        )

    def _run_nested_oa_command(
        self,
        command: str,
        args: dict[str, Any],
        timeout_seconds: float,
    ) -> DaemonResponse:
        return self._run_command_with_trace(
            {
                "system": "oa",
                "command": command,
                "args": args,
                "timeout_seconds": timeout_seconds,
            }
        )

    def _append_pending_submit_verification_audit(self, row: dict[str, Any]) -> None:
        append_oa_write_verification_audit(
            self.config_store.root,
            affair_id=str(row.get("affair_id") or ""),
            action=str(row.get("action") or ""),
            source_url=str(row.get("href") or ""),
            verification=row.get("verification") if isinstance(row.get("verification"), dict) else {},
            submit=row.get("submit") if isinstance(row.get("submit"), dict) else {},
        )

    def _run_oa_meeting_reply_command(
        self,
        command: str,
        args: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> DaemonResponse:
        mode = "dry-run" if command == "meeting_reply_dry_run" else "execute"
        action = _normalize_oa_meeting_attitude(str(args.get("attitude") or "join"))
        feedback = str(args.get("feedback") or "")
        plan = _build_oa_meeting_reply_plan(mode=mode, action=action, feedback=feedback)
        if mode == "execute" and args.get("confirm") is not True:
            _append_oa_meeting_reply_audit(self.config_store.root, plan)
            return DaemonResponse(
                409,
                {
                    "ok": False,
                    "requires_confirmation": True,
                    "confirmed": False,
                    "error": "oa meeting reply execute requires confirm=true",
                    "result": plan,
                },
            )

        target, target_error = self._resolve_oa_meeting_reply_target(args, timeout_seconds)
        if target_error is not None:
            plan["blocked"] = True
            plan["blocked_reasons"].append(target_error.body.get("error", "target resolution failed"))
            _append_oa_meeting_reply_audit(self.config_store.root, plan)
            return DaemonResponse(
                target_error.status,
                {**target_error.body, "result": plan},
            )
        plan["target"] = target

        view_response = self._run_oa_meeting_view(
            target["meeting_id"],
            target.get("proxy_id", ""),
            timeout_seconds,
        )
        if view_response.status != 200 or view_response.body.get("ok") is False:
            plan["blocked"] = True
            plan["checks"].append(_oa_write_check("meeting_view", False, view_response.body.get("error", "meeting view failed")))
            plan["blocked_reasons"].append(view_response.body.get("error", "meeting view failed"))
            _append_oa_meeting_reply_audit(self.config_store.root, plan)
            return DaemonResponse(view_response.status, {**view_response.body, "result": plan})
        meeting_view = view_response.body.get("result") if isinstance(view_response.body, dict) else {}
        if not isinstance(meeting_view, dict):
            meeting_view = {}
        precheck = _precheck_oa_meeting_reply_plan(plan, meeting_view)
        _append_oa_meeting_reply_audit(self.config_store.root, plan)
        if not precheck["passed"]:
            return DaemonResponse(
                409,
                {
                    "ok": False,
                    "requires_confirmation": mode == "execute",
                    "confirmed": args.get("confirm") is True,
                    "error": precheck["error"],
                    "result": plan,
                },
            )
        if mode == "dry-run":
            return DaemonResponse(200, {"ok": True, "requires_confirmation": False, "result": plan})

        plan["safety"]["will_execute"] = True
        submit_response = self._post_oa_meeting_reply(
            target["meeting_id"],
            target.get("proxy_id", ""),
            action["feedbackFlag"],
            feedback,
            timeout_seconds,
        )
        if submit_response.status != 200 or submit_response.body.get("ok") is False:
            return DaemonResponse(
                submit_response.status,
                {
                    "ok": False,
                    "requires_confirmation": True,
                    "confirmed": True,
                    "error": submit_response.body.get("error", "meeting reply submit failed"),
                    "result": {"submitted": False, "plan": plan, "submit": submit_response.body},
                },
            )
        verify_wait = max(float(args.get("verify_wait", 2.0)), 0.0)
        if verify_wait:
            time.sleep(verify_wait)
        verification = self._verify_oa_meeting_reply(
            plan,
            timeout_seconds=timeout_seconds,
        )
        result = {
            "submitted": verification.get("status") == "matched",
            "plan": plan,
            "submit": submit_response.body,
            "verification": verification,
        }
        if verification.get("status") != "matched":
            return DaemonResponse(
                502,
                {
                    "ok": False,
                    "requires_confirmation": True,
                    "confirmed": True,
                    "error": verification.get("error") or "meeting reply verification failed",
                    "result": result,
                },
            )
        _append_oa_meeting_reply_audit(self.config_store.root, {**plan, "verification": verification})
        return DaemonResponse(
            200,
            {
                "ok": True,
                "requires_confirmation": True,
                "confirmed": True,
                "result": result,
            },
        )

    def _resolve_oa_meeting_reply_target(
        self,
        args: dict[str, Any],
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], DaemonResponse | None]:
        target_id = str(args.get("id") or args.get("affair_id") or "").strip()
        source_url = str(args.get("source_url") or "").strip()
        meeting_id = str(args.get("meeting_id") or "").strip()
        proxy_id = str(args.get("proxy_id") or "").strip()
        source_item = None
        if not meeting_id and source_url:
            meeting_id = _query_param(source_url, "meetingId")
            proxy_id = proxy_id or _query_param(source_url, "proxyId")
        if not meeting_id and target_id:
            source_item, error = self._resolve_workflow_item_from_args(
                {"type": "pending", "id": target_id},
                timeout_seconds,
            )
            if error is not None:
                return {}, error
            source_url = str(source_item.get("href") or "")
            meeting_id = _query_param(source_url, "meetingId")
            proxy_id = proxy_id or _query_param(source_url, "proxyId")
        if not meeting_id:
            return {}, DaemonResponse(
                400,
                {
                    "ok": False,
                    "error": "meeting reply requires --id, --meeting-id, or --source-url with meetingId",
                },
            )
        target = {
            "affair_id": target_id,
            "meeting_id": meeting_id,
            "proxy_id": proxy_id,
            "source_url": source_url,
        }
        if source_item is not None:
            target["source_item"] = source_item
        return target, None

    def _run_oa_meeting_view(
        self,
        meeting_id: str,
        proxy_id: str,
        timeout_seconds: float,
    ) -> DaemonResponse:
        response = self._run_oa_meeting_ajax(
            "meetingView",
            [{"meetingId": meeting_id, "proxyId": proxy_id}],
            timeout_seconds,
        )
        if response.status != 200:
            return response
        replay = response.body.get("result") if isinstance(response.body, dict) else {}
        data = replay.get("json") if isinstance(replay, dict) else None
        if not isinstance(data, dict):
            return DaemonResponse(502, {"ok": False, "error": "meetingView response was not JSON"})
        return DaemonResponse(200, {"ok": True, "task_id": response.body.get("task_id"), "result": data})

    def _post_oa_meeting_reply(
        self,
        meeting_id: str,
        proxy_id: str,
        attitude: int,
        feedback: str,
        timeout_seconds: float,
    ) -> DaemonResponse:
        return self._run_oa_meeting_ajax(
            "reply",
            [
                {
                    "meetingId": meeting_id,
                    "proxyId": proxy_id,
                    "feedbackFlag": attitude,
                    "feedback": feedback,
                }
            ],
            timeout_seconds,
        )

    def _verify_oa_meeting_reply(
        self,
        plan: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
        response = self._run_oa_meeting_view(
            str(target.get("meeting_id") or ""),
            str(target.get("proxy_id") or ""),
            timeout_seconds,
        )
        if response.status != 200 or response.body.get("ok") is False:
            return {"status": "error", "error": response.body.get("error", "meeting view failed")}
        view = response.body.get("result") if isinstance(response.body, dict) else {}
        reply = _project_oa_meeting_reply((view or {}).get("myReply"))
        expected = plan.get("action", {}).get("feedbackFlag")
        if reply.get("feedbackFlag") == expected:
            return {"status": "matched", "reply": reply}
        return {
            "status": "mismatch",
            "expected_feedbackFlag": expected,
            "reply": reply,
            "error": "meeting reply did not match requested attitude after submit",
        }

    def _run_oa_meeting_ajax(
        self,
        manager_method: str,
        arguments: list[Any],
        timeout_seconds: float,
    ) -> DaemonResponse:
        if not self.bridge.list_clients():
            return DaemonResponse(409, {"ok": False, "error": "no Chrome extension client connected"})
        target_client_id = self._select_client_id_for_system("oa")
        if target_client_id is None:
            return DaemonResponse(409, {"ok": False, "error": "no browser client is currently registered for system: oa"})
        profile = self._load_system_profile("oa") or build_seeyon_profile()
        parsed = urlparse(profile.base_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        body = urlencode(
            {
                "managerMethod": manager_method,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            }
        )
        return self._run_page_fetch(
            "oa",
            target_client_id,
            {
                "method": "POST",
                "url": f"{base}/seeyon/ajax.do?method=ajaxAction&managerName=meetingAjaxManager",
                "headers": {"content-type": "application/x-www-form-urlencoded; charset=UTF-8"},
                "body": body,
                "max_text": 120000,
            },
            timeout_seconds,
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
        request = {
            "method": args.get("method", "GET").upper(),
            "url": args["url"],
            "headers": args.get("headers", {}),
            "body": args.get("body"),
        }
        if args.get("max_text") is not None:
            request["max_text"] = int(args["max_text"])
        return request

    def _validate_discovered_api_policy(
        self,
        system: str,
        api,
        *,
        confirmed: bool,
    ) -> DaemonResponse | None:
        request = api.request or {}
        profile = self._load_system_profile(system)
        parsed = urlparse(str(request.get("url") or ""))
        if parsed.scheme and parsed.netloc:
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if profile is None or origin not in profile.allowed_origins:
                return DaemonResponse(
                    403,
                    {
                        "ok": False,
                        "requires_confirmation": False,
                        "error": f"discovered API origin is not allowed for system {system}: {origin}",
                    },
                )
        if api.requires_confirmation and not confirmed:
            return DaemonResponse(
                409,
                {
                    "ok": False,
                    "requires_confirmation": True,
                    "error": (
                        "discovered API requires explicit confirmation; rerun with confirm=true "
                        f"after reviewing method={api.method}, access={api.access}, risk={api.risk}"
                    ),
                    "api": {
                        "system": api.system,
                        "name": api.name,
                        "method": api.method,
                        "access": api.access,
                        "risk": api.risk,
                    },
                },
            )
        return None

    def _trace_metadata(self, system: str, command: str, args: dict[str, Any]) -> dict[str, str]:
        if system == "oa" and command == "write_capabilities":
            return {"access": "read", "strategy": "daemon_api"}
        if system == "oa" and command in HISTORY_READ_COMMANDS:
            return {"access": "read", "strategy": "daemon_api"}
        if system == "oa" and command in {"template_match", "matter_profile", "matter_inspect", "launch_inspect", "launch_dry_run"}:
            return {"access": "read", "strategy": "daemon_api"}
        if system == "oa" and command == "launch_save_draft":
            return {"access": "write", "strategy": "human_gate"}
        if system == "oa" and command == "write_discover":
            return {"access": "read", "strategy": "daemon_api"}
        if system == "oa" and command == "write_endpoint_candidates":
            return {"access": "read", "strategy": "daemon_api"}
        if system == "oa" and command == "write_preflight":
            return {"access": "read", "strategy": "daemon_api"}
        if system == "oa" and command == "write_prepare":
            return {"access": "read", "strategy": "daemon_api"}
        if system == "oa" and command in {"write_draft", "write_dry_run"}:
            return {"access": "read", "strategy": "daemon_api"}
        if system == "oa" and command in {"write_execute", "pending_submit"}:
            return {"access": "write", "strategy": "human_gate"}
        if system == "oa" and command == "meeting_reply_dry_run":
            return {"access": "read", "strategy": "daemon_api"}
        if system == "oa" and command == "meeting_reply_execute":
            return {"access": "write", "strategy": "human_gate"}
        if system == "oa" and command in INBOX_READ_COMMANDS:
            return {"access": "read", "strategy": "daemon_api"}
        if system == "oa" and command in WORKFLOW_READ_COMMANDS:
            return {"access": "read", "strategy": "daemon_api"}
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
            looks_logged_in = _looks_like_logged_in_oa_client(client) if matches_system else False
            clients.append({**client, "matches_system": matches_system, "looks_logged_in": looks_logged_in})
        matched = [client for client in clients if client["matches_system"]]
        logged_in = [client for client in matched if client["looks_logged_in"]]
        suggestions = []
        warnings = []
        if not matched:
            suggestions = [
                "Start Chrome, load the BSCLI extension, and open the OA tab: http://10.10.50.110/seeyon/main.do?method=main",
                "After login, run: python -m bscli.cli.main --home .bscli daemon status",
            ]
        elif not logged_in:
            warnings.append(
                "OA uses a single browser session owner: if Playwright MCP is logged in, the default Chrome extension bridge may be kicked out; make default Chrome the active logged-in OA owner before bridge tests."
            )
        return {
            "connected": bool(matched),
            "client_count": len(matched),
            "clients": clients,
            "warnings": warnings,
            "suggestions": suggestions,
            "session_owner": {
                "exclusive_login": True,
                "active_bridge": "chrome_extension" if matched else "none",
                "logged_in_client_count": len(logged_in),
            },
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


def _looks_like_logged_in_oa_client(client: dict[str, Any]) -> bool:
    title = str(client.get("title") or "").strip()
    url = str(client.get("url") or "").strip()
    lowered = title.lower()
    if not title:
        return False
    if title == url or lowered.startswith(("http://", "https://")):
        return False
    if any(marker in title for marker in ("登录", "登陆", "用户登录")) or "login" in lowered:
        return False
    return True


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


def _pending_items_from_response_body(response_body: dict[str, Any]) -> list[dict[str, Any]]:
    result = response_body.get("result")
    if not isinstance(result, dict):
        return []
    items = result.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _apply_pending_submit_filters(
    items: list[dict[str, Any]],
    *,
    keyword: str,
    limit: Any,
) -> list[dict[str, Any]]:
    filtered = list(items)
    if keyword:
        needle = keyword.lower()
        filtered = [
            item
            for item in filtered
            if needle in json.dumps(item, ensure_ascii=False).lower()
        ]
    if limit is not None:
        try:
            filtered = filtered[: max(int(limit), 0)]
        except (TypeError, ValueError):
            filtered = []
    return filtered


def _action_available(actions: Any, action_code: str) -> bool:
    if not isinstance(actions, list):
        return False
    return any(
        isinstance(action, dict) and str(action.get("code") or "") == action_code
        for action in actions
    )


def _find_oa_write_action(actions: Any, action_code: str) -> dict[str, Any] | None:
    if not isinstance(actions, list):
        return None
    for action in actions:
        if isinstance(action, dict) and str(action.get("code") or "") == str(action_code):
            return action
    return None


def _summarize_oa_write_action(action: Any) -> dict[str, str]:
    if not isinstance(action, dict):
        return normalize_write_action("")
    code = str(action.get("code") or "")
    fallback = normalize_write_action(code)
    return {
        "code": code,
        "label": str(action.get("label") or fallback["label"]),
        "risk": str(action.get("risk") or fallback["risk"]),
    }


def _history_profile_kind(args: dict[str, Any]) -> str:
    kind = str(args.get("kind") or "done").strip().lower()
    aliases = {"track": "tracked", "tracking": "tracked", "follow": "tracked", "finished": "done"}
    kind = aliases.get(kind, kind)
    if kind not in {"sent", "done", "tracked", "all"}:
        raise ValueError(f"unsupported history profile kind: {kind}")
    return kind


def _build_oa_history_profile(*, kind: str, list_results: list[dict[str, Any]]) -> dict[str, Any]:
    source_items = []
    for result in list_results:
        result_kind = str(result.get("kind") or "")
        for item in result.get("items") or []:
            if not isinstance(item, dict):
                continue
            source_items.append({**item, "history_kind": item.get("history_kind") or result_kind})
    clusters_by_key: dict[str, dict[str, Any]] = {}
    for item in source_items:
        title = str(item.get("title") or "")
        parts = _history_cluster_parts(title, str(item.get("category") or ""))
        key = _normal_match_text(parts["title_pattern"])
        cluster = clusters_by_key.setdefault(
            key,
            {
                "cluster_id": _slug(parts["title_pattern"]) or f"cluster-{len(clusters_by_key) + 1}",
                "title_pattern": parts["title_pattern"],
                "category_tag": parts["category_tag"],
                "subject": parts["subject"],
                "count": 0,
                "kinds": set(),
                "categories": set(),
                "statuses": set(),
                "dates": [],
                "sample_items": [],
            },
        )
        cluster["count"] += 1
        if item.get("history_kind"):
            cluster["kinds"].add(str(item.get("history_kind")))
        if item.get("category"):
            cluster["categories"].add(str(item.get("category")))
        if item.get("status"):
            cluster["statuses"].add(str(item.get("status")))
        if item.get("date"):
            cluster["dates"].append(str(item.get("date")))
        samples = cluster["sample_items"]
        if len(samples) < 5:
            samples.append(
                {
                    "title": item.get("title", ""),
                    "status": item.get("status", ""),
                    "date": item.get("date", ""),
                    "category": item.get("category", ""),
                    "affair_id": str(item.get("affair_id") or ""),
                    "href": item.get("href", ""),
                    "history_kind": item.get("history_kind", ""),
                }
            )
    source_count = len(source_items)
    clusters = []
    for cluster in clusters_by_key.values():
        dates = sorted(date for date in cluster.pop("dates") if date)
        count = int(cluster["count"])
        clusters.append(
            {
                **cluster,
                "share": round(count / source_count, 4) if source_count else 0,
                "kinds": sorted(cluster["kinds"]),
                "categories": sorted(cluster["categories"]),
                "statuses": sorted(cluster["statuses"]),
                "date_range": {"start": dates[0], "end": dates[-1]} if dates else {"start": "", "end": ""},
            }
        )
    clusters.sort(key=lambda row: (-int(row.get("count") or 0), str(row.get("title_pattern") or "")))
    return {
        "schema_version": "bscli.oa_history_profile.v1",
        "kind": kind,
        "source": "history_list",
        "source_count": source_count,
        "cluster_count": len(clusters),
        "clusters": clusters,
    }


def _history_cluster_parts(title: str, category: str = "") -> dict[str, str]:
    title = _clean_title(title)
    for pattern, opener, closer in (
        (r"^【([^】]+)】\s*([^-–—(（]+)", "【", "】"),
        (r"^\[([^\]]+)\]\s*([^-–—(（]+)", "[", "]"),
    ):
        match = re.match(pattern, title)
        if match:
            category_tag = match.group(1).strip()
            subject = _clean_title(match.group(2))
            return {
                "category_tag": category_tag,
                "subject": subject,
                "title_pattern": f"{opener}{category_tag}{closer} {subject}" if opener == "[" else f"{opener}{category_tag}{closer}{subject}",
            }
    subject = _clean_title(re.split(r"\s*[-–—(（]\s*", title, maxsplit=1)[0])
    return {
        "category_tag": category,
        "subject": subject,
        "title_pattern": subject,
    }


def _clean_title(value: str) -> str:
    value = re.sub(r"^\s*[（(]\s*自动发起\s*[）)]\s*", " ", str(value or ""))
    value = re.sub(r"\s*\(auto(?:matically)? started\)\s*", " ", str(value or ""), flags=re.I)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:300]


def _build_oa_template_match(profile: dict[str, Any], templates: dict[str, Any]) -> dict[str, Any]:
    template_items = [item for item in templates.get("items") or [] if isinstance(item, dict)]
    matched_clusters = []
    for cluster in profile.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        candidates = _rank_template_candidates(cluster, template_items)
        top = candidates[0] if candidates else None
        second = candidates[1] if len(candidates) > 1 else None
        if top and top["score"] >= 0.78 and (second is None or top["score"] - second["score"] >= 0.08):
            status = "matched"
            best_template = {
                "template_id": top["template_id"],
                "title": top["title"],
                "href": top["href"],
                "score": top["score"],
            }
        elif top and top["score"] >= 0.45:
            status = "ambiguous"
            best_template = {}
        else:
            status = "unmatched"
            best_template = {}
        matched_clusters.append(
            {
                **cluster,
                "match_status": status,
                "best_template": best_template,
                "candidates": candidates[:3],
            }
        )
    return {
        "schema_version": "bscli.oa_template_match.v1",
        "source": "history_profile+template_list_api",
        "history_profile": {
            "kind": profile.get("kind", ""),
            "source_count": profile.get("source_count", 0),
            "cluster_count": profile.get("cluster_count", 0),
        },
        "template_count": len(template_items),
        "cluster_count": len(matched_clusters),
        "clusters": matched_clusters,
    }


def _matter_profile_args(args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"kind": str(args.get("kind") or "all").strip().lower() or "all"}
    if args.get("keyword"):
        payload["keyword"] = args["keyword"]
    if args.get("limit") is not None:
        payload["limit"] = args["limit"]
    return payload


def _matter_query(args: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(args.get("id") or "").strip(),
        "name": str(args.get("name") or "").strip(),
    }


def _build_oa_matter_profile(template_match: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    matters = []
    for cluster in template_match.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        matters.append(_matter_from_template_cluster(cluster))
    matched_count = sum(1 for matter in matters if matter.get("template"))
    return {
        "schema_version": "bscli.oa_matter_profile.v1",
        "source": "template_match",
        "kind": args.get("kind", "all"),
        "keyword": args.get("keyword", ""),
        "source_count": (template_match.get("history_profile") or {}).get("source_count", 0),
        "template_count": template_match.get("template_count", 0),
        "matter_count": len(matters),
        "matched_template_count": matched_count,
        "unmatched_template_count": len(matters) - matched_count,
        "matters": matters,
        "read_effect": {
            "history_read": True,
            "template_list_read": True,
            "launch_page_opened": False,
            "submitted_count": 0,
        },
    }


def _matter_from_template_cluster(cluster: dict[str, Any]) -> dict[str, Any]:
    template = cluster.get("best_template") if isinstance(cluster.get("best_template"), dict) else {}
    template = {key: template.get(key, "") for key in ("template_id", "title", "href", "score") if template.get(key) not in (None, "")}
    matter_id = _matter_id_from_cluster(cluster)
    match_status = str(cluster.get("match_status") or "unmatched")
    return {
        "matter_id": matter_id,
        "name": str(cluster.get("title_pattern") or cluster.get("subject") or matter_id),
        "subject": str(cluster.get("subject") or ""),
        "category_tag": str(cluster.get("category_tag") or ""),
        "frequency": {
            "count": int(cluster.get("count") or 0),
            "share": float(cluster.get("share") or 0),
            "kinds": cluster.get("kinds") if isinstance(cluster.get("kinds"), list) else [],
            "date_range": cluster.get("date_range") if isinstance(cluster.get("date_range"), dict) else {"start": "", "end": ""},
        },
        "template_match_status": match_status,
        "template": template,
        "template_candidates": cluster.get("candidates") if isinstance(cluster.get("candidates"), list) else [],
        "sample_items": cluster.get("sample_items") if isinstance(cluster.get("sample_items"), list) else [],
        "available_actions": _matter_available_actions(template, match_status),
    }


def _matter_id_from_cluster(cluster: dict[str, Any]) -> str:
    cluster_id = str(cluster.get("cluster_id") or "").strip()
    if len(cluster_id) >= 4 and not re.fullmatch(r"cluster-\d+", cluster_id):
        return cluster_id
    title = str(cluster.get("title_pattern") or cluster.get("subject") or "").strip()
    slug = _slug(title)
    if len(slug) >= 4:
        return slug
    basis = "|".join(
        [
            title,
            str(cluster.get("category_tag") or ""),
            str(cluster.get("subject") or ""),
        ]
    )
    digest = hashlib.sha1(basis.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"matter-{digest}"


def _matter_available_actions(template: dict[str, Any], match_status: str) -> list[dict[str, Any]]:
    if template.get("template_id") and match_status == "matched":
        return [
            {
                "name": "launch.save_draft",
                "command": "launch_save_draft",
                "status": "available",
                "access": "write",
                "risk": "medium",
                "requires_confirmation": True,
                "description": "Create or update an OA launch-page draft for this matched template.",
            },
            {
                "name": "launch.dry_run",
                "command": "launch_dry_run",
                "status": "available",
                "access": "read",
                "risk": "low",
                "requires_confirmation": False,
                "description": "Validate launch-page fields before saving a draft.",
            },
        ]
    return [
        {
            "name": "launch.save_draft",
            "command": "launch_save_draft",
            "status": "blocked",
            "access": "write",
            "risk": "medium",
            "requires_confirmation": True,
            "reason": "matter has no unambiguous matched template",
        }
    ]


def _find_oa_matter(matters: list[Any], args: dict[str, Any]) -> dict[str, Any] | None:
    query = _matter_query(args)
    wanted_id = _normal_match_text(query["id"])
    wanted_name = _normal_match_text(query["name"] or query["id"])
    for matter in matters:
        if not isinstance(matter, dict):
            continue
        matter_id = _normal_match_text(str(matter.get("matter_id") or ""))
        name = _normal_match_text(str(matter.get("name") or ""))
        if wanted_id and matter_id == wanted_id:
            return matter
        if wanted_name and (name == wanted_name or wanted_name in name or name in wanted_name):
            return matter
    return None


def _build_oa_matter_inspection(
    profile: dict[str, Any],
    matter: dict[str, Any],
    *,
    launch_inspection: dict[str, Any],
) -> dict[str, Any]:
    template = matter.get("template") if isinstance(matter.get("template"), dict) else {}
    result = {
        "schema_version": "bscli.oa_matter_inspection.v1",
        "source": "matter_profile",
        "profile": {
            "kind": profile.get("kind", ""),
            "matter_count": profile.get("matter_count", 0),
            "matched_template_count": profile.get("matched_template_count", 0),
        },
        "matter": matter,
        "launch_inspection": launch_inspection,
        "next_steps": _matter_next_steps(matter, launch_inspection),
        "read_effect": {
            "history_read": True,
            "template_list_read": True,
            "launch_page_opened": bool(launch_inspection),
            "submitted_count": 0,
        },
    }
    if template.get("template_id"):
        result["template_id"] = template.get("template_id")
    return result


def _matter_next_steps(matter: dict[str, Any], launch_inspection: dict[str, Any]) -> list[str]:
    template = matter.get("template") if isinstance(matter.get("template"), dict) else {}
    template_id = str(template.get("template_id") or "")
    if not template_id:
        return ["Run oa template match with a broader history kind or keyword to resolve this matter to a template."]
    steps = [
        f"oa launch dry-run --template-id {template_id} --field <field>=<value>",
        f"oa launch save-draft --template-id {template_id} --field <field>=<value> --confirm",
    ]
    if not launch_inspection:
        steps.insert(0, f"oa matter inspect --id {matter.get('matter_id')} --with-launch")
    return steps


def _rank_template_candidates(cluster: dict[str, Any], templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for template in templates:
        score, evidence = _template_match_score(cluster, template)
        if score <= 0:
            continue
        candidates.append(
            {
                "template_id": str(template.get("template_id") or ""),
                "title": template.get("title", ""),
                "href": template.get("href", ""),
                "score": round(score, 4),
                "evidence": evidence,
            }
        )
    candidates.sort(key=lambda item: (-float(item.get("score") or 0), str(item.get("title") or "")))
    return candidates


def _template_match_score(cluster: dict[str, Any], template: dict[str, Any]) -> tuple[float, list[str]]:
    cluster_title = str(cluster.get("title_pattern") or "")
    subject = str(cluster.get("subject") or "")
    category = str(cluster.get("category_tag") or "")
    template_title = str(template.get("title") or "")
    cluster_norm = _normal_match_text(cluster_title)
    subject_norm = _normal_match_text(subject)
    category_norm = _normal_match_text(category)
    template_norm = _normal_match_text(template_title)
    if not cluster_norm or not template_norm:
        return 0.0, []
    evidence = []
    score = SequenceMatcher(None, cluster_norm, template_norm).ratio()
    if cluster_norm == template_norm:
        score = max(score, 0.95)
        evidence.append("exact")
    elif cluster_norm in template_norm or template_norm in cluster_norm:
        score = max(score, 0.86)
        evidence.append("substring")
    if subject_norm and subject_norm in template_norm:
        score = max(score, 0.82)
        evidence.append("subject")
    if category_norm and category_norm in template_norm:
        score = min(max(score + 0.08, score), 0.99)
        evidence.append("category")
    overlap = _char_overlap(cluster_norm, template_norm)
    if overlap >= 0.5:
        score = max(score, overlap * 0.75)
        evidence.append("overlap")
    return min(score, 0.99), evidence or ["similarity"]


def _normal_match_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").lower(), flags=re.UNICODE)


def _char_overlap(left: str, right: str) -> float:
    left_set = {char for char in left if char.strip()}
    right_set = {char for char in right if char.strip()}
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug[:80]


def _attach_oa_write_promotion_evidence(
    plan: dict[str, Any],
    *,
    detail: dict[str, Any],
    actions: list,
) -> None:
    promotion = plan.get("promotion")
    if not isinstance(promotion, dict):
        return
    action_code = str(plan.get("action", {}).get("code") or "")
    available_action = _find_oa_write_action(actions, action_code)
    execute_allowed = promotion.get("execute_allowed") is True
    promotion["evidence"] = {
        "action_present": available_action is not None,
        "available_action": _project_oa_write_action_evidence(available_action),
        "write_hints": _project_oa_write_hints_evidence(detail.get("write_hints")),
        "execute_allowed": execute_allowed,
        "verification_method": str(promotion.get("verification_method") or ""),
        "missing_for_execute": [] if execute_allowed else list(promotion.get("requirements") or []),
    }
    hints = promotion["evidence"].get("write_hints")
    endpoint_candidates = hints.get("endpoint_candidates") if isinstance(hints, dict) else []
    promotion["evidence"]["endpoint_analysis"] = classify_write_endpoint_candidates(
        endpoint_candidates,
        action=action_code,
    )


def _project_oa_write_action_evidence(action: Any) -> dict[str, Any]:
    if not isinstance(action, dict):
        return {}
    summary = _summarize_oa_write_action(action)
    for key in ("id", "access", "requires_confirmation", "supports_dry_run", "source"):
        if key in action:
            summary[key] = action.get(key)
    return summary


def _project_oa_write_hints_evidence(write_hints: Any) -> dict[str, Any]:
    if not isinstance(write_hints, dict):
        return {}
    evidence = {}
    for key in ("csrf_tokens", "hidden_fields", "endpoint_candidates"):
        value = write_hints.get(key)
        if isinstance(value, list):
            evidence[key] = [
                item for item in value
                if isinstance(item, dict)
            ]
    return evidence


def _oa_write_check(
    name: str,
    passed: bool,
    message: str,
    **extra: Any,
) -> dict[str, Any]:
    check = {
        "name": name,
        "passed": passed,
        "message": message,
    }
    for key, value in extra.items():
        if value is not None:
            check[key] = value
    return check


def _item_present_by_affair_id(items: list[dict[str, Any]], affair_id: str) -> bool:
    return any(
        isinstance(item, dict) and str(item.get("affair_id") or "") == str(affair_id)
        for item in items
    )


def _pending_submit_verification(
    status: str,
    *,
    affair_id: str,
    before_present: bool,
    after_present: bool,
    run_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    verification = {
        "type": "pending_disappearance",
        "status": status,
        "verified": status in {"disappeared", "still_pending"},
        "affair_id": str(affair_id),
        "before_present": before_present,
        "after_present": after_present,
        "present_after_submit": after_present,
    }
    if run_id:
        verification["run_id"] = run_id
    if task_id:
        verification["task_id"] = task_id
    return verification


def _oa_endpoint_probe_policy() -> dict[str, Any]:
    return {
        "automatic_network_probe": "disabled",
        "safe_to_call_default": False,
        "reason": "no endpoint candidate was called; write-like URLs require a user-confirmed test plan before any network probe",
    }


def _mark_oa_write_plan_for_execution(plan: dict[str, Any]) -> None:
    safety = plan.setdefault("safety", {})
    safety["will_execute"] = True
    safety["dry_run_only"] = False
    request = plan.setdefault("request", {})
    request["status"] = "sent_by_extension"
    request["reason"] = "confirmed OA write dispatched through the Chrome extension bridge"


def _normalize_oa_meeting_attitude(value: str) -> dict[str, Any]:
    normalized = (value or "").strip().lower().replace("-", "_")
    mapping = {
        "join": {"code": "join", "label": "参加", "feedbackFlag": 1},
        "attend": {"code": "join", "label": "参加", "feedbackFlag": 1},
        "1": {"code": "join", "label": "参加", "feedbackFlag": 1},
        "参加": {"code": "join", "label": "参加", "feedbackFlag": 1},
        "not_join": {"code": "not_join", "label": "不参加", "feedbackFlag": 0},
        "0": {"code": "not_join", "label": "不参加", "feedbackFlag": 0},
        "不参加": {"code": "not_join", "label": "不参加", "feedbackFlag": 0},
        "pending": {"code": "pending", "label": "待定", "feedbackFlag": -1},
        "-1": {"code": "pending", "label": "待定", "feedbackFlag": -1},
        "待定": {"code": "pending", "label": "待定", "feedbackFlag": -1},
    }
    if normalized not in mapping:
        raise ValueError(f"unsupported meeting reply attitude: {value}")
    return dict(mapping[normalized])


def _build_oa_meeting_reply_plan(
    *,
    mode: str,
    action: dict[str, Any],
    feedback: str,
) -> dict[str, Any]:
    return {
        "schema_version": "bscli.oa_meeting_reply_plan.v1",
        "mode": mode,
        "target": {},
        "action": action,
        "feedback": {"text": feedback, "length": len(feedback)},
        "current_reply": {},
        "checks": [],
        "missing": [],
        "blocked": False,
        "blocked_reasons": [],
        "precheck": {"status": "not_run", "passed": False},
        "safety": {
            "will_execute": False,
            "requires_confirmation": mode == "execute",
        },
        "governance": build_write_governance(
            "meeting.reply",
            verification_method="meeting_reply_readback",
        ),
    }


def _precheck_oa_meeting_reply_plan(plan: dict[str, Any], meeting_view: dict[str, Any]) -> dict[str, Any]:
    auth = meeting_view.get("meetingAuth") if isinstance(meeting_view.get("meetingAuth"), dict) else {}
    meeting = meeting_view.get("meetingVo") if isinstance(meeting_view.get("meetingVo"), dict) else {}
    reply = _project_oa_meeting_reply(meeting_view.get("myReply"))
    plan["meeting"] = {
        "title": meeting.get("title", ""),
        "state": meeting.get("state"),
        "roomState": meeting.get("roomState"),
    }
    plan["current_reply"] = reply
    checks = [
        _oa_write_check("meeting_view", True, "meeting view loaded"),
        _oa_write_check("reply_allowed", auth.get("showReply") is True, "meeting allows reply"),
        _oa_write_check("attitude_allowed", auth.get("showReplyAttitude") is True, "meeting allows attitude reply"),
        _oa_write_check("my_reply_found", bool(reply), "current user reply state found"),
    ]
    plan["checks"].extend(checks)
    failed = [check for check in checks if not check["passed"]]
    if failed:
        plan["blocked"] = True
        plan["blocked_reasons"] = [check["message"] for check in failed]
        plan["missing"] = [check["name"] for check in failed]
        plan["precheck"] = {
            "status": "blocked",
            "passed": False,
            "error": "; ".join(plan["blocked_reasons"]),
        }
        return {"passed": False, "error": plan["precheck"]["error"]}
    plan["precheck"] = {"status": "passed", "passed": True}
    return {"passed": True, "error": ""}


def _project_oa_meeting_reply(reply: Any) -> dict[str, Any]:
    if not isinstance(reply, dict):
        return {}
    return {
        "id": reply.get("id", ""),
        "userId": reply.get("userId", ""),
        "userName": reply.get("userName", ""),
        "feedbackFlag": reply.get("feedbackFlag"),
        "feedbackName": reply.get("feedbackName", ""),
        "state": reply.get("state"),
        "lookState": reply.get("lookState"),
        "readDate": reply.get("readDate"),
    }


def _append_oa_meeting_reply_audit(home: Path, plan: dict[str, Any]) -> Path:
    audit_dir = home / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / "oa-meeting-replies.jsonl"
    sanitized = json.loads(json.dumps(plan, ensure_ascii=False))
    if isinstance(sanitized.get("feedback"), dict):
        sanitized["feedback"].pop("text", None)
    sanitized["audited_at"] = datetime.now(UTC).isoformat()
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(sanitized, ensure_ascii=False, sort_keys=True) + "\n")
    return audit_path


def _capability_name_from_command(command_name: str) -> str:
    return command_name.replace("_", ".")


def _workflow_read_effect(workflow_type: str, *, detail_page_opened: bool) -> dict[str, Any]:
    return {
        "detail_page_opened": detail_page_opened,
        "may_mark_read": detail_page_opened and workflow_type == "pending",
        "note": (
            "Opening a pending detail page may change its read/unread state."
            if detail_page_opened and workflow_type == "pending"
            else ""
        ),
    }


def _history_read_effect(kind: str, *, detail_page_opened: bool) -> dict[str, Any]:
    return {
        "detail_page_opened": detail_page_opened,
        "may_mark_read": False,
        "note": "Historical detail pages are used as read-only samples for discovery." if detail_page_opened else "",
        "kind": kind,
    }


def _launch_read_effect(*, page_opened: bool) -> dict[str, Any]:
    return {
        "launch_page_opened": page_opened,
        "may_create_draft": page_opened,
        "submitted_count": 0,
        "note": "Launch pages may create or keep a draft; no submit/approve/archive/delete action is executed.",
    }


def _merge_write_discovery_action(
    action_index: dict[str, dict[str, Any]],
    action: dict[str, Any],
    workflow: dict[str, Any],
) -> None:
    code = str(action.get("code") or "")
    label = str(action.get("label") or code)
    key = code or label
    if not key:
        return
    promotion = write_action_promotion(code)
    row = action_index.setdefault(
        key,
        {
            "code": code,
            "label": label,
            "name": write_action_type(code),
            "risk": str(action.get("risk") or "medium"),
            "seen_count": 0,
            "dry_run_allowed": promotion["dry_run_allowed"],
            "execute_allowed": promotion["execute_allowed"],
            "promotion_status": promotion["status"],
            "verification_method": promotion["verification_method"],
            "requirements": promotion["requirements"],
            "blocked_reasons": promotion["blocked_reasons"],
            "sample_workflows": [],
        },
    )
    row["seen_count"] = int(row.get("seen_count") or 0) + 1
    samples = row.get("sample_workflows") if isinstance(row.get("sample_workflows"), list) else []
    sample = {
        "title": workflow.get("title", ""),
        "affair_id": workflow.get("affair_id", ""),
        "href": workflow.get("href", ""),
        "history_kind": workflow.get("history_kind", ""),
    }
    if sample not in samples and len(samples) < 5:
        samples.append(sample)
    row["sample_workflows"] = samples


_WORKFLOW_FOCUS_KEYWORDS = (
    ("contract", 4, ("contract", "agreement", "合同", "协议")),
    ("archive", 4, ("archive", "filing", "归档", "存档")),
    ("meeting", 3, ("meeting", "conference", "会议", "交流会")),
    ("finance", 3, ("budget", "invoice", "reimburse", "预算", "发票", "报销")),
    ("weekly_report", 2, ("weekly", "report", "周报")),
)


def _workflow_brief_item(item: dict[str, Any]) -> dict[str, Any]:
    attention = _workflow_item_attention(item)
    return {
        "title": item.get("title", ""),
        "affair_id": str(item.get("affair_id") or ""),
        "sender": item.get("sender", ""),
        "date": item.get("date", ""),
        "category": item.get("category", ""),
        "read": item.get("read"),
        "href": item.get("href", ""),
        "detail_read": False,
        "signals": {
            "has_href": bool(item.get("href")),
            "has_affair_id": bool(item.get("affair_id")),
        },
        "attention_score": attention["score"],
        "attention_signals": attention["signals"],
    }


def _workflow_detail_summary(
    source_item: dict[str, Any],
    detail: dict[str, Any],
    args: dict[str, Any],
) -> dict[str, Any]:
    text = str(detail.get("text") or "")
    text_limit = _workflow_text_limit_from_args(args)
    actions = detail.get("actions") if isinstance(detail.get("actions"), list) else []
    action_codes = _workflow_action_codes(actions)
    high_risk_action_count = _workflow_high_risk_action_count(actions)
    attention = _workflow_detail_attention(source_item, detail)
    return {
        "title": detail.get("title") or source_item.get("title", ""),
        "affair_id": str(source_item.get("affair_id") or _query_param(str(detail.get("url") or ""), "affairId")),
        "sender": source_item.get("sender", ""),
        "date": source_item.get("date", ""),
        "category": source_item.get("category", ""),
        "url": detail.get("url") or source_item.get("href", ""),
        "text_excerpt": text[:text_limit],
        "text_length": len(text),
        "field_count": _list_len(detail.get("fields")),
        "attachment_count": _list_len(detail.get("attachments")),
        "opinion_count": _list_len(detail.get("workflow")),
        "action_count": _list_len(detail.get("actions")),
        "has_attachments": _list_len(detail.get("attachments")) > 0,
        "has_write_actions": _list_len(detail.get("actions")) > 0,
        "action_codes": action_codes,
        "high_risk_action_count": high_risk_action_count,
        "attention_score": attention["score"],
        "attention_signals": attention["signals"],
    }


def _workflow_evidence_packet(
    source_item: dict[str, Any],
    detail: dict[str, Any],
    summary: dict[str, Any],
    args: dict[str, Any],
) -> dict[str, Any]:
    text = str(detail.get("text") or "")
    text_limit = _workflow_text_limit_from_args(args)
    full_text_length = int(summary.get("text_length") or len(text))
    fields = detail.get("fields") if isinstance(detail.get("fields"), list) else []
    attachments = detail.get("attachments") if isinstance(detail.get("attachments"), list) else []
    opinions = detail.get("workflow") if isinstance(detail.get("workflow"), list) else []
    actions = detail.get("actions") if isinstance(detail.get("actions"), list) else []
    action_codes = _workflow_action_codes(actions)
    packet = {
        "identity": {
            "title": summary.get("title") or detail.get("title") or source_item.get("title", ""),
            "affair_id": summary.get("affair_id") or source_item.get("affair_id", ""),
            "sender": summary.get("sender") or source_item.get("sender", ""),
            "date": summary.get("date") or source_item.get("date", ""),
            "category": summary.get("category") or source_item.get("category", ""),
            "url": summary.get("url") or detail.get("url") or source_item.get("href", ""),
        },
        "body": {
            "text_excerpt": text[:text_limit],
            "text_length": full_text_length,
            "truncated": full_text_length > text_limit,
        },
        "fields": fields,
        "attachments": {"count": len(attachments), "items": attachments},
        "opinions": {"count": len(opinions), "items": opinions},
        "actions": {
            "count": len(actions),
            "high_risk_count": _workflow_high_risk_action_count(actions),
            "codes": action_codes,
            "items": actions,
        },
    }
    attention = _workflow_evidence_attention(packet)
    packet["attention_score"] = attention["score"]
    packet["attention_signals"] = attention["signals"]
    packet["recommended_next_step"] = _inbox_recommended_next_step(attention["signals"], detail_read=True)
    return packet


def _inbox_analysis_item(index: int, item: dict[str, Any], workflow_type: str) -> dict[str, Any]:
    attention = _workflow_item_attention(item)
    href = str(item.get("href") or "")
    affair_id = str(item.get("affair_id") or "")
    return {
        "rank": index,
        "source_index": index,
        "title": item.get("title", ""),
        "affair_id": affair_id,
        "sender": item.get("sender", ""),
        "date": item.get("date", ""),
        "category": item.get("category", ""),
        "read": item.get("read"),
        "href": href,
        "detail_read": False,
        "summary": _inbox_item_summary(item),
        "attention_score": attention["score"],
        "attention_signals": attention["signals"],
        "recommended_next_step": _inbox_recommended_next_step(attention["signals"], detail_read=False),
        "commands": _workflow_followup_commands(workflow_type, affair_id=affair_id, href=href),
    }


def _enrich_inbox_item_with_evidence(item: dict[str, Any], evidence: dict[str, Any]) -> None:
    evidence_summary = _workflow_evidence_summary(evidence)
    evidence_attention = _workflow_evidence_attention(evidence)
    item["detail_read"] = True
    item["evidence_summary"] = evidence_summary
    item["attention_score"] = int(item.get("attention_score") or 0) + evidence_attention["score"]
    item["attention_signals"] = _merge_unique_strings(
        item.get("attention_signals", []),
        evidence_attention["signals"],
    )
    item["recommended_next_step"] = _inbox_recommended_next_step(item["attention_signals"], detail_read=True)


def _workflow_evidence_summary(evidence: dict[str, Any]) -> dict[str, Any]:
    body = evidence.get("body") if isinstance(evidence.get("body"), dict) else {}
    attachments = evidence.get("attachments") if isinstance(evidence.get("attachments"), dict) else {}
    opinions = evidence.get("opinions") if isinstance(evidence.get("opinions"), dict) else {}
    actions = evidence.get("actions") if isinstance(evidence.get("actions"), dict) else {}
    return {
        "body": {
            "text_excerpt": body.get("text_excerpt", ""),
            "text_length": body.get("text_length", 0),
            "truncated": body.get("truncated", False),
        },
        "attachments": {"count": int(attachments.get("count") or 0)},
        "opinions": {"count": int(opinions.get("count") or 0)},
        "actions": {
            "count": int(actions.get("count") or 0),
            "high_risk_count": int(actions.get("high_risk_count") or 0),
            "codes": actions.get("codes") if isinstance(actions.get("codes"), list) else [],
        },
    }


def _oa_write_prepare_packet(
    *,
    workflow_type: str,
    evidence_result: dict[str, Any],
    preflight: dict[str, Any],
    action: str,
    opinion: str,
) -> dict[str, Any]:
    evidence = evidence_result.get("evidence") if isinstance(evidence_result.get("evidence"), dict) else {}
    source_item = evidence_result.get("source_item") if isinstance(evidence_result.get("source_item"), dict) else {}
    decision = preflight.get("decision") if isinstance(preflight.get("decision"), dict) else {}
    execution_contract = (
        preflight.get("execution_contract")
        if isinstance(preflight.get("execution_contract"), dict)
        else {}
    )
    target = preflight.get("target") if isinstance(preflight.get("target"), dict) else {}
    if not target:
        identity = evidence.get("identity") if isinstance(evidence.get("identity"), dict) else {}
        target = {"affair_id": str(identity.get("affair_id") or source_item.get("affair_id") or "")}
    return {
        "schema_version": "bscli.oa_write_prepare.v1",
        "type": workflow_type,
        "target": target,
        "action": preflight.get("action") if isinstance(preflight.get("action"), dict) else {"code": action},
        "opinion": {"length": len(str(opinion or ""))},
        "workflow": {
            "source_item": source_item,
            "evidence_summary": _workflow_evidence_summary(evidence),
            "attention_signals": evidence.get("attention_signals", []),
            "read_effect": evidence_result.get("read_effect", {}),
        },
        "preflight": {
            "decision": decision,
            "execution_contract": execution_contract,
            "read_effect": preflight.get("read_effect", {}),
            "probe_policy": preflight.get("probe_policy", {}),
            "plan": preflight.get("plan", {}),
        },
        "next_steps": _oa_write_prepare_next_steps(decision, execution_contract),
    }


def _oa_write_prepare_next_steps(
    decision: dict[str, Any],
    execution_contract: dict[str, Any],
) -> dict[str, Any]:
    status = str(decision.get("status") or "")
    if status == "ready_for_execute":
        return {
            "status": "needs_human_confirmation",
            "message": "Preflight passed; execute only after the user explicitly confirms the production write.",
            "dry_run_command_template": execution_contract.get("dry_run_command_template", ""),
            "execute_command_template": execution_contract.get("execute_command_template", ""),
            "requires_confirmation": True,
        }
    if status == "dry_run_only":
        return {
            "status": "dry_run_only",
            "message": "The action is visible and dry-run checked, but production execution is not promoted.",
            "dry_run_command_template": execution_contract.get("dry_run_command_template", ""),
            "execute_command_template": "",
            "requires_confirmation": False,
        }
    return {
        "status": "blocked",
        "message": "The target is not ready for execution; review blocked_reasons and suggestions.",
        "dry_run_command_template": execution_contract.get("dry_run_command_template", ""),
        "execute_command_template": "",
        "requires_confirmation": False,
    }


def _rank_inbox_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        items,
        key=lambda item: (
            -int(item.get("attention_score") or 0),
            int(item.get("source_index") or 0),
        ),
    )
    for rank, item in enumerate(ranked, start=1):
        item["rank"] = rank
    return ranked


def _workflow_item_attention(item: dict[str, Any]) -> dict[str, Any]:
    score = 0
    signals: list[str] = []
    read_value = item.get("read")
    if read_value is False or str(read_value).lower() in {"false", "0", "unread", "no"}:
        score += 5
        signals.append("unread")
    if not item.get("href"):
        score += 2
        signals.append("missing_detail_href")
    if not item.get("affair_id"):
        score += 2
        signals.append("missing_affair_id")
    keyword_attention = _workflow_keyword_attention(
        " ".join(str(item.get(key) or "") for key in ("title", "category", "sender"))
    )
    score += keyword_attention["score"]
    signals.extend(keyword_attention["signals"])
    return {"score": score, "signals": _merge_unique_strings(signals)}


def _workflow_detail_attention(source_item: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    attention_source = dict(source_item)
    detail_url = str(detail.get("url") or "")
    if not attention_source.get("title"):
        attention_source["title"] = detail.get("title", "")
    if not attention_source.get("href") and detail_url:
        attention_source["href"] = detail_url
    if not attention_source.get("affair_id") and detail_url:
        attention_source["affair_id"] = _query_param(detail_url, "affairId")
    base = _workflow_item_attention(attention_source)
    packet = {
        "body": {
            "text_excerpt": str(detail.get("text") or "")[:300],
            "text_length": len(str(detail.get("text") or "")),
            "truncated": False,
        },
        "attachments": {"count": _list_len(detail.get("attachments"))},
        "opinions": {"count": _list_len(detail.get("workflow"))},
        "actions": {
            "count": _list_len(detail.get("actions")),
            "high_risk_count": _workflow_high_risk_action_count(
                detail.get("actions") if isinstance(detail.get("actions"), list) else []
            ),
            "codes": _workflow_action_codes(detail.get("actions") if isinstance(detail.get("actions"), list) else []),
        },
    }
    evidence = _workflow_evidence_attention(packet)
    return {
        "score": base["score"] + evidence["score"],
        "signals": _merge_unique_strings(base["signals"], evidence["signals"]),
    }


def _workflow_evidence_attention(evidence: dict[str, Any]) -> dict[str, Any]:
    score = 0
    signals: list[str] = []
    attachments = evidence.get("attachments") if isinstance(evidence.get("attachments"), dict) else {}
    opinions = evidence.get("opinions") if isinstance(evidence.get("opinions"), dict) else {}
    actions = evidence.get("actions") if isinstance(evidence.get("actions"), dict) else {}
    body = evidence.get("body") if isinstance(evidence.get("body"), dict) else {}
    if int(attachments.get("count") or 0) > 0:
        score += 2
        signals.append("has_attachments")
    if int(opinions.get("count") or 0) > 0:
        score += 1
        signals.append("has_opinions")
    if int(actions.get("count") or 0) > 0:
        score += 3
        signals.append("has_candidate_actions")
    if int(actions.get("high_risk_count") or 0) > 0:
        score += 8
        signals.append("has_high_risk_actions")
    action_codes = actions.get("codes") if isinstance(actions.get("codes"), list) else []
    if any("archive" in str(code).lower() or "归档" in str(code) for code in action_codes):
        score += 4
        signals.append("archive_action_available")
    if body.get("truncated") is True:
        score += 1
        signals.append("body_truncated")
    existing = evidence.get("attention_signals") if isinstance(evidence.get("attention_signals"), list) else []
    return {"score": score, "signals": _merge_unique_strings(signals, existing)}


def _workflow_keyword_attention(text: str) -> dict[str, Any]:
    lowered = text.lower()
    score = 0
    signals = []
    for name, points, keywords in _WORKFLOW_FOCUS_KEYWORDS:
        if any(keyword.lower() in lowered for keyword in keywords):
            score += points
            signals.append(f"topic:{name}")
    return {"score": score, "signals": signals}


def _workflow_action_codes(actions: list[Any]) -> list[str]:
    codes = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        value = action.get("code") or action.get("action") or action.get("value") or action.get("label") or action.get("name")
        code = str(value or "").strip()
        if code and code not in codes:
            codes.append(code)
    return codes


def _workflow_high_risk_action_count(actions: list[Any]) -> int:
    return len(
        [
            action
            for action in actions
            if isinstance(action, dict) and str(action.get("risk") or "").lower() == "high"
        ]
    )


def _workflow_followup_commands(workflow_type: str, *, affair_id: str, href: str) -> dict[str, str]:
    if affair_id:
        target = f"--id {affair_id}"
    elif href:
        target = f'--url "{href}"'
    else:
        target = ""
    if not target:
        return {
            "list": f"oa workflow brief --type {workflow_type}",
        }
    return {
        "inspect": f"oa workflow inspect --type {workflow_type} {target}",
        "evidence": f"oa workflow evidence --type {workflow_type} {target}",
        "timeline": f"oa workflow timeline --type {workflow_type} {target}",
    }


def _inbox_item_summary(item: dict[str, Any]) -> str:
    parts = [
        str(item.get("title") or "").strip(),
        str(item.get("sender") or "").strip(),
        str(item.get("date") or "").strip(),
        str(item.get("category") or "").strip(),
    ]
    return " | ".join(part for part in parts if part)


def _inbox_recommended_next_step(signals: list[str], *, detail_read: bool) -> str:
    if "missing_detail_href" in signals:
        return "refresh_or_open_oa_before_detail_read"
    if "has_high_risk_actions" in signals or "archive_action_available" in signals:
        return "review_evidence_before_write"
    if not detail_read:
        return "inspect_before_action"
    return "ready_for_human_review"


def _inbox_deep_limit_from_args(args: dict[str, Any]) -> int:
    value = args.get("deep_limit")
    if value is None:
        return 3
    try:
        return max(int(value), 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("deep_limit must be an integer") from exc


def _merge_unique_strings(*groups: Any) -> list[str]:
    values: list[str] = []
    for group in groups:
        if isinstance(group, str):
            iterable = [group]
        elif isinstance(group, list):
            iterable = group
        else:
            iterable = []
        for item in iterable:
            value = str(item or "").strip()
            if value and value not in values:
                values.append(value)
    return values


def _workflow_timeline_entry(index: int, entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {
            "index": index,
            "node": "",
            "handler": "",
            "time": "",
            "opinion": "",
            "text": str(entry),
            "raw": entry,
        }
    text = str(entry.get("text") or entry.get("content") or entry.get("opinion") or "")
    return {
        "index": index,
        "node": entry.get("node") or entry.get("activity") or entry.get("step") or "",
        "handler": entry.get("handler") or entry.get("actor") or entry.get("name") or "",
        "time": entry.get("time") or entry.get("date") or entry.get("created_at") or "",
        "opinion": entry.get("opinion") or entry.get("attitude") or entry.get("decision") or "",
        "text": text,
        "raw": entry,
    }


def _workflow_text_limit_from_args(args: dict[str, Any]) -> int:
    value = args.get("text_limit")
    if value is None:
        return 3000
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 3000


def _list_len(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _oa_write_category_for_item(item: dict[str, Any]) -> str:
    href = str(item.get("href") or "").lower()
    kind = " ".join(
        str(item.get(key) or "").lower()
        for key in ("category", "type", "source", "module", "app")
    )
    if "meetingid=" in href or "meeting.do" in href or "meeting" in kind:
        return "meeting"
    return "workflow"


def _current_state_from_pending_item(item: dict[str, Any]) -> dict[str, Any]:
    state = {}
    for key in ("state", "status", "sender", "send_time", "receive_time", "deadline"):
        if key in item:
            state[key] = item.get(key)
    return state


def _unpromoted_oa_write_action_capabilities(
    actions: list[dict[str, Any]],
    affair_id: str,
) -> list[dict[str, Any]]:
    capabilities = []
    for action in actions:
        code = str(action.get("code") or "")
        if not is_dry_run_only_write_action(code):
            continue
        promotion = write_action_promotion(code)
        capabilities.append(
            {
                "name": write_action_type(code),
                "action": code,
                "label": action.get("label", ""),
                "risk": action.get("risk", "medium"),
                "dry_run_allowed": promotion["dry_run_allowed"],
                "execute_allowed": promotion["execute_allowed"],
                "dry_run_command": f"oa write dry-run --affair-id {affair_id} --action {code}",
                "execute_command": None,
                "tool_names": ["oa__write_dry_run"],
                "daemon_commands": {"dry_run": "write_dry_run"},
                "verification_method": promotion["verification_method"],
                "promotion_status": promotion["status"],
                "promotion_requirements": promotion["requirements"],
                "blocked_reasons": promotion["blocked_reasons"],
                "governance": build_write_governance(
                    write_action_type(code),
                    verification_method=promotion["verification_method"],
                ),
            }
        )
    return capabilities


def _blocked_reasons_from_unpromoted_actions(actions: list[dict[str, Any]]) -> list[str]:
    reasons = []
    for action in actions:
        for reason in action.get("blocked_reasons", []):
            if reason not in reasons:
                reasons.append(reason)
    return reasons


def _promotion_requirements_from_unpromoted_actions(actions: list[dict[str, Any]]) -> list[str]:
    requirements = []
    for action in actions:
        for requirement in action.get("promotion_requirements", []):
            if requirement not in requirements:
                requirements.append(requirement)
    return requirements


def _query_param(url: str, name: str) -> str:
    try:
        parsed = urlparse(url)
        return parse_qs(parsed.query).get(name, [""])[0]
    except Exception:
        return ""


def _section_url_with_arguments(url: str, updates: dict[str, Any]) -> str:
    if not updates:
        return url
    parsed = urlparse(str(url or ""))
    query = parse_qs(parsed.query, keep_blank_values=True)
    raw_arguments = query.get("arguments", ["{}"])[0] or "{}"
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError:
        arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    for key, value in updates.items():
        if value is not None:
            arguments[key] = str(value)
    query["arguments"] = [json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))]
    return parsed._replace(query=urlencode(query, doseq=True)).geturl()
