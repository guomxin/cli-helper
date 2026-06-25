from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path


ACTION_LABELS = {
    "Archive": "\u5904\u7406\u540e\u5f52\u6863",
    "Comment": "\u6682\u5b58\u5f85\u529e",
    "CommonPhrase": "\u5e38\u7528\u8bed",
    "ContinueSubmit": "\u63d0\u4ea4",
    "Delete": "\u5220\u9664",
    "Disagree": "\u4e0d\u540c\u610f",
    "Opinion": "\u610f\u89c1",
    "Return": "\u9000\u56de",
    "Revoke": "\u64a4\u9500",
    "Submit": "\u63d0\u4ea4",
    "Track": "\u8ddf\u8e2a",
    "UploadAttachment": "\u4e0a\u4f20\u9644\u4ef6",
}

HIGH_RISK_CODES = {
    "Archive",
    "ContinueSubmit",
    "Delete",
    "Disagree",
    "Return",
    "Revoke",
    "Submit",
    "UploadAttachment",
}

PROMOTED_EXECUTABLE_CODES = {"ContinueSubmit"}

DRY_RUN_ONLY_WRITE_CODES = {
    "Archive",
    "Comment",
    "Delete",
    "Disagree",
    "Return",
    "Revoke",
    "Submit",
    "UploadAttachment",
}

ACTION_TYPES = {
    "Archive": "workflow.archive",
    "Comment": "workflow.comment",
    "ContinueSubmit": "workflow.submit",
    "Delete": "workflow.delete",
    "Disagree": "workflow.disagree",
    "Return": "workflow.return",
    "Revoke": "workflow.revoke",
    "Submit": "workflow.submit",
    "UploadAttachment": "workflow.upload_attachment",
}


WRITE_GOVERNANCE_LIFECYCLE = [
    "resolve_target",
    "dry_run_precheck",
    "confirmation_gate",
    "execute",
    "readback_verification",
    "sanitized_audit",
]


def build_write_governance(
    action_type: str,
    *,
    verification_method: str,
) -> dict:
    return {
        "action_type": str(action_type or ""),
        "lifecycle": list(WRITE_GOVERNANCE_LIFECYCLE),
        "confirmation_required_for_execute": True,
        "verification_method": str(verification_method or ""),
        "audit_policy": "redact_user_text",
    }


def write_action_type(code: str) -> str:
    return ACTION_TYPES.get(str(code or "").strip(), "workflow.unpromoted")


def is_dry_run_only_write_action(code: str) -> bool:
    code = str(code or "").strip()
    return code in DRY_RUN_ONLY_WRITE_CODES and code not in PROMOTED_EXECUTABLE_CODES


def write_action_promotion(code: str) -> dict:
    code = str(code or "").strip()
    if code in PROMOTED_EXECUTABLE_CODES:
        return {
            "status": "promoted",
            "execute_allowed": True,
            "dry_run_allowed": True,
            "verification_method": "pending_disappearance",
            "requirements": [],
            "blocked_reasons": [],
        }
    requirements = [
        f"execution mapping for {code or 'this action'}",
        "post-write verification method",
        "real OA dry-run evidence and user-confirmed production test",
    ]
    return {
        "status": "dry_run_only",
        "execute_allowed": False,
        "dry_run_allowed": True,
        "verification_method": "not_promoted",
        "requirements": requirements,
        "blocked_reasons": [f"execute not promoted for {code or 'this action'}"],
    }


def normalize_write_action(action: str) -> dict:
    code = str(action or "").strip()
    label = ACTION_LABELS.get(code, code)
    return {
        "code": code,
        "label": label,
        "risk": write_action_risk(code, label),
    }


def write_action_risk(code: str, label: str = "") -> str:
    if code in HIGH_RISK_CODES:
        return "high"
    value = f"{code} {label}"
    if any(
        marker in value
        for marker in (
            "\u5220\u9664",
            "\u64a4\u9500",
            "\u9000\u56de",
            "\u63d0\u4ea4",
            "\u4e0d\u540c\u610f",
            "\u5f52\u6863",
            "\u4e0a\u4f20",
        )
    ):
        return "high"
    return "medium"


def build_oa_write_plan(
    *,
    affair_id: str,
    action: str,
    opinion: str,
    mode: str,
    source_url: str = "",
) -> dict:
    normalized_action = normalize_write_action(action)
    promotion = write_action_promotion(normalized_action["code"])
    request_status = {
        "draft": "not_built",
        "dry-run": "not_sent",
        "execute": "blocked",
    }.get(mode, "not_built")
    request_reason = (
        f"OA action {normalized_action['code']} has not been promoted to an executable command"
        if not promotion["execute_allowed"]
        else "confirmed OA write will use the promoted Chrome extension workflow"
        if mode == "execute"
        else "write endpoint discovery is required before production execution"
    )
    if mode != "execute":
        request_reason = (
            f"OA action {normalized_action['code']} is available for dry-run only until promotion requirements are met"
            if not promotion["execute_allowed"]
            else "write endpoint discovery is required before production execution"
        )
    target = {"affair_id": str(affair_id)}
    if source_url:
        target["source_url"] = source_url
    payload_preview = {
        "affairId": str(affair_id),
        "actionCode": normalized_action["code"],
        "opinionText": str(opinion or ""),
        "sourceUrl": source_url or "",
        "dryRunOnly": True,
    }
    payload_fields = [
        {"name": "affairId", "value_present": bool(payload_preview["affairId"])},
        {"name": "actionCode", "value_present": bool(payload_preview["actionCode"])},
        {
            "name": "opinionText",
            "value_present": bool(payload_preview["opinionText"]),
            "length": len(payload_preview["opinionText"]),
        },
        {"name": "sourceUrl", "value_present": bool(payload_preview["sourceUrl"])},
    ]
    return {
        "schema_version": "bscli.oa_write_plan.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "system": "oa",
        "target": target,
        "action": {
            "code": normalized_action["code"],
            "label": normalized_action["label"],
            "risk": normalized_action["risk"],
        },
        "opinion": {
            "text": str(opinion or ""),
            "length": len(str(opinion or "")),
        },
        "safety": {
            "will_execute": False,
            "requires_confirmation": True,
            "risk": normalized_action["risk"],
            "dry_run_only": True,
        },
        "governance": build_write_governance(
            write_action_type(normalized_action["code"]),
            verification_method=promotion["verification_method"],
        ),
        "promotion": promotion,
        "request": {
            "status": request_status,
            "method": None,
            "url": None,
            "body": None,
            "payload_preview": payload_preview,
            "payload_fields": payload_fields,
            "reason": request_reason,
        },
    }


def sanitize_oa_write_plan_for_audit(plan: dict) -> dict:
    sanitized = json.loads(json.dumps(plan, ensure_ascii=False))
    opinion = sanitized.get("opinion")
    if isinstance(opinion, dict):
        opinion.pop("text", None)
    request = sanitized.get("request")
    if isinstance(request, dict):
        request["body"] = None
        payload_preview = request.get("payload_preview")
        if isinstance(payload_preview, dict):
            payload_preview["opinionText"] = None
    return sanitized


def append_oa_write_audit(home: Path, plan: dict) -> Path:
    audit_dir = home / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / "oa-write-plans.jsonl"
    sanitized = sanitize_oa_write_plan_for_audit(plan)
    with audit_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(sanitized, ensure_ascii=False, sort_keys=True))
        file.write("\n")
    return audit_path


def append_oa_write_verification_audit(
    home: Path,
    *,
    affair_id: str,
    action: str,
    source_url: str,
    verification: dict,
    submit: dict | None = None,
) -> Path:
    audit_dir = home / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / "oa-write-verifications.jsonl"
    row = {
        "schema_version": "bscli.oa_write_verification.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "system": "oa",
        "target": {
            "affair_id": str(affair_id),
            "source_url": str(source_url or ""),
        },
        "action": normalize_write_action(action),
        "verification": verification,
        "submit": submit or {},
    }
    with audit_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
        file.write("\n")
    return audit_path
