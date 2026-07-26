from __future__ import annotations

from copy import deepcopy
import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


class CentralHttpWorker:
    """Origin-restricted HTTP worker with encrypted caller-owned session state."""

    def __init__(self, *, allowed_origins: set[str]) -> None:
        self.allowed_origins = {_normalize_origin(origin) for origin in allowed_origins}
        self._open = build_opener(_RejectRedirectHandler()).open
        self._state: dict[str, Any] = {"cookies": [], "http": {}}

    def __enter__(self) -> CentralHttpWorker:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: Any = None,
        timeout_seconds: float = 30,
    ) -> dict:
        self._validate_url(url)
        request_headers = dict(headers or {})
        encoded_body = None
        if body is not None:
            encoded_body = json.dumps(
                body,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json;charset=utf-8")
        request_headers.setdefault("Accept", "application/json")
        started_at = time.monotonic()
        request = Request(
            url,
            data=encoded_body,
            headers=request_headers,
            method=method.upper(),
        )
        try:
            response = self._open(request, timeout=max(timeout_seconds, 0.1))
        except HTTPError as exc:
            return self._response(
                status=exc.code,
                url=exc.geturl(),
                headers=exc.headers,
                content=exc.read(),
                started_at=started_at,
            )
        except (TimeoutError, URLError) as exc:
            raise ConnectionError(f"downstream HTTP request failed: {exc}") from exc
        with response:
            return self._response(
                status=response.status,
                url=response.geturl(),
                headers=response.headers,
                content=response.read(),
                started_at=started_at,
            )

    def capture_session_state(self) -> dict:
        return deepcopy(self._state)

    def restore_session_state(self, state: dict) -> None:
        if not isinstance(state, dict) or not isinstance(state.get("cookies"), list):
            raise ValueError("central HTTP session state is invalid")
        http_state = state.get("http")
        if not isinstance(http_state, dict):
            raise ValueError("central HTTP session state must contain HTTP state")
        self._state = {"cookies": [], "http": deepcopy(http_state)}

    def clear_session_state(self) -> None:
        self._state = {"cookies": [], "http": {}}

    def get_http_state(self) -> dict:
        return deepcopy(self._state["http"])

    def set_http_state(self, state: dict) -> None:
        if not isinstance(state, dict):
            raise TypeError("HTTP session state must be an object")
        self._state = {"cookies": [], "http": deepcopy(state)}

    def _response(
        self,
        *,
        status: int,
        url: str,
        headers,
        content: bytes,
        started_at: float,
    ) -> dict:
        self._validate_url(url)
        content_type = str(headers.get("Content-Type") or "")
        text = content.decode("utf-8", errors="replace")
        payload = None
        if "json" in content_type.lower() or text.lstrip().startswith(("{", "[")):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None
        return {
            "status": int(status),
            "url": url,
            "content_type": content_type,
            "json": payload,
            "text": text,
            "elapsed_ms": max(0, round((time.monotonic() - started_at) * 1000)),
        }

    def _validate_url(self, url: str) -> None:
        origin = _origin_from_url(url)
        if origin not in self.allowed_origins:
            raise ValueError(f"request origin is not allowed: {origin}")


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, _request, _file_pointer, _code, _message, _headers, _url):
        raise ValueError("downstream HTTP redirects are not allowed")

def _normalize_origin(value: str) -> str:
    return _origin_from_url(value)


def _origin_from_url(value: str) -> str:
    parsed = urlparse(str(value or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("HTTP worker URL must use http(s) with a host")
    if parsed.username or parsed.password:
        raise ValueError("HTTP worker URL must not contain credentials")
    default_port = 80 if parsed.scheme == "http" else 443
    port = parsed.port or default_port
    netloc = parsed.hostname.lower()
    if port != default_port:
        netloc = f"{netloc}:{port}"
    return f"{parsed.scheme.lower()}://{netloc}"
