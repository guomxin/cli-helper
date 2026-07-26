from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

from bscli.adapters.base import (
    AdapterAuthenticationRejected,
    AdapterBusinessRuleRejected,
    AdapterLoginContractMismatch,
    AdapterLoginRequired,
    AdapterSessionCheckUnavailable,
    AdapterUnsupportedAuthMethod,
)
from bscli.core.capability import CapabilityRegistry, CapabilitySpec


TAIHUA_SYSTEM_ID = "taihua"
TAIHUA_ADAPTER_ID = "taihua-central"
TAIHUA_SYSTEM_NAME = "泰华日志系统"

TAIHUA_MY_LOGS_CAPABILITY = "taihua.work_log.my.list"
TAIHUA_TEAM_LOGS_CAPABILITY = "taihua.work_log.team.list"
TAIHUA_PROJECT_SEARCH_CAPABILITY = "taihua.project.search"
TAIHUA_WORK_LOG_CREATE_PREPARE_CAPABILITY = "taihua.work_log.create.prepare"
TAIHUA_WORK_LOG_CREATE_CAPABILITY = "taihua.work_log.create"

_MY_LOGS_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "start_date": {"type": "string"},
        "end_date": {"type": "string"},
        "keyword": {"type": "string"},
        "limit": {"type": "integer"},
    },
    "additionalProperties": False,
}

_TEAM_LOGS_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "keyword": {"type": "string"},
        "page": {"type": "integer"},
        "size": {"type": "integer"},
        "view_mode": {"type": "string"},
        "dept_id": {"type": "integer"},
        "member_id": {"type": "integer"},
    },
    "additionalProperties": False,
}

_PROJECT_SEARCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "keyword": {"type": "string"},
        "limit": {"type": "integer"},
    },
    "additionalProperties": False,
}

TAIHUA_WORK_LOG_CREATE_PREPARE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "log_date": {"type": "string"},
        "hours": {"type": "number"},
        "project": {"type": "string"},
        "content": {"type": "string"},
        "input_submission_id": {"type": "string"},
    },
    "additionalProperties": False,
}

TAIHUA_WORK_LOG_CREATE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"authorization_id": {"type": "string"}},
    "required": ["authorization_id"],
    "additionalProperties": False,
}

TAIHUA_WORK_LOG_FIELD_CARD_SCHEMA = {
    "schema_version": "agentbridge.taihua_work_log_fields.v1",
    "title": "填写工作日志",
    "system": TAIHUA_SYSTEM_NAME,
    "effect": "创建并正式提交一条个人工作日志",
    "submit_label": "提交字段",
    "notice": "字段提交后还需单独授权；授权前不会向泰华日志系统写入任何内容。",
    "fields": [
        {
            "name": "log_date",
            "label": "日志日期",
            "control": "date",
            "required": True,
        },
        {
            "name": "hours",
            "label": "工时",
            "control": "number",
            "required": True,
            "minimum": 0.5,
            "maximum": 16,
            "step": 0.5,
        },
        {
            "name": "project",
            "label": "所属项目名称或编码（可选）",
            "control": "text",
            "required": False,
            "max_length": 255,
            "autocomplete": "off",
        },
        {
            "name": "content",
            "label": "日志内容",
            "control": "textarea",
            "required": True,
            "max_length": 4000,
            "rows": 6,
        },
    ],
}


class TaihuaLoginRequired(AdapterLoginRequired):
    pass


class TaihuaAuthenticationRejected(AdapterAuthenticationRejected):
    pass


class TaihuaLoginContractMismatch(AdapterLoginContractMismatch):
    pass


class TaihuaUnsupportedAuthMethod(AdapterUnsupportedAuthMethod):
    pass


class TaihuaSessionCheckUnavailable(AdapterSessionCheckUnavailable):
    pass


class TaihuaWorkLogContractMismatch(RuntimeError):
    pass


class TaihuaWorkLogOutcomeUnknown(RuntimeError):
    pass


class TaihuaBusinessRuleRejected(AdapterBusinessRuleRejected):
    error_code = "TAIHUA_BUSINESS_RULE_REJECTED"


