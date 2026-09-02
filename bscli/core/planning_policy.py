from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone as fixed_timezone
import hashlib
import json
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


COMPOSED_TASK_POLICY_VERSION = "agentbridge.composed-task-planning-policy.v1"

COMPOSED_TASK_PLANNING_POLICY = {
    "schemaVersion": COMPOSED_TASK_POLICY_VERSION,
    "appliesTo": "authenticated_agentbridge_business_turns",
    "modelContext": "\n".join(
        (
            "AgentBridge durable composed-task policy:",
            "- When a later action or artifact depends on business data that must be read first, call agentbridge_task_plan_catalog and submit exactly one durable plan through agentbridge_task_plan_prepare.",
            "- Keep independent reads, target selection, approvals, batch approvals, and forms whose business content was supplied directly by the user on their existing atomic paths.",
            "- Preserve requested dates, ranges, hours, preview-only intent, and submit intent. If the catalog cannot express a required constraint, stop and explain the gap.",
            "- Use only catalog-declared arguments and bindings. Never call hidden commit/resume tools or emulate a composed task with separate source and sink calls.",
            "- If AgentBridge returns PLAN_REQUIRED, read the catalog and repair the route once. Do not ask the user to rephrase or loop between direct prepare and planning.",
            "- Reuse an active plan's authoritative state instead of creating another plan or repeating successful source steps.",
        )
    ),
    "repair": {
        "errorCode": "PLAN_REQUIRED",
        "action": "prepare_task_plan",
        "maximumAttempts": 1,
    },
}


_DESCRIPTORS: dict[str, dict[str, Any]] = {
    "oa.workflow.pending.list": {
        "roles": ["selector"],
        "selectorContract": {"resourcePointer": "/items"},
    },
    "oa.workflow.done.list": {
        "roles": ["business_source"],
        "sourceContract": {
            "itemsPointer": "/items",
            "coveragePointer": "/coverage",
            "dateArguments": ["start_date", "end_date"],
            "dateBasis": "processed_at",
        },
    },
    "oa.workflow.sent.list": {
        "roles": ["business_source"],
        "sourceContract": {
            "itemsPointer": "/items",
            "coveragePointer": "/coverage",
            "dateArguments": ["start_date", "end_date"],
            "dateBasis": "initiated_at",
        },
    },
    "taihua.work_log.create.prepare": {
        "roles": ["write_sink"],
        "inputProvenance": {
            "content": "user_or_bound_transform",
            "hours": "user_decision",
            "log_date": "user_constraint",
            "project": "user_decision",
        },
    },
}


def planning_descriptor(capability_name: str) -> dict[str, Any] | None:
    value = _DESCRIPTORS.get(capability_name)
    if value is None:
        return None
    return {
        "schemaVersion": "agentbridge.planning-descriptor.v1",
        "capabilityName": capability_name,
        **deepcopy(value),
    }


def planning_role(capability_name: str) -> str | None:
    descriptor = planning_descriptor(capability_name)
    roles = descriptor.get("roles") if descriptor else None
    return roles[0] if isinstance(roles, list) and roles else None


def compile_temporal_constraints(
    proposal: dict[str, Any],
    *,
    accepted_at: str | None,
    default_timezone: str = "Asia/Shanghai",
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Compile v2 temporal constraints without trusting model-supplied anchor time."""

    if proposal.get("schemaVersion") != "agentbridge.task-plan.proposal.v2":
        return deepcopy(proposal), None
    constraints = proposal.get("constraints") or {}
    if not isinstance(constraints, dict):
        raise ValueError("计划 constraints 必须是 JSON 对象。")
    temporal = constraints.get("temporal")
    if temporal is None:
        return deepcopy(proposal), None
    if not isinstance(temporal, dict) or set(temporal) - {"kind", "start", "end"}:
        raise ValueError("计划 temporal 约束不合法。")
    kind = str(temporal.get("kind") or "").strip()
    if kind not in {"previous_day", "previous_calendar_week", "absolute_range"}:
        raise ValueError("计划 temporal.kind 不受支持。")
    if kind != "absolute_range" and (
        temporal.get("start") is not None or temporal.get("end") is not None
    ):
        raise ValueError("相对日期约束不能同时声明 start 或 end。")
    timezone_name = default_timezone
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        if timezone_name == "Asia/Shanghai":
            timezone = fixed_timezone(timedelta(hours=8), name=timezone_name)
        else:
            raise ValueError("当前身份缺少可用时区。") from exc
    if not accepted_at:
        raise ValueError("计划缺少可信的原请求受理时间。")
    anchor = datetime.fromisoformat(str(accepted_at).replace("Z", "+00:00"))
    if anchor.tzinfo is None:
        raise ValueError("原请求受理时间必须包含时区。")
    local_day = anchor.astimezone(timezone).date()
    if kind == "previous_day":
        start = end = local_day - timedelta(days=1)
    elif kind == "previous_calendar_week":
        this_monday = local_day - timedelta(days=local_day.weekday())
        start = this_monday - timedelta(days=7)
        end = this_monday - timedelta(days=1)
    else:
        try:
            start = datetime.fromisoformat(str(temporal.get("start"))).date()
            end = datetime.fromisoformat(str(temporal.get("end"))).date()
        except (TypeError, ValueError) as exc:
            raise ValueError("绝对日期范围必须包含有效的 start 和 end。") from exc
        if start > end:
            raise ValueError("绝对日期范围 start 不能晚于 end。")

    compiled = deepcopy(proposal)
    start_text = start.isoformat()
    end_text = end.isoformat()
    date_basis: dict[str, str] = {}
    for step in compiled.get("steps") or []:
        if not isinstance(step, dict) or step.get("kind") != "capability":
            continue
        descriptor = planning_descriptor(str(step.get("capabilityName") or ""))
        source = descriptor.get("sourceContract") if descriptor else None
        date_arguments = source.get("dateArguments") if isinstance(source, dict) else None
        if date_arguments != ["start_date", "end_date"]:
            continue
        arguments = step.setdefault("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("计划步骤 arguments 必须是 JSON 对象。")
        for name, expected in (("start_date", start_text), ("end_date", end_text)):
            supplied = arguments.get(name)
            if supplied is not None and supplied != expected:
                raise ValueError(f"步骤 {step.get('stepKey')} 的 {name} 与时间约束不一致。")
            arguments[name] = expected
        date_basis[str(step.get("stepKey") or "")] = str(source.get("dateBasis") or "")

    context = {
        "schemaVersion": "agentbridge.request-temporal-context.v1",
        "acceptedAt": anchor.isoformat(),
        "timeZone": timezone_name,
        "locale": "zh-CN",
        "constraint": deepcopy(temporal),
        "absoluteRange": {"start": start_text, "end": end_text},
        "stepDateBasis": date_basis,
    }
    return compiled, context


def authority_snapshot(
    identity: dict[str, Any], *, required_scopes: list[str]
) -> dict[str, Any]:
    scopes = sorted(str(value) for value in identity.get("scopes") or [])
    return {
        "schemaVersion": "agentbridge.execution-authority-snapshot.v1",
        "tokenId": str(identity.get("token_id") or ""),
        "userSubject": str(identity.get("user_subject") or ""),
        "expiresAt": identity.get("expires_at"),
        "scopeHash": "sha256:"
        + hashlib.sha256(
            json.dumps(scopes, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "requiredScopes": sorted(str(value) for value in required_scopes),
    }
