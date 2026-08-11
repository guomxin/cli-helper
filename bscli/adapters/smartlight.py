from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from typing import Any
from urllib.parse import quote, urlencode, urljoin, urlparse

from bscli.adapters.base import (
    AdapterAuthenticationRejected,
    AdapterLoginContractMismatch,
    AdapterLoginRequired,
    AdapterSessionCheckUnavailable,
)
from bscli.core.capability import CapabilityRegistry, CapabilitySpec


SMARTLIGHT_SYSTEM_ID = "smartlight"
SMARTLIGHT_ADAPTER_ID = "smartlight-central"
SMARTLIGHT_SYSTEM_NAME = "照明实验室测试系统"

SMARTLIGHT_OVERVIEW_CAPABILITY = "smartlight.system.overview"
SMARTLIGHT_LAMPPOST_LIST_CAPABILITY = "smartlight.lamppost.list"
SMARTLIGHT_ALARM_LIST_CAPABILITY = "smartlight.alarm.list"
SMARTLIGHT_INSPECTION_TASK_LIST_CAPABILITY = "smartlight.inspection_task.list"
SMARTLIGHT_LEAKAGE_SUMMARY_CAPABILITY = "smartlight.leakage.summary"

_FORM_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}

_CAS_HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


class SmartlightLoginRequired(AdapterLoginRequired):
    pass


class SmartlightAuthenticationRejected(AdapterAuthenticationRejected):
    def __init__(
        self,
        message: str,
        *,
        error_code: str = "AUTHENTICATION_REJECTED",
    ) -> None:
        super().__init__(message)
        self.error_code = error_code


class SmartlightLoginContractMismatch(AdapterLoginContractMismatch):
    pass


class SmartlightSessionCheckUnavailable(AdapterSessionCheckUnavailable):
    pass


