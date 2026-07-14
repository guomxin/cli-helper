from __future__ import annotations

from dataclasses import dataclass
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
from pathlib import Path
import re
import ssl
from urllib.parse import urlparse

from bscli.auth.action_card import TrustedActionApplication
from bscli.auth.card import MAX_AUTH_BODY_BYTES, AuthCardResponse, TrustedAuthApplication
from bscli.auth.field_card import TrustedFieldApplication
from bscli.core.network_security import validate_insecure_private_http_endpoint


@dataclass(frozen=True)
class AuthServerConfig:
    host: str
    port: int
    public_base_url: str
    tls_cert: Path | None
    tls_key: Path | None

    @property
    def secure_cookie(self) -> bool:
        return self.public_base_url.startswith("https://")

    @property
    def insecure_private_http(self) -> bool:
        return self.tls_cert is None and not _is_loopback_host(self.host)


class AuthHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def validate_auth_server_config(
    *,
    host: str,
    port: int,
    public_base_url: str | None,
    tls_cert: str | Path | None,
    tls_key: str | Path | None,
    allow_insecure_private_http: bool = False,
) -> AuthServerConfig:
    if port < 0 or port > 65535:
        raise ValueError("authentication server port is invalid")
    cert = Path(tls_cert).resolve() if tls_cert else None
    key = Path(tls_key).resolve() if tls_key else None
    if (cert is None) != (key is None):
        raise ValueError("both TLS certificate and key are required")
    loopback = _is_loopback_host(host)
    if not loopback and cert is None and not allow_insecure_private_http:
        raise ValueError("non-loopback authentication card service requires TLS")
    if public_base_url is None:
        if not loopback:
            raise ValueError("non-loopback authentication card service requires public base URL")
        public_base_url = f"http://127.0.0.1:{port}"
    normalized_base_url = _normalize_public_base_url(public_base_url)
    if not loopback and cert is None:
        validate_insecure_private_http_endpoint(
            host=host,
            port=port,
            public_base_url=normalized_base_url,
            service_name="authentication card service",
        )
    elif not loopback and not normalized_base_url.startswith("https://"):
        raise ValueError("non-loopback authentication card public URL must use HTTPS")
    if cert is not None and not normalized_base_url.startswith("https://"):
        raise ValueError("TLS authentication card service must use an HTTPS public URL")
    return AuthServerConfig(
        host=host,
        port=port,
        public_base_url=normalized_base_url,
        tls_cert=cert,
        tls_key=key,
    )


