from __future__ import annotations

from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from bscli.analytics.contracts import Budget, Query, bigint, decimal_hours, fingerprint, reject
from bscli.core.capability_runtime import CapabilityRejected


class BoundedWorker:
    """Applies the same budget to requests made by authentication refresh."""
    def __init__(self, worker, budget: Budget):
        self.worker, self.budget = worker, budget

    def __getattr__(self, name):
        return getattr(self.worker, name)

    def request(self, method, url, **kwargs):
        kwargs.update(timeout_seconds=self.budget.request(), max_response_bytes=8 * 1024 * 1024,
                      deadline=self.budget.deadline, cancellation=self.budget.canceled)
        response = self.worker.request(method, url, **kwargs)
        self.budget.check()
        # Authorization denied by a team endpoint is not an expired login.
        if response.get("status") == 403 and "/api/work-logs/team" in url:
            reject("COVERAGE_UNVERIFIABLE")
        return response


def projection(row: dict, query: Query, *, owner: str | None = None, owner_username: str | None = None) -> dict:
    if not isinstance(row, dict):
        reject("DATA_CONTRACT_CHANGED")
    try:
        log_date = date.fromisoformat(row["logDate"])
        if log_date.isoformat() != row["logDate"] or not query.start <= log_date < query.end:
            reject("COVERAGE_UNVERIFIABLE")
        kind = row["typeCode"]
        if kind not in ("DAILY", "WEEKLY"):
            reject("DATA_CONTRACT_CHANGED")
        if owner is not None:
            user = row.get("user") if isinstance(row.get("user"), dict) else {}
            ids = [value for value in (row.get("userId"), user.get("id")) if value is not None]
            usernames = [v for v in (row.get("username"), user.get("username")) if v is not None]
            if (any(bigint(value) != owner for value in ids)
                    or (owner_username and any(v != owner_username for v in usernames))
                    or (not ids and (not owner_username or not usernames))):
                reject("COVERAGE_UNVERIFIABLE")
        created = row.get("createdAt")
        if created is not None:
            created = datetime.fromisoformat(created).replace(microsecond=0).isoformat()
        return {"id": bigint(row["id"]), "log_date": log_date.isoformat(),
                "type_code": kind, "hours": str(decimal_hours(row["hours"]).normalize()),
                "project_id": bigint(row["projectId"]) if row.get("projectId") is not None else None,
                "created_at": created}
    except (ValueError, TypeError, KeyError):
        reject("DATA_CONTRACT_CHANGED")


def unique_rows(rows, query, *, owner=None, owner_username=None):
    if not isinstance(rows, list):
        reject("COVERAGE_UNVERIFIABLE")
    if len(rows) >= 500:
        reject("RESULT_INCOMPLETE")
    projected = [projection(row, query, owner=owner, owner_username=owner_username) for row in rows]
    if len({row["id"] for row in projected}) != len(projected):
        reject("RESULT_INCOMPLETE")
    return sorted(projected, key=lambda row: int(row["id"]))


class PersonalVisibilityProvider:
    def __init__(self, adapter, worker, *, expected_principal: str, expected_id: str | None = None):
        self.adapter, self.worker = adapter, worker
        self.expected_principal, self.expected_id = expected_principal, expected_id

    def capture(self, query: Query, budget: Budget) -> dict:
        from bscli.adapters.taihua import TaihuaLoginRequired
        worker = BoundedWorker(self.worker, budget)
        try:
            return self._capture(query, worker)
        except (CapabilityRejected, TaihuaLoginRequired):
            raise
        except Exception:
            # No upstream bodies or URLs in analysis operation failures.
            reject("COVERAGE_UNVERIFIABLE")

    def _capture(self, query, worker):
        read = lambda path: self.adapter._authorized_json(worker, "GET", path)
        principal = read("/api/users/principal")
        if not isinstance(principal, dict):
            reject("IDENTITY_UNVERIFIED")
        user_id = bigint(principal.get("id"))
        observed = str(principal.get("fullname") or principal.get("username") or "").strip()
        if not self.expected_principal or observed != self.expected_principal:
            reject("IDENTITY_CHANGED")
        if self.expected_id is not None and user_id != self.expected_id:
            reject("IDENTITY_CHANGED")
        # Resolve self by exact ID, never by a model-provided name.
        member = self.adapter._resolve_team_member(worker, {"member_id": user_id})
        if not member or bigint(member.get("id")) != user_id:
            reject("IDENTITY_UNVERIFIED")
        username = principal.get("username")
        if not isinstance(username, str) or not username or member.get("username") != username:
            reject("IDENTITY_UNVERIFIED")
        dept_id = bigint(member.get("deptId"))
        principal_dept = principal.get("dept")
        if not isinstance(principal_dept, dict) or bigint(principal_dept.get("id")) != dept_id:
            reject("IDENTITY_UNVERIFIED")
        dates = {"startDate": query.start.isoformat(), "endDate": (query.end - timedelta(days=1)).isoformat()}
        personal = unique_rows(read("/api/work-logs/range?" + urlencode(dates)), query)
        team, expected_total = [], None
        for page in range(1, 6):
            params = {**dates, "userId": user_id, "deptId": dept_id, "page": page,
                      "size": 100, "viewMode": "logDate", "sort": ["logDate,desc", "createdAt,desc"]}
            payload = read("/api/work-logs/team?" + urlencode(params, doseq=True))
            if not isinstance(payload, dict) or type(payload.get("totalElements")) is not int:
                reject("COVERAGE_UNVERIFIABLE")
            total, content = payload["totalElements"], payload.get("content")
            if total < 0 or not isinstance(content, list):
                reject("COVERAGE_UNVERIFIABLE")
            if total >= 500:
                reject("RESULT_INCOMPLETE")
            if expected_total is not None and total != expected_total:
                reject("SOURCE_CHANGED")
            expected_total = total
            if len(content) > 100 or len(content) != min(100, total - len(team)):
                reject("RESULT_INCOMPLETE")
            team.extend(content)
            if len(team) == total:
                break
        normalized_team = unique_rows(team, query, owner=user_id, owner_username=username)
        if len(normalized_team) != expected_total or normalized_team != personal:
            reject("COVERAGE_UNVERIFIABLE")
        snapshot = {"principal_id": user_id, "principal_username": username, "dept_id": dept_id, "rows": personal}
        return {**snapshot, "fingerprint": fingerprint(snapshot)}