class TaihuaCentralAdapter:
    def __init__(self, *, base_url: str) -> None:
        parsed = urlparse(str(base_url or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Taihua base URL must use http(s)")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Taihua base URL is invalid")
        self.base_url = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}/"
        self.origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"

    def authentication_contract(self) -> dict:
        return {
            "system_id": TAIHUA_SYSTEM_ID,
            "system_name": TAIHUA_SYSTEM_NAME,
            "origin": self.origin,
            "page_fingerprint": "taihua-password-login-v1",
            "fields": [
                {
                    "name": "username",
                    "label": "泰华日志系统用户名",
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
            ],
        }

    def authenticate(
        self,
        worker,
        credentials: dict,
        *,
        timeout_seconds: float,
    ) -> dict:
        response = worker.request(
            "POST",
            self._url("/api/authenticates/basic"),
            body={
                "username": str(credentials.get("username") or "").strip(),
                "password": str(credentials.get("password") or ""),
            },
            timeout_seconds=timeout_seconds,
        )
        if response["status"] in {401, 403}:
            raise TaihuaAuthenticationRejected(_response_message(response))
        if response["status"] < 200 or response["status"] >= 300:
            raise TaihuaSessionCheckUnavailable(_response_message(response))
        payload = _json_object(response, authentication=True)
        if payload.get("mustChangePassword"):
            raise TaihuaUnsupportedAuthMethod(
                "首次登录必须先在泰华日志系统中修改初始密码。"
            )
        token = str(payload.get("token") or "").strip()
        refresh_token = str(payload.get("refreshToken") or "").strip()
        if not token or not refresh_token:
            raise TaihuaLoginContractMismatch("登录响应缺少访问令牌或刷新令牌。")
        worker.set_http_state(_token_state(payload))
        principal = self._authorized_json(worker, "GET", "/api/users/principal")
        observed = str(principal.get("fullname") or principal.get("username") or "").strip()
        if not observed:
            raise TaihuaLoginContractMismatch("登录成功后无法核验实际用户身份。")
        return {
            "observed_principal_ref": observed,
            "principal": _normalize_principal(principal),
            "transport": "central_http_token",
        }

    def probe_session(self, worker) -> dict:
        principal = self._authorized_json(worker, "GET", "/api/users/principal")
        observed = str(principal.get("fullname") or principal.get("username") or "").strip()
        if not observed:
            raise TaihuaLoginContractMismatch("会话检查响应缺少用户身份。")
        return {
            "authenticated": True,
            "observed_principal_ref": observed,
            "principal": _normalize_principal(principal),
            "template_count": None,
            "transport": "central_http_token",
            "browser_bridge_used": False,
        }

    def invoke_capability(self, capability_name: str, worker, arguments: dict) -> dict:
        if capability_name == TAIHUA_MY_LOGS_CAPABILITY:
            return self.list_my_logs(worker, arguments)
        if capability_name == TAIHUA_TEAM_LOGS_CAPABILITY:
            return self.list_team_logs(worker, arguments)
        if capability_name == TAIHUA_PROJECT_SEARCH_CAPABILITY:
            return self.search_projects(worker, arguments)
        raise KeyError(f"unsupported Taihua capability: {capability_name}")

    def list_my_logs(self, worker, arguments: dict) -> dict:
        today = date.today().isoformat()
        start_date = _normalize_date(arguments.get("start_date") or today, "start_date")
        end_date = _normalize_date(arguments.get("end_date") or start_date, "end_date")
        if end_date < start_date:
            raise ValueError("end_date must not be earlier than start_date")
        params: dict[str, Any] = {"startDate": start_date, "endDate": end_date}
        keyword = str(arguments.get("keyword") or "").strip()
        if keyword:
            params["keyword"] = keyword
        payload = self._authorized_json(
            worker,
            "GET",
            f"/api/work-logs/range?{urlencode(params)}",
        )
        if not isinstance(payload, list):
            raise TaihuaSessionCheckUnavailable("个人日志接口未返回列表。")
        limit = _bounded_int(arguments.get("limit"), default=100, minimum=1, maximum=500)
        items = [_normalize_work_log(item) for item in payload[:limit] if isinstance(item, dict)]
        return {
            "count": len(items),
            "startDate": start_date,
            "endDate": end_date,
            "items": items,
        }

    def list_team_logs(self, worker, arguments: dict) -> dict:
        params: dict[str, Any] = {
            "page": _bounded_int(arguments.get("page"), default=1, minimum=1, maximum=10000),
            "size": _bounded_int(arguments.get("size"), default=20, minimum=1, maximum=100),
            "sort": ["createdAt,desc", "id,desc"],
            "viewMode": str(arguments.get("view_mode") or "submittedAt"),
        }
        for argument_name, parameter_name in (
            ("keyword", "keyword"),
            ("dept_id", "deptId"),
            ("member_id", "memberId"),
        ):
            value = arguments.get(argument_name)
            if value not in (None, ""):
                params[parameter_name] = value
        query = urlencode(params, doseq=True)
        payload = self._authorized_json(worker, "GET", f"/api/work-logs/team?{query}")
        content, total = _page_content(payload, "团队日志")
        return {
            "count": len(content),
            "total": total,
            "page": params["page"],
            "size": params["size"],
            "items": [_normalize_work_log(item) for item in content],
        }

    def search_projects(self, worker, arguments: dict) -> dict:
        keyword = str(arguments.get("keyword") or "").strip()
        limit = _bounded_int(arguments.get("limit"), default=20, minimum=1, maximum=50)
        payload = self._authorized_json(
            worker,
            "GET",
            f"/api/projects?{urlencode({'keyword': keyword, 'limit': limit})}",
        )
        if not isinstance(payload, list):
            raise TaihuaSessionCheckUnavailable("项目接口未返回列表。")
        items = [_normalize_project(item) for item in payload if isinstance(item, dict)]
        return {"count": len(items), "keyword": keyword, "items": items}

    def project_candidates(self, worker, query: str, *, limit: int = 20) -> list[dict]:
        return self.search_projects(worker, {"keyword": query, "limit": limit})["items"]

    def work_logs_for_date(self, worker, log_date: str) -> list[dict]:
        return self.list_my_logs(
            worker,
            {"start_date": log_date, "end_date": log_date, "limit": 500},
        )["items"]

    def create_work_log(self, worker, payload: dict) -> dict:
        return self._authorized_json(
            worker,
            "POST",
            "/api/work-logs",
            body=payload,
            business_write=True,
        )

    def _authorized_json(
        self,
        worker,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        business_write: bool = False,
    ):
        state = worker.get_http_state()
        authorization = str(state.get("authorization") or "")
        if not authorization:
            raise TaihuaLoginRequired("泰华日志系统会话不存在。")
        response = worker.request(
            method,
            self._url(path),
            headers={"Authorization": authorization, "X-Sisyphus-Client": "agentbridge"},
            body=body,
        )
        if _token_invalid(response):
            self._refresh(worker)
            state = worker.get_http_state()
            response = worker.request(
                method,
                self._url(path),
                headers={
                    "Authorization": str(state["authorization"]),
                    "X-Sisyphus-Client": "agentbridge",
                },
                body=body,
            )
        if response["status"] in {401, 403} or _token_invalid(response):
            raise TaihuaLoginRequired("泰华日志系统登录已过期。")
        if response["status"] < 200 or response["status"] >= 300:
            message = _response_message(response)
            if business_write and response["status"] in {400, 409, 422}:
                raise TaihuaBusinessRuleRejected(message)
            if business_write:
                raise TaihuaWorkLogOutcomeUnknown(
                    f"泰华工作日志提交返回异常状态（HTTP {response['status']}）：{message}"
                )
            raise TaihuaSessionCheckUnavailable(message)
        if response["json"] is None:
            if business_write:
                return {}
            raise TaihuaSessionCheckUnavailable(
                f"泰华接口未返回 JSON（HTTP {response['status']}）。"
            )
        return response["json"]

    def _refresh(self, worker) -> None:
        state = worker.get_http_state()
        refresh_token = str(state.get("refresh_token") or "")
        if not refresh_token:
            raise TaihuaLoginRequired("泰华日志系统刷新令牌不存在。")
        response = worker.request(
            "POST",
            self._url("/api/authenticates/refresh"),
            headers={"X-Sisyphus-Client": "agentbridge"},
            body={"refreshToken": refresh_token},
        )
        if response["status"] in {401, 403} or _token_invalid(response):
            raise TaihuaLoginRequired("泰华日志系统刷新令牌已失效。")
        if response["status"] < 200 or response["status"] >= 300:
            raise TaihuaSessionCheckUnavailable(
                f"泰华日志系统暂时无法刷新会话（HTTP {response['status']}）。"
            )
        payload = _json_object(response)
        if not payload.get("token") or not payload.get("refreshToken"):
            raise TaihuaSessionCheckUnavailable("泰华日志系统刷新响应无效。")
        worker.set_http_state(_token_state(payload))

    def _url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip("/"))


