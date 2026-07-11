from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from uuid import uuid4


class CentralProfileInUseError(RuntimeError):
    pass


class CentralBrowserWorker:
    def __init__(
        self,
        *,
        profile_path: Path | str,
        allowed_origins: set[str],
        headless: bool = True,
        executable_path: str | None = None,
        playwright_starter: Callable[[], Any] | None = None,
    ) -> None:
        self.profile_path = Path(profile_path)
        self.allowed_origins = {_normalize_origin(origin) for origin in allowed_origins}
        self.headless = headless
        self.executable_path = executable_path
        self._playwright_starter = playwright_starter or _start_playwright
        self._controller = None
        self._context = None
        self._lease = _ProfileLease(self.profile_path / ".agentbridge-browser-lease.json")

    def __enter__(self) -> CentralBrowserWorker:
        return self.start()

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def start(self) -> CentralBrowserWorker:
        if self._context is not None:
            return self
        self.profile_path.mkdir(parents=True, exist_ok=True)
        self._lease.acquire()
        try:
            self._controller = self._playwright_starter()
            launch_options: dict[str, Any] = {"headless": self.headless}
            if self.executable_path:
                launch_options["executable_path"] = self.executable_path
            self._context = self._controller.chromium.launch_persistent_context(
                str(self.profile_path),
                **launch_options,
            )
        except Exception:
            if self._controller is not None:
                self._controller.stop()
            self._controller = None
            self._lease.release()
            raise
        return self

    def close(self) -> None:
        context, self._context = self._context, None
        controller, self._controller = self._controller, None
        try:
            if context is not None:
                context.close()
        finally:
            try:
                if controller is not None:
                    controller.stop()
            finally:
                self._lease.release()

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: Any = None,
        timeout_seconds: float = 30,
    ) -> dict:
        self._require_started()
        self._validate_url(url)
        options: dict[str, Any] = {
            "method": method.upper(),
            "headers": headers or {},
            "timeout": max(timeout_seconds, 0.1) * 1000,
            "max_redirects": 0,
        }
        if body is not None:
            options["data"] = body
        response = self._context.request.fetch(url, **options)
        try:
            self._validate_url(response.url)
            response_headers = response.headers
            if callable(response_headers):
                response_headers = response_headers()
            content_type = str((response_headers or {}).get("content-type") or "")
            text = response.text()
            payload = None
            if "json" in content_type.lower():
                try:
                    payload = response.json()
                except Exception:
                    payload = None
            return {
                "status": response.status,
                "url": response.url,
                "content_type": content_type,
                "json": payload,
                "text": text,
            }
        finally:
            dispose = getattr(response, "dispose", None)
            if callable(dispose):
                dispose()

    def goto(self, url: str, *, timeout_seconds: float = 30):
        self._require_started()
        self._validate_url(url)
        page = self.page
        page.goto(url, wait_until="domcontentloaded", timeout=max(timeout_seconds, 0.1) * 1000)
        self._validate_url(page.url)
        return page

    @property
    def page(self):
        self._require_started()
        if self._context.pages:
            return self._context.pages[0]
        return self._context.new_page()

    @property
    def page_title(self) -> str:
        return self.page.title()

    @property
    def page_url(self) -> str:
        return self.page.url

    def capture_session_state(self) -> dict:
        self._require_started()
        cookies = self._context.cookies()
        if any(not self._cookie_is_allowed(cookie) for cookie in cookies):
            raise ValueError("central browser produced a disallowed cookie")
        return {"cookies": cookies}

    def restore_session_state(self, state: dict) -> None:
        self._require_started()
        cookies = state.get("cookies") if isinstance(state, dict) else None
        if not isinstance(cookies, list):
            raise ValueError("central browser session state must contain cookies")
        for cookie in cookies:
            if not isinstance(cookie, dict) or not self._cookie_is_allowed(cookie):
                raise ValueError("central browser session state contains a disallowed cookie")
        if cookies:
            self._context.add_cookies(cookies)

    def clear_session_state(self) -> None:
        self._require_started()
        self._context.clear_cookies()

    def _require_started(self) -> None:
        if self._context is None:
            raise RuntimeError("central browser worker is not started")

    def _validate_url(self, url: str) -> None:
        origin = _origin_from_url(url)
        if origin not in self.allowed_origins:
            raise ValueError(f"request origin is not allowed: {origin}")

    def _cookie_is_allowed(self, cookie: dict) -> bool:
        cookie_url = cookie.get("url")
        if isinstance(cookie_url, str) and cookie_url:
            try:
                return _origin_from_url(cookie_url) in self.allowed_origins
            except ValueError:
                return False
        domain = str(cookie.get("domain") or "").lstrip(".").lower()
        if not domain:
            return False
        allowed_hosts = {urlparse(origin).hostname or "" for origin in self.allowed_origins}
        return any(host == domain or host.endswith(f".{domain}") for host in allowed_hosts)


def _start_playwright():
    from playwright.sync_api import sync_playwright

    return sync_playwright().start()


def _origin_from_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("browser URL must include an http(s) origin")
    if parsed.username or parsed.password:
        raise ValueError("browser URL must not contain credentials")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _normalize_origin(origin: str) -> str:
    parsed = urlparse(origin)
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError(f"allowed origin must not include a path: {origin}")
    return _origin_from_url(origin)


class _ProfileLease:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lease_id = str(uuid4())
        self.acquired = False

    def acquire(self) -> None:
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "lease_id": self.lease_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=True,
        ).encode("utf-8")
        for _attempt in range(2):
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError:
                owner = self._read_owner()
                owner_pid = owner.get("pid") if isinstance(owner, dict) else None
                if isinstance(owner_pid, int) and not _pid_is_running(owner_pid):
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                owner_text = str(owner_pid) if owner_pid is not None else "unknown"
                raise CentralProfileInUseError(
                    f"central browser profile is already leased by process {owner_text}"
                )
            else:
                try:
                    os.write(descriptor, payload)
                finally:
                    os.close(descriptor)
                self.acquired = True
                return
        raise CentralProfileInUseError("central browser profile lease could not be acquired")

    def release(self) -> None:
        if not self.acquired:
            return
        self.acquired = False
        owner = self._read_owner()
        if isinstance(owner, dict) and owner.get("lease_id") != self.lease_id:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _read_owner(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_is_running(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_pid_is_running(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == 5
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)
