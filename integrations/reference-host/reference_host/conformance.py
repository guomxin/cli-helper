from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ConformanceCase:
    case_id: str
    level: str
    title: str


CASES = (
    ConformanceCase("H01", "L1", "未识别宿主只协商到 L1"),
    ConformanceCase("H02", "L1", "L1 有效会话只读"),
    ConformanceCase("H03", "L1", "L1 交互需求安全降级"),
    ConformanceCase("H04", "L2", "私有交互不进入模型结果"),
    ConformanceCase("H05", "L2", "多端展示单次消费"),
    ConformanceCase("H06", "L2", "重复 resume 幂等"),
    ConformanceCase("H07", "L2", "取消和过期不续办"),
    ConformanceCase("H08", "L2", "登录后自动继续原请求"),
    ConformanceCase("H09", "L2", "字段卡与授权卡分离"),
    ConformanceCase("H10", "L2", "提交超时不自动重试"),
    ConformanceCase("H11", "L3", "宿主重启恢复原任务"),
    ConformanceCase("H12", "L3", "同名下游主体按 Token 隔离"),
    ConformanceCase("H13", "L3", "过期文件可按资格重生"),
    ConformanceCase("H14", "L3", "通知失败不改业务终态"),
    ConformanceCase("H15", "L3", "时间线排序与去重"),
    ConformanceCase("H16", "L2", "私有元数据不得传给模型"),
    ConformanceCase("H17", "L2", "内部工具对模型不可见"),
    ConformanceCase("H18", "L3", "跨用户 Endpoint 拒绝"),
    ConformanceCase("H19", "L3", "晚认领已完成交互可续办"),
    ConformanceCase("H20", "L3", "重复完成事件只续办一次"),
    ConformanceCase("H21", "L3", "多宿主展示与单协调者"),
    ConformanceCase("H22", "L3", "非持有宿主禁止接管续办"),
    ConformanceCase("H23", "L3", "批量任务逐项独立授权"),
    ConformanceCase("H24", "L3", "按副作用边界有界恢复"),
    ConformanceCase("H25", "L3", "宿主运行证据进入中央治理"),
    ConformanceCase("H26", "L2", "登录后按原参数自动续办"),
    ConformanceCase("H27", "L3", "受理后断线只读对账且不重放"),
    ConformanceCase("H28", "L3", "提交经权限校验的持久任务计划"),
    ConformanceCase("H29", "L3", "任务计划重启恢复不重放业务提交"),
)


class ConformanceReport:
    def __init__(self, *, host_name: str, host_version: str) -> None:
        self.host_name = host_name
        self.host_version = host_version
        self._results: dict[str, dict[str, Any]] = {}

    def record(
        self,
        case_id: str,
        *,
        passed: bool,
        evidence: str,
    ) -> None:
        case = _case(case_id)
        self._results[case.case_id] = {
            "caseId": case.case_id,
            "level": case.level,
            "title": case.title,
            "passed": bool(passed),
            "evidence": str(evidence).strip()[:1_000],
        }

    def require_complete(self) -> None:
        missing = [case.case_id for case in CASES if case.case_id not in self._results]
        failed = [
            case_id
            for case_id, result in self._results.items()
            if result["passed"] is not True
        ]
        if missing or failed:
            raise AssertionError(
                f"host conformance incomplete; missing={missing}, failed={failed}"
            )

    def as_dict(self) -> dict[str, Any]:
        ordered = [
            self._results.get(
                case.case_id,
                {
                    "caseId": case.case_id,
                    "level": case.level,
                    "title": case.title,
                    "passed": False,
                    "evidence": "not recorded",
                },
            )
            for case in CASES
        ]
        return {
            "schemaVersion": "agentbridge.host-conformance-report.v1",
            "host": {"name": self.host_name, "version": self.host_version},
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "passed": all(item["passed"] for item in ordered),
            "caseCount": len(ordered),
            "results": ordered,
        }

    def write(self, path: Path | str) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(self.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return output


def case_catalog() -> list[dict[str, str]]:
    return [asdict(case) for case in CASES]


def validate_case_ids(values: Iterable[str]) -> None:
    expected = {case.case_id for case in CASES}
    actual = {str(value).upper() for value in values}
    if actual != expected:
        raise ValueError(
            f"conformance case IDs differ; missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}"
        )


def _case(case_id: str) -> ConformanceCase:
    normalized = str(case_id).upper()
    for case in CASES:
        if case.case_id == normalized:
            return case
    raise KeyError(f"unknown host conformance case: {case_id}")
