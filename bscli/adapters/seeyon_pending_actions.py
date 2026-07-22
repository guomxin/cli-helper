from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Callable


EFFICIENCY_DATA_APPROVAL_PREPARE_CAPABILITY = (
    "oa.efficiency_data.approval.prepare"
)
EFFICIENCY_DATA_APPROVE_CAPABILITY = "oa.efficiency_data.approve"
TRAVEL_EXPENSE_APPROVAL_PREPARE_CAPABILITY = (
    "oa.travel_expense.approval.prepare"
)
TRAVEL_EXPENSE_APPROVE_CAPABILITY = "oa.travel_expense.approve"
WEEKLY_REPORT_ACKNOWLEDGEMENT_PREPARE_CAPABILITY = (
    "oa.weekly_report.acknowledgement.prepare"
)
WEEKLY_REPORT_ACKNOWLEDGE_CAPABILITY = "oa.weekly_report.acknowledge"
STANDARD_COLLABORATION_APPROVAL_PREPARE_CAPABILITY = (
    "oa.standard_collaboration.approval.prepare"
)
STANDARD_COLLABORATION_APPROVE_CAPABILITY = (
    "oa.standard_collaboration.approve"
)

PENDING_ACTION_PREPARE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "affair_id": {"type": "string"},
        "opinion": {"type": "string", "maxLength": 1000},
        "input_submission_id": {"type": "string"},
    },
    "required": ["affair_id"],
    "additionalProperties": False,
}

PENDING_ACTION_COMMIT_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"authorization_id": {"type": "string"}},
    "required": ["authorization_id"],
    "additionalProperties": False,
}


def _opinion_card(
    *,
    schema_version: str,
    title: str,
    effect: str,
    submit_label: str,
) -> dict:
    return {
        "schema_version": schema_version,
        "title": title,
        "system": "致远 OA",
        "effect": effect,
        "submit_label": submit_label,
        "notice": (
            "提交意见后还需在独立授权卡中核对精确事项；"
            "授权前不会处理待办。"
        ),
        "fields": [
            {
                "name": "opinion",
                "label": "处理意见",
                "control": "textarea",
                "required": True,
                "max_length": 1000,
                "rows": 4,
            }
        ],
    }


EFFICIENCY_DATA_APPROVAL_FIELD_CARD_SCHEMA = _opinion_card(
    schema_version="agentbridge.oa_efficiency_data_approval_fields.v1",
    title="填写效能数据审批意见",
    effect="审批通过一条效能数据流程",
    submit_label="提交审批意见",
)
TRAVEL_EXPENSE_APPROVAL_FIELD_CARD_SCHEMA = _opinion_card(
    schema_version="agentbridge.oa_travel_expense_approval_fields.v1",
    title="填写差旅费审批意见",
    effect="审批通过一条差旅费审批报销单",
    submit_label="提交审批意见",
)
WEEKLY_REPORT_ACKNOWLEDGEMENT_FIELD_CARD_SCHEMA = _opinion_card(
    schema_version="agentbridge.oa_weekly_report_acknowledgement_fields.v1",
    title="填写周报阅办意见",
    effect="阅办一条周报发送流程",
    submit_label="提交阅办意见",
)
STANDARD_COLLABORATION_APPROVAL_FIELD_CARD_SCHEMA = _opinion_card(
    schema_version="agentbridge.oa_standard_collaboration_approval_fields.v1",
    title="填写普通协同事项审批意见",
    effect="审批通过一条普通协同事项",
    submit_label="提交审批意见",
)


