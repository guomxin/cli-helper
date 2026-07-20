from __future__ import annotations

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
        self.evidence.append(entry)

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