def build_taihua_capability_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for spec in (
        CapabilitySpec(
            name=TAIHUA_MY_LOGS_CAPABILITY,
            version="0.1.0",
            description="List the authenticated user's Taihua work logs by date range.",
            input_schema=_MY_LOGS_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="read",
            adapter=TAIHUA_ADAPTER_ID,
            workflow="taihua-my-work-logs-v1",
        ),
        CapabilitySpec(
            name=TAIHUA_TEAM_LOGS_CAPABILITY,
            version="0.1.0",
            description="List Taihua team work logs within the authenticated user's scope.",
            input_schema=_TEAM_LOGS_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="read",
            adapter=TAIHUA_ADAPTER_ID,
            workflow="taihua-team-work-logs-v1",
        ),
        CapabilitySpec(
            name=TAIHUA_PROJECT_SEARCH_CAPABILITY,
            version="0.1.0",
            description="Search projects available to the authenticated Taihua user.",
            input_schema=_PROJECT_SEARCH_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="read",
            adapter=TAIHUA_ADAPTER_ID,
            workflow="taihua-project-search-v1",
        ),
        CapabilitySpec(
            name=TAIHUA_WORK_LOG_CREATE_PREPARE_CAPABILITY,
            version="0.1.0",
            description="Collect and freeze one Taihua work-log submission for approval.",
            input_schema=TAIHUA_WORK_LOG_CREATE_PREPARE_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="controlled_write",
            adapter=TAIHUA_ADAPTER_ID,
            workflow="taihua-work-log-create-prepare-v1",
        ),
        CapabilitySpec(
            name=TAIHUA_WORK_LOG_CREATE_CAPABILITY,
            version="0.1.0",
            description="Create one approved Taihua work log and verify it by readback.",
            input_schema=TAIHUA_WORK_LOG_CREATE_INPUT_SCHEMA,
            output_schema={"type": "object"},
            effect="controlled_write",
            adapter=TAIHUA_ADAPTER_ID,
            workflow="taihua-work-log-create-v1",
        ),
    ):
        registry.register(spec)
    return registry


