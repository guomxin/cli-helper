from __future__ import annotations

import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import re
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from bscli.adapters.seeyon_business_trip import (
    BUSINESS_TRIP_PREPARE_CAPABILITY,
    BUSINESS_TRIP_PREPARE_INPUT_SCHEMA,
    BUSINESS_TRIP_SAVE_CAPABILITY,
    BUSINESS_TRIP_SAVE_INPUT_SCHEMA,
)
from bscli.adapters.seeyon_business_trip_submit import (
    BUSINESS_TRIP_SUBMIT_CAPABILITY,
    BUSINESS_TRIP_SUBMIT_INPUT_SCHEMA,
    BUSINESS_TRIP_SUBMIT_PREPARE_CAPABILITY,
    BUSINESS_TRIP_SUBMIT_PREPARE_INPUT_SCHEMA,
)
from bscli.adapters.seeyon_leave import (
    LEAVE_PREPARE_CAPABILITY,
    LEAVE_PREPARE_INPUT_SCHEMA,
    LEAVE_SAVE_CAPABILITY,
    LEAVE_SAVE_INPUT_SCHEMA,
)
from bscli.adapters.seeyon_leave_submit import (
    LEAVE_SUBMIT_CAPABILITY,
    LEAVE_SUBMIT_INPUT_SCHEMA,
    LEAVE_SUBMIT_PREPARE_CAPABILITY,
    LEAVE_SUBMIT_PREPARE_INPUT_SCHEMA,
)
from bscli.adapters.seeyon_meeting import (
    MEETING_CREATE_CAPABILITY,
    MEETING_CREATE_INPUT_SCHEMA,
    MEETING_PREPARE_CAPABILITY,
    MEETING_PREPARE_INPUT_SCHEMA,
)
from bscli.adapters.seeyon_missed_punch import (
    MISSED_PUNCH_APPROVAL_PREPARE_CAPABILITY,
    MISSED_PUNCH_APPROVAL_PREPARE_INPUT_SCHEMA,
    MISSED_PUNCH_APPROVE_CAPABILITY,
    MISSED_PUNCH_APPROVE_INPUT_SCHEMA,
    MISSED_PUNCH_PREPARE_CAPABILITY,
    MISSED_PUNCH_PREPARE_INPUT_SCHEMA,
    MISSED_PUNCH_SAVE_CAPABILITY,
    MISSED_PUNCH_SAVE_INPUT_SCHEMA,
)
from bscli.adapters.seeyon_workflow_revoke import (
    WORKFLOW_REVOKE_CAPABILITY,
    WORKFLOW_REVOKE_INPUT_SCHEMA,
    WORKFLOW_REVOKE_PREPARE_CAPABILITY,
    WORKFLOW_REVOKE_PREPARE_INPUT_SCHEMA,
)
from bscli.adapters.seeyon_system import SEEYON_OA_URL
from bscli.adapters.seeyon_home import (
    TEMPLATE_CENTER_API_URL,
    extract_history_sections,
    parse_navigation_inventory,
    parse_oa_detail,
    parse_pending_projection,
    parse_sent_projection,
    parse_template_center_response,
)
from bscli.core.capability import CapabilityRegistry, CapabilitySpec


class SeeyonLoginRequired(RuntimeError):
    pass


class SeeyonAuthenticationRejected(RuntimeError):
    pass


class SeeyonLoginContractMismatch(RuntimeError):
    pass


class SeeyonUnsupportedAuthMethod(RuntimeError):
    pass


class SeeyonReadContractMismatch(RuntimeError):
    pass


class SeeyonSessionCheckUnavailable(RuntimeError):
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

_WORKFLOW_LIST_CAPABILITIES = {
    "oa.workflow.pending.list": "pending",
    "oa.workflow.done.list": "done",
    "oa.workflow.tracked.list": "tracked",
}

_WORKFLOW_COLLECTIONS = frozenset(_WORKFLOW_LIST_CAPABILITIES.values())
_INTERNAL_WORKFLOW_COLLECTIONS = frozenset((*_WORKFLOW_COLLECTIONS, "sent"))

