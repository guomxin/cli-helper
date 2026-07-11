from __future__ import annotations

import hashlib
import json
import re
import time
from urllib.parse import urljoin, urlparse

from bscli.adapters.seeyon import SEEYON_OA_URL
from bscli.adapters.seeyon_home import TEMPLATE_CENTER_API_URL, parse_template_center_response
from bscli.core.capability import CapabilityRegistry, CapabilitySpec


class SeeyonLoginRequired(RuntimeError):
    pass


class SeeyonAuthenticationRejected(RuntimeError):
    pass


class SeeyonLoginContractMismatch(RuntimeError):
    pass


class SeeyonUnsupportedAuthMethod(RuntimeError):
    pass


_AUTHENTICATION_FIELDS = [
    {
        "name": "username",
        "label": "OA 账号",
        "input_type": "text",
        "autocomplete": "username",
        "required": True,
    },
    {
        "name": "password",
        "label": "密码",
        "input_type": "password",
        "autocomplete": "current-password",
        "required": True,
    },
]

_USERNAME_SELECTORS = (
    '#login_username',
    '#loginName',
    '#username',
    '#userName',
    'input[name="login_username"]',
    'input[name="loginName"]',
    'input[name="username"]',
    'input[name="userName"]',
    'input[autocomplete="username"]',
    'input[type="text"]',
    'input:not([type])',
)

_PASSWORD_SELECTORS = (
    '#login_password1',
    '#login_password',
    '#password',
    '#pwd',
    'input[name="login_password1"]',
    'input[name="login_password"]',
    'input[name="password"]',
    'input[name="pwd"]',
    'input[type="password"]',
)

_SUBMIT_SELECTORS = (
    '#login_button',
    '#loginBtn',
    '#login',
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("登录")',
    'a:has-text("登录")',
)

_UNSUPPORTED_AUTH_SELECTORS = (
    'input[name*="captcha" i]',
    'input[id*="captcha" i]',
    'input[name*="verifyCode" i]',
    'input[id*="verifyCode" i]',
    'input[placeholder*="验证码"]',
)


def build_central_capability_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(
            name="oa.template.list",
            version="0.1.0",
            description="List templates available to the current OA user.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            output_schema={"type": "object"},
            effect="read",
            adapter="seeyon-central",
            workflow="template-list-v1",
        )
    )
    return registry


