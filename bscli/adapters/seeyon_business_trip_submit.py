from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import time
from typing import Callable

from bscli.adapters.seeyon_business_trip import (
    BUSINESS_TRIP_FIELD_CARD_SCHEMA,
    BUSINESS_TRIP_FORM_APP_ID,
    BUSINESS_TRIP_PREPARE_INPUT_SCHEMA,
    BUSINESS_TRIP_SAVE_INPUT_SCHEMA,
    BUSINESS_TRIP_TEMPLATE_ID,
    BUSINESS_TRIP_TEMPLATE_TITLE,
    BusinessTripContractMismatch,
    BusinessTripOutcomeUnknown,
    _assert_readback,
    _fill_business_trip_form,
    _open_and_validate_form,
    _read_business_trip_form,
    _resolve_template,
    _validate_optional_inputs,
    business_trip_contract_fingerprint,
    business_trip_summary,
    normalize_business_trip_inputs,
)
from bscli.adapters.seeyon_submit_phases import SubmissionPhaseTracker


BUSINESS_TRIP_SUBMIT_PREPARE_CAPABILITY = "oa.business_trip.submit.prepare"
BUSINESS_TRIP_SUBMIT_CAPABILITY = "oa.business_trip.submit"
BUSINESS_TRIP_SUBMIT_CONTRACT_VERSION = "seeyon-business-trip-submit-v2"
BUSINESS_TRIP_SUBMIT_PREPARE_INPUT_SCHEMA = BUSINESS_TRIP_PREPARE_INPUT_SCHEMA
BUSINESS_TRIP_SUBMIT_INPUT_SCHEMA = BUSINESS_TRIP_SAVE_INPUT_SCHEMA

BUSINESS_TRIP_SUBMIT_FIELD_CARD_SCHEMA = deepcopy(BUSINESS_TRIP_FIELD_CARD_SCHEMA)
BUSINESS_TRIP_SUBMIT_FIELD_CARD_SCHEMA.update(
    {
        "schema_version": "agentbridge.oa_business_trip_submit_fields.v1",
        "title": "填写并提交出差申请",
        "effect": "生成一份待确认的出差申请提交计划",
        "notice": "字段提交后还需单独授权；授权后会正式发送并进入 OA 审批流程。",
    }
)


