from __future__ import annotations

import base64
from collections import Counter
from datetime import datetime, timedelta, timezone
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
SMARTLIGHT_ASSET_SEARCH_CAPABILITY = "smartlight.asset.search"
SMARTLIGHT_ASSET_DETAIL_CAPABILITY = "smartlight.asset.detail"
SMARTLIGHT_ALARM_ANALYSIS_CAPABILITY = "smartlight.alarm.analysis"
SMARTLIGHT_INSPECTION_TASK_DETAIL_CAPABILITY = "smartlight.inspection_task.detail"
SMARTLIGHT_LEAKAGE_ANALYSIS_CAPABILITY = "smartlight.leakage.analysis"

_SMARTLIGHT_ANALYSIS_LIMIT = 500
_SMARTLIGHT_ANALYSIS_PAGE_SIZE = 100

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

_SMARTLIGHT_BUSINESS_TIMEZONE_NAME = "Asia/Shanghai"
_SMARTLIGHT_BUSINESS_TIMEZONE = timezone(
    timedelta(hours=8),
    name=_SMARTLIGHT_BUSINESS_TIMEZONE_NAME,
)


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
            "password": hashlib.md5(password.encode("utf-8")).hexdigest().upper(),  # noqa: S324
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
        if capability_name == SMARTLIGHT_ASSET_SEARCH_CAPABILITY:
            return self.search_assets(worker, arguments)
        if capability_name == SMARTLIGHT_ASSET_DETAIL_CAPABILITY:
            return self.asset_detail(worker, arguments)
        if capability_name == SMARTLIGHT_ALARM_ANALYSIS_CAPABILITY:
            return self.analyze_alarms(worker, arguments)
        if capability_name == SMARTLIGHT_INSPECTION_TASK_DETAIL_CAPABILITY:
            return self.inspection_task_detail(worker, arguments)
        if capability_name == SMARTLIGHT_LEAKAGE_ANALYSIS_CAPABILITY:
            return self.analyze_leakage(worker, arguments)
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
        searchable_lampposts = self._authorized_post_json(
            worker,
            "/lLamppost/getDataByConditionForFacilityEx",
            {
                "json": _json_text(_lamppost_filters(context, "")),
                "orderBy": "lamp_post_code",
                "pageNum": 1,
                "pageSize": 1,
                "organroleId": context["organroleId"],
            },
        )
        cabinet_items = cabinets if isinstance(cabinets, list) else []
        map_lamp_total = _page_total(lampposts)
        searchable_lamp_total = _page_total(searchable_lampposts)
        return {
            "principal": context,
            "cabinetTotal": len(cabinet_items),
            "lampPostTotal": searchable_lamp_total,
            "lampPostCounts": {
                "searchable": searchable_lamp_total,
                "mapDetail": map_lamp_total,
            },
            "countSemantics": {
                "lampPostTotal": "searchable_lamppost_list",
                "lampPostCounts.mapDetail": "map_detail_inventory",
            },
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
                    _lamppost_filters(context, keyword)
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

    def search_assets(self, worker, arguments: dict) -> dict:
        context = self._principal_context(worker)
        asset_type = _normalize_asset_type(arguments.get("asset_type"))
        page = _bounded_int(arguments.get("page"), default=1, minimum=1, maximum=10000)
        size = _bounded_int(arguments.get("size"), default=20, minimum=1, maximum=100)
        keyword = str(arguments.get("keyword") or "").strip()
        payload = self._asset_page(
            worker,
            context,
            asset_type=asset_type,
            keyword=keyword,
            page=page,
            size=size,
        )
        items = _page_items(payload)
        return {
            "assetType": asset_type,
            "keyword": keyword or None,
            "page": page,
            "size": size,
            "total": _page_total(payload),
            "count": len(items),
            "items": [
                _normalize_asset_summary(asset_type, item)
                for item in items
            ],
        }

    def asset_detail(self, worker, arguments: dict) -> dict:
        context = self._principal_context(worker)
        asset_type = _normalize_asset_type(arguments.get("asset_type"))
        asset_id = str(arguments.get("asset_id") or "").strip()
        if not asset_id:
            raise ValueError("asset_id is required")

        if asset_type == "lamppost":
            payload = self._authorized_post_json(
                worker,
                "/lLamppost/getLampPostDetail",
                {
                    "lampPostId": asset_id,
                    "userId": context["userId"],
                },
            )
            record = _result_record(payload)
            if record is None or str(
                _first(record, "lampPostID", "lampPostId", "id") or ""
            ) != asset_id:
                return _missing_asset_detail(asset_type, asset_id)
            return {
                "assetType": asset_type,
                "assetId": asset_id,
                "found": True,
                "detail": _normalize_lamppost_detail(record),
            }

        record = self._find_asset_record(
            worker,
            context,
            asset_type=asset_type,
            asset_id=asset_id,
        )
        if record is None:
            return _missing_asset_detail(asset_type, asset_id)
        result = {
            "assetType": asset_type,
            "assetId": asset_id,
            "found": True,
            "detail": (
                _normalize_cabinet_detail(record)
                if asset_type == "cabinet"
                else _normalize_rtu_detail(record)
            ),
        }
        if asset_type == "rtu":
            relays_payload = self._authorized_post_json(
                worker,
                "/rRturelay/getDataByCondition",
                {
                    "json": _json_text({"rtuId": asset_id}),
                    "pageNum": 1,
                    "pageSize": 999,
                    "organroleId": context["organroleId"],
                },
            )
            relay_items = _page_items(relays_payload)
            result["relayTotal"] = _page_total(relays_payload)
            result["relays"] = [_normalize_rtu_relay(item) for item in relay_items]
        return result

    def _find_asset_record(
        self,
        worker,
        context: dict,
        *,
        asset_type: str,
        asset_id: str,
    ) -> dict | None:
        for page in range(1, 6):
            payload = self._asset_page(
                worker,
                context,
                asset_type=asset_type,
                keyword="",
                page=page,
                size=100,
                asset_id=asset_id,
            )
            items = _page_items(payload)
            for item in items:
                if str(_asset_id(asset_type, item) or "") == asset_id:
                    return item
            if not items or page * 100 >= _page_total(payload):
                break
        return None

    def _asset_page(
        self,
        worker,
        context: dict,
        *,
        asset_type: str,
        keyword: str,
        page: int,
        size: int,
        asset_id: str = "",
    ) -> Any:
        if asset_type == "lamppost":
            query = _lamppost_filters(context, keyword)
            if asset_id:
                query["lampPostId"] = asset_id
            return self._authorized_post_json(
                worker,
                "/lLamppost/getDataByConditionForFacilityEx",
                {
                    "json": _json_text(query),
                    "orderBy": "lamp_post_code",
                    "pageNum": page,
                    "pageSize": size,
                    "organroleId": context["organroleId"],
                },
            )
        if asset_type == "rtu":
            return self._authorized_post_json(
                worker,
                "/rRtu/getDataByCondition",
                {
                    "json": _json_text(
                        {
                            "_like_params": keyword,
                            "_timebegin_addDate": "",
                            "_timeend_addDate": "",
                            "rtuId": asset_id,
                        }
                    ),
                    "orderBy": "add_date desc",
                    "pageNum": page,
                    "pageSize": size,
                    "organroleId": context["organroleId"],
                },
            )
        return self._authorized_post_json(
            worker,
            "/rControlCabinet/getDataByCondition",
            {
                "json": _json_text(
                    {
                        "labels": [],
                        "_like_controlCabinetName": keyword,
                        "_timebegin_addDate": "",
                        "_timeend_addDate": "",
                        "_include_transTypeId": [],
                        "_include_workAreaId": [],
                        "ltOrGtTime": "",
                        "_timeend_maxDate": "",
                        "controlCabinetName": "",
                        "controlCabinetId": asset_id,
                        "controlCabinetCode": "",
                        "userId": context["userId"],
                        "electricalType": "",
                        "electricityNumberFlag": "",
                    }
                ),
                "pageNum": page,
                "pageSize": size,
                "organroleId": context["organroleId"],
            },
        )

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
        normalized_items = [_normalize_alarm(item) for item in items]
        normalized_items.sort(
            key=lambda item: (
                str(item.get("lastActivityAt") or ""),
                str(item.get("occurredAt") or ""),
            ),
            reverse=True,
        )
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
            "summaryScope": {
                "type": "current_system_snapshot",
                "pageFiltered": False,
                "checkedAt": datetime.now(timezone.utc).isoformat(),
            },
            "sort": {
                "field": "lastActivityAt",
                "direction": "desc",
                "scope": "returned_page",
            },
            "timeSemantics": {
                "recentField": "lastActivityAt",
                "occurredAt": "Time when the alarm first occurred.",
                "lastActivityAt": "Most recent alarm activity; use this for recent ordering.",
            },
            "items": normalized_items,
        }

    def analyze_alarms(self, worker, arguments: dict) -> dict:
        context = self._principal_context(worker)
        keyword = str(arguments.get("keyword") or "").strip()
        alarm_type = str(arguments.get("alarm_type") or "").strip()
        alarm_state = _normalize_choice(
            arguments.get("alarm_state"),
            default="all",
            allowed={"all", "current", "cleared"},
            field_name="alarm_state",
        )
        time_field = _normalize_choice(
            arguments.get("time_field"),
            default="last_activity",
            allowed={"last_activity", "occurred"},
            field_name="time_field",
        )
        top_n = _bounded_int(arguments.get("top_n"), default=10, minimum=1, maximum=20)
        start_date, end_date, range_source, last_days = _resolve_analysis_date_range(
            arguments
        )
        raw_items: list[dict] = []
        downstream_total = 0
        snapshot: dict[str, Any] = {}
        for page in range(1, (_SMARTLIGHT_ANALYSIS_LIMIT // _SMARTLIGHT_ANALYSIS_PAGE_SIZE) + 1):
            payload = self._alarm_analysis_page(
                worker,
                context,
                keyword=keyword,
                alarm_state=alarm_state,
                time_field=time_field,
                start_date=start_date,
                end_date=end_date,
                page=page,
            )
            if page == 1:
                snapshot = _selected_fields(
                    payload,
                    ("todayAlarm", "untreated", "yesterdayAlarm"),
                )
            page_payload = (
                payload.get("RtuHisHitchAlarm")
                if isinstance(payload, dict)
                else payload
            )
            page_items = _page_items(page_payload)
            if page == 1:
                downstream_total = _page_total(page_payload)
            raw_items.extend(page_items)
            if (
                not page_items
                or len(raw_items) >= downstream_total
                or len(raw_items) >= _SMARTLIGHT_ANALYSIS_LIMIT
            ):
                break

        normalized = [_normalize_alarm(item) for item in raw_items]
        if alarm_type:
            needle = alarm_type.casefold()
            normalized = [
                item
                for item in normalized
                if needle in str(item.get("type") or "").casefold()
            ]
        if alarm_state != "all":
            expected_code = 0 if alarm_state == "current" else 1
            normalized = [
                item for item in normalized if item.get("stateCode") == expected_code
            ]
        sort_field = "lastActivityAt" if time_field == "last_activity" else "occurredAt"
        normalized.sort(
            key=lambda item: str(item.get(sort_field) or ""),
            reverse=True,
        )
        return {
            "filters": {
                "keyword": keyword or None,
                "alarmType": alarm_type or None,
                "alarmState": alarm_state,
                "timeField": time_field,
            },
            "dateRange": {
                "source": range_source,
                "lastDays": last_days,
                "startDate": start_date,
                "endDate": end_date,
                "inclusive": True,
                "timezone": _SMARTLIGHT_BUSINESS_TIMEZONE_NAME,
            },
            "downstreamTotal": downstream_total,
            "retrievedCount": len(raw_items),
            "analyzedCount": len(normalized),
            "truncated": downstream_total > len(raw_items),
            "analysisLimit": _SMARTLIGHT_ANALYSIS_LIMIT,
            "snapshot": snapshot,
            "stateCounts": _top_counts(normalized, "stateLabel", top_n),
            "topAlarmTypes": _top_counts(normalized, "type", top_n),
            "topDevices": _top_counts(normalized, "device", top_n),
            "dailyTrend": _daily_counts(normalized, sort_field),
            "recentRecords": normalized[:20],
            "timeSemantics": {
                "filterField": sort_field,
                "occurredAt": "Time when the alarm first occurred.",
                "lastActivityAt": "Most recent alarm activity.",
            },
        }

    def _alarm_analysis_page(
        self,
        worker,
        context: dict,
        *,
        keyword: str,
        alarm_state: str,
        time_field: str,
        start_date: str,
        end_date: str,
        page: int,
    ) -> Any:
        state_values = {
            "all": ["131", "132"],
            "current": ["131"],
            "cleared": ["132"],
        }[alarm_state]
        return self._authorized_post_json(
            worker,
            "/rHisHitchAlarm/getDataByRtuAlarm",
            {
                "json": _json_text(
                    {
                        "codeOrName": keyword,
                        "_include_conductStatue": state_values,
                        "_include_hitchDicIds": [],
                        "_include_weightFacto": [],
                        "_include_isSubmitWorkArea": [],
                        "conductStatue": [],
                        "weightFacto": [1, 2, 3, 4, 5, 6],
                        "hitchDicId": "",
                        "_timebegin_begin": (
                            f"{start_date} 00:00:00" if start_date else ""
                        ),
                        "_timeend_end": (
                            f"{end_date} 23:59:59" if end_date else ""
                        ),
                        "type": "3",
                        "_include_groupId": [],
                        "groupId": "",
                        "dateType": "1" if time_field == "last_activity" else "0",
                        "showData": True,
                        "userId": context["userId"],
                        "leakageCurrent": "",
                        "reporType": "",
                    }
                ),
                "pageNum": page,
                "pageSize": _SMARTLIGHT_ANALYSIS_PAGE_SIZE,
                "organroleId": context["organroleId"],
            },
        )

    def list_inspection_tasks(self, worker, arguments: dict) -> dict:
        context = self._principal_context(worker)
        page = _bounded_int(arguments.get("page"), default=1, minimum=1, maximum=10000)
        size = _bounded_int(arguments.get("size"), default=20, minimum=1, maximum=100)
        task_name = str(arguments.get("task_name") or "").strip()
        plan_name = str(arguments.get("plan_name") or "").strip()
        state = _normalize_inspection_state_filter(arguments.get("state"))
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
            "fieldSemantics": {
                "progress": (
                    "Downstream-reported task progress. Display it exactly and do not "
                    "derive another percentage from device counts."
                ),
                "deviceCounts": (
                    "Independent confirmed, lamp-post and RTU counts. Do not present "
                    "confirmed/(lampPosts+rtus) as completed/total."
                ),
            },
            "items": [_normalize_inspection_task(item) for item in items],
        }

    def inspection_task_detail(self, worker, arguments: dict) -> dict:
        context = self._principal_context(worker)
        task_id = str(arguments.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("task_id is required")
        start_date = _optional_date(arguments.get("start_date"), "start_date")
        end_date = _optional_date(arguments.get("end_date"), "end_date")
        if start_date and end_date and start_date > end_date:
            raise ValueError("start_date cannot be after end_date")
        detail_date = _optional_date(arguments.get("detail_date"), "detail_date")
        clockin_user = str(arguments.get("clockin_user") or "").strip()
        has_issues = arguments.get("has_issues")
        if has_issues is not None and not isinstance(has_issues, bool):
            raise ValueError("has_issues must be a boolean")
        page = _bounded_int(arguments.get("page"), default=1, minimum=1, maximum=10000)
        size = _bounded_int(arguments.get("size"), default=20, minimum=1, maximum=100)

        task_payload = self._authorized_post_json(
            worker,
            "/inspectionTask/getDataByCondition",
            {
                "json": _json_text({"_taskId": task_id}),
                "pageNum": 1,
                "pageSize": 100,
                "organroleId": context["organroleId"],
            },
        )
        task_record = next(
            (
                item
                for item in _page_items(task_payload)
                if str(_first(item, "inspectionTaskId", "taskId", "id") or "")
                == task_id
            ),
            None,
        )
        daily_payload = self._authorized_post_json(
            worker,
            "/InspectionDeviceGroup/getDataByCondition",
            {
                "json": _json_text(
                    {
                        "taskId": task_id,
                        "startTime": start_date,
                        "endTime": end_date,
                    }
                ),
                "pageNum": 1,
                "pageSize": _SMARTLIGHT_ANALYSIS_LIMIT,
            },
        )
        daily_records = [_normalize_inspection_day(item) for item in _page_items(daily_payload)]
        result: dict[str, Any] = {
            "taskId": task_id,
            "found": task_record is not None or bool(daily_records),
            "task": _normalize_inspection_task(task_record) if task_record else None,
            "filters": {
                "startDate": start_date or None,
                "endDate": end_date or None,
                "detailDate": detail_date or None,
                "clockinUser": clockin_user or None,
                "hasIssues": has_issues,
            },
            "dailyTotal": _page_total(daily_payload),
            "dailyCount": len(daily_records),
            "dailyTruncated": _page_total(daily_payload) > len(daily_records),
            "days": daily_records,
            "fieldSemantics": {
                "plannedDeviceCount": "Downstream planned device count for that day.",
                "completedDeviceCount": "Downstream completed device count for that day.",
                "completionRate": "Downstream-reported daily completion rate.",
                "missingDevices": "Not available; do not infer device identities from a count difference.",
            },
        }
        if not detail_date:
            return result

        matching_day = next(
            (item for item in daily_records if item.get("date") == detail_date),
            None,
        )
        if matching_day is None:
            result.update(
                {
                    "detailDateFound": False,
                    "clockinTotal": 0,
                    "clockinCount": 0,
                    "clockins": [],
                }
            )
            return result
        clockin_payload = self._authorized_post_json(
            worker,
            "/inspectionTask/getClockinDataByTaskId",
            {
                "json": _json_text(
                    {
                        "taskId": task_id,
                        "groupId": matching_day.get("groupId"),
                        "clockinUser": clockin_user,
                        "hasIssues": has_issues,
                        "_startTime": f"{detail_date} 00:00:00",
                        "_endTime": f"{detail_date} 24:00:00",
                    }
                ),
                "pageNum": page,
                "pageSize": size,
            },
        )
        clockins = [_normalize_inspection_clockin(item) for item in _page_items(clockin_payload)]
        result.update(
            {
                "detailDateFound": True,
                "clockinPage": page,
                "clockinSize": size,
                "clockinTotal": _page_total(clockin_payload),
                "clockinCount": len(clockins),
                "clockins": clockins,
            }
        )
        return result

    def leakage_summary(self, worker, arguments: dict) -> dict:
        context = self._principal_context(worker)
        page = _bounded_int(arguments.get("page"), default=1, minimum=1, maximum=10000)
        size = _bounded_int(arguments.get("size"), default=20, minimum=1, maximum=100)
        start_date, end_date, range_source, last_days = _resolve_date_range(arguments)
        query = {
            "dateType": "",
            "_include_controlCabinetId": [],
            "_like_lampPostCode": "",
            "_timebegin_alarmAddDate": "",
            "_timeend_alarmAddDate": "",
            "_include_alarmState": [0, 1],
            "_include_duration": [0],
            "_include_hitchDicId": [],
            "_include_streetId": [],
            "_include_workId": [],
            "_timebegin_lastDate": f"{start_date} 00:00:00" if start_date else "",
            "_timeend_lastDate": f"{end_date} 23:59:59" if end_date else "",
            "_show_newData": True,
            "_leakage_threshold": 0,
            "_leakage_current": 0,
            "_duration": 0,
            "userId": context["userId"],
        }
        records = self._authorized_post_json(
            worker,
            "/lHisHitchAlarm/getDataByCondition",
            {
                "json": _json_text(query),
                "orderBy": "l_his_coplog.cop_date",
                "pageNum": page,
                "pageSize": size,
                "organroleId": context["organroleId"],
            },
        )
        counts = self._authorized_post_json(
            worker,
            "/lHisHitchAlarm/getCountDataByCondition",
            {
                "json": _json_text(
                    {
                        **query,
                        "_timebegin_lastDate": "",
                        "_timeend_lastDate": "",
                    }
                ),
                "organroleId": context["organroleId"],
            },
        )
        items = _page_items(records)
        record_total = _page_total(records)
        return {
            "startDate": start_date or None,
            "endDate": end_date or None,
            "dateRange": {
                "source": range_source,
                "lastDays": last_days,
                "startDate": start_date or None,
                "endDate": end_date or None,
                "inclusive": True,
                "timezone": _SMARTLIGHT_BUSINESS_TIMEZONE_NAME,
            },
            "page": page,
            "size": size,
            "total": record_total,
            "count": len(items),
            "rangeSummary": {"recordTotal": record_total},
            "summary": _bounded_json(counts),
            "summaryScope": {
                "type": "current_system_snapshot",
                "dateRangeApplied": False,
                "checkedAt": datetime.now(timezone.utc).isoformat(),
            },
            "items": [_normalize_leakage(item) for item in items],
        }

    def analyze_leakage(self, worker, arguments: dict) -> dict:
        context = self._principal_context(worker)
        top_n = _bounded_int(arguments.get("top_n"), default=10, minimum=1, maximum=20)
        start_date, end_date, range_source, last_days = _resolve_analysis_date_range(
            arguments
        )
        query = {
            "dateType": "",
            "_include_controlCabinetId": [],
            "_like_lampPostCode": "",
            "_timebegin_alarmAddDate": "",
            "_timeend_alarmAddDate": "",
            "_include_alarmState": [0, 1],
            "_include_duration": [0],
            "_include_hitchDicId": [],
            "_include_streetId": [],
            "_include_workId": [],
            "_timebegin_lastDate": (
                f"{start_date} 00:00:00" if start_date else ""
            ),
            "_timeend_lastDate": f"{end_date} 23:59:59" if end_date else "",
            "_show_newData": True,
            "_leakage_threshold": 0,
            "_leakage_current": 0,
            "_duration": 0,
            "userId": context["userId"],
        }
        raw_items: list[dict] = []
        downstream_total = 0
        for page in range(1, (_SMARTLIGHT_ANALYSIS_LIMIT // _SMARTLIGHT_ANALYSIS_PAGE_SIZE) + 1):
            payload = self._authorized_post_json(
                worker,
                "/lHisHitchAlarm/getDataByCondition",
                {
                    "json": _json_text(query),
                    "orderBy": "l_his_coplog.cop_date",
                    "pageNum": page,
                    "pageSize": _SMARTLIGHT_ANALYSIS_PAGE_SIZE,
                    "organroleId": context["organroleId"],
                },
            )
            page_items = _page_items(payload)
            if page == 1:
                downstream_total = _page_total(payload)
            raw_items.extend(page_items)
            if (
                not page_items
                or len(raw_items) >= downstream_total
                or len(raw_items) >= _SMARTLIGHT_ANALYSIS_LIMIT
            ):
                break
        normalized = [_normalize_leakage(item) for item in raw_items]
        normalized.sort(key=lambda item: str(item.get("time") or ""), reverse=True)
        return {
            "dateRange": {
                "source": range_source,
                "lastDays": last_days,
                "startDate": start_date,
                "endDate": end_date,
                "inclusive": True,
                "timezone": _SMARTLIGHT_BUSINESS_TIMEZONE_NAME,
            },
            "downstreamTotal": downstream_total,
            "retrievedCount": len(raw_items),
            "analyzedCount": len(normalized),
            "truncated": downstream_total > len(raw_items),
            "analysisLimit": _SMARTLIGHT_ANALYSIS_LIMIT,
            "dailyTrend": _daily_counts(normalized, "time"),
            "topLampPosts": _top_counts(normalized, "lampPost", top_n),
            "topRoads": _top_counts(normalized, "road", top_n),
            "topAlarmTypes": _top_counts(normalized, "alarmType", top_n),
            "stateCounts": _top_counts(normalized, "state", top_n),
            "recentRecords": normalized[:20],
            "summaryScope": {
                "type": "date_range_records_only",
                "globalDashboardCountersIncluded": False,
            },
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
            version="0.2.0",
            description=(
                "Summarize visible Smartlight cabinets and report both searchable "
                "and map-detail lamp-post counts."
            ),
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
            version="0.2.0",
            description=(
                "List RTU alarms visible to the authenticated Smartlight user, "
                "ordered by latest activity within the returned page."
            ),
            input_schema=_paged_schema({"keyword": {"type": "string"}}),
            output_schema={"type": "object"},
            effect="read",
            adapter=SMARTLIGHT_ADAPTER_ID,
            workflow="smartlight-alarm-list-v1",
        ),
        CapabilitySpec(
            name=SMARTLIGHT_INSPECTION_TASK_LIST_CAPABILITY,
            version="0.2.1",
            description=(
                "List Smartlight inspection tasks with task, plan and state filters, "
                "including group, schedule, progress and device counts."
            ),
            input_schema=_paged_schema(
                {
                    "task_name": {"type": "string"},
                    "plan_name": {"type": "string"},
                    "state": {"type": ["integer", "string", "null"]},
                }
            ),
            output_schema={"type": "object"},
            effect="read",
            adapter=SMARTLIGHT_ADAPTER_ID,
            workflow="smartlight-inspection-task-list-v1",
        ),
        CapabilitySpec(
            name=SMARTLIGHT_LEAKAGE_SUMMARY_CAPABILITY,
            version="0.2.0",
            description=(
                "List date-filtered Smartlight leakage records and separately expose "
                "the unfiltered current-system dashboard counters."
            ),
            input_schema=_paged_schema(
                {
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "last_days": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 3660,
                    },
                }
            ),
            output_schema={"type": "object"},
            effect="read",
            adapter=SMARTLIGHT_ADAPTER_ID,
            workflow="smartlight-leakage-summary-v1",
        ),
        CapabilitySpec(
            name=SMARTLIGHT_ASSET_SEARCH_CAPABILITY,
            version="0.1.0",
            description=(
                "Search visible Smartlight control cabinets, RTUs, or lamp posts "
                "through one normalized asset contract."
            ),
            input_schema={
                **_paged_schema(
                    {
                    "asset_type": {
                        "type": "string",
                        "enum": ["cabinet", "rtu", "lamppost"],
                    },
                    "keyword": {"type": "string"},
                    }
                ),
                "required": ["asset_type"],
            },
            output_schema={"type": "object"},
            effect="read",
            adapter=SMARTLIGHT_ADAPTER_ID,
            workflow="smartlight-asset-search-v1",
        ),
        CapabilitySpec(
            name=SMARTLIGHT_ASSET_DETAIL_CAPABILITY,
            version="0.1.0",
            description=(
                "Read an exact Smartlight asset by ID; RTU details include relay "
                "and circuit structure."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "asset_type": {
                        "type": "string",
                        "enum": ["cabinet", "rtu", "lamppost"],
                    },
                    "asset_id": {"type": "string"},
                },
                "required": ["asset_type", "asset_id"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            effect="read",
            adapter=SMARTLIGHT_ADAPTER_ID,
            workflow="smartlight-asset-detail-v1",
        ),
        CapabilitySpec(
            name=SMARTLIGHT_ALARM_ANALYSIS_CAPABILITY,
            version="0.1.0",
            description=(
                "Analyze a bounded date range of Smartlight RTU alarms by state, "
                "type, device, and day."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "alarm_type": {"type": "string"},
                    "alarm_state": {
                        "type": "string",
                        "enum": ["all", "current", "cleared"],
                    },
                    "time_field": {
                        "type": "string",
                        "enum": ["last_activity", "occurred"],
                    },
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "last_days": {"type": "integer", "minimum": 1, "maximum": 3660},
                    "top_n": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            effect="read",
            adapter=SMARTLIGHT_ADAPTER_ID,
            workflow="smartlight-alarm-analysis-v1",
        ),
        CapabilitySpec(
            name=SMARTLIGHT_INSPECTION_TASK_DETAIL_CAPABILITY,
            version="0.1.0",
            description=(
                "Read daily progress and observed clock-in records for an exact "
                "Smartlight inspection task."
            ),
            input_schema={
                **_paged_schema(
                    {
                    "task_id": {"type": "string"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "detail_date": {"type": "string"},
                    "clockin_user": {"type": "string"},
                    "has_issues": {"type": ["boolean", "null"]},
                    }
                ),
                "required": ["task_id"],
            },
            output_schema={"type": "object"},
            effect="read",
            adapter=SMARTLIGHT_ADAPTER_ID,
            workflow="smartlight-inspection-task-detail-v1",
        ),
        CapabilitySpec(
            name=SMARTLIGHT_LEAKAGE_ANALYSIS_CAPABILITY,
            version="0.1.0",
            description=(
                "Analyze bounded Smartlight leakage records by day, lamp post, "
                "road, alarm type, and state."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "last_days": {"type": "integer", "minimum": 1, "maximum": 3660},
                    "top_n": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            effect="read",
            adapter=SMARTLIGHT_ADAPTER_ID,
            workflow="smartlight-leakage-analysis-v1",
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


def _normalize_asset_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in {"cabinet", "rtu", "lamppost"}:
        raise ValueError("asset_type must be cabinet, rtu, or lamppost")
    return normalized


def _normalize_choice(
    value: Any,
    *,
    default: str,
    allowed: set[str],
    field_name: str,
) -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{field_name} must be one of: {choices}")
    return normalized


def _resolve_analysis_date_range(arguments: dict) -> tuple[str, str, str, int | None]:
    if not any(
        arguments.get(name) not in (None, "")
        for name in ("start_date", "end_date", "last_days")
    ):
        start, end, _source, days = _resolve_date_range({"last_days": 30})
        return start, end, "default_last_days", days
    return _resolve_date_range(arguments)


def _optional_date(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    try:
        datetime.strptime(normalized, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{field_name} must use YYYY-MM-DD") from exc
    return normalized


def _json_text(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _lamppost_filters(context: dict, keyword: str) -> dict:
    return {
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


def _resolve_date_range(
    arguments: dict,
    *,
    now: datetime | None = None,
) -> tuple[str, str, str, int | None]:
    start_date = str(arguments.get("start_date") or "").strip()
    end_date = str(arguments.get("end_date") or "").strip()
    raw_last_days = arguments.get("last_days")
    if raw_last_days is not None and (start_date or end_date):
        raise ValueError("last_days cannot be combined with start_date or end_date")
    if raw_last_days is not None:
        last_days = _bounded_int(
            raw_last_days,
            default=30,
            minimum=1,
            maximum=3660,
        )
        business_now = (now or datetime.now(_SMARTLIGHT_BUSINESS_TIMEZONE)).astimezone(
            _SMARTLIGHT_BUSINESS_TIMEZONE
        )
        end = business_now.date()
        start = end - timedelta(days=last_days - 1)
        return start.isoformat(), end.isoformat(), "last_days", last_days
    for name, value in (("start_date", start_date), ("end_date", end_date)):
        if not value:
            continue
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"{name} must use YYYY-MM-DD") from exc
    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date cannot be after end_date")
    return start_date, end_date, "explicit" if (start_date or end_date) else "none", None


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


def _result_record(payload: Any) -> dict | None:
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if isinstance(result, dict):
        return result
    if isinstance(result, list):
        return next((item for item in result if isinstance(item, dict)), None)
    if any(key in payload for key in ("id", "lampPostId", "lampPostID")):
        return payload
    return None


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


def _asset_id(asset_type: str, item: dict) -> Any:
    if asset_type == "cabinet":
        return _first(item, "controlCabinetId", "id")
    if asset_type == "rtu":
        return _first(item, "rtuId", "id")
    return _first(item, "lampPostID", "lampPostId", "id")


def _normalize_asset_summary(asset_type: str, item: dict) -> dict:
    if asset_type == "cabinet":
        return {
            **_normalize_cabinet(item),
            "assetType": asset_type,
            "road": _first(item, "streetName", "roadName"),
            "side": _first(item, "streetSideName", "sideName"),
            "workArea": _first(item, "workAreaName"),
            "address": _first(item, "address", "controlCabinetAddress"),
            "capacity": _first(item, "capacityStr", "capacity"),
            "electricalType": _first(item, "electricalTypeName", "electricalType"),
        }
    if asset_type == "rtu":
        return {
            "assetType": asset_type,
            "id": _first(item, "rtuId", "id"),
            "code": _first(item, "rtuCode", "code"),
            "name": _first(item, "rtuName", "name"),
            "model": _first(item, "productModel", "modelName", "rtuModel"),
            "type": _first(item, "rtuTypeName", "typeName"),
            "cabinet": _first(item, "controlCabinetName", "cabinetName"),
            "group": _first(item, "groupName", "rtuGroupName"),
            "workArea": _first(item, "workAreaName"),
            "state": _first(
                item,
                "runningStateName",
                "runStateName",
                "onlineState",
                "state",
            ),
        }
    return {**_normalize_lamppost(item), "assetType": asset_type}


def _missing_asset_detail(asset_type: str, asset_id: str) -> dict:
    return {
        "assetType": asset_type,
        "assetId": asset_id,
        "found": False,
        "detail": None,
    }


def _normalize_cabinet_detail(item: dict) -> dict:
    return {
        **_normalize_asset_summary("cabinet", item),
        "transformerType": _first(item, "transTypeName", "transformerTypeName"),
        "electricityNumber": _first(item, "electricityNumber"),
        "organization": _first(item, "organName", "organizationName"),
        "longitude": _first(item, "longitude", "lng", "lon"),
        "latitude": _first(item, "latitude", "lat"),
        "createdAt": _first(item, "addDate", "createdAt"),
        "remark": _first(item, "remark"),
    }


def _normalize_rtu_detail(item: dict) -> dict:
    return {
        **_normalize_asset_summary("rtu", item),
        "installDate": _first(item, "installDate", "installationDate"),
        "outDate": _first(item, "outDate", "productionDate"),
        "warrantyUntil": _first(item, "warrantyDate", "warrantyUntil"),
        "onlineAt": _first(item, "onlineTime", "lastOnlineTime"),
        "manufacturer": _first(item, "manufacturerName", "factoryName"),
        "createdAt": _first(item, "addDate", "createdAt"),
        "remark": _first(item, "remark"),
    }


def _normalize_lamppost_detail(item: dict) -> dict:
    return {
        **_normalize_asset_summary("lamppost", item),
        "type": _first(item, "lampPostTypeName", "LampPostTypeName", "typeName"),
        "height": _first(item, "lampPostHeight", "height"),
        "material": _first(item, "materialTypeName", "materialName"),
        "rtu": _first(item, "rtuName"),
        "rtuCode": _first(item, "rtuCode"),
        "transformer": _first(item, "transformerName", "transName"),
        "address": _first(item, "address", "lampPostAddress"),
        "longitude": _first(item, "longitude", "lng", "lon"),
        "latitude": _first(item, "latitude", "lat"),
        "createdAt": _first(item, "addDate", "createdAt"),
        "remark": _first(item, "remark"),
    }


def _normalize_rtu_relay(item: dict) -> dict:
    switches: list[dict] = []
    circuit_count = 0
    for switch in item.get("roadSwitchList") or []:
        if not isinstance(switch, dict):
            continue
        circuits = []
        for circuit in switch.get("rRturoadList") or []:
            if not isinstance(circuit, dict):
                continue
            circuits.append(
                {
                    "id": _first(circuit, "rturoadId", "id"),
                    "number": _first(circuit, "rturoadNumber", "roadNumber"),
                    "name": _first(circuit, "rturoadName", "roadName"),
                    "phase": _first(circuit, "powerType", "powerTypeName"),
                    "lampPostCount": _first(circuit, "lamppostCount"),
                    "lampCount": _first(circuit, "lampCount"),
                }
            )
        circuit_count += len(circuits)
        switches.append(
            {
                "id": _first(switch, "roadSwitchId", "id"),
                "number": _first(switch, "roadSwitchNumber"),
                "model": _first(switch, "switchModel"),
                "circuits": circuits,
            }
        )
    return {
        "id": _first(item, "rturelayId", "id"),
        "number": _first(item, "rturelayNumber"),
        "name": _first(item, "rturelayName"),
        "enabled": _first(item, "isEnabled"),
        "workMode": _first(item, "workModelName"),
        "externalPower": _first(item, "externalPowerFlag"),
        "remark": _first(item, "relayRemark", "remark"),
        "roadSwitchCount": len(switches),
        "circuitCount": circuit_count,
        "roadSwitches": switches,
    }


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
    occurred_at = _first(
        item,
        "occurDate",
        "alarmTime",
        "hitchTime",
        "createTime",
    )
    last_activity_at = _first(item, "lastDate", "lastTime") or occurred_at
    state_code = _first(item, "conductStatue", "alarmState", "dealState")
    if isinstance(state_code, str) and state_code.strip().isdigit():
        state_code = int(state_code.strip())
    state_label = _first(item, "alarmStateName")
    if not state_label:
        state_label = {0: "当前告警", 1: "已消除"}.get(state_code, state_code)
    return {
        "id": _first(item, "hitchAlarmId", "alarmId", "id"),
        "occurredAt": occurred_at,
        "lastActivityAt": last_activity_at,
        "time": occurred_at,
        "lastTime": last_activity_at,
        "device": _first(
            item,
            "controlCabinetName",
            "rtuName",
            "deviceName",
        ),
        "deviceCode": _first(item, "rtuCode", "controlCabinetCode", "deviceCode"),
        "type": _first(
            item,
            "hitchName",
            "alarmTypeName",
            "hitchTypeName",
            "alarmName",
        ),
        "level": _first(item, "alarmLevelName", "alarmLevel", "weightFacto"),
        "state": state_label,
        "stateCode": state_code,
        "stateLabel": state_label,
        "message": _first(
            item,
            "hitchIntro",
            "alarmContent",
            "hitchContent",
            "description",
        ),
        "workArea": _first(item, "workAreaName"),
        "group": _first(item, "groupName"),
    }


def _normalize_inspection_task(item: dict) -> dict:
    state_code, state_label = _inspection_state(item)
    confirmed_device_count = _first(item, "confirmDeviceNum")
    lamp_post_count = _first(item, "lampostQty", "lampPostQty")
    rtu_count = _first(item, "rtuQty")
    return {
        "id": _first(item, "inspectionTaskId", "taskId", "id"),
        "taskName": _first(item, "taskName", "inspectionTaskName"),
        "planName": _first(item, "planName", "inspectionPlanName"),
        "state": state_label or state_code,
        "stateCode": state_code,
        "stateLabel": state_label,
        "assignee": _first(item, "inspectionUserName", "taskUserName", "userName"),
        "inspectionGroup": _first(item, "groupName", "inspectionGroupName"),
        "startTime": _first(
            item,
            "startTime",
            "planStartTime",
            "taskStartDate",
        ),
        "endTime": _first(
            item,
            "endTime",
            "planEndTime",
            "taskDeadline",
        ),
        "progress": _first(item, "taskProgress", "progress"),
        "yesterdayProgress": _first(
            item,
            "yesterdayTaskProgress",
            "yesterdayProgress",
        ),
        "confirmedDeviceCount": confirmed_device_count,
        "lampPostCount": lamp_post_count,
        "rtuCount": rtu_count,
        "deviceCounts": {
            "confirmed": confirmed_device_count,
            "lampPosts": lamp_post_count,
            "rtus": rtu_count,
        },
        "progressScope": "downstream_reported_independent_metric",
        "cycle": _first(item, "planCycleName"),
        "batch": _first(item, "planBatch"),
        "createdAt": _first(item, "addTime", "createdAt"),
        "createdBy": _first(item, "addUser", "createdBy"),
    }


def _normalize_inspection_day(item: dict) -> dict:
    return {
        "groupId": _first(item, "groupId", "inspectionDeviceGroupId"),
        "date": _date_part(
            _first(item, "dateTimeStr", "inspectionDate", "dateTime", "date")
        ),
        "plannedDeviceCount": _first(item, "deviceNum", "plannedDeviceNum"),
        "completedDeviceCount": _first(item, "realityNum", "completedDeviceNum"),
        "completionRate": _first(item, "rate", "completionRate"),
    }


def _normalize_inspection_clockin(item: dict) -> dict:
    has_issues = _first(item, "hasIssues", "isProblem", "hasProblem")
    if has_issues in (0, "0", "false", "False"):
        has_issues = False
    elif has_issues in (1, "1", "true", "True"):
        has_issues = True
    return {
        "id": _first(item, "clockinId", "inspectionClockinId", "id"),
        "recordedAt": _first(
            item,
            "clockinTime",
            "clockInTime",
            "inspectionTime",
            "addTime",
        ),
        "recorder": _first(
            item,
            "clockinUserName",
            "clockInUserName",
            "inspectionUserName",
            "userName",
        ),
        "deviceId": _first(item, "deviceId", "facilityId"),
        "deviceCode": _first(
            item,
            "deviceCode",
            "lampPostCode",
            "rtuCode",
            "controlCabinetCode",
        ),
        "deviceName": _first(
            item,
            "deviceName",
            "lampPostName",
            "rtuName",
            "controlCabinetName",
        ),
        "deviceType": _first(item, "deviceTypeName", "facilityTypeName", "deviceType"),
        "position": _first(item, "clockinAddress", "position", "address"),
        "hasIssues": has_issues,
        "issueDescription": _first(
            item,
            "issuesDescription",
            "issueDescription",
            "problemDescription",
            "remark",
        ),
    }


def _inspection_state(item: dict) -> tuple[object, object]:
    raw_code = _first(item, "taskState", "state")
    raw_label = _first(item, "taskStateName")
    state_code = raw_code
    if isinstance(raw_code, str) and raw_code.strip().isdigit():
        state_code = int(raw_code.strip())
    state_labels = {
        1: "待执行",
        2: "执行中",
    }
    return state_code, raw_label or state_labels.get(state_code)


def _normalize_inspection_state_filter(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Smartlight inspection state must be a numeric state code")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        labels = {
            "待执行": 1,
            "执行中": 2,
        }
        if normalized in labels:
            return labels[normalized]
        if normalized.isdigit():
            return int(normalized)
    raise ValueError(
        "Smartlight inspection state must be a numeric state code, 待执行, or 执行中"
    )


def _normalize_leakage(item: dict) -> dict:
    return {
        "id": _first(item, "hisHitchAlarmId", "coplogId", "alarmId", "id"),
        "time": _first(
            item,
            "alarmAddDate",
            "lastDate",
            "alarmTime",
            "operationTime",
            "createTime",
        ),
        "lampPost": _first(item, "lampPostName", "lampPostCode"),
        "lamp": _first(item, "aloneLampName", "lampName", "lampEffectName"),
        "road": _first(item, "streetName", "roadName"),
        "value": _first(
            item,
            "leakageCurrent",
            "electricLeakage",
            "leakageValue",
            "currentValue",
        ),
        "voltage": _first(item, "leakageVoltage", "voltage"),
        "alarmType": _first(item, "hitchDicName", "alarmTypeName"),
        "state": _first(item, "alarmStateName", "alarmState", "state"),
    }


def _count_values(items: list[dict], names: tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = _first(item, *names)
        key = str(value if value not in (None, "") else "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _top_counts(items: list[dict], field: str, limit: int) -> list[dict]:
    counts = Counter(
        str(item[field])
        for item in items
        if item.get(field) not in (None, "")
    )
    return [
        {"value": value, "count": count}
        for value, count in counts.most_common(limit)
    ]


def _daily_counts(items: list[dict], field: str) -> list[dict]:
    counts = Counter(
        date
        for item in items
        if (date := _date_part(item.get(field)))
    )
    return [
        {"date": date, "count": counts[date]}
        for date in sorted(counts)
    ]


def _date_part(value: Any) -> str | None:
    normalized = str(value or "").strip()
    if len(normalized) < 10:
        return None
    candidate = normalized[:10]
    try:
        datetime.strptime(candidate, "%Y-%m-%d")
    except ValueError:
        return None
    return candidate