def prepare_taihua_work_log_create(adapter, worker, arguments: dict) -> dict:
    inputs = normalize_work_log_inputs(arguments)
    project = _resolve_project(adapter, worker, inputs.pop("project"))
    if project is not None:
        inputs["project_id"] = int(project["id"])
        inputs["project_name"] = project["name"]
        inputs["project_code"] = project.get("code")
    existing = adapter.work_logs_for_date(worker, inputs["log_date"])
    if _matching_work_log(existing, inputs) is not None:
        raise TaihuaBusinessRuleRejected(
            "已存在日期、工时、项目和内容完全相同的个人日志，已停止重复创建。"
        )
    return {
        "plan": {
            "schema_version": "agentbridge.taihua_work_log_create_plan.v1",
            "business_intent": "create_work_log",
            "exact_input": inputs,
            "preconditions": {
                "same_date_log_count": len(existing),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            },
            "expected_effect": {
                "kind": "create_work_log",
                "log_date": inputs["log_date"],
            },
        },
        "summary": {
            "title": "提交泰华工作日志",
            "effect": "正式创建一条个人工作日志",
            "fields": [
                {"label": "日志日期", "value": inputs["log_date"]},
                {"label": "工时", "value": f"{inputs['hours']} 小时"},
                {
                    "label": "所属项目",
                    "value": inputs.get("project_name") or "未关联项目",
                },
                {"label": "日志内容", "value": inputs["content"]},
            ],
        },
    }


