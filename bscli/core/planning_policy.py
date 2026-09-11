from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone as fixed_timezone
import hashlib
import json
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bscli.core.query_contracts import oa_history_query_contract


COMPOSED_TASK_POLICY_VERSION = "agentbridge.composed-task-planning-policy.v1"

COMPOSED_TASK_PLANNING_POLICY = {
    "schemaVersion": COMPOSED_TASK_POLICY_VERSION,
    "appliesTo": "authenticated_agentbridge_business_turns",
    "modelContext": "\n".join(
        (
            "AgentBridge durable composed-task policy:",
            "- 泰华日报类型与日期范围不能混淆：用户说‘周报’时，先澄清是 WEEKLY 周报还是一周 DAILY 日报汇总，不调用分析工具猜测。明确 WEEKLY 时说明数据库分析仅支持 DAILY；明确‘一周日报汇总’才按 DAILY 查询。不得把日报结果标为周报。",
            "- 用户指定‘数据库分析/数据库统计’时，保持该数据路径：当前只支持本人 DAILY 汇总、历史结果、比较和 CSV。对他人日志、正文或不支持的类型，说明限制；不得改用 taihua_work_log_team_list 或普通日志 API。若用户另行明确选择普通日志查询，再按其现有授权执行。",
            "- 面向用户用简短中文解释限制，不输出 catalog、transform 等内部规则。只在确实需要用户登录、填写或授权时要求操作；查询条件核验失败应说明查询停止。下载入口过期可用仍有效的来源 result_id 重新导出，不等于分析结果过期。",
            "- When a later action or artifact depends on business data that must be read first, call agentbridge_task_plan_catalog and submit exactly one durable plan through agentbridge_task_plan_prepare, except for the protected personal-analytics export route below.",
            "- Personal analytics for ONE date window, including its CSV export, uses the atomic protected-result route: taihua_analytics_personal_summary(group_by=day) -> taihua_analytics_report_export(result_id returned by summary) -> taihua_analytics_report_download(report_id returned by export). This is an authorized read/report delivery chain, not a composed business plan. Do not put these calls in a durable plan or invent a CSV attachment transform. Deliver the authenticated file through the host's existing attachment mechanism; do not expose base64 or invent a download URL.",
            "- Comparing TWO personal-analytics windows uses exactly two summary capability steps and taihua_personal_compare.v1 in a v2 durable plan, followed by taihua_analytics_result_get. CSV export accepts a single daily summary, not the comparison result; if requested, export the underlying daily source summaries separately through the protected-result route.",
            "- Combining, comparing, or summarizing two or more business sources or distinct business collections is a composed task even when the user only wants a preview. Use one durable plan; do not read them separately and synthesize the answer in model text.",
            "- A read-only multi-source plan must end with a catalog-declared result-projection transform. Bind every business source into that transform; source capability steps alone are not a complete plan.",
            "- A read-only plan stores internal orchestration state, but does not write to OA or another business system. A user request such as 'do not write to any system' permits this internal read-only route; exclude all write sinks and preserve preview-only intent. Do not refuse a read-only plan merely because it is durable. Business writes still require their own authorization.",
            "- Keep independent reads, target selection, approvals, batch approvals, and forms whose business content was supplied directly by the user on their existing atomic paths.",
            "- For multiple/all OA pending actions, use oa_workflow_pending_batch_prepare once. It persists the selected list and advances one separately authorized item at a time. For above/selected items pass their exact observed affair_ids; for a current category use workflow_types and optional title keyword. Never call only the first singular prepare and promise to continue later. Batch overflow or unsupported items must be explained, not silently truncated or bypassed via singular tools.",
            "- Preserve requested dates, ranges, hours, preview-only intent, and submit intent. If the catalog cannot express a required constraint, stop and explain the gap.",
            "- Use each business source's queryContract: send user-requested date bounds to the source capability, never fetch an unfiltered first page and filter it in model text. Done dates mean the current user's processing date; Sent dates mean initiation date. For complete summaries use the declared maximum limit and inspect coverage; never infer completeness from row order or a 50-row page.",
            "- Use only catalog-declared arguments and bindings. Never call hidden commit/resume tools or emulate a composed task with separate source and sink calls.",
            "- If AgentBridge returns PLAN_REQUIRED, read the catalog and repair the route once. Do not ask the user to rephrase or loop between direct prepare and planning.",
            "- If plan preparation or execution fails, report that authoritative plan failure. Do not query operation history to reconstruct and present a business result outside the plan.",
            "- PLAN_SOURCE_INCOMPLETE means safely stopped before the write sink. Report the incomplete sources and no business submission. Do not retry atomic source/sink tools in the same turn, ask to authorize a partial submission, or claim the business write failed.",
            "- For protected_analytics plan projections, use taihua_analytics_result_get with the projected result_id; the plan contains references only. Never infer metrics from references. For other successful plans, answer from plan.resultProjection.result. Do not query source operations again; the projection is the bounded authoritative result for the user.",
            "- Reuse an active plan's authoritative state instead of creating another plan or repeating successful source steps.",
            "- To cancel an unsubmitted task/card, use agentbridge_task_cancel with the actual task_id. It routes durable plans and ordinary cards safely. agentbridge_task_plan_cancel requires a real plan_id, not a task ID. Closing or ignoring a card is not cancellation. This does not revoke any submitted business workflow.",
        )
    ),
    "repair": {
        "errorCode": "PLAN_REQUIRED",
        "action": "prepare_task_plan",
        "maximumAttempts": 1,
    },
}


_DESCRIPTORS: dict[str, dict[str, Any]] = {
    "taihua.analytics.personal.summary": {
        "mcpToolName": "taihua_analytics_personal_summary", "roles": ["business_source"],
        "sourceContract": {"protectedReference": True, "windowArguments": ["start_date", "end_date_exclusive"],
                           "dateBasis": "log_date", "maximumDays": 7},
    },
    "oa.workflow.pending.list": {
        "roles": ["selector"],
        "selectorContract": {"resourcePointer": "/items"},
    },
    "oa.workflow.done.list": {
        "mcpToolName": "oa_workflow_done_list",
        "roles": ["business_source"],
        "sourceContract": {
            "itemsPointer": "/items",
            "coveragePointer": "/coverage",
            "dateArguments": ["start_date", "end_date"],
            "dateBasis": "processed_at",
            "queryContract": oa_history_query_contract("done"),
        },
    },
    "oa.workflow.sent.list": {
        "mcpToolName": "oa_workflow_sent_list",
        "roles": ["business_source"],
        "sourceContract": {
            "itemsPointer": "/items",
            "coveragePointer": "/coverage",
            "dateArguments": ["start_date", "end_date"],
            "dateBasis": "initiated_at",
            "queryContract": oa_history_query_contract("sent"),
        },
    },
    "taihua.work_log.create.prepare": {
        "mcpToolName": "taihua_work_log_create_prepare",
        "roles": ["write_sink"],
        "inputProvenance": {
            "content": "user_or_bound_transform",
            "hours": "user_decision",
            "log_date": "user_constraint",
            "project": "user_decision",
        },
    },
}


def planning_capability_for_tool(tool_name: str | None) -> str | None:
    return next((name for name, descriptor in _DESCRIPTORS.items()
                 if descriptor.get("mcpToolName") == tool_name and tool_name), None)


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
    if kind not in {"previous_day", "day_before_yesterday", "previous_calendar_week", "absolute_range"}:
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
    elif kind == "day_before_yesterday":
        start = end = local_day - timedelta(days=2)
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