class SeeyonCentralAdapter:
    def __init__(self, *, base_url: str = SEEYON_OA_URL) -> None:
        self.base_url = base_url
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Seeyon base URL must include an http(s) origin")
        self.origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
        self.template_center_url = urljoin(base_url, TEMPLATE_CENTER_API_URL)

    def authentication_contract(self) -> dict:
        fingerprint_input = {
            "version": "seeyon-form-login-v1",
            "origin": self.origin,
            "fields": _AUTHENTICATION_FIELDS,
            "username_selectors": _USERNAME_SELECTORS,
            "password_selectors": _PASSWORD_SELECTORS,
            "submit_selectors": _SUBMIT_SELECTORS,
        }
        digest = hashlib.sha256(
            json.dumps(
                fingerprint_input,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:24]
        return {
            "system_id": "oa",
            "system_name": "致远 OA",
            "origin": self.origin,
            "page_fingerprint": f"seeyon-form-login-v1:{digest}",
            "fields": [dict(field) for field in _AUTHENTICATION_FIELDS],
        }

    def authenticate(
        self,
        worker,
        credentials: dict,
        *,
        timeout_seconds: float = 45,
    ) -> dict:
        if set(credentials) != {"username", "password"}:
            raise ValueError("Seeyon authentication requires username and password")
        username = credentials.get("username")
        password = credentials.get("password")
        if not isinstance(username, str) or not username or len(username) > 256:
            raise ValueError("Seeyon username is invalid")
        if not isinstance(password, str) or not password or len(password) > 1024:
            raise ValueError("Seeyon password is invalid")

        worker.clear_session_state()
        self.open_login(worker)
        page = worker.page
        contract_deadline = time.monotonic() + min(max(timeout_seconds, 1), 15)
        login_frame = username_locator = password_locator = None
        while time.monotonic() < contract_deadline:
            frames = list(getattr(page, "frames", []) or [page])
            if any(_has_visible(frame, _UNSUPPORTED_AUTH_SELECTORS) for frame in frames):
                raise SeeyonUnsupportedAuthMethod(
                    "The OA login page requires a verification challenge not supported by this card."
                )
            login_frame, username_locator, password_locator = _find_login_form(frames)
            if login_frame is not None:
                break
            time.sleep(0.1)
        if login_frame is None or username_locator is None or password_locator is None:
            raise SeeyonLoginContractMismatch(
                "The OA login page no longer matches the registered form contract."
            )
        username_locator.fill(username)
        password_locator.fill(password)
        submit_locator = _find_visible([login_frame], _SUBMIT_SELECTORS)
        if submit_locator is not None:
            submit_locator.click()
        else:
            password_locator.press("Enter")

        deadline = time.monotonic() + max(timeout_seconds, 1)
        while time.monotonic() < deadline:
            try:
                templates = self.list_templates(worker)
            except SeeyonLoginRequired:
                time.sleep(0.25)
                continue
            observed_principal = _principal_from_title(worker.page_title)
            if observed_principal:
                return {
                    "templates": templates,
                    "observed_principal_ref": observed_principal,
                    "page_url": worker.page_url,
                }
            time.sleep(0.25)
        raise SeeyonAuthenticationRejected(
            "The OA login was not accepted before the authentication challenge expired."
        )

    def list_templates(self, worker) -> dict:
        response = worker.request("GET", self.template_center_url)
        status = int(response.get("status") or 0)
        final_url = str(response.get("url") or "")
        payload = response.get("json")
        if status in {401, 403} or _looks_like_login_url(final_url) or not isinstance(payload, dict):
            raise SeeyonLoginRequired("The central OA session is not logged in or has expired.")
        result = parse_template_center_response(payload, base_url=self.base_url)
        return {
            **result,
            "transport": "central_http_session",
            "browser_bridge_used": False,
        }

    def open_login(self, worker) -> None:
        worker.goto(self.base_url)

    def wait_for_login(
        self,
        worker,
        *,
        timeout_seconds: float = 120,
        poll_interval: float = 1,
    ) -> dict:
        self.open_login(worker)
        deadline = time.monotonic() + max(timeout_seconds, 1)
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                templates = self.list_templates(worker)
            except SeeyonLoginRequired as exc:
                last_error = exc
                time.sleep(max(poll_interval, 0.1))
                continue
            return {
                "templates": templates,
                "observed_principal_ref": _principal_from_title(worker.page_title),
                "page_url": worker.page_url,
            }
        raise SeeyonLoginRequired(
            str(last_error) if last_error else "Timed out waiting for the central OA login."
        )


def _looks_like_login_url(url: str) -> bool:
    lowered = url.lower()
    return any(marker in lowered for marker in ("/login", "login.do", "method=login"))


def _principal_from_title(title: str) -> str | None:
    match = re.search(r",\s*([^,]+?)\s*,\s*您好", title or "")
    return match.group(1).strip() if match else None


def _find_visible(frames: list, selectors: tuple[str, ...]):
    for frame in frames:
        for selector in selectors:
            locator = frame.locator(selector)
            count = locator.count()
            for index in range(count):
                candidate = locator.nth(index)
                if candidate.is_visible():
                    return candidate
    return None


def _find_login_form(frames: list):
    for frame in frames:
        username_locator = _find_visible([frame], _USERNAME_SELECTORS)
        password_locator = _find_visible([frame], _PASSWORD_SELECTORS)
        if username_locator is not None and password_locator is not None:
            return frame, username_locator, password_locator
    return None, None, None


def _has_visible(frame, selectors: tuple[str, ...]) -> bool:
    return _find_visible([frame], selectors) is not None