def commit_taihua_work_log_create(
    adapter,
    worker,
    plan: dict,
    *,
    enter_commit_boundary,
) -> dict:
    if plan.get("schema_version") != "agentbridge.taihua_work_log_create_plan.v1":
        raise TaihuaWorkLogContractMismatch("泰华日志写入计划版本不受支持。")
    inputs = deepcopy(plan.get("exact_input"))
    if not isinstance(inputs, dict):
        raise TaihuaWorkLogContractMismatch("泰华日志写入计划缺少冻结字段。")
    existing = adapter.work_logs_for_date(worker, inputs["log_date"])
    if _matching_work_log(existing, inputs) is not None:
        raise TaihuaBusinessRuleRejected(
            "授权后发现相同日志已存在，本次计划已停止以避免重复提交。"
        )
    payload = {
        "logDate": inputs["log_date"],
        "typeCode": "DAILY",
        "hours": inputs["hours"],
        "content": inputs["content"],
    }
    if inputs.get("project_id") is not None:
        payload["projectId"] = inputs["project_id"]
    enter_commit_boundary()
    created = adapter.create_work_log(worker, payload)
    created_id = str(created.get("id") or "") if isinstance(created, dict) else ""
    readback = adapter.work_logs_for_date(worker, inputs["log_date"])
    matched = _matching_work_log(readback, inputs, created_id=created_id)
    if matched is None:
        raise TaihuaWorkLogOutcomeUnknown(
            "泰华接口已接受日志提交，但权威回读未找到对应记录。"
        )
    return {
        "status": "created",
        "workLog": matched,
        "verification": {
            "method": "GET /api/work-logs/range",
            "matched": True,
        },
    }


def _matching_work_log(
    items: list[dict],
    inputs: dict,
    *,
    created_id: str = "",
) -> dict | None:
    expected_project_id = str(inputs.get("project_id") or "")
    for item in items:
        if created_id and str(item.get("id") or "") == created_id:
            return item
        try:
            same_hours = float(item.get("hours") or 0) == float(inputs["hours"])
        except (TypeError, ValueError):
            same_hours = False
        if (
            item.get("logDate") == inputs["log_date"]
            and item.get("content") == inputs["content"]
            and same_hours
            and str(item.get("projectId") or "") == expected_project_id
        ):
            return item
    return None


def normalize_work_log_inputs(arguments: dict) -> dict:
    log_date = _normalize_date(arguments.get("log_date"), "log_date")
    hours = arguments.get("hours")
    if isinstance(hours, bool) or not isinstance(hours, (int, float)):
        raise TaihuaWorkLogContractMismatch("工时必须是数字。")
    hours = float(hours)
    if hours < 0.5 or hours > 16 or round(hours * 2) != hours * 2:
        raise TaihuaWorkLogContractMismatch("工时必须是 0.5 至 16 之间的半小时倍数。")
    content = str(arguments.get("content") or "").strip()
    if not content or len(content) > 4000:
        raise TaihuaWorkLogContractMismatch("日志内容不能为空且不能超过 4000 个字符。")
    project = str(arguments.get("project") or "").strip()
    if len(project) > 255:
        raise TaihuaWorkLogContractMismatch("项目名称或编码不能超过 255 个字符。")
    return {
        "log_date": log_date,
        "hours": int(hours) if hours.is_integer() else hours,
        "project": project,
        "content": content,
    }