class SmartlightCentralAdapter:
    def __init__(self, *, base_url: str, allow_insecure_http: bool = False) -> None:
        parsed = urlparse(str(base_url or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Smartlight base URL must use http(s)")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Smartlight base URL is invalid")
        if parsed.path.rstrip("/") != "/smartlight":
            raise ValueError("Smartlight base URL must end with /smartlight")
        if parsed.scheme == "http" and not allow_insecure_http:
            raise ValueError(
                "Smartlight plain HTTP requires explicit allow_insecure_http opt-in"
            )
        self.origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
        self.base_url = f"{self.origin}/smartlight/"
        self.allowed_origins = {self.origin}

    def authentication_contract(self) -> dict:
        entry_url = f"{self.origin}/smartlight/"
        return {
            "system_id": SMARTLIGHT_SYSTEM_ID,
            "system_name": SMARTLIGHT_SYSTEM_NAME,
            "origin": self.origin,
            "page_fingerprint": "smartlight-cas-captcha-v1",
            "fields": [
                {
                    "name": "username",
                    "label": "系统账号",
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
                {
                    "name": "authcode",
                    "label": "验证码",
                    "input_type": "text",
                    "autocomplete": "off",
                    "required": True,
                },
            ],
            "prepared_authentication": {
                "kind": "image_captcha",
                "entry_url": entry_url,
            },
        }

    def prepare_authentication(self, worker, *, timeout_seconds: float) -> dict:
        response = worker.request_bytes(
            "GET",
            self.base_url,
            headers=_CAS_HTML_HEADERS,
            timeout_seconds=timeout_seconds,
        )
        response = self._follow_login_redirects(
            worker,
            response,
            timeout_seconds=timeout_seconds,
        )
        if response["status"] != 200:
            raise SmartlightSessionCheckUnavailable(
                "Smartlight CAS login page is temporarily unavailable."
            )
        parser = _CasLoginParser()
        parser.feed(response["body"].decode("utf-8", errors="replace"))
        parser.close()
        if not parser.form_action or not parser.captcha_src:
            raise SmartlightLoginContractMismatch(
                "CAS login page no longer exposes the registered captcha form."
            )
        required_hidden = {"lt", "execution", "_eventId"}
        if not required_hidden.issubset(parser.hidden_fields):
            raise SmartlightLoginContractMismatch(
                "CAS login page is missing required hidden fields."
            )
        page_url = str(response.get("url") or self.base_url)
        form_url = _same_origin_url(
            self.origin,
            urljoin(page_url, parser.form_action),
        )
        captcha_url = _same_origin_url(
            self.origin,
            urljoin(page_url, parser.captcha_src),
        )
        separator = "&" if "?" in captcha_url else "?"
        captcha = worker.request_bytes(
            "GET",
            f"{captcha_url}{separator}agentbridge={quote(str(datetime.now(timezone.utc).timestamp()))}",
            headers={
                **_CAS_HTML_HEADERS,
                "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*",
                "Referer": page_url,
            },
            timeout_seconds=timeout_seconds,
        )
        content_type = str(captcha.get("content_type") or "").split(";", 1)[0].lower()
        body = captcha.get("body")
        if captcha.get("status") != 200 or content_type not in {
            "image/jpeg",
            "image/png",
            "image/gif",
        } or not isinstance(body, bytes) or not body:
            raise SmartlightSessionCheckUnavailable(
                "Smartlight CAPTCHA image is temporarily unavailable."
            )
        worker.set_http_state(
            {
                "smartlight_prepared_login": {
                    "form_url": form_url,
                    "login_page_url": page_url,
                    "hidden_fields": {
                        name: parser.hidden_fields[name]
                        for name in sorted(required_hidden)
                    },
                    "captcha_content_type": content_type,
                    "captcha_body_base64": base64.b64encode(body).decode("ascii"),
                }
            }
        )
        return {
            "captcha": {"content_type": content_type, "body": body},
        }

    def recover_prepared_authentication(self, worker) -> dict | None:
        state = worker.get_http_state()
        prepared = state.get("smartlight_prepared_login")
        if not isinstance(prepared, dict):
            return None
        content_type = str(prepared.get("captcha_content_type") or "").lower()
        encoded = str(prepared.get("captcha_body_base64") or "")
        if content_type not in {"image/jpeg", "image/png", "image/gif"} or not encoded:
            return None
        try:
            body = base64.b64decode(encoded, validate=True)
        except ValueError:
            return None
        if not body or len(body) > 256 * 1024:
            return None
        return {"captcha": {"content_type": content_type, "body": body}}

    def authenticate(
        self,
        worker,
        credentials: dict,
        *,
        timeout_seconds: float,
    ) -> dict:
        state = worker.get_http_state()
        prepared = state.get("smartlight_prepared_login")
        if not isinstance(prepared, dict):
            raise SmartlightLoginContractMismatch(
                "Prepared CAS login state is unavailable."
            )
        form_url = _same_origin_url(self.origin, prepared.get("form_url"))
        login_page_url = _same_origin_url(
            self.origin,
            prepared.get("login_page_url"),
        )
        hidden = prepared.get("hidden_fields")
        if not isinstance(hidden, dict) or set(hidden) != {"lt", "execution", "_eventId"}:
            raise SmartlightLoginContractMismatch(
                "Prepared CAS login fields are invalid."
            )
        username = str(credentials.get("username") or "").strip()
        password = str(credentials.get("password") or "")
        authcode = str(credentials.get("authcode") or "").strip()
        if not username or not password or not authcode:
            raise ValueError("username, password, and authcode are required")

        # The legacy CAS page applies these transformations in JavaScript.
        payload = {
            **hidden,
            "username": base64.b64encode(username.encode("utf-8")).decode("ascii"),
            "password": hashlib.md5(password.encode("utf-8")).hexdigest(),  # noqa: S324
            "authcode": authcode,
            "submit": "登录",
        }
        response = worker.request_bytes(
            "POST",
            form_url,
            headers={
                **_CAS_HTML_HEADERS,
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": self.origin,
                "Referer": login_page_url,
            },
            body=urlencode(payload),
            timeout_seconds=timeout_seconds,
        )
        response = self._follow_login_redirects(
            worker,
            response,
            timeout_seconds=timeout_seconds,
        )
        if response["status"] != 200:
            raise SmartlightAuthenticationRejected(
                "The system rejected the authentication request."
            )
        if "/cas/login" in response["url"]:
            parser = _CasLoginParser()
            parser.feed(response["body"].decode("utf-8", errors="replace"))
            parser.close()
            error_code = _login_rejection_code(parser.error_message)
            raise SmartlightAuthenticationRejected(
                "The system rejected the account, password, or verification code.",
                error_code=error_code,
            )
        probe = self.probe_session(worker)
        return {
            "observed_principal_ref": probe["observed_principal_ref"],
            "principal": probe["principal"],
            "transport": "central_cas_cookie_jwt",
        }

    def probe_session(self, worker) -> dict:
        principal = self._cas_principal(worker)
        token_state = self._exchange_token(worker, principal)
        worker.set_http_state(token_state)
        return {
            "authenticated": True,
            "observed_principal_ref": token_state["principal"]["name"],
            "principal": token_state["principal"],
            "template_count": None,
            "transport": "central_cas_cookie_jwt",
        }

    def invoke_capability(self, capability_name: str, worker, arguments: dict) -> dict:
        if capability_name == SMARTLIGHT_OVERVIEW_CAPABILITY:
            return self.system_overview(worker)
        if capability_name == SMARTLIGHT_LAMPPOST_LIST_CAPABILITY:
            return self.list_lampposts(worker, arguments)
        if capability_name == SMARTLIGHT_ALARM_LIST_CAPABILITY:
            return self.list_alarms(worker, arguments)
        if capability_name == SMARTLIGHT_INSPECTION_TASK_LIST_CAPABILITY:
            return self.list_inspection_tasks(worker, arguments)
        if capability_name == SMARTLIGHT_LEAKAGE_SUMMARY_CAPABILITY:
            return self.leakage_summary(worker, arguments)
        raise KeyError(f"unsupported Smartlight capability: {capability_name}")

    def system_overview(self, worker) -> dict:
        context = self._principal_context(worker)
        cabinets = self._authorized_post_json(
            worker,
            "/map/getIntegratedRControlCabinetDataByCondition",
            {
                "findFlag": "true",
                "json": _json_text(
                    {
                        "_like_controlCabinetName": "",
                        "organroleId": context["organroleId"],
                    }
                ),
            },
        )
        lampposts = self._authorized_post_json(
            worker,
            "/lLamppost/getLampDetialList",
            {
                "json": _json_text({"lampTypeIds": [], "streetIds": []}),
                "pageNum": 1,
                "pageSize": 1,
                "organroleId": context["organroleId"],
            },
        )
        cabinet_items = cabinets if isinstance(cabinets, list) else []
        lamp_total = _page_total(lampposts)
        return {
            "principal": context,
            "cabinetTotal": len(cabinet_items),
            "lampPostTotal": lamp_total,
            "cabinetStates": _count_values(
                cabinet_items,
                ("onlineState", "state", "rtuState", "alarmState"),
            ),
            "sampleCabinets": [
                _normalize_cabinet(item)
                for item in cabinet_items[:10]
                if isinstance(item, dict)
            ],
        }

    def list_lampposts(self, worker, arguments: dict) -> dict:
        context = self._principal_context(worker)
        page = _bounded_int(arguments.get("page"), default=1, minimum=1, maximum=10000)
        size = _bounded_int(arguments.get("size"), default=20, minimum=1, maximum=100)
        keyword = str(arguments.get("keyword") or "").strip()
        payload = self._authorized_post_json(
            worker,
            "/lLamppost/getDataByConditionForFacilityEx",
            {
                "json": _json_text(
                    {
                        "labels": [],
                        "_like_params": keyword,
                        "_like_cabinet": "",
                        "_like_imei": "",
                        "_include_streetId": [],
                        "_include_controlCabinetId": [],
                        "_include_lampPostTypeId": None,
                        "_include_materialTypeId": [],
                        "_include_workAreaId": [],
                        "_include_streetSideId": [],
                        "_timebegin_addDate": "",
                        "_timeend_addDate": "",
                        "oddEvenNumber": None,
                        "userId": context["userId"],
                    }
                ),
                "orderBy": "lamp_post_code",
                "pageNum": page,
                "pageSize": size,
                "organroleId": context["organroleId"],
            },
        )
        items = _page_items(payload)
        return {
            "keyword": keyword or None,
            "page": page,
            "size": size,
            "total": _page_total(payload),
            "count": len(items),
            "items": [_normalize_lamppost(item) for item in items],
        }

    def list_alarms(self, worker, arguments: dict) -> dict:
        context = self._principal_context(worker)
        page = _bounded_int(arguments.get("page"), default=1, minimum=1, maximum=10000)
        size = _bounded_int(arguments.get("size"), default=20, minimum=1, maximum=100)
        keyword = str(arguments.get("keyword") or "").strip()
        payload = self._authorized_post_json(
            worker,
            "/rHisHitchAlarm/getDataByRtuAlarm",
            {
                "json": _json_text(
                    {
                        "_like_params": keyword,
                        "organroleId": context["organroleId"],
                    }
                ),
                "pageNum": page,
                "pageSize": size,
                "organroleId": context["organroleId"],
            },
        )
        page_payload = payload.get("RtuHisHitchAlarm") if isinstance(payload, dict) else payload
        items = _page_items(page_payload)
        return {
            "keyword": keyword or None,
            "page": page,
            "size": size,
            "total": _page_total(page_payload),
            "count": len(items),
            "summary": _selected_fields(
                payload,
                ("todayAlarm", "untreated", "yesterdayAlarm"),
            ),
            "items": [_normalize_alarm(item) for item in items],
        }

    def list_inspection_tasks(self, worker, arguments: dict) -> dict:
        context = self._principal_context(worker)
        page = _bounded_int(arguments.get("page"), default=1, minimum=1, maximum=10000)
        size = _bounded_int(arguments.get("size"), default=20, minimum=1, maximum=100)
        task_name = str(arguments.get("task_name") or "").strip()
        plan_name = str(arguments.get("plan_name") or "").strip()
        state = arguments.get("state")
        payload = self._authorized_post_json(
            worker,
            "/inspectionTask/getDataByCondition",
            {
                "json": _json_text(
                    {
                        "_taskName": task_name,
                        "_planName": plan_name,
                        "_taskState": state,
                    }
                ),
                "pageNum": page,
                "pageSize": size,
                "organroleId": context["organroleId"],
            },
        )
        items = _page_items(payload)
        return {
            "filters": {
                "taskName": task_name or None,
                "planName": plan_name or None,
                "state": state,
            },
            "page": page,
            "size": size,
            "total": _page_total(payload),
            "count": len(items),
            "items": [_normalize_inspection_task(item) for item in items],
        }

    def leakage_summary(self, worker, arguments: dict) -> dict:
        context = self._principal_context(worker)
        page = _bounded_int(arguments.get("page"), default=1, minimum=1, maximum=10000)
        size = _bounded_int(arguments.get("size"), default=20, minimum=1, maximum=100)
        query = {
            "_timebegin_alarmTime": str(arguments.get("start_date") or "").strip(),
            "_timeend_alarmTime": str(arguments.get("end_date") or "").strip(),
            "organroleId": context["organroleId"],
        }
        records = self._authorized_post_json(
            worker,
            "/lHisCoplog/getDataByLampElectricLeakage",
            {
                "json": _json_text(query),
                "pageNum": page,
                "pageSize": size,
                "organroleId": context["organroleId"],
            },
        )
        counts = self._authorized_post_json(
            worker,
            "/lHisCoplog/queryLampElectricLeakageCount",
            {
                "json": _json_text(query),
                "organroleId": context["organroleId"],
            },
        )
        items = _page_items(records)
        return {
            "startDate": query["_timebegin_alarmTime"] or None,
            "endDate": query["_timeend_alarmTime"] or None,
            "page": page,
            "size": size,
            "total": _page_total(records),
            "count": len(items),
            "summary": _bounded_json(counts),
            "items": [_normalize_leakage(item) for item in items],
        }

    def _follow_login_redirects(
        self,
        worker,
        response: dict,
        *,
        timeout_seconds: float,
    ) -> dict:
        for _ in range(5):
            if response["status"] not in {301, 302, 303, 307, 308}:
                return response
            location = response.get("location")
            if not location:
                raise SmartlightLoginContractMismatch(
                    "CAS redirect response has no destination."
                )
            target = _same_origin_url(self.origin, urljoin(response["url"], location))
            response = worker.request_bytes(
                "GET",
                target,
                headers=_CAS_HTML_HEADERS,
                timeout_seconds=timeout_seconds,
            )
        raise SmartlightLoginContractMismatch("CAS login redirect chain is too long.")

    def _cas_principal(self, worker) -> dict:
        response = worker.request(
            "POST",
            self._url("/userInfo/getCasLoginUser"),
            headers=_FORM_HEADERS,
            body="",
            timeout_seconds=20,
        )
        if response["status"] in {301, 302, 303, 307, 308, 401, 403}:
            raise SmartlightLoginRequired("Smartlight CAS session is not authenticated.")
        if response["status"] != 200:
            raise SmartlightSessionCheckUnavailable(
                f"Smartlight principal check failed with HTTP {response['status']}."
            )
        payload = response.get("json")
        if not isinstance(payload, dict):
            if "/cas/login" in str(response.get("url") or "") or "<html" in str(
                response.get("text") or ""
            ).lower():
                raise SmartlightLoginRequired(
                    "Smartlight CAS session is not authenticated."
                )
            raise SmartlightLoginContractMismatch(
                "Smartlight principal response is invalid."
            )
        account = str(payload.get("dlzh") or "").strip()
        name = str(payload.get("userName") or account).strip()
        password_digest = str(payload.get("dlmm") or "").strip()
        organrole_id = str(payload.get("organroleId") or "").strip()
        user_id = str(payload.get("yhid") or "").strip()
        if not account or not name or not password_digest or not organrole_id or not user_id:
            raise SmartlightLoginContractMismatch(
                "Smartlight principal response is missing required identity fields."
            )
        return {
            "account": account,
            "name": name,
            "password_digest": password_digest,
            "organId": str(payload.get("organId") or ""),
            "organroleId": organrole_id,
            "organroleName": str(payload.get("organroleName") or ""),
            "personId": str(payload.get("ryid") or ""),
            "userId": user_id,
        }

    def _exchange_token(self, worker, principal: dict) -> dict:
        response = worker.request(
            "POST",
            f"{self.origin}/jwtcenter//JWTInfoController/getToken",
            headers=_FORM_HEADERS,
            body=urlencode(
                {
                    "subject": principal["account"],
                    "loginPwd": principal["password_digest"],
                }
            ),
            timeout_seconds=20,
        )
        if response["status"] in {301, 302, 303, 307, 308, 401, 403}:
            raise SmartlightLoginRequired("Smartlight CAS session is not authenticated.")
        payload = response.get("json")
        data = payload.get("resp_data") if isinstance(payload, dict) else None
        if response["status"] != 200 or not isinstance(data, dict):
            raise SmartlightSessionCheckUnavailable(
                "Smartlight JWT service is temporarily unavailable."
            )
        access_token = str(data.get("access_token") or "").strip()
        refresh_token = str(data.get("refresh_token") or "").strip()
        if not access_token:
            raise SmartlightLoginContractMismatch(
                "Smartlight JWT response has no access token."
            )
        safe_principal = {
            key: value
            for key, value in principal.items()
            if key != "password_digest"
        }
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "access_token_duration": data.get("access_token_duration"),
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "principal": safe_principal,
        }

    def _principal_context(self, worker) -> dict:
        state = worker.get_http_state()
        principal = state.get("principal") if isinstance(state, dict) else None
        if not isinstance(principal, dict) or not principal.get("organroleId"):
            self.probe_session(worker)
            state = worker.get_http_state()
            principal = state.get("principal")
        return dict(principal)

    def _authorized_post_json(self, worker, path: str, fields: dict) -> Any:
        state = worker.get_http_state()
        token = str(state.get("access_token") or "")
        if not token:
            self.probe_session(worker)
            state = worker.get_http_state()
            token = str(state.get("access_token") or "")
        response = worker.request(
            "POST",
            self._url(path),
            headers={**_FORM_HEADERS, "x-Authentication-Token": token},
            body=urlencode(fields),
            timeout_seconds=30,
        )
        if response["status"] in {401, 403}:
            self.probe_session(worker)
            token = str(worker.get_http_state().get("access_token") or "")
            response = worker.request(
                "POST",
                self._url(path),
                headers={**_FORM_HEADERS, "x-Authentication-Token": token},
                body=urlencode(fields),
                timeout_seconds=30,
            )
        if response["status"] in {301, 302, 303, 307, 308, 401, 403}:
            raise SmartlightLoginRequired("Smartlight session is no longer authenticated.")
        if response["status"] != 200:
            raise SmartlightSessionCheckUnavailable(
                f"Smartlight API failed with HTTP {response['status']}."
            )
        payload = response.get("json")
        if payload is None:
            raise SmartlightSessionCheckUnavailable(
                "Smartlight API did not return JSON."
            )
        return payload

    def _url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip("/"))