_PROFILES = {
    "efficiency_data": {
        "prepare_capability": EFFICIENCY_DATA_APPROVAL_PREPARE_CAPABILITY,
        "commit_capability": EFFICIENCY_DATA_APPROVE_CAPABILITY,
        "contract_version": "seeyon-efficiency-data-approval-v1",
        "plan_schema": "agentbridge.oa_efficiency_data_approval_plan.v1",
        "result_schema": "agentbridge.oa_efficiency_data_approval_result.v1",
        "business_intent": "approve_efficiency_data",
        "title_rule": {"kind": "contains", "value": "效能数据"},
        "required_fields": {"接收人"},
        "allowed_fields": {"接收人"},
        "template_id": "",
        "form_app_id": "",
        "node_policies": {"approve", "审批"},
        "node_policy_names": {"审批"},
        "action_kind": "approval",
        "action_display": "审批通过",
        "summary_title": "审批效能数据流程",
        "summary_effect": "审批通过后该效能数据事项将离开待办列表",
        "authorize_label": "授权审批通过",
    },
    "travel_expense": {
        "prepare_capability": TRAVEL_EXPENSE_APPROVAL_PREPARE_CAPABILITY,
        "commit_capability": TRAVEL_EXPENSE_APPROVE_CAPABILITY,
        "contract_version": "seeyon-travel-expense-approval-v1",
        "plan_schema": "agentbridge.oa_travel_expense_approval_plan.v1",
        "result_schema": "agentbridge.oa_travel_expense_approval_result.v1",
        "business_intent": "approve_travel_expense_reimbursement",
        "title_rule": {
            "kind": "prefix",
            "value": "【报销】差旅费审批报销单-",
        },
        "required_fields": {
            "流水号",
            "姓名",
            "关联出差申请单",
            "应付金额合计",
            "收款账号",
        },
        "allowed_fields": None,
        "template_id": "-2046021869351779722",
        "form_app_id": "-2571419096251022663",
        "node_policies": {"报销审批"},
        "node_policy_names": {"报销审批"},
        "action_kind": "approval",
        "action_display": "审批通过",
        "summary_title": "审批差旅费报销单",
        "summary_effect": "审批通过后该报销事项将离开待办列表",
        "authorize_label": "授权审批通过",
    },
    "weekly_report": {
        "prepare_capability": WEEKLY_REPORT_ACKNOWLEDGEMENT_PREPARE_CAPABILITY,
        "commit_capability": WEEKLY_REPORT_ACKNOWLEDGE_CAPABILITY,
        "contract_version": "seeyon-weekly-report-acknowledgement-v1",
        "plan_schema": "agentbridge.oa_weekly_report_acknowledgement_plan.v1",
        "result_schema": "agentbridge.oa_weekly_report_acknowledgement_result.v1",
        "business_intent": "acknowledge_weekly_report",
        "title_rule": {"kind": "contains", "value": "周报发送流程"},
        "required_fields": {
            "周报名称",
            "年度",
            "本周 工作总结",
            "下周 工作计划",
        },
        "allowed_fields": None,
        "template_id": "1610567580409022440",
        "form_app_id": "-2351708227632217917",
        "node_policies": {"inform", "知会"},
        "node_policy_names": {"知会"},
        "action_kind": "acknowledgement",
        "action_display": "阅办知会",
        "summary_title": "阅办周报发送流程",
        "summary_effect": "阅办后该周报事项将离开待办列表",
        "authorize_label": "授权阅办周报",
    },
    "standard_collaboration": {
        "prepare_capability": STANDARD_COLLABORATION_APPROVAL_PREPARE_CAPABILITY,
        "commit_capability": STANDARD_COLLABORATION_APPROVE_CAPABILITY,
        "contract_version": "seeyon-standard-collaboration-approval-v1",
        "plan_schema": "agentbridge.oa_standard_collaboration_approval_plan.v1",
        "result_schema": "agentbridge.oa_standard_collaboration_approval_result.v1",
        "business_intent": "approve_standard_collaboration",
        "title_rule": {"kind": "standard_collaboration", "value": ""},
        "required_fields": set(),
        "allowed_fields": {"接收人"},
        "template_id": "",
        "form_app_id": "",
        "node_policies": {"approve", "审批"},
        "node_policy_names": {"审批"},
        "action_kind": "approval",
        "action_display": "审批通过",
        "summary_title": "审批普通协同事项",
        "summary_effect": "审批通过后该普通协同事项将离开待办列表",
        "authorize_label": "授权审批通过",
    },
}

PENDING_ACTION_CAPABILITY_DEFINITIONS = tuple(
    {
        "profile": profile_key,
        "prepare_capability": profile["prepare_capability"],
        "commit_capability": profile["commit_capability"],
        "workflow_prefix": profile_key.replace("_", "-"),
        "action_kind": profile["action_kind"],
    }
    for profile_key, profile in _PROFILES.items()
)

