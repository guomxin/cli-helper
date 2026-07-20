from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import time
from typing import Callable

from bscli.adapters.seeyon_leave import (
    LEAVE_FIELD_CARD_SCHEMA,
    LEAVE_FORM_APP_ID,
    LEAVE_PREPARE_INPUT_SCHEMA,
    LEAVE_SAVE_INPUT_SCHEMA,
    LEAVE_TEMPLATE_ID,
    LEAVE_TEMPLATE_TITLE,
    LeaveContractMismatch,
    LeaveOutcomeUnknown,
    _assert_readback,
    _fill_leave_form,
    _open_and_validate_form,
    _read_leave_form,
    _resolve_template,
    _validate_supported_option,
    leave_contract_fingerprint,
    leave_summary,
    normalize_leave_inputs,
)
from bscli.adapters.seeyon_submit_phases import (
    SubmissionPhaseTracker,
    pump_browser_events,
)


LEAVE_SUBMIT_PREPARE_CAPABILITY = "oa.leave.submit.prepare"
LEAVE_SUBMIT_CAPABILITY = "oa.leave.submit"
LEAVE_SUBMIT_CONTRACT_VERSION = "seeyon-leave-submit-v2"
LEAVE_SUBMIT_PREPARE_INPUT_SCHEMA = LEAVE_PREPARE_INPUT_SCHEMA
LEAVE_SUBMIT_INPUT_SCHEMA = LEAVE_SAVE_INPUT_SCHEMA

LEAVE_SUBMIT_FIELD_CARD_SCHEMA = deepcopy(LEAVE_FIELD_CARD_SCHEMA)
LEAVE_SUBMIT_FIELD_CARD_SCHEMA.update(
    {
        "schema_version": "agentbridge.oa_leave_submit_fields.v1",
        "title": "填写并提交请假申请",
        "effect": "生成一份待确认的请假申请提交计划",
        "notice": "字段提交后还需单独授权；授权后会正式发送并进入 OA 审批流程。",
    }
)


def prepare_leave_submission(adapter, worker, arguments: dict) -> dict:
    inputs = normalize_leave_inputs(arguments)
    sent_snapshot = _sent_snapshot(adapter, worker)
    template = _resolve_template(adapter.list_templates(worker))
    page, frame = _open_and_validate_form(worker, template)
    _validate_supported_option(frame, inputs["leave_type"])
    _fill_leave_form(page, frame, inputs)
    readback = _read_leave_form(page, frame)
    _assert_readback(inputs, readback, stage="prepare")
    expected_subject = str(readback.get("subject") or "").strip()
    if not expected_subject:
        raise LeaveContractMismatch(
            "The OA leave form did not produce a stable submission subject."
        )
    return {
        "plan": {
            "schema_version": "agentbridge.oa_leave_submit_plan.v1",
            "business_intent": "submit_leave_request",
            "target": {
                "template_title": template["title"],
                "template_id": template["template_id"],
                "form_app_id": template["form_app_id"],
                "launch_url": template["href"],
                "expected_subject": expected_subject,
                "sent_subject_marker": LEAVE_TEMPLATE_TITLE,
            },
            "form_contract": {
                "version": LEAVE_SUBMIT_CONTRACT_VERSION,
                "fingerprint": leave_submit_contract_fingerprint(),
                "send_control": "sendId_a",
                "forbidden_controls": ["saveDraft_a"],
            },
            "exact_input": inputs,
            "sent_baseline_affair_ids": sent_snapshot["affair_ids"],
            "preconditions": {
                "template_resolved": True,
                "cap4_frame_loaded": True,
                "supported_leave_type_present": True,
                "form_fields_matched": True,
                "send_control_present": True,
                "sent_collection_readable": True,
            },
            "expected_effect": {
                "workflow_submitted": True,
                "submitted_count": 1,
                "verification": [
                    "oa_submission_phase_observation",
                    "sent_collection_delta",
                    "sent_detail_readback",
                ],
            },
        },
        "summary": leave_submit_summary(inputs),
    }