def prepare_business_trip_submission(adapter, worker, arguments: dict) -> dict:
    inputs = normalize_business_trip_inputs(arguments)
    sent_snapshot = _sent_snapshot(adapter, worker)
    template = _resolve_template(adapter.list_templates(worker))
    page, frame = _open_and_validate_form(worker, template)
    _validate_optional_inputs(page, inputs)
    _fill_business_trip_form(page, frame, inputs)
    readback = _read_business_trip_form(page, frame)
    _assert_readback(inputs, readback, stage="prepare")
    expected_subject = str(readback.get("subject") or "").strip()
    if not expected_subject:
        raise BusinessTripContractMismatch(
            "The OA business-trip form did not produce a stable submission subject."
        )
    return {
        "plan": {
            "schema_version": "agentbridge.oa_business_trip_submit_plan.v1",
            "business_intent": "submit_business_trip_request",
            "target": {
                "template_title": template["title"],
                "template_id": template["template_id"],
                "form_app_id": template["form_app_id"],
                "launch_url": template["href"],
                "expected_subject": expected_subject,
            },
            "form_contract": {
                "version": BUSINESS_TRIP_SUBMIT_CONTRACT_VERSION,
                "fingerprint": business_trip_submit_contract_fingerprint(),
                "send_control": "sendId_a",
                "forbidden_controls": ["saveDraft_a"],
            },
            "exact_input": inputs,
            "sent_baseline_affair_ids": sent_snapshot["affair_ids"],
            "preconditions": {
                "template_resolved": True,
                "cap4_frame_loaded": True,
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
        "summary": business_trip_submit_summary(inputs),
    }


def submit_business_trip_request(
    adapter,
    worker,
    plan: dict,
    *,
    enter_commit_boundary: Callable[[], None],
    timeout_seconds: float = 75,
) -> dict:
    _validate_frozen_submit_plan(plan)
    inputs = normalize_business_trip_inputs(plan["exact_input"])
    template = _resolve_template(adapter.list_templates(worker))
    target = plan["target"]
    if any(
        (
            template["template_id"] != target.get("template_id"),
            template["form_app_id"] != target.get("form_app_id"),
            template["title"] != target.get("template_title"),
        )
    ):
        raise BusinessTripContractMismatch(
            "The OA business-trip template changed after submission authorization."
        )
    page, frame = _open_and_validate_form(worker, template)
    _validate_optional_inputs(page, inputs)
    _fill_business_trip_form(page, frame, inputs)
    precommit_readback = _read_business_trip_form(page, frame)
    _assert_readback(inputs, precommit_readback, stage="precommit")
    expected_subject = str(target.get("expected_subject") or "").strip()
    if str(precommit_readback.get("subject") or "").strip() != expected_subject:
        raise BusinessTripContractMismatch(
            "The OA business-trip subject changed after submission authorization."
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
            baseline_affair_ids=set(plan.get("sent_baseline_affair_ids") or []),
            expected_subject=expected_subject,
            timeout_seconds=timeout_seconds,
        )
        return {
            "schema_version": "agentbridge.oa_business_trip_submit_result.v1",
            "business_intent": "submit_business_trip_request",
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
    except BusinessTripOutcomeUnknown as exc:
        raise BusinessTripOutcomeUnknown(
            f"{exc} {phase_tracker.unknown_outcome_detail()}"
        ) from exc
    except BaseException as exc:
        if boundary_crossed:
            raise BusinessTripOutcomeUnknown(
                "The OA business-trip send boundary was crossed, but verification failed. "
                f"{phase_tracker.unknown_outcome_detail()}"
            ) from exc
        raise


def business_trip_submit_summary(inputs: dict) -> dict:
    draft_summary = business_trip_summary(inputs)
    return {
        "title": "提交出差申请",
        "system": "致远 OA",
        "effect": "立即发送并进入审批流程",
        "authorization_notice": "授权后会正式提交该出差申请并进入 OA 审批流程，不会只保存为草稿。",
        "authorize_label": "授权提交审批",
        "fields": draft_summary["fields"],
        "submitted_count": 1,
    }


def business_trip_submit_contract_fingerprint() -> str:
    contract = {
        "version": BUSINESS_TRIP_SUBMIT_CONTRACT_VERSION,
        "template_title": BUSINESS_TRIP_TEMPLATE_TITLE,
        "template_id": BUSINESS_TRIP_TEMPLATE_ID,
        "form_app_id": BUSINESS_TRIP_FORM_APP_ID,
        "base_form_fingerprint": business_trip_contract_fingerprint(),
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
    baseline_affair_ids: set[str],
    expected_subject: str,
    timeout_seconds: float,
) -> dict:
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
                and expected_subject in str(item.get("title") or "")
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
                if expected_subject not in detail_title:
                    raise BusinessTripOutcomeUnknown(
                        "The new sent OA item did not match the authorized subject."
                    )
                return {
                    "affair_id": affair_id,
                    "title": detail_title,
                    "state": "sent",
                    "detail_readable": True,
                    "field_count": len(detail.get("fields") or []),
                }
            if len(candidates) > 1:
                raise BusinessTripOutcomeUnknown(
                    "Multiple new sent OA items matched the authorized subject."
                )
        except BusinessTripOutcomeUnknown:
            raise
        except BaseException as exc:
            last_error = exc
        time.sleep(0.5)
    message = "The submitted business-trip request was not confirmed in the OA sent collection."
    if last_error is not None:
        message += f" Last readback error: {type(last_error).__name__}."
    raise BusinessTripOutcomeUnknown(message)


def _validate_frozen_submit_plan(plan: dict) -> None:
    if not isinstance(plan, dict) or plan.get("business_intent") != "submit_business_trip_request":
        raise BusinessTripContractMismatch(
            "The frozen plan is not a business-trip submission plan."
        )
    contract = plan.get("form_contract") if isinstance(plan.get("form_contract"), dict) else {}
    if (
        contract.get("version") != BUSINESS_TRIP_SUBMIT_CONTRACT_VERSION
        or contract.get("fingerprint") != business_trip_submit_contract_fingerprint()
    ):
        raise BusinessTripContractMismatch(
            "The business-trip submission contract changed after authorization."
        )