_SPECIALIZED_TITLE_MARKERS = (
    "效能数据",
    "周报发送流程",
    "【HR】",
    "【报销】",
    "【采购】",
    "【用印】",
)


class PendingActionContractMismatch(RuntimeError):
    pass


class PendingActionOutcomeUnknown(RuntimeError):
    pass


def prepare_efficiency_data_approval(adapter, worker, arguments: dict) -> dict:
    return _prepare_pending_action(adapter, worker, arguments, "efficiency_data")


def approve_efficiency_data(
    adapter,
    worker,
    plan: dict,
    *,
    enter_commit_boundary: Callable[[], None],
) -> dict:
    return _commit_pending_action(
        adapter,
        worker,
        plan,
        profile_key="efficiency_data",
        enter_commit_boundary=enter_commit_boundary,
    )


def prepare_travel_expense_approval(adapter, worker, arguments: dict) -> dict:
    return _prepare_pending_action(adapter, worker, arguments, "travel_expense")


def approve_travel_expense(
    adapter,
    worker,
    plan: dict,
    *,
    enter_commit_boundary: Callable[[], None],
) -> dict:
    return _commit_pending_action(
        adapter,
        worker,
        plan,
        profile_key="travel_expense",
        enter_commit_boundary=enter_commit_boundary,
    )


def prepare_weekly_report_acknowledgement(adapter, worker, arguments: dict) -> dict:
    return _prepare_pending_action(adapter, worker, arguments, "weekly_report")


def acknowledge_weekly_report(
    adapter,
    worker,
    plan: dict,
    *,
    enter_commit_boundary: Callable[[], None],
) -> dict:
    return _commit_pending_action(
        adapter,
        worker,
        plan,
        profile_key="weekly_report",
        enter_commit_boundary=enter_commit_boundary,
    )


def prepare_standard_collaboration_approval(adapter, worker, arguments: dict) -> dict:
    return _prepare_pending_action(adapter, worker, arguments, "standard_collaboration")


def approve_standard_collaboration(
    adapter,
    worker,
    plan: dict,
    *,
    enter_commit_boundary: Callable[[], None],
) -> dict:
    return _commit_pending_action(
        adapter,
        worker,
        plan,
        profile_key="standard_collaboration",
        enter_commit_boundary=enter_commit_boundary,
    )


def pending_action_contract_fingerprint(profile_key: str) -> str:
    profile = _profile(profile_key)
    contract = {
        "version": profile["contract_version"],
        "title_rule": profile["title_rule"],
        "required_fields": sorted(profile["required_fields"]),
        "allowed_fields": (
            sorted(profile["allowed_fields"])
            if profile["allowed_fields"] is not None
            else None
        ),
        "template_id": profile["template_id"],
        "form_app_id": profile["form_app_id"],
        "node_policies": sorted(profile["node_policies"]),
        "node_policy_names": sorted(profile["node_policy_names"]),
        "action_kind": profile["action_kind"],
        "selection_policy": "exactly_one_pending_affair_id",
        "internal_binding": "ContinueSubmit",
        "verification": "pending_disappearance",
    }
    return _fingerprint(contract)


def _prepare_pending_action(
    adapter,
    worker,
    arguments: dict,
    profile_key: str,
) -> dict:
    profile = _profile(profile_key)
    inputs = _normalize_inputs(arguments)
    source, detail = adapter.resolve_workflow_detail(
        worker,
        collection="pending",
        affair_id=inputs["affair_id"],
    )
    signals = _validate_target(
        profile_key,
        profile,
        source,
        detail,
        worker.page,
        inputs["affair_id"],
    )
    target = _frozen_target(source, detail, signals, profile_key)
    return {
        "plan": {
            "schema_version": profile["plan_schema"],
            "business_intent": profile["business_intent"],
            "target": target,
            "action_contract": {
                "version": profile["contract_version"],
                "fingerprint": pending_action_contract_fingerprint(profile_key),
                "internal_binding": "ContinueSubmit",
                "action_kind": profile["action_kind"],
                "verification": "pending_disappearance",
            },
            "exact_input": {"opinion": inputs["opinion"]},
            "preconditions": {
                "pending_target_resolved": True,
                "workflow_profile_matched": True,
                "target_identity_complete": True,
                "approval_action_present": True,
                "opinion_control_present": True,
                "submit_entry_present": True,
            },
            "expected_effect": {
                "pending_action_processed": True,
                "processed_count": 1,
                "verification": "pending_disappearance",
            },
        },
        "summary": _summary(profile_key, profile, target, detail, inputs["opinion"]),
    }


