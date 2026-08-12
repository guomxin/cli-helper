from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import re
from typing import Any


SMARTLIGHT_REPORT_ARTIFACT_TYPE = "smartlight_report"
SMARTLIGHT_REPORT_DOCUMENT_TYPE = "smartlight_csv_report"
SMARTLIGHT_REPORT_RECIPE_KIND = "smartlight_report.v1"
SMARTLIGHT_REPORT_CONTENT_TYPE = "text/csv"
SMARTLIGHT_REPORT_MAX_ROWS = 500

_BUSINESS_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
_INVALID_FILENAME_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_FORMULA_PREFIXES = ("=", "+", "-", "@")


def render_smartlight_report_csv(report: dict[str, Any]) -> bytes:
    columns = report.get("columns")
    rows = report.get("rows")
    if not isinstance(columns, list) or not columns:
        raise ValueError("Smartlight report columns are required")
    if not isinstance(rows, list):
        raise ValueError("Smartlight report rows must be a list")
    if len(rows) > SMARTLIGHT_REPORT_MAX_ROWS:
        raise ValueError("Smartlight report exceeds the row limit")

    normalized_columns: list[tuple[str, str]] = []
    for column in columns:
        if not isinstance(column, dict):
            raise ValueError("Smartlight report column is invalid")
        key = str(column.get("key") or "").strip()
        label = str(column.get("label") or "").strip()
        if not key or not label:
            raise ValueError("Smartlight report column is incomplete")
        normalized_columns.append((key, label))

    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow([label for _, label in normalized_columns])
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Smartlight report row is invalid")
        writer.writerow(
            [_safe_csv_cell(row.get(key)) for key, _ in normalized_columns]
        )
    return output.getvalue().encode("utf-8-sig")


def smartlight_report_filename(
    report: dict[str, Any],
    *,
    now: datetime | None = None,
) -> str:
    stem = str(report.get("filenameStem") or report.get("reportTitle") or "照明报告")
    stem = _INVALID_FILENAME_CHARACTERS.sub("_", stem)
    stem = re.sub(r"\s+", "_", stem).strip(" ._")[:120] or "照明报告"
    generated_at = now or datetime.now(_BUSINESS_TIMEZONE)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=_BUSINESS_TIMEZONE)
    generated_at = generated_at.astimezone(_BUSINESS_TIMEZONE)
    return f"{stem}_{generated_at:%Y%m%d_%H%M%S}.csv"


def smartlight_report_recipe(
    *,
    report_type: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": SMARTLIGHT_REPORT_RECIPE_KIND,
        "reportType": str(report_type),
        "arguments": dict(arguments),
    }


def is_smartlight_report_recipe(value: object) -> bool:
    return (
        isinstance(value, dict)
        and value.get("kind") == SMARTLIGHT_REPORT_RECIPE_KIND
        and isinstance(value.get("reportType"), str)
        and isinstance(value.get("arguments"), dict)
    )


def _safe_csv_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = str(value)
    candidate = text.lstrip()
    if candidate.startswith(_FORMULA_PREFIXES):
        return f"'{text}"
    return text
