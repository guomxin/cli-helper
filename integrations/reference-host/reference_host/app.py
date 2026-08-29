from __future__ import annotations

import asyncio
from concurrent.futures import Future
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import threading
import time
from typing import Any, Coroutine
from urllib.parse import parse_qs, quote, urlparse

from .host_profile import build_host_profile
from .identity import IdentityRegistry
from .recovery import ReferenceHostRecovery
from .runtime_collector import ReferenceHostRuntimeCollector
from .state import ACTIVE_TASK_STATES, ReferenceHostState
from .task_driver import ReferenceTaskDriver


STATIC_DIRECTORY = Path(__file__).with_name("static")
TASK_ROUTE = re.compile(r"^/api/tasks/([^/]+)$")
EVENT_ROUTE = re.compile(r"^/api/tasks/([^/]+)/events$")
STREAM_ROUTE = re.compile(r"^/api/tasks/([^/]+)/stream$")
INTERACTION_ROUTE = re.compile(r"^/api/tasks/([^/]+)/interaction/open$")
ARTIFACT_DOWNLOAD_ROUTE = re.compile(
    r"^/api/tasks/([^/]+)/artifacts/([^/]+)/download$"
)
ARTIFACT_REISSUE_ROUTE = re.compile(
    r"^/api/tasks/([^/]+)/artifacts/([^/]+)/reissue$"
)
TERMINAL_TASK_STATES = frozenset({"succeeded", "failed", "canceled", "unknown"})


class AsyncHostRuntime:
    def __init__(self) -> None:
        mcp_url = os.environ.get(
            "AGENTBRIDGE_REFERENCE_MCP_URL",
            "https://10.10.50.213:8780/mcp",
        )
        state_path = Path(
            os.environ.get(
                "AGENTBRIDGE_REFERENCE_STATE_PATH",
                str(Path.home() / ".agentbridge-reference-host" / "state.db"),
            )
        )
        identities = IdentityRegistry.from_environment()
        self.driver = ReferenceTaskDriver(
            identities=identities,
            state=ReferenceHostState(state_path),
            mcp_url=mcp_url,
            ca_bundle=os.environ.get("AGENTBRIDGE_REFERENCE_CA_BUNDLE") or None,
            allow_insecure_tls=_env_bool(
                "AGENTBRIDGE_REFERENCE_ALLOW_INSECURE_TLS",
                False,
            ),
            poll_interval_seconds=_env_float(
                "AGENTBRIDGE_REFERENCE_POLL_SECONDS",
                2.0,
                0.25,
                30.0,
            ),
            maximum_interaction_wait_seconds=_env_float(
                "AGENTBRIDGE_REFERENCE_INTERACTION_WAIT_SECONDS",
                900.0,
                60.0,
                86_400.0,
            ),
        )
        self.recovery = ReferenceHostRecovery(self.driver)
        self.collector = ReferenceHostRuntimeCollector(
            self.driver,
            interval_seconds=_env_float(
                "AGENTBRIDGE_REFERENCE_RUNTIME_SAMPLE_SECONDS",
                60.0,
                15.0,
                3_600.0,
            ),
        )
        self.recovery_report: dict[str, Any] = {}
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="reference-host-async-runtime",
            daemon=True,
        )
        self._thread.start()
        try:
            self.call(self._initialize(), timeout=180)
        except Exception:
            try:
                self.call(self._close(), timeout=30)
            except Exception:
                pass
            self._stop_loop()
            raise

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    async def _initialize(self) -> None:
        await self.driver.initialize()
        self.recovery_report = await self.recovery.recover_all()
        await self.collector.collect_once()
        self.collector.start()

    def submit(self, operation: Coroutine[Any, Any, Any]) -> Future[Any]:
        return asyncio.run_coroutine_threadsafe(operation, self.loop)

    def call(
        self,
        operation: Coroutine[Any, Any, Any],
        *,
        timeout: float = 180.0,
    ) -> Any:
        return self.submit(operation).result(timeout=timeout)

    def close(self) -> None:
        try:
            self.call(self._close(), timeout=30)
        finally:
            self._stop_loop()

    def _stop_loop(self) -> None:
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=10)
        if not self.loop.is_closed():
            self.loop.close()

    async def _close(self) -> None:
        await self.collector.stop()
        await self.driver.close()


class ReferenceHostHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        runtime: AsyncHostRuntime,
        *,
        ui_token: str | None,
    ) -> None:
        self.runtime = runtime
        self.ui_token = ui_token
        self.browser_session = secrets.token_urlsafe(32)
        super().__init__(address, ReferenceHostHandler)


class ReferenceHostHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AgentBridgeReferenceHost/0.1"

    @property
    def app(self) -> ReferenceHostHttpServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, format: str, *args: Any) -> None:
        path = urlparse(self.path).path
        message = format % args
        print(f"reference-host {self.client_address[0]} {path} {message}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "healthy",
                    "hostInstanceId": self.app.runtime.driver.instance_id,
                },
            )
            return
        if self._establish_browser_session(parsed):
            return
        if not self._authorized():
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": {"code": "REFERENCE_HOST_AUTH_REQUIRED"}},
            )
            return
        try:
            self._dispatch_get(parsed)
        except (BrokenPipeError, ConnectionResetError):
            return
        except KeyError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, "NOT_FOUND", str(exc))
        except PermissionError as exc:
            self._send_error_json(HTTPStatus.FORBIDDEN, "FORBIDDEN", str(exc))
        except Exception as exc:
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                _error_code(exc),
                str(exc),
            )

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not self._authorized():
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": {"code": "REFERENCE_HOST_AUTH_REQUIRED"}},
            )
            return
        try:
            self._dispatch_post(parsed, self._read_json())
        except KeyError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, "NOT_FOUND", str(exc))
        except (ValueError, TypeError) as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", str(exc))
        except PermissionError as exc:
            self._send_error_json(HTTPStatus.FORBIDDEN, "FORBIDDEN", str(exc))
        except Exception as exc:
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                _error_code(exc),
                str(exc),
            )

    def _dispatch_get(self, parsed: Any) -> None:
        path = parsed.path
        query = parse_qs(parsed.query)
        runtime = self.app.runtime
        if path in {"/", "/index.html"}:
            self._send_static("index.html", "text/html; charset=utf-8")
            return
        if path == "/assets/styles.css":
            self._send_static("styles.css", "text/css; charset=utf-8")
            return
        if path == "/assets/host.js":
            self._send_static("host.js", "text/javascript; charset=utf-8")
            return
        if path == "/api/status":
            identities = runtime.driver.identities.list()
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ready",
                    "host": build_host_profile(instance_id=runtime.driver.instance_id),
                    "identities": [identity.public_dict() for identity in identities],
                    "counts": {
                        identity.label: runtime.driver.state.counts(
                            identity_label=identity.label
                        )
                        for identity in identities
                    },
                    "identityErrors": runtime.driver.identity_errors(),
                    "recovery": runtime.recovery_report,
                },
            )
            return
        if path == "/api/tools":
            identity = _query_text(query, "identity")
            runtime.call(runtime.driver.ensure_identity(identity), timeout=180)
            tools = [
                {
                    "name": tool.get("name"),
                    "title": tool.get("title") or tool.get("name"),
                    "description": tool.get("description") or "",
                    "inputSchema": tool.get("inputSchema") or {"type": "object"},
                    "annotations": tool.get("annotations") or {},
                }
                for tool in runtime.driver.list_tools(identity)
            ]
            self._send_json(HTTPStatus.OK, {"tools": tools})
            return
        if path == "/api/tasks":
            identity = _optional_query_text(query, "identity")
            active_only = _query_bool(query, "active", False)
            self._send_json(
                HTTPStatus.OK,
                {
                    "tasks": runtime.driver.list_task_views(
                        identity_label=identity,
                        active_only=active_only,
                        limit=200,
                    )
                },
            )
            return
        match = TASK_ROUTE.match(path)
        if match:
            local_task_id = match.group(1)
            self._send_json(
                HTTPStatus.OK,
                {
                    "task": runtime.driver.view_task(local_task_id),
                    "events": runtime.driver.state.list_events(
                        local_task_id=local_task_id,
                        limit=500,
                    ),
                },
            )
            return
        match = EVENT_ROUTE.match(path)
        if match:
            after = int((query.get("after") or ["0"])[0])
            self._send_json(
                HTTPStatus.OK,
                {
                    "events": runtime.driver.state.list_events(
                        local_task_id=match.group(1),
                        after_sequence=after,
                        limit=500,
                    )
                },
            )
            return
        match = STREAM_ROUTE.match(path)
        if match:
            after = int((query.get("after") or ["0"])[0])
            self._stream_task(match.group(1), after_sequence=after)
            return
        match = INTERACTION_ROUTE.match(path)
        if match:
            url = runtime.call(
                runtime.driver.interaction_url(match.group(1), refresh=True),
                timeout=180,
            )
            self._redirect(url)
            return
        match = ARTIFACT_DOWNLOAD_ROUTE.match(path)
        if match:
            url = runtime.call(
                runtime.driver.artifacts.download_url(match.group(1), match.group(2)),
                timeout=180,
            )
            self._redirect(url)
            return
        self._send_error_json(HTTPStatus.NOT_FOUND, "NOT_FOUND", "路径不存在")

    def _dispatch_post(self, parsed: Any, body: dict[str, Any]) -> None:
        path = parsed.path
        runtime = self.app.runtime
        if path == "/api/tasks":
            identity_label = _body_text(body, "identityLabel", 120)
            tool_name = _body_text(body, "toolName", 160)
            arguments = body.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise ValueError("arguments 必须是 JSON 对象")
            title = body.get("title")
            if title is not None:
                title = _body_text(body, "title", 200)
            task = runtime.call(
                runtime.driver.enqueue_task(
                    identity_label=identity_label,
                    tool_name=tool_name,
                    arguments=arguments,
                    title=title,
                ),
                timeout=180,
            )
            self._send_json(HTTPStatus.ACCEPTED, {"task": task})
            return
        if path == "/api/recover":
            runtime.recovery_report = runtime.call(
                runtime.recovery.recover_all(),
                timeout=180,
            )
            self._send_json(HTTPStatus.OK, runtime.recovery_report)
            return
        match = ARTIFACT_REISSUE_ROUTE.match(path)
        if match:
            result = runtime.call(
                runtime.driver.artifacts.reissue(match.group(1), match.group(2)),
                timeout=180,
            )
            self._send_json(HTTPStatus.OK, result)
            return
        self._send_error_json(HTTPStatus.NOT_FOUND, "NOT_FOUND", "路径不存在")

    def _stream_task(self, local_task_id: str, *, after_sequence: int) -> None:
        self.app.runtime.driver.state.get_task(local_task_id)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self._security_headers()
        self.end_headers()
        cursor = max(int(after_sequence), 0)
        last_keepalive = time.monotonic()
        terminal_seen_at: float | None = None
        while True:
            events = self.app.runtime.driver.state.list_events(
                local_task_id=local_task_id,
                after_sequence=cursor,
                limit=200,
            )
            for event in events:
                cursor = max(cursor, int(event["sequence"]))
                self._write_sse("task-event", event, event_id=str(cursor))
            task = self.app.runtime.driver.view_task(local_task_id)
            if events:
                self._write_sse("task-snapshot", task)
            if task["status"] in TERMINAL_TASK_STATES:
                terminal_seen_at = terminal_seen_at or time.monotonic()
                if time.monotonic() - terminal_seen_at >= 1.0:
                    self._write_sse("task-complete", task)
                    return
            if time.monotonic() - last_keepalive >= 15:
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                last_keepalive = time.monotonic()
            time.sleep(0.5)

    def _write_sse(
        self,
        event: str,
        value: dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> None:
        lines = []
        if event_id:
            lines.append(f"id: {event_id}")
        lines.append(f"event: {event}")
        lines.append(
            "data: "
            + json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        )
        payload = ("\n".join(lines) + "\n\n").encode("utf-8")
        self.wfile.write(payload)
        self.wfile.flush()

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Content-Length 无效") from exc
        if length < 0 or length > 1_000_000:
            raise ValueError("请求正文过大")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求正文不是有效 JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("请求正文必须是 JSON 对象")
        return value

    def _send_static(self, filename: str, content_type: str) -> None:
        path = STATIC_DIRECTORY / filename
        self._send_bytes(HTTPStatus.OK, path.read_bytes(), content_type)

    def _send_json(self, status: HTTPStatus, value: dict[str, Any]) -> None:
        self._send_bytes(
            status,
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _send_error_json(
        self,
        status: HTTPStatus,
        code: str,
        message: str,
    ) -> None:
        self._send_json(
            status,
            {"error": {"code": code, "message": str(message)[:500]}},
        )

    def _send_bytes(
        self,
        status: HTTPStatus,
        payload: bytes,
        content_type: str,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, url: str, *, set_cookie: str | None = None) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", url)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self._security_headers()
        self.end_headers()

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'",
        )

    def _establish_browser_session(self, parsed: Any) -> bool:
        if not self.app.ui_token:
            return False
        supplied = (parse_qs(parsed.query).get("access_token") or [""])[0]
        if not supplied:
            return False
        if not hmac.compare_digest(supplied, self.app.ui_token):
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": {"code": "REFERENCE_HOST_AUTH_REJECTED"}},
            )
            return True
        cookie = (
            "reference_host_session="
            + quote(self.app.browser_session, safe="")
            + "; Path=/; HttpOnly; SameSite=Strict"
        )
        self._redirect(parsed.path or "/", set_cookie=cookie)
        return True

    def _authorized(self) -> bool:
        if not self.app.ui_token:
            return True
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get("reference_host_session")
        return bool(
            morsel
            and hmac.compare_digest(morsel.value, self.app.browser_session)
        )