def submit_leave_request(
    adapter,
    worker,
    plan: dict,
    *,
    enter_commit_boundary: Callable[[], None],
    timeout_seconds: float = 75,
) -> dict:
    _validate_frozen_submit_plan(plan)
    inputs = normalize_leave_inputs(plan["exact_input"])
    template = _resolve_template(adapter.list_templates(worker))
    target = plan["target"]
    if any(
        (
            template["template_id"] != target.get("template_id"),
            template["form_app_id"] != target.get("form_app_id"),
            template["title"] != target.get("template_title"),
        )
    ):
        raise LeaveContractMismatch(
            "The OA leave template changed after submission authorization."
        )
    page, frame = _open_and_validate_form(worker, template)
    _validate_supported_option(frame, inputs["leave_type"])
    _fill_leave_form(page, frame, inputs)
    precommit_readback = _read_leave_form(page, frame)
    _assert_readback(inputs, precommit_readback, stage="precommit")
    expected_subject = str(target.get("expected_subject") or "").strip()
    if str(precommit_readback.get("subject") or "").strip() != expected_subject:
        raise LeaveContractMismatch(
            "The OA leave subject changed after submission authorization."
        )

    phase_tracker = SubmissionPhaseTracker()
    boundary_crossed = False

    page.on("response", phase_tracker.observe_response)
    page.on("dialog", lambda dialog: dialog.accept())
    enter_commit_boundary()
    boundary_crossed = True
    try:
        page.locator("#sendId_a").click(timeout=10000)
        submitted = _wait_for_sent_readback(
            adapter,
            worker,
            page=page,
            baseline_affair_ids=set(plan.get("sent_baseline_affair_ids") or []),
            subject_marker=str(target.get("sent_subject_marker") or "").strip(),
            timeout_seconds=timeout_seconds,
        )
        return {
            "schema_version": "agentbridge.oa_leave_submit_result.v1",
            "business_intent": "submit_leave_request",
            "workflow_submitted": True,
            "submitted_count": 1,
            "submitted": submitted,
            "verification": {
                "confirmed": True,
                "methods": [
                    "oa_submission_phase_observation",
                    "sent_collection_delta",
                    "sent_detail_readback",
                ],
            },
            "request_evidence": phase_tracker.evidence,
            "transport": "central_browser_session",
            "browser_bridge_used": False,
        }
    except LeaveOutcomeUnknown as exc:
        raise LeaveOutcomeUnknown(
            f"{exc} {phase_tracker.unknown_outcome_detail()}"
        ) from exc
    except BaseException as exc:
        if boundary_crossed:
            raise LeaveOutcomeUnknown(
                "The OA leave send boundary was crossed, but verification failed. "
                f"{phase_tracker.unknown_outcome_detail()}"
            ) from exc
        raise


def leave_submit_summary(inputs: dict) -> dict:
    draft_summary = leave_summary(inputs)
    return {
        "title": "提交请假申请",
        "system": "致远 OA",
        "effect": "立即发送并进入审批流程",
        "authorization_notice": "授权后会正式提交该请假申请并进入 OA 审批流程，不会只保存为草稿。",
        "authorize_label": "授权提交审批",
        "fields": draft_summary["fields"],
        "submitted_count": 1,
    }


def leave_submit_contract_fingerprint() -> str:
    contract = {
        "version": LEAVE_SUBMIT_CONTRACT_VERSION,
        "template_title": LEAVE_TEMPLATE_TITLE,
        "template_id": LEAVE_TEMPLATE_ID,
        "form_app_id": LEAVE_FORM_APP_ID,
        "base_form_fingerprint": leave_contract_fingerprint(),
        "send_control": "sendId_a",
        "forbidden_controls": ["saveDraft_a"],
        "verification": [
            "oa_submission_phase_observation",
            "sent_collection_delta",
            "sent_detail_readback",
        ],
    }
    canonical = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _sent_snapshot(adapter, worker) -> dict:
    result = adapter.list_workflows(worker, collection="sent", arguments={"limit": 100})
    items = [item for item in result.get("items") or [] if isinstance(item, dict)]
    return {
        "affair_ids": sorted(
            str(item.get("affair_id") or "")
            for item in items
            if str(item.get("affair_id") or "")
        )
    }


def _wait_for_sent_readback(
    adapter,
    worker,
    *,
    page,
    baseline_affair_ids: set[str],
    subject_marker: str,
    timeout_seconds: float,
) -> dict:
    if not subject_marker:
        raise LeaveContractMismatch("The frozen leave subject marker is missing.")
    deadline = time.monotonic() + max(timeout_seconds, 5)
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            result = adapter.list_workflows(worker, collection="sent", arguments={"limit": 100})
            candidates = [
                item
                for item in result.get("items") or []
                if isinstance(item, dict)
                and str(item.get("affair_id") or "") not in baseline_affair_ids
                and subject_marker in str(item.get("title") or "")
            ]
            if len(candidates) == 1:
                item = candidates[0]
                affair_id = str(item.get("affair_id") or "")
                source_item, detail = adapter.resolve_workflow_detail(
                    worker,
                    collection="sent",
                    affair_id=affair_id,
                )
                detail_title = str(detail.get("title") or source_item.get("title") or "")
                if subject_marker not in detail_title:
                    raise LeaveOutcomeUnknown(
                        "The new sent OA leave item did not match the authorized form."
                    )
                return {
                    "affair_id": affair_id,
                    "title": detail_title,
                    "state": "sent",
                    "detail_readable": True,
                    "field_count": len(detail.get("fields") or []),
                }
            if len(candidates) > 1:
                raise LeaveOutcomeUnknown(
                    "Multiple new sent OA leave items matched the authorized form."
                )
        except LeaveOutcomeUnknown:
            raise
        except BaseException as exc:
            last_error = exc
        pump_browser_events(page)
    message = "The submitted leave request was not confirmed in the OA sent collection."
    if last_error is not None:
        message += f" Last readback error: {type(last_error).__name__}."
    raise LeaveOutcomeUnknown(message)


def _validate_frozen_submit_plan(plan: dict) -> None:
    if not isinstance(plan, dict) or plan.get("business_intent") != "submit_leave_request":
        raise LeaveContractMismatch("The frozen plan is not a leave submission plan.")
    contract = plan.get("form_contract") if isinstance(plan.get("form_contract"), dict) else {}
    if (
        contract.get("version") != LEAVE_SUBMIT_CONTRACT_VERSION
        or contract.get("fingerprint") != leave_submit_contract_fingerprint()
    ):
        raise LeaveContractMismatch(
            "The leave submission contract changed after authorization."
        )