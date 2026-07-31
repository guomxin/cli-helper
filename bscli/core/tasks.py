from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4


TASK_STATUSES = {
    "active",
    "waiting_user",
    "running",
    "succeeded",
    "failed",
    "outcome_unknown",
    "canceled",
    "expired",
}


class TaskNotFound(KeyError):
    pass


class TaskIntegrityError(RuntimeError):
    pass


class TaskHubStore:
    """Persistent, non-sensitive task continuity ledger."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS client_endpoints (
                    endpoint_id TEXT PRIMARY KEY,
                    user_subject TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    agent_host TEXT NOT NULL,
                    endpoint_key TEXT NOT NULL,
                    client_type TEXT NOT NULL,
                    external_subject TEXT NOT NULL,
                    account_id TEXT,
                    conversation_ref TEXT NOT NULL,
                    label TEXT,
                    capabilities_json TEXT NOT NULL,
                    route_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    UNIQUE (agent_host, endpoint_key)
                );

                CREATE INDEX IF NOT EXISTS client_endpoints_subject_state
                ON client_endpoints (user_subject, state, updated_at);

                CREATE TABLE IF NOT EXISTS agent_tasks (
                    task_id TEXT PRIMARY KEY,
                    user_subject TEXT NOT NULL,
                    agent_host TEXT NOT NULL,
                    host_task_key TEXT NOT NULL,
                    origin_endpoint_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    current_operation_id TEXT,
                    current_interaction_id TEXT,
                    active_conversation_ref TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT,
                    UNIQUE (user_subject, agent_host, host_task_key)
                );

                CREATE INDEX IF NOT EXISTS agent_tasks_subject_status
                ON agent_tasks (user_subject, status, updated_at);

                CREATE TABLE IF NOT EXISTS task_events (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    user_subject TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    causation_ref TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS task_events_task_created
                ON task_events (task_id, created_at);

                CREATE TABLE IF NOT EXISTS task_operations (
                    task_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL UNIQUE,
                    user_subject TEXT NOT NULL,
                    linked_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, operation_id)
                );

                CREATE TABLE IF NOT EXISTS task_interactions (
                    task_id TEXT NOT NULL,
                    interaction_id TEXT NOT NULL UNIQUE,
                    user_subject TEXT NOT NULL,
                    linked_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, interaction_id)
                );

                CREATE TABLE IF NOT EXISTS task_subscriptions (
                    subscription_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    endpoint_id TEXT NOT NULL,
                    user_subject TEXT NOT NULL,
                    event_filters_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (task_id, endpoint_id)
                );

                CREATE TABLE IF NOT EXISTS notification_outbox (
                    delivery_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    endpoint_id TEXT NOT NULL,
                    user_subject TEXT NOT NULL,
                    payload_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    next_attempt_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    acknowledged_at TEXT,
                    UNIQUE (event_id, endpoint_id, payload_type)
                );

                CREATE INDEX IF NOT EXISTS notification_outbox_endpoint_state
                ON notification_outbox (endpoint_id, state, next_attempt_at);
                """
            )
            self._repair_terminal_task_statuses(connection)

    @staticmethod
    def _repair_terminal_task_statuses(
        connection: sqlite3.Connection,
    ) -> None:
        operations_table = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'operations'
            """
        ).fetchone()
        if operations_table is None:
            return
        connection.execute(
            """
            UPDATE agent_tasks
            SET status = 'succeeded',
                version = version + 1,
                finished_at = COALESCE(
                    (
                        SELECT operations.finished_at
                        FROM operations
                        WHERE operations.operation_id =
                            agent_tasks.current_operation_id
                    ),
                    updated_at
                )
            WHERE status = 'active'
              AND current_operation_id IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM operations
                  WHERE operations.operation_id =
                        agent_tasks.current_operation_id
                    AND operations.status = 'succeeded'
              )
            """
        )

    def ensure_endpoint(
        self,
        *,
        user_subject: str,
        token_id: str,
        agent_host: str,
        endpoint_key: str,
        client_type: str,
        external_subject: str,
        conversation_ref: str,
        account_id: str | None = None,
        label: str | None = None,
        capabilities: list[str] | None = None,
        route: dict[str, Any] | None = None,
    ) -> tuple[dict, bool]:
        values = {
            "user_subject": _required_text(user_subject, "user_subject", 256),
            "token_id": _required_text(token_id, "token_id", 256),
            "agent_host": _required_text(agent_host, "agent_host", 80),
            "endpoint_key": _required_text(endpoint_key, "endpoint_key", 768),
            "client_type": _required_text(client_type, "client_type", 80),
            "external_subject": _required_text(
                external_subject,
                "external_subject",
                768,
            ),
            "conversation_ref": _required_text(
                conversation_ref,
                "conversation_ref",
                1024,
            ),
        }
        normalized_account = _optional_text(account_id, "account_id", 512)
        normalized_label = _optional_text(label, "label", 120)
        capabilities_json = _canonical_json(
            sorted(
                {
                    _required_text(item, "capability", 120)
                    for item in (capabilities or [])
                }
            )
        )
        route_json = _canonical_json(_safe_object(route))
        now = _utc_now()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM client_endpoints
                WHERE agent_host = ? AND endpoint_key = ?
                """,
                (values["agent_host"], values["endpoint_key"]),
            ).fetchone()
            if existing is not None:
                if existing["user_subject"] != values["user_subject"]:
                    raise TaskIntegrityError(
                        "client endpoint is already bound to another user"
                    )
                endpoint_id = existing["endpoint_id"]
                existing_capabilities = set(
                    json.loads(existing["capabilities_json"])
                )
                merged_capabilities_json = _canonical_json(
                    sorted(
                        existing_capabilities
                        | set(json.loads(capabilities_json))
                    )
                )
                connection.execute(
                    """
                    UPDATE client_endpoints
                    SET token_id = ?, client_type = ?, external_subject = ?,
                        account_id = ?, conversation_ref = ?, label = ?,
                        capabilities_json = ?, route_json = ?, state = 'active',
                        updated_at = ?, last_seen_at = ?
                    WHERE endpoint_id = ?
                    """,
                    (
                        values["token_id"],
                        values["client_type"],
                        values["external_subject"],
                        normalized_account,
                        values["conversation_ref"],
                        normalized_label,
                        merged_capabilities_json,
                        route_json,
                        now,
                        now,
                        endpoint_id,
                    ),
                )
                row = self._select_endpoint(connection, endpoint_id)
                return _endpoint_from_row(row), True

            endpoint_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO client_endpoints (
                    endpoint_id, user_subject, token_id, agent_host,
                    endpoint_key, client_type, external_subject, account_id,
                    conversation_ref, label, capabilities_json, route_json,
                    state, created_at, updated_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    endpoint_id,
                    values["user_subject"],
                    values["token_id"],
                    values["agent_host"],
                    values["endpoint_key"],
                    values["client_type"],
                    values["external_subject"],
                    normalized_account,
                    values["conversation_ref"],
                    normalized_label,
                    capabilities_json,
                    route_json,
                    now,
                    now,
                    now,
                ),
            )
            row = self._select_endpoint(connection, endpoint_id)
        return _endpoint_from_row(row), False

    def ensure_task(
        self,
        *,
        user_subject: str,
        agent_host: str,
        host_task_key: str,
        origin_endpoint_id: str,
        active_conversation_ref: str,
        title: str,
        summary: dict[str, Any] | None = None,
    ) -> tuple[dict, bool]:
        user_subject = _required_text(user_subject, "user_subject", 256)
        agent_host = _required_text(agent_host, "agent_host", 80)
        host_task_key = _required_text(host_task_key, "host_task_key", 1024)
        active_conversation_ref = _required_text(
            active_conversation_ref,
            "active_conversation_ref",
            1024,
        )
        title = _required_text(title, "title", 240)
        summary_json = _canonical_json(_safe_object(summary))
        now = _utc_now()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            endpoint = self._select_endpoint(connection, origin_endpoint_id)
            if endpoint["user_subject"] != user_subject:
                raise TaskIntegrityError("task endpoint belongs to another user")
            if endpoint["agent_host"] != agent_host:
                raise TaskIntegrityError("task endpoint belongs to another agent host")
            existing = connection.execute(
                """
                SELECT * FROM agent_tasks
                WHERE user_subject = ? AND agent_host = ? AND host_task_key = ?
                """,
                (user_subject, agent_host, host_task_key),
            ).fetchone()
            if existing is not None:
                if existing["origin_endpoint_id"] != origin_endpoint_id:
                    raise TaskIntegrityError(
                        "host task key is already bound to another endpoint"
                    )
                connection.execute(
                    """
                    UPDATE agent_tasks
                    SET active_conversation_ref = ?, updated_at = ?
                    WHERE task_id = ?
                    """,
                    (
                        active_conversation_ref,
                        now,
                        existing["task_id"],
                    ),
                )
                row = self._select_task(connection, existing["task_id"])
                return _task_from_row(row), True

            task_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO agent_tasks (
                    task_id, user_subject, agent_host, host_task_key,
                    origin_endpoint_id, title, status, summary_json,
                    active_conversation_ref, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, 1, ?, ?)
                """,
                (
                    task_id,
                    user_subject,
                    agent_host,
                    host_task_key,
                    origin_endpoint_id,
                    title,
                    summary_json,
                    active_conversation_ref,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO task_subscriptions (
                    subscription_id, task_id, endpoint_id, user_subject,
                    event_filters_json, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    str(uuid4()),
                    task_id,
                    origin_endpoint_id,
                    user_subject,
                    _canonical_json(["*"]),
                    now,
                    now,
                ),
            )
            self._subscribe_companion_endpoints(
                connection,
                task_id=task_id,
                user_subject=user_subject,
                created_at=now,
            )
            self._append_event(
                connection,
                task_id=task_id,
                user_subject=user_subject,
                event_type="task.created",
                payload={"status": "active", "title": title},
                causation_ref=host_task_key,
                created_at=now,
            )
            row = self._select_task(connection, task_id)
        return _task_from_row(row), False

    def get_task(self, task_id: str, *, user_subject: str) -> dict:
        with self._connect() as connection:
            row = self._select_task(connection, task_id)
        if row["user_subject"] != user_subject:
            raise TaskNotFound(f"task not found: {task_id}")
        return _task_from_row(row)

    def endpoint_for_key(
        self,
        *,
        user_subject: str,
        agent_host: str,
        endpoint_key: str,
    ) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM client_endpoints
                WHERE user_subject = ? AND agent_host = ? AND endpoint_key = ?
                  AND state = 'active'
                """,
                (user_subject, agent_host, endpoint_key),
            ).fetchone()
        if row is None:
            raise TaskNotFound("client endpoint not found")
        return _endpoint_from_row(row)

    def link_operation(
        self,
        *,
        task_id: str,
        user_subject: str,
        operation: dict[str, Any],
    ) -> dict:
        operation_id = _required_text(
            operation.get("operation_id"),
            "operation_id",
            256,
        )
        if operation.get("user_subject") != user_subject:
            raise TaskIntegrityError("operation belongs to another user")
        status = str(operation.get("status") or "")
        task_status = _task_status_for_operation(status)
        event_type = _event_type_for_operation(status)
        now = _utc_now()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._select_owned_task(connection, task_id, user_subject)
            linked = connection.execute(
                "SELECT task_id FROM task_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if linked is not None and linked["task_id"] != task_id:
                raise TaskIntegrityError("operation is already linked to another task")
            if linked is None:
                connection.execute(
                    """
                    INSERT INTO task_operations (
                        task_id, operation_id, user_subject, linked_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (task_id, operation_id, user_subject, now),
                )
                self._append_event(
                    connection,
                    task_id=task_id,
                    user_subject=user_subject,
                    event_type="task.operation.linked",
                    payload={
                        "operationId": operation_id,
                        "capability": operation.get("capability_name"),
                    },
                    causation_ref=operation_id,
                    created_at=now,
                )
            if task["status"] != "outcome_unknown" or task_status == "outcome_unknown":
                self._update_task_state(
                    connection,
                    task_id=task_id,
                    status=task_status,
                    current_operation_id=operation_id,
                    current_interaction_id=task["current_interaction_id"],
                    now=now,
                )
            if linked is None or task["status"] != task_status:
                self._append_event(
                    connection,
                    task_id=task_id,
                    user_subject=user_subject,
                    event_type=event_type,
                    payload={
                        "operationId": operation_id,
                        "status": status,
                        "errorCode": (operation.get("error") or {}).get("code"),
                    },
                    causation_ref=operation_id,
                    created_at=now,
                )
            row = self._select_task(connection, task_id)
        return _task_from_row(row)

    def link_interaction(
        self,
        *,
        task_id: str,
        user_subject: str,
        interaction_record: dict[str, Any],
        interaction: dict[str, Any],
    ) -> dict:
        interaction_id = _required_text(
            interaction_record.get("interaction_id"),
            "interaction_id",
            256,
        )
        if interaction_record.get("user_subject") != user_subject:
            raise TaskIntegrityError("interaction belongs to another user")
        state = str(interaction.get("state") or "")
        task_status = _task_status_for_interaction(state)
        event_type = _event_type_for_interaction(state)
        now = _utc_now()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._select_owned_task(connection, task_id, user_subject)
            linked = connection.execute(
                "SELECT task_id FROM task_interactions WHERE interaction_id = ?",
                (interaction_id,),
            ).fetchone()
            if linked is not None and linked["task_id"] != task_id:
                raise TaskIntegrityError("interaction is already linked to another task")
            if linked is None:
                connection.execute(
                    """
                    INSERT INTO task_interactions (
                        task_id, interaction_id, user_subject, linked_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (task_id, interaction_id, user_subject, now),
                )
            if task["status"] != "outcome_unknown":
                self._update_task_state(
                    connection,
                    task_id=task_id,
                    status=task_status,
                    current_operation_id=task["current_operation_id"],
                    current_interaction_id=interaction_id,
                    now=now,
                )
            if linked is None or task["status"] != task_status:
                if event_type == "task.interaction.waiting":
                    self._subscribe_companion_endpoints(
                        connection,
                        task_id=task_id,
                        user_subject=user_subject,
                        created_at=now,
                    )
                self._append_event(
                    connection,
                    task_id=task_id,
                    user_subject=user_subject,
                    event_type=event_type,
                    payload={
                        "interactionId": interaction_id,
                        "interactionType": interaction.get("type"),
                        "state": state,
                    },
                    causation_ref=interaction_id,
                    created_at=now,
                )
            row = self._select_task(connection, task_id)
        return _task_from_row(row)

    def task_id_for_operation(
        self,
        operation_id: str,
        *,
        user_subject: str,
    ) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT task_id FROM task_operations
                WHERE operation_id = ? AND user_subject = ?
                """,
                (operation_id, user_subject),
            ).fetchone()
        return str(row["task_id"]) if row else None

    def task_id_for_interaction(
        self,
        interaction_id: str,
        *,
        user_subject: str,
    ) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT task_id FROM task_interactions
                WHERE interaction_id = ? AND user_subject = ?
                """,
                (interaction_id, user_subject),
            ).fetchone()
        return str(row["task_id"]) if row else None

    def recovery_candidates(
        self,
        *,
        user_subject: str,
        endpoint_id: str,
        limit: int = 100,
    ) -> list[dict]:
        limit = min(max(int(limit), 1), 500)
        with self._connect() as connection:
            endpoint = self._select_endpoint(connection, endpoint_id)
            if endpoint["user_subject"] != user_subject:
                raise TaskNotFound("client endpoint not found")
            rows = connection.execute(
                """
                SELECT * FROM agent_tasks
                WHERE user_subject = ? AND origin_endpoint_id = ?
                  AND current_interaction_id IS NOT NULL
                  AND status IN ('active', 'waiting_user', 'running')
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_subject, endpoint_id, limit),
            ).fetchall()
        selected_endpoint = _endpoint_from_row(endpoint)
        return [
            {
                "task": _task_from_row(row),
                "endpoint": selected_endpoint,
                "interaction_id": row["current_interaction_id"],
            }
            for row in rows
        ]

    def list_tasks(
        self,
        *,
        user_subject: str,
        endpoint_id: str | None = None,
        active_only: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        limit = min(max(int(limit), 1), 500)
        query = "SELECT * FROM agent_tasks WHERE user_subject = ?"
        parameters: list[Any] = [user_subject]
        if endpoint_id:
            query += " AND origin_endpoint_id = ?"
            parameters.append(endpoint_id)
        if active_only:
            query += " AND status IN ('active', 'waiting_user', 'running')"
        query += " ORDER BY updated_at DESC LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_task_from_row(row) for row in rows]

    def list_endpoints(
        self,
        *,
        user_subject: str,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[dict]:
        limit = min(max(int(limit), 1), 500)
        query = "SELECT * FROM client_endpoints WHERE user_subject = ?"
        parameters: list[Any] = [user_subject]
        if active_only:
            query += " AND state = 'active'"
        query += " ORDER BY updated_at DESC LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_endpoint_from_row(row) for row in rows]

    def list_user_events(
        self,
        *,
        user_subject: str,
        after_event_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        limit = min(max(int(limit), 1), 500)
        with self._connect() as connection:
            parameters: list[Any] = [user_subject]
            query = "SELECT * FROM task_events WHERE user_subject = ?"
            if after_event_id:
                cursor = connection.execute(
                    """
                    SELECT created_at, rowid FROM task_events
                    WHERE event_id = ? AND user_subject = ?
                    """,
                    (after_event_id, user_subject),
                ).fetchone()
                if cursor is None:
                    raise TaskNotFound(
                        f"task event not found: {after_event_id}"
                    )
                query += (
                    " AND (created_at > ? OR "
                    "(created_at = ? AND rowid > ?))"
                )
                parameters.extend(
                    [
                        cursor["created_at"],
                        cursor["created_at"],
                        cursor["rowid"],
                    ]
                )
            query += " ORDER BY created_at, rowid LIMIT ?"
            parameters.append(limit)
            rows = connection.execute(query, parameters).fetchall()
        return [_event_from_row(row) for row in rows]

    def list_events(
        self,
        *,
        task_id: str,
        user_subject: str,
        limit: int = 100,
    ) -> list[dict]:
        limit = min(max(int(limit), 1), 500)
        with self._connect() as connection:
            self._select_owned_task(connection, task_id, user_subject)
            rows = connection.execute(
                """
                SELECT * FROM task_events
                WHERE task_id = ? AND user_subject = ?
                ORDER BY created_at, rowid
                LIMIT ?
                """,
                (task_id, user_subject, limit),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def list_outbox(
        self,
        *,
        user_subject: str,
        endpoint_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        limit = min(max(int(limit), 1), 500)
        query = "SELECT * FROM notification_outbox WHERE user_subject = ?"
        parameters: list[Any] = [user_subject]
        if endpoint_id:
            query += " AND endpoint_id = ?"
            parameters.append(endpoint_id)
        query += " ORDER BY created_at, rowid LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_outbox_from_row(row) for row in rows]

    def claim_outbox(
        self,
        *,
        user_subject: str,
        endpoint_id: str,
        limit: int = 10,
        lease_seconds: int = 30,
    ) -> list[dict]:
        limit = min(max(int(limit), 1), 100)
        lease_seconds = min(max(int(lease_seconds), 5), 300)
        now = _utc_now()
        lease_until = _utc_after(lease_seconds)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            endpoint = self._select_endpoint(connection, endpoint_id)
            if (
                endpoint["user_subject"] != user_subject
                or endpoint["state"] != "active"
            ):
                raise TaskNotFound("client endpoint not found")
            connection.execute(
                """
                UPDATE notification_outbox
                SET state = 'failed', updated_at = ?
                WHERE user_subject = ? AND endpoint_id = ?
                  AND state = 'delivering'
                  AND attempt_count >= 5
                  AND next_attempt_at <= ?
                """,
                (now, user_subject, endpoint_id, now),
            )
            rows = connection.execute(
                """
                SELECT * FROM notification_outbox
                WHERE user_subject = ? AND endpoint_id = ?
                  AND attempt_count < 5
                  AND (
                    state = 'pending'
                    OR (state = 'delivering' AND next_attempt_at <= ?)
                  )
                ORDER BY created_at, rowid
                LIMIT ?
                """,
                (user_subject, endpoint_id, now, limit),
            ).fetchall()
            claimed = []
            for row in rows:
                cursor = connection.execute(
                    """
                    UPDATE notification_outbox
                    SET state = 'delivering',
                        attempt_count = attempt_count + 1,
                        next_attempt_at = ?, updated_at = ?
                    WHERE delivery_id = ?
                      AND attempt_count < 5
                      AND (
                        state = 'pending'
                        OR (state = 'delivering' AND next_attempt_at <= ?)
                      )
                    """,
                    (lease_until, now, row["delivery_id"], now),
                )
                if cursor.rowcount == 1:
                    claimed.append(
                        connection.execute(
                            """
                            SELECT * FROM notification_outbox
                            WHERE delivery_id = ?
                            """,
                            (row["delivery_id"],),
                        ).fetchone()
                    )
        return [_outbox_from_row(row) for row in claimed]

    def acknowledge_outbox(
        self,
        *,
        user_subject: str,
        endpoint_id: str,
        delivery_id: str,
        succeeded: bool,
        retry_after_seconds: int = 5,
    ) -> dict:
        retry_after_seconds = min(max(int(retry_after_seconds), 1), 300)
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM notification_outbox
                WHERE delivery_id = ? AND user_subject = ? AND endpoint_id = ?
                """,
                (delivery_id, user_subject, endpoint_id),
            ).fetchone()
            if row is None:
                raise TaskNotFound("notification delivery not found")
            if succeeded:
                connection.execute(
                    """
                    UPDATE notification_outbox
                    SET state = 'acknowledged', updated_at = ?,
                        acknowledged_at = ?
                    WHERE delivery_id = ?
                    """,
                    (now, now, delivery_id),
                )
            elif int(row["attempt_count"]) >= 5:
                connection.execute(
                    """
                    UPDATE notification_outbox
                    SET state = 'failed', updated_at = ?,
                        acknowledged_at = NULL
                    WHERE delivery_id = ?
                    """,
                    (now, delivery_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE notification_outbox
                    SET state = 'pending', next_attempt_at = ?, updated_at = ?,
                        acknowledged_at = NULL
                    WHERE delivery_id = ?
                    """,
                    (_utc_after(retry_after_seconds), now, delivery_id),
                )
            updated = connection.execute(
                """
                SELECT * FROM notification_outbox WHERE delivery_id = ?
                """,
                (delivery_id,),
            ).fetchone()
        return _outbox_from_row(updated)

    @staticmethod
    def _subscribe_companion_endpoints(
        connection: sqlite3.Connection,
        *,
        task_id: str,
        user_subject: str,
        created_at: str,
    ) -> None:
        endpoints = connection.execute(
            """
            SELECT endpoint_id, capabilities_json
            FROM client_endpoints
            WHERE user_subject = ? AND state = 'active'
            """,
            (user_subject,),
        ).fetchall()
        for endpoint in endpoints:
            capabilities = set(json.loads(endpoint["capabilities_json"]))
            if not capabilities.intersection(
                {
                    "direct_status",
                    "trusted_interaction",
                    "workspace.task.read",
                }
            ):
                continue
            connection.execute(
                """
                INSERT INTO task_subscriptions (
                    subscription_id, task_id, endpoint_id, user_subject,
                    event_filters_json, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                ON CONFLICT(task_id, endpoint_id) DO NOTHING
                """,
                (
                    str(uuid4()),
                    task_id,
                    endpoint["endpoint_id"],
                    user_subject,
                    _canonical_json(
                        [
                            "task.created",
                            "task.operation.linked",
                            "task.operation.running",
                            "task.interaction.waiting",
                            "task.interaction.completed",
                            "task.interaction.expired",
                            "task.interaction.failed",
                            "task.interaction.superseded",
                            "task.canceled",
                            "task.operation.succeeded",
                            "task.operation.failed",
                            "task.operation.outcome_unknown",
                        ]
                    ),
                    created_at,
                    created_at,
                ),
            )

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        task_id: str,
        user_subject: str,
        event_type: str,
        payload: dict[str, Any],
        causation_ref: str | None,
        created_at: str,
    ) -> str:
        event_id = str(uuid4())
        payload_json = _canonical_json(payload)
        connection.execute(
            """
            INSERT INTO task_events (
                event_id, task_id, user_subject, event_type, payload_json,
                causation_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                task_id,
                user_subject,
                event_type,
                payload_json,
                causation_ref,
                created_at,
            ),
        )
        subscriptions = connection.execute(
            """
            SELECT * FROM task_subscriptions
            WHERE task_id = ? AND user_subject = ? AND state = 'active'
            """,
            (task_id, user_subject),
        ).fetchall()
        for subscription in subscriptions:
            filters = json.loads(subscription["event_filters_json"])
            if "*" not in filters and event_type not in filters:
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO notification_outbox (
                    delivery_id, event_id, task_id, endpoint_id, user_subject,
                    payload_type, payload_json, state, attempt_count,
                    next_attempt_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'task_event', ?, 'pending', 0, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    event_id,
                    task_id,
                    subscription["endpoint_id"],
                    user_subject,
                    _canonical_json(
                        {
                            "eventId": event_id,
                            "taskId": task_id,
                            "eventType": event_type,
                            "payload": payload,
                        }
                    ),
                    created_at,
                    created_at,
                    created_at,
                ),
            )
        return event_id

    @staticmethod
    def _update_task_state(
        connection: sqlite3.Connection,
        *,
        task_id: str,
        status: str,
        current_operation_id: str | None,
        current_interaction_id: str | None,
        now: str,
    ) -> None:
        if status not in TASK_STATUSES:
            raise ValueError(f"unsupported task status: {status}")
        finished_at = now if status in {
            "succeeded",
            "failed",
            "outcome_unknown",
            "canceled",
            "expired",
        } else None
        connection.execute(
            """
            UPDATE agent_tasks
            SET status = ?, current_operation_id = ?,
                current_interaction_id = ?, version = version + 1,
                updated_at = ?, finished_at = ?
            WHERE task_id = ?
            """,
            (
                status,
                current_operation_id,
                current_interaction_id,
                now,
                finished_at,
                task_id,
            ),
        )

    @staticmethod
    def _select_endpoint(
        connection: sqlite3.Connection,
        endpoint_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM client_endpoints WHERE endpoint_id = ?",
            (endpoint_id,),
        ).fetchone()
        if row is None:
            raise TaskNotFound(f"client endpoint not found: {endpoint_id}")
        return row

    @staticmethod
    def _select_task(
        connection: sqlite3.Connection,
        task_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM agent_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise TaskNotFound(f"task not found: {task_id}")
        return row

    def _select_owned_task(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        user_subject: str,
    ) -> sqlite3.Row:
        row = self._select_task(connection, task_id)
        if row["user_subject"] != user_subject:
            raise TaskNotFound(f"task not found: {task_id}")
        return row


def _task_status_for_operation(status: str) -> str:
    return {
        "pending": "running",
        "running": "running",
        "requires_user_action": "waiting_user",
        "succeeded": "succeeded",
        "failed": "failed",
        "unknown": "outcome_unknown",
    }.get(status, "active")


def _event_type_for_operation(status: str) -> str:
    return {
        "pending": "task.operation.running",
        "running": "task.operation.running",
        "requires_user_action": "task.interaction.waiting",
        "succeeded": "task.operation.succeeded",
        "failed": "task.operation.failed",
        "unknown": "task.operation.outcome_unknown",
    }.get(status, "task.operation.updated")


def _task_status_for_interaction(state: str) -> str:
    return {
        "pending": "waiting_user",
        "processing": "waiting_user",
        "completed": "active",
        "declined": "canceled",
        "expired": "expired",
        "failed": "failed",
        "superseded": "active",
    }.get(state, "active")


def _event_type_for_interaction(state: str) -> str:
    return {
        "pending": "task.interaction.waiting",
        "processing": "task.interaction.waiting",
        "completed": "task.interaction.completed",
        "declined": "task.canceled",
        "expired": "task.interaction.expired",
        "failed": "task.interaction.failed",
        "superseded": "task.interaction.superseded",
    }.get(state, "task.interaction.updated")


def _endpoint_from_row(row: sqlite3.Row) -> dict:
    value = dict(row)
    value["capabilities"] = json.loads(value.pop("capabilities_json"))
    value["route"] = json.loads(value.pop("route_json"))
    return value


def _task_from_row(row: sqlite3.Row) -> dict:
    value = dict(row)
    value["summary"] = json.loads(value.pop("summary_json"))
    return value


def _event_from_row(row: sqlite3.Row) -> dict:
    value = dict(row)
    value["payload"] = json.loads(value.pop("payload_json"))
    return value


def _outbox_from_row(row: sqlite3.Row) -> dict:
    value = dict(row)
    value["payload"] = json.loads(value.pop("payload_json"))
    return value


def _safe_object(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError("value must be an object")
    return value


def _required_text(value: Any, name: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > maximum
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ValueError(f"{name} is invalid")
    return normalized


def _optional_text(value: Any, name: str, maximum: int) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return _required_text(normalized, name, maximum)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_after(seconds: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).isoformat()
