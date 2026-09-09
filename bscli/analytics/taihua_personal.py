from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bscli.analytics.contracts import BUSINESS_TZ, POLICY, Budget, Query, metrics, reject


class TaihuaAnalyticsService:
    def __init__(self, executor, results):
        self.executor, self.results = executor, results

    def run(self, *, config, owner, operation_id, session, provider, query: Query, budget: Budget):
        budget.phase("visibility")
        before = provider.capture(query, budget)
        budget.phase("query")
        numeric = self.executor.summarize(config, before, query, budget)
        budget.phase("verify")
        after = provider.capture(query, budget)
        if before["fingerprint"] != after["fingerprint"]:
            reject("SOURCE_CHANGED")
        checked_at = datetime.now(timezone.utc).isoformat()
        rows = numeric["rows"]
        days = [query.start + timedelta(days=i) for i in range((query.end-query.start).days)]
        output = {
            "schema_version": "taihua.analytics.personal.v1",
            "scope": {"kind": "self", "label": "本人可见日报"},
            "window": {"start_date": query.start.isoformat(), "end_date_exclusive": query.end.isoformat(),
                       "timezone": "Asia/Shanghai", "period_closed": query.end <= datetime.now(BUSINESS_TZ).date()},
            "log_type": "DAILY", "group_by": query.group_by,
            "metrics": numeric["metrics"],
            "series": [{"date": day.isoformat(), **metrics([r for r in rows if r["log_date"] == day.isoformat()])}
                       for day in days] if query.group_by == "day" else [],
            "coverage": {"status": "complete", "basis": "matched_personal_and_self_filtered_team_api",
                         "candidate_count": len(before["rows"]), "selected_count": len(rows), "truncated": False},
            "quality": {"nonpositive_hours_count": sum(1 for r in rows if float(r["hours"]) <= 0)},
            "provenance": {"source_id": config.source_id, "policy_version": POLICY, "dataset_version": "1.0.0",
                           "api_checked_at": checked_at, "database_read_at": numeric["database_read_at"],
                           "privilege_policy": config.privilege_policy,
                           "privilege_warnings": numeric.get("privilege_warnings", []),
                           "consistency": "api_bracketed_database_snapshot"},
        }
        budget.phase("deliver")
        payload = {"arguments": query.arguments(), "session_id": session["session_id"],
                   "authority_generation": session["authority_generation"], "principal_id": before["principal_id"],
                   "policy_version": POLICY, "visibility": before,
                   "evidence_rows": numeric["evidence_rows"], "output": output}
        reference = self.results.save(owner=owner, operation_id=operation_id, payload=payload)
        return reference, {**output, "result_id": reference["result_id"]}

    def get(self, *, result_id, owner, session, provider, budget: Budget):
        payload = self.results.load(result_id, owner=owner)
        if payload.get("kind") == "comparison":
            for source_id in payload["source_result_ids"]:
                source = self.results.load(source_id, owner=owner)
                if source.get("kind"):
                    reject("DATA_CONTRACT_CHANGED")
                self.get(result_id=source_id, owner=owner, session=session, provider=provider, budget=budget)
            self.results.load(result_id, owner=owner)
            budget.check()
            return {**payload["output"], "result_id": result_id}
        if payload.get("kind"):
            reject("INVALID_ANALYSIS_INPUT")
        if (payload["session_id"] != session["session_id"]
                or payload["authority_generation"] != session["authority_generation"]
                or payload["policy_version"] != POLICY):
            reject("RESULT_ACCESS_REVOKED")
        budget.phase("verify")
        now = provider.capture(Query.parse(payload["arguments"]), budget)
        if now["principal_id"] != payload["principal_id"] or now["fingerprint"] != payload["visibility"]["fingerprint"]:
            reject("RESULT_ACCESS_REVOKED")
        budget.phase("deliver")
        self.results.load(result_id, owner=owner)  # Recheck TTL after potentially slow API authorization.
        return {**payload["output"], "result_id": result_id}
