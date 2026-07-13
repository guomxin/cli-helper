from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Callable, Iterator


class WriteAuthorizationNotFound(KeyError):
    pass


class WriteAuthorizationStateError(RuntimeError):
    pass


class WriteAuthorizationAccessDenied(RuntimeError):
    pass


class WriteAuthorizationIntegrityError(RuntimeError):
    pass


class WriteAuthorizationStore:
    def __init__(
        self,
        db_path: Path | str,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS write_authorizations (
                    authorization_id TEXT PRIMARY KEY,
                    user_subject TEXT NOT NULL,
                    system_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    capability_name TEXT NOT NULL,
                    capability_version TEXT NOT NULL,
                    prepare_operation_id TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    plan_hash TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    card_url TEXT NOT NULL,
                    csrf_hash TEXT,
                    state TEXT NOT NULL,
                    commit_operation_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    decided_at TEXT,
                    consumed_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS write_authorizations_subject_state
                ON write_authorizations (user_subject, state, created_at)
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS immutable_write_authorization_plan
                BEFORE UPDATE OF user_subject, system_id, session_id,
                    capability_name, capability_version, prepare_operation_id,
                    plan_json, plan_hash, summary_json, card_url, created_at,
                    expires_at
                ON write_authorizations
                BEGIN
                    SELECT RAISE(ABORT, 'write authorization plan is immutable');
                END
                """
            )

    def create(
        self,
        *,
        user_subject: str,
        system_id: str,
        session_id: str,
        capability_name: str,
        capability_version: str,
        prepare_operation_id: str,
        plan: dict[str, Any],
        summary: dict[str, Any],
        card_base_url: str,
        ttl_seconds: int = 600,
    ) -> dict:
        required = {
            "user_subject": user_subject,
            "system_id": system_id,
            "session_id": session_id,
            "capability_name": capability_name,
            "capability_version": capability_version,
            "prepare_operation_id": prepare_operation_id,
        }
        missing = [name for name, value in required.items() if not str(value or "").strip()]
        if missing:
            raise ValueError(f"write authorization is missing: {', '.join(missing)}")
        if not isinstance(plan, dict) or not isinstance(summary, dict):
            raise TypeError("write authorization plan and summary must be objects")
        if ttl_seconds < 30 or ttl_seconds > 1800:
            raise ValueError("write authorization TTL must be between 30 and 1800 seconds")
        base_url = _validate_card_base_url(card_base_url)
        plan_json = _canonical_json(plan)
        plan_hash = _json_hash(plan_json)
        summary_json = _canonical_json(summary)
        now = _as_utc(self.clock())
        expires_at = now + timedelta(seconds=ttl_seconds)
        authorization_id = secrets.token_urlsafe(32)
        card_url = f"{base_url}/authorize/{authorization_id}"

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE write_authorizations
                SET state = 'superseded', csrf_hash = NULL,
                    updated_at = ?, decided_at = ?
                WHERE user_subject = ? AND capability_name = ?
                  AND state IN ('pending', 'approved')
                """,
                (_format_time(now), _format_time(now), user_subject, capability_name),
            )
            connection.execute(
                """
                INSERT INTO write_authorizations (
                    authorization_id, user_subject, system_id, session_id,
                    capability_name, capability_version, prepare_operation_id,
                    plan_json, plan_hash, summary_json, card_url, state,
                    created_at, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    authorization_id,
                    user_subject,
                    system_id,
                    session_id,
                    capability_name,
                    capability_version,
                    prepare_operation_id,
                    plan_json,
                    plan_hash,
                    summary_json,
                    card_url,
                    _format_time(now),
                    _format_time(now),
                    _format_time(expires_at),
                ),
            )
            row = self._select(connection, authorization_id)
        return _authorization_from_row(row, include_plan=False)

    def get(self, authorization_id: str, *, include_plan: bool = False) -> dict:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._expire_if_needed(connection, self._select(connection, authorization_id))
        return _authorization_from_row(row, include_plan=include_plan)

    def issue_csrf(self, authorization_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = _as_utc(self.clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._expire_if_needed(connection, self._select(connection, authorization_id))
            if row["state"] != "pending":
                raise WriteAuthorizationStateError(
                    f"write authorization is not pending: {row['state']}"
                )
            connection.execute(
                """
                UPDATE write_authorizations
                SET csrf_hash = ?, updated_at = ?
                WHERE authorization_id = ?
                """,
                (_token_hash(token), _format_time(now), authorization_id),
            )
        return token

    def decide(
        self,
        authorization_id: str,
        *,
        decision: str,
        csrf_token: str,
        csrf_cookie: str,
    ) -> dict:
        if decision not in {"approve", "reject"}:
            raise ValueError("write authorization decision must be approve or reject")
        now = _as_utc(self.clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._expire_if_needed(connection, self._select(connection, authorization_id))
            if row["state"] != "pending":
                raise WriteAuthorizationStateError(
                    f"write authorization is not pending: {row['state']}"
                )
            expected_hash = str(row["csrf_hash"] or "")
            supplied_hash = _token_hash(csrf_token) if csrf_token else ""
            if (
                not expected_hash
                or not csrf_cookie
                or not hmac.compare_digest(csrf_token, csrf_cookie)
                or not hmac.compare_digest(expected_hash, supplied_hash)
            ):
                raise WriteAuthorizationAccessDenied(
                    "write authorization card CSRF validation failed"
                )
            state = "approved" if decision == "approve" else "rejected"
            cursor = connection.execute(
                """
                UPDATE write_authorizations
                SET state = ?, csrf_hash = NULL, decided_at = ?, updated_at = ?
                WHERE authorization_id = ? AND state = 'pending'
                """,
                (state, _format_time(now), _format_time(now), authorization_id),
            )
            if cursor.rowcount != 1:
                raise WriteAuthorizationStateError(
                    "write authorization could not be decided"
                )
            row = self._select(connection, authorization_id)
        return _authorization_from_row(row, include_plan=False)

    def consume(
        self,
        authorization_id: str,
        *,
        user_subject: str,
        system_id: str,
        session_id: str,
        capability_name: str,
        capability_version: str,
        commit_operation_id: str,
    ) -> dict:
        now = _as_utc(self.clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._expire_if_needed(connection, self._select(connection, authorization_id))
            if row["state"] != "approved":
                raise WriteAuthorizationStateError(
                    f"write authorization is not approved: {row['state']}"
                )
            bindings_match = all(
                (
                    row["user_subject"] == user_subject,
                    row["system_id"] == system_id,
                    row["session_id"] == session_id,
                    row["capability_name"] == capability_name,
                    row["capability_version"] == capability_version,
                )
            )
            if not bindings_match:
                raise WriteAuthorizationAccessDenied(
                    "write authorization is not bound to this user, session, or capability"
                )
            plan_json = str(row["plan_json"])
            if not hmac.compare_digest(str(row["plan_hash"]), _json_hash(plan_json)):
                raise WriteAuthorizationIntegrityError(
                    "write authorization plan integrity check failed"
                )
            cursor = connection.execute(
                """
                UPDATE write_authorizations
                SET state = 'consumed', commit_operation_id = ?,
                    consumed_at = ?, updated_at = ?
                WHERE authorization_id = ? AND state = 'approved'
                """,
                (
                    commit_operation_id,
                    _format_time(now),
                    _format_time(now),
                    authorization_id,
                ),
            )
            if cursor.rowcount != 1:
                raise WriteAuthorizationStateError(
                    "write authorization could not be consumed"
                )
            row = self._select(connection, authorization_id)
        return _authorization_from_row(row, include_plan=True)

    def _select(self, connection: sqlite3.Connection, authorization_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM write_authorizations WHERE authorization_id = ?",
            (authorization_id,),
        ).fetchone()
        if row is None:
            raise WriteAuthorizationNotFound(
                f"write authorization not found: {authorization_id}"
            )
        return row

    def _expire_if_needed(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> sqlite3.Row:
        now = _as_utc(self.clock())
        if row["state"] in {"pending", "approved"} and now >= _parse_time(row["expires_at"]):
            connection.execute(
                """
                UPDATE write_authorizations
                SET state = 'expired', csrf_hash = NULL,
                    decided_at = COALESCE(decided_at, ?), updated_at = ?
                WHERE authorization_id = ?
                """,
                (_format_time(now), _format_time(now), row["authorization_id"]),
            )
            return self._select(connection, row["authorization_id"])
        return row


def _authorization_from_row(row: sqlite3.Row, *, include_plan: bool) -> dict:
    value = dict(row)
    plan = json.loads(value.pop("plan_json"))
    value["summary"] = json.loads(value.pop("summary_json"))
    value.pop("csrf_hash", None)
    if include_plan:
        value["plan"] = plan
    return value


def _validate_card_base_url(value: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(str(value or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("write authorization card base URL must be http(s)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("write authorization card base URL is invalid")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_hash(canonical_json: str) -> str:
    digest = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("write authorization clock must return timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)