def build_smartlight_capability_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    specs = (
        CapabilitySpec(
            name=SMARTLIGHT_OVERVIEW_CAPABILITY,
            version="0.1.0",
            description="Summarize visible Smartlight cabinets and lamp posts.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            output_schema={"type": "object"},
            effect="read",
            adapter=SMARTLIGHT_ADAPTER_ID,
            workflow="smartlight-system-overview-v1",
        ),
        CapabilitySpec(
            name=SMARTLIGHT_LAMPPOST_LIST_CAPABILITY,
            version="0.1.0",
            description="List lamp posts visible to the authenticated Smartlight user.",
            input_schema=_paged_schema({"keyword": {"type": "string"}}),
            output_schema={"type": "object"},
            effect="read",
            adapter=SMARTLIGHT_ADAPTER_ID,
            workflow="smartlight-lamppost-list-v1",
        ),
        CapabilitySpec(
            name=SMARTLIGHT_ALARM_LIST_CAPABILITY,
            version="0.1.0",
            description="List RTU alarms visible to the authenticated Smartlight user.",
            input_schema=_paged_schema({"keyword": {"type": "string"}}),
            output_schema={"type": "object"},
            effect="read",
            adapter=SMARTLIGHT_ADAPTER_ID,
            workflow="smartlight-alarm-list-v1",
        ),
        CapabilitySpec(
            name=SMARTLIGHT_INSPECTION_TASK_LIST_CAPABILITY,
            version="0.1.0",
            description="List Smartlight inspection tasks with task and plan filters.",
            input_schema=_paged_schema(
                {
                    "task_name": {"type": "string"},
                    "plan_name": {"type": "string"},
                    "state": {"type": ["string", "integer", "null"]},
                }
            ),
            output_schema={"type": "object"},
            effect="read",
            adapter=SMARTLIGHT_ADAPTER_ID,
            workflow="smartlight-inspection-task-list-v1",
        ),
        CapabilitySpec(
            name=SMARTLIGHT_LEAKAGE_SUMMARY_CAPABILITY,
            version="0.1.0",
            description="Summarize Smartlight lamp leakage records in a date range.",
            input_schema=_paged_schema(
                {
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                }
            ),
            output_schema={"type": "object"},
            effect="read",
            adapter=SMARTLIGHT_ADAPTER_ID,
            workflow="smartlight-leakage-summary-v1",
        ),
    )
    for spec in specs:
        registry.register(spec)
    return registry