def _resolve_project(adapter, worker, query: str) -> dict | None:
    if not query:
        return None
    candidates = adapter.project_candidates(worker, query, limit=20)
    normalized = query.casefold()
    exact = [
        item
        for item in candidates
        if normalized in {
            str(item.get("name") or "").casefold(),
            str(item.get("code") or "").casefold(),
        }
    ]
    if len(exact) == 1:
        return exact[0]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise TaihuaBusinessRuleRejected(f"没有找到项目：{query}")
    labels = "；".join(
        f"{item.get('code') or '-'} {item.get('name') or '-'}"
        for item in candidates[:5]
    )
    raise TaihuaBusinessRuleRejected(f"项目匹配不唯一，请使用准确名称或编码：{labels}")


def _normalize_date(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field_name} must use YYYY-MM-DD") from exc


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("pagination value must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"pagination value must be between {minimum} and {maximum}")
    return value


def _json_object(response: dict, *, authentication: bool = False) -> dict:
    payload = response.get("json")
    if isinstance(payload, dict):
        return payload
    error = (
        TaihuaLoginContractMismatch
        if authentication
        else TaihuaSessionCheckUnavailable
    )
    raise error(f"泰华接口未返回 JSON 对象（HTTP {response.get('status')}）。")


def _token_state(payload: dict) -> dict:
    return {
        "authorization": f"Bearer {str(payload['token']).strip()}",
        "refresh_token": str(payload["refreshToken"]).strip(),
        "token_expired_at": payload.get("tokenExpired"),
        "refresh_token_expired_at": payload.get("refreshTokenExpired"),
    }


def _token_invalid(response: dict) -> bool:
    if response.get("status") in {401, 403}:
        return True
    payload = response.get("json")
    return isinstance(payload, dict) and payload.get("code") == "A0230"


def _response_message(response: dict) -> str:
    payload = response.get("json")
    if isinstance(payload, dict):
        for key in ("message", "msg", "detail", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    text = str(response.get("text") or "").strip()
    if text:
        return text[:500]
    return f"泰华接口调用失败（HTTP {response.get('status')}）。"


def _normalize_principal(payload: dict) -> dict:
    dept = payload.get("dept") if isinstance(payload.get("dept"), dict) else {}
    return {
        "id": str(payload.get("id") or ""),
        "username": str(payload.get("username") or ""),
        "fullname": str(payload.get("fullname") or ""),
        "department": str(dept.get("name") or ""),
        "roles": [
            str(item.get("name") or "")
            for item in payload.get("roles") or []
            if isinstance(item, dict)
        ],
    }


def _normalize_work_log(item: dict) -> dict:
    return {
        "id": str(item.get("id") or ""),
        "logDate": item.get("logDate"),
        "typeCode": item.get("typeCode"),
        "hours": item.get("hours"),
        "content": item.get("content"),
        "projectId": str(item.get("projectId") or "") or None,
        "projectName": item.get("projectName"),
        "projectCode": item.get("projectCode"),
        "userId": str(item.get("userId") or "") or None,
        "username": item.get("username"),
        "fullname": item.get("fullname"),
        "department": item.get("deptName") or item.get("departmentName"),
        "createdAt": item.get("createdAt"),
        "updatedAt": item.get("updatedAt"),
        "commentCount": item.get("commentCount"),
        "attachments": [
            {
                "id": str(attachment.get("id") or ""),
                "name": attachment.get("originalFileName") or attachment.get("name"),
            }
            for attachment in item.get("attachments") or []
            if isinstance(attachment, dict)
        ],
    }


def _normalize_project(item: dict) -> dict:
    return {
        "id": str(item.get("id") or ""),
        "name": item.get("name"),
        "code": item.get("code"),
        "status": item.get("status"),
    }


def _page_content(payload: Any, label: str) -> tuple[list[dict], int]:
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
        raise TaihuaSessionCheckUnavailable(f"{label}接口未返回分页列表。")
    content = [item for item in payload["content"] if isinstance(item, dict)]
    total = payload.get("totalElements")
    return content, int(total) if isinstance(total, int) else len(content)