def _commit_pending_action(
    adapter,
    worker,
    plan: dict,
    *,
    profile_key: str,
    enter_commit_boundary: Callable[[], None],
    timeout_seconds: float = 60,
) -> dict:
    profile = _profile(profile_key)
    _validate_plan(plan, profile_key, profile)
    target = dict(plan["target"])
    affair_id = _bounded_identifier(target.get("affair_id"), "affair_id")
    opinion = _bounded_text(plan.get("exact_input", {}).get("opinion"), "opinion", 1000)
    source, detail = adapter.resolve_workflow_detail(
        worker,
        collection="pending",
        affair_id=affair_id,
    )
    signals = _validate_target(
        profile_key,
        profile,
        source,
        detail,
        worker.page,
        affair_id,
    )
    current_target = _frozen_target(source, detail, signals, profile_key)
    _assert_frozen_target(target, current_target)

    page = worker.page
    page.on("dialog", lambda dialog: dialog.accept())
    boundary_crossed = False
    enter_commit_boundary()
    boundary_crossed = True
    try:
        scheduled = page.evaluate(
            _PENDING_ACTION_SCRIPT,
            {
                "affair_id": affair_id,
                "opinion": opinion,
                "action_kind": profile["action_kind"],
            },
        )
        if not isinstance(scheduled, dict) or scheduled.get("scheduled") is not True:
            raise PendingActionOutcomeUnknown(
                "OA did not schedule the pending action submission."
            )
        deadline = time.monotonic() + max(timeout_seconds, 5)
        while time.monotonic() < deadline:
            time.sleep(0.8)
            pending = adapter.list_workflows(
                worker,
                collection="pending",
                arguments={"limit": 100},
            )
            if not any(
                str(item.get("affair_id") or "") == affair_id
                for item in pending.get("items") or []
            ):
                result = {
                    "schema_version": profile["result_schema"],
                    "business_intent": profile["business_intent"],
                    "pending_action_processed": True,
                    "processed_count": 1,
                    "action_kind": profile["action_kind"],
                    "workflow_profile": profile_key,
                    "target": {
                        "affair_id": affair_id,
                        "title": str(source.get("title") or ""),
                    },
                    "verification": {
                        "confirmed": True,
                        "method": "pending_disappearance",
                    },
                    "transport": "central_browser_session",
                    "browser_bridge_used": False,
                }
                if profile["action_kind"] == "acknowledgement":
                    result["workflow_acknowledged"] = True
                else:
                    result["workflow_approved"] = True
                return result
        raise PendingActionOutcomeUnknown(
            "The pending action was scheduled, but the affair remained in the pending list."
        )
    except PendingActionOutcomeUnknown:
        raise
    except BaseException as exc:
        if boundary_crossed:
            raise PendingActionOutcomeUnknown(
                "The OA pending-action boundary was crossed, but verification failed."
            ) from exc
        raise


