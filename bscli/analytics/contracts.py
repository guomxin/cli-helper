from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import re
import threading
import time
from typing import Callable

from bscli.core.capability_runtime import CapabilityRejected

SUMMARY = "taihua.analytics.personal.summary"
RESULT_GET = "taihua.analytics.result.get"
CAPABILITIES = frozenset({SUMMARY, RESULT_GET, "taihua.analytics.report.export", "taihua.analytics.report.download"})
SCOPES = frozenset({"taihua:read", "taihua:analytics:read"})
POLICY = "taihua.personal.api-visible.v1"
BUSINESS_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
INPUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["start_date", "end_date_exclusive", "log_type", "group_by"],
    "properties": {
        "start_date": {"type": "string", "format": "date"},
        "end_date_exclusive": {"type": "string", "format": "date"},
        "log_type": {"type": "string", "enum": ["DAILY"]},
        "group_by": {"type": "string", "enum": ["none", "day"]},
    },
}


def reject(code: str) -> None:
    messages = {
        "INVALID_ANALYSIS_INPUT": "日期、分组或输入字段不符合本人日报分析契约。",
        "LOG_TYPE_UNSUPPORTED": "一期仅支持 DAILY 日报。",
        "DATA_ACCESS_DENIED": "当前身份没有本人数据分析权限。",
        "IDENTITY_UNVERIFIED": "无法核验日志系统本人身份。",
        "IDENTITY_CHANGED": "日志系统身份已变化，请重新建立正确会话。",
        "COVERAGE_UNVERIFIABLE": "无法核验本人可见日志的完整性，未生成统计。",
        "RESULT_INCOMPLETE": "数据达到读取上限，请缩小日期范围。",
        "SOURCE_CHANGED": "核验期间源数据或身份发生变化，请重新发起查询。",
        "DATA_CONTRACT_CHANGED": "源数据字段不符合已验证契约，分析已停止。",
        "SOURCE_UNAVAILABLE": "分析数据源暂不可用，请联系管理员。",
        "DATASOURCE_AUTH_FAILED": "数据库服务凭据无效，请联系管理员处理。",
        "DATASOURCE_POLICY_REJECTED": "数据库账号权限尚未达到分析启用要求，请管理员收敛权限。",
        "ANALYSIS_BUSY": "本人分析正在执行或数据源繁忙，请稍后重试。",
        "BUDGET_EXCEEDED": "分析超过时间或请求预算，已停止。",
        "INTERRUPTED": "分析已中断，未交付结果。",
        "RESULT_EXPIRED": "分析结果已过期，请重新查询。",
        "RESULT_ACCESS_REVOKED": "当前权限或可见数据已变化，不能读取此历史结果。",
    }
    raise CapabilityRejected(code, messages.get(code, "分析未完成。"))


def bigint(value) -> str:
    # Never accept float IDs, including apparently integral IEEE-754 values.
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        reject("DATA_CONTRACT_CHANGED")
    text = str(value)
    if not re.fullmatch(r"[1-9][0-9]{0,18}", text) or int(text) > 2**63 - 1:
        reject("DATA_CONTRACT_CHANGED")
    return text


def decimal_hours(value) -> Decimal:
    if isinstance(value, bool) or value is None:
        reject("DATA_CONTRACT_CHANGED")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        reject("DATA_CONTRACT_CHANGED")
    if not number.is_finite() or abs(number) > Decimal("999.9") or number != number.quantize(Decimal("0.1")):
        reject("DATA_CONTRACT_CHANGED")
    return number


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@dataclass(frozen=True)
class Query:
    start: date
    end: date
    group_by: str

    @classmethod
    def parse(cls, args: dict, *, today: date | None = None) -> "Query":
        if not isinstance(args, dict) or set(args) != set(INPUT_SCHEMA["required"]):
            reject("INVALID_ANALYSIS_INPUT")
        if args["log_type"] != "DAILY":
            reject("LOG_TYPE_UNSUPPORTED")
        if args["group_by"] not in ("none", "day"):
            reject("INVALID_ANALYSIS_INPUT")
        try:
            for key in ("start_date", "end_date_exclusive"):
                if not isinstance(args[key], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args[key]):
                    raise ValueError()
            start, end = date.fromisoformat(args["start_date"]), date.fromisoformat(args["end_date_exclusive"])
        except (ValueError, TypeError):
            reject("INVALID_ANALYSIS_INPUT")
        today = today or datetime.now(BUSINESS_TZ).date()
        if not 1 <= (end - start).days <= 7 or end > today + timedelta(days=1):
            reject("INVALID_ANALYSIS_INPUT")
        return cls(start, end, args["group_by"])

    def arguments(self) -> dict:
        return {"start_date": self.start.isoformat(), "end_date_exclusive": self.end.isoformat(),
                "log_type": "DAILY", "group_by": self.group_by}


class Budget:
    def __init__(self, *, canceled: threading.Event | None = None,
                 authority: Callable[[], bool] = lambda: True,
                 phase: Callable[[str], None] = lambda _phase: None) -> None:
        self.deadline = time.monotonic() + 60
        self.canceled = canceled or threading.Event()
        self.authority = authority
        self.phase_callback = phase
        self.requests = 0

    def check(self) -> None:
        if self.canceled.is_set():
            reject("INTERRUPTED")
        if time.monotonic() >= self.deadline:
            reject("BUDGET_EXCEEDED")
        if not self.authority():
            reject("DATA_ACCESS_DENIED")

    def phase(self, name: str) -> None:
        self.check()
        self.phase_callback(name)

    def request(self) -> float:
        self.check()
        self.requests += 1
        if self.requests > 18:
            reject("BUDGET_EXCEEDED")
        return min(5.0, self.deadline - time.monotonic())


def metrics(rows: list[dict]) -> dict:
    total = sum((decimal_hours(r["hours"]) for r in rows), Decimal(0))
    unassigned = [r for r in rows if r["project_id"] is None]
    unassigned_hours = sum((decimal_hours(r["hours"]) for r in unassigned), Decimal(0))
    days = len({r["log_date"] for r in rows})
    return {
        "log_count": len(rows), "registered_hours": str(total),
        "logged_people": int(bool(rows)), "logged_person_days": days,
        "hours_per_logged_person_day": str((total / days).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if days else None,
        "unassigned_project_log_count": len(unassigned), "unassigned_project_hours": str(unassigned_hours),
        "unassigned_project_hours_ratio": str((unassigned_hours / total).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)) if total else None,
    }