_WORKFLOW_LIST_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "keyword": {"type": "string"},
        "limit": {"type": "integer"},
    },
    "additionalProperties": False,
}

_WORKFLOW_DETAIL_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "collection": {"type": "string"},
        "affair_id": {"type": "string"},
        "text_limit": {"type": "integer"},
    },
    "required": ["collection", "affair_id"],
    "additionalProperties": False,
}

_WORKFLOW_OPINIONS_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "collection": {"type": "string"},
        "affair_id": {"type": "string"},
        "limit": {"type": "integer"},
    },
    "required": ["collection", "affair_id"],
    "additionalProperties": False,
}


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
    registry.register(
        CapabilitySpec(
            name=BUSINESS_TRIP_PREPARE_CAPABILITY,
            version="0.3.0",
            description=(
                "Collect business-trip fields through a trusted card, validate the live "
                "OA form, and create a separate one-time confirmation card."
            ),
            input_schema=BUSINESS_TRIP_PREPARE_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="reversible_write",
            adapter="seeyon-central",
            workflow="business-trip-draft-prepare-v2",
        )
    )
    registry.register(
        CapabilitySpec(
            name=BUSINESS_TRIP_SAVE_CAPABILITY,
            version="0.1.0",
            description=(
                "Consume a trusted authorization once, save the frozen business-trip "
                "plan as an OA wait-send draft, and verify it by server readback."
            ),
            input_schema=BUSINESS_TRIP_SAVE_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="reversible_write",
            adapter="seeyon-central",
            workflow="business-trip-draft-save-v1",
        )
    )
    registry.register(
        CapabilitySpec(
            name=BUSINESS_TRIP_SUBMIT_PREPARE_CAPABILITY,
            version="0.2.0",
            description=(
                "Collect business-trip fields through a trusted card, validate the live "
                "OA form and sent-item baseline, and create a separate submit authorization."
            ),
            input_schema=BUSINESS_TRIP_SUBMIT_PREPARE_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="controlled_write",
            adapter="seeyon-central",
            workflow="business-trip-submit-prepare-v1",
        )
    )
    registry.register(
        CapabilitySpec(
            name=BUSINESS_TRIP_SUBMIT_CAPABILITY,
            version="0.1.0",
            description=(
                "Consume one trusted authorization, submit the frozen business-trip "
                "request, and verify one new readable item in the OA sent collection."
            ),
            input_schema=BUSINESS_TRIP_SUBMIT_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="controlled_write",
            adapter="seeyon-central",
            workflow="business-trip-submit-commit-v1",
        )
    )
    registry.register(
        CapabilitySpec(
            name=LEAVE_PREPARE_CAPABILITY,
            version="0.2.0",
            description=(
                "Collect supported leave-request fields through a trusted card, validate "
                "the live OA form, and create a separate draft-save authorization."
            ),
            input_schema=LEAVE_PREPARE_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="reversible_write",
            adapter="seeyon-central",
            workflow="leave-draft-prepare-v1",
        )
    )
    registry.register(
        CapabilitySpec(
            name=LEAVE_SAVE_CAPABILITY,
            version="0.1.0",
            description=(
                "Consume one trusted authorization, save the frozen leave request as an "
                "OA wait-send draft, and verify it by server readback without submission."
            ),
            input_schema=LEAVE_SAVE_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="reversible_write",
            adapter="seeyon-central",
            workflow="leave-draft-save-v1",
        )
    )
    registry.register(
        CapabilitySpec(
            name=LEAVE_SUBMIT_PREPARE_CAPABILITY,
            version="0.1.0",
            description=(
                "Collect supported leave-request fields through a trusted card, validate "
                "the live OA form and sent-item baseline, and create a submit authorization."
            ),
            input_schema=LEAVE_SUBMIT_PREPARE_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="controlled_write",
            adapter="seeyon-central",
            workflow="leave-submit-prepare-v1",
        )
    )
    registry.register(
        CapabilitySpec(
            name=LEAVE_SUBMIT_CAPABILITY,
            version="0.1.0",
            description=(
                "Consume one trusted authorization, submit the frozen leave request, "
                "and verify one new readable item in the OA sent collection."
            ),
            input_schema=LEAVE_SUBMIT_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="controlled_write",
            adapter="seeyon-central",
            workflow="leave-submit-commit-v1",
        )
    )
    for spec in (
        CapabilitySpec(
            name=MISSED_PUNCH_PREPARE_CAPABILITY,
            version="0.2.0",
            description=(
                "Collect missed-punch fields in a trusted card, validate the live OA "
                "form, and create a separate draft-save authorization."
            ),
            input_schema=MISSED_PUNCH_PREPARE_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="reversible_write",
            adapter="seeyon-central",
            workflow="missed-punch-draft-prepare-v1",
        ),
        CapabilitySpec(
            name=MISSED_PUNCH_SAVE_CAPABILITY,
            version="0.1.0",
            description=(
                "Consume one trusted authorization, save the frozen missed-punch plan "
                "as an OA wait-send draft, and verify it without submitting approval."
            ),
            input_schema=MISSED_PUNCH_SAVE_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="reversible_write",
            adapter="seeyon-central",
            workflow="missed-punch-draft-save-v1",
        ),
        CapabilitySpec(
            name=MISSED_PUNCH_APPROVAL_PREPARE_CAPABILITY,
            version="0.2.0",
            description=(
                "Collect an approval opinion in a trusted card, validate one exact "
                "pending missed-punch item, and create a separate approval authorization."
            ),
            input_schema=MISSED_PUNCH_APPROVAL_PREPARE_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="controlled_write",
            adapter="seeyon-central",
            workflow="missed-punch-approval-prepare-v1",
        ),
        CapabilitySpec(
            name=MISSED_PUNCH_APPROVE_CAPABILITY,
            version="0.1.0",
            description=(
                "Consume one trusted authorization, approve the frozen missed-punch "
                "target, and verify that it left the pending collection."
            ),
            input_schema=MISSED_PUNCH_APPROVE_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="controlled_write",
            adapter="seeyon-central",
            workflow="missed-punch-approval-commit-v1",
        ),
        CapabilitySpec(
            name=MEETING_PREPARE_CAPABILITY,
            version="0.1.0",
            description=(
                "Collect meeting fields in a trusted card, resolve and validate room "
                "availability, and create a separate meeting-create authorization."
            ),
            input_schema=MEETING_PREPARE_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="controlled_write",
            adapter="seeyon-central",
            workflow="meeting-create-prepare-v1",
        ),
        CapabilitySpec(
            name=MEETING_CREATE_CAPABILITY,
            version="0.1.0",
            description=(
                "Consume one trusted authorization, recheck room availability, create "
                "and send the meeting, then verify room-list and meeting-view readback."
            ),
            input_schema=MEETING_CREATE_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="controlled_write",
            adapter="seeyon-central",
            workflow="meeting-create-commit-v1",
        ),
    ):
        registry.register(spec)
    registry.register(
        CapabilitySpec(
            name=WORKFLOW_REVOKE_PREPARE_CAPABILITY,
            version="0.1.0",
            description=(
                "Collect a revoke comment in a trusted card, resolve one exact active "
                "sent workflow, run non-destructive OA eligibility checks, and create "
                "a separate revoke authorization."
            ),
            input_schema=WORKFLOW_REVOKE_PREPARE_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="controlled_write",
            adapter="seeyon-central",
            workflow="workflow-revoke-prepare-v1",
        )
    )
    registry.register(
        CapabilitySpec(
            name=WORKFLOW_REVOKE_CAPABILITY,
            version="0.1.0",
            description=(
                "Consume one trusted authorization, revoke the frozen sent workflow "
                "through OA's native action, and verify its revoked wait-send state."
            ),
            input_schema=WORKFLOW_REVOKE_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="controlled_write",
            adapter="seeyon-central",
            workflow="workflow-revoke-commit-v1",
        )
    )
    for capability_name, collection in _WORKFLOW_LIST_CAPABILITIES.items():
        registry.register(
            CapabilitySpec(
                name=capability_name,
                version="0.1.0",
                description=f"List {collection} workflows for the current OA user.",
                input_schema=_WORKFLOW_LIST_INPUT_SCHEMA,
                output_schema={"type": "object"},
                effect="read",
                adapter="seeyon-central",
                workflow="workflow-list-v1",
            )
        )
    registry.register(
        CapabilitySpec(
            name="oa.workflow.detail.get",
            version="0.1.0",
            description="Get a rendered OA workflow detail by opaque affair ID.",
            input_schema=_WORKFLOW_DETAIL_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="read",
            adapter="seeyon-central",
            workflow="workflow-detail-v1",
        )
    )
    registry.register(
        CapabilitySpec(
            name="oa.workflow.opinions.list",
            version="0.1.0",
            description="List the rendered opinions for an OA workflow.",
            input_schema=_WORKFLOW_OPINIONS_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="read",
            adapter="seeyon-central",
            workflow="workflow-opinions-v1",
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
            except (SeeyonLoginRequired, SeeyonSessionCheckUnavailable):
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
        try:
            response = worker.request("GET", self.template_center_url)
        except Exception as exc:
            raise SeeyonSessionCheckUnavailable(
                f"OA session check request failed ({_safe_exception_code(exc)})."
            ) from exc
        status = int(response.get("status") or 0)
        final_url = str(response.get("url") or "")
        payload = response.get("json")
        text = str(response.get("text") or "")
        if status in {301, 302, 303, 307, 308, 401, 403} or _looks_like_login_url(
            final_url
        ):
            raise SeeyonLoginRequired("The central OA session is not logged in or has expired.")
        if status < 200 or status >= 300:
            diagnostics = _safe_response_diagnostics(response)
            if status in {408, 425, 429} or status >= 500:
                raise SeeyonSessionCheckUnavailable(
                    f"OA session check received a temporary response ({diagnostics})."
                )
            raise SeeyonReadContractMismatch(
                f"The OA template center returned an unexpected response ({diagnostics})."
            )
        if not isinstance(payload, dict):
            if _looks_like_login_html(text):
                raise SeeyonLoginRequired(
                    "The central OA session is not logged in or has expired."
                )
            raise SeeyonSessionCheckUnavailable(
                "OA session check did not return JSON "
                f"({_safe_response_diagnostics(response)})."
            )
        result = parse_template_center_response(payload, base_url=self.base_url)
        return {
            **result,
            "transport": "central_http_session",
            "browser_bridge_used": False,
        }

    def probe_session(self, worker) -> dict:
        templates = self.list_templates(worker)
        return {
            "authenticated": True,
            "template_count": int(templates.get("count") or 0),
            "transport": templates["transport"],
            "browser_bridge_used": False,
        }

    def invoke_capability(self, capability_name: str, worker, arguments: dict) -> dict:
        if capability_name == "oa.template.list":
            if arguments:
                raise ValueError("oa.template.list does not accept arguments")
            return self.list_templates(worker)
        collection = _WORKFLOW_LIST_CAPABILITIES.get(capability_name)
        if collection:
            return self.list_workflows(worker, collection=collection, arguments=arguments)
        if capability_name == "oa.workflow.detail.get":
            return self.get_workflow_detail(worker, arguments=arguments)
        if capability_name == "oa.workflow.opinions.list":
            return self.list_workflow_opinions(worker, arguments=arguments)
        raise KeyError(f"unsupported Seeyon central capability: {capability_name}")

    def list_workflows(self, worker, *, collection: str, arguments: dict | None = None) -> dict:
        collection = _validated_internal_collection(collection)
        arguments = arguments or {}
        keyword = _validated_optional_string(arguments.get("keyword"), "keyword", maximum=200)
        limit = _validated_integer(arguments.get("limit"), "limit", default=50, minimum=1, maximum=100)
        parsed = self._fetch_workflow_collection(worker, collection)
        public_items = [_public_workflow_item(item, collection) for item in parsed.get("items") or []]
        public_items = [item for item in public_items if item.get("title")]
        source_count = len(public_items)
        if keyword:
            needle = keyword.casefold()
            public_items = [
                item
                for item in public_items
                if needle in " ".join(str(value) for value in item.values()).casefold()
            ]
        matched_count = len(public_items)
        public_items = public_items[:limit]
        return {
            "schema_version": "bscli.oa_workflow_list.v1",
            "collection": collection,
            "source": "section_api",
            "source_count": source_count,
            "matched_count": matched_count,
            "count": len(public_items),
            "total": parsed.get("total"),
            "page": parsed.get("page"),
            "items": public_items,
            "transport": "central_http_session",
            "browser_bridge_used": False,
        }

    def get_workflow_detail(self, worker, *, arguments: dict) -> dict:
        collection = _validated_collection(arguments.get("collection"))
        affair_id = _validated_identifier(arguments.get("affair_id"), "affair_id")
        text_limit = _validated_integer(
            arguments.get("text_limit"),
            "text_limit",
            default=6000,
            minimum=0,
            maximum=20000,
        )
        source_item, parsed_detail = self._render_workflow_detail(
            worker,
            collection=collection,
            affair_id=affair_id,
        )
        opinions = _public_opinions(parsed_detail.get("workflow"))
        attachments = [
            {"name": _public_text(item.get("name"))}
            for item in parsed_detail.get("attachments") or []
            if isinstance(item, dict) and item.get("name")
        ]
        fields = [
            {
                "name": _public_text(item.get("name")),
                "value": _public_text(item.get("value")),
            }
            for item in parsed_detail.get("fields") or []
            if isinstance(item, dict) and item.get("name")
        ]
        return {
            "schema_version": "bscli.oa_workflow_detail.v1",
            "collection": collection,
            "source_item": _public_workflow_item(source_item, collection),
            "detail": {
                "title": _public_text(source_item.get("title") or parsed_detail.get("title")),
                "text": str(parsed_detail.get("text") or "")[:text_limit],
                "fields": fields,
                "field_count": len(fields),
                "attachments": attachments,
                "attachment_count": len(attachments),
                "opinions": opinions,
                "opinion_count": len(opinions),
            },
            "transport": "central_browser_session",
            "browser_bridge_used": False,
        }

    def list_workflow_opinions(self, worker, *, arguments: dict) -> dict:
        collection = _validated_collection(arguments.get("collection"))
        affair_id = _validated_identifier(arguments.get("affair_id"), "affair_id")
        limit = _validated_integer(arguments.get("limit"), "limit", default=100, minimum=1, maximum=100)
        source_item, parsed_detail = self._render_workflow_detail(
            worker,
            collection=collection,
            affair_id=affair_id,
        )
        opinions = _public_opinions(parsed_detail.get("workflow"))[:limit]
        return {
            "schema_version": "bscli.oa_workflow_opinions.v1",
            "collection": collection,
            "source_item": _public_workflow_item(source_item, collection),
            "count": len(opinions),
            "items": opinions,
            "transport": "central_browser_session",
            "browser_bridge_used": False,
        }

    def resolve_workflow_detail(
        self,
        worker,
        *,
        collection: str,
        affair_id: str,
    ) -> tuple[dict, dict]:
        """Resolve one workflow for a process adapter without exposing its URL."""
        return self._render_workflow_detail(
            worker,
            collection=_validated_internal_collection(collection),
            affair_id=_validated_identifier(affair_id, "affair_id"),
        )

    def _fetch_workflow_collection(self, worker, collection: str) -> dict:
        self.list_templates(worker)
        section_url = self._discover_section_url(worker, collection)
        response = worker.request("GET", section_url)
        status = int(response.get("status") or 0)
        final_url = str(response.get("url") or "")
        payload = response.get("json")
        if status in {301, 302, 303, 307, 308, 401, 403} or _looks_like_login_url(final_url):
            raise SeeyonLoginRequired("The central OA session expired while reading workflows.")
        if status < 200 or status >= 300:
            raise SeeyonReadContractMismatch(f"The OA workflow section returned HTTP {status}.")
        if not isinstance(payload, dict):
            raise SeeyonReadContractMismatch("The OA workflow section did not return JSON.")
        if not isinstance(payload.get("Data"), dict):
            raise SeeyonReadContractMismatch("The OA workflow section JSON is missing Data.")
        parser = parse_pending_projection if collection == "pending" else parse_sent_projection
        parsed = parser(payload, base_url=final_url or self.base_url)
        if parsed.get("error"):
            raise SeeyonReadContractMismatch(str(parsed["error"]))
        return parsed

    def _discover_section_url(
        self,
        worker,
        collection: str,
        *,
        timeout_seconds: float = 10,
    ) -> str:
        worker.goto(self.base_url)
        deadline = time.monotonic() + max(timeout_seconds, 0.1)
        while time.monotonic() < deadline:
            if _looks_like_login_url(worker.page_url):
                raise SeeyonLoginRequired("The central OA session expired while opening the home page.")
            resource_urls = worker.resource_urls()
            if collection == "pending":
                section_url = _find_section_resource_url(resource_urls, "pendingSection")
                if section_url:
                    return _section_url_with_arguments(
                        section_url,
                        {"sectionBeanId": "pendingSection"},
                    )
            else:
                section_url = _find_section_resource_url(resource_urls, "sentSection")
                if section_url:
                    inventory = parse_navigation_inventory(worker.page.content(), base_url=self.base_url)
                    history = extract_history_sections(inventory)
                    history_item = next(
                        (
                            item
                            for item in history.get("items") or []
                            if item.get("kind") == collection
                        ),
                        None,
                    )
                    if history_item:
                        return _section_url_with_arguments(
                            section_url,
                            {
                                "sectionBeanId": "sentSection",
                                "entityId": history_item.get("section_id"),
                                "panelId": history_item.get("tab_id"),
                            },
                        )
            time.sleep(0.25)
        raise SeeyonReadContractMismatch(
            f"The OA home page did not expose the {collection} section contract in time."
        )

    def _render_workflow_detail(self, worker, *, collection: str, affair_id: str) -> tuple[dict, dict]:
        parsed = self._fetch_workflow_collection(worker, collection)
        source_item = next(
            (
                item
                for item in parsed.get("items") or []
                if str(item.get("affair_id") or "") == affair_id
            ),
            None,
        )
        if source_item is None:
            raise SeeyonReadContractMismatch(
                f"Workflow affair_id was not found in the current {collection} collection."
            )
        detail_url = str(source_item.get("href") or "")
        if not detail_url:
            raise SeeyonReadContractMismatch("The selected workflow does not expose a detail page.")
        snapshot = worker.rendered_snapshot(detail_url, settle_ms=1800, include_frames=True)
        final_url = str(snapshot.get("url") or detail_url)
        if _looks_like_login_url(final_url):
            raise SeeyonLoginRequired("The central OA session expired while rendering workflow detail.")
        html_parts = [str(snapshot.get("html") or "")]
        html_parts.extend(
            str(frame.get("html") or "")
            for frame in snapshot.get("frames") or []
            if isinstance(frame, dict)
        )
        parsed_detail = parse_oa_detail("\n".join(html_parts), base_url=final_url)
        return source_item, parsed_detail

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
            except (SeeyonLoginRequired, SeeyonSessionCheckUnavailable) as exc:
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


def _find_section_resource_url(resource_urls: list[str], section_bean_id: str) -> str:
    for url in resource_urls:
        parsed = urlparse(str(url or ""))
        query = parse_qs(parsed.query, keep_blank_values=True)
        if query.get("managerName", [""])[0] != "sectionManager":
            continue
        if query.get("managerMethod", [""])[0] != "doProjection":
            continue
        arguments = _section_arguments(url)
        if arguments.get("sectionBeanId") == section_bean_id:
            return url
    return ""


def _section_arguments(url: str) -> dict:
    query = parse_qs(urlparse(str(url or "")).query, keep_blank_values=True)
    raw_arguments = query.get("arguments", ["{}"])[0] or "{}"
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return {}
    return arguments if isinstance(arguments, dict) else {}


def _section_url_with_arguments(url: str, updates: dict) -> str:
    parsed = urlparse(str(url or ""))
    query = parse_qs(parsed.query, keep_blank_values=True)
    arguments = _section_arguments(url)
    for key, value in updates.items():
        if value not in (None, ""):
            arguments[key] = str(value)
    query["arguments"] = [json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))]
    return parsed._replace(query=urlencode(query, doseq=True)).geturl()