def main() -> None:
    bind = os.environ.get("AGENTBRIDGE_REFERENCE_BIND", "127.0.0.1").strip()
    port = _env_int("AGENTBRIDGE_REFERENCE_PORT", 8791, 1, 65_535)
    ui_token = os.environ.get("AGENTBRIDGE_REFERENCE_UI_TOKEN", "").strip() or None
    if not _is_loopback(bind) and ui_token is None:
        raise RuntimeError(
            "Non-loopback Reference Host requires AGENTBRIDGE_REFERENCE_UI_TOKEN"
        )
    runtime = AsyncHostRuntime()
    try:
        server = ReferenceHostHttpServer((bind, port), runtime, ui_token=ui_token)
    except Exception:
        runtime.close()
        raise
    stop_event = threading.Event()

    def stop(_signum: int, _frame: Any) -> None:
        if stop_event.is_set():
            return
        stop_event.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    for name in ("SIGINT", "SIGTERM"):
        value = getattr(signal, name, None)
        if value is not None:
            signal.signal(value, stop)
    print(f"AgentBridge Reference Host: http://{bind}:{port}")
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        runtime.close()


def _query_text(query: dict[str, list[str]], name: str) -> str:
    value = _optional_query_text(query, name)
    if not value:
        raise ValueError(f"缺少查询参数：{name}")
    return value