class _CasLoginParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.form_action: str | None = None
        self.captcha_src: str | None = None
        self.hidden_fields: dict[str, str] = {}
        self.error_message = ""
        self._in_auth_form = False
        self._in_error = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        if tag == "form" and values.get("id") == "auth-form":
            self._in_auth_form = True
            self.form_action = values.get("action") or None
            return
        if tag == "div" and "login-error-info" in values.get("class", "").split():
            self._in_error = True
            return
        if tag == "img" and values.get("id") == "captchaImage":
            self.captcha_src = values.get("src") or None
            return
        if (
            tag == "input"
            and self._in_auth_form
            and values.get("type", "").lower() == "hidden"
            and values.get("name") in {"lt", "execution", "_eventId"}
        ):
            self.hidden_fields[values["name"]] = values.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._in_error:
            self._in_error = False
        if tag == "form":
            self._in_auth_form = False

    def handle_data(self, data: str) -> None:
        if self._in_error:
            text = " ".join(data.split())
            if text:
                self.error_message = " ".join(
                    part for part in (self.error_message, text) if part
                )


def _login_rejection_code(message: str) -> str:
    normalized = "".join(str(message or "").split())
    if "验证码" in normalized:
        return "CAPTCHA_REJECTED"
    if any(token in normalized for token in ("用户名", "账号", "密码", "凭证")):
        return "CREDENTIALS_REJECTED"
    return "AUTHENTICATION_REJECTED"