def _validated_collection(value) -> str:
    if not isinstance(value, str) or value not in _WORKFLOW_COLLECTIONS:
        choices = ", ".join(sorted(_WORKFLOW_COLLECTIONS))
        raise ValueError(f"collection must be one of: {choices}")
    return value


def _validated_internal_collection(value) -> str:
    if not isinstance(value, str) or value not in _INTERNAL_WORKFLOW_COLLECTIONS:
        choices = ", ".join(sorted(_INTERNAL_WORKFLOW_COLLECTIONS))
        raise ValueError(f"internal collection must be one of: {choices}")
    return value


def _validated_identifier(value, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError(f"{name} must be a non-empty string of at most 256 characters")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{name} must not contain control characters")
    return value


def _validated_optional_string(value, name: str, *, maximum: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if len(value) > maximum:
        raise ValueError(f"{name} must be at most {maximum} characters")
    return value.strip()


def _validated_integer(value, name: str, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _public_workflow_item(item: dict, collection: str) -> dict:
    public = {
        "affair_id": str(item.get("affair_id") or ""),
        "title": _public_text(item.get("title")),
    }
    if collection == "pending":
        public.update(
            {
                "sender": _public_text(item.get("sender")),
                "date": _public_text(item.get("date")),
                "category": _public_text(item.get("category")),
                "read": bool(item.get("read")),
            }
        )
    else:
        public.update(
            {
                "status": _public_text(item.get("status")),
                "date": _public_text(item.get("date")),
                "category": _public_text(item.get("category")),
            }
        )
    return public


def _public_opinions(value) -> list[dict]:
    opinions = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        public = {
            key: _public_text(item.get(key))
            for key in ("text", "handler", "opinion", "time")
            if item.get(key) not in (None, "")
        }
        if public:
            opinions.append(public)
    return opinions


def _public_text(value) -> str:
    text = re.sub(r"&nbsp;?", " ", str(value or ""), flags=re.IGNORECASE)
    text = unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _looks_like_login_url(url: str) -> bool:
    lowered = url.lower()
    return any(marker in lowered for marker in ("/login", "login.do", "method=login"))


def _looks_like_login_html(value: str) -> bool:
    detector = _LoginFormDetector()
    try:
        detector.feed(str(value or "")[:262_144])
    except Exception:
        return False
    return detector.has_username and detector.has_password


def _safe_response_diagnostics(response: dict) -> str:
    status = int(response.get("status") or 0)
    media_type = str(response.get("content_type") or "unknown")
    media_type = media_type.split(";", 1)[0].strip().lower()
    media_type = re.sub(r"[^a-z0-9.+/-]", "_", media_type)[:80] or "unknown"
    try:
        elapsed_ms = max(0, int(response.get("elapsed_ms") or 0))
    except (TypeError, ValueError):
        elapsed_ms = 0
    return f"HTTP {status}, content_type={media_type}, elapsed_ms={elapsed_ms}"


def _safe_exception_code(exc: Exception) -> str:
    value = re.sub(r"[^A-Z0-9_.-]", "_", exc.__class__.__name__.upper())[:80]
    return value or "REQUEST_ERROR"


class _LoginFormDetector(HTMLParser):
    _USERNAME_NAMES = {"login_username", "loginname", "username"}
    _PASSWORD_NAMES = {"login_password1", "login_password", "password", "pwd"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_username = False
        self.has_password = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "input":
            return
        values = {
            str(name or "").lower(): str(value or "").strip()
            for name, value in attrs
        }
        input_type = values.get("type", "").lower()
        field_name = (values.get("name") or values.get("id") or "").lower()
        autocomplete = values.get("autocomplete", "").lower()
        if input_type == "password" or field_name in self._PASSWORD_NAMES:
            self.has_password = True
        if (
            field_name in self._USERNAME_NAMES
            or autocomplete == "username"
            or input_type == "text"
        ):
            self.has_username = True


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
