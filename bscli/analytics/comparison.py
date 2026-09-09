"""Deterministic comparison of already authorized personal summaries."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from bscli.analytics.contracts import reject

COMPARE = "taihua_personal_compare.v1"
REFERENCE_SCHEMA = {
    "type": "object", "required": ["result_id", "protected", "schema_version"],
    "additionalProperties": False,
    "properties": {"result_id": {"type": "string"}, "protected": {"type": "boolean", "enum": [True]},
                   "schema_version": {"type": "string", "enum": ["taihua.analytics.reference.v1"]}},
}
COMPARE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["baseline", "current", "allow_unequal_windows"],
    "properties": {"baseline": REFERENCE_SCHEMA, "current": REFERENCE_SCHEMA,
                   "allow_unequal_windows": {"type": "boolean"}},
}


def compare(baseline, current, *, allow_unequal_windows=False):
    for value in (baseline, current):
        if (value.get("schema_version") != "taihua.analytics.personal.v1"
                or value.get("log_type") != "DAILY"
                or value.get("coverage", {}).get("status") != "complete"):
            reject("DATA_CONTRACT_CHANGED")
    if any(baseline["provenance"][key] != current["provenance"][key]
           for key in ("source_id", "policy_version", "dataset_version")):
        reject("DATA_CONTRACT_CHANGED")
    lengths = [(date.fromisoformat(v["window"]["end_date_exclusive"])
                - date.fromisoformat(v["window"]["start_date"])).days for v in (baseline, current)]
    unequal = lengths[0] != lengths[1]
    if unequal and not allow_unequal_windows:
        reject("INVALID_ANALYSIS_INPUT")
    # Unequal windows compare daily rates only; raw totals remain source facts.
    names = ("registered_hours", "log_count", "logged_person_days")
    values = {}
    for name in names:
        a, b = (Decimal(str(v["metrics"][name])) for v in (baseline, current))
        if unequal:
            a, b = a / lengths[0], b / lengths[1]
        values[name + ("_per_calendar_day" if unequal else "")] = {
            "baseline": str(a.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
            "current": str(b.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
            "delta": str((b-a).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
            "change_rate": None if a == 0 else str(((b-a)/abs(a)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
            "change_rate_note": "基期为零" if a == 0 else "差额除以基期绝对值；比率非百分数字符串",
        }
    return {"schema_version": "taihua.analytics.comparison.v1", "log_type": "DAILY",
            "scope": {"kind": "self"}, "unequal_windows": unequal,
            "comparison": values,
            "sources": [{"result_id": v["result_id"], "window": v["window"],
                         "provenance": v["provenance"]} for v in (baseline, current)],
            "consistency": "independent_source_snapshots"}


def protected_only(_arguments):
    # Plain registry execution has no trusted principal or result access guard.
    from bscli.core.transforms import TransformRejected
    raise TransformRejected("DATA_ACCESS_DENIED", "比较必须在受控任务计划中解析结果引用。")