def _validate_target(
    profile_key: str,
    profile: dict,
    source: dict,
    detail: dict,
    page,
    affair_id: str,
) -> dict:
    title = str(source.get("title") or detail.get("title") or "").strip()
    if not _title_matches(profile["title_rule"], title):
        raise PendingActionContractMismatch(
            f"The selected pending workflow is not a registered {profile_key} item."
        )
    field_names = {
        str(item.get("name") or "").strip()
        for item in detail.get("fields") or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    missing_fields = set(profile["required_fields"]) - field_names
    if missing_fields:
        raise PendingActionContractMismatch(
            "The pending workflow is missing registered fields: "
            + ", ".join(sorted(missing_fields))
        )
    allowed_fields = profile["allowed_fields"]
    if allowed_fields is not None and not field_names.issubset(set(allowed_fields)):
        raise PendingActionContractMismatch(
            "The pending workflow exposes fields outside the registered profile."
        )
    actions = {
        str(item.get("code") or "")
        for item in detail.get("actions") or []
        if isinstance(item, dict)
    }
    if "ContinueSubmit" not in actions:
        raise PendingActionContractMismatch(
            "The registered pending action is unavailable on the target workflow."
        )
    signals = page.evaluate(_PAGE_CONTRACT_SCRIPT, affair_id)
    if not isinstance(signals, dict):
        raise PendingActionContractMismatch(
            "The OA pending page no longer matches the registered contract."
        )
    if not all(
        (
            signals.get("affair_matches") is True,
            signals.get("comment_present") is True,
            signals.get("submit_present") is True,
            signals.get("page_path") == "/seeyon/collaboration/collaboration.do",
        )
    ):
        raise PendingActionContractMismatch(
            "The OA pending page no longer matches the registered contract."
        )
    node_policy = str(signals.get("node_policy") or "").strip()
    node_policy_name = str(signals.get("node_policy_name") or "").strip()
    if (
        node_policy not in profile["node_policies"]
        and node_policy_name not in profile["node_policy_names"]
    ):
        raise PendingActionContractMismatch(
            "The OA node policy does not match the registered workflow profile."
        )
    attitudes = {
        str(item or "").lower() for item in signals.get("attitude_codes") or []
    }
    if profile["action_kind"] == "approval" and "agree" not in attitudes:
        raise PendingActionContractMismatch(
            "The OA approval page does not expose the registered agree attitude."
        )
    identity = signals.get("identity")
    if not isinstance(identity, dict):
        raise PendingActionContractMismatch("The OA workflow identity is unavailable.")
    if not str(identity.get("summary_id") or "") or not str(
        identity.get("process_id") or ""
    ):
        raise PendingActionContractMismatch(
            "The OA workflow summary or process identity is unavailable."
        )
    if str(identity.get("template_id") or "") != profile["template_id"]:
        raise PendingActionContractMismatch(
            "The OA workflow template identity does not match the registered profile."
        )
    if str(identity.get("form_app_id") or "") != profile["form_app_id"]:
        raise PendingActionContractMismatch(
            "The OA workflow form identity does not match the registered profile."
        )
    return signals


def _frozen_target(
    source: dict,
    detail: dict,
    signals: dict,
    profile_key: str,
) -> dict:
    identity = signals["identity"]
    return {
        "profile": profile_key,
        "affair_id": str(source.get("affair_id") or ""),
        "summary_id": str(identity.get("summary_id") or ""),
        "process_id": str(identity.get("process_id") or ""),
        "template_id": str(identity.get("template_id") or ""),
        "form_app_id": str(identity.get("form_app_id") or ""),
        "form_record_id": str(identity.get("form_record_id") or ""),
        "title": str(source.get("title") or ""),
        "sender": str(source.get("sender") or ""),
        "date": str(source.get("date") or ""),
        "node_policy": str(signals.get("node_policy") or ""),
        "node_policy_name": str(signals.get("node_policy_name") or ""),
        "detail_fingerprint": _detail_fingerprint(source, detail),
    }


def _assert_frozen_target(expected: dict, actual: dict) -> None:
    for name in (
        "profile",
        "affair_id",
        "summary_id",
        "process_id",
        "template_id",
        "form_app_id",
        "form_record_id",
        "title",
        "sender",
        "date",
        "node_policy",
        "node_policy_name",
        "detail_fingerprint",
    ):
        if str(expected.get(name) or "") != str(actual.get(name) or ""):
            raise PendingActionContractMismatch(
                f"The OA pending target {name} changed after authorization."
            )


def _summary(
    profile_key: str,
    profile: dict,
    target: dict,
    detail: dict,
    opinion: str,
) -> dict:
    fields = [
        {"label": "事项", "value": target["title"]},
        {"label": "发起人", "value": target["sender"]},
        {"label": "日期", "value": target["date"]},
    ]
    values = _field_values(detail)
    if profile_key == "travel_expense":
        for name in (
            "姓名",
            "费用归算类型",
            "费用归属部门",
            "费用归属事项",
            "关联出差申请单",
            "应付金额合计",
        ):
            if values.get(name):
                fields.append({"label": name, "value": values[name]})
    elif profile_key == "weekly_report":
        for name in ("周报名称", "年度", "本周说明"):
            if values.get(name):
                fields.append({"label": name, "value": values[name]})
    fields.extend(
        [
            {
                "label": "附件数量",
                "value": str(len(detail.get("attachments") or [])),
            },
            {
                "label": "已有意见数量",
                "value": str(len(detail.get("workflow") or [])),
            },
            {"label": "本次操作", "value": profile["action_display"]},
            {"label": "处理意见", "value": opinion},
        ]
    )
    return {
        "title": profile["summary_title"],
        "system": "致远 OA",
        "effect": profile["summary_effect"],
        "authorization_notice": (
            "授权后将立即处理这一条精确绑定的待办；"
            "不会处理同名的其他事项，也不会自动重试未知结果。"
        ),
        "authorize_label": profile["authorize_label"],
        "fields": fields,
        "processed_count": 1,
    }


def _normalize_inputs(arguments: dict) -> dict:
    if not isinstance(arguments, dict):
        raise ValueError("pending-action input must be an object")
    return {
        "affair_id": _bounded_identifier(arguments.get("affair_id"), "affair_id"),
        "opinion": _bounded_text(arguments.get("opinion"), "opinion", 1000),
    }


def _validate_plan(plan: dict, profile_key: str, profile: dict) -> None:
    if not isinstance(plan, dict) or plan.get("business_intent") != profile["business_intent"]:
        raise PendingActionContractMismatch(
            "The frozen plan is not the registered pending-action plan."
        )
    target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
    contract = (
        plan.get("action_contract")
        if isinstance(plan.get("action_contract"), dict)
        else {}
    )
    if target.get("profile") != profile_key:
        raise PendingActionContractMismatch("The frozen workflow profile changed.")
    if (
        contract.get("version") != profile["contract_version"]
        or contract.get("fingerprint")
        != pending_action_contract_fingerprint(profile_key)
    ):
        raise PendingActionContractMismatch(
            "The pending-action contract changed after authorization."
        )


def _title_matches(rule: dict, title: str) -> bool:
    kind = rule.get("kind")
    value = str(rule.get("value") or "")
    if kind == "contains":
        return value in title
    if kind == "prefix":
        return title.startswith(value)
    if kind == "standard_collaboration":
        return bool(title) and not any(marker in title for marker in _SPECIALIZED_TITLE_MARKERS)
    return False


def _field_values(detail: dict) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in detail.get("fields") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = re.sub(r"\s+", " ", str(item.get("value") or "")).strip()
        if name and value and name not in values:
            values[name] = value[:500]
    return values


def _detail_fingerprint(source: dict, detail: dict) -> str:
    value = {
        "title": str(source.get("title") or detail.get("title") or ""),
        "fields": [
            {
                "name": str(item.get("name") or ""),
                "value": str(item.get("value") or ""),
            }
            for item in detail.get("fields") or []
            if isinstance(item, dict)
        ],
        "attachments": [
            str(item.get("name") or "")
            for item in detail.get("attachments") or []
            if isinstance(item, dict)
        ],
    }
    return _fingerprint(value)


def _profile(profile_key: str) -> dict:
    try:
        return _PROFILES[profile_key]
    except KeyError as exc:
        raise ValueError(f"unsupported pending-action profile: {profile_key}") from exc


def _bounded_identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"{name} must be a non-empty string of at most 256 characters")
    text = value.strip()
    if any(ord(character) < 32 for character in text):
        raise ValueError(f"{name} must not contain control characters")
    return text


