from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from urllib.parse import quote, urlparse
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
    "superseded",
}

ACTIVE_TASK_STATUSES = {"active", "waiting_user", "running"}
TERMINAL_TASK_STATUSES = TASK_STATUSES - ACTIVE_TASK_STATUSES
CONTINUATION_STATES = {"awaiting_selection", "selected", "expired", "cleared"}
CONTINUATION_EXECUTION_MODES = {"observe_only", "resume", "follow_up"}

PULL_BASED_CLIENT_TYPES = {"web", "webchat"}


class TaskNotFound(KeyError):
    pass


class TaskIntegrityError(RuntimeError):
    pass


def _is_pull_based_endpoint(endpoint: sqlite3.Row | dict[str, Any]) -> bool:
    return str(endpoint["client_type"] or "").lower() in PULL_BASED_CLIENT_TYPES


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
                    last_state TEXT,
                    last_observed_at TEXT,
                    PRIMARY KEY (task_id, interaction_id)
                );

                CREATE TABLE IF NOT EXISTS task_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    user_subject TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL UNIQUE,
                    filename TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    download_url TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS task_artifacts_task_created
                ON task_artifacts (task_id, created_at);

                CREATE INDEX IF NOT EXISTS task_artifacts_subject_state
                ON task_artifacts (user_subject, state, expires_at);

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

                CREATE TABLE IF NOT EXISTS user_timeline (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_id TEXT NOT NULL UNIQUE,
                    user_subject TEXT NOT NULL,
                    entry_type TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL,
                    source_endpoint_id TEXT,
                    task_id TEXT,
                    role TEXT,
                    text TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (user_subject, dedupe_key)
                );

                CREATE INDEX IF NOT EXISTS user_timeline_subject_sequence
                ON user_timeline (user_subject, sequence);

                CREATE TABLE IF NOT EXISTS task_continuations (
                    endpoint_id TEXT PRIMARY KEY,
                    user_subject TEXT NOT NULL,
                    agent_host TEXT NOT NULL,
                    selected_task_id TEXT,
                    candidate_task_ids_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    execution_mode TEXT NOT NULL,
                    reason TEXT,
                    expires_at TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS task_continuations_subject_state
                ON task_continuations (user_subject, state, updated_at);
                """
            )
            self._migrate_task_interaction_observations(connection)
            self._repair_terminal_task_statuses(connection)
            self._reconcile_pull_based_deliveries(connection)

    @staticmethod
    def _migrate_task_interaction_observations(
        connection: sqlite3.Connection,
    ) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(task_interactions)"
            ).fetchall()
        }
        if "last_state" not in columns:
            connection.execute(
                "ALTER TABLE task_interactions ADD COLUMN last_state TEXT"
            )
        if "last_observed_at" not in columns:
            connection.execute(
                "ALTER TABLE task_interactions ADD COLUMN last_observed_at TEXT"
            )
        event_states = {
            "task.interaction.waiting": "pending",
            "task.interaction.completed": "completed",
            "task.canceled": "declined",
            "task.interaction.expired": "expired",
            "task.interaction.failed": "failed",
            "task.interaction.superseded": "superseded",
        }
        rows = connection.execute(
            """
            SELECT interaction_id
            FROM task_interactions
            WHERE last_state IS NULL
            """
        ).fetchall()
        for row in rows:
            event = connection.execute(
                """
                SELECT event_type, created_at
                FROM task_events
                WHERE causation_ref = ?
                  AND (
                      event_type LIKE 'task.interaction.%'
                      OR event_type = 'task.canceled'
                  )
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (row["interaction_id"],),
            ).fetchone()
            state = event_states.get(str(event["event_type"])) if event else None
            if state:
                connection.execute(
                    """
                    UPDATE task_interactions
                    SET last_state = ?, last_observed_at = ?
                    WHERE interaction_id = ?
                    """,
                    (state, event["created_at"], row["interaction_id"]),
                )

    @staticmethod
    def _repair_terminal_task_statuses(
        connection: sqlite3.Connection,
    ) -> None:
        connection.execute(
            """
            UPDATE agent_tasks
            SET status = 'succeeded',
                version = version + 1,
                updated_at = COALESCE(
                    (
                        SELECT MAX(task_artifacts.created_at)
                        FROM task_artifacts
                        WHERE task_artifacts.task_id = agent_tasks.task_id
                          AND task_artifacts.artifact_type = 'certificate_scan'
                    ),
                    updated_at
                ),
                finished_at = COALESCE(
                    (
                        SELECT MAX(task_artifacts.created_at)
                        FROM task_artifacts
                        WHERE task_artifacts.task_id = agent_tasks.task_id
                          AND task_artifacts.artifact_type = 'certificate_scan'
                    ),
                    updated_at
                )
            WHERE status IN ('active', 'waiting_user', 'running')
              AND title = 'Prepare and Deliver One OA Certificate Scan'
              AND EXISTS (
                  SELECT 1 FROM task_artifacts
                  WHERE task_artifacts.task_id = agent_tasks.task_id
                    AND task_artifacts.artifact_type = 'certificate_scan'
              )
            """
        )
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
                current_interaction_id = COALESCE(
                    (
                        SELECT task_interactions.interaction_id
                        FROM task_interactions
                        WHERE task_interactions.task_id =
                            agent_tasks.task_id
                          AND (
                              task_interactions.last_state IS NULL
                              OR task_interactions.last_state NOT IN (
                                  'pending', 'processing'
                              )
                          )
                        ORDER BY task_interactions.linked_at DESC
                        LIMIT 1
                    ),
                    current_interaction_id
                ),
                finished_at = COALESCE(
                    (
                        SELECT operations.finished_at
                        FROM operations
                        WHERE operations.operation_id =
                            agent_tasks.current_operation_id
                    ),
                    updated_at
                )
            WHERE status IN ('active', 'waiting_user', 'running')
              AND current_operation_id IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM operations
                  WHERE operations.operation_id =
                        agent_tasks.current_operation_id
                    AND operations.status = 'succeeded'
              )
            """
        )
        connection.execute(
            """
            UPDATE agent_tasks
            SET status = 'superseded',
                version = version + 1,
                updated_at = COALESCE(
                    (
                        SELECT task_interactions.last_observed_at
                        FROM task_interactions
                        WHERE task_interactions.interaction_id =
                            agent_tasks.current_interaction_id
                    ),
                    updated_at
                ),
                finished_at = COALESCE(
                    (
                        SELECT task_interactions.last_observed_at
                        FROM task_interactions
                        WHERE task_interactions.interaction_id =
                            agent_tasks.current_interaction_id
                    ),
                    updated_at
                )
            WHERE status IN ('active', 'waiting_user', 'running')
              AND current_interaction_id IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM task_interactions
                  WHERE task_interactions.interaction_id =
                        agent_tasks.current_interaction_id
                    AND task_interactions.last_state = 'superseded'
              )
            """
        )

    @staticmethod
    def _reconcile_pull_based_deliveries(
        connection: sqlite3.Connection,
    ) -> None:
        now = _utc_now()
        placeholders = ", ".join("?" for _ in PULL_BASED_CLIENT_TYPES)
        client_types = tuple(sorted(PULL_BASED_CLIENT_TYPES))
        connection.execute(
            f"""
            UPDATE task_subscriptions
            SET state = 'inactive', updated_at = ?
            WHERE state = 'active'
              AND endpoint_id IN (
                  SELECT endpoint_id FROM client_endpoints
                  WHERE LOWER(client_type) IN ({placeholders})
              )
            """,
            (now, *client_types),
        )
        connection.execute(
            f"""
            UPDATE notification_outbox
            SET state = 'acknowledged', updated_at = ?,
                acknowledged_at = COALESCE(acknowledged_at, ?)
            WHERE payload_type = 'task_event'
              AND state IN ('pending', 'delivering', 'failed')
              AND endpoint_id IN (
                  SELECT endpoint_id FROM client_endpoints
                  WHERE LOWER(client_type) IN ({placeholders})
              )
            """,
            (now, now, *client_types),
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

    def touch_endpoint(
        self,
        *,
        endpoint_id: str,
        user_subject: str,
    ) -> dict:
        endpoint_id = _required_text(endpoint_id, "endpoint_id", 256)
        user_subject = _required_text(user_subject, "user_subject", 256)
        now = _utc_now()
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE client_endpoints
                SET updated_at = ?, last_seen_at = ?
                WHERE endpoint_id = ? AND user_subject = ?
                  AND state = 'active'
                """,
                (now, now, endpoint_id, user_subject),
            )
            if updated.rowcount != 1:
                raise TaskNotFound("client endpoint not found")
            row = self._select_endpoint(connection, endpoint_id)
        return _endpoint_from_row(row)

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
            if not _is_pull_based_endpoint(endpoint):
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
                "SELECT * FROM task_interactions WHERE interaction_id = ?",
                (interaction_id,),
            ).fetchone()
            if linked is not None and linked["task_id"] != task_id:
                raise TaskIntegrityError("interaction is already linked to another task")
            if linked is None:
                connection.execute(
                    """
                    INSERT INTO task_interactions (
                        task_id, interaction_id, user_subject, linked_at,
                        last_state, last_observed_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        interaction_id,
                        user_subject,
                        now,
                        state,
                        now,
                    ),
                )
            elif linked["last_state"] != state:
                connection.execute(
                    """
                    UPDATE task_interactions
                    SET last_state = ?, last_observed_at = ?
                    WHERE interaction_id = ?
                    """,
                    (state, now, interaction_id),
                )
            state_changed = linked is None or linked["last_state"] != state
            event_changed = (
                linked is None
                or _event_type_for_interaction(
                    str(linked["last_state"] or "")
                )
                != event_type
            )
            may_update_task = _interaction_may_update_task(
                task=task,
                interaction_id=interaction_id,
                newly_linked=linked is None,
            )
            if may_update_task:
                self._update_task_state(
                    connection,
                    task_id=task_id,
                    status=task_status,
                    current_operation_id=task["current_operation_id"],
                    current_interaction_id=interaction_id,
                    now=now,
                )
            if may_update_task and state_changed and event_changed:
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

    def link_artifact(
        self,
        *,
        task_id: str,
        user_subject: str,
        artifact: dict[str, Any],
    ) -> tuple[dict, bool]:
        artifact_type = _required_text(
            artifact.get("artifact_type") or "file",
            "artifact_type",
            80,
        )
        source_ref = _required_text(
            artifact.get("source_ref"),
            "source_ref",
            256,
        )
        filename = _required_text(artifact.get("filename"), "filename", 240)
        content_type = _required_text(
            artifact.get("content_type"),
            "content_type",
            120,
        )
        byte_size = int(artifact.get("byte_size") or 0)
        if byte_size <= 0 or byte_size > 32 * 1024 * 1024:
            raise ValueError("artifact byte_size is invalid")
        download_url = _artifact_download_url(artifact.get("download_url"))
        expires_at = _required_future_time(
            artifact.get("expires_at"),
            "expires_at",
        )
        now = _utc_now()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._select_owned_task(connection, task_id, user_subject)
            existing = connection.execute(
                "SELECT * FROM task_artifacts WHERE source_ref = ?",
                (source_ref,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["task_id"] != task_id
                    or existing["user_subject"] != user_subject
                ):
                    raise TaskIntegrityError(
                        "artifact is already linked to another task or user"
                    )
                return _artifact_from_row(existing), True

            self._subscribe_companion_endpoints(
                connection,
                task_id=task_id,
                user_subject=user_subject,
                created_at=now,
            )
            artifact_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO task_artifacts (
                    artifact_id, task_id, user_subject, artifact_type,
                    source_ref, filename, content_type, byte_size,
                    download_url, state, created_at, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?, ?)
                """,
                (
                    artifact_id,
                    task_id,
                    user_subject,
                    artifact_type,
                    source_ref,
                    filename,
                    content_type,
                    byte_size,
                    download_url,
                    now,
                    now,
                    expires_at,
                ),
            )
            self._append_event(
                connection,
                task_id=task_id,
                user_subject=user_subject,
                event_type="task.artifact.ready",
                payload={
                    "artifactId": artifact_id,
                    "artifactType": artifact_type,
                    "filename": filename,
                    "contentType": content_type,
                    "size": byte_size,
                    "downloadUrl": download_url,
                    "expiresAt": expires_at,
                },
                causation_ref=source_ref,
                created_at=now,
            )
            row = connection.execute(
                "SELECT * FROM task_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return _artifact_from_row(row), False

    def get_artifact(
        self,
        *,
        task_id: str,
        artifact_id: str,
        user_subject: str,
        include_source_ref: bool = False,
    ) -> dict:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._select_owned_task(connection, task_id, user_subject)
            connection.execute(
                """
                UPDATE task_artifacts
                SET state = 'expired', updated_at = ?
                WHERE artifact_id = ? AND task_id = ? AND user_subject = ?
                  AND state = 'ready' AND expires_at <= ?
                """,
                (now, artifact_id, task_id, user_subject, now),
            )
            row = connection.execute(
                """
                SELECT * FROM task_artifacts
                WHERE artifact_id = ? AND task_id = ? AND user_subject = ?
                """,
                (artifact_id, task_id, user_subject),
            ).fetchone()
        if row is None:
            raise TaskNotFound(f"artifact not found: {artifact_id}")
        return _artifact_from_row(
            row,
            include_source_ref=include_source_ref,
        )

    def refresh_artifact(
        self,
        *,
        task_id: str,
        artifact_id: str,
        user_subject: str,
        expected_source_ref: str,
        artifact: dict[str, Any],
    ) -> dict:
        source_ref = _required_text(
            artifact.get("source_ref"),
            "source_ref",
            256,
        )
        filename = _required_text(artifact.get("filename"), "filename", 240)
        content_type = _required_text(
            artifact.get("content_type"),
            "content_type",
            120,
        )
        byte_size = int(artifact.get("byte_size") or 0)
        if byte_size <= 0 or byte_size > 32 * 1024 * 1024:
            raise ValueError("artifact byte_size is invalid")
        download_url = _artifact_download_url(artifact.get("download_url"))
        expires_at = _required_future_time(
            artifact.get("expires_at"),
            "expires_at",
        )
        expected_source_ref = _required_text(
            expected_source_ref,
            "expected_source_ref",
            256,
        )
        now = _utc_now()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._select_owned_task(connection, task_id, user_subject)
            row = connection.execute(
                """
                SELECT * FROM task_artifacts
                WHERE artifact_id = ? AND task_id = ? AND user_subject = ?
                """,
                (artifact_id, task_id, user_subject),
            ).fetchone()
            if row is None:
                raise TaskNotFound(f"artifact not found: {artifact_id}")
            if row["source_ref"] != expected_source_ref:
                raise TaskIntegrityError(
                    "artifact source changed while the download was being refreshed"
                )
            if row["artifact_type"] != "certificate_scan":
                raise TaskIntegrityError("artifact type cannot be refreshed")
            if row["state"] != "expired":
                raise TaskIntegrityError("only an expired artifact can be refreshed")
            conflict = connection.execute(
                """
                SELECT artifact_id FROM task_artifacts
                WHERE source_ref = ? AND artifact_id <> ?
                """,
                (source_ref, artifact_id),
            ).fetchone()
            if conflict is not None:
                raise TaskIntegrityError(
                    "replacement download is already linked to another artifact"
                )
            connection.execute(
                """
                UPDATE task_artifacts
                SET source_ref = ?, filename = ?, content_type = ?,
                    byte_size = ?, download_url = ?, state = 'ready',
                    updated_at = ?, expires_at = ?
                WHERE artifact_id = ?
                """,
                (
                    source_ref,
                    filename,
                    content_type,
                    byte_size,
                    download_url,
                    now,
                    expires_at,
                    artifact_id,
                ),
            )
            connection.execute(
                """
                UPDATE agent_tasks
                SET version = version + 1, updated_at = ?
                WHERE task_id = ?
                """,
                (now, task_id),
            )
            self._append_event(
                connection,
                task_id=task_id,
                user_subject=user_subject,
                event_type="task.artifact.refreshed",
                payload={
                    "artifactId": artifact_id,
                    "artifactType": row["artifact_type"],
                    "filename": filename,
                    "contentType": content_type,
                    "size": byte_size,
                    "downloadUrl": download_url,
                    "expiresAt": expires_at,
                },
                causation_ref=source_ref,
                created_at=now,
            )
            refreshed = connection.execute(
                "SELECT * FROM task_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return _artifact_from_row(refreshed, include_source_ref=True)

    def complete_task(
        self,
        *,
        task_id: str,
        user_subject: str,
        reason: str,
        causation_ref: str | None = None,
    ) -> dict:
        completion_reason = _required_text(reason, "reason", 120)
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._select_owned_task(connection, task_id, user_subject)
            if task["status"] == "succeeded":
                return _task_from_row(task)
            if task["status"] not in ACTIVE_TASK_STATUSES:
                raise TaskIntegrityError(
                    f"terminal task cannot be completed: {task['status']}"
                )
            self._subscribe_companion_endpoints(
                connection,
                task_id=task_id,
                user_subject=user_subject,
                created_at=now,
            )
            self._update_task_state(
                connection,
                task_id=task_id,
                status="succeeded",
                current_operation_id=task["current_operation_id"],
                current_interaction_id=task["current_interaction_id"],
                now=now,
            )
            self._append_event(
                connection,
                task_id=task_id,
                user_subject=user_subject,
                event_type="task.completed",
                payload={"status": "succeeded", "reason": completion_reason},
                causation_ref=causation_ref,
                created_at=now,
            )
            row = self._select_task(connection, task_id)
        return _task_from_row(row)

    def list_artifacts(
        self,
        *,
        task_id: str,
        user_subject: str,
        limit: int = 100,
    ) -> list[dict]:
        limit = min(max(int(limit), 1), 500)
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._select_owned_task(connection, task_id, user_subject)
            connection.execute(
                """
                UPDATE task_artifacts
                SET state = 'expired', updated_at = ?
                WHERE task_id = ? AND user_subject = ?
                  AND state = 'ready' AND expires_at <= ?
                """,
                (now, task_id, user_subject, now),
            )
            rows = connection.execute(
                """
                SELECT * FROM task_artifacts
                WHERE task_id = ? AND user_subject = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (task_id, user_subject, limit),
            ).fetchall()
        return [_artifact_from_row(row) for row in rows]

    def list_user_artifacts(
        self,
        *,
        user_subject: str,
        limit: int = 100,
    ) -> list[dict]:
        limit = min(max(int(limit), 1), 500)
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE task_artifacts
                SET state = 'expired', updated_at = ?
                WHERE user_subject = ? AND state = 'ready' AND expires_at <= ?
                """,
                (now, user_subject, now),
            )
            rows = connection.execute(
                """
                SELECT * FROM task_artifacts
                WHERE user_subject = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_subject, limit),
            ).fetchall()
        return [_artifact_from_row(row) for row in rows]

    def append_timeline_message(
        self,
        *,
        user_subject: str,
        source_endpoint_id: str,
        message_key: str,
        role: str,
        text: str,
        task_id: str | None = None,
    ) -> tuple[dict, bool]:
        user_subject = _required_text(user_subject, "user_subject", 256)
        message_key = _required_text(message_key, "message_key", 768)
        if role not in {"user", "assistant"}:
            raise ValueError("timeline role is invalid")
        normalized_text = _timeline_text(text)
        normalized_task_id = (
            _required_text(task_id, "task_id", 128) if task_id else None
        )
        now = _utc_now()
        dedupe_key = f"message:{message_key}"

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            endpoint = self._select_endpoint(connection, source_endpoint_id)
            if (
                endpoint["user_subject"] != user_subject
                or endpoint["state"] != "active"
            ):
                raise TaskNotFound("client endpoint not found")
            if normalized_task_id:
                self._select_owned_task(
                    connection,
                    normalized_task_id,
                    user_subject,
                )
            existing = connection.execute(
                """
                SELECT * FROM user_timeline
                WHERE user_subject = ? AND dedupe_key = ?
                """,
                (user_subject, dedupe_key),
            ).fetchone()
            if existing is not None:
                return _timeline_from_row(existing), True

            entry_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO user_timeline (
                    entry_id, user_subject, entry_type, dedupe_key,
                    source_endpoint_id, task_id, role, text,
                    payload_json, created_at
                ) VALUES (?, ?, 'chat_message', ?, ?, ?, ?, ?, '{}', ?)
                """,
                (
                    entry_id,
                    user_subject,
                    dedupe_key,
                    source_endpoint_id,
                    normalized_task_id,
                    role,
                    normalized_text,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM user_timeline WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()
            self._enqueue_timeline_message(
                connection,
                entry=row,
                source_endpoint_id=source_endpoint_id,
            )
        return _timeline_from_row(row), False

    def list_timeline(
        self,
        *,
        user_subject: str,
        after_sequence: int | None = None,
        limit: int = 200,
    ) -> list[dict]:
        limit = min(max(int(limit), 1), 500)
        with self._connect() as connection:
            if after_sequence is None:
                rows = connection.execute(
                    """
                    SELECT * FROM user_timeline
                    WHERE user_subject = ?
                    ORDER BY sequence DESC
                    LIMIT ?
                    """,
                    (user_subject, limit),
                ).fetchall()
                rows = list(reversed(rows))
            else:
                after_sequence = max(int(after_sequence), 0)
                rows = connection.execute(
                    """
                    SELECT * FROM user_timeline
                    WHERE user_subject = ? AND sequence > ?
                    ORDER BY sequence
                    LIMIT ?
                    """,
                    (user_subject, after_sequence, limit),
                ).fetchall()
        return [_timeline_from_row(row) for row in rows]

    def latest_timeline_sequence(self, *, user_subject: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT MAX(sequence) AS sequence
                FROM user_timeline
                WHERE user_subject = ?
                """,
                (user_subject,),
            ).fetchone()
        return int(row["sequence"] or 0)

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

    def continuation_candidates(
        self,
        *,
        user_subject: str,
        agent_host: str,
        endpoint_id: str,
        active_only: bool = False,
        cross_endpoint_only: bool = False,
        source_client_type: str | None = None,
        max_age_minutes: int = 1_440,
        limit: int = 8,
    ) -> list[dict]:
        limit = min(max(int(limit), 1), 20)
        max_age_minutes = min(max(int(max_age_minutes), 1), 10_080)
        cutoff = _utc_before(minutes=max_age_minutes)
        normalized_source = _optional_text(
            source_client_type,
            "source_client_type",
            80,
        )
        source_types = _client_type_family(normalized_source)
        query = """
            SELECT task.*
            FROM agent_tasks AS task
            JOIN client_endpoints AS origin
              ON origin.endpoint_id = task.origin_endpoint_id
            WHERE task.user_subject = ? AND task.agent_host = ?
              AND task.updated_at >= ?
        """
        parameters: list[Any] = [user_subject, agent_host, cutoff]
        if active_only:
            query += " AND task.status IN ('active', 'waiting_user', 'running')"
        if cross_endpoint_only:
            query += " AND task.origin_endpoint_id <> ?"
            parameters.append(endpoint_id)
        if source_types:
            placeholders = ", ".join("?" for _ in source_types)
            query += f" AND LOWER(origin.client_type) IN ({placeholders})"
            parameters.extend(source_types)
        query += " ORDER BY task.updated_at DESC, task.created_at DESC LIMIT ?"
        parameters.append(limit)

        with self._connect() as connection:
            endpoint = self._select_endpoint(connection, endpoint_id)
            if (
                endpoint["user_subject"] != user_subject
                or endpoint["agent_host"] != agent_host
                or endpoint["state"] != "active"
            ):
                raise TaskNotFound("client endpoint not found")
            rows = connection.execute(query, parameters).fetchall()
            candidates = []
            for row in rows:
                origin = self._select_endpoint(
                    connection,
                    row["origin_endpoint_id"],
                )
                candidates.append(
                    {
                        "task": _task_from_row(row),
                        "origin_endpoint": _endpoint_from_row(origin),
                    }
                )
        return candidates

    def set_continuation_candidates(
        self,
        *,
        user_subject: str,
        agent_host: str,
        endpoint_id: str,
        candidate_task_ids: list[str],
        reason: str | None = None,
        ttl_seconds: int = 600,
    ) -> tuple[dict, bool]:
        normalized_ids = list(
            dict.fromkeys(
                _required_text(value, "candidate_task_id", 128)
                for value in candidate_task_ids
            )
        )
        if not normalized_ids or len(normalized_ids) > 20:
            raise ValueError("candidate_task_ids are invalid")
        normalized_reason = _optional_text(reason, "reason", 120)
        candidate_json = _canonical_json(normalized_ids)
        now = _utc_now()
        expires_at = _utc_after(min(max(int(ttl_seconds), 60), 3_600))

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            endpoint = self._select_endpoint(connection, endpoint_id)
            if (
                endpoint["user_subject"] != user_subject
                or endpoint["agent_host"] != agent_host
                or endpoint["state"] != "active"
            ):
                raise TaskNotFound("client endpoint not found")
            for task_id in normalized_ids:
                task = self._select_owned_task(connection, task_id, user_subject)
                if task["agent_host"] != agent_host:
                    raise TaskNotFound(f"task not found: {task_id}")
            existing = connection.execute(
                "SELECT * FROM task_continuations WHERE endpoint_id = ?",
                (endpoint_id,),
            ).fetchone()
            reused = bool(
                existing is not None
                and existing["state"] == "awaiting_selection"
                and existing["candidate_task_ids_json"] == candidate_json
                and existing["reason"] == normalized_reason
                and existing["expires_at"] > now
            )
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO task_continuations (
                        endpoint_id, user_subject, agent_host,
                        selected_task_id, candidate_task_ids_json, state,
                        execution_mode, reason, expires_at, version,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, NULL, ?, 'awaiting_selection',
                              'observe_only', ?, ?, 1, ?, ?)
                    """,
                    (
                        endpoint_id,
                        user_subject,
                        agent_host,
                        candidate_json,
                        normalized_reason,
                        expires_at,
                        now,
                        now,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE task_continuations
                    SET selected_task_id = NULL,
                        candidate_task_ids_json = ?,
                        state = 'awaiting_selection',
                        execution_mode = 'observe_only', reason = ?,
                        expires_at = ?, updated_at = ?,
                        version = version + ?
                    WHERE endpoint_id = ?
                    """,
                    (
                        candidate_json,
                        normalized_reason,
                        expires_at,
                        now,
                        0 if reused else 1,
                        endpoint_id,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM task_continuations WHERE endpoint_id = ?",
                (endpoint_id,),
            ).fetchone()
        return _continuation_from_row(row), reused

    def select_continuation(
        self,
        *,
        user_subject: str,
        agent_host: str,
        endpoint_id: str,
        task_id: str,
        execution_mode: str,
        reason: str | None = None,
        ttl_seconds: int = 21_600,
    ) -> tuple[dict, dict, dict, bool]:
        task_id = _required_text(task_id, "task_id", 128)
        if execution_mode not in CONTINUATION_EXECUTION_MODES:
            raise ValueError("execution_mode is invalid")
        normalized_reason = _optional_text(reason, "reason", 120)
        now = _utc_now()
        expires_at = _utc_after(min(max(int(ttl_seconds), 300), 86_400))

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            endpoint = self._select_endpoint(connection, endpoint_id)
            if (
                endpoint["user_subject"] != user_subject
                or endpoint["agent_host"] != agent_host
                or endpoint["state"] != "active"
            ):
                raise TaskNotFound("client endpoint not found")
            task = self._select_owned_task(connection, task_id, user_subject)
            if task["agent_host"] != agent_host:
                raise TaskNotFound(f"task not found: {task_id}")
            existing = connection.execute(
                "SELECT * FROM task_continuations WHERE endpoint_id = ?",
                (endpoint_id,),
            ).fetchone()
            reused = bool(
                existing is not None
                and existing["state"] == "selected"
                and existing["selected_task_id"] == task_id
                and existing["execution_mode"] == execution_mode
                and existing["reason"] == normalized_reason
                and existing["expires_at"] > now
            )
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO task_continuations (
                        endpoint_id, user_subject, agent_host,
                        selected_task_id, candidate_task_ids_json, state,
                        execution_mode, reason, expires_at, version,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, '[]', 'selected', ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        endpoint_id,
                        user_subject,
                        agent_host,
                        task_id,
                        execution_mode,
                        normalized_reason,
                        expires_at,
                        now,
                        now,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE task_continuations
                    SET selected_task_id = ?, candidate_task_ids_json = '[]',
                        state = 'selected', execution_mode = ?, reason = ?,
                        expires_at = ?, updated_at = ?,
                        version = version + ?
                    WHERE endpoint_id = ?
                    """,
                    (
                        task_id,
                        execution_mode,
                        normalized_reason,
                        expires_at,
                        now,
                        0 if reused else 1,
                        endpoint_id,
                    ),
                )
            if task["active_conversation_ref"] != endpoint["conversation_ref"]:
                connection.execute(
                    """
                    UPDATE agent_tasks
                    SET active_conversation_ref = ?, version = version + 1,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (endpoint["conversation_ref"], now, task_id),
                )
            if not reused:
                self._append_event(
                    connection,
                    task_id=task_id,
                    user_subject=user_subject,
                    event_type="task.continuation.selected",
                    payload={
                        "endpointId": endpoint_id,
                        "executionMode": execution_mode,
                    },
                    causation_ref=endpoint_id,
                    created_at=now,
                )
            row = connection.execute(
                "SELECT * FROM task_continuations WHERE endpoint_id = ?",
                (endpoint_id,),
            ).fetchone()
            selected_task = self._select_task(connection, task_id)
        return (
            _continuation_from_row(row),
            _task_from_row(selected_task),
            _endpoint_from_row(endpoint),
            reused,
        )

    def select_continuation_candidate(
        self,
        *,
        user_subject: str,
        agent_host: str,
        endpoint_id: str,
        ordinal: int,
        execution_mode: str,
        reason: str | None = None,
        ttl_seconds: int = 21_600,
    ) -> tuple[dict, dict, dict, bool]:
        continuation = self.get_continuation(
            user_subject=user_subject,
            agent_host=agent_host,
            endpoint_id=endpoint_id,
        )
        if continuation is None or continuation["state"] != "awaiting_selection":
            raise TaskNotFound("task continuation choices not found")
        candidates = continuation["candidate_task_ids"]
        index = int(ordinal) - 1
        if index < 0 or index >= len(candidates):
            raise TaskNotFound("task continuation choice not found")
        return self.select_continuation(
            user_subject=user_subject,
            agent_host=agent_host,
            endpoint_id=endpoint_id,
            task_id=candidates[index],
            execution_mode=execution_mode,
            reason=reason,
            ttl_seconds=ttl_seconds,
        )

    def get_continuation(
        self,
        *,
        user_subject: str,
        agent_host: str,
        endpoint_id: str,
    ) -> dict | None:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            endpoint = self._select_endpoint(connection, endpoint_id)
            if (
                endpoint["user_subject"] != user_subject
                or endpoint["agent_host"] != agent_host
                or endpoint["state"] != "active"
            ):
                raise TaskNotFound("client endpoint not found")
            row = connection.execute(
                "SELECT * FROM task_continuations WHERE endpoint_id = ?",
                (endpoint_id,),
            ).fetchone()
            if row is None:
                return None
            if row["state"] in {"awaiting_selection", "selected"} and row[
                "expires_at"
            ] <= now:
                connection.execute(
                    """
                    UPDATE task_continuations
                    SET state = 'expired', selected_task_id = NULL,
                        candidate_task_ids_json = '[]', updated_at = ?,
                        version = version + 1
                    WHERE endpoint_id = ?
                    """,
                    (now, endpoint_id),
                )
                row = connection.execute(
                    "SELECT * FROM task_continuations WHERE endpoint_id = ?",
                    (endpoint_id,),
                ).fetchone()
        return _continuation_from_row(row)

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

    def list_continuations(
        self,
        *,
        user_subject: str,
        limit: int = 100,
    ) -> list[dict]:
        limit = min(max(int(limit), 1), 500)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM task_continuations
                WHERE user_subject = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_subject, limit),
            ).fetchall()
        return [_continuation_from_row(row) for row in rows]

    def runtime_diagnostics(self) -> dict[str, Any]:
        return self.inspect_runtime(self.db_path)

    @staticmethod
    def inspect_runtime(db_path: Path | str) -> dict[str, Any]:
        """Read task-hub health without exposing message or business payloads."""

        resolved = Path(db_path).resolve().as_posix()
        connection = sqlite3.connect(
            f"file:{quote(resolved, safe='/:')}?mode=ro",
            uri=True,
            timeout=10,
        )
        connection.row_factory = sqlite3.Row
        try:
            users = {
                str(row["user_subject"])
                for table in (
                    "client_endpoints",
                    "agent_tasks",
                    "task_artifacts",
                    "user_timeline",
                    "notification_outbox",
                    "task_continuations",
                )
                for row in connection.execute(
                    f"SELECT DISTINCT user_subject FROM {table}"
                ).fetchall()
            }
            has_workspace_accounts = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'workspace_accounts'
                """
            ).fetchone() is not None
            has_identity_tokens = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'mcp_identity_tokens'
                """
            ).fetchone() is not None
            if has_workspace_accounts:
                users.update(
                    str(row["user_subject"])
                    for row in connection.execute(
                        "SELECT DISTINCT user_subject FROM workspace_accounts"
                    ).fetchall()
                )

            user_records = []
            for user_subject in sorted(users):
                endpoint_rows = connection.execute(
                    """
                    SELECT client_type, state, COUNT(*) AS count,
                           MAX(last_seen_at) AS last_seen_at
                    FROM client_endpoints
                    WHERE user_subject = ?
                    GROUP BY client_type, state
                    ORDER BY client_type, state
                    """,
                    (user_subject,),
                ).fetchall()
                task_rows = connection.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM agent_tasks
                    WHERE user_subject = ?
                    GROUP BY status ORDER BY status
                    """,
                    (user_subject,),
                ).fetchall()
                outbox_rows = connection.execute(
                    """
                    SELECT state, COUNT(*) AS count
                    FROM notification_outbox
                    WHERE user_subject = ?
                    GROUP BY state ORDER BY state
                    """,
                    (user_subject,),
                ).fetchall()
                continuation_rows = connection.execute(
                    """
                    SELECT state, execution_mode, COUNT(*) AS count
                    FROM task_continuations
                    WHERE user_subject = ?
                    GROUP BY state, execution_mode
                    ORDER BY state, execution_mode
                    """,
                    (user_subject,),
                ).fetchall()
                artifact_rows = connection.execute(
                    """
                    SELECT state, COUNT(*) AS count
                    FROM task_artifacts
                    WHERE user_subject = ?
                    GROUP BY state ORDER BY state
                    """,
                    (user_subject,),
                ).fetchall()
                timeline = connection.execute(
                    """
                    SELECT COUNT(*) AS count, MAX(sequence) AS latest_sequence,
                           MAX(created_at) AS latest_at
                    FROM user_timeline WHERE user_subject = ?
                    """,
                    (user_subject,),
                ).fetchone()
                oldest_outstanding = connection.execute(
                    """
                    SELECT MIN(created_at) AS oldest_at
                    FROM notification_outbox
                    WHERE user_subject = ?
                      AND state IN ('pending', 'delivering', 'deferred')
                    """,
                    (user_subject,),
                ).fetchone()
                workspace_count = 0
                if has_workspace_accounts:
                    workspace_count = int(
                        connection.execute(
                            """
                            SELECT COUNT(*) AS count FROM workspace_accounts
                            WHERE user_subject = ? AND state = 'active'
                            """,
                            (user_subject,),
                        ).fetchone()["count"]
                    )
                user_records.append(
                    {
                        "user_subject": user_subject,
                        "endpoints": [
                            {
                                "client_type": row["client_type"],
                                "state": row["state"],
                                "count": int(row["count"]),
                                "last_seen_at": row["last_seen_at"],
                            }
                            for row in endpoint_rows
                        ],
                        "active_workspace_accounts": workspace_count,
                        "task_statuses": {
                            str(row["status"]): int(row["count"])
                            for row in task_rows
                        },
                        "timeline_entries": int(timeline["count"] or 0),
                        "latest_timeline_sequence": int(
                            timeline["latest_sequence"] or 0
                        ),
                        "latest_timeline_at": timeline["latest_at"],
                        "outbox_states": {
                            str(row["state"]): int(row["count"])
                            for row in outbox_rows
                        },
                        "task_continuations": [
                            {
                                "state": str(row["state"]),
                                "execution_mode": str(row["execution_mode"]),
                                "count": int(row["count"]),
                            }
                            for row in continuation_rows
                        ],
                        "artifact_states": {
                            str(row["state"]): int(row["count"])
                            for row in artifact_rows
                        },
                        "oldest_outstanding_delivery_at": oldest_outstanding[
                            "oldest_at"
                        ],
                    }
                )

            violation_queries = {
                "task_origin_user_mismatch": """
                    SELECT COUNT(*) AS count
                    FROM agent_tasks AS task
                    LEFT JOIN client_endpoints AS endpoint
                      ON endpoint.endpoint_id = task.origin_endpoint_id
                    WHERE endpoint.endpoint_id IS NULL
                       OR endpoint.user_subject <> task.user_subject
                """,
                "task_event_user_mismatch": """
                    SELECT COUNT(*) AS count
                    FROM task_events AS event
                    LEFT JOIN agent_tasks AS task ON task.task_id = event.task_id
                    WHERE task.task_id IS NULL
                       OR task.user_subject <> event.user_subject
                """,
                "task_operation_user_mismatch": """
                    SELECT COUNT(*) AS count
                    FROM task_operations AS link
                    LEFT JOIN agent_tasks AS task ON task.task_id = link.task_id
                    WHERE task.task_id IS NULL
                       OR task.user_subject <> link.user_subject
                """,
                "task_interaction_user_mismatch": """
                    SELECT COUNT(*) AS count
                    FROM task_interactions AS link
                    LEFT JOIN agent_tasks AS task ON task.task_id = link.task_id
                    WHERE task.task_id IS NULL
                       OR task.user_subject <> link.user_subject
                """,
                "task_artifact_user_mismatch": """
                    SELECT COUNT(*) AS count
                    FROM task_artifacts AS artifact
                    LEFT JOIN agent_tasks AS task
                      ON task.task_id = artifact.task_id
                    WHERE task.task_id IS NULL
                       OR task.user_subject <> artifact.user_subject
                """,
                "subscription_user_mismatch": """
                    SELECT COUNT(*) AS count
                    FROM task_subscriptions AS subscription
                    LEFT JOIN agent_tasks AS task
                      ON task.task_id = subscription.task_id
                    LEFT JOIN client_endpoints AS endpoint
                      ON endpoint.endpoint_id = subscription.endpoint_id
                    WHERE task.task_id IS NULL OR endpoint.endpoint_id IS NULL
                       OR task.user_subject <> subscription.user_subject
                       OR endpoint.user_subject <> subscription.user_subject
                """,
                "outbox_user_mismatch": """
                    SELECT COUNT(*) AS count
                    FROM notification_outbox AS delivery
                    LEFT JOIN agent_tasks AS task
                      ON task.task_id = NULLIF(delivery.task_id, '')
                    LEFT JOIN client_endpoints AS endpoint
                      ON endpoint.endpoint_id = delivery.endpoint_id
                    WHERE endpoint.endpoint_id IS NULL
                       OR endpoint.user_subject <> delivery.user_subject
                       OR (
                           delivery.task_id <> ''
                           AND (
                               task.task_id IS NULL
                               OR task.user_subject <> delivery.user_subject
                           )
                       )
                """,
                "timeline_user_mismatch": """
                    SELECT COUNT(*) AS count
                    FROM user_timeline AS timeline
                    LEFT JOIN client_endpoints AS endpoint
                      ON endpoint.endpoint_id = timeline.source_endpoint_id
                    LEFT JOIN agent_tasks AS task ON task.task_id = timeline.task_id
                    WHERE (
                        timeline.source_endpoint_id IS NOT NULL
                        AND (
                            endpoint.endpoint_id IS NULL
                            OR endpoint.user_subject <> timeline.user_subject
                        )
                    ) OR (
                        timeline.task_id IS NOT NULL
                        AND (
                            task.task_id IS NULL
                            OR task.user_subject <> timeline.user_subject
                        )
                    )
                """,
                "continuation_binding_mismatch": """
                    SELECT COUNT(*) AS count
                    FROM task_continuations AS continuation
                    LEFT JOIN client_endpoints AS endpoint
                      ON endpoint.endpoint_id = continuation.endpoint_id
                    LEFT JOIN agent_tasks AS selected_task
                      ON selected_task.task_id = continuation.selected_task_id
                    WHERE endpoint.endpoint_id IS NULL
                       OR endpoint.user_subject <> continuation.user_subject
                       OR endpoint.agent_host <> continuation.agent_host
                       OR (
                           continuation.selected_task_id IS NOT NULL
                           AND (
                               selected_task.task_id IS NULL
                               OR selected_task.user_subject <>
                                  continuation.user_subject
                               OR selected_task.agent_host <>
                                  continuation.agent_host
                           )
                       )
                       OR EXISTS (
                           SELECT 1
                           FROM json_each(
                               continuation.candidate_task_ids_json
                           ) AS candidate
                           LEFT JOIN agent_tasks AS candidate_task
                             ON candidate_task.task_id = candidate.value
                           WHERE candidate_task.task_id IS NULL
                              OR candidate_task.user_subject <>
                                 continuation.user_subject
                              OR candidate_task.agent_host <>
                                 continuation.agent_host
                       )
                """,
            }
            if has_identity_tokens:
                violation_queries["endpoint_token_user_mismatch"] = """
                    SELECT COUNT(*) AS count
                    FROM client_endpoints AS endpoint
                    LEFT JOIN mcp_identity_tokens AS token
                      ON token.token_id = endpoint.token_id
                    WHERE endpoint.token_id NOT LIKE 'workspace-account:%'
                      AND (
                          token.token_id IS NULL
                          OR token.user_subject <> endpoint.user_subject
                      )
                """
            if has_workspace_accounts:
                violation_queries["workspace_endpoint_user_mismatch"] = """
                    SELECT COUNT(*) AS count
                    FROM workspace_accounts AS account
                    LEFT JOIN client_endpoints AS endpoint
                      ON endpoint.endpoint_id = account.endpoint_id
                    WHERE account.endpoint_id IS NULL
                       OR endpoint.endpoint_id IS NULL
                       OR endpoint.user_subject <> account.user_subject
                """
            violations = {
                name: int(connection.execute(query).fetchone()["count"])
                for name, query in violation_queries.items()
            }
            totals = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM client_endpoints
                     WHERE state = 'active') AS active_endpoints,
                    (SELECT COUNT(*) FROM agent_tasks
                     WHERE status IN ('active', 'waiting_user', 'running'))
                        AS active_tasks,
                    (SELECT COUNT(*) FROM notification_outbox
                     WHERE state IN ('pending', 'delivering', 'deferred'))
                        AS outstanding_deliveries,
                    (SELECT COUNT(*) FROM notification_outbox
                     WHERE state = 'deferred') AS deferred_deliveries,
                    (SELECT COUNT(*) FROM notification_outbox
                     WHERE state = 'failed') AS failed_deliveries,
                    (SELECT COUNT(*) FROM task_continuations
                     WHERE state IN ('awaiting_selection', 'selected'))
                        AS active_task_continuations,
                    (SELECT COUNT(*) FROM task_artifacts
                     WHERE state = 'ready'
                       AND datetime(expires_at) > datetime('now'))
                        AS ready_artifacts,
                    (SELECT COUNT(*) FROM task_artifacts
                     WHERE state = 'expired'
                        OR datetime(expires_at) <= datetime('now'))
                        AS expired_artifacts,
                    (SELECT COUNT(*) FROM user_timeline) AS timeline_entries
                """
            ).fetchone()
        finally:
            connection.close()

        violation_count = sum(violations.values())
        return {
            "generated_at": _utc_now(),
            "summary": {
                "users": len(user_records),
                "active_endpoints": int(totals["active_endpoints"]),
                "active_tasks": int(totals["active_tasks"]),
                "outstanding_deliveries": int(totals["outstanding_deliveries"]),
                "deferred_deliveries": int(totals["deferred_deliveries"]),
                "failed_deliveries": int(totals["failed_deliveries"]),
                "active_task_continuations": int(
                    totals["active_task_continuations"]
                ),
                "ready_artifacts": int(totals["ready_artifacts"]),
                "expired_artifacts": int(totals["expired_artifacts"]),
                "timeline_entries": int(totals["timeline_entries"]),
                "isolation_violation_count": violation_count,
            },
            "users": user_records,
            "isolation": {
                "passed": violation_count == 0,
                "violations": violations,
            },
        }

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
                if after_event_id.startswith("time:"):
                    cursor_time = after_event_id.removeprefix("time:")
                    try:
                        parsed = datetime.fromisoformat(cursor_time)
                    except ValueError as exc:
                        raise TaskNotFound(
                            f"task event cursor is invalid: {after_event_id}"
                        ) from exc
                    if parsed.tzinfo is None:
                        raise TaskNotFound(
                            f"task event cursor is invalid: {after_event_id}"
                        )
                    query += " AND created_at > ?"
                    parameters.append(parsed.isoformat())
                else:
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

    def latest_user_event_id(self, *, user_subject: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT event_id FROM task_events
                WHERE user_subject = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (user_subject,),
            ).fetchone()
        return str(row["event_id"]) if row is not None else None

    def current_user_event_cursor(self, *, user_subject: str) -> str:
        return (
            self.latest_user_event_id(user_subject=user_subject)
            or f"time:{_utc_now()}"
        )

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
        newest_first: bool = False,
    ) -> list[dict]:
        limit = min(max(int(limit), 1), 500)
        query = "SELECT * FROM notification_outbox WHERE user_subject = ?"
        parameters: list[Any] = [user_subject]
        if endpoint_id:
            query += " AND endpoint_id = ?"
            parameters.append(endpoint_id)
        direction = "DESC" if newest_first else "ASC"
        query += f" ORDER BY created_at {direction}, rowid {direction} LIMIT ?"
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
            endpoint = self._select_endpoint(connection, endpoint_id)
            if (
                endpoint["user_subject"] != user_subject
                or endpoint["state"] != "active"
            ):
                raise TaskNotFound("client endpoint not found")
            candidate = connection.execute(
                """
                SELECT 1 FROM notification_outbox
                WHERE user_subject = ? AND endpoint_id = ?
                  AND (
                    (
                      attempt_count < 5
                      AND (
                        state = 'pending'
                        OR (state = 'delivering' AND next_attempt_at <= ?)
                      )
                    )
                    OR (
                      state = 'delivering'
                      AND attempt_count >= 5
                      AND next_attempt_at <= ?
                    )
                  )
                LIMIT 1
                """,
                (user_subject, endpoint_id, now, now),
            ).fetchone()
            if candidate is None:
                return []

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
        defer_until_activity: bool = False,
    ) -> dict:
        if succeeded and defer_until_activity:
            raise ValueError(
                "successful delivery cannot be deferred until endpoint activity"
            )
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
            elif defer_until_activity:
                connection.execute(
                    """
                    UPDATE notification_outbox
                    SET state = 'deferred', next_attempt_at = ?, updated_at = ?,
                        acknowledged_at = NULL
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

    def reactivate_deferred_outbox(
        self,
        *,
        user_subject: str,
        endpoint_id: str,
        delay_seconds: int = 5,
    ) -> int:
        delay_seconds = min(max(int(delay_seconds), 0), 300)
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            endpoint = self._select_endpoint(connection, endpoint_id)
            if (
                endpoint["user_subject"] != user_subject
                or endpoint["state"] != "active"
            ):
                raise TaskNotFound("client endpoint not found")
            cursor = connection.execute(
                """
                UPDATE notification_outbox
                SET state = 'pending', attempt_count = 0,
                    next_attempt_at = ?, updated_at = ?,
                    acknowledged_at = NULL
                WHERE user_subject = ? AND endpoint_id = ?
                  AND state = 'deferred'
                """,
                (
                    _utc_after(delay_seconds),
                    now,
                    user_subject,
                    endpoint_id,
                ),
            )
        return int(cursor.rowcount)

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
            SELECT endpoint_id, client_type, capabilities_json
            FROM client_endpoints
            WHERE user_subject = ? AND state = 'active'
            """,
            (user_subject,),
        ).fetchall()
        for endpoint in endpoints:
            if _is_pull_based_endpoint(endpoint):
                continue
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
                ON CONFLICT(task_id, endpoint_id) DO UPDATE SET
                    event_filters_json = excluded.event_filters_json,
                    state = 'active',
                    updated_at = excluded.updated_at
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
                            "task.artifact.ready",
                            "task.completed",
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
        connection.execute(
            """
            INSERT OR IGNORE INTO user_timeline (
                entry_id, user_subject, entry_type, dedupe_key,
                source_endpoint_id, task_id, role, text,
                payload_json, created_at
            ) VALUES (?, ?, 'task_event', ?, NULL, ?, NULL, NULL, ?, ?)
            """,
            (
                event_id,
                user_subject,
                f"task-event:{event_id}",
                task_id,
                _canonical_json(
                    {
                        "eventId": event_id,
                        "eventType": event_type,
                        "payload": payload,
                    }
                ),
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
    def _enqueue_timeline_message(
        connection: sqlite3.Connection,
        *,
        entry: sqlite3.Row,
        source_endpoint_id: str,
    ) -> None:
        source = connection.execute(
            """
            SELECT endpoint_id, client_type, label
            FROM client_endpoints
            WHERE endpoint_id = ?
            """,
            (source_endpoint_id,),
        ).fetchone()
        endpoints = connection.execute(
            """
            SELECT endpoint_id, client_type, capabilities_json
            FROM client_endpoints
            WHERE user_subject = ? AND state = 'active'
              AND endpoint_id != ?
            """,
            (entry["user_subject"], source_endpoint_id),
        ).fetchall()
        payload = _canonical_json(
            {
                "entryId": entry["entry_id"],
                "sequence": entry["sequence"],
                "role": entry["role"],
                "text": entry["text"],
                "createdAt": entry["created_at"],
                "source": {
                    "endpointId": source_endpoint_id,
                    "clientType": source["client_type"] if source else "unknown",
                    "label": source["label"] if source else None,
                },
            }
        )
        for endpoint in endpoints:
            capabilities = set(json.loads(endpoint["capabilities_json"]))
            if endpoint["client_type"] in {"web", "webchat"}:
                continue
            if not capabilities.intersection(
                {"direct_status", "timeline_message"}
            ):
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO notification_outbox (
                    delivery_id, event_id, task_id, endpoint_id, user_subject,
                    payload_type, payload_json, state, attempt_count,
                    next_attempt_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'timeline_message', ?,
                          'pending', 0, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    entry["entry_id"],
                    entry["task_id"] or "",
                    endpoint["endpoint_id"],
                    entry["user_subject"],
                    payload,
                    entry["created_at"],
                    entry["created_at"],
                    entry["created_at"],
                ),
            )

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
            "superseded",
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
        "requires_user_action": "task.operation.requires_user_action",
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
        "superseded": "superseded",
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


def _interaction_may_update_task(
    *,
    task: sqlite3.Row,
    interaction_id: str,
    newly_linked: bool,
) -> bool:
    if task["status"] in TERMINAL_TASK_STATUSES:
        return False
    if newly_linked:
        return True
    return task["current_interaction_id"] == interaction_id


def _endpoint_from_row(row: sqlite3.Row) -> dict:
    value = dict(row)
    value["capabilities"] = json.loads(value.pop("capabilities_json"))
    value["route"] = json.loads(value.pop("route_json"))
    return value


def _task_from_row(row: sqlite3.Row) -> dict:
    value = dict(row)
    value["summary"] = json.loads(value.pop("summary_json"))
    return value


def _artifact_from_row(
    row: sqlite3.Row,
    *,
    include_source_ref: bool = False,
) -> dict:
    result = {
        "artifact_id": row["artifact_id"],
        "task_id": row["task_id"],
        "user_subject": row["user_subject"],
        "artifact_type": row["artifact_type"],
        "filename": row["filename"],
        "content_type": row["content_type"],
        "byte_size": int(row["byte_size"]),
        "download_url": row["download_url"],
        "state": row["state"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "expires_at": row["expires_at"],
    }
    if include_source_ref:
        result["source_ref"] = row["source_ref"]
    return result


def _event_from_row(row: sqlite3.Row) -> dict:
    value = dict(row)
    value["payload"] = json.loads(value.pop("payload_json"))
    return value


def _outbox_from_row(row: sqlite3.Row) -> dict:
    value = dict(row)
    value["payload"] = json.loads(value.pop("payload_json"))
    return value


def _timeline_from_row(row: sqlite3.Row) -> dict:
    value = dict(row)
    value["payload"] = json.loads(value.pop("payload_json"))
    return value


def _continuation_from_row(row: sqlite3.Row) -> dict:
    value = dict(row)
    value["candidate_task_ids"] = json.loads(
        value.pop("candidate_task_ids_json")
    )
    value["allow_new_operation"] = value["execution_mode"] in {
        "resume",
        "follow_up",
    }
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


def _required_future_time(value: Any, name: str) -> str:
    normalized = _required_text(value, name, 80)
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{name} is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} is invalid")
    parsed = parsed.astimezone(timezone.utc)
    if parsed <= datetime.now(timezone.utc):
        raise ValueError(f"{name} is expired")
    return parsed.isoformat()


def _artifact_download_url(value: Any) -> str:
    normalized = _required_text(value, "download_url", 2_048)
    parsed = urlparse(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("download_url is invalid")
    return normalized


def _timeline_text(value: Any) -> str:
    normalized = str(value or "").replace("\0", "").strip()
    if not normalized or len(normalized) > 50_000:
        raise ValueError("timeline text is invalid")
    return normalized


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_after(seconds: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).isoformat()


def _utc_before(*, minutes: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(minutes=minutes)
    ).isoformat()


def _client_type_family(value: str | None) -> tuple[str, ...]:
    normalized = str(value or "").strip().lower()
    if normalized in {"web", "webchat"}:
        return ("web", "webchat")
    if normalized in {"wechat", "weixin", "openclaw-weixin"}:
        return ("wechat", "weixin", "openclaw-weixin")
    if normalized:
        return (normalized,)
    return ()
