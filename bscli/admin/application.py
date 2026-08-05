from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import os
import secrets
import sqlite3

from bscli.admin.stores import (
    AdminAccountStore,
    AdminAuditStore,
    AdminSessionStore,
)
from bscli.core.central_service import CentralCapabilityService, session_response
from bscli.core.mcp_identities import McpIdentityTokenStore
from bscli.core.runtime_diagnostics import HOST_CONTROL_DIAGNOSTICS
from bscli.core.sessions import SessionPrincipalMismatch


MCP_SCOPES = (
    "oa:read",
    "oa:write:draft",
    "oa:write:approval",
    "oa:write:meeting",
    "oa:write:submit",
    "oa:write:revoke",
    "taihua:read",
    "taihua:write:worklog",
    "yuque:read",
)
SYSTEM_LABELS = {
    "oa": "致远 OA",
    "taihua": "泰华日志",
    "yuque": "部门信息库",
}


class AdminControlPlane:
    def __init__(
        self,
        *,
        service: CentralCapabilityService,
        identity_store: McpIdentityTokenStore,
        started_at: str | None = None,
    ) -> None:
        self.service = service
        self.identity_store = identity_store
        self.db_path = service.db_path
        self.accounts = AdminAccountStore(self.db_path)
        self.admin_sessions = AdminSessionStore(self.db_path)
        self.audit = AdminAuditStore(self.db_path)
        self.policies = service.governance_policies
        self.started_at = started_at or _utc_now()
        self.release_id = os.environ.get("AGENTBRIDGE_RELEASE_ID") or "development"

    def list_admin_accounts(self) -> list[dict]:
        return self.accounts.list()

    def create_admin_account(
        self,
        *,
        actor: dict,
        request_ip: str,
        username: str,
        role: str,
        reason: str,
    ) -> dict:
        _require_admin(actor)
        bootstrap_password = f"Ab9!{secrets.token_urlsafe(18)}"
        account = self.accounts.create(
            username=username,
            password=bootstrap_password,
            role=role,
            must_change_password=True,
        )
        self.audit.append(
            actor=actor,
            action="admin.account.create",
            target_type="admin_account",
            target_id=account["account_id"],
            request_ip=request_ip,
            reason=reason,
            result="succeeded",
            after=account,
        )
        return {**account, "bootstrap_password": bootstrap_password}
    def overview(self) -> dict:
        tokens = self.identity_store.list(limit=1000)
        sessions = self.service.sessions.list(limit=1000)
        operations = self.service.operations.list(limit=500)
        policies = self.policies.list(state="paused")
        now = datetime.now(timezone.utc)
        recent_cutoff = now - timedelta(hours=24)
        recent_operations = [
            item for item in operations if _parse_time(item["created_at"]) >= recent_cutoff
        ]
        status_counts = Counter(item["status"] for item in recent_operations)
        system_counts: dict[str, Counter] = defaultdict(Counter)
        for session in sessions:
            system_counts[session["system_id"]][session["state"]] += 1
        systems = []
        configured = set(self.service._adapters_by_system)
        for system_id in sorted(configured | set(system_counts)):
            counts = system_counts[system_id]
            systems.append(
                {
                    "system_id": system_id,
                    "label": SYSTEM_LABELS.get(system_id, system_id),
                    "configured": system_id in configured,
                    "active_sessions": counts["active"],
                    "attention_sessions": sum(
                        count for state, count in counts.items() if state not in {"active", "new"}
                    ),
                    "total_sessions": sum(counts.values()),
                }
            )
        runtime = self.runtime()
        task_hub = runtime["coordination"]["task_hub"]
        task_summary = task_hub["summary"]
        waiting_tasks = sum(
            int(user.get("task_statuses", {}).get("waiting_user", 0))
            for user in task_hub.get("users", [])
        )
        return {
            "generated_at": _utc_now(),
            "runtime": runtime,
            "summary": {
                "users": len(self._users(tokens=tokens, sessions=sessions)),
                "active_tokens": sum(
                    1
                    for item in tokens
                    if item["state"] == "active" and _parse_time(item["expires_at"]) > now
                ),
                "active_sessions": sum(1 for item in sessions if item["state"] == "active"),
                "paused_policies": len(policies),
                "operations_24h": len(recent_operations),
                "failed_operations_24h": status_counts["failed"] + status_counts["unknown"],
                "active_tasks": task_summary.get("active_tasks", 0),
                "waiting_tasks": waiting_tasks,
                "active_endpoints": task_summary.get("active_endpoints", 0),
                "outstanding_deliveries": task_summary.get("outstanding_deliveries", 0),
                "isolation_violations": task_summary.get("isolation_violation_count", 0),
            },
            "operation_statuses_24h": dict(status_counts),
            "systems": systems,
            "recent_operations": self._operation_projections(recent_operations[:8]),
            "paused_policies": policies[:8],
        }

    def runtime(self) -> dict:
        return {
            "service": "agentbridge",
            "release_id": self.release_id,
            "started_at": self.started_at,
            "admin_api": "v2",
            "database": "available",
            "systems": [
                {
                    "system_id": system_id,
                    "label": SYSTEM_LABELS.get(system_id, system_id),
                    "configured": True,
                }
                for system_id in sorted(self.service._adapters_by_system)
            ],
            "session_keepalive": {
                "enabled": self.service.session_keepalive_lease_seconds is not None,
                "activity_lease_seconds": self.service.session_keepalive_lease_seconds,
            },
            "coordination": {
                "task_hub": self.service.tasks.runtime_diagnostics(),
                "host_control": HOST_CONTROL_DIAGNOSTICS.snapshot(),
            },
        }

    def coordination(
        self,
        *,
        user_subject: str | None = None,
        task_status: str | None = None,
        limit: int = 200,
    ) -> dict:
        limit = min(max(int(limit), 1), 500)
        diagnostics = self.service.tasks.runtime_diagnostics()
        subjects = [
            item["user_subject"]
            for item in diagnostics.get("users", [])
            if not user_subject or item["user_subject"] == user_subject
        ]
        endpoints: list[dict] = []
        tasks: list[dict] = []
        deliveries: list[dict] = []
        continuations: list[dict] = []
        for subject in subjects:
            subject_endpoints = self.service.tasks.list_endpoints(
                user_subject=subject,
                active_only=False,
                limit=500,
            )
            endpoint_index = {
                item["endpoint_id"]: item for item in subject_endpoints
            }
            endpoints.extend(
                self._endpoint_projection(item) for item in subject_endpoints
            )
            subject_tasks = self.service.tasks.list_tasks(
                user_subject=subject,
                limit=limit,
            )
            if task_status:
                subject_tasks = [
                    item for item in subject_tasks
                    if item["status"] == task_status
                ]
            tasks.extend(
                self._task_projection(
                    item,
                    origin_endpoint=endpoint_index.get(item["origin_endpoint_id"]),
                )
                for item in subject_tasks
            )
            deliveries.extend(
                self._delivery_projection(item)
                for item in self.service.tasks.list_outbox(
                    user_subject=subject,
                    limit=limit,
                    newest_first=True,
                )
            )
            continuations.extend(
                self._continuation_projection(
                    item,
                    endpoint=endpoint_index.get(item["endpoint_id"]),
                )
                for item in self.service.tasks.list_continuations(
                    user_subject=subject,
                    limit=limit,
                )
            )
        tasks.sort(key=lambda item: item["updated_at"], reverse=True)
        deliveries.sort(key=lambda item: item["updated_at"], reverse=True)
        continuations.sort(key=lambda item: item["updated_at"], reverse=True)
        deferred_by_endpoint = Counter(
            item["endpoint_id"]
            for item in deliveries
            if item["state"] == "deferred"
        )
        for endpoint in endpoints:
            deferred_count = deferred_by_endpoint.get(endpoint["endpoint_id"], 0)
            endpoint["deferred_delivery_count"] = deferred_count
            endpoint["delivery_state"] = (
                "waiting_activity"
                if deferred_count
                else "ready"
                if endpoint["state"] == "active"
                else "inactive"
            )
        active_endpoint_count = sum(
            1 for item in endpoints if item["state"] == "active"
        )
        pull_endpoint_count = sum(
            1
            for item in endpoints
            if item["state"] == "active" and item["delivery_mode"] == "pull"
        )
        scoped_users = [
            item for item in diagnostics.get("users", [])
            if item["user_subject"] in subjects
        ]
        task_statuses = Counter(
            {
                status: sum(
                    int(item.get("task_statuses", {}).get(status, 0))
                    for item in scoped_users
                )
                for status in {
                    status
                    for item in scoped_users
                    for status in item.get("task_statuses", {})
                }
            }
        )
        outstanding = sum(
            sum(
                int(count)
                for state, count in item.get("outbox_states", {}).items()
                if state in {"pending", "delivering", "deferred"}
            )
            for item in scoped_users
        )
        failed = sum(
            int(item.get("outbox_states", {}).get("failed", 0))
            for item in scoped_users
        )
        deferred = sum(
            int(item.get("outbox_states", {}).get("deferred", 0))
            for item in scoped_users
        )
        return {
            "generated_at": _utc_now(),
            "summary": {
                "users": len(subjects),
                "active_endpoints": active_endpoint_count,
                "pull_endpoints": pull_endpoint_count,
                "direct_endpoints": active_endpoint_count - pull_endpoint_count,
                "active_tasks": sum(
                    task_statuses.get(status, 0)
                    for status in {"active", "waiting_user", "running"}
                ),
                "waiting_tasks": task_statuses.get("waiting_user", 0),
                "active_continuations": sum(
                    1
                    for item in continuations
                    if item["state"] in {"selected", "awaiting_selection"}
                ),
                "outstanding_deliveries": outstanding,
                "deferred_deliveries": deferred,
                "failed_deliveries": failed,
                "isolation_violations": diagnostics.get("summary", {}).get(
                    "isolation_violation_count", 0
                ),
            },
            "task_statuses": dict(task_statuses),
            "isolation": diagnostics.get("isolation", {}),
            "tasks": tasks[:limit],
            "endpoints": endpoints[:limit],
            "deliveries": deliveries[:limit],
            "continuations": continuations[:limit],
        }

    def users(self) -> list[dict]:
        return self._users(
            tokens=self.identity_store.list(limit=1000),
            sessions=self.service.sessions.list(limit=1000),
        )

    def _users(self, *, tokens: list[dict], sessions: list[dict]) -> list[dict]:
        records: dict[str, dict] = {}
        for token in tokens:
            user = records.setdefault(token["user_subject"], _empty_user(token["user_subject"]))
            user["token_count"] += 1
            if token["state"] == "active" and _parse_time(token["expires_at"]) > datetime.now(timezone.utc):
                user["active_token_count"] += 1
        for session in sessions:
            user = records.setdefault(session["user_subject"], _empty_user(session["user_subject"]))
            for principal in (
                session.get("expected_principal_ref"),
                session.get("downstream_principal_ref"),
            ):
                if principal:
                    user["principal_refs"].add(principal)
            user["principal_bindings"][session["system_id"]] = {
                "expected": session.get("expected_principal_ref"),
                "verified": session.get("downstream_principal_ref"),
            }
            user["sessions"][session["system_id"]] = session["state"]
        return sorted(
            ({**item, "principal_refs": sorted(item["principal_refs"])} for item in records.values()),
            key=lambda item: item["user_subject"],
        )

    def list_tokens(self, *, user_subject: str | None = None, limit: int = 500) -> list[dict]:
        return self.identity_store.list(user_subject=user_subject, limit=limit)

    def issue_token(
        self,
        *,
        actor: dict,
        request_ip: str,
        user_subject: str,
        expected_principal_ref: str | None,
        principal_bindings: dict[str, str] | None = None,
        label: str | None,
        scopes: list[str],
        ttl_hours: int,
        reason: str,
    ) -> dict:
        _require_admin(actor)
        if ttl_hours < 1 or ttl_hours > 90 * 24:
            raise ValueError("token lifetime must be between 1 hour and 90 days")
        normalized_scopes = set(scopes)
        if not normalized_scopes or not normalized_scopes.issubset(MCP_SCOPES):
            raise ValueError("MCP identity token contains an unsupported scope")
        if any(scope.startswith("oa:") for scope in normalized_scopes):
            normalized_scopes.add("oa:read")
        if any(scope.startswith("taihua:") for scope in normalized_scopes):
            normalized_scopes.add("taihua:read")
        if any(scope.startswith("yuque:") for scope in normalized_scopes):
            normalized_scopes.add("yuque:read")
        system_ids = {
            scope.split(":", 1)[0]
            for scope in normalized_scopes
            if scope.startswith(("oa:", "taihua:", "yuque:"))
        }
        try:
            resolved_bindings = self.service.sessions.ensure_principal_bindings(
                user_subject=user_subject,
                system_ids=system_ids,
                principal_bindings=principal_bindings,
                fallback_principal_ref=expected_principal_ref,
            )
        except SessionPrincipalMismatch as exc:
            raise ValueError(str(exc)) from exc
        token_principal = (
            str(expected_principal_ref or "").strip()
            or resolved_bindings.get("oa")
            or resolved_bindings[sorted(resolved_bindings)[0]]
        )
        issued = self.identity_store.issue(
            user_subject=user_subject,
            expected_principal_ref=token_principal,
            label=label,
            scopes=sorted(normalized_scopes),
            ttl_seconds=ttl_hours * 3600,
        )
        secret = issued.pop("token")
        public_issued = {**issued, "principal_bindings": resolved_bindings}
        self.audit.append(
            actor=actor,
            action="mcp.token.issue",
            target_type="mcp_token",
            target_id=issued["token_id"],
            request_ip=request_ip,
            reason=reason,
            result="succeeded",
            after=public_issued,
        )
        return {**public_issued, "token_secret": secret}

    def revoke_token(
        self,
        *,
        actor: dict,
        request_ip: str,
        token_id: str,
        reason: str,
    ) -> dict:
        _require_admin(actor)
        before = self.identity_store.get(token_id)
        after = self.identity_store.revoke(token_id)
        self.audit.append(
            actor=actor,
            action="mcp.token.revoke",
            target_type="mcp_token",
            target_id=token_id,
            request_ip=request_ip,
            reason=reason,
            before=before,
            after=after,
            result="succeeded",
        )
        return after

    def sessions(self) -> list[dict]:
        return [self._session_projection(item) for item in self.service.sessions.list(limit=1000)]

    def invalidate_session(
        self,
        *,
        actor: dict,
        request_ip: str,
        session_id: str,
        reason: str,
    ) -> dict:
        _require_admin(actor)
        before = self.service.sessions.get(session_id)
        after = self.service.sessions.mark_expired(
            session_id,
            f"Administratively invalidated: {reason}",
        )
        self.service.session_states.delete(session_id)
        public_before = self._session_projection(before)
        public_after = self._session_projection(after)
        self.audit.append(
            actor=actor,
            action="session.invalidate",
            target_type="downstream_session",
            target_id=session_id,
            request_ip=request_ip,
            reason=reason,
            before=public_before,
            after=public_after,
            result="succeeded",
        )
        return public_after

    def rebind_session_principal(
        self,
        *,
        actor: dict,
        request_ip: str,
        session_id: str,
        expected_principal_ref: str,
        reason: str,
    ) -> dict:
        _require_admin(actor)
        before = self.service.sessions.get(session_id)
        self.service.session_states.delete(session_id)
        after = self.service.sessions.rebind_expected_principal(
            session_id,
            expected_principal_ref=expected_principal_ref,
            reason=reason,
        )
        public_before = self._session_projection(before)
        public_after = self._session_projection(after)
        self.audit.append(
            actor=actor,
            action="session.principal.rebind",
            target_type="downstream_session",
            target_id=session_id,
            request_ip=request_ip,
            reason=reason,
            before=public_before,
            after=public_after,
            result="succeeded",
        )
        return public_after

    def inspect_session(
        self,
        *,
        actor: dict,
        request_ip: str,
        session_id: str,
        reason: str,
    ) -> dict:
        _require_admin(actor)
        session = self.service.sessions.get(session_id)
        result = self.service.inspect_session(
            user_subject=session["user_subject"],
            system_id=session["system_id"],
        )
        self.audit.append(
            actor=actor,
            action="session.live_check",
            target_type="downstream_session",
            target_id=session_id,
            request_ip=request_ip,
            reason=reason,
            result="succeeded",
            after={
                "status": result.get("status"),
                "status_source": result.get("statusSource"),
                "checked_at": result.get("checkedAt"),
            },
        )
        return result

    def capabilities(self) -> list[dict]:
        policies = self.policies.list(state="paused")
        result = []
        for spec in self.service.registry.list():
            matching = [
                item
                for item in policies
                if item["scope_type"] == "global"
                or (item["scope_type"] == "system" and item["scope_value"] == spec.system)
                or (
                    item["scope_type"] == "capability"
                    and item["scope_value"] == spec.name
                    and item["capability_version"] in {"*", spec.version}
                )
            ]
            result.append({**spec.to_dict(), "paused_by": matching})
        return result

    def pause_policy(
        self,
        *,
        actor: dict,
        request_ip: str,
        scope_type: str,
        scope_value: str,
        capability_version: str,
        reason: str,
    ) -> dict:
        _require_admin(actor)
        before = next(
            (
                item
                for item in self.policies.list()
                if item["scope_type"] == scope_type
                and item["scope_value"] == ("*" if scope_type == "global" else scope_value)
                and item["capability_version"] == (capability_version if scope_type == "capability" else "*")
            ),
            None,
        )
        after = self.policies.pause(
            scope_type=scope_type,
            scope_value=scope_value,
            capability_version=capability_version,
            reason=reason,
            actor=actor["username"],
        )
        self.audit.append(
            actor=actor,
            action="governance.write.pause",
            target_type="governance_policy",
            target_id=after["policy_id"],
            request_ip=request_ip,
            reason=reason,
            before=before,
            after=after,
            result="succeeded",
        )
        return after

    def resume_policy(
        self,
        *,
        actor: dict,
        request_ip: str,
        policy_id: str,
        reason: str,
    ) -> dict:
        _require_admin(actor)
        before = self.policies.get(policy_id)
        after = self.policies.resume(policy_id, reason=reason, actor=actor["username"])
        self.audit.append(
            actor=actor,
            action="governance.write.resume",
            target_type="governance_policy",
            target_id=policy_id,
            request_ip=request_ip,
            reason=reason,
            before=before,
            after=after,
            result="succeeded",
        )
        return after

    def operations(
        self,
        *,
        user_subject: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        records = self.service.operations.list(
            user_subject=user_subject,
            limit=min(max(limit, 1), 1000),
        )
        if status:
            records = [item for item in records if item["status"] == status]
        return self._operation_projections(records)

    def interactions(
        self,
        *,
        user_subject: str | None = None,
        interaction_type: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        records = self.service.interactions.list_all(limit=min(max(limit, 1), 500))
        if user_subject:
            records = [item for item in records if item["user_subject"] == user_subject]
        if interaction_type:
            records = [item for item in records if item["interaction_type"] == interaction_type]
        return [self._interaction_projection(item) for item in records]

    def _operation_projections(self, records: list[dict]) -> list[dict]:
        interaction_states = self._operation_interaction_states(
            [
                item["operation_id"]
                for item in records
                if item["status"] == "requires_user_action"
            ]
        )
        return [
            self._operation_projection(
                item,
                interaction=interaction_states.get(item["operation_id"]),
            )
            for item in records
        ]

    def _operation_interaction_states(
        self,
        operation_ids: list[str],
    ) -> dict[str, dict]:
        normalized = list(dict.fromkeys(str(item) for item in operation_ids if item))
        if not normalized:
            return {}
        records: list[sqlite3.Row] = []
        with closing(sqlite3.connect(self.db_path, timeout=30)) as connection:
            connection.row_factory = sqlite3.Row
            for offset in range(0, len(normalized), 400):
                chunk = normalized[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                records.extend(
                    connection.execute(
                        f"""
                        SELECT interaction.operation_id,
                               interaction.interaction_type,
                               interaction.created_at,
                               interaction.expires_at,
                               CASE interaction.interaction_type
                                 WHEN 'credential' THEN challenge.state
                                 WHEN 'business_input' THEN submission.state
                                 WHEN 'execution_authorization' THEN authorization.state
                               END AS resource_state
                        FROM interactions AS interaction
                        LEFT JOIN auth_challenges AS challenge
                          ON interaction.interaction_type = 'credential'
                         AND challenge.challenge_id = interaction.resource_id
                        LEFT JOIN field_submissions AS submission
                          ON interaction.interaction_type = 'business_input'
                         AND submission.submission_id = interaction.resource_id
                        LEFT JOIN write_authorizations AS authorization
                          ON interaction.interaction_type = 'execution_authorization'
                         AND authorization.authorization_id = interaction.resource_id
                        WHERE interaction.operation_id IN ({placeholders})
                        ORDER BY interaction.created_at DESC
                        """,
                        chunk,
                    ).fetchall()
                )
        result: dict[str, dict] = {}
        for row in sorted(records, key=lambda item: item["created_at"], reverse=True):
            result.setdefault(
                row["operation_id"],
                {
                    "interaction_type": row["interaction_type"],
                    "state": row["resource_state"] or "unavailable",
                    "expires_at": row["expires_at"],
                },
            )
        return result

    @staticmethod
    def _operation_projection(
        record: dict,
        *,
        interaction: dict | None = None,
    ) -> dict:
        error = record.get("error") or {}
        effective_status = _effective_operation_status(
            record["status"],
            interaction=interaction,
        )
        return {
            "operation_id": record["operation_id"],
            "request_id": record["request_id"],
            "user_subject": record["user_subject"],
            "capability_name": record["capability_name"],
            "capability_version": record["capability_version"],
            "status": record["status"],
            "effective_status": effective_status,
            "awaiting_user_action": effective_status == "awaiting_user",
            "interaction_type": (
                interaction.get("interaction_type") if interaction else None
            ),
            "interaction_state": interaction.get("state") if interaction else None,
            "error_code": error.get("code"),
            "error_message": error.get("message"),
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
            "finished_at": record.get("finished_at"),
        }

    def _interaction_projection(self, record: dict) -> dict:
        resource_state = "unknown"
        try:
            if record["interaction_type"] == "credential":
                resource_state = self.service.challenges.get(record["resource_id"])["state"]
            elif record["interaction_type"] == "business_input":
                resource_state = self.service.field_submissions.get(
                    record["resource_id"], include_values=False
                )["state"]
            elif record["interaction_type"] == "execution_authorization":
                resource_state = self.service.write_authorizations.get(
                    record["resource_id"], include_plan=False
                )["state"]
        except Exception:
            resource_state = "unavailable"
        return {
            "interaction_id": record["interaction_id"],
            "interaction_type": record["interaction_type"],
            "user_subject": record["user_subject"],
            "system_id": record["system_id"],
            "session_id": record["session_id"],
            "operation_id": record.get("operation_id"),
            "title": record["title"],
            "state": resource_state,
            "created_at": record["created_at"],
            "expires_at": record["expires_at"],
        }

    def _session_projection(self, record: dict) -> dict:
        status = session_response(
            record,
            activity_lease_seconds=self.service.session_keepalive_lease_seconds,
        )
        return {
            "session_id": record["session_id"],
            "user_subject": record["user_subject"],
            "system_id": record["system_id"],
            "expected_principal_ref": record.get("expected_principal_ref"),
            "downstream_principal_ref": record.get("downstream_principal_ref"),
            "state": record["state"],
            "last_error": record.get("last_error"),
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
            "last_verified_at": record.get("last_verified_at"),
            "last_user_activity_at": record.get("last_user_activity_at"),
            "last_keepalive_at": record.get("last_keepalive_at"),
            "keepalive_eligible_until": status.get("keepaliveEligibleUntil"),
            "keepalive_state": status.get("keepaliveState"),
            "expired_at": record.get("expired_at"),
        }

    @staticmethod
    def _endpoint_projection(record: dict) -> dict:
        client_type = str(record["client_type"])
        return {
            "endpoint_id": record["endpoint_id"],
            "user_subject": record["user_subject"],
            "agent_host": record["agent_host"],
            "client_type": client_type,
            "label": record.get("label"),
            "delivery_mode": (
                "pull" if client_type.lower() in {"web", "webchat"} else "direct"
            ),
            "capabilities": list(record.get("capabilities") or []),
            "state": record["state"],
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
            "last_seen_at": record["last_seen_at"],
        }

    @staticmethod
    def _task_projection(
        record: dict,
        *,
        origin_endpoint: dict | None,
    ) -> dict:
        return {
            "task_id": record["task_id"],
            "user_subject": record["user_subject"],
            "agent_host": record["agent_host"],
            "title": record["title"],
            "status": record["status"],
            "origin_endpoint_id": record["origin_endpoint_id"],
            "origin_client_type": (
                origin_endpoint.get("client_type") if origin_endpoint else None
            ),
            "origin_label": origin_endpoint.get("label") if origin_endpoint else None,
            "current_operation_id": record.get("current_operation_id"),
            "current_interaction_id": record.get("current_interaction_id"),
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
            "finished_at": record.get("finished_at"),
        }

    @staticmethod
    def _delivery_projection(record: dict) -> dict:
        payload = record.get("payload") or {}
        return {
            "delivery_id": record["delivery_id"],
            "task_id": record["task_id"],
            "endpoint_id": record["endpoint_id"],
            "user_subject": record["user_subject"],
            "payload_type": record["payload_type"],
            "event_type": str(payload.get("eventType") or "")[:120] or None,
            "state": record["state"],
            "attempt_count": record["attempt_count"],
            "next_attempt_at": (
                None if record["state"] == "deferred"
                else record["next_attempt_at"]
            ),
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
            "acknowledged_at": record.get("acknowledged_at"),
        }

    @staticmethod
    def _continuation_projection(
        record: dict,
        *,
        endpoint: dict | None,
    ) -> dict:
        return {
            "endpoint_id": record["endpoint_id"],
            "user_subject": record["user_subject"],
            "agent_host": record["agent_host"],
            "client_type": endpoint.get("client_type") if endpoint else None,
            "selected_task_id": record.get("selected_task_id"),
            "candidate_count": len(record.get("candidate_task_ids") or []),
            "state": record["state"],
            "execution_mode": record["execution_mode"],
            "reason": _runtime_code(record.get("reason")),
            "expires_at": record["expires_at"],
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
        }


def _empty_user(user_subject: str) -> dict:
    return {
        "user_subject": user_subject,
        "principal_refs": set(),
        "principal_bindings": {},
        "token_count": 0,
        "active_token_count": 0,
        "sessions": {},
    }


def _require_admin(actor: dict) -> None:
    if actor.get("role") != "admin":
        raise PermissionError("administrator role is required")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _runtime_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 120:
        return None
    if any(
        not (character.isascii() and (character.isalnum() or character in "._:-"))
        for character in normalized
    ):
        return None
    return normalized


def _effective_operation_status(
    status: str,
    *,
    interaction: dict | None,
) -> str:
    if status != "requires_user_action":
        return status
    if not interaction:
        return "user_action_handoff"
    resource_state = str(interaction.get("state") or "unavailable")
    if resource_state == "pending":
        expires_at = interaction.get("expires_at")
        if expires_at and _parse_time(str(expires_at)) <= datetime.now(timezone.utc):
            return "user_action_expired"
        return "awaiting_user"
    if resource_state in {"claimed", "processing"}:
        return "awaiting_user"
    return {
        "submitted": "user_action_completed",
        "approved": "user_action_completed",
        "completed": "user_action_completed",
        "consumed": "resumed",
        "expired": "user_action_expired",
        "rejected": "user_action_rejected",
        "declined": "user_action_rejected",
        "superseded": "user_action_superseded",
        "failed": "user_action_failed",
    }.get(resource_state, "user_action_handoff")