def create_auth_http_server(
    *,
    config: AuthServerConfig,
    application: TrustedAuthApplication,
    action_application: TrustedActionApplication | None = None,
    field_application: TrustedFieldApplication | None = None,
) -> ThreadingHTTPServer:
    expected_scheme = urlparse(config.public_base_url).scheme.lower()
    allowed_hosts = {_hostname(config.public_base_url)}
    if _is_loopback_host(config.host):
        allowed_hosts.update({"127.0.0.1", "localhost", "::1"})

    class AuthRequestHandler(BaseHTTPRequestHandler):
        server_version = "AgentBridgeAuth/0.1"
        sys_version = ""

        def log_message(self, _format: str, *_args) -> None:
            return None

        def do_GET(self) -> None:
            if not self._host_allowed():
                self._send(application._message_response(
                    status=400,
                    title="请求主机无效",
                    message="认证请求已被拒绝。",
                    tone="error",
                ))
                return
            if self.path == "/favicon.ico":
                self.send_response(204)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            card_application, card_id = self._card_target()
            if card_application is None or card_id is None:
                self._send(application._message_response(
                    status=404,
                    title="页面不存在",
                    message="请从智能体打开可信卡片。",
                    tone="error",
                ))
                return
            self._send(
                card_application.get_card(
                    card_id,
                    secure_cookie=config.secure_cookie,
                )
            )

        def do_POST(self) -> None:
            if not self._host_allowed() or not self._origin_allowed():
                self._send(application._message_response(
                    status=403,
                    title="请求来源无效",
                    message="认证请求已被拒绝。",
                    tone="error",
                ))
                return
            card_application, card_id = self._card_target()
            if card_application is None or card_id is None:
                self._send(application._message_response(
                    status=404,
                    title="页面不存在",
                    message="请从智能体打开可信卡片。",
                    tone="error",
                ))
                return
            try:
                content_length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                content_length = -1
            if content_length < 0 or content_length > MAX_AUTH_BODY_BYTES:
                self.close_connection = True
                self._send(card_application.submit_card(
                    card_id,
                    body=b"x" * (MAX_AUTH_BODY_BYTES + 1),
                    content_type=self.headers.get("Content-Type") or "",
                    csrf_cookie="",
                ))
                return
            body = self.rfile.read(content_length)
            csrf_cookie = _csrf_cookie(self.headers.get("Cookie") or "")
            try:
                response = card_application.submit_card(
                    card_id,
                    body=body,
                    content_type=self.headers.get("Content-Type") or "",
                    csrf_cookie=csrf_cookie,
                )
            finally:
                body = b""
            self._send(response)

        def do_OPTIONS(self) -> None:
            self.send_response(405)
            self.send_header("Allow", "GET, POST")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _host_allowed(self) -> bool:
            try:
                host = _host_header_name(self.headers.get("Host") or "")
            except ValueError:
                return False
            return host in allowed_hosts

        def _origin_allowed(self) -> bool:
            return _request_origin_allowed(
                origin=self.headers.get("Origin"),
                sec_fetch_site=self.headers.get("Sec-Fetch-Site"),
                host_header=self.headers.get("Host") or "",
                expected_scheme=expected_scheme,
                allowed_hosts=allowed_hosts,
                allow_opaque_without_fetch_metadata=config.insecure_private_http,
            )

        def _card_target(self):
            challenge_id = _challenge_id_from_path(self.path)
            if challenge_id is not None:
                return application, challenge_id
            authorization_id = _authorization_id_from_path(self.path)
            if authorization_id is not None and action_application is not None:
                return action_application, authorization_id
            submission_id = _field_submission_id_from_path(self.path)
            if submission_id is not None and field_application is not None:
                return field_application, submission_id
            return None, None

        def _send(self, response: AuthCardResponse) -> None:
            self.send_response(response.status)
            for name, value in response.headers.items():
                self.send_header(name, value)
            if config.secure_cookie:
                self.send_header("Strict-Transport-Security", "max-age=31536000")
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

    server = AuthHTTPServer((config.host, config.port), AuthRequestHandler)
    if config.tls_cert is not None and config.tls_key is not None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(config.tls_cert, config.tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def serve_auth_cards(
    *,
    config: AuthServerConfig,
    application: TrustedAuthApplication,
    action_application: TrustedActionApplication | None = None,
    field_application: TrustedFieldApplication | None = None,
) -> None:
    server = create_auth_http_server(
        config=config,
        application=application,
        action_application=action_application,
        field_application=field_application,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


def _challenge_id_from_path(path: str) -> str | None:
    match = re.fullmatch(r"/auth/([A-Za-z0-9_-]{32,128})", path.split("?", 1)[0])
    return match.group(1) if match else None


def _authorization_id_from_path(path: str) -> str | None:
    match = re.fullmatch(r"/authorize/([A-Za-z0-9_-]{32,128})", path.split("?", 1)[0])
    return match.group(1) if match else None


def _field_submission_id_from_path(path: str) -> str | None:
    match = re.fullmatch(r"/input/([A-Za-z0-9_-]{32,128})", path.split("?", 1)[0])
    return match.group(1) if match else None


def _csrf_cookie(value: str) -> str:
    cookie = SimpleCookie()
    try:
        cookie.load(value)
    except Exception:
        return ""
    morsel = cookie.get("agentbridge_csrf")
    return morsel.value if morsel is not None else ""


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
        raise ValueError("authentication card public base URL must be http(s)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("authentication card public base URL is invalid")
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"


def _origin(value: str) -> str:
    parsed = urlparse(value)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _hostname(value: str) -> str:
    hostname = urlparse(value).hostname
    if not hostname:
        raise ValueError("public base URL hostname is required")
    return hostname.lower()


def _host_header_name(value: str) -> str:
    parsed = urlparse(f"//{value}")
    if not parsed.hostname:
        raise ValueError("Host header is invalid")
    return parsed.hostname.lower()


def _request_origin_allowed(
    *,
    origin: str | None,
    sec_fetch_site: str | None,
    host_header: str,
    expected_scheme: str,
    allowed_hosts: set[str],
    allow_opaque_without_fetch_metadata: bool = False,
) -> bool:
    # Some browsers serialize a same-origin form POST from a loopback or
    # private-HTTP page as an opaque origin. Fetch Metadata is preferred proof;
    # the explicit private-HTTP PoC mode falls back to the card's CSRF binding.
    if origin is None:
        return True
    if origin.strip().lower() == "null":
        fetch_site = (sec_fetch_site or "").strip().lower()
        return fetch_site == "same-origin" or (
            allow_opaque_without_fetch_metadata and not fetch_site
        )

    try:
        parsed_origin = urlparse(origin)
        parsed_host = urlparse(f"//{host_header}")
        origin_host = (parsed_origin.hostname or "").lower()
        request_host = (parsed_host.hostname or "").lower()
        origin_port = parsed_origin.port or _default_port(parsed_origin.scheme)
        request_port = parsed_host.port or _default_port(expected_scheme)
    except ValueError:
        return False

    return (
        parsed_origin.scheme.lower() == expected_scheme
        and parsed_origin.username is None
        and parsed_origin.password is None
        and parsed_origin.path in {"", "/"}
        and not parsed_origin.params
        and not parsed_origin.query
        and not parsed_origin.fragment
        and origin_host in allowed_hosts
        and request_host in allowed_hosts
        and origin_port == request_port
    )


def _default_port(scheme: str) -> int | None:
    if scheme.lower() == "http":
        return 80
    if scheme.lower() == "https":
        return 443
    return None
