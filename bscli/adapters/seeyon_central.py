from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlparse

from bscli.adapters.seeyon import SEEYON_OA_URL
from bscli.adapters.seeyon_home import TEMPLATE_CENTER_API_URL, parse_template_center_response
from bscli.core.capability import CapabilityRegistry, CapabilitySpec


class SeeyonLoginRequired(RuntimeError):
    pass


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