def _same_origin_url(origin: str, value: Any) -> str:
    target = urljoin(f"{origin}/", str(value or ""))
    parsed = urlparse(target)
    target_origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    if target_origin != origin:
        raise SmartlightLoginContractMismatch("CAS URL escaped the registered origin.")
    return target


def _paged_schema(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": {
            **properties,
            "page": {"type": "integer"},
            "size": {"type": "integer"},
        },
        "additionalProperties": False,
    }


def _json_text(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        normalized = int(value if value not in (None, "") else default)
    except (TypeError, ValueError) as exc:
        raise ValueError("pagination value must be an integer") from exc
    if normalized < minimum or normalized > maximum:
        raise ValueError(f"pagination value must be between {minimum} and {maximum}")
    return normalized


def _page_items(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for name in ("list", "data", "rows", "content", "items"):
        items = payload.get(name)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _page_total(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return 0
    for name in ("totalCount", "total", "records", "count"):
        try:
            if payload.get(name) is not None:
                return int(payload[name])
        except (TypeError, ValueError):
            continue
    return len(_page_items(payload))


def _selected_fields(payload: Any, names: tuple[str, ...]) -> dict:
    if not isinstance(payload, dict):
        return {}
    return {name: _bounded_json(payload[name]) for name in names if name in payload}


def _bounded_json(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, list):
        return [_bounded_json(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, dict):
        return {
            str(key)[:80]: _bounded_json(item, depth=depth + 1)
            for key, item in list(value.items())[:30]
        }
    return str(value)[:200]


def _first(item: dict, *names: str) -> Any:
    for name in names:
        value = item.get(name)
        if value not in (None, ""):
            return value
    return None


def _normalize_cabinet(item: dict) -> dict:
    return {
        "id": _first(item, "controlCabinetId", "id"),
        "code": _first(item, "controlCabinetCode", "code"),
        "name": _first(item, "controlCabinetName", "name"),
        "state": _first(item, "onlineState", "rtuState", "state"),
        "alarmState": item.get("alarmState"),
    }


def _normalize_lamppost(item: dict) -> dict:
    return {
        "id": _first(item, "lampPostID", "lampPostId", "id"),
        "code": _first(item, "LampPostCode", "lampPostCode", "code"),
        "name": _first(item, "lampPostName", "name"),
        "road": _first(item, "StreetName", "streetName", "roadName"),
        "side": _first(item, "StreetSideName", "streetSideName"),
        "cabinet": _first(item, "controlCabinetName", "cabinetName"),
        "workArea": _first(item, "WorkAreaName", "workAreaName"),
        "lampCount": _first(item, "aloneLampCount", "lampCount"),
        "state": _first(item, "onlineState", "state", "lampPostState"),
    }


def _normalize_alarm(item: dict) -> dict:
    return {
        "id": _first(item, "alarmId", "id"),
        "time": _first(item, "alarmTime", "hitchTime", "createTime"),
        "device": _first(
            item,
            "controlCabinetName",
            "rtuName",
            "deviceName",
        ),
        "type": _first(item, "alarmTypeName", "hitchTypeName", "alarmName"),
        "level": _first(item, "alarmLevelName", "alarmLevel"),
        "state": _first(item, "alarmStateName", "alarmState", "dealState"),
        "message": _first(item, "alarmContent", "hitchContent", "description"),
    }


def _normalize_inspection_task(item: dict) -> dict:
    return {
        "id": _first(item, "inspectionTaskId", "taskId", "id"),
        "taskName": _first(item, "taskName", "inspectionTaskName"),
        "planName": _first(item, "planName", "inspectionPlanName"),
        "state": _first(item, "taskStateName", "taskState", "state"),
        "assignee": _first(item, "inspectionUserName", "taskUserName", "userName"),
        "startTime": _first(item, "startTime", "planStartTime"),
        "endTime": _first(item, "endTime", "planEndTime"),
    }


def _normalize_leakage(item: dict) -> dict:
    return {
        "id": _first(item, "coplogId", "alarmId", "id"),
        "time": _first(item, "alarmTime", "operationTime", "createTime"),
        "lampPost": _first(item, "lampPostName", "lampPostCode"),
        "lamp": _first(item, "aloneLampName", "lampName"),
        "road": _first(item, "streetName", "roadName"),
        "value": _first(item, "electricLeakage", "leakageValue", "currentValue"),
        "state": _first(item, "alarmStateName", "alarmState", "state"),
    }


def _count_values(items: list[dict], names: tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = _first(item, *names)
        key = str(value if value not in (None, "") else "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts
