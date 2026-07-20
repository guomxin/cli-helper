from __future__ import annotations

import hashlib
import html
import json
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse


_MAX_EVIDENCE_ITEMS = 20


def pump_browser_events(page, milliseconds: int = 250) -> None:
    wait_for_timeout = getattr(page, "wait_for_timeout", None)
    if callable(wait_for_timeout):
        try:
            wait_for_timeout(milliseconds)
            return
        except Exception:
            pass
    time.sleep(milliseconds / 1000)


class SubmissionPhaseTracker:
    """Collect sanitized evidence for Seeyon CAP4 submission phases."""

    def __init__(self) -> None:
        self.evidence: list[dict[str, Any]] = []
        self._pending_business_validation: dict[str, Any] | None = None
        self._continued_validation_fingerprints: set[str] = set()

    def observe_response(self, response) -> None:
        request = getattr(response, "request", None)
        method = _attribute_value(request, "method")
        if str(method or "").upper() != "POST":
            return

        url = str(getattr(response, "url", "") or "")
        phase = _classify_phase(url, request)
        if phase is None or len(self.evidence) >= _MAX_EVIDENCE_ITEMS:
            return

        status = _attribute_value(response, "status")
        entry: dict[str, Any] = {
            "sequence": len(self.evidence) + 1,
            "phase": phase["phase"],
            "method": "POST",
            "endpoint": phase["endpoint"],
            "status": int(status or 0),
        }
        if phase.get("operation"):
            entry["operation"] = phase["operation"]
        if phase["phase"] == "cap4_form_save":
            outcome = _read_cap4_outcome(response)
            if outcome is not None:
                entry.update(outcome["evidence"])
                self._pending_business_validation = outcome.get("validation")
        self.evidence.append(entry)

    @property
    def pending_business_validation(self) -> dict[str, Any] | None:
        if self._pending_business_validation is None:
            return None
        return dict(self._pending_business_validation)

    def mark_business_validation_continued(self) -> None:
        if self._pending_business_validation is not None:
            self._continued_validation_fingerprints.add(
                str(self._pending_business_validation.get("fingerprint") or "")
            )
        self._pending_business_validation = None

    def business_validation_was_continued(self, fingerprint: str) -> bool:
        return fingerprint in self._continued_validation_fingerprints

    def unknown_outcome_detail(self) -> str:
        if not self.evidence:
            return "No recognized OA submission response was observed after the send click."
        last = self.evidence[-1]
        final_send_observed = any(
            item.get("phase") == "workflow_send" for item in self.evidence
        )
        final_state = "observed" if final_send_observed else "not observed"
        return (
            f"Last observed OA submission phase: {last['phase']} "
            f"(HTTP {last['status']}); final workflow send was {final_state}."
        )


def business_validation_fingerprint(validation: dict[str, Any]) -> str:
    frozen = {
        "code": str(validation.get("code") or ""),
        "force_check": bool(validation.get("force_check")),
        "message": str(validation.get("message") or ""),
    }
    canonical = json.dumps(
        frozen,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _classify_phase(url: str, request) -> dict[str, str] | None:
    parsed = urlparse(url)
    path = parsed.path
    if path.endswith("/ajax.do"):
        query_method = str(parse_qs(parsed.query).get("method", [""])[0] or "")
        if query_method != "ajaxAction":
            return None
        post_data = str(_attribute_value(request, "post_data") or "")
        manager_method = str(parse_qs(post_data).get("managerMethod", [""])[0] or "")
        phase_by_method = {
            "checkAffairAndLock4NewColJson": "affair_lock_check",
            "checkTemplate": "template_check",
        }
        phase = phase_by_method.get(manager_method)
        if phase is None:
            return None
        return {
            "phase": phase,
            "endpoint": "/seeyon/ajax.do",
            "operation": manager_method,
        }
    if path.endswith("/rest/cap4/form/saveOrUpdate"):
        return {
            "phase": "cap4_form_save",
            "endpoint": "/seeyon/rest/cap4/form/saveOrUpdate",
        }
    if path.endswith("/collaboration/collaboration.do"):
        return {
            "phase": "workflow_send",
            "endpoint": "/seeyon/collaboration/collaboration.do",
            "operation": str(parse_qs(parsed.query).get("method", [""])[0] or ""),
        }
    return None


def _attribute_value(value, name: str):
    if value is None:
        return None
    result = getattr(value, name, None)
    return result() if callable(result) else result


def _read_cap4_outcome(response) -> dict[str, Any] | None:
    try:
        payload = _attribute_value(response, "json")
    except Exception:
        return None
    outcome = _find_cap4_result(payload)
    if outcome is None:
        return None

    success = _as_int(outcome.get("success"))
    code = str(outcome.get("code") or "").strip()
    evidence: dict[str, Any] = {"businessSuccess": success}
    if code:
        evidence["businessCode"] = code
    if success in {1, 2}:
        evidence["businessStatus"] = "accepted"
        return {"evidence": evidence, "validation": None}
    validation = _sanitize_business_validation(outcome, code)
    evidence.update(
        {
            "businessStatus": (
                "validation_required"
                if code in {"3003", "3004", "3008"}
                else "rejected"
            ),
            "validationCode": code,
            "validationCanContinue": validation["can_continue"],
        }
    )
    return {"evidence": evidence, "validation": validation}


def _find_cap4_result(payload: Any) -> dict[str, Any] | None:
    candidates = [payload]
    visited: set[int] = set()
    for _ in range(4):
        next_candidates: list[Any] = []
        for candidate in candidates:
            if not isinstance(candidate, dict) or id(candidate) in visited:
                continue
            visited.add(id(candidate))
            if "success" in candidate and "code" in candidate:
                return candidate
            for key in ("data", "result", "resultdata"):
                nested = candidate.get(key)
                if isinstance(nested, dict):
                    next_candidates.append(nested)
        candidates = next_candidates
    return None


def _sanitize_business_validation(
    outcome: dict[str, Any],
    code: str,
) -> dict[str, Any]:
    data = outcome.get("data") if isinstance(outcome.get("data"), dict) else {}
    message = ""
    force_check = True
    if code == "3003":
        raw_result = data.get("validateResult")
        try:
            result = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
        except (TypeError, ValueError):
            result = None
        if isinstance(result, dict):
            message = str(result.get("ruleError") or "")
            force_check = _as_int(result.get("forceCheck")) == 1
    elif code == "3004":
        unique = data.get("validateDataUnique")
        if isinstance(unique, dict):
            message = str(unique.get("msg") or "")
    elif code == "3008":
        message = str(outcome.get("message") or "")
    else:
        message = str(outcome.get("message") or "")

    clean_message = _clean_validation_message(message) or f"OA business validation {code}"
    validation = {
        "code": code,
        "message": clean_message,
        "force_check": force_check,
        "can_continue": code == "3003" and not force_check,
    }
    validation["fingerprint"] = business_validation_fingerprint(validation)
    return validation


def _clean_validation_message(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))
    return re.sub(r"\s+", " ", without_tags).strip()[:500]


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