def _bounded_text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    text = re.sub(r"[\r\n\t]+", " ", value).strip()
    if not text:
        raise ValueError(f"{name} is required")
    if len(text) > maximum:
        raise ValueError(f"{name} must not exceed {maximum} characters")
    return text


def _fingerprint(value: dict) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


_PAGE_CONTRACT_SCRIPT = r"""
(expectedAffairId) => {
  const read = (names) => {
    for (const name of names) {
      const element = document.querySelector(`#${name}`)
        || document.querySelector(`[name='${name}']`);
      const value = String(element?.value || window[name] || '').trim();
      if (value) return value;
    }
    return '';
  };
  const pageAffairId = read(['affairId'])
    || new URLSearchParams(location.search).get('affairId')
    || '';
  const comment = document.querySelector('#content_deal_comment')
    || document.querySelector("textarea[name='content_deal_comment']")
    || document.querySelector('textarea#content')
    || document.querySelector("textarea[name='content']");
  return {
    affair_matches: String(pageAffairId) === String(expectedAffairId),
    comment_present: Boolean(comment),
    submit_present: typeof window.submitClickFunc === 'function'
      || typeof window.dealSubmitFunc === 'function'
      || typeof window.$?.content?.callback?.dealSubmit === 'function',
    page_path: location.pathname,
    node_policy: String(window.nodePolicy || ''),
    node_policy_name: String(window.nodePolicyName || ''),
    attitude_codes: Array.from(
      document.querySelectorAll("input[type='radio'][name='attitude']")
    ).map((radio) => String(radio.getAttribute('code') || radio.value || '').toLowerCase()),
    identity: {
      summary_id: read(['summaryId']),
      process_id: read(['processId']),
      template_id: read(['templeteId', 'templateId']),
      form_app_id: read(['formAppId']),
      form_record_id: read(['formRecordid', 'formRecordId']),
    },
  };
}
"""


