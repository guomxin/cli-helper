from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping
from uuid import uuid4


ACTIVE_TASK_STATES = frozenset(
    {"starting", "running", "waiting_user", "recovering", "observe_only"}
)


class ReferenceHostState:
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
                CREATE TABLE IF NOT EXISTS reference_tasks (
                    local_task_id TEXT PRIMARY KEY,
                    identity_label TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    task_id TEXT,
                    endpoint_id TEXT,
                    conversation_ref TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    active_interaction_id TEXT,
                    lease_version INTEGER,
                    last_sequence INTEGER NOT NULL DEFAULT 0,
                    restartable_read_arguments_json TEXT,
                    result_summary_json TEXT NOT NULL,
                    error_summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT
                );

                CREATE INDEX IF NOT EXISTS reference_tasks_identity_status
                ON reference_tasks (identity_label, status, updated_at);

                CREATE TABLE IF NOT EXISTS reference_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    local_task_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS reference_events_task_sequence
                ON reference_events (local_task_id, sequence);
                """
            )

    def create_task(
        self,
        *,
        identity_label: str,
        tool_name: str,
        conversation_ref: str,
        title: str,
        restartable_read_arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        local_task_id = str(uuid4())
        safe_arguments = (
            _safe_json_object(restartable_read_arguments)
            if restartable_read_arguments is not None
            else None
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO reference_tasks (
                    local_task_id, identity_label, tool_name, conversation_ref,
                    title, status, restartable_read_arguments_json,
                    result_summary_json, error_summary_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'starting', ?, '{}', '{}', ?, ?)
                """,
                (
                    local_task_id,
                    identity_label,
                    tool_name,
                    conversation_ref,
                    title,
                    (
                        json.dumps(safe_arguments, ensure_ascii=False, sort_keys=True)
                        if safe_arguments is not None
                        else None
                    ),
                    now,
                    now,
                ),
            )
        self.append_event(
            local_task_id=local_task_id,
            event_key=f"local:{local_task_id}:created",
            kind="task.created",
            summary="任务已由 Reference Host 接受",
        )
        return self.get_task(local_task_id)

    def bind_central_task(
        self,
        *,
        local_task_id: str,
        task_id: str,
        endpoint_id: str,
        lease_version: int,
    ) -> dict[str, Any]:
        return self.update_task(
            local_task_id,
            task_id=task_id,
            endpoint_id=endpoint_id,
            lease_version=lease_version,
            status="running",
        )

    def update_task(self, local_task_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {
            "task_id",
            "endpoint_id",
            "status",
            "active_interaction_id",
            "lease_version",
            "last_sequence",
            "result_summary",
            "error_summary",
            "finished_at",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported task state fields: {sorted(unknown)}")
        assignments = []
        values: list[Any] = []
        for name, value in changes.items():
            column = name
            if name in {"result_summary", "error_summary"}:
                column = f"{name}_json"
                value = json.dumps(
                    _safe_json_object(value),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            assignments.append(f"{column} = ?")
            values.append(value)
        assignments.append("updated_at = ?")
        values.append(_utc_now())
        values.append(local_task_id)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE reference_tasks SET {', '.join(assignments)} "
                "WHERE local_task_id = ?",
                values,
            )
            if cursor.rowcount != 1:
                raise KeyError(f"reference task not found: {local_task_id}")
        return self.get_task(local_task_id)

    def finish_task(
        self,
        local_task_id: str,
        *,
        status: str,
        result_summary: Mapping[str, Any] | None = None,
        error_summary: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in {"succeeded", "failed", "canceled", "unknown"}:
            raise ValueError("terminal reference task status is invalid")
        return self.update_task(
            local_task_id,
            status=status,
            result_summary=result_summary or {},
            error_summary=error_summary or {},
            active_interaction_id=None,
            finished_at=_utc_now(),
        )

    def append_event(
        self,
        *,
        local_task_id: str,
        event_key: str,
        kind: str,
        summary: str,
        payload: Mapping[str, Any] | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO reference_events (
                    event_key, local_task_id, kind, summary, payload_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_key,
                    local_task_id,
                    kind,
                    summary,
                    json.dumps(
                        _safe_json_object(payload),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    created_at or _utc_now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM reference_events WHERE event_key = ?",
                (event_key,),
            ).fetchone()
        return _event(row)

    def get_task(self, local_task_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM reference_tasks WHERE local_task_id = ?",
                (local_task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"reference task not found: {local_task_id}")
        return _task(row)

    def task_for_central_id(
        self,
        *,
        identity_label: str,
        task_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM reference_tasks
                WHERE identity_label = ? AND task_id = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (identity_label, task_id),
            ).fetchone()
        return _task(row) if row is not None else None

    def list_tasks(
        self,
        *,
        identity_label: str | None = None,
        active_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM reference_tasks WHERE 1 = 1"
        values: list[Any] = []
        if identity_label:
            query += " AND identity_label = ?"
            values.append(identity_label)
        if active_only:
            placeholders = ",".join("?" for _ in ACTIVE_TASK_STATES)
            query += f" AND status IN ({placeholders})"
            values.extend(sorted(ACTIVE_TASK_STATES))
        query += " ORDER BY updated_at DESC LIMIT ?"
        values.append(min(max(int(limit), 1), 500))
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [_task(row) for row in rows]

    def list_events(
        self,
        *,
        local_task_id: str,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM reference_events
                WHERE local_task_id = ? AND sequence > ?
                ORDER BY sequence LIMIT ?
                """,
                (
                    local_task_id,
                    max(int(after_sequence), 0),
                    min(max(int(limit), 1), 500),
                ),
            ).fetchall()
        return [_event(row) for row in rows]

    def counts(self, *, identity_label: str | None = None) -> dict[str, int]:
        tasks = self.list_tasks(identity_label=identity_label, limit=500)
        return {
            "active": sum(task["status"] in ACTIVE_TASK_STATES for task in tasks),
            "waiting": sum(task["status"] == "waiting_user" for task in tasks),
            "failed": sum(task["status"] in {"failed", "unknown"} for task in tasks),
        }


def _task(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value["restartable_read_arguments"] = _json_or_empty(
        value.pop("restartable_read_arguments_json")
    )
    value["result_summary"] = _json_or_empty(value.pop("result_summary_json"))
    value["error_summary"] = _json_or_empty(value.pop("error_summary_json"))
    return value


def _event(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value["payload"] = _json_or_empty(value.pop("payload_json"))
    return value


def _json_or_empty(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


def _safe_json_object(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("state payload must be an object")
    return _strip_secrets(dict(value))


def _strip_secrets(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return "[depth-limited]"
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            name = str(key)
            lowered = name.casefold()
            if any(
                marker in lowered
                for marker in (
                    "authorization",
                    "bearer",
                    "cookie",
                    "credential",
                    "password",
                    "secret",
                    "token",
                    "url",
                )
            ):
                continue
            result[name] = _strip_secrets(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [_strip_secrets(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and len(value) > 2_000:
            return value[:2_000] + "..."
        return value
    return str(value)[:500]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
