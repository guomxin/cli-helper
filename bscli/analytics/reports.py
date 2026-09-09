"""Encrypted, short-lived CSV artifacts; no anonymous delivery URLs."""
import base64
import csv
from datetime import datetime, timedelta, timezone
from io import StringIO

from bscli.analytics.contracts import reject
from bscli.core.report_exports import _safe_csv_cell

EXPORT = "taihua.analytics.report.export"
DOWNLOAD = "taihua.analytics.report.download"
EXPORT_SCOPES = frozenset({"taihua:read", "taihua:analytics:read", "taihua:analytics:export"})


def render_csv(summary):
    if summary.get("schema_version") != "taihua.analytics.personal.v1" or summary.get("group_by") != "day":
        reject("INVALID_ANALYSIS_INPUT")
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["日期", "登记工时", "日志数", "人日", "未归项目工时", "口径", "时区", "口径版本", "API核验时间", "数据库读取时间"])
    for row in summary["series"]:
        writer.writerow([_safe_csv_cell(str(value)) for value in (
            row["date"], row["registered_hours"], row["log_count"], row["logged_person_days"],
            row["unassigned_project_hours"], "本人可见 DAILY 日报；登记工时不代表考勤工时", "Asia/Shanghai",
            summary["provenance"]["policy_version"], summary["provenance"]["api_checked_at"],
            summary["provenance"]["database_read_at"])])
    return output.getvalue().encode("utf-8-sig")


def issue(results, *, owner, operation_id, result_id, summary, authority_id):
    now = datetime.now(timezone.utc)
    expires = min(now + timedelta(hours=24), results.expires_at(result_id, owner=owner))
    payload = {"kind": "csv", "source_result_id": result_id,
               "authority_id": authority_id,
               "filename": "taihua-personal-daily.csv", "content_type": "text/csv; charset=utf-8",
               "content_base64": base64.b64encode(render_csv(summary)).decode("ascii"),
               "download_expires_at": min(now + timedelta(minutes=10), expires).isoformat()}
    ref = results.save(owner=owner, operation_id=operation_id, payload=payload, expires_at=expires)
    return ref, {"report_id": ref["result_id"], "artifact_type": "taihua_personal_csv",
                 "filename": payload["filename"], "download_expires_at": payload["download_expires_at"],
                 "expires_at": expires.isoformat(), "download_tool": "taihua_analytics_report_download",
                 "authentication_required": True}