def _optional_query_text(
    query: dict[str, list[str]],
    name: str,
) -> str | None:
    value = (query.get(name) or [""])[0].strip()
    if len(value) > 256:
        raise ValueError(f"查询参数过长：{name}")
    return value or None


def _query_bool(
    query: dict[str, list[str]],
    name: str,
    default: bool,
) -> bool:
    value = (query.get(name) or [""])[0].strip().casefold()
    if not value:
        return default
    if value in {"1", "true", "yes"}:
        return True
    if value in {"0", "false", "no"}:
        return False
    raise ValueError(f"布尔查询参数无效：{name}")


def _body_text(body: dict[str, Any], name: str, maximum: int) -> str:
    value = body.get(name)
    if not isinstance(value, str):
        raise ValueError(f"缺少字段：{name}")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"字段无效：{name}")
    return normalized


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value < minimum or value > maximum:
        raise ValueError(f"{name} is out of range")
    return value


def _env_float(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = float(os.environ.get(name, str(default)))
    if value < minimum or value > maximum:
        raise ValueError(f"{name} is out of range")
    return value


def _is_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return bool(socket.getaddrinfo(host, None)) and all(
            address[4][0].startswith("127.") or address[4][0] == "::1"
            for address in socket.getaddrinfo(host, None)
        )
    except socket.gaierror:
        return False


def _error_code(exc: Exception) -> str:
    value = getattr(exc, "code", None) or exc.__class__.__name__
    return "".join(
        character if character.isalnum() else "_"
        for character in str(value).upper()
    ).strip("_")[:120]


if __name__ == "__main__":
    main()