_PENDING_ACTION_SCRIPT = r"""
({ affair_id, opinion, action_kind }) => {
  const read = (names) => {
    for (const name of names) {
      const element = document.querySelector(`#${name}`)
        || document.querySelector(`[name='${name}']`);
      const value = String(element?.value || window[name] || '').trim();
      if (value) return value;
    }
    return '';
  };
  const pageAffairId = read(['affairId'])
    || new URLSearchParams(location.search).get('affairId')
    || '';
  if (String(pageAffairId) !== String(affair_id)) {
    throw new Error('affair_id mismatch before pending action');
  }
  const comment = document.querySelector('#content_deal_comment')
    || document.querySelector("textarea[name='content_deal_comment']")
    || document.querySelector('textarea#content')
    || document.querySelector("textarea[name='content']");
  if (!comment) throw new Error('pending-action opinion control is missing');
  const setValue = (element, value) => {
    const setter = Object.getOwnPropertyDescriptor(element.constructor.prototype, 'value')?.set;
    if (setter) setter.call(element, value); else element.value = value;
    element.dispatchEvent(new Event('input', { bubbles: true }));
    element.dispatchEvent(new Event('change', { bubbles: true }));
    element.dispatchEvent(new Event('blur', { bubbles: true }));
  };
  setValue(comment, String(opinion));
  if (action_kind === 'approval') {
    const radios = Array.from(document.querySelectorAll("input[type='radio'][name='attitude']"));
    const agree = radios.find((radio) => {
      const code = String(radio.getAttribute('code') || '').toLowerCase();
      const value = String(radio.value || '').toLowerCase();
      return code === 'agree' || value === 'agree'
        || value.endsWith('.agree');
    });
    if (!agree) throw new Error('registered agree attitude is missing');
    agree.checked = true;
    agree.dispatchEvent(new Event('input', { bubbles: true }));
    agree.dispatchEvent(new Event('change', { bubbles: true }));
    const attitudeCode = agree.getAttribute('code') || agree.value || 'agree';
    for (const selector of ['#hidAttitudeCode', '#hidAttitude', '#nodeattitude']) {
      const element = document.querySelector(selector);
      if (element) setValue(element, attitudeCode);
    }
  }
  const nodePolicy = String(window.nodePolicy || '').toLowerCase();
  const nodePolicyName = String(window.nodePolicyName || '');
  let submit = null;
  let submitName = '';
  if ((nodePolicy === 'inform' || nodePolicyName.includes('知会'))
      && typeof window.dealSubmitFunc === 'function') {
    submit = window.dealSubmitFunc;
    submitName = 'dealSubmitFunc';
  } else if (typeof window.submitClickFunc === 'function') {
    submit = window.submitClickFunc;
    submitName = 'submitClickFunc';
  } else if (typeof window.dealSubmitFunc === 'function') {
    submit = window.dealSubmitFunc;
    submitName = 'dealSubmitFunc';
  } else if (typeof window.$?.content?.callback?.dealSubmit === 'function') {
    submit = window.$.content.callback.dealSubmit;
    submitName = '$.content.callback.dealSubmit';
  }
  if (!submit) throw new Error('pending-action submit entry is missing');
  window.setTimeout(() => submit.call(window), 0);
  return { scheduled: true, submit_entry: submitName };
}
"""
