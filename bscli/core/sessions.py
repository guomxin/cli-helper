from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Iterator
from uuid import uuid4


class SessionPrincipalMismatch(RuntimeError):
    pass


class SessionRegistry:
    def __init__(self, db_path: Path | str, profile_root: Path | str) -> None:
        self.db_path = Path(db_path)
        self.profile_root = Path(profile_root)
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_subject TEXT NOT NULL,
                    system_id TEXT NOT NULL,
                    expected_principal_ref TEXT,
                    downstream_principal_ref TEXT,
                    profile_path TEXT NOT NULL,
                    state TEXT NOT NULL,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_verified_at TEXT,
                    UNIQUE (user_subject, system_id)
                )
                """
            )

    def get_or_create(
        self,
        *,
        user_subject: str,
        system_id: str,
        expected_principal_ref: str | None = None,
    ) -> dict:
        if not user_subject or not system_id:
            raise ValueError("user_subject and system_id are required")
        existing = self.find(user_subject=user_subject, system_id=system_id)
        if existing is not None:
            bound_principal = (existing.get("expected_principal_ref") or "").strip()
            requested_principal = (expected_principal_ref or "").strip()
            if bound_principal and requested_principal and bound_principal != requested_principal:
                message = "session is already bound to a different expected principal"
                self.quarantine(existing["session_id"], message)
                raise SessionPrincipalMismatch(message)
            if requested_principal and not bound_principal:
                existing = self._update_expected(existing["session_id"], expected_principal_ref)
            Path(existing["profile_path"]).mkdir(parents=True, exist_ok=True)
            return existing

        profile_path = self._profile_path(user_subject=user_subject, system_id=system_id)
        profile_path.mkdir(parents=True, exist_ok=True)
        now = _utc_now()
        session_id = str(uuid4())
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO sessions (
                        session_id, user_subject, system_id, expected_principal_ref,
                        profile_path, state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'new', ?, ?)
                    """,
                    (
                        session_id,
                        user_subject,
                        system_id,
                        expected_principal_ref,
                        str(profile_path),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = self.find(user_subject=user_subject, system_id=system_id)
                if existing is None:
                    raise
                return existing
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return _session_from_row(row)

    def find(self, *, user_subject: str, system_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE user_subject = ? AND system_id = ?",
                (user_subject, system_id),
            ).fetchone()
        return _session_from_row(row) if row is not None else None

    def get(self, session_id: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"session not found: {session_id}")
        return _session_from_row(row)

    def mark_awaiting_login(self, session_id: str) -> dict:
        return self._set_state(session_id, "awaiting_login", last_error=None)

    def mark_expired(self, session_id: str, message: str = "Downstream login expired.") -> dict:
        return self._set_state(session_id, "expired", last_error=message)

    def quarantine(self, session_id: str, message: str) -> dict:
        return self._set_state(session_id, "quarantined", last_error=message)

    def activate(self, session_id: str, *, observed_principal_ref: str | None) -> dict:
        session = self.get(session_id)
        expected = (session.get("expected_principal_ref") or "").strip()
        observed = (observed_principal_ref or "").strip()
        if not expected:
            message = "expected downstream principal is not configured"
            self._set_state(session_id, "quarantined", last_error=message)
            raise SessionPrincipalMismatch(message)
        if not observed:
            message = "downstream principal could not be verified"
            self._set_state(session_id, "quarantined", last_error=message)
            raise SessionPrincipalMismatch(message)
        if expected and expected != observed:
            message = "observed downstream principal does not match the expected principal"
            self._set_state(
                session_id,
                "quarantined",
                downstream_principal_ref=observed or None,
                last_error=message,
            )
            raise SessionPrincipalMismatch(message)

        if observed:
            with self._connect() as connection:
                conflicting = connection.execute(
                    """
                    SELECT session_id FROM sessions
                    WHERE system_id = ? AND downstream_principal_ref = ?
                      AND user_subject <> ? AND state = 'active'
                    """,
                    (session["system_id"], observed, session["user_subject"]),
                ).fetchone()
            if conflicting is not None:
                message = "downstream principal is already bound to another active user"
                self._set_state(
                    session_id,
                    "quarantined",
                    downstream_principal_ref=observed,
                    last_error=message,
                )
                raise SessionPrincipalMismatch(message)

        return self._set_state(
            session_id,
            "active",
            downstream_principal_ref=observed or None,
            last_error=None,
            verified=True,
        )

    def _update_expected(self, session_id: str, expected_principal_ref: str) -> dict:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET expected_principal_ref = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (expected_principal_ref, now, session_id),
            )
        return self.get(session_id)

    def _set_state(
        self,
        session_id: str,
        state: str,
        *,
        downstream_principal_ref: str | None = None,
        last_error: str | None,
        verified: bool = False,
    ) -> dict:
        now = _utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE sessions
                SET state = ?, downstream_principal_ref = COALESCE(?, downstream_principal_ref),
                    last_error = ?, updated_at = ?,
                    last_verified_at = CASE WHEN ? THEN ? ELSE last_verified_at END
                WHERE session_id = ?
                """,
                (
                    state,
                    downstream_principal_ref,
                    last_error,
                    now,
                    1 if verified else 0,
                    now,
                    session_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"session not found: {session_id}")
        return self.get(session_id)

    def _profile_path(self, *, user_subject: str, system_id: str) -> Path:
        safe_system = re.sub(r"[^a-zA-Z0-9_-]", "_", system_id)[:64] or "system"
        user_key = hashlib.sha256(user_subject.encode("utf-8")).hexdigest()[:24]
        return (self.profile_root / safe_system / user_key).resolve()


def _session_from_row(row: sqlite3.Row | None) -> dict:
    if row is None:
        raise KeyError("session not found")
    return dict(row)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
